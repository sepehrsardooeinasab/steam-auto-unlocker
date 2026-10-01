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


def load_config(path):
    if not path.exists():
        print(f"Missing {path}")
        sys.exit(1)

    config = json.loads(path.read_text())
    if not config.get("achievements"):
        print("No achievements found in config.")
        sys.exit(1)

    return config


def load_progress(path):
    if not path.exists():
        return dict(DEFAULT_PROGRESS)
    return {**DEFAULT_PROGRESS, **json.loads(path.read_text())}


def save_progress(path, progress):
    path.write_text(json.dumps(progress, indent=2))


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
