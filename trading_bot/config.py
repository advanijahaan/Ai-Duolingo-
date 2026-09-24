"""Configuration loaded from environment variables.

API keys are never hard-coded: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY
(from the Alpaca paper dashboard) in your shell or a .env file.
"""
import os
from dataclasses import dataclass, field

PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"
DATA_BASE_URL = "https://data.alpaca.markets/v2"


def _load_dotenv(path=".env"):
    """Minimal .env loader so no extra dependency is needed."""
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_list(name, default):
    raw = os.getenv(name)
    if not raw:
        return default
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


@dataclass
class Config:
    api_key: str
    api_secret: str
    base_url: str = PAPER_BASE_URL
    data_url: str = DATA_BASE_URL
    data_feed: str = "iex"  # free accounts only get the IEX feed

    # Stocks: these are always watched, plus the most-traded stocks of the day (scan)
    symbols: list = field(default_factory=lambda: ["SPY", "QQQ"])
    # World markets through US-listed funds and foreign companies (Alpaca can't reach foreign exchanges)
    world_symbols: list = field(default_factory=lambda: [
        # country / region ETFs
        "EFA", "EEM", "VGK", "EWJ", "FXI", "KWEB", "MCHI", "INDA", "EWZ", "EWU", "EWC", "EWY", "EWT",
        # big foreign companies listed in the US
        "TSM", "ASML", "BABA", "PDD", "NVO", "SAP", "SHEL", "BP", "SHOP", "MELI", "NU",
        # commodities and bonds
        "GLD", "SLV", "USO", "UNG", "TLT"])
    scan_stocks: bool = True
    scan_top: int = 50               # how many most-active stocks to look at
    max_scanned: int = 40            # how many of them to trade after filtering
    min_price: float = 10.0          # skip penny stocks
    min_dollar_volume: float = 20e6  # skip thinly traded stocks
    rescan_minutes: int = 30
    # Crypto trades 24/7
    crypto_symbols: list = field(default_factory=lambda: [
        "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD",
        "LTC/USD", "AVAX/USD", "LINK/USD", "ADA/USD", "DOT/USD"])
    crypto_timeframe: str = "1Hour"  # 5-minute crypto moves are too small to beat the fees
    crypto_learn_days: int = 30
    crypto_cost_pct: float = 0.005   # Alpaca crypto fees are ~0.25% per side
    min_order_dollars: float = 10.0
    timeframe: str = "5Min"
    poll_seconds: int = 60

    # Strategy
    fast_ema: int = 9
    slow_ema: int = 21
    rsi_period: int = 14
    rsi_max_entry: float = 70.0
    atr_period: int = 14
    stop_atr_mult: float = 1.5
    take_profit_atr_mult: float = 3.0
    # mean reversion
    mr_sma: int = 20
    mr_oversold: float = 30.0
    mr_exit_rsi: float = 55.0
    mr_target_atr_mult: float = 2.0
    # breakout
    breakout_lookback: int = 20
    breakout_exit_lookback: int = 10
    breakout_volume_mult: float = 1.5
    breakout_stop_atr_mult: float = 2.0
    breakout_target_atr_mult: float = 4.0

    # Learning: which strategy each symbol uses
    learn_days: int = 10          # calendar days of history to replay each strategy on
    prior_strength: float = 10.0  # trades' worth of "assume average" before trusting a symbol's own record
    live_weight: float = 2.0      # a real trade counts as much as this many replayed ones
    min_score: float = 0.05       # sit a symbol out unless the best strategy expects > this many R per trade
    cost_pct: float = 0.0005      # assumed round-trip slippage when replaying
    state_file: str = "bot_state.json"

    # Risk
    risk_per_trade: float = 0.01      # risk 1% of equity per trade
    max_position_pct: float = 0.20    # never put more than 20% of equity in one name
    max_open_positions: int = 8
    daily_loss_limit: float = 0.03    # stop trading after -3% on the day
    no_new_entries_minutes: int = 30  # before close
    flatten_minutes: int = 10         # close everything this long before close

    @classmethod
    def from_env(cls):
        _load_dotenv()
        key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("APCA_API_KEY_ID")
        secret = os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
        if not key or not secret:
            raise SystemExit(
                "Missing API keys. Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY "
                "(see .env.example)."
            )
        base_url = os.getenv("ALPACA_BASE_URL", PAPER_BASE_URL).rstrip("/")
        if not base_url.endswith("/v2"):
            base_url += "/v2"
        if "paper-api" not in base_url and os.getenv("ALPACA_ALLOW_LIVE") != "1":
            raise SystemExit(
                f"Refusing to trade against {base_url}: this bot is for paper trading. "
                "Set ALPACA_ALLOW_LIVE=1 only if you really mean to use real money."
            )
        cfg = cls(api_key=key, api_secret=secret, base_url=base_url)
        cfg.symbols = _env_list("BOT_SYMBOLS", cfg.symbols)
        if os.getenv("BOT_WORLD", "").lower() in ("0", "off", "false", "no"):
            cfg.world_symbols = []
        if os.getenv("BOT_SCAN_STOCKS", "").lower() in ("0", "off", "false", "no"):
            cfg.scan_stocks = False
        crypto = os.getenv("BOT_CRYPTO", "")
        if crypto.lower() in ("0", "off", "false", "no"):
            cfg.crypto_symbols = []
        elif crypto:
            cfg.crypto_symbols = _env_list("BOT_CRYPTO", cfg.crypto_symbols)
        cfg.data_feed = os.getenv("ALPACA_DATA_FEED", cfg.data_feed)
        return cfg
