import os
import sys
import json
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
JSONS_DIR = PROJECT_DIR / "jsons"
CSVS_DIR = PROJECT_DIR / "csvs"
COMPLETED_DIR = PROJECT_DIR / "completed"

DEFAULT_PROGRESS = {
    "appid": 0,
    "last_completed": -1,
    "next_unlock_at": None,
    "session_ends_at": None,
}


def profile_paths(game_name):
    """config/progress paths for a game, sharing the same jsons/ folder."""
    if game_name:
        return (JSONS_DIR / f"config_{game_name}.json",
                JSONS_DIR / f"progress_{game_name}.json")
    return JSONS_DIR / "config.json", JSONS_DIR / "progress.json"


def list_profiles():
    """Game names (None for the bare config.json) for every config*.json in jsons/, with their appid."""
    if not JSONS_DIR.exists():
        return []

    profiles = []
    for path in sorted(JSONS_DIR.glob("config*.json")):
        name = path.stem[len("config_"):] if path.stem.startswith("config_") else None
        try:
            appid = json.loads(path.read_text()).get("appid")
        except (json.JSONDecodeError, OSError):
            appid = None
        profiles.append((name, appid))

    return profiles


def config_problems(config):
    """Everything wrong with a config's shape, as messages (empty when it's
    fine) — checked up front so a bad field can't surface as a KeyError
    halfway through a session, after "play" was already sent."""
    if not isinstance(config, dict):
        return ["the file must be a JSON object"]
    problems = []
    appid = config.get("appid")
    if isinstance(appid, bool) or not isinstance(appid, int) or appid <= 0:
        problems.append(f"appid must be a positive whole number (got {appid!r})")

    achievements = config.get("achievements")
    if not isinstance(achievements, list) or not achievements:
        return problems + ["no achievements found"]

    seen = set()
    for n, ach in enumerate(achievements, 1):
        where = f"achievement #{n}"
        if not isinstance(ach, dict):
            problems.append(f"{where} isn't an object")
            continue
        ach_id, delay, new_session = ach.get("id"), ach.get("delay"), ach.get("new_session")
        if isinstance(ach_id, bool) or not isinstance(ach_id, int) or ach_id <= 0:
            problems.append(f"{where}: id must be a positive whole number (got {ach_id!r})")
        elif ach_id in seen:
            problems.append(f"{where}: id {ach_id} appears more than once")
        else:
            seen.add(ach_id)
        if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
            problems.append(f"{where}: delay must be a whole number of seconds, 0 or more (got {delay!r})")
        if not isinstance(new_session, bool):
            problems.append(f"{where}: new_session must be true or false (got {new_session!r})")
    return problems


def load_config(path):
    if not path.exists():
        print(f"Missing {path}")
        sys.exit(1)

    try:
        config = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"{path.name} isn't valid JSON ({e}).")
        sys.exit(1)
    problems = config_problems(config)
    if problems:
        print(f"{path.name} has problems:")
        for problem in problems[:10]:
            print(f"  - {problem}")
        if len(problems) > 10:
            print(f"  ...and {len(problems) - 10} more.")
        sys.exit(1)

    return config


def load_progress(path):
    if not path.exists():
        return dict(DEFAULT_PROGRESS)
    return {**DEFAULT_PROGRESS, **json.loads(path.read_text())}


def save_progress(path, progress):
    """Writes to a temp file and renames it over the real one, which is
    atomic: a signal or crash mid-write leaves the old file intact instead
    of a truncated one that can't be parsed."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(progress, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def cleanup_profile(game_name):
    """Once a game is finished: moves its config and CSV (the single
    csvs/<name>.csv, or an older export's csvs/<name>/ folder) into
    completed/<name>/, and deletes its progress file. A game finished
    before gets a dated folder instead, so nothing is overwritten."""
    config_path, progress_path = profile_paths(game_name)
    name = game_name or "default"

    dest = COMPLETED_DIR / name
    if dest.exists():
        dest = COMPLETED_DIR / f"{name}_{datetime.now():%Y-%m-%d_%H%M%S}"
    dest.mkdir(parents=True)

    moved = []
    if config_path.exists():
        shutil.move(config_path, dest / config_path.name)
        moved.append("config")
    csvs = [p for p in (CSVS_DIR / f"{name}.csv", CSVS_DIR / name) if p.exists()]
    for csv in csvs:
        shutil.move(csv, dest / csv.name)
    if csvs:
        moved.append("CSV")
    progress_path.unlink(missing_ok=True)

    if moved:
        print(f"Moved {' and '.join(moved)} to {dest.relative_to(PROJECT_DIR)}/.")
    if not csvs:
        print(f"Warning: no CSV found for {name} in csvs/ — nothing to move.")
