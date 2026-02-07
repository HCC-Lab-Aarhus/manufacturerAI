import os
from pathlib import Path

def _load_env():
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env"
    if env_file.exists():
        try:
            content = env_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                         os.environ[key] = val
        except Exception:
            pass

_load_env()
bin_path = os.environ.get("BLENDER_BIN")
print(f"Blender Bin: '{bin_path}'")
if bin_path and os.path.exists(bin_path):
    print("EXISTS")
else:
    print("NOT FOUND")