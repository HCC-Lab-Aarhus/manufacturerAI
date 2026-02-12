"""
OpenSCAD compiler wrapper — runs openscad CLI for syntax checking and STL rendering.
"""

from __future__ import annotations
import struct
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


def _parse_stl(data: bytes) -> list[tuple[tuple[float,...], tuple[float,...], tuple[float,...], tuple[float,...]]]:
    """Parse an STL file (binary or ASCII) into a list of triangles.

    Each triangle is (normal, v1, v2, v3) where each is (x, y, z).
    """
    triangles = []

    # Detect ASCII vs binary: ASCII starts with 'solid'
    if data[:5] == b"solid" and b"\n" in data[:256]:
        import re
        text = data.decode("ascii", errors="replace")
        # Match each facet block
        facet_re = re.compile(
            r"facet\s+normal\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+"
            r"outer\s+loop\s+"
            r"vertex\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+"
            r"vertex\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+"
            r"vertex\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+"
            r"endloop\s+endfacet",
            re.IGNORECASE,
        )
        for m in facet_re.finditer(text):
            vals = [float(m.group(i)) for i in range(1, 13)]
            triangles.append((
                tuple(vals[0:3]),
                tuple(vals[3:6]),
                tuple(vals[6:9]),
                tuple(vals[9:12]),
            ))
    else:
        # Binary STL: 80-byte header, 4-byte count, 50 bytes per triangle
        if len(data) < 84:
            return []
        n = struct.unpack_from("<I", data, 80)[0]
        off = 84
        for _ in range(n):
            if off + 50 > len(data):
                break
            vals = struct.unpack_from("<12f", data, off)
            triangles.append((
                tuple(vals[0:3]),
                tuple(vals[3:6]),
                tuple(vals[6:9]),
                tuple(vals[9:12]),
            ))
            off += 50
    return triangles


def merge_stl_files(
    stl_paths: list[tuple[Path, tuple[float, float, float]]],
    output_path: Path,
) -> bool:
    """Merge multiple STL files (binary or ASCII) into one binary STL,
    applying XYZ translation offsets.

    Parameters
    ----------
    stl_paths : list of (path, (dx, dy, dz))
        Each entry is an STL file and a translation offset.
    output_path : Path
        Where to write the merged binary STL.

    Returns True on success, False if any input is missing/invalid.
    """
    all_packed: list[bytes] = []

    for stl_path, (dx, dy, dz) in stl_paths:
        if not stl_path.exists():
            return False
        data = stl_path.read_bytes()
        triangles = _parse_stl(data)
        if not triangles:
            return False
        for normal, v1, v2, v3 in triangles:
            vals = list(normal) + [
                v1[0] + dx, v1[1] + dy, v1[2] + dz,
                v2[0] + dx, v2[1] + dy, v2[2] + dz,
                v3[0] + dx, v3[1] + dy, v3[2] + dz,
            ]
            all_packed.append(struct.pack("<12fH", *vals, 0))

    # Write merged binary STL
    header = b"\x00" * 80
    with open(output_path, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", len(all_packed)))
        for tri in all_packed:
            f.write(tri)
    return True
