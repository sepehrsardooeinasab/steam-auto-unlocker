from datetime import datetime
import re

# -----------------------------
# 1. Read Steam Hunters Export
# -----------------------------
# Lines matching any of these patterns are noise that can precede an
# achievement name (e.g. a "N guide(s)" marker). Kept as a tuple so new
# skip-line shapes can be added later without touching the parsing logic.
SKIP_LINE_PATTERNS = (
    re.compile(r"^\d+\s+guides?$", re.IGNORECASE),
)

def _is_skip_line(line):
    return any(p.search(line) for p in SKIP_LINE_PATTERNS)

def _group_into_blocks(lines):
    '''
    Steam Hunters exports separate every line with a single blank line
    (just spacing), but separate one achievement entry from the next with
    a run of 2+ blank lines. Group raw lines into per-entry blocks using
    that signal, dropping the internal single blanks.
    '''
    blocks = []
    current_block = []
    blank_run = 0

    for line in lines:
        if line == '':
            blank_run += 1
            continue

        if blank_run >= 2 and current_block:
            blocks.append(current_block)
            current_block = []
        blank_run = 0
        current_block.append(line)

    if current_block:
        blocks.append(current_block)

    return blocks

def read_steamhunters_export(input_file):
    # Seconds are optional: SteamHunters normally shows "HH:MM" but has
    # been observed to include ":SS" on some exports.
    time_pattern = re.compile(r"\d{1,2} \w{3} '\d{2} @ \d{1,2}:\d{2}(:\d{2})? (am|pm)")
    achievements = []

    with open(input_file, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f]

    # order_index is the achievement's 1-based position in the page's
    # default sort order (counting locked achievements too), which we use
    # in place of a separate SteamDB id.
    for order_index, block in enumerate(_group_into_blocks(lines), start=1):
        idx = 0

        # 1. Skip any leading noise lines (e.g. "1 guide")
        while idx < len(block) and _is_skip_line(block[idx]):
            idx += 1

        if idx >= len(block):
            continue

        # 2. Name is the next line
        ach_name = block[idx]
        idx += 1

        # 3. A date line anywhere in the rest of the block means unlocked;
        #    its absence means the achievement is still locked -> skip it.
        unlock_time = None
        for line in block[idx:]:
            match = time_pattern.search(line)
            if match:
                has_seconds = match.group(1) is not None
                fmt = "%d %b '%y @ %I:%M:%S %p" if has_seconds else "%d %b '%y @ %I:%M %p"
                try:
                    unlock_time = datetime.strptime(line, fmt)
                except ValueError:
                    unlock_time = None
                break

        if unlock_time is not None:
            achievements.append({"ach_name": ach_name, "ach_id": order_index, "unlock_time": unlock_time})

    achievements.sort(key=lambda x: x["unlock_time"])
    return achievements

# -------------------------
# 2. Add Delay Between Unlocks
# -------------------------
def add_achievement_delays(merged_data):
    result_with_delays = []
    previous_time = None

    for entry in merged_data:
        delay = 0 if previous_time is None else int((entry['unlock_time'] - previous_time).total_seconds())
        result_with_delays.append({
            "ach_name": entry['ach_name'],
            "ach_id": entry['ach_id'],
            "unlock_time": entry['unlock_time'],
            "delay": delay})
        previous_time = entry['unlock_time']

    return result_with_delays

# -------------------------
# 3. Split Into Play Sessions
# -------------------------
def split_sessions_by_gaps(result_with_delays, gap_limit=6*3600, cumulative_limit=12*3600):
    sessions = []
    current_session = []
    initial_delays_per_session = []
    session_durations = []
    cumulative_delay = 0

    for achievement in result_with_delays:
        delay = achievement['delay']

        # Split if single gap too large
        if delay > gap_limit and current_session:
            sessions.append(current_session)
            session_durations.append(cumulative_delay)
            current_session = []
            cumulative_delay = 0

        # Split if total session time too long
        elif cumulative_delay + delay > cumulative_limit and current_session:
            sessions.append(current_session)
            session_durations.append(cumulative_delay)
            current_session = []
            cumulative_delay = 0

        # Reset delay to 0 if first in session
        if not current_session:
            achievement = achievement.copy()
            initial_delays_per_session.append(achievement['delay'])
            achievement['delay'] = 0

        current_session.append(achievement)
        cumulative_delay += achievement['delay']

    if current_session:
        sessions.append(current_session)
        session_durations.append(cumulative_delay)

    # Exclude first session's initial delay (always 0)
    gaps_between_sessions = initial_delays_per_session[1:] if len(sessions)>1 else []
    return sessions, gaps_between_sessions, session_durations