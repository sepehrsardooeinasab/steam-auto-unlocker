import json
import subprocess

API_URL = "http://127.0.0.1:1242/Api/Command"


def send_command(command):
    payload = json.dumps({"Command": command})

    proc = subprocess.run(
        ["curl", "-s", "-X", "POST", API_URL,
         "-H", "Content-Type: application/json",
         "-d", payload],
        capture_output=True, text=True)

    response = json.loads(proc.stdout)
    return response.get("Result", "")


def send_aset(appid, ach_id):
    result = send_command(f"aset {appid} {ach_id}")

    if "already unlocked" in result.lower():
        return "already_unlocked", result
    if "success" in result.lower():
        return "success", result
    return "unknown", result
