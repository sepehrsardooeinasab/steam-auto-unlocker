import json
import re
import subprocess
import time

API_URL = "http://127.0.0.1:1242/Api/Command"
BOT_CONNECT_TIMEOUT = 60


def send_command(command):
    """Returns the response's Result string, or None if ASF wasn't reachable.

    Retries for a while if the bot is still connecting to Steam (e.g. right
    after ASF was just started), instead of failing on the first attempt."""
    payload = json.dumps({"Command": command})
    deadline = time.monotonic() + BOT_CONNECT_TIMEOUT
    printed_waiting = False

    while True:
        try:
            proc = subprocess.run(
                ["curl", "-s", "-X", "POST", API_URL,
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return None

        try:
            response = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None

        result = response.get("Result", "")

        if "not connected" in result.lower() and time.monotonic() < deadline:
            if not printed_waiting:
                print("Bot isn't connected to Steam yet, waiting...")
                printed_waiting = True
            time.sleep(2)
            continue

        return result


def send_aset(appid, ach_id):
    result = send_command(f"aset {appid} {ach_id}")

    if result is None:
        return "unreachable", ""
    if "already unlocked" in result.lower():
        return "already_unlocked", result
    if "success" in result.lower():
        return "success", result
    return "unknown", result


def send_alist(appid):
    """Returns {achievement_number: is_unlocked} for every achievement ASF knows
    about, or None if ASF wasn't reachable. Doesn't modify anything, unlike aset."""
    result = send_command(f"alist {appid}")

    if result is None:
        return None

    statuses = {}
    for line in result.splitlines():
        match = re.match(r"\s*(\d+)\s*\[(✅|❌)\]", line)
        if match:
            statuses[int(match.group(1))] = match.group(2) == "✅"

    return statuses
