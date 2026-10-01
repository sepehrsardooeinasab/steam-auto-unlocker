import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unlocker.runner import list_status, run
from unlocker.state import list_profiles


def choose_game_name():
    profiles = list_profiles()
    if not profiles:
        print("No config.json files found in jsons/.")
        sys.exit(1)

    if len(profiles) == 1:
        return profiles[0][0]

    print("Available configs:")
    for i, (name, appid) in enumerate(profiles, 1):
        print(f"  {i}. {name or '(default)'}  [appid {appid}]")

    while True:
        choice = input(f"Choose a config [1-{len(profiles)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(profiles):
            return profiles[int(choice) - 1][0]
        print("Please enter a valid number.")


def parse_delay(text):
    """"30" (minutes), "45m", "2h", "1h30m", "90s" -> seconds, or None if invalid."""
    if text.isdigit():
        return int(text) * 60
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text)
    if not m or not text:
        return None
    h, mins, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mins * 60 + s


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--status" in args:
        list_status()
        sys.exit(0)
    force = "-f" in args or "--force" in args
    time_only = "-t" in args or "--time" in args
    wait_ready = "-w" in args or "--wait" in args

    delay = None
    for flag in ("-in", "--in"):
        if flag in args:
            i = args.index(flag)
            delay = parse_delay(args[i + 1]) if i + 1 < len(args) else None
            if delay is None:
                print("-in needs a time, e.g. 30, 45m, 2h, 1h30m.")
                sys.exit(1)
            del args[i:i + 2]

    if wait_ready and delay is not None:
        print("Use either -w or -in, not both.")
        sys.exit(1)

    positional = [a for a in args
                  if a not in ("-f", "--force", "-t", "--time", "-w", "--wait")]

    game_name = positional[0] if positional else choose_game_name()
    run(game_name, force=force, time_only=time_only, delay=delay, wait_ready=wait_ready)