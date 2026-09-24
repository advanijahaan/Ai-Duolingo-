# Run anywhere with Docker:  docker build -t trading-bot . &&
#   docker run -d --restart always --env-file .env -v bot-data:/data --name trading-bot trading-bot
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY trading_bot ./trading_bot
ENV BOT_STATE_FILE=/data/bot_state.json PYTHONUNBUFFERED=1
VOLUME /data
CMD ["python", "-m", "trading_bot.bot"]
