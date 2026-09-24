"""Pure indicator and strategy logic (no network), so it is easy to test.

Each strategy looks at closed bars (oldest first, Alpaca keys o/h/l/c/v) and
returns a Signal for the latest bar:
  * "buy"  - open a long position, with an ATR-based stop and target
  * "sell" - exit an open position
  * "hold" - do nothing
The learner (learner.py) decides which strategy each symbol should use.
"""
from dataclasses import dataclass


def ema(values, period):
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    # pad so out[i] lines up with values[i]
    return [None] * (period - 1) + out


def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = [], []
    for prev, cur in zip(values, values[1:]):
        change = cur - prev
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def atr(highs, lows, closes, period=14):
    if len(closes) <= period:
        return None
    trs = [
        max(h - l, abs(h - pc), abs(l - pc))
        for h, l, pc in zip(highs[1:], lows[1:], closes[:-1])
    ]
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


@dataclass
class Signal:
    action: str  # "buy", "sell" or "hold"
    price: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    reason: str = ""


def trend(bars, cfg):
    """Trend following: buy when the fast EMA crosses above the slow EMA."""
    needed = max(cfg.slow_ema, cfg.rsi_period, cfg.atr_period) + 2
    if len(bars) < needed:
        return Signal("hold", reason=f"not enough bars ({len(bars)}/{needed})")

    closes = [b["c"] for b in bars]
    fast = ema(closes, cfg.fast_ema)
    slow = ema(closes, cfg.slow_ema)
    price = closes[-1]
    crossed_up = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
    crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]

    if crossed_down:
        return Signal("sell", price=price, reason="fast EMA crossed below slow EMA")

    if crossed_up:
        r = rsi(closes, cfg.rsi_period)
        if r is not None and r >= cfg.rsi_max_entry:
            return Signal("hold", price=price, reason=f"crossover but RSI {r:.1f} overbought")
        return _entry(bars, cfg, cfg.stop_atr_mult, cfg.take_profit_atr_mult,
                      f"bullish EMA crossover, RSI {r:.1f}")

    return Signal("hold", price=price, reason="no crossover")


def mean_reversion(bars, cfg):
    """Buy the bounce: RSI climbs back out of oversold. Exit once price recovers to its average."""
    needed = max(cfg.mr_sma, cfg.rsi_period, cfg.atr_period) + 2
    if len(bars) < needed:
        return Signal("hold", reason=f"not enough bars ({len(bars)}/{needed})")

    closes = [b["c"] for b in bars]
    price = closes[-1]
    sma = sum(closes[-cfg.mr_sma:]) / cfg.mr_sma
    r_now, r_prev = rsi(closes, cfg.rsi_period), rsi(closes[:-1], cfg.rsi_period)

    if price >= sma or r_now >= cfg.mr_exit_rsi:
        return Signal("sell", price=price, reason=f"back to average (RSI {r_now:.1f})")
    if r_prev < cfg.mr_oversold <= r_now:
        return _entry(bars, cfg, cfg.stop_atr_mult, cfg.mr_target_atr_mult,
                      f"RSI bounced out of oversold ({r_prev:.1f} -> {r_now:.1f})")
    return Signal("hold", price=price, reason=f"RSI {r_now:.1f}, no bounce")


def breakout(bars, cfg):
    """Buy a fresh break above the recent high on above-average volume."""
    n = cfg.breakout_lookback
    needed = max(n, cfg.atr_period) + 2
    if len(bars) < needed:
        return Signal("hold", reason=f"not enough bars ({len(bars)}/{needed})")

    price, prev_close = bars[-1]["c"], bars[-2]["c"]
    prior = bars[-n - 1:-1]
    high = max(b["h"] for b in prior)
    exit_low = min(b["l"] for b in bars[-cfg.breakout_exit_lookback - 1:-1])
    avg_vol = sum(b["v"] for b in prior) / len(prior)

    if price < exit_low:
        return Signal("sell", price=price, reason=f"broke below {cfg.breakout_exit_lookback}-bar low")
    if price > high >= prev_close and bars[-1]["v"] >= cfg.breakout_volume_mult * avg_vol:
        return _entry(bars, cfg, cfg.breakout_stop_atr_mult, cfg.breakout_target_atr_mult,
                      f"broke {n}-bar high {high:.2f} on {bars[-1]['v'] / max(avg_vol, 1):.1f}x volume")
    return Signal("hold", price=price, reason="no breakout")


def _entry(bars, cfg, stop_mult, target_mult, reason):
    price = bars[-1]["c"]
    vol = atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], cfg.atr_period)
    if not vol:
        return Signal("hold", price=price, reason="ATR unavailable")
    return Signal("buy", price=price, stop=price - stop_mult * vol, target=price + target_mult * vol,
                  reason=f"{reason}, ATR {vol:.2f}")


STRATEGIES = {"trend": trend, "mean_reversion": mean_reversion, "breakout": breakout}
