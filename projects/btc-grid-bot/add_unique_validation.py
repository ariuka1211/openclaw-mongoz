#!/usr/bin/env python3
import re

# Read the file
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'r') as f:
    content = f.read()

# Add validation for unique levels in deploy method
# Find the deploy method and add a check after levels are received
new_validation = '''        # Validate that levels are unique
        if len(buy_levels) != len(set(buy_levels)):
            raise ValueError("buy_levels contains duplicates")
        if len(sell_levels) != len(set(sell_levels)):
            raise ValueError("sell_levels contains duplicates")
'''

# Insert after the line "buy_levels = sorted(levels["buy_levels"])" and similarly for sell
pattern = r'(        buy_levels = sorted\(levels\["buy_levels"\]\)\n)'
replacement = r'\1' + new_validation + '\n'

content = re.sub(pattern, replacement, content, flags=re.DOTALL)

# Write back
with open('/root/.openclaw/workspace/projects/btc-grid-bot/grid.py', 'w') as f:
    f.write(content)

print("Added unique levels validation to grid.py")