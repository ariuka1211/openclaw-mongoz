"""
Signal Weight Analyzer — correlates scanner signals with trade outcomes.

Reads the decisions and outcomes tables to find which signal components
(funding, OI trend, momentum, MA, orderbook) correlate with winning trades.
Outputs suggested weight adjustments as JSON for human review.

Does NOT auto-apply weights — just suggests.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ai-trader.signal_analyzer")

# Signal score fields in the signals snapshot
SIGNAL_KEYS = [
    "fundingSpreadScore",
    "oiTrendScore",
    "momentumScore",
    "maAlignmentScore",
    "orderBlockScore",
]

# Default weights (what the scanner uses as baseline)
DEFAULT_WEIGHTS = {
    "fundingSpreadScore": 0.35,
    "oiTrendScore": 0.10,
    "momentumScore": 0.15,
    "maAlignmentScore": 0.25,
    "orderBlockScore": 0.15,
}


def analyze_signals(db_path: str, output_path: str = "state/signal_weights_suggested.json"):
    """Analyze which signals correlate with winning trades."""
    sys.path.insert(0, str(Path(__file__).parent))
    from db import DecisionDB

    db = DecisionDB(db_path)

    # Get all executed decisions with their signal snapshots
    rows = db.conn.execute("""
        SELECT d.id, d.symbol, d.timestamp, d.signals_snapshot
        FROM decisions d
        WHERE d.executed = 1 AND d.action = 'open'
        ORDER BY d.timestamp DESC
        LIMIT 100
    """).fetchall()

    if not rows:
        log.info("No executed open decisions found — nothing to analyze")
        db.close()
        return

    # Match each decision to its outcome
    signal_wins = {k: [] for k in SIGNAL_KEYS}
    signal_losses = {k: [] for k in SIGNAL_KEYS}

    analyzed = 0
    for row in rows:
        dec_id, symbol, dec_ts, signals_json = row

        # Find matching outcome (same symbol, within 24h after decision)
        outcome = db.conn.execute("""
            SELECT pnl_usd FROM outcomes
            WHERE symbol = ?
            AND timestamp > ?
            AND timestamp < datetime(?, '+24 hours')
            ORDER BY timestamp ASC
            LIMIT 1
        """, (symbol, dec_ts, dec_ts)).fetchone()

        if outcome is None:
            continue  # position still open or outcome not logged yet

        pnl_usd = outcome[0]
        is_win = pnl_usd > 0

        # Parse signal snapshot to find this symbol's scores
        try:
            signals = json.loads(signals_json) if signals_json else []
        except (json.JSONDecodeError, TypeError):
            continue

        # Find the signal entry for this symbol
        sig_entry = None
        for s in signals:
            if s.get("symbol") == symbol:
                sig_entry = s
                break

        if not sig_entry:
            continue

        analyzed += 1
        for key in SIGNAL_KEYS:
            score = sig_entry.get(key)
            if score is not None:
                if is_win:
                    signal_wins[key].append(float(score))
                else:
                    signal_losses[key].append(float(score))

    db.close()

    if analyzed < 5:
        log.info(f"Only {analyzed} matched trades — need at least 5 for meaningful analysis")
        # Still output a report saying not enough data
        report = {
            "generated": datetime.now(timezone.utc).isoformat(),
            "trades_analyzed": analyzed,
            "status": "insufficient_data",
            "message": f"Only {analyzed} trades with outcomes found. Need ≥5 for meaningful analysis.",
        }
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)
        log.info(f"Report written to {output_path}")
        return

    # Compute average scores for wins and losses per signal
    results = {}
    for key in SIGNAL_KEYS:
        avg_win = sum(signal_wins[key]) / len(signal_wins[key]) if signal_wins[key] else 0
        avg_loss = sum(signal_losses[key]) / len(signal_losses[key]) if signal_losses[key] else 0
        win_count = len(signal_wins[key])
        loss_count = len(signal_losses[key])

        # Signal effectiveness: how much higher are win scores vs loss scores?
        delta = avg_win - avg_loss
        results[key] = {
            "avg_score_wins": round(avg_win, 1),
            "avg_score_losses": round(avg_loss, 1),
            "delta": round(delta, 1),
            "win_count": win_count,
            "loss_count": loss_count,
        }

    # Compute suggested weights based on signal effectiveness
    # Apply power curve (1.5) to amplify meaningful deltas over tiny ones
    # This prevents signals with near-zero delta from getting ~20% weight after normalization
    deltas = {k: max(0.01, abs(r["delta"]) ** 1.5) for k, r in results.items()}
    total_delta = sum(deltas.values())
    suggested_weights = {k: round(deltas[k] / total_delta, 3) for k in SIGNAL_KEYS}

    # Blend with defaults (70% data-driven, 30% default) to avoid overfitting on small samples
    blended_weights = {}
    for k in SIGNAL_KEYS:
        blended = 0.7 * suggested_weights[k] + 0.3 * DEFAULT_WEIGHTS[k]
        blended_weights[k] = round(blended, 3)

    # Normalize blended weights to sum to 1
    total_blended = sum(blended_weights.values())
    blended_weights = {k: round(v / total_blended, 3) for k, v in blended_weights.items()}

    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "trades_analyzed": analyzed,
        "signal_analysis": results,
        "current_weights": DEFAULT_WEIGHTS,
        "suggested_weights_raw": suggested_weights,
        "suggested_weights_blended": blended_weights,
        "notes": [
            "Blended = 70% data-driven + 30% default (prevents overfitting on small samples)",
            "Review before applying — correlation ≠ causation",
            "Re-run after more trades for better signal",
        ],
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    log.info(f"Signal analysis complete: {analyzed} trades analyzed")
    log.info(f"Results written to {output_path}")
    for key in SIGNAL_KEYS:
        r = results[key]
        log.info(
            f"  {key}: win_avg={r['avg_score_wins']:.0f} "
            f"loss_avg={r['avg_score_losses']:.0f} "
            f"delta={r['delta']:+.0f} "
            f"→ suggested={blended_weights[key]:.3f}"
        )


def main():
    config_path = os.environ.get("AI_TRADER_CONFIG", "config.json")
    if not Path(config_path).exists():
        log.error(f"Config not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    db_path = config["db_path"]
    output_path = config.get("signal_weights_output", "state/signal_weights_suggested.json")
    analyze_signals(db_path, output_path)


if __name__ == "__main__":
    main()
