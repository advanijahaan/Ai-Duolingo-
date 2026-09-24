import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from trading_bot.bot import TradingBot, completed_bars
from trading_bot.config import Config
from trading_bot.risk import daily_loss_hit, position_size
import tempfile

from trading_bot.learner import Learner, backtest
from trading_bot.strategy import Signal, atr, breakout, ema, mean_reversion, rsi, trend

T0 = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)


def make_bars(closes):
    return [
        {"t": (T0 + timedelta(minutes=5 * i)).isoformat().replace("+00:00", "Z"),
         "o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": 1000}
        for i, c in enumerate(closes)
    ]


STATE_DIR = tempfile.mkdtemp()


def cfg(**kw):
    c = Config(api_key="k", api_secret="s")
    c.symbols = ["AAPL"]
    c.scan_stocks = False
    c.world_symbols = []
    c.crypto_symbols = []
    c.crypto_timeframe = "5Min"
    c.state_file = os.path.join(STATE_DIR, f"state-{id(c)}.json")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# Downtrend then a sharp turn up -> bullish crossover on the final bar.
DOWN_THEN_UP = [100 - 0.3 * i for i in range(30)] + [91 + i for i in range(15)]


def crossover_series():
    c = cfg()
    # find the first prefix whose last bar is the crossover
    for n in range(c.slow_ema + 2, len(DOWN_THEN_UP) + 1):
        if trend(make_bars(DOWN_THEN_UP[:n]), c).action == "buy":
            return DOWN_THEN_UP[:n]
    raise AssertionError("fixture never crosses")


class IndicatorTests(unittest.TestCase):
    def test_ema_constant(self):
        self.assertAlmostEqual(ema([5.0] * 20, 9)[-1], 5.0)
        self.assertEqual(ema([1, 2], 9), [])

    def test_rsi_extremes(self):
        self.assertEqual(rsi(list(range(30)), 14), 100.0)
        self.assertLess(rsi(list(range(30, 0, -1)), 14), 1)

    def test_atr_constant_range(self):
        closes = [10.0] * 30
        self.assertAlmostEqual(atr([c + 1 for c in closes], [c - 1 for c in closes], closes), 2.0)


class StrategyTests(unittest.TestCase):
    def test_not_enough_bars(self):
        self.assertEqual(trend(make_bars([1, 2, 3]), cfg()).action, "hold")

    def test_buy_on_bullish_cross(self):
        sig = trend(make_bars(crossover_series()), cfg())
        self.assertEqual(sig.action, "buy")
        self.assertLess(sig.stop, sig.price)
        self.assertGreater(sig.target, sig.price)

    def test_sell_on_bearish_cross(self):
        up_then_down = [100 + 0.3 * i for i in range(30)] + [109 - i for i in range(15)]
        actions = [trend(make_bars(up_then_down[:n]), cfg()).action
                   for n in range(25, len(up_then_down) + 1)]
        self.assertIn("sell", actions)
        self.assertNotIn("buy", actions)

    def test_rsi_filter_blocks_entry(self):
        sig = trend(make_bars(crossover_series()), cfg(rsi_max_entry=1))
        self.assertEqual(sig.action, "hold")
        self.assertIn("overbought", sig.reason)


class OtherStrategyTests(unittest.TestCase):
    def test_mean_reversion_buys_bounce(self):
        closes = [100.0] * 20 + [100 - 1.0 * i for i in range(1, 16)]
        actions = []
        for step in range(8):
            closes.append(closes[-1] + 0.6)
            actions.append(mean_reversion(make_bars(closes), cfg()).action)
        self.assertIn("buy", actions)

    def test_mean_reversion_exits_at_average(self):
        closes = [100.0] * 20 + [95.0] * 5 + [101.0]
        self.assertEqual(mean_reversion(make_bars(closes), cfg()).action, "sell")

    def test_breakout_needs_volume(self):
        bars = make_bars([100.0] * 25 + [103.0])
        self.assertEqual(breakout(bars, cfg()).action, "hold")
        bars[-1]["v"] = 5000
        sig = breakout(bars, cfg())
        self.assertEqual(sig.action, "buy")
        self.assertLess(sig.stop, sig.price)


def scripted(entries):
    """Strategy that buys at the given bar counts with stop -2 / target +4."""
    def fn(bars, c):
        price = bars[-1]["c"]
        if len(bars) in entries:
            return Signal("buy", price=price, stop=price - 2, target=price + 4)
        return Signal("hold", price=price)
    return fn


class BacktestTests(unittest.TestCase):
    def test_target_and_stop(self):
        c = cfg(cost_pct=0)
        closes = [100.0] * 40
        bars = make_bars(closes)
        bars[32]["h"] = 105  # target hit after entry at bar 31 (window length 32)
        bars[36]["l"] = 97   # stop hit after entry at bar 35
        self.assertEqual(backtest(scripted({32, 36}), bars, c), [2.0, -1.0])

    def test_stop_wins_when_both_touched(self):
        bars = make_bars([100.0] * 40)
        bars[32]["h"], bars[32]["l"] = 105, 97
        self.assertEqual(backtest(scripted({32}), bars, cfg(cost_pct=0)), [-1.0])

    def test_exit_at_end_of_day(self):
        bars = make_bars([100.0] * 40)
        for b in bars[34:]:
            b["t"] = "2026-09-25" + b["t"][10:]
        bars[33]["c"] = 101.0
        self.assertEqual(backtest(scripted({32}), bars, cfg(cost_pct=0)), [0.5])


class LearnerTests(unittest.TestCase):
    def test_picks_best_and_shrinks_small_samples(self):
        results = {"trend": [0.5] * 20, "mean_reversion": [3.0], "breakout": [-1.0] * 5}
        learner = Learner(cfg(), backtester=lambda fn, bars, c, **kw: results[fn.__name__])
        learner.update("AAPL", [])
        choice, scores = learner.choose("AAPL")
        self.assertEqual(choice, "trend")  # one lucky 3R trade shouldn't beat a steady record
        self.assertLess(scores["breakout"], 0)

    def test_live_results_persist_and_change_choice(self):
        c = cfg()
        learner = Learner(c, backtester=lambda fn, bars, c, **kw: [0.2] * 10 if fn is trend else [0.1] * 10)
        learner.update("AAPL", [])
        self.assertEqual(learner.choose("AAPL")[0], "trend")
        for _ in range(10):
            learner.record_trade("AAPL", "trend", -1.0)
        reloaded = Learner(c, backtester=learner.backtester)
        reloaded.update("AAPL", [])
        self.assertNotEqual(reloaded.choose("AAPL")[0], "trend")


class RiskTests(unittest.TestCase):
    def test_size_by_risk(self):
        # risk $1000 (1% of 100k), $2/share -> 500 shares, cap 20% = 200 shares at $100
        self.assertEqual(position_size(100_000, 1e9, 100, 98, cfg()), 200)
        self.assertEqual(position_size(100_000, 1e9, 100, 90, cfg()), 100)

    def test_fractional_crypto_size(self):
        qty = position_size(100_000, 1e9, 60_000, 59_000, cfg(), fractional=True)
        self.assertAlmostEqual(qty, 0.333333)
        self.assertEqual(position_size(100_000, 5, 60_000, 59_000, cfg(), fractional=True), 0)  # < $10

    def test_size_limited_by_cash(self):
        self.assertEqual(position_size(100_000, 500, 100, 90, cfg()), 5)

    def test_invalid_stop(self):
        self.assertEqual(position_size(100_000, 1e9, 100, 101, cfg()), 0)

    def test_daily_loss(self):
        self.assertTrue(daily_loss_hit({"equity": "96000", "last_equity": "100000"}, cfg()))
        self.assertFalse(daily_loss_hit({"equity": "99000", "last_equity": "100000"}, cfg()))


class ConfigTests(unittest.TestCase):
    def test_refuses_live_url(self):
        env = {"ALPACA_API_KEY_ID": "k", "ALPACA_API_SECRET_KEY": "s",
               "ALPACA_BASE_URL": "https://api.alpaca.markets"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                Config.from_env()

    def test_paper_url_normalised(self):
        env = {"ALPACA_API_KEY_ID": "k", "ALPACA_API_SECRET_KEY": "s",
               "ALPACA_BASE_URL": "https://paper-api.alpaca.markets"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(Config.from_env().base_url, "https://paper-api.alpaca.markets/v2")


class FakeClient:
    def __init__(self, bars, positions=(), minutes_to_close=120, equity="100000", last_equity="100000",
                 is_open=True, actives=()):
        self.bars = bars
        self.positions = list(positions)
        self.now = datetime.fromisoformat(bars[-1]["t"].replace("Z", "+00:00")) + timedelta(minutes=5)
        self.close = self.now + timedelta(minutes=minutes_to_close)
        self.equity, self.last_equity = equity, last_equity
        self.orders, self.closed, self.closed_all = [], [], 0
        self.sell_fill = None
        self.open_orders = []
        self.is_open, self.actives = is_open, list(actives)
        self.bar_requests = []

    def get_clock(self):
        return {"is_open": self.is_open, "timestamp": self.now.isoformat(),
                "next_close": self.close.isoformat(), "next_open": ""}

    def get_account(self):
        return {"equity": self.equity, "last_equity": self.last_equity, "buying_power": "200000",
                "non_marginable_buying_power": "100000"}

    def get_most_active_stocks(self, top):
        return self.actives

    def get_positions(self):
        return self.positions

    def get_open_orders(self):
        return self.open_orders

    def get_bars(self, symbol, timeframe, lookback_days=5):
        self.bar_requests.append(symbol)
        return self.bars

    def get_last_sell_fill(self, symbol, after):
        return self.sell_fill

    def submit_buy(self, symbol, qty, tp, sl, client_order_id=None):
        self.orders.append((symbol, qty, tp, sl))
        self.open_orders.append({"symbol": symbol, "side": "buy"})
        self.order_ids = getattr(self, "order_ids", []) + [client_order_id]

    def close_position(self, symbol):
        self.closed.append(symbol)
        self.positions = [p for p in self.positions if p["symbol"] != symbol.replace("/", "")]

    def close_all_positions(self):
        self.closed_all += 1


def trend_learner(c=None):
    """Learner whose replay always says trend wins, so bot tests are deterministic."""
    def fake_backtest(fn, bars, c, **kw):
        return [1.0] * 10 if fn is trend else [-1.0] * 10
    return Learner(c or cfg(), backtester=fake_backtest)


class BotTests(unittest.TestCase):
    def test_places_bracket_buy_once_per_bar(self):
        client = FakeClient(make_bars(crossover_series()))
        bot = TradingBot(cfg(), learner=trend_learner(), client=client)
        bot.run_once()
        bot.last_bar_seen.clear()  # force a re-fetch of the same bar
        bot.run_once()  # same bar again: must not double-buy
        self.assertEqual(len(client.orders), 1)
        symbol, qty, tp, sl = client.orders[0]
        self.assertEqual(symbol, "AAPL")
        self.assertGreater(qty, 0)
        self.assertLess(sl, tp)
        self.assertTrue(client.order_ids[0].startswith("trend-AAPL-"))
        self.assertEqual(bot.learner.state["active"]["AAPL"], "trend")
        self.assertIn("AAPL", bot.open_trades)

    def test_learns_from_closed_trade(self):
        client = FakeClient(make_bars(crossover_series()))
        bot = TradingBot(cfg(), learner=trend_learner(), client=client)
        bot.open_trades["AAPL"] = {"strategy": "trend", "entry": 100.0, "stop": 98.0, "opened_at": "x"}
        client.sell_fill = 104.0
        bot.run_once()
        self.assertEqual(bot.learner.state["live_results"]["trend"]["AAPL"], [2.0])

    def test_sits_out_when_nothing_works(self):
        c = cfg()
        learner = Learner(c, backtester=lambda fn, bars, c, **kw: [-1.0] * 10)
        client = FakeClient(make_bars(crossover_series()))
        TradingBot(c, learner=learner, client=client).run_once()
        self.assertEqual(client.orders, [])
        self.assertIsNone(learner.state["active"]["AAPL"])

    def test_dry_run_places_nothing(self):
        client = FakeClient(make_bars(crossover_series()))
        TradingBot(cfg(), learner=trend_learner(), client=client, dry_run=True).run_once()
        self.assertEqual(client.orders, [])

    def test_no_entries_near_close(self):
        client = FakeClient(make_bars(crossover_series()), minutes_to_close=20)
        TradingBot(cfg(), learner=trend_learner(), client=client).run_once()
        self.assertEqual(client.orders, [])

    def test_flattens_at_end_of_day(self):
        client = FakeClient(make_bars(crossover_series()), positions=[{"symbol": "AAPL", "asset_class": "us_equity", "avg_entry_price": "100"},
                                       {"symbol": "BTCUSD", "asset_class": "crypto", "avg_entry_price": "100"}],
                            minutes_to_close=5)
        TradingBot(cfg(), learner=trend_learner(), client=client).run_once()
        self.assertEqual(client.closed, ["AAPL"])  # crypto keeps trading overnight

    def test_daily_loss_halts(self):
        client = FakeClient(make_bars(crossover_series()), positions=[{"symbol": "AAPL", "avg_entry_price": "100"}],
                            equity="95000", last_equity="100000")
        TradingBot(cfg(), learner=trend_learner(), client=client).run_once()
        self.assertEqual(client.closed_all, 1)
        self.assertEqual(client.orders, [])

    def test_crypto_trades_when_stock_market_closed(self):
        c = cfg(crypto_symbols=["BTC/USD"])
        client = FakeClient(make_bars(crossover_series()), is_open=False)
        bot = TradingBot(c, learner=trend_learner(c), client=client)
        bot.run_once()
        self.assertEqual(client.bar_requests, ["BTC/USD"])  # stocks skipped while closed
        self.assertEqual(len(client.orders), 1)
        symbol, qty, tp, sl = client.orders[0]
        self.assertEqual(symbol, "BTC/USD")
        self.assertNotEqual(qty, int(qty))  # fractional coins
        self.assertIn("target", bot.open_trades["BTC/USD"])

    def test_crypto_stop_loss_enforced_by_bot(self):
        c = cfg(crypto_symbols=["BTC/USD"])
        client = FakeClient(make_bars(crossover_series()), is_open=False,
                            positions=[{"symbol": "BTCUSD", "asset_class": "crypto",
                                        "avg_entry_price": "100", "current_price": "97"}])
        bot = TradingBot(c, learner=trend_learner(c), client=client)
        bot.open_trades["BTC/USD"] = {"strategy": "trend", "entry": 100.0, "stop": 98.0, "target": 104.0,
                                      "opened_at": "x"}
        bot.run_once()
        self.assertEqual(client.closed, ["BTC/USD"])

    def test_universe_has_world_and_filtered_scan(self):
        c = cfg(scan_stocks=True, world_symbols=["EWJ"])
        client = FakeClient(make_bars(crossover_series()),
                            actives=[("NVDA", 225.0, 5e8), ("PENNY", 0.5, 5e8), ("THIN", 50.0, 1e5)])
        bot = TradingBot(c, learner=trend_learner(c), client=client)
        self.assertEqual(bot.stock_universe(client.now), ["AAPL", "EWJ", "NVDA"])

    def test_only_fetches_when_new_bar_due(self):
        client = FakeClient(make_bars(crossover_series()))
        bot = TradingBot(cfg(), learner=trend_learner(), client=client)
        bot.run_once()
        bot.run_once()
        self.assertEqual(client.bar_requests, ["AAPL"])

    def test_drops_forming_bar(self):
        bars = make_bars([1, 2, 3])
        last = datetime.fromisoformat(bars[-1]["t"].replace("Z", "+00:00"))
        self.assertEqual(len(completed_bars(bars, "5Min", last + timedelta(minutes=2))), 2)
        self.assertEqual(len(completed_bars(bars, "5Min", last + timedelta(minutes=5))), 3)


if __name__ == "__main__":
    unittest.main()
