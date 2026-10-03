import csv
import json

from unlocker.state import (
    COMPLETED_DIR,
    CSVS_DIR,
    DEFAULT_PROGRESS,
    PROJECT_DIR,
    load_progress,
    profile_paths)


def _format_span(seconds):
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def _find_files(name):
    """(config_path, csv_path or None, progress_path or None, finished) for a
    game — from jsons/ + csvs/ while it's in progress, or from its most
    recent completed/<name>[_<date>]/ folder once it's finished."""
    config_path, progress_path = profile_paths(name)
    if config_path.exists():
        csv_path = next((p for p in (CSVS_DIR / f"{name}.csv", CSVS_DIR / name / "merged.csv")
                         if p.exists()), None)
        return config_path, csv_path, progress_path, False

    dirs = [d for d in (*COMPLETED_DIR.glob(name), *COMPLETED_DIR.glob(f"{name}_*"))
            if (d / config_path.name).exists()]
    if not dirs:
        return None
    d = max(dirs, key=lambda p: p.stat().st_mtime)
    csv_path = next((p for p in (d / f"{name}.csv", d / name / "merged.csv") if p.exists()), None)
    return d / config_path.name, csv_path, None, True


def _load_names(csv_path):
    """Achievement id (as a string) -> name, from either the single per-game
    CSV or an older export's merged.csv."""
    if csv_path is None:
        return {}
    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return {}
    header = rows[0]
    if "ach_id" in header:  # merged.csv
        id_col, name_col = header.index("ach_id"), header.index("ach_name")
    else:  # session,#,achievement,id,... — label rows have no id
        id_col, name_col = 3, 2
    return {r[id_col]: r[name_col] for r in rows[1:] if len(r) > id_col and r[id_col]}


def _sessions(achievements):
    """Config achievements -> list of sessions, each a dict with its config
    indices, gap before it and active duration."""
    sessions = []
    for i, ach in enumerate(achievements):
        if not sessions or ach["new_session"]:
            sessions.append({"indices": [], "gap": ach["delay"] if sessions else 0, "duration": 0})
        else:
            sessions[-1]["duration"] += ach["delay"]
        sessions[-1]["indices"].append(i)
    return sessions


def show_timeline(name, which=None):
    """Prints a game's achievements session by session, with names from its
    CSV and done/next status from its progress. which: None shows the
    current session onward, "all" every session, a number just that one."""
    found = _find_files(name)
    if found is None:
        print(f"No config found for {name} in jsons/ or completed/.")
        return
    config_path, csv_path, progress_path, finished = found

    config = json.loads(config_path.read_text())
    achievements = config["achievements"]
    names = _load_names(csv_path)
    sessions = _sessions(achievements)

    if finished:
        last_completed = len(achievements) - 1
    else:
        progress = load_progress(progress_path)
        if progress["appid"] != 0 and progress["appid"] != config["appid"]:
            progress = dict(DEFAULT_PROGRESS)
        last_completed = progress["last_completed"]
    next_i = last_completed + 1
    current = next((si for si, s in enumerate(sessions) if next_i in s["indices"]), len(sessions) - 1)

    if which is None:
        shown = range(current, len(sessions))
    elif which == "all":
        shown = range(len(sessions))
    else:
        n = int(which)
        if not 1 <= n <= len(sessions):
            print(f"{name} has sessions 1-{len(sessions)}.")
            return
        shown = range(n - 1, n)

    where = f"from {csv_path.relative_to(PROJECT_DIR)}" if csv_path else "no CSV found — names unknown"
    state = "finished" if finished else f"session {current + 1}/{len(sessions)}"
    source = f"  ·  copied from {config['copied_from']}" if config.get("copied_from") else ""
    print(f"{name}  ·  {len(achievements)} achievements  ·  {state}{source}  ·  {where}")

    # One table of achievements. A session's first achievement shows the
    # gap before that session in DELAY/GAP (it has no delay of its own) and
    # the session's length in SESSION_LEN, left empty on the other rows.
    header = ("SESSION", "SESSION_LEN", "#", "ACHIEVEMENT", "ID", "DELAY/GAP", "STATUS")
    rows = []
    for si in shown:
        for pos, i in enumerate(sessions[si]["indices"], 1):
            ach = achievements[i]
            wait = sessions[si]["gap"] if pos == 1 and si > 0 else ach["delay"]
            status = "✓ done" if i <= last_completed else ("▶ next" if i == next_i else "")
            rows.append((str(si + 1), _format_span(sessions[si]["duration"]) if pos == 1 else "", str(pos),
                         names.get(str(ach["id"]), "?"), str(ach["id"]), _format_span(wait), status))

    widths = [max(len(r[c]) for r in [header] + rows) for c in range(len(header))]
    print()
    for r in [header] + rows:
        print("  " + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
