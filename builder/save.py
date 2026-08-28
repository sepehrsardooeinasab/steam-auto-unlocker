import json
import csv
import math

# -------------------------
# Show Durations & Delays
# -------------------------
def format_seconds(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{int(hours)} h {int(minutes)} min {int(secs)} sec"

def rough_duration(seconds):
    seconds = int(seconds)
    if seconds >= 86400: 
        return f"> 1 day"
    elif seconds >= 3600:
        return f"~ {math.ceil(seconds / 3600):02d} hour"
    elif seconds >= 60:
        return f"~ {math.ceil(seconds / 60):02d} min"
    else:
        return f"= {seconds:02d} sec"

# -------------------------
# Save Data
# -------------------------
def save_merged_with_delay_csv(result_with_delay, csvs_folder):
    output_path = csvs_folder / "merged.csv"
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=["ach_name", "ach_id", "unlock_time", "delay (s)"])
        writer.writeheader()
        
        for ach in result_with_delay:
            writer.writerow({
                "ach_name": ach['ach_name'],
                "ach_id": ach['ach_id'],
                "unlock_time": ach['unlock_time'],
                "delay (s)": ach['delay']})
            

def save_sessions_csv(sessions, inter_session_gaps, csvs_folder):
    output_path = csvs_folder / "sessions.csv"
    with open(output_path, "w", newline='', encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["session_index", "ach_name", "ach_id", "unlock_time", "delay (h m s)"])
        writer.writeheader()

        for i, session in enumerate(sessions, 1):
            for ach in session:
                writer.writerow({
                    "session_index": i,
                    "ach_name": ach["ach_name"],
                    "ach_id": ach["ach_id"],
                    "unlock_time": ach["unlock_time"],
                    "delay (h m s)": rough_duration(ach["delay"])})
            
            # Write a row showing the gap after the session
            if i < len(sessions):
                writer.writerow({
                    "delay (h m s)": f"{rough_duration(inter_session_gaps[i - 1])}"})

            #writer.writerow({})  # Blank row between sessions


def save_session_summary_csv(sessions, session_durations, inter_session_gaps, csvs_folder):
    output_path = csvs_folder / "summary_session.csv"
    with open(output_path, "w", newline='', encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["session_index", "session_duration", "gap_from_previous"])
        writer.writeheader()

        for i in range(len(sessions)):
            writer.writerow({
                "session_index": i + 1,
                "session_duration": rough_duration(session_durations[i]),
                "gap_from_previous": rough_duration(inter_session_gaps[i - 1]) if i != 0 else ""})

# def save_sessions_json(sessions, app_id, output_folder):
#     for i, session in enumerate(sessions, 1):
#         output = {
#             "appid": app_id,
#             "achievements": [{
#                     "id": a["ach_id"],
#                     "delay": a["delay"]}
#                 for a in session]}
        
#         with open(output_folder / f"config{i}.json", "w", encoding="utf-8") as f:
#             json.dump(output, f, indent=2)