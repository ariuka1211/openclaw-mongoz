"""Lighter Trading Bot Dashboard — FastAPI backend."""

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="Lighter Bot Dashboard")

SIGNALS_PATH = Path("/root/.openclaw/workspace/lighter-bot/signals.json")
BOT_LOG_PATH = Path("/root/.openclaw/workspace/lighter-copilot/bot.log")

# Compiled regexes
TRACKING_RE = re.compile(
    r"📌 Tracking: (LONG|SHORT) (\S+) @ \$([0-9.,]+), size=([0-9.]+), mode=(\S+) \(lev=([0-9.]+)x\)"
)
OPENED_RE = re.compile(r"✅ Position opened: (LONG|SHORT) \$([0-9.]+)")
CLOSED_RE = re.compile(r"Position closed: (\S+)")
ROE_RE = re.compile(r"ROE:\s*([+-][0-9.]+)%")
SIGNAL_RE = re.compile(r"📡 Signal: (LONG|SHORT) (\S+) score=([0-9]+) size=\$([0-9.]+)")
BALANCE_RE = re.compile(r"Balance: \$([0-9.]+) USDC")
SCALING_RE = re.compile(r"Scaling positions: balance=\$([0-9.]+) / scanner_equity=\$([0-9.]+) = ([0-9.]+)×")
ERROR_RE = re.compile(r"\[ERROR\]")
START_RE = re.compile(r"🚀 Lighter Copilot starting")
DSL_RE = re.compile(r"DSL (TRAILING|STAGNATION|BREACH)")


def read_signals():
    try:
        with open(SIGNALS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"timestamp": None, "opportunities": [], "config": {}}


def parse_bot_log(max_lines=5000):
    if not BOT_LOG_PATH.exists():
        return {"positions": [], "activity": [], "errors": [], "balance": 13.03,
                "bot_running": False, "last_signal_time": None, "scaling_info": None}

    try:
        result = subprocess.run(
            ["tail", "-n", str(max_lines), str(BOT_LOG_PATH)],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
    except Exception:
        return {"positions": [], "activity": [], "errors": [], "balance": 13.03,
                "bot_running": False, "last_signal_time": None, "scaling_info": None}

    # Find last "Scaling" line — positions after this are current
    last_scaling_idx = -1
    for i, line in enumerate(lines):
        if "Scaling positions" in line:
            last_scaling_idx = i

    # Parse current cycle positions
    positions = {}
    if last_scaling_idx >= 0:
        for line in lines[last_scaling_idx:]:
            m = TRACKING_RE.search(line)
            if m:
                direction, symbol, price, size, mode, lev = m.groups()
                positions[symbol] = {
                    "symbol": symbol,
                    "direction": direction.lower(),
                    "entry_price": float(price.replace(",", "")),
                    "size": float(size),
                    "mode": mode,
                    "leverage": float(lev),
                }
            m = CLOSED_RE.search(line)
            if m:
                positions.pop(m.group(1), None)

    # Parse full log for activity/errors/balance
    activity = []
    errors = []
    balance = None
    scaling_info = None
    last_signal_time = None
    last_roe = None

    for line in lines:
        ts_match = re.match(r"(\d{2}:\d{2}:\d{2})", line)
        ts_str = ts_match.group(1) if ts_match else ""

        m = BALANCE_RE.search(line)
        if m:
            balance = float(m.group(1))

        m = SCALING_RE.search(line)
        if m:
            scaling_info = float(m.group(3))

        m = ROE_RE.search(line)
        if m:
            last_roe = float(m.group(1))

        m = SIGNAL_RE.search(line)
        if m:
            direction, symbol, score, size = m.groups()
            last_signal_time = ts_str
            activity.append({"time": ts_str, "type": "signal",
                             "text": f"{direction} {symbol} score={score} size=${size}"})

        m = OPENED_RE.search(line)
        if m:
            direction, size = m.groups()
            activity.append({"time": ts_str, "type": "opened",
                             "text": f"Opened {direction} ${size}"})

        m = CLOSED_RE.search(line)
        if m:
            symbol = m.group(1)
            roe_text = f" (ROE: {last_roe:+.1f}%)" if last_roe is not None else ""
            activity.append({"time": ts_str, "type": "closed",
                             "text": f"Closed {symbol}{roe_text}"})
            last_roe = None

        if DSL_RE.search(line):
            activity.append({"time": ts_str, "type": "dsl", "text": line.strip()[-80:]})

        if "Max concurrent" in line:
            activity.append({"time": ts_str, "type": "warning", "text": line.strip()[-80:]})

        if ERROR_RE.search(line):
            err_msg = line.strip()
            if "HTTP response headers" not in err_msg and "HTTP response body" not in err_msg:
                errors.append({"time": ts_str, "text": err_msg[:200]})

    # Check if bot process is running
    bot_running = False
    try:
        r = subprocess.run(["pgrep", "-f", "bot.py"], capture_output=True, text=True, timeout=3)
        bot_running = r.returncode == 0
    except Exception:
        pass

    return {
        "positions": list(positions.values()),
        "activity": activity[-30:],
        "errors": errors[-20:],
        "balance": balance,
        "scaling_info": scaling_info,
        "bot_running": bot_running,
        "last_signal_time": last_signal_time,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/dashboard")
async def api_dashboard():
    signals = read_signals()
    bot = parse_bot_log()

    top_signals = signals.get("opportunities", [])[:10]

    signal_age = None
    if signals.get("timestamp"):
        try:
            ts = datetime.fromisoformat(signals["timestamp"].replace("Z", "+00:00"))
            age_secs = (datetime.now(timezone.utc) - ts).total_seconds()
            signal_age = f"{int(age_secs // 60)}m ago"
        except Exception:
            signal_age = signals["timestamp"]

    return JSONResponse({
        "account": {
            "balance": bot["balance"] or 13.03,
            "equity": bot["balance"] or 13.03,
            "scaling_factor": bot.get("scaling_info"),
        },
        "positions": bot["positions"],
        "signals": [
            {
                "symbol": s["symbol"],
                "score": s["compositeScore"],
                "direction": s["direction"],
                "position_size_usd": round(s["positionSizeUsd"], 2),
                "leverage": round(s["actualLeverage"], 2),
                "last_price": s["lastPrice"],
                "funding_spread": round(s["fundingSpread8h"], 4),
                "daily_volume": round(s["dailyVolumeUsd"], 0),
                "safety_pass": s["safetyPass"],
            }
            for s in top_signals
        ],
        "activity": bot["activity"],
        "errors": bot["errors"],
        "system": {
            "bot_running": bot["bot_running"],
            "signal_age": signal_age,
            "total_opportunities": len(signals.get("opportunities", [])),
            "config": signals.get("config", {}),
        },
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
