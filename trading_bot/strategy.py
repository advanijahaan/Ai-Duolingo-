"""Pure indicator and signal logic (no network), so it is easy to test.

Strategy: trend-following EMA crossover on intraday bars.
  * BUY  when the fast EMA crosses above the slow EMA and RSI isn't overbought.
  * SELL when the fast EMA crosses back below the slow EMA.
Stops and targets are set from ATR so they scale with each stock's volatility.
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


def generate_signal(bars, cfg):
    """bars: list of dicts with Alpaca keys o/h/l/c/v, oldest first."""
    needed = max(cfg.slow_ema, cfg.rsi_period, cfg.atr_period) + 2
    if len(bars) < needed:
        return Signal("hold", reason=f"not enough bars ({len(bars)}/{needed})")

    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]

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
        vol = atr(highs, lows, closes, cfg.atr_period)
        if not vol:
            return Signal("hold", price=price, reason="ATR unavailable")
        return Signal(
            "buy",
            price=price,
            stop=price - cfg.stop_atr_mult * vol,
            target=price + cfg.take_profit_atr_mult * vol,
            reason=f"bullish EMA crossover, RSI {r:.1f}, ATR {vol:.2f}",
        )

    return Signal("hold", price=price, reason="no crossover")
