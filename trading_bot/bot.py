"""Self-learning paper-trading bot for Alpaca.

Trades US and world-market stocks/ETFs (long and short) and options (calls and puts)
during US market hours, and crypto 24/7.

Run:  python -m trading_bot.bot            # trade in a loop
      python -m trading_bot.bot --once     # single pass, then exit
      python -m trading_bot.bot --dry-run  # log decisions, place no orders
      python -m trading_bot.bot --report   # show what the bot has learned
"""
import argparse
import logging
import time
import math
import uuid
from datetime import datetime, timedelta

from .alpaca_client import AlpacaClient, AlpacaError, is_crypto, norm
from .config import Config
from .learner import Learner
from .risk import daily_loss_hit, option_contracts, pick_option, position_size
from .strategy import LONG_STRATEGIES, STRATEGIES

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
        self.halted_day = None
        self.scanned = []
        self.scanned_at = None
        self.shortable_cache = {}

    @property
    def open_trades(self):
        return self.learner.state["open_trades"]

    # --- which symbols to trade ---
    def stock_universe(self, now):
        if self.cfg.scan_stocks and (self.scanned_at is None
                                     or now - self.scanned_at >= timedelta(minutes=self.cfg.rescan_minutes)):
            try:
                found = [sym for sym, price, dollar_vol in self.client.get_most_active_stocks(self.cfg.scan_top)
                         if price >= self.cfg.min_price and dollar_vol >= self.cfg.min_dollar_volume]
                added = set(found[: self.cfg.max_scanned]) - set(self.scanned)
                self.scanned = found[: self.cfg.max_scanned]
                self.scanned_at = now
                log.info("Stock scan: watching %d most-traded stocks%s", len(self.scanned),
                         f" (new: {', '.join(sorted(added))})" if added else "")
            except AlpacaError as exc:
                log.error("Stock scan failed, keeping previous list: %s", exc)
        held = [s for s in self.open_trades if not is_crypto(s)]
        held += [t["underlying"] for t in self.open_trades.values() if t.get("underlying")]
        return list(dict.fromkeys(self.cfg.symbols + self.cfg.options_underlyings + self.cfg.world_symbols
                                  + self.scanned + held))

    def _shortable(self, symbol):
        if symbol not in self.shortable_cache:
            try:
                asset = self.client.get_asset(symbol)
                self.shortable_cache[symbol] = bool(asset.get("shortable") and asset.get("easy_to_borrow"))
            except AlpacaError:
                return False
        return self.shortable_cache[symbol]

    def _allowed(self, symbol):
        """Which strategies a symbol may use: shorts need options (puts) or a borrowable stock."""
        if is_crypto(symbol) or not self.cfg.allow_shorts:
            return list(LONG_STRATEGIES)
        if symbol in self.cfg.options_underlyings or self._shortable(symbol):
            return list(STRATEGIES)
        return list(LONG_STRATEGIES)

    @staticmethod
    def _is_entry_order(order):
        return order.get("client_order_id", "").split("-")[0] in STRATEGIES

    def _timeframe(self, symbol):
        return self.cfg.crypto_timeframe if is_crypto(symbol) else self.cfg.timeframe

    def _get_bars(self, symbol, now):
        days = self.cfg.crypto_learn_days if is_crypto(symbol) else self.cfg.learn_days
        timeframe = self._timeframe(symbol)
        return completed_bars(self.client.get_bars(symbol, timeframe, days), timeframe, now)

    def _due(self, symbol, now):
        """Only fetch bars when a new bar should have closed since the last one we saw."""
        last = self.last_bar_seen.get(symbol)
        minutes = _timeframe_minutes(self._timeframe(symbol))
        return last is None or _parse_ts(last) + timedelta(minutes=2 * minutes) <= now

    # --- main loop ---
    def run_once(self):
        clock = self.client.get_clock()
        now = _parse_ts(clock["timestamp"])
        stock_open = clock["is_open"]
        positions = {norm(p["symbol"]): p for p in self.client.get_positions() or []}
        entry_orders = [o for o in self.client.get_open_orders() or [] if self._is_entry_order(o)]
        pending = {norm(o["symbol"]) for o in entry_orders}
        self._learn_from_closed_trades(positions, pending)
        self._protect_crypto(positions)

        account = self.client.get_account()
        minutes_to_close = (_parse_ts(clock["next_close"]) - now).total_seconds() / 60
        log.info("Equity $%.2f (prev close $%.2f) | stocks %s | %d positions",
                 float(account["equity"]), float(account["last_equity"]),
                 f"open, {minutes_to_close:.0f} min to close" if stock_open else f"closed until {clock['next_open']}",
                 len(positions))

        today = now.date()
        if self.halted_day == today:
            return
        if daily_loss_hit(account, self.cfg):
            log.warning("Daily loss limit hit, selling everything and pausing for the rest of the day")
            self._flatten(positions, "daily loss limit", stocks_only=False)
            self.halted_day = today
            return

        self._check_managed_exits(positions)

        symbols = list(self.cfg.crypto_symbols)
        if stock_open:
            if minutes_to_close <= self.cfg.flatten_minutes:
                for order in entry_orders:
                    if not is_crypto(order["symbol"]):
                        self._act(f"CANCEL unfilled {order['symbol']} entry (end of day)",
                                  lambda o=order: self.client.cancel_order(o["id"]))
                self._flatten(positions, "end of day", stocks_only=True)
            else:
                symbols = self.stock_universe(now) + symbols

        # 1) fetch bars and re-learn for every symbol that has a new closed bar
        fresh = {}
        for symbol in symbols:
            if not self._due(symbol, now):
                continue
            try:
                bars = self._get_bars(symbol, now)
            except AlpacaError as exc:
                log.error("%s: %s", symbol, exc)
                continue
            if bars and self.last_bar_seen.get(symbol) != bars[-1]["t"]:
                self.last_bar_seen[symbol] = bars[-1]["t"]
                self.learner.update(symbol, bars, self._allowed(symbol))
                fresh[symbol] = bars

        # 2) pick a strategy per symbol and act on it
        stock_entries = stock_open and minutes_to_close > self.cfg.no_new_entries_minutes
        for symbol, bars in fresh.items():
            try:
                self._handle_symbol(symbol, bars, account, positions, pending,
                                    allow_entries=is_crypto(symbol) or stock_entries)
            except AlpacaError as exc:
                log.error("%s: %s", symbol, exc)
        self.learner.save()

    def _handle_symbol(self, symbol, bars, account, positions, pending, allow_entries):
        key = norm(symbol)
        choice, scores = self.learner.choose(symbol)
        previous = self.learner.state["active"].get(symbol)
        if choice != previous:
            log.info("%s: AI switched strategy %s -> %s (%s)", symbol, previous or "none", choice or "sit out",
                     ", ".join(f"{k} {v:+.2f}R" for k, v in scores.items()))
        self.learner.state["active"][symbol] = choice

        # exits follow the strategy that opened the trade (shares, or an option on this underlying)
        options_held = [c for c, t in self.open_trades.items() if t.get("underlying") == symbol]
        for held in ([key] if key in positions else []) + [c for c in options_held if c in positions]:
            trade = self.open_trades.get(symbol if held == key else held, {})
            owner = trade.get("strategy") or choice or "trend"
            signal = STRATEGIES[owner](bars, self.cfg)
            log.info("%-9s [%s] holding %s: %s", symbol, owner, held, signal.reason)
            if signal.action == "sell":
                target = symbol if held == key else held
                self._act(f"EXIT {target} ({owner})", lambda: self.client.close_position(target))
                positions.pop(held, None)
        if key in positions or options_held:
            return

        if choice is None or key in pending:
            return
        signal = STRATEGIES[choice](bars, self.cfg)
        log.info("%-9s [%s] %s: %s", symbol, choice, "SHORT" if signal.action == "buy" and signal.side == "short"
                 else signal.action.upper(), signal.reason)
        if signal.action != "buy":
            return
        if not allow_entries:
            log.info("%s: too close to the close for new entries", symbol)
            return
        if len(positions) + len(pending) >= self.cfg.max_open_positions:
            log.info("%s: max open positions reached", symbol)
            return

        if symbol in self.cfg.options_underlyings:
            if self._enter_option(symbol, choice, signal, bars, account):
                pending.add(key)
                return
            if signal.side == "short" and not self._shortable(symbol):
                return

        crypto = is_crypto(symbol)
        cash = account["non_marginable_buying_power"] if crypto else account["buying_power"]
        qty = position_size(float(account["equity"]), float(cash), signal.price, signal.stop, self.cfg,
                            fractional=crypto, side=signal.side)
        if not qty:
            log.info("%s: position size too small", symbol)
            return
        order_side = "buy" if signal.side == "long" else "sell"
        order_id = f"{choice}-{key}-{uuid.uuid4().hex[:8]}"
        self._act(
            f"{'BUY' if order_side == 'buy' else 'SHORT'} {qty} {symbol} @ ~{signal.price:.4g} "
            f"stop {signal.stop:.4g} target {signal.target:.4g} ({choice})",
            lambda: self.client.submit_entry(symbol, qty, order_side, signal.target, signal.stop, order_id),
        )
        pending.add(key)
        if not self.dry_run:
            self.open_trades[symbol] = {"strategy": choice, "side": signal.side, "entry": signal.price,
                                        "stop": signal.stop, "target": signal.target, "opened_at": bars[-1]["t"],
                                        "managed": crypto}

    def _enter_option(self, symbol, choice, signal, bars, account):
        """Buy a call (bullish) or put (bearish). Returns False if no good contract was found."""
        kind = "call" if signal.side == "long" else "put"
        today = _parse_ts(bars[-1]["t"]).date()
        try:
            candidates = self.client.get_option_candidates(
                symbol, kind, signal.price, today + timedelta(days=self.cfg.option_min_days),
                today + timedelta(days=self.cfg.option_max_days))
        except AlpacaError as exc:
            log.error("%s options: %s", symbol, exc)
            return False
        contract = pick_option(candidates, self.cfg)
        if not contract:
            log.info("%s: no liquid %s found, trading the shares instead", symbol, kind)
            return False
        cash = float(account.get("options_buying_power") or account["buying_power"])
        qty = option_contracts(float(account["equity"]), cash, contract["ask"], self.cfg)
        if qty < 1:
            log.info("%s: option too expensive for the risk limit", symbol)
            return False
        occ, ask = contract["symbol"], contract["ask"]
        order_id = f"{choice}-{occ}-{uuid.uuid4().hex[:8]}"
        self._act(
            f"BUY {qty} {symbol} {kind.upper()} {occ} @ {ask:.2f} (delta {contract['delta']:+.2f}, "
            f"expires {contract['expiration']}) ({choice})",
            lambda: self.client.submit_option_buy(occ, qty, ask, order_id),
        )
        if not self.dry_run:
            self.open_trades[occ] = {
                "strategy": choice, "side": "long", "underlying": symbol, "entry": ask,
                "stop": ask * (1 - self.cfg.option_stop_pct), "target": ask * (1 + self.cfg.option_take_profit_pct),
                "opened_at": bars[-1]["t"], "managed": True}
        return True

    def _protect_crypto(self, positions):
        """Once a crypto buy fills, park a stop-loss order at Alpaca so the position is protected
        even if the bot goes offline. (Options can't have stop orders, so those stay bot-managed.)"""
        for symbol, trade in self.open_trades.items():
            pos = positions.get(norm(symbol))
            if not is_crypto(symbol) or not pos or trade.get("stop_order_id") or self.dry_run:
                continue
            try:
                order = self.client.submit_crypto_stop(symbol, pos["qty"], trade["stop"])
                trade["stop_order_id"] = order["id"]
                log.info("%s: stop-loss parked at Alpaca at %.4g", symbol, trade["stop"])
            except AlpacaError as exc:
                log.error("%s: could not place stop-loss: %s", symbol, exc)

    def _check_managed_exits(self, positions):
        """Crypto and options have no bracket orders, so enforce their stop-loss and take-profit here every loop."""
        for symbol, trade in self.open_trades.items():
            pos = positions.get(norm(symbol))
            if not trade.get("managed", is_crypto(symbol)) or not pos:
                continue
            price = float(pos["current_price"])
            if price <= trade["stop"] or price >= trade.get("target", math.inf):
                why = "stop-loss" if price <= trade["stop"] else "take-profit"
                try:
                    self._act(f"SELL {symbol} @ ~{price:.4g} ({why})", lambda: self.client.close_position(symbol))
                    positions.pop(norm(symbol))
                except AlpacaError as exc:
                    log.error("%s: %s", symbol, exc)

    def _learn_from_closed_trades(self, positions, pending):
        """Record the result of every trade that has closed since the last check."""
        for symbol, trade in list(self.open_trades.items()):
            key = norm(symbol)
            if key in positions:
                trade["entry"] = float(positions[key]["avg_entry_price"])
                continue
            if key in pending:
                continue
            short = trade.get("side") == "short"
            # None if the entry never filled
            exit_price = self.client.get_last_exit_fill(symbol, trade["opened_at"], "buy" if short else "sell")
            if exit_price is not None:
                risk = abs(trade["entry"] - trade["stop"])
                r = ((trade["entry"] - exit_price) if short else (exit_price - trade["entry"])) / risk
                log.info("%s: trade closed at %.4g, %+.2fR -> learning for %s", symbol, exit_price, r, trade["strategy"])
                self.learner.record_trade(trade.get("underlying", symbol), trade["strategy"], r)
            del self.open_trades[symbol]
            self.learner.save()

    def _flatten(self, positions, reason, stocks_only):
        targets = [p["symbol"] for p in positions.values() if not (stocks_only and p.get("asset_class") == "crypto")]
        if not targets:
            return
        if stocks_only:
            for sym in targets:
                self._act(f"SELL {sym} ({reason})", lambda s=sym: self.client.close_position(s))
                positions.pop(norm(sym))
        else:
            self._act(f"CLOSE ALL {len(targets)} positions ({reason})", self.client.close_all_positions)
            positions.clear()

    def _act(self, description, fn):
        if self.dry_run:
            log.info("[dry-run] would %s", description)
            return
        log.info("ORDER: %s", description)
        fn()

    def report(self):
        """Replay every strategy on current data and print what the bot would pick."""
        now = _parse_ts(self.client.get_clock()["timestamp"])
        symbols = self.stock_universe(now) + list(self.cfg.crypto_symbols)
        for symbol in symbols:
            self.learner.update(symbol, self._get_bars(symbol, now), self._allowed(symbol))
        print(f"{'symbol':9} {'strategy':15} {'score':>7} {'replay trades':>13} {'replay R':>9} "
              f"{'real trades':>11} {'real R':>7}")
        using = []
        for symbol in symbols:
            choice, _ = self.learner.choose(symbol)
            for name, score, n, total, live_n, live_total in self.learner.scoreboard(symbol):
                mark = "  <- using" if name == choice else ""
                print(f"{symbol:9} {name:15} {score:+7.2f} {n:13d} {total:+9.2f} {live_n:11d} {live_total:+7.2f}{mark}")
            if choice is None:
                print(f"{symbol:9} (sitting out: no strategy is working right now)")
            else:
                using.append(f"{symbol} ({choice})")
            print()
        print(f"Trading {len(using)} of {len(symbols)}: {', '.join(using) or 'none right now'}")

    def run_forever(self):
        log.info("Trading %d stocks + %d options underlyings + %d world%s + %d crypto, shorts %s, on %s (%s)",
                 len(self.cfg.symbols), len(self.cfg.options_underlyings), len(self.cfg.world_symbols),
                 " + daily most-traded scan" if self.cfg.scan_stocks else "", len(self.cfg.crypto_symbols),
                 "on" if self.cfg.allow_shorts else "off",
                 self.cfg.base_url, "DRY RUN" if self.dry_run else "paper orders")
        while True:
            try:
                self.run_once()
            except (AlpacaError, OSError) as exc:
                log.error("Loop error: %s", exc)
            except Exception:  # never let one bad response stop the bot
                log.exception("Unexpected error, carrying on")
            time.sleep(self.cfg.poll_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--dry-run", action="store_true", help="log decisions without placing orders")
    parser.add_argument("--report", action="store_true", help="show which strategy each market uses and why")
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
            log.info("Stopped. Stock trades keep their stop-loss/take-profit orders; "
                     "crypto and option stops are only watched while the bot runs.")


if __name__ == "__main__":
    main()
