from pathlib import Path
import shutil
import json

from builder.processor import (
    read_steamhunters_export,
    add_achievement_delays,
    split_sessions_by_gaps)

from builder.save import (
    save_merged_with_delay_csv,
    save_sessions_csv,
    save_session_summary_csv,
    rough_duration)

from builder.validate import (
    validate_steamhunters_export,
    validate_delayed_achievements,
    validate_separated_sessions)

'''
INSTRUCTIONS:
1. Copy the SteamHunters achievements page (default sort order),
   including still-locked achievements -- an achievement's position in
   that listing becomes its 'id'.
2. Make sure to update gap_limit / cumulative_limit / min_gap in
   GAME_CONFIG below if this game needs different session-splitting
   values. You'll be prompted for APP_ID separately every run.
'''
# ---------------------
# Per-Game Config
# ---------------------
# gap_limit: gap between unlocks that counts as a new play session
# cumulative_limit: max length a single session can run before force-splitting
# min_gap: validation threshold, sessions must be split by at least this
GAME_CONFIG = {
    "gap_limit": 2 * 3600,
    "cumulative_limit": 5 * 3600,
    "min_gap": 1 * 3600,
}

# ---------------------
# APP_ID (entered fresh every run)
# ---------------------
def prompt_int(prompt_text):
    while True:
        raw = input(prompt_text).strip()
        try:
            return int(raw)
        except ValueError:
            print("Please enter a whole number.")

APP_ID = prompt_int("APP_ID: ")

GAME_NAME = input("Game name: ").strip().lower()
while not GAME_NAME:
    GAME_NAME = input("Game name: ").strip().lower()

# ---------------------
# File Paths
# ---------------------
input_file_SH = "SH_achievements.txt"

csvs_folder   = Path("csvs") / GAME_NAME
output_folder = Path("jsons")

for folder in [csvs_folder]: #, output_folder
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

# ---------------------
# Load & Process Data
# ---------------------
steamhunters_achievements = read_steamhunters_export(input_file_SH)
result_with_delays        = add_achievement_delays(steamhunters_achievements)

# ---------------------
# Validate & Save CSVs
# ---------------------
validate_steamhunters_export(steamhunters_achievements)
validate_delayed_achievements(result_with_delays)
save_merged_with_delay_csv(result_with_delays, csvs_folder)

# -----------------------------------
# Split into Sessions & Validate
# -----------------------------------
sessions, inter_session_gaps, session_durations = split_sessions_by_gaps(
    result_with_delays, gap_limit=GAME_CONFIG["gap_limit"], cumulative_limit=GAME_CONFIG["cumulative_limit"])

validate_separated_sessions(sessions, inter_session_gaps, session_durations, min_gap=GAME_CONFIG["min_gap"])
save_session_summary_csv(sessions, session_durations, inter_session_gaps, csvs_folder)
save_sessions_csv(sessions, inter_session_gaps, csvs_folder)

# ---------------------
# Build single config.json
# ---------------------
all_achievements = []

for session_idx, session in enumerate(sessions):
    for ach_idx, ach in enumerate(session):
        is_first_in_session = (ach_idx == 0)

        # First achievement of a new session carries the inter-session gap as its delay
        # First achievement of the very first session has no delay
        if session_idx == 0 and is_first_in_session:
            delay = ach.get("delay", 0)
            new_session = False
        elif is_first_in_session:
            delay = inter_session_gaps[session_idx - 1]
            new_session = True
        else:
            delay = ach.get("delay", 0)
            new_session = False

        all_achievements.append({
            "id": ach["ach_id"],
            "delay": delay,
            "new_session": new_session
        })

config = {
    "appid": APP_ID,
    "achievements": all_achievements
}

config_path = output_folder / f"config_{GAME_NAME}.json"
config_path.write_text(json.dumps(config, indent=2))

# ---------------------
# Summary
# ---------------------
print(f"Achievements split into {len(sessions)} sessions")
print(f"Total achievements: {len(all_achievements)}")

min_session_dur = min(session_durations)
max_session_dur = max(session_durations)
min_gap = min(inter_session_gaps) if inter_session_gaps else 0
max_gap = max(inter_session_gaps) if inter_session_gaps else 0

print(f"Session duration ranges from {rough_duration(min_session_dur)} to {rough_duration(max_session_dur)}")
print(f"Inter-session gaps range from {rough_duration(min_gap)} to {rough_duration(max_gap)}")
print(f"Single config.json saved to {config_path}")
