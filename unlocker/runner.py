import json
import os
import random
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta

from unlocker.api import (
    API_URL,
    api_request_args,
    send_command,
    send_aset,
    send_alist,
    ensure_asf_running,
    notify,
    schedule_asf_kill,
    start_keep_awake,
    stop_keep_awake)
from unlocker.settings import SETTINGS
from unlocker.state import (
    DEFAULT_PROGRESS,
    list_profiles,
    profile_paths,
    load_config,
    load_progress,
    save_progress,
    cleanup_profile)

# See unlocker/settings.py for what each of these is for.
ASF_SHUTDOWN_DELAY = SETTINGS["asf_shutdown_delay"]
WARM_SETTLE_DELAY = SETTINGS["warm_settle_delay"]
RECONNECT_SETTLE_DELAY = SETTINGS["reconnect_settle_delay"]
SIMULTANEOUS_MAX_DELAY = SETTINGS["simultaneous_max_delay"]
JITTER_ENABLED = SETTINGS["jitter_enabled"]
JITTER_MIN_DELAY = SETTINGS["jitter_min_delay"]
JITTER_PERCENT = SETTINGS["jitter_percent"]


def _jitter(seconds):
    """A configured delay with the optional randomness applied: delays
    longer than JITTER_MIN_DELAY move by up to ±JITTER_PERCENT of
    themselves, when jitter is on. Shorter ones (and everything, when it's
    off) come back unchanged."""
    if not JITTER_ENABLED or seconds <= JITTER_MIN_DELAY:
        return seconds
    spread = seconds * JITTER_PERCENT / 100
    return max(0, round(seconds + random.uniform(-spread, spread)))


def _session_bounds(achievements):
    """Splits achievements into (start, end) index ranges, one per session."""
    bounds = []
    start = 0
    for i in range(1, len(achievements) + 1):
        if i == len(achievements) or achievements[i]["new_session"]:
            bounds.append((start, i))
            start = i
    return bounds


class _Signalled(Exception):
    """Raised from a SIGHUP/SIGTERM handler, so the run unwinds through its
    finally (stopping keep-awake) instead of dying on the spot."""
    def __init__(self, signum):
        super().__init__(signal.Signals(signum).name)
        self.signum = signum


def _raise_signalled(signum, frame):
    raise _Signalled(signum)


def _schedule_asf_shutdown(delay_seconds=ASF_SHUTDOWN_DELAY):
    """Detached background timer: after delay_seconds, shuts ArchiSteamFarm
    down entirely — but only if it still looks idle by then, so it isn't
    killed out from under an interleaved session for another game that
    might get started in the meantime."""
    (status_argv, headers), (exit_argv, _) = api_request_args("status"), api_request_args("exit")
    # Headers (which may hold the IPC password) go through the environment,
    # not the -c code below, which is visible in `ps`.
    child_code = f"""
import json, os, subprocess, time

def request(argv):
    p = subprocess.run(argv, input=os.environ["UNLOCKER_API_HEADERS"],
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
if "not farming anything" in cmd({status_argv!r}).lower():
    cmd({exit_argv!r})
"""
    subprocess.Popen(
        [sys.executable, "-c", child_code],
        env={**os.environ, "UNLOCKER_API_HEADERS": headers},
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
    """Prints a table of every profile's next session: number, length,
    achievement count, and when it can run."""
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
                rows.append((label, "-", "-", "-", "no achievements in config"))
                continue
            status = _session_status(config, load_progress(progress_path))
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
            rows.append((label, "-", "-", "-", f"unreadable ({e.__class__.__name__})"))
            continue

        if status is None:
            rows.append((label, "-", "-", "-", "completed"))
            continue

        number, total, count, duration, wait = status
        when = _format_duration(wait) if wait > 0 else "now"
        rows.append((label, f"{number}/{total}", f"~{_format_duration(duration)}", str(count), when))

    header = ("NAME", "SESSION", "DURATION", "#ACH", "READY IN")
    widths = [max(len(r[c]) for r in [header] + rows) for c in range(len(header))]
    for r in [header, tuple("-" * w for w in widths)] + rows:
        print("  ".join(r[c].ljust(widths[c]) for c in range(len(header))).rstrip())


def run(game_name=None, force=False, time_only=False, delay=None, wait_ready=False):
    """delay: seconds to wait before starting (-in). wait_ready: wait until
    the session can actually run — cooldown over and first unlock due — and
    start then (-w). Either way the wait happens after the confirmation
    prompt, under keep-awake, and before ASF is touched."""
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
        cleanup_profile(game_name)
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


    now = datetime.now()
    # Set when the between-sessions cooldown is still running but -w/-in
    # will wait it out; the cooldown reset below then happens after that wait.
    cooldown_ends = None
    if progress["session_ends_at"] is not None:
        session_start = datetime.fromisoformat(progress["session_ends_at"])
        if now < session_start:
            remaining = session_start - now
            if time_only:
                print(f"{int(remaining.total_seconds())} {est_duration}")
                return
            if not wait_ready and (delay is None or now + timedelta(seconds=delay) < session_start):
                hours, rem = divmod(int(remaining.total_seconds()), 3600)
                minutes = rem // 60
                print(f"Session can be run after {hours}h {minutes}m (at {session_start:%H:%M}).")
                if delay is not None:
                    print(f"-in {_format_duration(delay)} would start before then — use a longer delay, or -w.")
                print(session_line)
                return
            cooldown_ends = session_start
        else:
            progress["session_ends_at"] = None
            progress["next_unlock_at"] = now.isoformat()

    if cooldown_ends is not None:
        # Once the cooldown resets, the first achievement fires immediately.
        ready_at = cooldown_ends
    else:
        _, est_wait, _ = _estimate_session(
            achievements, start_from, progress, progress["last_completed"] == -1)
        if time_only:
            print(f"{est_wait} {est_duration}")
            return
        ready_at = now + timedelta(seconds=est_wait)

    if ready_at > now:
        print(f"Session can be run in {_format_duration((ready_at - now).total_seconds())} "
              f"(at {ready_at:%H:%M}).")
    else:
        print("Session can be run now.")

    if wait_ready:
        start_at = max(ready_at, now)
    elif delay is not None:
        start_at = now + timedelta(seconds=delay)
    else:
        start_at = now
    if start_at > now:
        print(f"Starting in {_format_duration((start_at - now).total_seconds())} (at {start_at:%H:%M}).")

    if JITTER_ENABLED:
        print(f"Randomness on: delays over {_format_duration(JITTER_MIN_DELAY)} "
              f"vary by up to ±{JITTER_PERCENT:g}%.")

    if force:
        print(session_line)
    else:
        answer = input(f"{session_line} Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("Cancelled.")
            return

    # Only touch ASF once the user has actually committed to running —
    # not before, so declining the prompt above never starts it up.
    game_label = game_name or "default"
    # SIGHUP: the terminal window was closed. SIGTERM: runsteamunlocker -k.
    signal.signal(signal.SIGHUP, _raise_signalled)
    signal.signal(signal.SIGTERM, _raise_signalled)

    # Nothing to wait out (e.g. a single-achievement session that fires right
    # away) means nothing to stay awake for — the run is over in seconds.
    awake_for = (max(start_at, ready_at) - now).total_seconds() + est_duration
    keep_awake_proc = start_keep_awake() if awake_for > 0 else None
    if keep_awake_proc is not None:
        print(f"Keeping the system awake for {_format_duration(awake_for)}.")

    try:
        if start_at > datetime.now():
            print(f"Waiting until {start_at:%H:%M} before starting...")
            # Short sleeps against the absolute target, so drift can't make
            # it start late.
            while (left := (start_at - datetime.now()).total_seconds()) > 0:
                time.sleep(min(left, 60))

        if cooldown_ends is not None:
            progress["session_ends_at"] = None
            progress["next_unlock_at"] = datetime.now().isoformat()

        ensure_asf_running()

        # Real unlock state from Steam, independent of the config's ordering, so
        # achievements already unlocked (in any order) never trigger a wait.
        unlocked, reconnected = send_alist(appid)
        if unlocked is None:
            print(f"ERROR: ArchiSteamFarm isn't reachable at {API_URL}")
            notify(game_label, "Stopped: ArchiSteamFarm isn't reachable.", error=True)
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
                gap = _jitter(achievements[next_i]["delay"])
                progress["next_unlock_at"] = None
                progress["session_ends_at"] = (datetime.now() + timedelta(seconds=gap)).isoformat()
                save_progress(progress_path, progress)
                send_command("resume")
                _schedule_asf_shutdown()
                print(f"Session complete. Next session in {gap // 3600}h {(gap % 3600) // 60}m.")
                next_at = datetime.now() + timedelta(seconds=gap)
                notify(game_label, f"Session {session_index + 1}/{len(session_bounds)} done. "
                              f"Next in {_format_duration(gap)} (at {next_at:%a %H:%M}).")
                return True  # stop the script
            elif next_i < len(achievements):
                progress["next_unlock_at"] = (issued_at + timedelta(seconds=_jitter(achievements[next_i]["delay"]))).isoformat()
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
                remaining_delay = _jitter(ach["delay"])
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
                    notify(game_label, "Stopped mid-session: ArchiSteamFarm isn't reachable.", error=True)
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
                    notify(game_label, f"Stopped mid-session: unexpected response for {label}.", error=True)
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
        notify(game_label, "All achievements unlocked.")
        cleanup_profile(game_name)
    except _Signalled as e:
        if e.signum == signal.SIGHUP:
            notify(game_label, "Stopped: the terminal running it was closed.", error=True)
        else:
            print("\nStopped.")
    except Exception as e:
        notify(game_label, f"Crashed: {e.__class__.__name__}: {e}", error=True)
        raise
    finally:
        stop_keep_awake(keep_awake_proc)
