import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

try:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_SCRIPT = os.path.join(BASE_DIR, "sweep_bot.py")

def check_git_updates() -> bool:
    """
    Checks if remote git repository has new commits using locale-independent rev-list.
    """
    if not os.path.exists(os.path.join(BASE_DIR, ".git")):
        return False
    try:
        # Fetch remote updates
        subprocess.run(["git", "fetch"], cwd=BASE_DIR, capture_output=True, text=True, timeout=15)
        # Check commits behind upstream in a language-agnostic manner
        res = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..@{u}"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=10
        )
        if res.returncode == 0:
            count = int(res.stdout.strip() or 0)
            if count > 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [Watchdog] Detected {count} incoming commit(s). Pulling...")
                pull_res = subprocess.run(["git", "pull"], cwd=BASE_DIR, capture_output=True, text=True, timeout=20)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [Watchdog] {pull_res.stdout.strip()}")
                return True
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [Watchdog] Git update check skipped: {e}")
    return False

def main():
    parser = argparse.ArgumentParser(description="24/7 Watchdog Supervisor for Sweep Bot")
    parser.add_argument("--auto-update", action="store_true", help="Enable automatic git pull on changes")
    parser.add_argument("--update-interval", type=int, default=1800, help="Git update check interval in seconds (default: 1800)")
    parser.add_argument("--max-retries", type=int, default=50, help="Maximum consecutive restarts on failure")
    parser.add_argument("bot_args", nargs=argparse.REMAINDER, help="Arguments passed through to sweep_bot.py")
    args = parser.parse_args()

    print("=" * 65)
    print("  Sweep Bot 24/7 Watchdog Supervisor")
    print(f"  Target: {BOT_SCRIPT}")
    print(f"  Auto-Update: {'Enabled' if args.auto_update else 'Disabled'}")
    print("=" * 65)

    consecutive_crashes = 0
    last_update_check = time.time()

    cmd = [sys.executable, BOT_SCRIPT]
    if args.bot_args:
        extra = args.bot_args[1:] if args.bot_args and args.bot_args[0] == "--" else args.bot_args
        cmd.extend(extra)

    while True:
        try:
            if args.auto_update and (time.time() - last_update_check > args.update_interval):
                check_git_updates()
                last_update_check = time.time()

            start_time = time.time()
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # Print command without exposing secrets
            print(f"\n[{ts}] [Watchdog] Starting Sweep Bot: {' '.join(cmd)}")

            process = subprocess.Popen(cmd, cwd=BASE_DIR)

            while True:
                ret = process.poll()
                if ret is not None:
                    uptime = time.time() - start_time
                    ts_exit = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"\n[{ts_exit}] [Watchdog] Bot process exited with code {ret} (uptime: {uptime:.1f}s)")

                    if ret == 0:
                        print(f"[{ts_exit}] [Watchdog] Clean exit by user. Stopping supervisor.")
                        return

                    # If bot ran stably for >= 10 minutes (600s), reset consecutive crashes
                    if uptime >= 600:
                        consecutive_crashes = 0

                    consecutive_crashes += 1
                    if consecutive_crashes >= args.max_retries:
                        print(f"[{ts_exit}] [Watchdog] Max consecutive retries reached ({args.max_retries}). Exiting.")
                        sys.exit(1)

                    backoff = min(5 * consecutive_crashes, 30)
                    print(f"[{ts_exit}] [Watchdog] Restarting bot in {backoff}s (consecutive crash #{consecutive_crashes})...")
                    time.sleep(backoff)
                    break

                # Periodic update check while bot is running
                if args.auto_update and (time.time() - last_update_check > args.update_interval):
                    last_update_check = time.time()
                    if check_git_updates():
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] [Watchdog] Restarting bot to apply updates...")
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        break

                time.sleep(1)

        except KeyboardInterrupt:
            print("\n[Watchdog] Terminating bot process...")
            if 'process' in locals() and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            print("[Watchdog] Supervisor stopped.")
            sys.exit(0)

if __name__ == "__main__":
    main()
