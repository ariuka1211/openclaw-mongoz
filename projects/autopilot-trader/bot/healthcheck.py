"""
Health check for Docker — checks if bot process is alive.
Uses /proc filesystem (no external deps needed).
"""
import os
import sys

def main():
    # Find bot.py process by scanning /proc
    bot_pid = None
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                    cmdline = f.read().decode("utf-8", errors="replace")
                    if "bot.py" in cmdline:
                        bot_pid = pid_dir
                        break
            except (PermissionError, FileNotFoundError):
                continue
    except Exception as e:
        print(f"UNHEALTHY: cannot scan /proc: {e}")
        sys.exit(1)

    if not bot_pid:
        print("UNHEALTHY: bot.py process not found")
        sys.exit(1)

    # Check if process is alive (not zombie)
    try:
        with open(f"/proc/{bot_pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    state = line.split()[1]
                    if state == "Z":
                        print(f"UNHEALTHY: bot.py is zombie")
                        sys.exit(1)
                    break
    except FileNotFoundError:
        print("UNHEALTHY: process vanished")
        sys.exit(1)

    print(f"OK (pid {bot_pid})")
    sys.exit(0)

if __name__ == "__main__":
    main()
