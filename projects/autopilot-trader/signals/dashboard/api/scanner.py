"""Scanner endpoints — read signals.json for opportunities and stats."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query

log = logging.getLogger("dashboard.api.scanner")

PROJECT_ROOT = Path("/root/.openclaw/workspace/projects/autopilot-trader")
SIGNALS_PATH = PROJECT_ROOT / "signals" / "signals.json"

router = APIRouter()


def _read_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@router.get("/api/scanner/opportunities")
async def get_opportunities(n: int = Query(default=20, ge=1, le=100)):
    data = _read_json(SIGNALS_PATH)
    if not data:
        return []

    opps = data.get("opportunities", [])
    opps.sort(key=lambda o: o.get("compositeScore", 0), reverse=True)
    return opps[:n]


@router.get("/api/scanner/funding")
async def get_funding():
    """Funding spread data sorted by absolute spread (highest arbitrage first)."""
    data = _read_json(SIGNALS_PATH)
    if not data:
        return []

    opps = data.get("opportunities", [])
    funding_data = []
    for opp in opps:
        spread = opp.get("fundingSpread8h", 0) or 0
        funding_data.append({
            "symbol": opp["symbol"],
            "direction": opp.get("direction"),
            "lighterFundingRate8h": opp.get("lighterFundingRate8h", 0),
            "cexAvgFundingRate8h": opp.get("cexAvgFundingRate8h", 0),
            "fundingSpread8h": round(spread, 6),
        })

    funding_data.sort(key=lambda x: abs(x["fundingSpread8h"]), reverse=True)
    return funding_data


@router.get("/api/scanner/distribution")
async def get_distribution():
    """Score histogram buckets (0-10, 10-20, ..., 90-100) + component averages."""
    data = _read_json(SIGNALS_PATH)
    if not data:
        return {"buckets": {}, "component_averages": {}}

    opps = data.get("opportunities", [])

    buckets = {f"{i}-{i+10}": 0 for i in range(0, 100, 10)}
    component_sums = {
        "fundingSpreadScore": 0,
        "volumeAnomalyScore": 0,
        "momentumScore": 0,
        "maAlignmentScore": 0,
        "orderBlockScore": 0,
    }
    count = 0

    for opp in opps:
        score = opp.get("compositeScore", 0)
        bucket_idx = min(int(score // 10) * 10, 90)
        buckets[f"{bucket_idx}-{bucket_idx + 10}"] = buckets.get(f"{bucket_idx}-{bucket_idx + 10}", 0) + 1

        for key in component_sums:
            component_sums[key] += opp.get(key, 0)
        count += 1

    component_averages = {
        k: round(v / count, 2) if count > 0 else 0
        for k, v in component_sums.items()
    }

    return {
        "buckets": buckets,
        "component_averages": component_averages,
        "total": count,
    }


@router.get("/api/scanner/stats")
async def get_scanner_stats():
    """Total count, timestamp, age, config."""
    data = _read_json(SIGNALS_PATH)
    if not data:
        return {"total": 0, "timestamp": None, "age_seconds": None, "config": {}}

    opps = data.get("opportunities", [])
    timestamp = data.get("timestamp")
    age_seconds = None

    if timestamp:
        try:
            ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            age_seconds = int((datetime.now(timezone.utc) - ts).total_seconds())
        except Exception:
            pass

    return {
        "total": len(opps),
        "timestamp": timestamp,
        "age_seconds": age_seconds,
        "config": data.get("config", {}),
    }
