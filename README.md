# Ai-Duolingo-
Duolingo for free

## AI day-trading bot (Alpaca paper trading)

`trading_bot/` is a bot that trades during the day on Alpaca's **paper** (simulated money) account at
`https://paper-api.alpaca.markets/v2`. It won't connect to a live-money URL unless you set `ALPACA_ALLOW_LIVE=1`.

### Setup
```bash
pip install -r requirements.txt
cp .env.example .env   # then paste your paper API key + secret into .env
```
Get keys at https://app.alpaca.markets → switch to the Paper account → API Keys.

### Run
```bash
python -m trading_bot.bot --dry-run --once   # check keys/data; logs decisions, places no orders
python -m trading_bot.bot                    # trade in a loop (polls every 60s)
python -m unittest discover -s tests         # run the tests
```

### How it trades
- **Watchlist:** SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA, META (override with `BOT_SYMBOLS`), on 5-minute bars.
- **Entry:** 9 EMA crosses above 21 EMA and RSI(14) < 70. It buys at market with a **bracket order**.
  The stop is 1.5×ATR below the entry and the take-profit is 3×ATR above it.
- **Exit:** the stop or take-profit fills, or the 9 EMA crosses back below the 21 EMA.
- **Risk:** each trade risks about 1% of equity, with at most 20% of equity in one stock and at most 5 positions.
  If the account is down 3% on the day, the bot sells everything and stops until tomorrow.
  It opens no new trades in the last 30 minutes and sells everything 10 minutes before the close.

Tune any of these in `trading_bot/config.py`. This is a learning project: past behaviour on paper doesn't predict real results.
