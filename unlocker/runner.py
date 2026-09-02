import subprocess
import sys
import time
from datetime import datetime, timedelta

from unlocker.api import API_URL, send_command, send_aset, send_alist
from unlocker.state import (
    DEFAULT_PROGRESS,
    profile_paths,
    load_config,
    load_progress,
    save_progress,
    cleanup_profile)

ASF_SHUTDOWN_DELAY = 300  # 5 minutes


def _session_bounds(achievements):
    """Splits achievements into (start, end) index ranges, one per session."""
    bounds = []
    start = 0
    for i in range(1, len(achievements) + 1):
        if i == len(achievements) or achievements[i]["new_session"]:
            bounds.append((start, i))
            start = i
    return bounds


def _schedule_asf_shutdown(delay_seconds=ASF_SHUTDOWN_DELAY):
    """Detached background timer: after delay_seconds, shuts ArchiSteamFarm
    down entirely — but only if it still looks idle by then, so it isn't
    killed out from under an interleaved session for another game that
    might get started in the meantime."""
    child_code = f"""
import json, subprocess, time

def cmd(c):
    p = subprocess.run(
        ["curl", "-s", "-X", "POST", {API_URL!r},
         "-H", "Content-Type: application/json", "-d", json.dumps({{"Command": c}})],
        capture_output=True, text=True, timeout=30)
    try:
        return json.loads(p.stdout).get("Result", "")
    except Exception:
        return ""

time.sleep({delay_seconds})
if "not farming anything" in cmd("status").lower():
    cmd("exit")
"""
    subprocess.Popen(
        [sys.executable, "-c", child_code],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _estimate_session(achievements, start_from, unlocked, progress, first_run):
    """Returns (end_index, wait_seconds, duration_seconds) for the session
    starting at start_from: every achievement up to (not including) the next
    one that starts a new session, or the end of the list.

    wait_seconds is how long until the first real (not-already-unlocked)
    achievement can fire — mirrors the real loop's delay rule for it exactly,
    including the first-run/just-resumed-from-cooldown "fires immediately"
    cases. duration_seconds is the configured delay for every real
    achievement after that one, i.e. the active time once the session has
    actually started."""
    end = start_from + 1
    while end < len(achievements) and not achievements[end]["new_session"]:
        end += 1

    wait = 0
    duration = 0
    is_first_real = True
    for i in range(start_from, end):
        ach = achievements[i]
        if unlocked.get(ach["id"], False):
            continue

        if is_first_real:
            is_first_real = False
            if first_run:
                wait = 0
            elif progress["next_unlock_at"] is not None:
                unlock_at = datetime.fromisoformat(progress["next_unlock_at"])
                wait = max(0, int((unlock_at - datetime.now()).total_seconds()))
            else:
                wait = ach["delay"]
        else:
            duration += ach["delay"]

    return end, wait, duration


def _format_duration(seconds):
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{int(seconds)}s"


def run(game_name=None, force=False):
    config_path, progress_path = profile_paths(game_name)

    config = load_config(config_path)
    achievements = config["achievements"]
    appid = config["appid"]

    progress = load_progress(progress_path)

    if progress["appid"] != 0 and progress["appid"] != appid:
        print(f"New game detected (was {progress['appid']}, now {appid}). Resetting progress.")
        progress = dict(DEFAULT_PROGRESS)

    if progress["session_ends_at"] is not None:
        session_start = datetime.fromisoformat(progress["session_ends_at"])
        if datetime.now() < session_start:
            remaining = session_start - datetime.now()
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            print(f"Session can be run after {hours}h {minutes}m (at {session_start:%H:%M}).")
            return
        progress["session_ends_at"] = None
        progress["next_unlock_at"] = datetime.now().isoformat()

    start_from = progress["last_completed"] + 1
    if start_from >= len(achievements):
        print("All achievements already completed.")
        cleanup_profile(config_path, progress_path)
        return

    # Cheap, ASF-independent estimate from the config alone, so the user can
    # decide whether to bother connecting at all before we touch ASF.
    if not force:
        session_bounds = _session_bounds(achievements)
        session_index = next(idx for idx, (s, e) in enumerate(session_bounds) if s <= start_from < e)
        _, session_end_i = session_bounds[session_index]
        raw_duration = sum(achievements[i]["delay"] for i in range(start_from, session_end_i))
        answer = input(
            f"Session {session_index + 1}/{len(session_bounds)} "
            f"~{_format_duration(raw_duration)}. Continue? [y/N] "
        ).strip().lower()
        if answer != "y":
            print("Cancelled.")
            return

    # Real unlock state from Steam, independent of the config's ordering, so
    # achievements already unlocked (in any order) never trigger a wait.
    unlocked = send_alist(appid)
    if unlocked is None:
        print(f"ERROR: ArchiSteamFarm isn't reachable at {API_URL}")
        return

    awaiting_first_unlock = progress["last_completed"] == -1
    _, wait_seconds, _ = _estimate_session(
        achievements, start_from, unlocked, progress, awaiting_first_unlock)

    if wait_seconds > 0:
        run_at = datetime.now() + timedelta(seconds=wait_seconds)
        print(f"Session can be run in {_format_duration(wait_seconds)} (at {run_at:%H:%M}).")
    else:
        print("Session can be run now.")

    send_command(f"play {appid}")

    def advance(i, issued_at):
        """Record achievement i as done and schedule (or end) what's next."""
        progress["last_completed"] = i
        progress["appid"] = appid

        next_i = i + 1
        if next_i < len(achievements) and achievements[next_i]["new_session"]:
            gap = achievements[next_i]["delay"]
            progress["next_unlock_at"] = None
            progress["session_ends_at"] = (datetime.now() + timedelta(seconds=gap)).isoformat()
            save_progress(progress_path, progress)
            send_command("resume")
            _schedule_asf_shutdown()
            print(f"Session complete. Next session in {gap // 3600}h {(gap % 3600) // 60}m.")
            return True  # stop the script
        elif next_i < len(achievements):
            progress["next_unlock_at"] = (issued_at + timedelta(seconds=achievements[next_i]["delay"])).isoformat()
        else:
            progress["next_unlock_at"] = None

        save_progress(progress_path, progress)
        return False

    # awaiting_first_unlock (set above): on a brand new profile, the first
    # achievement that isn't already unlocked has no real "previous unlock"
    # to pace a delay from, so it fires immediately instead of waiting out
    # its configured gap.

    i = start_from

    while i < len(achievements):
        ach = achievements[i]

        if unlocked.get(ach["id"], False):
            print(f"Already unlocked, skipping: {ach['id']}")
            progress["last_completed"] = i
            progress["appid"] = appid
            save_progress(progress_path, progress)
            i += 1
            continue

        if awaiting_first_unlock:
            remaining_delay = 0
        else:
            remaining_delay = ach["delay"]
            if progress["next_unlock_at"] is not None:
                unlock_at = datetime.fromisoformat(progress["next_unlock_at"])
                remaining_delay = max(0, int((unlock_at - datetime.now()).total_seconds()))
                progress["next_unlock_at"] = None
        awaiting_first_unlock = False

        if remaining_delay > 0:
            print(f"Waiting {remaining_delay}s before unlocking: {ach['id']}...")
            time.sleep(remaining_delay)

        issued_at = datetime.now()
        status, result = send_aset(appid, ach["id"])

        if status == "unreachable":
            print(f"ERROR: ArchiSteamFarm isn't reachable at {API_URL} — is it running?")
            progress["next_unlock_at"] = datetime.now().isoformat()
            save_progress(progress_path, progress)
            return

        if status == "unknown":
            print(f"ERROR: unexpected response for {ach['id']}: {result}")
            progress["next_unlock_at"] = datetime.now().isoformat()
            save_progress(progress_path, progress)
            return

        if status == "already_unlocked":
            print(f"Already unlocked, skipping: {ach['id']}")
        else:
            print(f"Unlocked: {ach['id']} at [{issued_at:%H:%M:%S}]")

        if advance(i, issued_at):
            return
        i += 1

    send_command("resume")
    _schedule_asf_shutdown()
    print("\nAll achievements unlocked.")
    cleanup_profile(config_path, progress_path)
