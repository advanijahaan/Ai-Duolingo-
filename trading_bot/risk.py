"""Position sizing and account-level safety checks."""
import math


def position_size(equity, buying_power, entry, stop, cfg, fractional=False, side="long"):
    """Shares (or coins) to trade so that hitting the stop loses ~risk_per_trade of equity."""
    risk_per_share = entry - stop if side == "long" else stop - entry
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


def option_contracts(equity, buying_power, premium, cfg):
    """Contracts to buy so that losing option_stop_pct of the premium costs ~risk_per_trade of equity."""
    cost = premium * 100
    if cost <= 0:
        return 0
    by_risk = equity * cfg.risk_per_trade / (cost * cfg.option_stop_pct)
    by_cap = equity * cfg.max_position_pct / cost
    by_cash = buying_power / cost
    return max(0, math.floor(min(by_risk, by_cap, by_cash)))


def pick_option(candidates, cfg):
    """Choose the contract closest to the target delta with a tight enough spread.

    Deltas within 0.05 of each other count as equally close; then the nearest expiry wins (cheaper).
    """
    good = []
    for c in candidates:
        mid = (c["bid"] + c["ask"]) / 2
        if mid > 0 and (c["ask"] - c["bid"]) / mid <= cfg.option_max_spread:
            good.append(c)
    if not good:
        return None
    return min(good, key=lambda c: (int(abs(abs(c["delta"]) - cfg.option_target_delta) / 0.05), c["expiration"]))
