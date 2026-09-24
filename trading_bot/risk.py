"""Position sizing and account-level safety checks."""
import math


def position_size(equity, buying_power, entry, stop, cfg, fractional=False):
    """Shares (or coins) to buy so that hitting the stop loses ~risk_per_trade of equity."""
    risk_per_share = entry - stop
    if entry <= 0 or risk_per_share <= 0:
        return 0
    by_risk = (equity * cfg.risk_per_trade) / risk_per_share
    by_cap = (equity * cfg.max_position_pct) / entry
    by_cash = buying_power / entry
    qty = min(by_risk, by_cap, by_cash)
    if fractional:
        qty = math.floor(qty * 1e6) / 1e6
        return qty if qty * entry >= cfg.min_order_dollars else 0
    return max(0, math.floor(qty))


def daily_loss_hit(account, cfg):
    """True when equity has fallen more than daily_loss_limit since yesterday's close."""
    equity = float(account["equity"])
    last_equity = float(account["last_equity"])
    if last_equity <= 0:
        return False
    return (equity - last_equity) / last_equity <= -cfg.daily_loss_limit
