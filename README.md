# crypto-5p

A minimal Kraken-driven crypto bot that buys after a 5% drop from session high and sells after a 5% rise from entry. Default pair is `XMRUSD`.

## Quickstart

```bash
# 1) Create and activate a local venv (recommended)
python3 -m venv 5p-venv
source 5p-venv/bin/activate

# 2) Install dependencies
pip install -r requirements.txt

# 3) Create .env from example and fill your keys
cp config.env.example .env
# edit .env and set KRAKEN_API_KEY, KRAKEN_API_SECRET, PAIR=XMRUSD (defaults exist)
```

Run dry-run (no orders):
```bash
python3 main.py dry-run
```

Run live (places orders):
```bash
python3 main.py start
```

Minimal web UI (status page on http://localhost:8000):
```bash
# no orders
python3 main.py web-dry-run
# live trading
python3 main.py web
```

Set `PORT` in `.env` to change the web UI port (default 8000).

## Configuration (.env)
- `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`: Kraken API credentials
- `PAIR`: trading pair, e.g. `XMRUSD`
- `PRICE_DROP_THRESHOLD`: buy if price falls this fraction from session high (e.g. `0.05`)
- `PRICE_RISE_THRESHOLD`: sell if price rises this fraction from entry (e.g. `0.05`)
- `MAX_POSITION_SIZE`: max USD to deploy per position
- `MIN_TRADE_AMOUNT`: minimum USD per buy
- `TRADING_ENABLED`: `true` or `false`
- `LOOP_SLEEP_SECONDS`: loop delay seconds
- `LOG_LEVEL`: `DEBUG`, `INFO` (default), `WARNING`, `ERROR`, `CRITICAL`
- `LOG_TO_FILE`: `true` to write `trading_bot.log`, otherwise logs to console
- `PORT`: web UI port (default `8000`)
- `TRADES_CSV`: path to CSV ledger (default `trades.csv`)

## Trade logging
- Successful buys/sells are appended to a CSV ledger (`TRADES_CSV`, default `trades.csv`).
- Columns: `timestamp,pair,side,qty_xmr,price_usd,notional_usd,pnl_usd,txid`.
- `pnl_usd` is only filled on sells; buys leave it blank.

## Structure
- `src/kraken_client.py`: minimal Kraken REST helpers
- `src/bot_kraken.py`: 5% drop/5% rise strategy loop and status snapshot
- `main.py`: entrypoint (CLI modes: start, dry-run, web, web-dry-run)
- `config.env.example`: example environment

## Notes
- Educational only; trading is risky.
- Uses market orders; slippage possible.

## Troubleshooting
- If you see `ModuleNotFoundError: No module named 'dotenv'`, ensure you installed requirements in the active venv.
- On macOS with Homebrew Python, avoid PEP 668 errors by using a project venv (`python3 -m venv 5p-venv`).
- If logs seem sparse, set `LOG_LEVEL=DEBUG` and lower `LOOP_SLEEP_SECONDS` in `.env`.
