"""
FastAPI Web Dashboard.

Real-time monitoring dashboard for the trading bot.
Shows positions, P&L, signals, and system health.

Run with: uvicorn src.dashboard.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger


app = FastAPI(title="Trader AI Dashboard", version="1.0.0")


# In-memory state (updated by the main engine)
_dashboard_state = {
    "mode": "paper",
    "status": "idle",
    "capital": 0,
    "daily_pnl": 0,
    "positions": [],
    "recent_trades": [],
    "signals": [],
    "regime": "unknown",
    "watchlist": [],
    "last_update": None,
    "kill_switch": False,
}


def update_dashboard(data: dict):
    """Update dashboard state from the trading engine."""
    _dashboard_state.update(data)
    _dashboard_state["last_update"] = datetime.utcnow().isoformat()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    state = _dashboard_state
    pnl = state["daily_pnl"]
    pnl_color = "#10b981" if pnl >= 0 else "#ef4444"
    mode_badge = "📄 PAPER" if state["mode"] == "paper" else "🔴 LIVE"
    kill_status = "🛑 ACTIVE" if state["kill_switch"] else "✅ OK"

    positions_html = ""
    for pos in state.get("positions", []):
        pos_pnl_color = "#10b981" if pos.get("pnl", 0) >= 0 else "#ef4444"
        positions_html += f"""
        <tr>
            <td>{pos.get('symbol', '')}</td>
            <td>{pos.get('side', '')}</td>
            <td>{pos.get('quantity', 0)}</td>
            <td>${pos.get('entry_price', 0):.2f}</td>
            <td>${pos.get('current_price', 0):.2f}</td>
            <td style="color: {pos_pnl_color}">${pos.get('pnl', 0):+.2f}</td>
            <td>{pos.get('strategy', '')}</td>
        </tr>
        """

    trades_html = ""
    for trade in state.get("recent_trades", [])[-10:]:
        t_pnl_color = "#10b981" if trade.get("pnl", 0) >= 0 else "#ef4444"
        trades_html += f"""
        <tr>
            <td>{trade.get('time', '')}</td>
            <td>{trade.get('symbol', '')}</td>
            <td>{trade.get('side', '')}</td>
            <td>${trade.get('entry', 0):.2f}</td>
            <td>${trade.get('exit', 0):.2f}</td>
            <td style="color: {t_pnl_color}">${trade.get('pnl', 0):+.2f}</td>
            <td>{trade.get('strategy', '')}</td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trader AI — Dashboard</title>
        <meta http-equiv="refresh" content="30">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: 'Inter', -apple-system, sans-serif;
                background: #0f0f23;
                color: #e0e0e0;
                padding: 20px;
            }}
            h1 {{ color: #00d4ff; margin-bottom: 20px; font-size: 1.8em; }}
            h2 {{ color: #8b8bcd; margin: 20px 0 10px; font-size: 1.2em; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }}
            .card {{
                background: #1a1a3e;
                border: 1px solid #2a2a5e;
                border-radius: 12px;
                padding: 20px;
                text-align: center;
            }}
            .card .label {{ font-size: 0.8em; color: #8b8bcd; text-transform: uppercase; }}
            .card .value {{ font-size: 1.8em; font-weight: bold; margin-top: 5px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th, td {{ padding: 10px 15px; text-align: left; border-bottom: 1px solid #2a2a5e; }}
            th {{ color: #00d4ff; font-size: 0.85em; text-transform: uppercase; }}
            tr:hover {{ background: #1a1a3e; }}
            .status-bar {{
                display: flex; justify-content: space-between; align-items: center;
                background: #1a1a3e; border-radius: 8px; padding: 10px 20px; margin-bottom: 20px;
            }}
            .badge {{
                background: #2a2a5e; padding: 4px 12px; border-radius: 20px; font-size: 0.85em;
            }}
        </style>
    </head>
    <body>
        <h1>🤖 Trader AI Dashboard</h1>

        <div class="status-bar">
            <span class="badge">{mode_badge}</span>
            <span>Status: {state['status']}</span>
            <span>Regime: {state['regime']}</span>
            <span>Kill Switch: {kill_status}</span>
            <span>Updated: {state.get('last_update', 'never')}</span>
        </div>

        <div class="grid">
            <div class="card">
                <div class="label">Capital</div>
                <div class="value">${state['capital']:,.2f}</div>
            </div>
            <div class="card">
                <div class="label">Daily P&L</div>
                <div class="value" style="color: {pnl_color}">${pnl:+.2f}</div>
            </div>
            <div class="card">
                <div class="label">Open Positions</div>
                <div class="value">{len(state.get('positions', []))}</div>
            </div>
            <div class="card">
                <div class="label">Trades Today</div>
                <div class="value">{len(state.get('recent_trades', []))}</div>
            </div>
        </div>

        <h2>📊 Open Positions</h2>
        <table>
            <tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Current</th><th>P&L</th><th>Strategy</th></tr>
            {positions_html or '<tr><td colspan="7" style="text-align:center; color:#555">No open positions</td></tr>'}
        </table>

        <h2>📈 Recent Trades</h2>
        <table>
            <tr><th>Time</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Strategy</th></tr>
            {trades_html or '<tr><td colspan="7" style="text-align:center; color:#555">No trades today</td></tr>'}
        </table>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/api/status")
async def api_status():
    """API endpoint for bot status."""
    return _dashboard_state


@app.get("/api/health")
async def api_health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
