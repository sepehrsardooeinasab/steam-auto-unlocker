from collections import defaultdict

def validate_steamhunters_export(achievements):
    '''
    Validate SteamHunters export:
    - No empty 'ach_name'
    - 'ach_id' is a valid 1-based default-order position
    - No missing 'unlock_time'
    '''
    for ach in achievements:
        assert isinstance(ach.get('ach_name'), str) and ach['ach_name'].strip(), \
            f"Invalid or empty 'ach_name' in SteamHunters entry: {ach}"
        assert isinstance(ach.get('ach_id'), int) and ach['ach_id'] > 0, \
            f"Invalid or missing 'ach_id' in SteamHunters entry: {ach}"
        assert ach.get('unlock_time') is not None, \
            f"Missing 'unlock_time' in SteamHunters entry: {ach}"


def validate_delayed_achievements(achievements):
    '''
    Validate delayed achievements:
    - No empty name, id, unlock_time, or invalid delay
    - Detect and warn about achievements with identical unlock_time
    '''
    unlock_time_map = defaultdict(list)

    for i, ach in enumerate(achievements):
        assert isinstance(ach.get('ach_name'), str) and ach['ach_name'].strip(), \
            f"Invalid or empty 'ach_name' in delayed achievement: {ach}"
        assert isinstance(ach.get('ach_id'), int) and ach['ach_id'] > 0, \
            f"Invalid or missing 'ach_id' in delayed achievement: {ach}"
        assert ach.get('unlock_time') is not None, \
            f"Missing 'unlock_time' in delayed achievement: {ach}"
        assert isinstance(ach.get('delay'), int) and ach['delay'] >= 0, \
            f"Invalid 'delay' in delayed achievement: {ach}"

        if i != 0:  # skip first entry by index
            unlock_time_map[ach['unlock_time']].append(ach['ach_name'])

    # Filter to keep only timestamps with multiple achievements
    simultaneous_unlocks = {t: names for t, names in unlock_time_map.items() if len(names) > 1}

    if simultaneous_unlocks:
        print(f"Warning: {len(simultaneous_unlocks)} timestamps have multiple achievements unlocked together:")
        for time, names in simultaneous_unlocks.items():
            print(f" - {time} ({len(names)} achievements):")
            for name in names:
                print(f"    • {name}")

    print(' ')
    return simultaneous_unlocks


def validate_separated_sessions(sessions, inter_session_gaps, session_durations, min_gap):
    '''
    Validate session splitting:
    - Gaps between sessions must be larger than min_gap (default 2h)
    '''
    FlagZeroSession=False
    for i, gap in enumerate(inter_session_gaps):
        assert gap > min_gap, f"Session gap too short (Session {i+2} starts too soon): {gap} sec"
    for i, duration in enumerate(session_durations):
        if duration<=1:
            print(f"Session {i+1} has zero duration")
            FlagZeroSession=True
    if FlagZeroSession:
        print("")
