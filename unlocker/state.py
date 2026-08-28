import sys
import json
from pathlib import Path

JSONS_DIR = Path("jsons")

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


def cleanup_profile(config_path, progress_path):
    config_path.unlink(missing_ok=True)
    progress_path.unlink(missing_ok=True)
    print(f"Cleaned up {config_path} and {progress_path}.")
