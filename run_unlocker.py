import sys

from unlocker.runner import run
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


if __name__ == "__main__":
    game_name = sys.argv[1] if len(sys.argv) > 1 else choose_game_name()
    run(game_name)
