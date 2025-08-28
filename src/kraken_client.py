import os
import time
import logging
from typing import Optional, Dict

from dotenv import load_dotenv
import krakenex


load_dotenv()


class KrakenClient:
    """Lightweight Kraken REST client helpers for spot XMR/USD trading."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.api_key = os.getenv("KRAKEN_API_KEY", "")
        self.api_secret = os.getenv("KRAKEN_API_SECRET", "")
        self.pair = os.getenv("PAIR", "XMRUSD")

        self.client = krakenex.API(key=self.api_key, secret=self.api_secret)

    def _query(self, method: str, data: Optional[Dict] = None, private: bool = False) -> Optional[Dict]:
        try:
            if private:
                response = self.client.query_private(method, data or {})
            else:
                response = self.client.query_public(method, data or {})

            if response is None:
                return None

            if response.get("error"):
                self.logger.error(f"Kraken API error on {method}: {response['error']}")
                return None

            return response.get("result")
        except Exception as exc:
            self.logger.error(f"Kraken API exception on {method}: {exc}")
            return None

    def get_ticker_price(self) -> Optional[float]:
        """Get the latest trade price for configured pair."""
        result = self._query("Ticker", {"pair": self.pair})
        if not result:
            return None
        # result is a dict keyed by pair altname; pick the first and use c[0] (last trade price)
        try:
            first_key = next(iter(result))
            last_trade = result[first_key]["c"][0]
            return float(last_trade)
        except Exception as exc:
            self.logger.error(f"Failed to parse ticker for {self.pair}: {exc}")
            return None

    def get_balances(self) -> Optional[Dict[str, float]]:
        """Return balances for base and quote assets.

        Kraken assets use codes like 'XXMR' and 'ZUSD'; we'll map common ones.
        """
        asset_map = {
            "XMR": ["XMR", "XXMR"],
            "USD": ["USD", "ZUSD"],
        }
        result = self._query("Balance", private=True)
        if result is None:
            return None
        balances = {k: float(v) for k, v in result.items()}

        base = "XMR"
        quote = "USD"
        base_bal = 0.0
        quote_bal = 0.0
        for key in asset_map[base]:
            base_bal += balances.get(key, 0.0)
        for key in asset_map[quote]:
            quote_bal += balances.get(key, 0.0)
        return {base: base_bal, quote: quote_bal}

    def place_market_buy_usd(self, usd_amount: float) -> Optional[Dict]:
        """Place a market buy order by USD notional. Converts to volume using current price."""
        price = self.get_ticker_price()
        if not price or price <= 0:
            self.logger.error("Cannot place buy: invalid current price")
            return None
        volume = usd_amount / price
        return self._add_order("buy", volume)

    def place_market_sell(self, base_quantity: float) -> Optional[Dict]:
        return self._add_order("sell", base_quantity)

    def _add_order(self, side: str, volume: float) -> Optional[Dict]:
        data = {
            "pair": self.pair,
            "type": side,
            "ordertype": "market",
            "volume": f"{volume:.8f}",
        }
        result = self._query("AddOrder", data, private=True)
        return result


