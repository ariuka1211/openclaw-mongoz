#!/usr/bin/env python3
"""
BTC Grid Bot — Memory Query Tool

Query and analyze stored AI decisions and session outcomes.
Usage: python3 memory_query.py [--days 7] [--summary] [--patterns]
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add bot to path
sys.path.insert(0, str(Path(__file__).parent))

from core.memory_layer import get_memory


def format_timestamp(ts_str):
    """Format ISO timestamp to readable string."""
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.strftime('%m/%d %H:%M')
    except:
        return ts_str[:16]


def print_analyses(days=7):
    """Print recent AI analyses."""
    mem = get_memory()
    analyses = mem.query_analyses(days=days)
    
    print(f"\n🧠 AI Analyses (last {days} days)")
    print("=" * 60)
    
    if not analyses:
        print("No analyses found.")
        return
    
    for a in analyses[-10:]:  # Show last 10
        ts = format_timestamp(a['_timestamp'])
        direction = a.get('direction', '?')
        confidence = a.get('confidence', '?')
        note = a.get('note', '')[:50]
        price = a.get('market_context', {}).get('current_price', 0)
        regime = a.get('market_context', {}).get('regime', '?')
        
        emoji = {'long': '📈', 'short': '📉', 'pause': '⏸️'}.get(direction, '❓')
        
        print(f"{ts} {emoji} {direction:5} | {confidence:4} | ${price:>6.0f} | {regime:8} | {note}")


def print_fills(days=7):
    """Print recent trade fills."""
    mem = get_memory()
    fills = mem.query_trades(days=days)
    
    print(f"\n⚡ Trade Fills (last {days} days)")
    print("=" * 60)
    
    if not fills:
        print("No fills found.")
        return
    
    for f in fills[-15:]:  # Show last 15
        ts = format_timestamp(f['_timestamp'])
        trade_data = f.get('trade_data', {})
        session_ctx = f.get('session_context', {})
        
        fill_type = trade_data.get('type', 'individual_fill')
        side = trade_data.get('side', '?')
        price = trade_data.get('price', 0)
        size = trade_data.get('size', 0)
        level = trade_data.get('level', '?')
        time_since = trade_data.get('time_since_deploy_min', 0)
        
        if fill_type == 'completed_trade':
            # Completed trade (buy+sell pair)
            pnl = trade_data.get('pnl', 0)
            buy_price = trade_data.get('buy_price', 0)
            sell_price = trade_data.get('sell_price', 0)
            direction = trade_data.get('grid_direction', 'long')
            duration = trade_data.get('trade_duration_min', 0)
            emoji = '✅' if pnl >= 0 else '❌'
            
            if direction == 'long':
                print(f"{ts} {emoji} ${buy_price:>6.0f}→${sell_price:>6.0f} | ${pnl:>5.2f} | {duration:3.0f}m | {direction}")
            else:
                print(f"{ts} {emoji} ${sell_price:>6.0f}→${buy_price:>6.0f} | ${pnl:>5.2f} | {duration:3.0f}m | {direction}")
        else:
            # Individual fill
            emoji = '🔵' if side == 'buy' else '🟠'
            grid_dir = trade_data.get('grid_direction', '?')
            print(f"{ts} {emoji} {side.upper():4} ${price:>6.0f} | L{level} | {time_since:3.0f}m | {grid_dir}")


def print_sessions(days=7):
    """Print recent session outcomes."""
    mem = get_memory()
    sessions = mem.query_sessions(limit=50)
    
    # Filter by days
    cutoff = datetime.now() - timedelta(days=days)
    recent_sessions = []
    for s in sessions:
        try:
            ts = datetime.fromisoformat(s['_timestamp'].replace('Z', '+00:00'))
            if ts >= cutoff:
                recent_sessions.append(s)
        except:
            continue
    
    print(f"\n💼 Sessions (last {days} days)")
    print("=" * 60)
    
    if not recent_sessions:
        print("No sessions found.")
        return
    
    total_pnl = 0
    for s in recent_sessions[-10:]:  # Show last 10
        ts = format_timestamp(s['_timestamp'])
        pnl = s.get('realized_pnl', 0)
        trades = s.get('trades', 0)
        win_rate = s.get('win_rate', 0)
        issues = s.get('issues', [])
        
        total_pnl += pnl
        emoji = '✅' if pnl >= 0 else '❌'
        issue_txt = f"({', '.join(issues[:2])})" if issues else ""
        
        print(f"{ts} {emoji} ${pnl:>6.2f} | {trades:2} trades | {win_rate:.1%} win | {issue_txt}")
    
    print(f"\nTotal PnL: ${total_pnl:.2f}")


def print_summary():
    """Print overall pattern summary."""
    mem = get_memory()
    summary = mem.get_pattern_summary(days=30)
    
    print(f"\n📊 Pattern Summary (30 days)")
    print("=" * 60)
    
    if summary.get('status') == 'no_data':
        print(summary.get('message', 'No data available'))
        return
    
    print(f"Sessions: {summary.get('total_sessions', 0)} total")
    print(f"  • Wins: {summary.get('win_sessions', 0)}")
    print(f"  • Losses: {summary.get('loss_sessions', 0)}")
    print(f"  • Avg PnL: ${summary.get('avg_session_pnl', 0):.2f}")
    print(f"  • Best: ${summary.get('best_session', 0):.2f}")
    print(f"  • Worst: ${summary.get('worst_session', 0):.2f}")
    
    print(f"\nTrades: {summary.get('total_trades', 0)} total")
    by_side = summary.get('by_side', {})
    for side, data in by_side.items():
        count = data.get('count', 0)
        total_pnl = data.get('total_pnl', 0)
        avg_pnl = total_pnl / count if count > 0 else 0
        print(f"  • {side}: {count} fills, avg ${avg_pnl:.2f}")
    
    print(f"\nAI Decisions: {summary.get('total_analyses', 0)} total")
    directions = summary.get('analysis_directions', {})
    for direction, data in directions.items():
        count = data.get('count', 0)
        pauses = data.get('pauses', 0)
        print(f"  • {direction}: {count} times ({pauses} paused)")


def print_patterns():
    """Print pattern analysis."""
    mem = get_memory()
    
    print(f"\n🔍 Pattern Analysis")
    print("=" * 60)
    
    # Analyze win/loss patterns
    sessions = mem.query_sessions(limit=100)
    analyses = mem.query_analyses(days=30)
    fills = mem.query_trades(days=30)
    
    if not sessions and not fills:
        print("Insufficient data for pattern analysis.")
        return
    
    # Analyze fill timing patterns
    completed_trades = [f for f in fills if f.get('trade_data', {}).get('type') == 'completed_trade']
    if completed_trades:
        print("Fill Timing Patterns:")
        quick_fills = [t for t in completed_trades if t.get('trade_data', {}).get('trade_duration_min', 999) <= 10]
        slow_fills = [t for t in completed_trades if t.get('trade_data', {}).get('trade_duration_min', 0) > 30]
        
        if quick_fills:
            quick_wins = sum(1 for t in quick_fills if t.get('trade_data', {}).get('pnl', 0) > 0)
            quick_win_rate = quick_wins / len(quick_fills)
            quick_avg_pnl = sum(t.get('trade_data', {}).get('pnl', 0) for t in quick_fills) / len(quick_fills)
            print(f"  • Quick fills (≤10min): {quick_win_rate:.1%} win rate ({quick_wins}/{len(quick_fills)}), avg ${quick_avg_pnl:.2f}")
        
        if slow_fills:
            slow_wins = sum(1 for t in slow_fills if t.get('trade_data', {}).get('pnl', 0) > 0)
            slow_win_rate = slow_wins / len(slow_fills)
            slow_avg_pnl = sum(t.get('trade_data', {}).get('pnl', 0) for t in slow_fills) / len(slow_fills)
            print(f"  • Slow fills (>30min): {slow_win_rate:.1%} win rate ({slow_wins}/{len(slow_fills)}), avg ${slow_avg_pnl:.2f}")
    
    # Win rate by direction
    direction_outcomes = {}
    for i, analysis in enumerate(analyses):
        direction = analysis.get('direction')
        if direction and i < len(sessions):
            session = sessions[i]
            pnl = session.get('realized_pnl', 0)
            
            if direction not in direction_outcomes:
                direction_outcomes[direction] = {'wins': 0, 'losses': 0, 'total_pnl': 0}
            
            if pnl > 0:
                direction_outcomes[direction]['wins'] += 1
            elif pnl < 0:
                direction_outcomes[direction]['losses'] += 1
            direction_outcomes[direction]['total_pnl'] += pnl
    
    print("\nWin Rate by Direction:")
    for direction, data in direction_outcomes.items():
        total = data['wins'] + data['losses']
        win_rate = data['wins'] / total if total > 0 else 0
        avg_pnl = data['total_pnl'] / total if total > 0 else 0
        print(f"  • {direction}: {win_rate:.1%} win rate ({data['wins']}/{total}), avg ${avg_pnl:.2f}")
    
    # Market regime analysis
    regime_outcomes = {}
    for i, analysis in enumerate(analyses):
        regime = analysis.get('market_context', {}).get('regime', 'unknown')
        if regime and i < len(sessions):
            session = sessions[i]
            pnl = session.get('realized_pnl', 0)
            
            if regime not in regime_outcomes:
                regime_outcomes[regime] = {'count': 0, 'total_pnl': 0, 'wins': 0}
            
            regime_outcomes[regime]['count'] += 1
            regime_outcomes[regime]['total_pnl'] += pnl
            if pnl > 0:
                regime_outcomes[regime]['wins'] += 1
    
    print("\nPerformance by Market Regime:")
    for regime, data in regime_outcomes.items():
        count = data['count']
        avg_pnl = data['total_pnl'] / count if count > 0 else 0
        win_rate = data['wins'] / count if count > 0 else 0
        print(f"  • {regime}: {count} sessions, ${avg_pnl:.2f} avg, {win_rate:.1%} win")


def main():
    parser = argparse.ArgumentParser(description='Query grid bot memory')
    parser.add_argument('--days', type=int, default=7, help='Days to look back')
    parser.add_argument('--summary', action='store_true', help='Show pattern summary')
    parser.add_argument('--patterns', action='store_true', help='Show detailed patterns')
    parser.add_argument('--all', action='store_true', help='Show everything')
    
    args = parser.parse_args()
    
    if args.all:
        args.summary = args.patterns = True
    
    # Default to showing recent data if no flags
    if not (args.summary or args.patterns):
        print_analyses(args.days)
        print_fills(args.days)
        print_sessions(args.days)
    
    if args.summary:
        print_summary()
    
    if args.patterns:
        print_patterns()


if __name__ == '__main__':
    main()