from __future__ import annotations
import os
import subprocess
from pathlib import Path

class BlenderRunner:
    def __init__(self, blender_bin: str | None = None):
        self.blender_bin = blender_bin or os.environ.get("BLENDER_BIN") or "blender"

    def generate_stls(self, params_path: Path, out_dir: Path) -> None:
        script = Path(__file__).resolve().parent / "generate_blender.py"
        cmd = [self.blender_bin, "-b", "--python", str(script), "--", str(params_path), str(out_dir)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                "Blender generation failed.\n"
                f"Command: {' '.join(cmd)}\n"
                f"STDOUT:\n{proc.stdout}\n"
                f"STDERR:\n{proc.stderr}\n"
            )
