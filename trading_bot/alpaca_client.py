"""Thin REST wrapper around the Alpaca trading and market-data APIs."""
from datetime import datetime, timedelta, timezone

import requests


class AlpacaError(RuntimeError):
    pass


def is_crypto(symbol):
    return "/" in symbol


def norm(symbol):
    """Alpaca reports crypto positions as BTCUSD but orders as BTC/USD; compare on this."""
    return symbol.replace("/", "")


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
    def submit_buy(self, symbol, qty, take_profit, stop_loss, client_order_id=None):
        """Stocks get a bracket order. Crypto can't use brackets, so the bot watches its stop/target itself."""
        if is_crypto(symbol):
            body = {"symbol": symbol, "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "gtc"}
            if client_order_id:
                body["client_order_id"] = client_order_id
            return self._trade("POST", "/orders", json=body)
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
        if client_order_id:
            body["client_order_id"] = client_order_id
        return self._trade("POST", "/orders", json=body)

    def get_last_sell_fill(self, symbol, after):
        """Average fill price of the most recent filled sell of `symbol` since `after` (ISO time)."""
        orders = self._trade("GET", "/orders", params={
            "status": "closed", "symbols": symbol, "after": after,
            "direction": "desc", "nested": "true", "limit": 50,
        }) or []
        fills = []
        for order in orders:
            for o in [order] + (order.get("legs") or []):
                if o["side"] == "sell" and o["status"] == "filled" and o.get("filled_avg_price"):
                    fills.append((o["filled_at"], float(o["filled_avg_price"])))
        return max(fills)[1] if fills else None

    def close_position(self, symbol):
        # Cancel the bracket legs first, otherwise the shares are held by them.
        for order in self.get_open_orders() or []:
            if norm(order["symbol"]) == norm(symbol):
                self._trade("DELETE", f"/orders/{order['id']}")
        return self._trade("DELETE", f"/positions/{norm(symbol)}")

    def close_all_positions(self):
        return self._trade("DELETE", "/positions", params={"cancel_orders": "true"})

    # --- market data ---
    def get_most_active_stocks(self, top):
        """Most-traded stocks today with price and IEX dollar volume: [(symbol, price, dollar_volume)]."""
        data_root = self.cfg.data_url.rsplit("/", 1)[0]
        actives = self._request("GET", f"{data_root}/v1beta1/screener/stocks/most-actives",
                                params={"by": "trades", "top": top})["most_actives"]
        symbols = [a["symbol"] for a in actives]
        snaps = self._request("GET", f"{self.cfg.data_url}/stocks/snapshots",
                              params={"symbols": ",".join(symbols), "feed": self.cfg.data_feed}) or {}
        out = []
        for sym in symbols:
            snap = snaps.get(sym) or {}
            bar = snap.get("dailyBar") or snap.get("prevDailyBar") or {}
            if bar:
                out.append((sym, bar["c"], bar["c"] * bar["v"]))
        return out

    def get_bars(self, symbol, timeframe, lookback_days=5):
        start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        params = {"symbols": symbol, "timeframe": timeframe, "start": start, "limit": 10000}
        if is_crypto(symbol):
            url = f"{self.cfg.data_url.rsplit('/', 1)[0]}/v1beta3/crypto/us/bars"
        else:
            url = f"{self.cfg.data_url}/stocks/bars"
            params.update(feed=self.cfg.data_feed, adjustment="raw")
        bars = []
        while True:
            data = self._request("GET", url, params=params)
            bars.extend((data.get("bars") or {}).get(symbol, []))
            token = data.get("next_page_token")
            if not token:
                return bars
            params["page_token"] = token
