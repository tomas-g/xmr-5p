import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
import threading
import csv
import pathlib
import json
from collections import deque

from dotenv import load_dotenv

from src.kraken_client import KrakenClient


load_dotenv()


# PDT timezone (UTC-7, Pacific Daylight Time)
PDT = timezone(timedelta(hours=-7))

def format_pdt_timestamp() -> str:
    """Format current time as PDT in YYYY-MM-DD HH:MM:SS format."""
    return datetime.now(PDT).strftime("%Y-%m-%d %H:%M:%S")


class SimpleThresholdBot:
    """Buy after 5% drop from session high; sell after 5% rise from entry."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        logging.basicConfig(
            level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()),
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('trading_bot.log') if os.getenv('LOG_TO_FILE', 'false').lower() == 'true' else logging.StreamHandler(),
            ]
        )

        self.client = KrakenClient()
        self.pair = os.getenv("PAIR", "XMRUSD")

        # Strategy parameters
        self.drop_pct = float(os.getenv("PRICE_DROP_THRESHOLD", "0.05"))
        self.rise_pct = float(os.getenv("PRICE_RISE_THRESHOLD", "0.05"))
        self.min_trade_usd = float(os.getenv("MIN_TRADE_AMOUNT", "10.0"))
        self.max_position_usd = float(os.getenv("MAX_POSITION_SIZE", "100.0"))
        self.trading_enabled = os.getenv("TRADING_ENABLED", "true").lower() == "true"
        self.loop_sleep_seconds = int(os.getenv("LOOP_SLEEP_SECONDS", "30"))

        # Trade ledger
        self.trades_csv_path = os.getenv("TRADES_CSV", "trades.csv")
        
        # State persistence
        self.state_file_path = os.getenv("BOT_STATE_FILE", ".bot_state.json")

        # Session tracking - will be loaded from state file if it exists
        self.session_high: Optional[float] = None
        self.entry_price: Optional[float] = None
        self.position_qty: float = 0.0
        self.last_sell_price: Optional[float] = None

        # Status logging tracking
        self._last_status_log_hour = None

        # Load persistent state
        self._load_state()

        # Rolling 24h price history: deque of (timestamp, price)
        self._price_history = deque()

        # Status snapshot
        self._status_lock = threading.Lock()
        self._last_status = {
            "timestamp": None,
            "pair": self.pair,
            "price": None,
            "session_high": None,
            "entry_price": None,
            "position_qty": 0.0,
            "usd_available": None,
            "xmr_available": None,
            "drop_threshold_price": None,
            "rise_threshold_price": None,
            "price_24h_start": None,
            "price_24h_change_pct": None,
            "mode": None,
            "last_sell_price": None,
            "last_action": None,
            "trading_enabled": self.trading_enabled,
        }

    def _load_state(self):
        """Load bot state from JSON file if it exists."""
        try:
            state_path = pathlib.Path(self.state_file_path)
            if state_path.exists():
                with state_path.open("r") as f:
                    state = json.load(f)
                self.position_qty = float(state.get("position_qty", 0.0))
                self.entry_price = state.get("entry_price")
                self.last_sell_price = state.get("last_sell_price")
                self.session_high = state.get("session_high")
                self._loaded_from_state = True
                self.logger.info(f"Loaded state: pos_qty={self.position_qty:.6f}, entry={self.entry_price}, last_sell={self.last_sell_price}, session_high={self.session_high}")
            else:
                self._loaded_from_state = False
                self.logger.info("No existing state file found, starting fresh")
        except Exception as exc:
            self.logger.error(f"Failed to load state file: {exc}. Starting fresh.")

    def _save_state(self):
        """Save bot state to JSON file."""
        try:
            state = {
                "position_qty": self.position_qty,
                "entry_price": self.entry_price,
                "last_sell_price": self.last_sell_price,
                "session_high": self.session_high,
                "timestamp": format_pdt_timestamp()
            }
            state_path = pathlib.Path(self.state_file_path)
            with state_path.open("w") as f:
                json.dump(state, f, indent=2)
            self.logger.debug(f"Saved state: pos_qty={self.position_qty:.6f}")
        except Exception as exc:
            self.logger.error(f"Failed to save state file: {exc}")

    def _update_status(self, **kwargs):
        with self._status_lock:
            self._last_status.update(kwargs)

    def get_status_snapshot(self) -> dict:
        with self._status_lock:
            return dict(self._last_status)

    def _log_status_snapshot(self, reason: str = "periodic"):
        """Log the current status snapshot with a reason."""
        status = self.get_status_snapshot()
        # Remove some fields for cleaner logging
        clean_status = {k: v for k, v in status.items() if k not in ["timestamp"]}
        self.logger.info(f"STATUS-{reason.upper()}: {json.dumps(clean_status, separators=(',', ':'))}")

    def _should_log_hourly_status(self) -> bool:
        """Check if we should log hourly status."""
        now = datetime.now(PDT)
        current_hour = now.hour
        if self._last_status_log_hour != current_hour:
            self._last_status_log_hour = current_hour
            return True
        return False

    def _append_trade(self, *, side: str, qty_xmr: float, price_usd: float, pnl_usd: Optional[float], txid: Optional[str]):
        try:
            path = pathlib.Path(self.trades_csv_path)
            new_file = not path.exists()
            with path.open("a", newline="") as f:
                writer = csv.writer(f)
                if new_file:
                    writer.writerow(["timestamp", "pair", "side", "qty_xmr", "price_usd", "notional_usd", "pnl_usd", "txid"]) 
                timestamp = format_pdt_timestamp()
                notional = qty_xmr * price_usd
                writer.writerow([
                    timestamp,
                    self.pair,
                    side,
                    f"{qty_xmr:.8f}",
                    f"{price_usd:.6f}",
                    f"{notional:.2f}",
                    ("" if pnl_usd is None else f"{pnl_usd:.2f}"),
                    (txid or ""),
                ])
        except Exception as exc:
            self.logger.error(f"Failed writing trade to CSV: {exc}")

    def _extract_txid(self, order_result: Optional[dict]) -> Optional[str]:
        try:
            if not order_result:
                return None
            txids = order_result.get("txid")
            if isinstance(txids, list):
                return ",".join(txids)
            if isinstance(txids, str):
                return txids
            return None
        except Exception:
            return None

    def _update_price_history(self, now_ts: float, price: float):
        # Append current
        self._price_history.append((now_ts, price))
        # Drop entries older than 24 hours
        cutoff = now_ts - 24 * 60 * 60
        while self._price_history and self._price_history[0][0] < cutoff:
            self._price_history.popleft()

    def _compute_24h_stats(self, current_price: float):
        if not self._price_history:
            return None, None
        start_price = self._price_history[0][1]
        if start_price and start_price > 0:
            change_pct = (current_price - start_price) / start_price
        else:
            change_pct = None
        return start_price, change_pct

    def tick(self):
        price = self.client.get_ticker_price()
        if price is None:
            self.logger.warning("No price, skipping tick")
            return

        # Update 24h price history
        now = datetime.utcnow().timestamp()
        self._update_price_history(now, price)
        start_24h, change_24h = self._compute_24h_stats(price)

        # Fetch balances once per tick
        balances = self.client.get_balances() or {}
        usd_available = float(balances.get("USD", 0.0))
        xmr_available = float(balances.get("XMR", 0.0))

        # Compute reference thresholds for visibility
        # Use last_sell_price for buy threshold if available, otherwise fall back to session_high
        buy_baseline = self.last_sell_price if self.last_sell_price is not None else self.session_high
        drop_ref = None if buy_baseline is None else buy_baseline * (1.0 - self.drop_pct)
        rise_ref = None if self.entry_price is None else self.entry_price * (1.0 + self.rise_pct)

        # Determine current mode
        mode = "SELL MODE" if self.position_qty > 0 else "BUY MODE"

        # Remove verbose tick logging - only log on hour or trades

        # Determine last action for display
        if hasattr(self, '_loaded_from_state') and self._loaded_from_state and self.position_qty > 0:
            last_action_display = f"LOADED: BUY {self.position_qty:.6f} XMR @ {self.entry_price:.2f}"
        else:
            last_action_display = None

        # Update status before actions
        self._update_status(
            timestamp=format_pdt_timestamp(),
            price=price,
            session_high=self.session_high,
            entry_price=self.entry_price,
            position_qty=self.position_qty,
            usd_available=usd_available,
            xmr_available=xmr_available,
            drop_threshold_price=drop_ref,
            rise_threshold_price=rise_ref,
            price_24h_start=start_24h,
            price_24h_change_pct=change_24h,
            mode=mode,
            last_sell_price=self.last_sell_price,
            last_action=last_action_display,
        )

        # Log status snapshot on the hour
        if self._should_log_hourly_status():
            self._log_status_snapshot("hourly")

        # Track session high while flat (for display purposes)
        if self.position_qty <= 0:
            if self.session_high is None or price > self.session_high:
                self.session_high = price

            # Check buy condition using last_sell_price baseline if available
            buy_baseline = self.last_sell_price if self.last_sell_price is not None else self.session_high
            drop_condition = buy_baseline and price <= buy_baseline * (1.0 - self.drop_pct)
            self.logger.debug(
                f"Flat: checking buy condition -> {bool(drop_condition)} (price={price:.4f}, baseline={buy_baseline:.4f})"
            )
            if drop_condition:
                # Use available USD, capped by max position; ensure >= min trade
                usd_cap = min(self.max_position_usd, usd_available)
                if usd_cap < self.min_trade_usd:
                    self.logger.info(
                        f"Buy signal but insufficient USD: have ${usd_available:.2f}, need >= ${self.min_trade_usd:.2f}"
                    )
                    self._update_status(last_action="buy_skipped_insufficient_usd")
                    return
                usd_to_buy = usd_cap
                baseline_type = "last_sell" if self.last_sell_price is not None else "session_high"
                self.logger.info(
                    f"Buy signal: price {price:.4f} dropped >= {self.drop_pct:.1%} from {baseline_type} {buy_baseline:.4f}; sizing ${usd_to_buy:.2f}"
                )
                if self.trading_enabled:
                    order = self.client.place_market_buy_usd(usd_to_buy)
                    if order:
                        self.entry_price = price
                        self.position_qty = usd_to_buy / price
                        self._save_state()  # Save state after buy
                        self._loaded_from_state = False  # Clear loaded flag after real trade
                        self._update_status(entry_price=self.entry_price, position_qty=self.position_qty, last_action="buy")
                        txid = self._extract_txid(order)
                        self._append_trade(side="buy", qty_xmr=self.position_qty, price_usd=price, pnl_usd=None, txid=txid)
                        self.logger.info(f"Bought ~{self.position_qty:.6f} XMR at ~{price:.4f}")
                        self._log_status_snapshot("trade_buy")  # Log status after buy
                else:
                    self._update_status(last_action="buy_simulated")
                    self.logger.info("Trading disabled (dry-run): would BUY")

        else:
            # Have position: sell after 5% rise from entry
            assert self.entry_price is not None
            rise_condition = price >= self.entry_price * (1.0 + self.rise_pct)
            self.logger.debug(
                f"Long: checking rise condition -> {rise_condition} (price={price:.4f}, entry={self.entry_price:.4f})"
            )
            if rise_condition:
                # Limit sell size to available XMR and tracked position
                qty_to_sell = min(self.position_qty, xmr_available)
                if qty_to_sell <= 0:
                    self.logger.info(
                        f"Sell signal but no XMR available to sell (tracked {self.position_qty:.6f}, wallet {xmr_available:.6f})"
                    )
                    self._update_status(last_action="sell_skipped_no_xmr")
                    return
                self.logger.info(
                    f"Sell signal: price {price:.4f} rose >= {self.rise_pct:.1%} from entry {self.entry_price:.4f}; sizing {qty_to_sell:.6f} XMR"
                )
                if self.trading_enabled:
                    order = self.client.place_market_sell(qty_to_sell)
                    if order:
                        pnl = (price - self.entry_price) * qty_to_sell
                        self.logger.info(f"Sold {qty_to_sell:.6f} XMR at ~{price:.4f}, PnL ${pnl:.2f}")
                        txid = self._extract_txid(order)
                        self._append_trade(side="sell", qty_xmr=qty_to_sell, price_usd=price, pnl_usd=pnl, txid=txid)
                        self.position_qty -= qty_to_sell
                        if self.position_qty <= 1e-8:
                            self.position_qty = 0.0
                            self.entry_price = None
                            # Set last_sell_price for next buy baseline
                            self.last_sell_price = price
                            self.session_high = price  # reset session reference
                        self._save_state()  # Save state after sell
                        self._loaded_from_state = False  # Clear loaded flag after real trade
                        self._update_status(position_qty=self.position_qty, entry_price=self.entry_price, session_high=self.session_high, last_sell_price=self.last_sell_price, last_action="sell")
                        self._log_status_snapshot("trade_sell")  # Log status after sell
                else:
                    self._update_status(last_action="sell_simulated")
                    self.logger.info("Trading disabled (dry-run): would SELL")

    def run(self):
        try:
            self.logger.info(f"Starting SimpleThresholdBot on {self.pair} | drop={self.drop_pct:.1%}, rise={self.rise_pct:.1%} | trading={self.trading_enabled}")
            
            # Test API connection
            test_price = self.client.get_ticker_price()
            if test_price:
                self.logger.info(f"API connection successful. Current {self.pair} price: ${test_price:.2f}")
            else:
                self.logger.error("API connection failed - no price data received")
                return
                
            self.logger.info("Bot started successfully - entering main loop")
        except Exception as exc:
            self.logger.error(f"Startup failed: {exc}")
            return
            
        while True:
            try:
                self.tick()
                time.sleep(self.loop_sleep_seconds)
            except KeyboardInterrupt:
                self.logger.info("Stopped by user")
                break
            except Exception as exc:
                self.logger.error(f"Loop error: {exc}")
                time.sleep(self.loop_sleep_seconds)


def main():
    bot = SimpleThresholdBot()
    bot.run()


if __name__ == "__main__":
    main()


