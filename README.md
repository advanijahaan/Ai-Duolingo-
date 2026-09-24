# Ai-Duolingo-
Duolingo for free

## AI day-trading bot (Alpaca paper trading)

A bot that day-trades stocks with **fake money** on Alpaca's paper account. It learns which strategy works
for each stock and switches on its own.

### How to see what it's making (phone or computer)

1. Go to **https://app.alpaca.markets** in any web browser (phone or computer) and log in.
2. At the top left, make sure your **Paper** account is selected. It shows a "Paper" label and starts with $100,000.
3. You'll see:
   - **Home:** your total money (equity) and today's gain or loss.
   - **Positions:** the stocks the bot owns right now and how much each is up or down.
   - **Orders:** every buy and sell the bot made. Click an order to open its details. The **Client Order ID**
     starts with the strategy the AI used, e.g. `breakout-AMD-1a2b3c4d`.

The bot only trades while the US stock market is open: Monday to Friday, 9:30 AM to 4:00 PM Eastern.
It sells everything a few minutes before the close, so overnight you'll see cash and no positions.

### How to run it on your computer (one-time setup, ~5 minutes)

1. **Install Python** from https://www.python.org/downloads/ (on Windows, tick "Add Python to PATH").
2. **Download this project.** On GitHub click the green **Code** button → **Download ZIP**, then unzip it.
3. **Open a terminal in the project folder.**
   - Windows: open the folder, click the address bar, type `cmd`, press Enter.
   - Mac: right-click the folder → **New Terminal at Folder**.
4. **Install what it needs:** `pip install -r requirements.txt`
5. **Add your keys.** Make a copy of `.env.example` and name it `.env`. Open it in Notepad or TextEdit and paste your
   paper **API Key** and **Secret Key**. Get them at https://app.alpaca.markets (Paper account) → **API Keys** on the home page.
   Never share `.env` or upload it anywhere.
6. **Test it** (places no orders): `python -m trading_bot.bot --dry-run --once`
7. **Start it:** `python -m trading_bot.bot`
   Leave the window open while the market is open. Close it or press **Ctrl+C** to stop.
   Stopping is safe: any open trades keep their automatic stop-loss and take-profit orders.

To see what the AI has learned and which strategy each stock is using: `python -m trading_bot.bot --report`

### How the AI works

The bot has 3 strategies:

| Strategy | Buys when… | Sells when… |
|---|---|---|
| `trend` | the short-term average price crosses above the longer-term one | it crosses back below |
| `mean_reversion` | a stock that dropped too far starts bouncing back (RSI climbs back above 30) | price gets back to its average |
| `breakout` | price breaks above its recent high on heavy volume | price falls below its recent low |

Every 5 minutes, for each stock, the bot:
1. **Replays** all 3 strategies on the last ~2 weeks of prices to see which would have made money.
2. **Remembers** how its real trades turned out, saved in `bot_state.json`. Real trades count double.
3. **Switches** each stock to the strategy that's working best. If none are making money, it **sits that stock out**.
   The log shows lines like `AMD: AI switched strategy trend -> breakout`.

It doesn't trust a strategy because of a few lucky trades. A strategy needs a steady record before the bot uses it.

**Safety limits:** every buy comes with a stop-loss and a take-profit. Each trade risks about 1% of the account.
No stock gets more than 20% of the money, and the bot holds at most 5 stocks at once. If the account is down 3% on
the day, it sells everything and stops until the next day. It won't connect to a real-money account unless you set
`ALPACA_ALLOW_LIVE=1`.

Settings live in `trading_bot/config.py`. For example, lowering `min_score` makes the bot trade more often, with less proof
that a strategy works. Stock list: set `BOT_SYMBOLS=SPY,AAPL,...` in `.env`.

This is a learning project with fake money. How it does on paper doesn't predict real results.

Developers: run the tests with `python -m unittest discover -s tests`.
