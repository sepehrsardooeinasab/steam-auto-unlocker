import time
from datetime import datetime, timedelta

from unlocker.api import send_command, send_aset
from unlocker.state import (
    DEFAULT_PROGRESS,
    profile_paths,
    load_config,
    load_progress,
    save_progress,
    cleanup_profile)


def run(game_name=None):
    config_path, progress_path = profile_paths(game_name)

    config = load_config(config_path)
    achievements = config["achievements"]
    appid = config["appid"]

    progress = load_progress(progress_path)

    is_first_run = False
    if progress["appid"] != 0 and progress["appid"] != appid:
        print(f"New game detected (was {progress['appid']}, now {appid}). Resetting progress.")
        progress = dict(DEFAULT_PROGRESS)
        is_first_run = True
    elif progress["last_completed"] == -1:
        is_first_run = True

    if progress["session_ends_at"] is not None:
        session_start = datetime.fromisoformat(progress["session_ends_at"])
        if datetime.now() < session_start:
            remaining = session_start - datetime.now()
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            print(f"Next session starts in {hours}h {minutes}m. Come back later.")
            return
        progress["session_ends_at"] = None
        progress["next_unlock_at"] = datetime.now().isoformat()

    start_from = progress["last_completed"] + 1
    if start_from >= len(achievements):
        print("All achievements already completed.")
        cleanup_profile(config_path, progress_path)
        return

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
            send_command("reset")
            print(f"Session complete. Next session in {gap // 3600}h {(gap % 3600) // 60}m.")
            return True  # stop the script
        elif next_i < len(achievements):
            progress["next_unlock_at"] = (issued_at + timedelta(seconds=achievements[next_i]["delay"])).isoformat()
        else:
            progress["next_unlock_at"] = None

        save_progress(progress_path, progress)
        return False

    i = start_from

    # First run: catch up on achievements already unlocked (e.g. from before
    # this tool was used) with no delay, stopping at the first one that
    # actually needed to be set.
    if is_first_run:
        while i < len(achievements):
            ach = achievements[i]
            status, result = send_aset(appid, ach["id"])

            if status == "already_unlocked":
                print(f"Already unlocked, skipping: {ach['id']}")
                if advance(i, datetime.now()):
                    return
                i += 1
                continue

            if status == "unknown":
                print(f"ERROR: unexpected response for {ach['id']}: {result}")
                return

            print(f"Unlocked: {ach['id']} at [{datetime.now():%H:%M:%S}]")
            if advance(i, datetime.now()):
                return
            i += 1
            break

        if i >= len(achievements):
            print("All achievements already unlocked.")
            cleanup_profile(config_path, progress_path)
            return

    while i < len(achievements):
        ach = achievements[i]

        remaining_delay = ach["delay"]
        if progress["next_unlock_at"] is not None:
            unlock_at = datetime.fromisoformat(progress["next_unlock_at"])
            remaining_delay = max(0, int((unlock_at - datetime.now()).total_seconds()))
            progress["next_unlock_at"] = None

        if remaining_delay > 0:
            print(f"Waiting {remaining_delay}s before unlocking: {ach['id']}...")
            time.sleep(remaining_delay)

        issued_at = datetime.now()
        status, result = send_aset(appid, ach["id"])

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

    print("\nAll achievements unlocked.")
    cleanup_profile(config_path, progress_path)
