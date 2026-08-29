import json
import subprocess

API_URL = "http://127.0.0.1:1242/Api/Command"


def send_command(command):
    """Returns the response's Result string, or None if ASF wasn't reachable."""
    payload = json.dumps({"Command": command})

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

    return response.get("Result", "")


def send_aset(appid, ach_id):
    result = send_command(f"aset {appid} {ach_id}")

    if result is None:
        return "unreachable", ""
    if "already unlocked" in result.lower():
        return "already_unlocked", result
    if "success" in result.lower():
        return "success", result
    return "unknown", result
