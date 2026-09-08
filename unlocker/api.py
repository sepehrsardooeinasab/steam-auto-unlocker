import json
import re
import subprocess
import time
from pathlib import Path

API_URL = "http://127.0.0.1:1242/Api/Command"
BOT_CONNECT_TIMEOUT = 60

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASF_DIR = PROJECT_ROOT / "archifarm"
ASF_BINARY = ASF_DIR / "ArchiSteamFarm"
ASF_LOG = ASF_DIR / "log.txt"


def ensure_asf_running():
    """Starts ArchiSteamFarm if it isn't already running, and waits a bit
    for it to come up. Mirrors runsteamunlocker's own bash version, but now
    called from here so it only runs after the user has actually confirmed
    they want to proceed, not unconditionally before asking."""
    if subprocess.run(["pgrep", "-f", str(ASF_BINARY)], capture_output=True).returncode == 0:
        return

    print("ArchiSteamFarm isn't running, starting it...")
    with open(ASF_LOG, "a") as log:
        subprocess.Popen(
            [str(ASF_BINARY)], cwd=str(ASF_DIR),
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True)
    time.sleep(5)


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
