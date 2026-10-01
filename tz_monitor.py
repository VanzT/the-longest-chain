"""
Twilight Zone Solver Monitor
=============================
Runs independently alongside tz_solver_pc6.py.
Checks hourly for meaningful changes and sends email alerts.

Triggers an email when:
  - A new best chain is found (tz_best_chain_pc6.txt gets longer)
  - One or more branches complete (completed_branches count increases)

Setup:
  1. Enable 2-Step Verification on your Google account
  2. Go to https://myaccount.google.com/apppasswords
  3. Create an App Password for "Mail"
  4. Fill in your details in the SETTINGS section below

Run alongside the solver:
  python3 tz_monitor.py
"""

import json
import os
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── SETTINGS ──────────────────────────────────────────────────────────────────
GMAIL_ADDRESS      = "your_email@gmail.com"       # your Gmail address
GMAIL_APP_PASSWORD = "xxxx xxxx xxxx xxxx"        # Gmail App Password (not your login password)
NOTIFY_ADDRESS     = "your_email@gmail.com"       # where to send alerts (can be same address)

CHECKPOINT_FILE    = "tz_checkpoint_pc6.json"
BEST_CHAIN_FILE    = "tz_best_chain_pc6.txt"
TOTAL_BRANCHES     = 44189                         # pc6 branch count for tz_cast_normalized.csv
CHECK_INTERVAL     = 3600                          # seconds between checks (3600 = 1 hour)
# ──────────────────────────────────────────────────────────────────────────────


def send_email(subject, body):
    """Send an email via Gmail SMTP."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = NOTIFY_ADDRESS
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, NOTIFY_ADDRESS, msg.as_string())
        print(f"  Email sent: {subject}")
        return True
    except Exception as e:
        print(f"  Failed to send email: {e}")
        return False


def read_checkpoint():
    """Read the checkpoint file. Returns None if file doesn't exist yet."""
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {
            "best_chain_len":  (len(data.get("best_chain", [])) + 1) // 2,
            "completed_count": len(data.get("completed_branches", [])),
            "completed_list":  data.get("completed_branches", []),
            "total_calls":     data.get("total_calls", 0),
        }
    except Exception as e:
        print(f"  Could not read checkpoint: {e}")
        return None


def read_best_chain_length():
    """Read the episode count from the best chain file. Returns None if not found."""
    if not os.path.exists(BEST_CHAIN_FILE):
        return None
    try:
        with open(BEST_CHAIN_FILE, encoding="utf-8") as f:
            for line in f:
                if line.startswith("Episodes in chain"):
                    return int(line.split(":")[1].strip())
    except Exception as e:
        print(f"  Could not read best chain file: {e}")
    return None


def format_chain_summary(checkpoint):
    """Build a readable summary of the current state."""
    if checkpoint is None:
        return "No checkpoint data available yet."
    lines = []
    pct = checkpoint['completed_count'] / TOTAL_BRANCHES * 100
    lines.append(f"Best chain length : {checkpoint['best_chain_len']} episodes")
    lines.append(f"Branches complete : {checkpoint['completed_count']} / {TOTAL_BRANCHES}  ({pct:.2f}%)")
    lines.append(f"Total DFS calls   : {checkpoint['total_calls']:,}")
    if checkpoint['completed_list']:
        lines.append("")
        lines.append("Most recently completed branches:")
        for b in checkpoint['completed_list'][-10:]:
            if len(b) >= 3:
                lines.append(f"  {b[0]} → [{b[1]}] → {b[2]}")
    return "\n".join(lines)


def format_time(s):
    if s < 60:   return f"{s:.0f}s"
    if s < 3600: return f"{s/60:.1f}m"
    return f"{int(s//3600)}h {int((s%3600)//60)}m"


def test_email():
    """Send a test email to confirm settings are correct."""
    print("Sending test email...")
    success = send_email(
        subject="TZ Monitor: Test Email",
        body=(
            "Your Twilight Zone solver monitor is set up correctly.\n\n"
            "You will receive an email when:\n"
            "  - A new best chain is found\n"
            "  - One or more branches complete\n\n"
            f"Watching: {CHECKPOINT_FILE}\n"
            f"          {BEST_CHAIN_FILE}\n\n"
            "Monitor is now running and will check every hour."
        )
    )
    if success:
        print("Test email sent successfully!")
    else:
        print("Test email failed. Check your Gmail settings.")
    return success


# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("TWILIGHT ZONE SOLVER MONITOR")
    print("=" * 55)
    print(f"Checking every {CHECK_INTERVAL//3600} hour(s)")
    print(f"Watching: {CHECKPOINT_FILE}")
    print(f"          {BEST_CHAIN_FILE}")
    print(f"Alerting: {NOTIFY_ADDRESS}")
    print()

    # Validate settings
    if "your_email" in GMAIL_ADDRESS:
        print("ERROR: Please fill in your Gmail address in SETTINGS before running.")
        exit(1)
    if "xxxx" in GMAIL_APP_PASSWORD:
        print("ERROR: Please fill in your Gmail App Password in SETTINGS before running.")
        exit(1)

    # Send test email on startup
    if not test_email():
        print("\nCould not send test email. Fix settings and try again.")
        exit(1)

    # Read initial state — files may not exist yet if solver just started
    print("\nReading initial state...")
    initial_checkpoint = read_checkpoint()
    initial_chain_len  = read_best_chain_length()

    # Chain length is independent of checkpoint — read it regardless
    initial_chain_len_display = str(initial_chain_len) if initial_chain_len else "unknown"

    if initial_checkpoint is None:
        print("  Checkpoint file not found yet — solver may still be starting up.")
        print("  Monitor will wait and check again each hour.")
        print(f"  Current best chain : {initial_chain_len_display} episodes")
        initial_completed = 0
    else:
        initial_completed = initial_checkpoint["completed_count"]
        print(f"  Current best chain : {initial_chain_len_display} episodes")
        print(f"  Completed branches : {initial_completed}")
        print(f"  Total calls        : {initial_checkpoint['total_calls']:,}")

    last_chain_len       = initial_chain_len
    last_completed_count = initial_completed
    start_time           = time.time()
    check_count          = 0

    # Send initial state email
    print("\nSending initial state email...")
    send_email(
        subject=f"TZ Monitor: Started — current best is {initial_chain_len_display} episodes",
        body=(
            f"Monitor has started. Current solver state:\n\n"
            f"{format_chain_summary(initial_checkpoint)}\n\n"
            f"You will receive an email when the best chain improves "
            f"or a branch completes.\n\n"
            f"Watching:\n  {CHECKPOINT_FILE}\n  {BEST_CHAIN_FILE}"
        )
    )

    print("Monitor running. Press Ctrl+C to stop.\n")

    while True:
        time.sleep(CHECK_INTERVAL)
        check_count += 1
        now     = time.time()
        elapsed = format_time(now - start_time)

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Check #{check_count} (uptime: {elapsed})")

        checkpoint = read_checkpoint()
        chain_len  = read_best_chain_length()

        # Files may not exist yet — wait silently
        if checkpoint is None:
            print("  Checkpoint not available yet — solver still starting or files not written")
            continue

        completed_count = checkpoint["completed_count"]
        new_chain    = chain_len is not None and chain_len > (last_chain_len or 0)
        new_branches = completed_count > last_completed_count

        if new_chain:
            improvement = chain_len - (last_chain_len or 0)
            send_email(
                subject=f"TZ Solver: NEW BEST CHAIN — {chain_len} episodes! (+{improvement})",
                body=(
                    f"A new longest chain has been found!\n\n"
                    f"Previous best : {last_chain_len} episodes\n"
                    f"New best      : {chain_len} episodes\n"
                    f"Improvement   : +{improvement} episodes\n\n"
                    f"Current state:\n"
                    f"{format_chain_summary(checkpoint)}\n\n"
                    f"Monitor uptime: {elapsed}"
                )
            )
            last_chain_len = chain_len
            print(f"  *** NEW BEST CHAIN: {chain_len} episodes ***")

        if new_branches:
            new_count = completed_count - last_completed_count
            pct = completed_count / TOTAL_BRANCHES * 100
            send_email(
                subject=f"TZ Solver: {new_count} branch(es) completed — {completed_count}/{TOTAL_BRANCHES} done ({pct:.2f}%)",
                body=(
                    f"{new_count} branch(es) have completed since the last check!\n\n"
                    f"Previous completed : {last_completed_count}\n"
                    f"Now completed      : {completed_count}\n"
                    f"Total branches     : {TOTAL_BRANCHES}\n"
                    f"Progress           : {pct:.2f}%\n\n"
                    f"Current state:\n"
                    f"{format_chain_summary(checkpoint)}\n\n"
                    f"Monitor uptime: {elapsed}"
                )
            )
            last_completed_count = completed_count
            print(f"  *** {new_count} NEW BRANCH(ES) COMPLETED ***")

        if not new_chain and not new_branches:
            chain_display = chain_len if chain_len else "unknown"
            print(f"  No changes. Best: {chain_display}, "
                  f"Branches: {completed_count}/{TOTAL_BRANCHES}, "
                  f"Calls: {checkpoint['total_calls']:,}")
