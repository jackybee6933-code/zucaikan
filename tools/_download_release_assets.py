import subprocess
import urllib.request
from pathlib import Path


ASSETS = {
    "football_analysis.exe": 709693339,
    "football_analysis_setup.exe": 709693341,
    "football_analysis_full.zip": 709693342,
    "updater.exe": 709693343,
}
DEST = Path(r"C:\Users\Administrator\AppData\Local\Temp\fa-v1.15-assets-20261104")
token = subprocess.run(
    ["gh", "auth", "token"], check=True, capture_output=True, text=True
).stdout.strip()
if not token:
    raise RuntimeError("GitHub CLI did not return an authentication token")

for name, asset_id in ASSETS.items():
    request = urllib.request.Request(
        f"https://api.github.com/repos/jackybee6933-code/zucaikan/releases/assets/{asset_id}",
        headers={
            "Accept": "application/octet-stream",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        data = response.read()
    (DEST / name).write_bytes(data)
    print(f"{name}: {len(data)} bytes")
