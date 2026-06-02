#!/usr/bin/env python3
"""
Watchdog for the Polybot live market data recorder.

Checks every N minutes that the recording process is alive and healthy.
Sends Telegram alerts on failure. Can optionally auto-restart.

Checks performed:
    1. Process alive (by PID or process name search)
    2. Data freshness (latest Parquet file age)
    3. Disk usage (prevents filling the disk)
    4. Tick rate (extracted from heartbeat log)

Alert triggers:
    - Process died → 🚨 CRITICAL: auto-restart if enabled
    - No data for >30 min → ⚠️ WARNING: process may be stuck
    - Disk > 500 MB → ⚠️ WARNING
    - Zero tick rate for >1h → ⚠️ WARNING

Usage (one-shot, for cron):
    python scripts/watchdog_recording.py --pid 55620 --once

Usage (daemon mode):
    python scripts/watchdog_recording.py --pid 55620 --interval 600

Usage (auto-find process):
    python scripts/watchdog_recording.py --auto --interval 600

Environment variables required for Telegram alerts:
    TELEGRAM_BOT_TOKEN  — from @BotFather
    TELEGRAM_CHAT_ID    — from @userinfobot
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import structlog

logger = structlog.get_logger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_PARQUET_DIR = Path("data/parquet")
DEFAULT_LOG_FILE = "/tmp/recording_168h_v6.log"
DEFAULT_CHECK_INTERVAL = 600  # 10 minutes
DEFAULT_DATA_MAX_AGE_MIN = 30  # alert if no new data for 30 min
DEFAULT_DISK_MAX_MB = 500  # alert if parquet dir exceeds 500 MB
DEFAULT_ZERO_TICK_HOURS = 1  # alert if zero ticks for 1 hour
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watchdog for Polybot live market data recorder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/watchdog_recording.py --pid 55620 --once
  python scripts/watchdog_recording.py --auto --interval 600
  python scripts/watchdog_recording.py --pid 55620 --interval 300 --auto-restart
        """,
    )
    parser.add_argument("--pid", type=int, help="PID of the recording process")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-find recording process by name")
    parser.add_argument("--interval", type=int, default=DEFAULT_CHECK_INTERVAL,
                        help=f"Seconds between checks (default: {DEFAULT_CHECK_INTERVAL})")
    parser.add_argument("--once", action="store_true",
                        help="Run a single check and exit (for cron)")
    parser.add_argument("--auto-restart", action="store_true",
                        help="Auto-restart recording if process died")
    parser.add_argument("--parquet-dir", default=str(DEFAULT_PARQUET_DIR),
                        help=f"Parquet data directory (default: {DEFAULT_PARQUET_DIR})")
    parser.add_argument("--log-file", default=DEFAULT_LOG_FILE,
                        help=f"Recording log file (default: {DEFAULT_LOG_FILE})")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Skip Telegram alerts (print to stdout only)")
    parser.add_argument("--data-max-age", type=int,
                        default=DEFAULT_DATA_MAX_AGE_MIN,
                        help=("Minutes without new data before warning "
                              f"(default: {DEFAULT_DATA_MAX_AGE_MIN})"))
    parser.add_argument("--quiet", action="store_true",
                        help="Only print alerts, not OK status")
    return parser.parse_args()


# ── Health Checks ─────────────────────────────────────────────────────────────

def find_recording_pid() -> int | None:
    """Find the recording process by searching for 'record_live_data.py'."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "record_live_data.py"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [int(p) for p in result.stdout.strip().split("\n") if p]
        # Exclude the watchdog itself and any shell wrappers
        for pid in pids:
            try:
                cmdline = open(f"/proc/{pid}/cmdline").read().replace("\x00", " ")
                if "record_live_data.py" in cmdline and "watchdog" not in cmdline:
                    return pid
            except (FileNotFoundError, PermissionError):
                continue
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def check_process_alive(pid: int | None) -> tuple[bool, str]:
    """
    Check if the recording process is alive.

    Returns (alive, detail_message).
    """
    if pid is None:
        return False, "No PID provided"

    try:
        os.kill(pid, 0)
        # Also check it's actually the recording script
        try:
            cmdline = open(f"/proc/{pid}/cmdline").read().replace("\x00", " ")
            if "record_live_data.py" not in cmdline:
                return False, f"PID {pid} exists but is not record_live_data.py: {cmdline[:80]}"
        except (FileNotFoundError, PermissionError):
            return False, f"PID {pid} exists but cannot read cmdline"

        # Get uptime
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "etime=", "--no-headers"],
                capture_output=True, text=True, timeout=5,
            )
            uptime = result.stdout.strip()
        except Exception:
            uptime = "unknown"

        return True, f"ALIVE (PID {pid}, uptime {uptime})"

    except (OSError, ProcessLookupError):
        return False, f"DEAD (PID {pid} not found)"


def check_data_freshness(parquet_dir: Path, max_age_min: int) -> tuple[bool, str]:
    """
    Check that new Parquet files are being created within max_age_min.

    Returns (fresh, detail_message).
    """
    if not parquet_dir.exists():
        return False, f"NO DATA — directory {parquet_dir} does not exist"

    parquet_files = list(parquet_dir.rglob("*.parquet"))
    if not parquet_files:
        return False, f"NO DATA — no Parquet files in {parquet_dir}"

    # Find the most recently modified file
    latest_file = max(parquet_files, key=lambda f: f.stat().st_mtime)
    latest_mtime = datetime.fromtimestamp(latest_file.stat().st_mtime, tz=timezone.utc)
    age = datetime.now(timezone.utc) - latest_mtime
    age_min = age.total_seconds() / 60

    total_files = len(parquet_files)
    total_size = sum(f.stat().st_size for f in parquet_files)

    if age_min > max_age_min:
        return False, (
            f"STALE DATA — newest file {age_min:.0f} min old "
            f"(max {max_age_min} min). {total_files} files, "
            f"{total_size / 1024:.0f} KB total"
        )

    return True, (
        f"FRESH — newest file {age_min:.0f} min ago. "
        f"{total_files} files, {total_size / 1024:.0f} KB total"
    )


def check_disk_usage(parquet_dir: Path, max_mb: int) -> tuple[bool, str]:
    """
    Check disk usage doesn't exceed max_mb.

    Returns (ok, detail_message).
    """
    if not parquet_dir.exists():
        return True, "No data directory yet"

    total_size = sum(
        f.stat().st_size for f in parquet_dir.rglob("*.parquet")
    )
    total_mb = total_size / (1024 * 1024)

    if total_mb > max_mb:
        return False, (
            f"HIGH DISK — {total_mb:.1f} MB exceeds {max_mb} MB limit"
        )

    return True, f"DISK OK — {total_mb:.1f} MB (limit {max_mb} MB)"


def extract_tick_stats(log_file: str) -> dict:
    """
    Extract tick statistics from the recording heartbeat log.

    Returns dict with:
        last_heartbeat: str (timestamp of last heartbeat)
        tick_rates: dict[market_id, ticks_per_hour]
        total_ticks: int
        minutes_since_heartbeat: float
    """
    log_path = Path(log_file)
    if not log_path.exists():
        return {"error": f"Log file not found: {log_file}"}

    try:
        lines = log_path.read_text().splitlines()
    except Exception:
        return {"error": f"Cannot read log: {log_file}"}

    # Find all heartbeat lines: "💓 [short_id..asset] N ticks | Hh elapsed | R ticks/h"
    heartbeats = []
    for line in lines:
        if "💓" not in line:
            continue
        # Parse: "  💓 [0xbb57ccf5853a85..BTC] 189 ticks | 0.1h elapsed | 1890 ticks/h"
        try:
            # Extract parts between brackets, after ticks, after elapsed
            bracket_part = line.split("[")[1].split("]")[0] if "[" in line else ""
            parts = bracket_part.split("..")
            if len(parts) >= 2:
                short_id = parts[0]
                asset = parts[1]

            ticks_part = line.split("]")[1] if "]" in line else ""
            ticks_str = ticks_part.split(" ticks")[0].strip()
            ticks = int(ticks_str)

            rate_part = line.split("|")[-1] if "|" in line else ""
            rate_str = rate_part.split(" ticks/h")[0].strip()
            rate = float(rate_str) if rate_str else 0

            heartbeats.append({
                "market": f"{short_id}..{asset}",
                "ticks": ticks,
                "rate": rate,
            })
        except (IndexError, ValueError):
            continue

    # Check time since last heartbeat (approximate via log file mtime)
    log_mtime = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc)
    minutes_since = (datetime.now(timezone.utc) - log_mtime).total_seconds() / 60

    recent_ticks = sum(h["ticks"] for h in heartbeats) if heartbeats else 0

    return {
        "last_heartbeats": heartbeats[-4:] if heartbeats else [],
        "recent_ticks": recent_ticks,
        "minutes_since_last_write": round(minutes_since, 1),
        "heartbeat_count": len(heartbeats),
    }


def check_tick_activity(stats: dict, zero_tick_hours: float) -> tuple[bool, str]:
    """
    Check that ticks are actually flowing.

    Returns (active, detail_message).
    """
    if "error" in stats:
        return True, f"Cannot check ticks: {stats['error']}"

    if not stats.get("last_heartbeats"):
        return True, "No heartbeats yet (recording may have just started)"

    recent_ticks = stats.get("recent_ticks", 0)

    if recent_ticks == 0 and stats.get("minutes_since_last_write", 0) > zero_tick_hours * 60:
        return False, (
            f"ZERO TICKS for >{zero_tick_hours}h — "
            f"process may be stuck or markets inactive"
        )

    markets_with_ticks = sum(
        1 for h in stats["last_heartbeats"] if h.get("ticks", 0) > 0
    )
    return True, (
        f"TICKS OK — {recent_ticks} recent ticks "
        f"across {markets_with_ticks} active markets"
    )


# ── Telegram Alerting ─────────────────────────────────────────────────────────

def send_telegram_alert(bot_token: str, chat_id: str, message: str) -> bool:
    """
    Send an alert via Telegram Bot API.

    Returns True if sent successfully.
    """
    if not bot_token or not chat_id:
        logger.warning("telegram_skipped", reason="missing_token_or_chat_id")
        return False

    import httpx

    url = TELEGRAM_API_URL.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error("telegram_alert_failed", error=str(e))
        return False


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_alert_message(
    checks: dict[str, tuple[bool, str]],
    tick_stats: dict,
    auto_restart: bool = False,
    restarted: bool = False,
) -> str:
    """Build a formatted Telegram alert message from check results."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "<b>🔍 Polybot Recording Watchdog</b>",
        f"<i>{now}</i>",
        "",
    ]

    status_emoji = "🟢"
    for name, (ok, _) in checks.items():
        if not ok:
            status_emoji = "🔴"
            break

    status_text = "HEALTHY" if status_emoji == '🟢' else "ISSUES DETECTED"
    lines.append(f"{status_emoji} <b>Status: {status_text}</b>")
    lines.append("")

    for name, (ok, detail) in checks.items():
        emoji = "✅" if ok else "❌"
        lines.append(f"{emoji} <b>{name}</b>: {_escape_html(detail)}")

    # Tick stats summary
    if "error" not in tick_stats:
        lines.append("")
        lines.append(f"📊 <b>Recent ticks</b>: {tick_stats.get('recent_ticks', 0)}")
        if tick_stats.get("last_heartbeats"):
            latest_hb = tick_stats["last_heartbeats"][-1]
            lines.append(
                f"📈 <b>Latest rate</b>: {latest_hb.get('rate', 0):.0f} ticks/h "
                f"({latest_hb.get('market', '?')})"
            )
        lines.append(
            f"⏱️ <b>Log stale</b>: {tick_stats.get('minutes_since_last_write', 0):.0f} min"
        )

    if restarted:
        lines.append("")
        lines.append("🔄 <b>Auto-restart triggered</b> — new recording process launched")

    return "\n".join(lines)


# ── Auto-Restart ──────────────────────────────────────────────────────────────

def restart_recording() -> tuple[bool, str]:
    """
    Attempt to restart the recording process.

    Returns (success, message).
    """
    script_path = Path(__file__).parent / "record_live_data.py"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = f"/tmp/recording_{timestamp}.log"

    try:
        # Kill existing recording processes (scoped to current user)
        import getpass
        user = getpass.getuser()
        subprocess.run(
            ["pkill", "-u", user, "-f", "record_live_data.py"],
            timeout=5,
        )

        # Launch new recording
        subprocess.Popen(
            [
                "setsid", "python", "-u", str(script_path),
                "--all", "--duration-hours", "168", "--batch-size", "1000",
            ],
            stdin=subprocess.DEVNULL,
            stdout=open(log_file, "w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        # Wait a moment and find the new PID
        time.sleep(3)
        new_pid = find_recording_pid()

        if new_pid:
            return True, f"Restarted successfully — new PID {new_pid}, log: {log_file}"
        return False, "Launched but cannot find new PID"

    except Exception as e:
        return False, f"Restart failed: {e}"


# ── Main Loop ─────────────────────────────────────────────────────────────────

def run_check(args: argparse.Namespace) -> tuple[bool, dict, dict]:
    """
    Run all health checks. Returns (all_ok, checks_dict, tick_stats).
    """
    # Determine PID
    pid = args.pid
    if args.auto or pid is None:
        pid = find_recording_pid()

    checks = {}
    checks["Process"] = check_process_alive(pid)
    checks["Data Freshness"] = check_data_freshness(
        Path(args.parquet_dir), args.data_max_age
    )
    checks["Disk Usage"] = check_disk_usage(
        Path(args.parquet_dir), DEFAULT_DISK_MAX_MB
    )

    tick_stats = extract_tick_stats(args.log_file)
    checks["Tick Activity"] = check_tick_activity(tick_stats, DEFAULT_ZERO_TICK_HOURS)

    all_ok = all(ok for ok, _ in checks.values())
    return all_ok, checks, tick_stats


def main() -> None:
    args = parse_args()

    # Telegram credentials (from environment, same as rest of the system)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not args.no_telegram and (not bot_token or not chat_id):
        print("⚠️  TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")
        print("   Telegram alerts disabled. Set env vars or use --no-telegram.")
        print("   Running in stdout-only mode.")
        args.no_telegram = True

    last_alert_state = None  # track state changes to avoid spam

    while True:
        all_ok, checks, tick_stats = run_check(args)
        restarted = False

        # Print status
        if not args.quiet or not all_ok:
            status = "🟢 HEALTHY" if all_ok else "🔴 ISSUES"
            print(f"\n{'═' * 55}")
            print(f"  {status} — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
            for name, (ok, detail) in checks.items():
                emoji = "✅" if ok else "❌"
                print(f"  {emoji} {name}: {detail}")
            if tick_stats.get("last_heartbeats"):
                print(
                    f"  📊 Ticks: "
                    f"{tick_stats.get('recent_ticks', 0)} recent"
                )
            print(f"{'═' * 55}")

        # Send Telegram alert if issues detected
        current_state = "ok" if all_ok else "alert"

        if not all_ok and current_state != last_alert_state and not args.no_telegram:
            # Attempt auto-restart if process is dead
            process_ok = checks.get("Process", (True, ""))[0]
            if not process_ok and args.auto_restart:
                print("  🔄 Auto-restarting recording...")
                success, msg = restart_recording()
                print(f"  {'✅' if success else '❌'} {msg}")
                restarted = success

            message = build_alert_message(
                checks, tick_stats,
                auto_restart=args.auto_restart,
                restarted=restarted,
            )

            sent = send_telegram_alert(bot_token, chat_id, message)
            if sent:
                print("  📤 Telegram alert sent")
            else:
                print("  ⚠️  Telegram alert failed (check credentials)")

        # Also send recovery notification
        if all_ok and current_state != last_alert_state \
                and last_alert_state == "alert" and not args.no_telegram:
            ts = datetime.now(timezone.utc)
            recovery_msg = (
                f"<b>✅ Polybot Recording RECOVERED</b>\n"
                f"<i>{ts.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>\n\n"
                "All checks passing."
            )
            send_telegram_alert(bot_token, chat_id, recovery_msg)

        last_alert_state = current_state

        if args.once:
            sys.exit(0 if all_ok else 1)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
