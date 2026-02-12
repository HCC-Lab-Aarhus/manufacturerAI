"""
OpenSCAD compiler wrapper — runs openscad CLI for syntax checking and STL rendering.
"""

from __future__ import annotations
import subprocess
import shutil
from pathlib import Path


def _find_openscad() -> str | None:
    """Locate the openscad binary."""
    # Try PATH first
    path = shutil.which("openscad")
    if path:
        return path
    # Common Windows locations
    for candidate in [
        r"C:\Program Files\OpenSCAD\openscad.exe",
        r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
    ]:
        if Path(candidate).exists():
            return candidate
    return None


def check_scad(scad_path: Path) -> tuple[bool, str]:
    """
    Syntax-check an OpenSCAD file without rendering.

    Returns (ok, message).
    """
    exe = _find_openscad()
    if not exe:
        return False, "OpenSCAD not found on PATH."

    try:
        result = subprocess.run(
            [exe, "-o", "/dev/null" if not _is_windows() else "NUL", str(scad_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        stderr = result.stderr.strip()
        if result.returncode == 0:
            return True, stderr or "OK"
        return False, stderr or f"OpenSCAD exited with code {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "OpenSCAD timed out (30s)."
    except Exception as e:
        return False, str(e)


def compile_scad(scad_path: Path, stl_path: Path | None = None) -> tuple[bool, str, Path | None]:
    """
    Compile an OpenSCAD file to STL.

    Returns (ok, message, stl_path_or_none).
    """
    exe = _find_openscad()
    if not exe:
        return False, "OpenSCAD not found on PATH.", None

    if stl_path is None:
        stl_path = scad_path.with_suffix(".stl")

    try:
        result = subprocess.run(
            [exe, "-o", str(stl_path), str(scad_path)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        stderr = result.stderr.strip()
        if result.returncode == 0 and stl_path.exists():
            return True, stderr or "OK", stl_path
        return False, stderr or f"OpenSCAD exited with code {result.returncode}", None
    except subprocess.TimeoutExpired:
        return False, "OpenSCAD timed out (600s).", None
    except Exception as e:
        return False, str(e), None


def _is_windows() -> bool:
    import sys
    return sys.platform == "win32"
