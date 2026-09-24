"""Position sizing and account-level safety checks."""
import math
from dataclasses import dataclass


@dataclass
class AccountLimits:
    name: str
    max_positions: int
    max_position_pct: float
    shorts: bool
    options: bool
    pdt_limited: bool

    def describe(self):
        extras = [f"up to {self.max_positions} positions of {self.max_position_pct:.0%} each",
                  "shorts on" if self.shorts else "no shorts",
                  "options on" if self.options else "no options"]
        if self.pdt_limited:
            extras.append("day-trade limit on")
        return f"{self.name} mode: " + ", ".join(extras)


def account_limits(equity, cfg):
    """What the bot may do at this account size. Small accounts hold fewer, bigger positions;
    shorting and options need margin_min_equity; below pdt_equity day trades are rationed."""
    if equity < 100:
        name, max_positions, pct = "tiny account", min(2, cfg.max_open_positions), 0.5
    elif equity < cfg.margin_min_equity:
        name, max_positions, pct = "small account", min(4, cfg.max_open_positions), 0.25
    else:
        name, max_positions, pct = "full", cfg.max_open_positions, cfg.max_position_pct
    big_enough = equity >= cfg.margin_min_equity
    return AccountLimits(
        name=name, max_positions=max_positions, max_position_pct=pct,
        shorts=cfg.allow_shorts and big_enough,
        options=bool(cfg.options_underlyings) and big_enough,
        pdt_limited=cfg.pdt_protection and equity < cfg.pdt_equity,
    )


def position_size(equity, buying_power, entry, stop, cfg, fractional=False, side="long", max_pct=None):
    """Shares (or coins) to trade so that hitting the stop loses ~risk_per_trade of equity."""
    risk_per_share = entry - stop if side == "long" else stop - entry
    if entry <= 0 or risk_per_share <= 0:
        return 0
    by_risk = (equity * cfg.risk_per_trade) / risk_per_share
    by_cap = (equity * (max_pct or cfg.max_position_pct)) / entry
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
