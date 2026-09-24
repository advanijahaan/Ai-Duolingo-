"""Thin REST wrapper around the Alpaca trading and market-data APIs."""
from datetime import datetime, timedelta, timezone

import requests


class AlpacaError(RuntimeError):
    pass


class AlpacaClient:
    def __init__(self, config, session=None):
        self.cfg = config
        self.session = session or requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": config.api_key,
            "APCA-API-SECRET-KEY": config.api_secret,
        })

    def _request(self, method, url, **kwargs):
        resp = self.session.request(method, url, timeout=15, **kwargs)
        if resp.status_code >= 400:
            raise AlpacaError(f"{method} {url} -> {resp.status_code}: {resp.text}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _trade(self, method, path, **kwargs):
        return self._request(method, f"{self.cfg.base_url}{path}", **kwargs)

    # --- account / market state ---
    def get_account(self):
        return self._trade("GET", "/account")

    def get_clock(self):
        return self._trade("GET", "/clock")

    def get_positions(self):
        return self._trade("GET", "/positions")

    def get_open_orders(self):
        return self._trade("GET", "/orders", params={"status": "open", "limit": 500})

    # --- orders ---
    def submit_bracket_buy(self, symbol, qty, take_profit, stop_loss):
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{take_profit:.2f}"},
            "stop_loss": {"stop_price": f"{stop_loss:.2f}"},
        }
        return self._trade("POST", "/orders", json=body)

    def close_position(self, symbol):
        # Cancel the bracket legs first, otherwise the shares are held by them.
        for order in self.get_open_orders() or []:
            if order["symbol"] == symbol:
                self._trade("DELETE", f"/orders/{order['id']}")
        return self._trade("DELETE", f"/positions/{symbol}")

    def close_all_positions(self):
        return self._trade("DELETE", "/positions", params={"cancel_orders": "true"})

    # --- market data ---
    def get_bars(self, symbol, timeframe, lookback_days=5):
        start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start,
            "limit": 10000,
            "feed": self.cfg.data_feed,
            "adjustment": "raw",
        }
        bars = []
        while True:
            data = self._request("GET", f"{self.cfg.data_url}/stocks/bars", params=params)
            bars.extend((data.get("bars") or {}).get(symbol, []))
            token = data.get("next_page_token")
            if not token:
                return bars
            params["page_token"] = token
