import json
import subprocess
import sys
import time
from datetime import datetime, timedelta

from unlocker.api import (
    API_URL,
    send_command,
    send_aset,
    send_alist,
    ensure_asf_running,
    schedule_asf_kill,
    start_caffeinate,
    stop_caffeinate)
from unlocker.state import (
    DEFAULT_PROGRESS,
    list_profiles,
    profile_paths,
    load_config,
    load_progress,
    save_progress,
    cleanup_profile)

ASF_SHUTDOWN_DELAY = 300  # 5 minutes
WARM_SETTLE_DELAY = 10  # bot was already connected to Steam throughout
# A 10s buffer after a fresh (re)connect wasn't enough in practice — it
# still produced an epoch/offline-looking achievement timestamp once — so
# this one is generously long rather than re-guessing a slightly bigger
# fixed number.
RECONNECT_SETTLE_DELAY = 60
# Unlocks this close together in the source data count as simultaneous (a
# 1s delay is usually the same event straddling a second boundary), and are
# batched into a single aset. Anything longer gets its own aset.
SIMULTANEOUS_MAX_DELAY = 1


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

def request(c):
    p = subprocess.run(
        ["curl", "-s", "-X", "POST", {API_URL!r},
         "-H", "Content-Type: application/json", "-d", json.dumps({{"Command": c}})],
        capture_output=True, text=True, timeout=30)
    return json.loads(p.stdout).get("Result", "")

def cmd(c, attempts=5, retry_delay=5):
    # A blip (e.g. Steam reconnecting) can make a single check fail;
    # retry the request itself a few times before giving up, so a
    # transient hiccup doesn't get treated as "still busy" and strand
    # ASF (and its Steam session) running forever.
    for attempt in range(attempts):
        try:
            return request(c)
        except Exception:
            if attempt == attempts - 1:
                return ""
            time.sleep(retry_delay)

time.sleep({delay_seconds})
if "not farming anything" in cmd("status").lower():
    cmd("exit")
"""
    subprocess.Popen(
        [sys.executable, "-c", child_code],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _estimate_session(achievements, start_from, progress, first_run):
    """Returns (end_index, wait_seconds, duration_seconds) for the session
    starting at start_from: every achievement up to (not including) the next
    one that starts a new session, or the end of the list.

    Every achievement in the session is waited out in order regardless of
    whether it turns out to already be unlocked — only whether an aset is
    actually sent depends on that — so this is purely positional. wait_seconds
    is how long until achievements[start_from] can fire — mirrors the real
    loop's delay rule for it exactly, including the first-run/just-resumed-
    from-cooldown "fires immediately" cases. duration_seconds is the
    configured delay for every achievement after that one, i.e. the active
    time once the session has actually started."""
    end = start_from + 1
    while end < len(achievements) and not achievements[end]["new_session"]:
        end += 1

    if first_run:
        wait = 0
    elif progress["next_unlock_at"] is not None:
        unlock_at = datetime.fromisoformat(progress["next_unlock_at"])
        wait = max(0, int((unlock_at - datetime.now()).total_seconds()))
    else:
        wait = achievements[start_from]["delay"]

    duration = sum(achievements[i]["delay"] for i in range(start_from + 1, end))

    return end, wait, duration


def _format_duration(seconds):
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{int(seconds)}s"


def _session_status(config, progress):
    """Read-only summary of a profile's next session, for listing: returns
    None when every achievement is done, else (session_number, session_total,
    count, duration_seconds, wait_seconds). Mirrors run()'s wait rules
    without touching ASF or writing progress."""
    achievements = config["achievements"]
    if progress["appid"] != 0 and progress["appid"] != config["appid"]:
        progress = dict(DEFAULT_PROGRESS)

    start_from = progress["last_completed"] + 1
    if start_from >= len(achievements):
        return None

    session_bounds = _session_bounds(achievements)
    session_index = next(idx for idx, (s, e) in enumerate(session_bounds) if s <= start_from < e)
    first_run = progress["last_completed"] == -1
    end, wait, duration = _estimate_session(achievements, start_from, progress, first_run)

    if progress["session_ends_at"] is not None:
        # Past cooldown, run() resets next_unlock_at to now, so it's ready.
        remaining = datetime.fromisoformat(progress["session_ends_at"]) - datetime.now()
        wait = max(0, int(remaining.total_seconds()))

    return session_index + 1, len(session_bounds), end - start_from, duration, wait


def list_status():
    """Prints one line per profile: next session, its size/length, and when it can run."""
    profiles = list_profiles()
    if not profiles:
        print("No configs found in jsons/.")
        return

    rows = []
    for name, _ in profiles:
        config_path, progress_path = profile_paths(name)
        label = name or "(default)"
        try:
            config = json.loads(config_path.read_text())
            if not config.get("achievements"):
                rows.append((label, "no achievements in config", "", ""))
                continue
            status = _session_status(config, load_progress(progress_path))
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
            rows.append((label, f"unreadable ({e.__class__.__name__})", "", ""))
            continue

        if status is None:
            rows.append((label, "all achievements completed", "", ""))
            continue

        number, total, count, duration, wait = status
        if wait > 0:
            run_at = datetime.now() + timedelta(seconds=wait)
            when = f"in {_format_duration(wait)} (at {run_at:%H:%M})"
        else:
            when = "ready now"
        rows.append((
            label,
            f"Session {number}/{total}",
            f"~{_format_duration(duration)} ({count} achievement{'s' if count != 1 else ''})",
            when,
        ))

    # Message-only rows (completed/unreadable) don't count toward column widths.
    full = [r for r in rows if r[3]] or [("", "", "", "")]
    widths = [max(len(r[c]) for r in rows if r in full or c == 0) for c in range(3)]
    for r in rows:
        if r[3]:
            print("  ".join(r[c].ljust(widths[c]) for c in range(3)) + "  " + r[3])
        else:
            print(f"{r[0].ljust(widths[0])}  {r[1]}")


def run(game_name=None, force=False, time_only=False):
    config_path, progress_path = profile_paths(game_name)

    config = load_config(config_path)
    achievements = config["achievements"]
    appid = config["appid"]

    progress = load_progress(progress_path)

    if progress["appid"] != 0 and progress["appid"] != appid:
        if not time_only:
            print(f"New game detected (was {progress['appid']}, now {appid}). Resetting progress.")
        progress = dict(DEFAULT_PROGRESS)

    start_from = progress["last_completed"] + 1
    if start_from >= len(achievements):
        if time_only:
            print("0 0")
            return
        print("All achievements already completed.")
        cleanup_profile(config_path, progress_path)
        return


    # Cheap, ASF-independent estimate from the config alone, so the user can
    # decide whether to bother connecting at all before we touch ASF. The
    # count/duration part doesn't depend on next_unlock_at, so it's safe to
    # compute before the session_ends_at cooldown check/reset below.
    session_bounds = _session_bounds(achievements)
    session_index = next(idx for idx, (s, e) in enumerate(session_bounds) if s <= start_from < e)
    session_end_i, _, est_duration = _estimate_session(
        achievements, start_from, progress, progress["last_completed"] == -1)
    count = session_end_i - start_from
    session_line = (
        f"Session {session_index + 1}/{len(session_bounds)} "
        f"~{_format_duration(est_duration)} "
        f"({count} achievement{'s' if count != 1 else ''})."
    )


    if progress["session_ends_at"] is not None:
        session_start = datetime.fromisoformat(progress["session_ends_at"])
        if datetime.now() < session_start:
            remaining = session_start - datetime.now()
            if time_only:
                print(f"{int(remaining.total_seconds())} {est_duration}")
                return
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            print(f"Session can be run after {hours}h {minutes}m (at {session_start:%H:%M}).")
            print(session_line)
            return
        progress["session_ends_at"] = None
        progress["next_unlock_at"] = datetime.now().isoformat()

    _, est_wait, _ = _estimate_session(
        achievements, start_from, progress, progress["last_completed"] == -1)

    if time_only:
        print(f"{est_wait} {est_duration}")
        return

    if est_wait > 0:
        run_at = datetime.now() + timedelta(seconds=est_wait)
        print(f"Session can be run in {_format_duration(est_wait)} (at {run_at:%H:%M}).")
    else:
        print("Session can be run now.")

    if force:
        print(session_line)
    else:
        answer = input(f"{session_line} Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("Cancelled.")
            return

    # Only touch ASF once the user has actually committed to running —
    # not before, so declining the prompt above never starts it up.
    caffeinate_proc = start_caffeinate()
    if caffeinate_proc is not None:
        print(f"Caffeinate activated for {_format_duration(est_wait + est_duration)}.")

    try:
        ensure_asf_running()

        # Real unlock state from Steam, independent of the config's ordering, so
        # achievements already unlocked (in any order) never trigger a wait.
        unlocked, reconnected = send_alist(appid)
        if unlocked is None:
            print(f"ERROR: ArchiSteamFarm isn't reachable at {API_URL}")
            # ensure_asf_running() may have just started it — don't leave
            # that process orphaned and running for the next session to
            # trip over.
            schedule_asf_kill()
            return

        awaiting_first_unlock = progress["last_completed"] == -1
        _, wait_seconds, _ = _estimate_session(
            achievements, start_from, progress, awaiting_first_unlock)

        if wait_seconds > 0:
            run_at = datetime.now() + timedelta(seconds=wait_seconds)
            print(f"Session can be run in {_format_duration(wait_seconds)} (at {run_at:%H:%M}).")
        else:
            print("Bot is now connected to Steam.")

        send_command(f"play {appid}")
        # Give Steam a moment to actually register the game as running before
        # the first aset — the first achievement of a session otherwise fires
        # with zero delay, right on top of "play", and unlocks with an epoch
        # (offline-looking) timestamp instead of a real one. A bot that just
        # (re)connected (fresh ASF start, or a reconnect after a mid-session
        # network blip) needs longer for Steam to consider the session
        # properly "online" than one that was already connected throughout.
        time.sleep(RECONNECT_SETTLE_DELAY if reconnected else WARM_SETTLE_DELAY)

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
        # achievement has no real "previous unlock" to pace a delay from, so
        # it fires immediately instead of waiting out its configured gap.

        i = start_from

        while i < len(achievements):
            ach = achievements[i]

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

            # Achievements that really unlocked together (delay within
            # SIMULTANEOUS_MAX_DELAY after this one, same session) go out in a
            # single aset, so they share one timestamp instead of drifting a
            # second apart per API call.
            group_end = i + 1
            while (group_end < len(achievements)
                   and not achievements[group_end]["new_session"]
                   and achievements[group_end]["delay"] <= SIMULTANEOUS_MAX_DELAY):
                group_end += 1
            group = achievements[i:group_end]

            # Always wait this achievement's own delay first, then check —
            # so a batch like 1,2,3,4,5,6 with 1,2,5 already unlocked still
            # spends each entry's own configured gap in order, instead of
            # skipping instantly and leaking that entry's delay onto the
            # next one.
            for a in group:
                if unlocked.get(a["id"], False):
                    print(f"Already unlocked, skipping: {a['id']}")
            to_unlock = [a["id"] for a in group if not unlocked.get(a["id"], False)]

            if to_unlock:
                label = ", ".join(str(a) for a in to_unlock)
                status, result = send_aset(appid, to_unlock)

                if status == "unreachable":
                    print(f"ERROR: ArchiSteamFarm isn't reachable at {API_URL} — is it running?")
                    progress["next_unlock_at"] = datetime.now().isoformat()
                    save_progress(progress_path, progress)
                    # "play" was already sent above, so leaving ASF running here
                    # would strand the bot stuck "playing" this game indefinitely.
                    # The API isn't reachable, so there's no graceful way to tell it
                    # to resume/exit — force-kill it directly instead.
                    schedule_asf_kill()
                    return

                if status == "unknown":
                    print(f"ERROR: unexpected response for {label}: {result}")
                    progress["next_unlock_at"] = datetime.now().isoformat()
                    save_progress(progress_path, progress)
                    schedule_asf_kill()
                    return

                if status == "already_unlocked":
                    print(f"Already unlocked, skipping: {label}")
                else:
                    print(f"Unlocked: {label} at [{issued_at:%H:%M:%S}]")

            for gi in range(i, group_end):
                if advance(gi, issued_at):
                    return
            i = group_end

        send_command("resume")
        _schedule_asf_shutdown()
        print("\nAll achievements unlocked.")
        cleanup_profile(config_path, progress_path)
    finally:
        stop_caffeinate(caffeinate_proc)
