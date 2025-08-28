#!/usr/bin/env python3
import argparse
import threading
import time
from dotenv import load_dotenv

from src.bot_kraken import SimpleThresholdBot


def start_web(trading_enabled: bool):
    import os
    from flask import Flask, jsonify, render_template_string

    os.environ["TRADING_ENABLED"] = "true" if trading_enabled else "false"

    bot = SimpleThresholdBot()

    # Start bot in background thread
    t = threading.Thread(target=bot.run, daemon=True)
    t.start()

    app = Flask(__name__)

    @app.get("/api/status")
    def api_status():
        return jsonify(bot.get_status_snapshot())

    INDEX_HTML = """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title>Crypto 5%</title>
        <style>
          body { font-family: -apple-system, system-ui, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 20px; }
          h1 { font-size: 20px; margin: 0 0 12px; }
          .grid { display: grid; grid-template-columns: 300px 1fr; gap: 6px 12px; max-width: 720px; }
          .key { color: #555; }
          .val { font-weight: 600; }
          .ok { color: #2e7d32; }
          .warn { color: #f9a825; }
          .err { color: #c62828; }
          .row { padding: 2px 0; border-bottom: 1px solid #eee; }
          .muted { color: #888; font-weight: 400; }
          .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
        </style>
      </head>
      <body>
        <h1>Crypto 5% Bot Status</h1>
        <div id="status" class="grid mono"></div>
        <script>
          const el = document.getElementById('status');
          function row(k, v) {
            return `<div class=\"key row\">${k}</div><div class=\"val row\">${v ?? '<span class=muted>n/a</span>'}</div>`;
          }
          function fmt4(x){ return (x?.toFixed) ? x.toFixed(4) : x }
          function fmt6(x){ return (x?.toFixed) ? x.toFixed(6) : x }
          function fmtP(x){ return (x==null) ? x : ( (x*100).toFixed(2) + '%') }
          async function tick(){
            try{
              const r = await fetch('/api/status');
              const s = await r.json();
              el.innerHTML = ''
                + row('timestamp', s.timestamp)
                + row('pair', s.pair)
                + row('trading_enabled', s.trading_enabled)
                + row('price', fmt4(s.price))
                + row('session_high', fmt4(s.session_high))
                + row('entry_price', fmt4(s.entry_price))
                + row('position_qty', fmt6(s.position_qty))
                + row('usd_available', s.usd_available?.toFixed ? '$' + s.usd_available.toFixed(2) : s.usd_available)
                + row('xmr_available', fmt6(s.xmr_available))
                + row('drop_threshold_price', fmt4(s.drop_threshold_price))
                + row('rise_threshold_price', fmt4(s.rise_threshold_price))
                + row('mode', s.mode)
                + row('last_sell_price', fmt4(s.last_sell_price))
                + row('24h_start', fmt4(s.price_24h_start))
                + row('24h_change', fmtP(s.price_24h_change_pct))
                + row('last_action', s.last_action);
            }catch(e){
              el.innerHTML = row('error', String(e));
            } finally {
              setTimeout(tick, 3000);
            }
          }
          tick();
        </script>
      </body>
    </html>
    """

    @app.get("/")
    def index():
        return render_template_string(INDEX_HTML)

    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)


def main():
    parser = argparse.ArgumentParser(description="Simple XMR/USD threshold bot on Kraken")
    parser.add_argument("mode", choices=["start", "dry-run", "web", "web-dry-run"], help="Run mode")
    args = parser.parse_args()

    load_dotenv()

    if args.mode in ("dry-run", "web-dry-run"):
        import os
        os.environ["TRADING_ENABLED"] = "false"

    if args.mode == "web" or args.mode == "web-dry-run":
        start_web(trading_enabled=(args.mode == "web"))
        return

    bot = SimpleThresholdBot()
    bot.run()


if __name__ == "__main__":
    main()


