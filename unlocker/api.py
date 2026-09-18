import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

API_URL = "http://127.0.0.1:1243/Api/Command"
BOT_CONNECT_TIMEOUT = 60

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASF_DIR = PROJECT_ROOT / "archifarm"
ASF_BINARY = ASF_DIR / "ArchiSteamFarm"
ASF_LOG = ASF_DIR / "log.txt"


def _stop_other_asf_instances():
    """Kills any running ArchiSteamFarm process that isn't this project's
    own. Steam only allows one active login per account, and bot1 here may
    share its account with other ASF installs on this machine — leaving
    one of those running would otherwise make this one hang forever trying
    to connect."""
    pids = subprocess.run(
        ["pgrep", "-f", "ArchiSteamFarm"], capture_output=True, text=True).stdout.split()
    own_binary = str(ASF_BINARY)
    stopped = False
    for pid in pids:
        cmdline = subprocess.run(
            ["ps", "-p", pid, "-o", "command="], capture_output=True, text=True).stdout
        if own_binary not in cmdline:
            subprocess.run(["kill", pid])
            stopped = True
    if stopped:
        print("Stopped another running ArchiSteamFarm instance (same Steam account).")
        time.sleep(3)


def ensure_asf_running():
    """Starts ArchiSteamFarm if it isn't already running, and waits a bit
    for it to come up. Mirrors runsteamunlocker's own bash version, but now
    called from here so it only runs after the user has actually confirmed
    they want to proceed, not unconditionally before asking."""
    _stop_other_asf_instances()

    if subprocess.run(["pgrep", "-f", str(ASF_BINARY)], capture_output=True).returncode == 0:
        return

    print("ArchiSteamFarm isn't running, starting it...")
    with open(ASF_LOG, "a") as log:
        subprocess.Popen(
            [str(ASF_BINARY)], cwd=str(ASF_DIR),
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True)


def start_caffeinate():
    """Starts `caffeinate` to keep the system (and display) from sleeping
    or locking for the duration of a run. No-op if caffeinate isn't
    installed (only macOS ships it) — returns None in that case, and callers
    should skip printing anything about it."""
    if shutil.which("caffeinate") is None:
        return None
    return subprocess.Popen(
        ["caffeinate", "-d", "-i", "-m", "-s"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_caffeinate(proc):
    """Stops a caffeinate process started by start_caffeinate(), if any."""
    if proc is None:
        return
    proc.terminate()
    proc.wait()


def schedule_asf_kill(delay_seconds=60):
    """Detached background timer: force-kills ArchiSteamFarm (same as
    runsteamunlocker's own `pkill -f`) after delay_seconds, regardless of
    whether its API is reachable. Used after an unlock fails outright, so a
    stuck or unreachable bot doesn't stay connected to Steam indefinitely."""
    subprocess.Popen(
        [sys.executable, "-c",
         f"import time, subprocess; time.sleep({delay_seconds}); "
         f"subprocess.run(['pkill', '-f', {str(ASF_BINARY)!r}])"],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def send_command(command):
    """Returns (result, reconnected). result is the response's Result string,
    or None if ASF wasn't reachable. reconnected is True if this call had to
    sit through the retry loop below before getting a real response — i.e.
    Steam only just (re)established the bot's session, whether because ASF
    was just started fresh or because it silently reconnected after a
    mid-session network blip (including one triggered by the retried command
    itself — see the "exception" handling below).

    Retries for a while if: the IPC server itself isn't up yet (e.g. right
    after ASF was just started — it can take longer than any fixed guess to
    finish loading plugins and bind its port); the bot is still connecting to
    Steam; or the command itself came back as a plugin-side exception (this
    has been observed to happen — and to force-disconnect the bot — when a
    command is sent right as the bot finishes connecting, e.g.
    ASFAchievementManager's alist throwing AsyncJobFailedException before the
    Steam session is fully ready). Bailing out on the first attempt instead
    would let that exception text be mistaken for real (e.g. empty-looking)
    command output by callers like send_alist."""
    payload = json.dumps({"Command": command})
    deadline = time.monotonic() + BOT_CONNECT_TIMEOUT
    printed_waiting = False
    reconnected = False

    while True:
        try:
            proc = subprocess.run(
                ["curl", "-s", "-X", "POST", API_URL,
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=30)
            response = json.loads(proc.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            response = None

        result = (response.get("Result") or "") if response is not None else ""
        not_connected = (
            response is None
            or "not connected" in result.lower()
            or "exception" in result.lower())

        if not_connected and time.monotonic() < deadline:
            reconnected = True
            if not printed_waiting:
                if response is None:
                    print("ArchiSteamFarm isn't reachable yet, waiting...")
                elif "exception" in result.lower():
                    print("ArchiSteamFarm returned an error, retrying...")
                else:
                    print("Bot isn't connected to Steam yet, waiting...")
                printed_waiting = True
            time.sleep(2)
            continue

        return (result if response is not None else None), reconnected


def send_aset(appid, ach_id):
    result, _ = send_command(f"aset {appid} {ach_id}")

    if result is None:
        return "unreachable", ""
    if "already unlocked" in result.lower():
        return "already_unlocked", result
    if "success" in result.lower():
        return "success", result
    return "unknown", result


def send_alist(appid):
    """Returns ({achievement_number: is_unlocked}, reconnected), or (None,
    reconnected) if ASF wasn't reachable. Doesn't modify anything, unlike
    aset. See send_command for what reconnected means."""
    result, reconnected = send_command(f"alist {appid}")

    if result is None:
        return None, reconnected

    statuses = {}
    for line in result.splitlines():
        match = re.match(r"\s*(\d+)\s*\[(✅|❌)\]", line)
        if match:
            statuses[int(match.group(1))] = match.group(2) == "✅"

    return statuses, reconnected
