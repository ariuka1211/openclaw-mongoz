with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'r') as f:
    lines = f.readlines()

# Remove line 101 (index 100) which is a stray '"'
if lines[100].strip() == '"':
    lines.pop(100)

with open('/root/.openclaw/workspace/projects/btc-grid-bot/main.py', 'w') as f:
    f.writelines(lines)

print("Removed stray quote from main.py")