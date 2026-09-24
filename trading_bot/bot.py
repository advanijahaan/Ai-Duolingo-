"""Intraday paper-trading bot for Alpaca.

Run:  python -m trading_bot.bot            # trade in a loop during market hours
      python -m trading_bot.bot --once     # single pass, then exit
      python -m trading_bot.bot --dry-run  # log decisions, place no orders
"""
import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from .alpaca_client import AlpacaClient, AlpacaError
from .config import Config
from .risk import daily_loss_hit, position_size
from .strategy import generate_signal

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
    def __init__(self, cfg, client=None, dry_run=False):
        self.cfg = cfg
        self.client = client or AlpacaClient(cfg)
        self.dry_run = dry_run
        self.last_bar_seen = {}
        self.halted_for_day = False

    def run_once(self):
        clock = self.client.get_clock()
        if not clock["is_open"]:
            log.info("Market closed. Next open: %s", clock["next_open"])
            self.halted_for_day = False
            self.last_bar_seen.clear()
            return

        now = _parse_ts(clock["timestamp"])
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

        positions = {p["symbol"]: p for p in self.client.get_positions() or []}
        pending = {o["symbol"] for o in self.client.get_open_orders() or [] if o["side"] == "buy"}
        allow_entries = minutes_to_close > self.cfg.no_new_entries_minutes

        for symbol in self.cfg.symbols:
            try:
                self._handle_symbol(symbol, account, positions, pending, allow_entries, now)
            except AlpacaError as exc:
                log.error("%s: %s", symbol, exc)

    def _handle_symbol(self, symbol, account, positions, pending, allow_entries, now):
        bars = completed_bars(self.client.get_bars(symbol, self.cfg.timeframe), self.cfg.timeframe, now)
        if not bars or self.last_bar_seen.get(symbol) == bars[-1]["t"]:
            return  # no new closed bar since last check
        self.last_bar_seen[symbol] = bars[-1]["t"]

        signal = generate_signal(bars, self.cfg)
        log.info("%-5s %-4s %s", symbol, signal.action.upper(), signal.reason)

        if signal.action == "sell" and symbol in positions:
            self._act(f"SELL {symbol} (exit)", lambda: self.client.close_position(symbol))
            positions.pop(symbol)

        elif signal.action == "buy" and symbol not in positions and symbol not in pending:
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
            self._act(
                f"BUY {qty} {symbol} @ ~{signal.price:.2f} stop {signal.stop:.2f} target {signal.target:.2f}",
                lambda: self.client.submit_bracket_buy(symbol, qty, signal.target, signal.stop),
            )
            pending.add(symbol)

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
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    bot = TradingBot(Config.from_env(), dry_run=args.dry_run)
    if args.once:
        bot.run_once()
    else:
        try:
            bot.run_forever()
        except KeyboardInterrupt:
            log.info("Stopped. Open positions keep their stop-loss/take-profit orders.")


if __name__ == "__main__":
    main()
