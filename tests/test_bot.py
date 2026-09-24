import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from trading_bot.bot import TradingBot, completed_bars
from trading_bot.config import Config
from trading_bot.risk import daily_loss_hit, position_size
from trading_bot.strategy import atr, ema, generate_signal, rsi

T0 = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)


def make_bars(closes):
    return [
        {"t": (T0 + timedelta(minutes=5 * i)).isoformat().replace("+00:00", "Z"),
         "o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": 1000}
        for i, c in enumerate(closes)
    ]


def cfg(**kw):
    c = Config(api_key="k", api_secret="s")
    c.symbols = ["AAPL"]
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# Downtrend then a sharp turn up -> bullish crossover on the final bar.
DOWN_THEN_UP = [100 - 0.3 * i for i in range(30)] + [91 + i for i in range(15)]


def crossover_series():
    c = cfg()
    # find the first prefix whose last bar is the crossover
    for n in range(c.slow_ema + 2, len(DOWN_THEN_UP) + 1):
        if generate_signal(make_bars(DOWN_THEN_UP[:n]), c).action == "buy":
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
        self.assertEqual(generate_signal(make_bars([1, 2, 3]), cfg()).action, "hold")

    def test_buy_on_bullish_cross(self):
        sig = generate_signal(make_bars(crossover_series()), cfg())
        self.assertEqual(sig.action, "buy")
        self.assertLess(sig.stop, sig.price)
        self.assertGreater(sig.target, sig.price)

    def test_sell_on_bearish_cross(self):
        up_then_down = [100 + 0.3 * i for i in range(30)] + [109 - i for i in range(15)]
        actions = [generate_signal(make_bars(up_then_down[:n]), cfg()).action
                   for n in range(25, len(up_then_down) + 1)]
        self.assertIn("sell", actions)
        self.assertNotIn("buy", actions)

    def test_rsi_filter_blocks_entry(self):
        sig = generate_signal(make_bars(crossover_series()), cfg(rsi_max_entry=1))
        self.assertEqual(sig.action, "hold")
        self.assertIn("overbought", sig.reason)


class RiskTests(unittest.TestCase):
    def test_size_by_risk(self):
        # risk $1000 (1% of 100k), $2/share -> 500 shares, cap 20% = 200 shares at $100
        self.assertEqual(position_size(100_000, 1e9, 100, 98, cfg()), 200)
        self.assertEqual(position_size(100_000, 1e9, 100, 90, cfg()), 100)

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
    def __init__(self, bars, positions=(), minutes_to_close=120, equity="100000", last_equity="100000"):
        self.bars = bars
        self.positions = list(positions)
        self.now = datetime.fromisoformat(bars[-1]["t"].replace("Z", "+00:00")) + timedelta(minutes=5)
        self.close = self.now + timedelta(minutes=minutes_to_close)
        self.equity, self.last_equity = equity, last_equity
        self.orders, self.closed, self.closed_all = [], [], 0

    def get_clock(self):
        return {"is_open": True, "timestamp": self.now.isoformat(),
                "next_close": self.close.isoformat(), "next_open": ""}

    def get_account(self):
        return {"equity": self.equity, "last_equity": self.last_equity, "buying_power": "200000"}

    def get_positions(self):
        return self.positions

    def get_open_orders(self):
        return []

    def get_bars(self, symbol, timeframe):
        return self.bars

    def submit_bracket_buy(self, symbol, qty, tp, sl):
        self.orders.append((symbol, qty, tp, sl))

    def close_position(self, symbol):
        self.closed.append(symbol)

    def close_all_positions(self):
        self.closed_all += 1


class BotTests(unittest.TestCase):
    def test_places_bracket_buy_once_per_bar(self):
        client = FakeClient(make_bars(crossover_series()))
        bot = TradingBot(cfg(), client=client)
        bot.run_once()
        bot.run_once()  # same bar again: must not double-buy
        self.assertEqual(len(client.orders), 1)
        symbol, qty, tp, sl = client.orders[0]
        self.assertEqual(symbol, "AAPL")
        self.assertGreater(qty, 0)
        self.assertLess(sl, tp)

    def test_dry_run_places_nothing(self):
        client = FakeClient(make_bars(crossover_series()))
        TradingBot(cfg(), client=client, dry_run=True).run_once()
        self.assertEqual(client.orders, [])

    def test_no_entries_near_close(self):
        client = FakeClient(make_bars(crossover_series()), minutes_to_close=20)
        TradingBot(cfg(), client=client).run_once()
        self.assertEqual(client.orders, [])

    def test_flattens_at_end_of_day(self):
        client = FakeClient(make_bars(crossover_series()), positions=[{"symbol": "AAPL"}], minutes_to_close=5)
        TradingBot(cfg(), client=client).run_once()
        self.assertEqual(client.closed_all, 1)

    def test_daily_loss_halts(self):
        client = FakeClient(make_bars(crossover_series()), positions=[{"symbol": "AAPL"}],
                            equity="95000", last_equity="100000")
        TradingBot(cfg(), client=client).run_once()
        self.assertEqual(client.closed_all, 1)
        self.assertEqual(client.orders, [])

    def test_drops_forming_bar(self):
        bars = make_bars([1, 2, 3])
        last = datetime.fromisoformat(bars[-1]["t"].replace("Z", "+00:00"))
        self.assertEqual(len(completed_bars(bars, "5Min", last + timedelta(minutes=2))), 2)
        self.assertEqual(len(completed_bars(bars, "5Min", last + timedelta(minutes=5))), 3)


if __name__ == "__main__":
    unittest.main()
