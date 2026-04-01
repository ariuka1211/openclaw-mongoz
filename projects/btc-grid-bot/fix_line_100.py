with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'r') as f:
    lines = f.readlines()

# Fix line 100 (index 99)
lines[99] = '                            f"Daily PnL: ${pnl:.2f} ({pnl/cfg[\'capital\'][\'starting_equity\']*100:.1f}%)\\n"\n'

with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'w') as f:
    f.writelines(lines)

print("Fixed line 100 in main.py")