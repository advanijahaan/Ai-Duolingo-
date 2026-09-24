"""Self-learning intraday paper-trading bot for Alpaca.

Run:  python -m trading_bot.bot            # trade in a loop during market hours
      python -m trading_bot.bot --once     # single pass, then exit
      python -m trading_bot.bot --dry-run  # log decisions, place no orders
      python -m trading_bot.bot --report   # show what the bot has learned
"""
import argparse
import logging
import time
import uuid
from datetime import datetime, timedelta

from .alpaca_client import AlpacaClient, AlpacaError
from .config import Config
from .learner import Learner
from .risk import daily_loss_hit, position_size
from .strategy import STRATEGIES

log = logging.getLogger("trading_bot")


def _parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _timeframe_minutes(timeframe):
    units = {"Min": 1, "Hour": 60}
    for suffix, mult in units.items():
        if timeframe.endswith(suffix):
            return int(timeframe[: -len(suffix)]) * mult
    raise ValueError(f"unsupported timeframe {timeframe}")


def completed_bars(bars, timeframe, now):
    """Drop the still-forming last bar so signals only use closed candles."""
    if bars and _parse_ts(bars[-1]["t"]) + timedelta(minutes=_timeframe_minutes(timeframe)) > now:
        return bars[:-1]
    return bars


class TradingBot:
    def __init__(self, cfg, client=None, learner=None, dry_run=False):
        self.cfg = cfg
        self.client = client or AlpacaClient(cfg)
        self.learner = learner or Learner(cfg)
        self.dry_run = dry_run
        self.last_bar_seen = {}
        self.halted_for_day = False

    @property
    def open_trades(self):
        return self.learner.state["open_trades"]

    def run_once(self):
        clock = self.client.get_clock()
        now = _parse_ts(clock["timestamp"])
        positions = {p["symbol"]: p for p in self.client.get_positions() or []}
        pending = {o["symbol"] for o in self.client.get_open_orders() or [] if o["side"] == "buy"}
        self._learn_from_closed_trades(positions, pending)

        if not clock["is_open"]:
            log.info("Market closed. Next open: %s", clock["next_open"])
            self.halted_for_day = False
            self.last_bar_seen.clear()
            return

        minutes_to_close = (_parse_ts(clock["next_close"]) - now).total_seconds() / 60
        account = self.client.get_account()
        log.info("Equity $%.2f (prev close $%.2f), %.0f min to close",
                 float(account["equity"]), float(account["last_equity"]), minutes_to_close)

        if minutes_to_close <= self.cfg.flatten_minutes:
            self._flatten("end of day")
            return

        if self.halted_for_day or daily_loss_hit(account, self.cfg):
            if not self.halted_for_day:
                log.warning("Daily loss limit hit, flattening and halting until tomorrow")
                self._flatten("daily loss limit")
                self.halted_for_day = True
            return

        # 1) fetch bars and re-learn for every symbol that has a new closed bar
        fresh = {}
        for symbol in self.cfg.symbols:
            try:
                bars = completed_bars(self.client.get_bars(symbol, self.cfg.timeframe, self.cfg.learn_days),
                                      self.cfg.timeframe, now)
            except AlpacaError as exc:
                log.error("%s: %s", symbol, exc)
                continue
            if bars and self.last_bar_seen.get(symbol) != bars[-1]["t"]:
                self.last_bar_seen[symbol] = bars[-1]["t"]
                self.learner.update(symbol, bars)
                fresh[symbol] = bars

        # 2) pick a strategy per symbol and act on it
        allow_entries = minutes_to_close > self.cfg.no_new_entries_minutes
        for symbol, bars in fresh.items():
            try:
                self._handle_symbol(symbol, bars, account, positions, pending, allow_entries)
            except AlpacaError as exc:
                log.error("%s: %s", symbol, exc)
        self.learner.save()

    def _handle_symbol(self, symbol, bars, account, positions, pending, allow_entries):
        choice, scores = self.learner.choose(symbol)
        previous = self.learner.state["active"].get(symbol)
        if choice != previous:
            log.info("%s: AI switched strategy %s -> %s (%s)", symbol, previous or "none", choice or "sit out",
                     ", ".join(f"{k} {v:+.2f}R" for k, v in scores.items()))
        self.learner.state["active"][symbol] = choice

        if symbol in positions:
            # exits follow the strategy that opened the trade
            owner = self.open_trades.get(symbol, {}).get("strategy") or choice or "trend"
            signal = STRATEGIES[owner](bars, self.cfg)
            log.info("%-5s [%s] holding: %s", symbol, owner, signal.reason)
            if signal.action == "sell":
                self._act(f"SELL {symbol} (exit, {owner})", lambda: self.client.close_position(symbol))
                positions.pop(symbol)
            return

        if choice is None or symbol in pending:
            return
        signal = STRATEGIES[choice](bars, self.cfg)
        log.info("%-5s [%s] %s: %s", symbol, choice, signal.action.upper(), signal.reason)
        if signal.action != "buy":
            return
        if not allow_entries:
            log.info("%s: too close to the close for new entries", symbol)
            return
        if len(positions) + len(pending) >= self.cfg.max_open_positions:
            log.info("%s: max open positions reached", symbol)
            return
        qty = position_size(float(account["equity"]), float(account["buying_power"]),
                            signal.price, signal.stop, self.cfg)
        if qty < 1:
            log.info("%s: position size rounds to 0 shares", symbol)
            return
        order_id = f"{choice}-{symbol}-{uuid.uuid4().hex[:8]}"
        self._act(
            f"BUY {qty} {symbol} @ ~{signal.price:.2f} stop {signal.stop:.2f} target {signal.target:.2f} ({choice})",
            lambda: self.client.submit_bracket_buy(symbol, qty, signal.target, signal.stop, order_id),
        )
        pending.add(symbol)
        if not self.dry_run:
            self.open_trades[symbol] = {"strategy": choice, "entry": signal.price, "stop": signal.stop,
                                        "opened_at": bars[-1]["t"]}

    def _learn_from_closed_trades(self, positions, pending):
        """Record the result of every trade that has closed since the last check."""
        for symbol, trade in list(self.open_trades.items()):
            if symbol in positions:
                trade["entry"] = float(positions[symbol]["avg_entry_price"])
                continue
            if symbol in pending:
                continue
            exit_price = self.client.get_last_sell_fill(symbol, trade["opened_at"])  # None if the buy never filled
            if exit_price is not None:
                r = (exit_price - trade["entry"]) / (trade["entry"] - trade["stop"])
                log.info("%s: trade closed at %.2f, %+.2fR -> learning for %s", symbol, exit_price, r, trade["strategy"])
                self.learner.record_trade(symbol, trade["strategy"], r)
            del self.open_trades[symbol]
            self.learner.save()

    def _flatten(self, reason):
        positions = self.client.get_positions() or []
        if positions:
            self._act(f"CLOSE ALL {len(positions)} positions ({reason})", self.client.close_all_positions)

    def _act(self, description, fn):
        if self.dry_run:
            log.info("[dry-run] would %s", description)
            return
        log.info("ORDER: %s", description)
        fn()

    def report(self):
        """Replay every strategy on current data and print what the bot would pick."""
        print(f"{'symbol':6} {'strategy':15} {'score':>7} {'replay trades':>13} {'replay R':>9} "
              f"{'real trades':>11} {'real R':>7}")
        for symbol in self.cfg.symbols:
            self.learner.update(symbol, self.client.get_bars(symbol, self.cfg.timeframe, self.cfg.learn_days))
        for symbol in self.cfg.symbols:
            choice, _ = self.learner.choose(symbol)
            for name, score, n, total, live_n, live_total in self.learner.scoreboard(symbol):
                mark = "  <- using" if name == choice else ""
                print(f"{symbol:6} {name:15} {score:+7.2f} {n:13d} {total:+9.2f} {live_n:11d} {live_total:+7.2f}{mark}")
            if choice is None:
                print(f"{symbol:6} (sitting out: no strategy is working right now)")
            print()

    def run_forever(self):
        log.info("Trading %s on %s (%s)", ", ".join(self.cfg.symbols), self.cfg.base_url,
                 "DRY RUN" if self.dry_run else "paper orders")
        while True:
            try:
                self.run_once()
            except (AlpacaError, OSError) as exc:
                log.error("Loop error: %s", exc)
            time.sleep(self.cfg.poll_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--dry-run", action="store_true", help="log decisions without placing orders")
    parser.add_argument("--report", action="store_true", help="show which strategy each stock uses and why")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    bot = TradingBot(Config.from_env(), dry_run=args.dry_run)
    if args.report:
        bot.report()
    elif args.once:
        bot.run_once()
    else:
        try:
            bot.run_forever()
        except KeyboardInterrupt:
            log.info("Stopped. Open positions keep their stop-loss/take-profit orders.")


if __name__ == "__main__":
    main()
