"""
Pure-Python ASCII G-code → Binary G-code (.bgcode) converter.

Implements the Prusa Binary G-code specification (version 1) so that
the post-processed G-code can be loaded on the Prusa Core One (and
other Prusa printers) **without** compatibility warnings.

Uses CRC32 checksums and no compression — the file is a bit larger
than a fully compressed bgcode but is perfectly valid and accepted
by the firmware.

Specification: https://github.com/prusa3d/libbgcode/blob/main/doc/specifications.md
"""

from __future__ import annotations

import base64
import io
import logging
import re
import struct
import zlib
from pathlib import Path

import numpy as np

log = logging.getLogger("manufacturerAI.gcode.bgcode")

# ── Constants ──────────────────────────────────────────────────────

MAGIC = 0x45444347          # "GCDE" in little-endian
VERSION = 1
CHECKSUM_CRC32 = 1
COMPRESSION_NONE = 0
ENCODING_INI = 0
GCODE_ENCODING_NONE = 0

# Block types
BT_FILE_METADATA = 0
BT_GCODE = 1
BT_SLICER_METADATA = 2
BT_PRINTER_METADATA = 3
BT_PRINT_METADATA = 4
BT_THUMBNAIL = 5

# Thumbnail formats
THUMB_PNG = 0
THUMB_JPG = 1
THUMB_QOI = 2

# Max G-code block size (same as libbgcode: 64 KB * 10)
GCODE_BLOCK_SIZE = 65536 * 10


# ── Keys that go into **printer** metadata ─────────────────────────
#    (everything else in the footer goes to slicer metadata)

_PRINTER_META_KEYS = {
    "printer_model", "filament_type", "filament_abrasive",
    "nozzle_diameter", "nozzle_high_flow",
    "bed_temperature", "brim_width", "fill_density",
    "layer_height", "temperature", "ironing", "support_material",
    "max_layer_z", "extruder_colour",
    "filament used [mm]", "filament used [cm3]", "filament used [g]",
    "filament cost",
    "estimated printing time (normal mode)",
    "estimated printing time (silent mode)",
    "total filament used for wipe tower [g]",
    "objects_info",
}

_PRINT_META_KEYS = {
    "total toolchanges",
    "filament used [mm]", "filament used [cm3]", "filament used [g]",
    "filament cost",
    "total filament used [g]", "total filament cost",
    "total filament used for wipe tower [g]",
    "estimated printing time (normal mode)",
    "estimated printing time (silent mode)",
    "estimated first layer printing time (normal mode)",
    "estimated first layer printing time (silent mode)",
}


# ── Low-level writers ──────────────────────────────────────────────

def _u16(v: int) -> bytes:
    return struct.pack("<H", v)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _crc32(data: bytes) -> bytes:
    """CRC32 of *data*, returned as 4 little-endian bytes."""
    return _u32(zlib.crc32(data) & 0xFFFFFFFF)


def _write_file_header() -> bytes:
    """10-byte file header: magic + version + checksum type."""
    return _u32(MAGIC) + _u32(VERSION) + _u16(CHECKSUM_CRC32)


def _block_header(block_type: int, data_size: int) -> bytes:
    """12-byte block header (no compression).

    Format: type(2) + compression(2) + uncompressed_size(4) + compressed_size(4).
    With no compression, both sizes are identical.
    """
    return (
        _u16(block_type)
        + _u16(COMPRESSION_NONE)
        + _u32(data_size)
        + _u32(data_size)
    )


def _metadata_payload(pairs: list[tuple[str, str]]) -> bytes:
    """INI-encoded metadata: ``key=value\\n`` for each pair."""
    parts: list[str] = []
    for k, v in pairs:
        parts.append(f"{k}={v}\n")
    return "".join(parts).encode("utf-8")


def _write_metadata_block(
    block_type: int,
    pairs: list[tuple[str, str]],
) -> bytes:
    """Full metadata block: header + encoding param + payload + CRC32."""
    encoding_param = _u16(ENCODING_INI)       # 2 bytes
    payload_data = _metadata_payload(pairs)    # variable
    full_payload = encoding_param + payload_data
    data_size = len(full_payload)

    header = _block_header(block_type, data_size)
    checksum = _crc32(header + full_payload)
    return header + full_payload + checksum


def _write_thumbnail_block(
    fmt: int,
    width: int,
    height: int,
    image_data: bytes,
) -> bytes:
    """Full thumbnail block: header + params (format, w, h) + data + CRC32."""
    params = _u16(fmt) + _u16(width) + _u16(height)   # 6 bytes
    full_payload = params + image_data
    data_size = len(full_payload)

    header = _block_header(BT_THUMBNAIL, data_size)
    checksum = _crc32(header + full_payload)
    return header + full_payload + checksum


def _write_gcode_block(raw_text: str) -> bytes:
    """Single G-code block: header + encoding param + data + CRC32."""
    encoding_param = _u16(GCODE_ENCODING_NONE)
    gcode_bytes = raw_text.encode("utf-8")
    full_payload = encoding_param + gcode_bytes
    data_size = len(full_payload)

    header = _block_header(BT_GCODE, data_size)
    checksum = _crc32(header + full_payload)
    return header + full_payload + checksum


# ── ASCII G-code parser ───────────────────────────────────────────

_THUMB_BEGIN_RE = re.compile(
    r"^;\s*thumbnail(?:_(?P<fmt>JPG|QOI))?\s+begin\s+"
    r"(?P<w>\d+)x(?P<h>\d+)\s+(?P<sz>\d+)",
    re.IGNORECASE,
)
_THUMB_END_RE = re.compile(
    r"^;\s*thumbnail(?:_(?:JPG|QOI))?\s+end",
    re.IGNORECASE,
)


def _parse_ascii_gcode(text: str):
    """Parse an ASCII G-code file into components for binarisation.

    Returns
    -------
    dict with keys:
        file_meta   – list[(key, value)]
        printer_meta – list[(key, value)]
        print_meta  – list[(key, value)]
        slicer_meta – list[(key, value)]
        thumbnails  – list[dict(fmt, w, h, data)]
        gcode_lines – list[str]   (lines that are NOT metadata/thumbnails)
    """
    lines = text.splitlines(keepends=True)

    file_meta: list[tuple[str, str]] = []
    printer_meta_map: dict[str, str] = {}
    print_meta_map: dict[str, str] = {}
    slicer_meta: list[tuple[str, str]] = []
    thumbnails: list[dict] = []
    gcode_lines: list[str] = []

    reading_config = False
    reading_thumbnail = False
    thumb_fmt = THUMB_PNG
    thumb_w = 0
    thumb_h = 0
    thumb_b64 = ""

    processed: set[int] = set()

    for idx, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n\r")
        stripped = line.lstrip("; ").strip()

        # ---- Producer (first few lines) ----
        if idx < 5 and not file_meta:
            if "generated by PrusaSlicer" in line:
                # Extract version and timestamp
                m = re.search(r"PrusaSlicer\s+([\d.]+)(?:\s+on\s+(.+))?", line)
                if m:
                    file_meta.append(("Producer", f"PrusaSlicer {m.group(1)}"))
                    if m.group(2):
                        file_meta.append(("Produced on", m.group(2).strip()))
                processed.add(idx)
                continue
            if "prepared by" in line.lower():
                m = re.search(r"prepared by\s+(.+)", line, re.IGNORECASE)
                if m:
                    file_meta.append(("Prepared by", m.group(1).strip()))
                processed.add(idx)
                continue

        # ---- Thumbnail blocks ----
        if not reading_thumbnail:
            tm = _THUMB_BEGIN_RE.match(line)
            if tm:
                fmt_str = (tm.group("fmt") or "PNG").upper()
                thumb_fmt = {"PNG": THUMB_PNG, "JPG": THUMB_JPG, "QOI": THUMB_QOI}.get(fmt_str, THUMB_PNG)
                thumb_w = int(tm.group("w"))
                thumb_h = int(tm.group("h"))
                thumb_b64 = ""
                reading_thumbnail = True
                processed.add(idx)
                continue
        else:
            if _THUMB_END_RE.match(line):
                # Decode the base64 thumbnail data
                try:
                    image_data = base64.b64decode(thumb_b64)
                    thumbnails.append({
                        "fmt": thumb_fmt,
                        "w": thumb_w,
                        "h": thumb_h,
                        "data": image_data,
                    })
                except Exception:
                    log.warning("Failed to decode thumbnail %dx%d", thumb_w, thumb_h)
                reading_thumbnail = False
                processed.add(idx)
                continue
            else:
                # Accumulate base64 data (strip leading "; ")
                b64_line = line.lstrip("; ").strip()
                thumb_b64 += b64_line
                processed.add(idx)
                continue

        # ---- Slicer config section ----
        if not reading_config:
            if stripped == "prusaslicer_config = begin":
                reading_config = True
                processed.add(idx)
                continue
        else:
            if stripped == "prusaslicer_config = end":
                reading_config = False
                processed.add(idx)
                continue
            # Parse key = value
            eq = stripped.find("=")
            if eq > 0:
                key = stripped[:eq].strip()
                value = stripped[eq + 1:].strip()
                # Categorise
                if key in _PRINTER_META_KEYS:
                    printer_meta_map.setdefault(key, value)
                if key in _PRINT_META_KEYS:
                    print_meta_map.setdefault(key, value)
                slicer_meta.append((key, value))
                processed.add(idx)
                continue

        # ---- Standalone metadata comments (outside config section) ----
        # Lines like "; filament used [mm] = 1234" appear before the config block
        if line.startswith(";") and "=" in line:
            comment_body = line[1:].strip()
            eq = comment_body.find("=")
            if eq > 0:
                key = comment_body[:eq].strip()
                value = comment_body[eq + 1:].strip()
                if key in _PRINTER_META_KEYS:
                    printer_meta_map.setdefault(key, value)
                    processed.add(idx)
                    continue
                if key in _PRINT_META_KEYS:
                    print_meta_map.setdefault(key, value)
                    processed.add(idx)
                    continue

        # ---- Normal G-code line ----
        gcode_lines.append(raw_line)

    # Build ordered metadata lists
    printer_meta = list(printer_meta_map.items())
    print_meta = list(print_meta_map.items())

    return {
        "file_meta": file_meta,
        "printer_meta": printer_meta,
        "print_meta": print_meta,
        "slicer_meta": slicer_meta,
        "thumbnails": thumbnails,
        "gcode_lines": gcode_lines,
    }


# ── STL thumbnail renderer ────────────────────────────────────────

def _render_stl_thumbnail(stl_path: Path, width: int, height: int) -> bytes | None:
    """Render an STL model to a PNG thumbnail using numpy + PIL.

    Produces a simple isometric-style silhouette that is good enough
    for the printer's display.  Returns raw PNG bytes, or *None* on
    failure.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        log.warning("Pillow not available — skipping thumbnail")
        return None

    try:
        data = stl_path.read_bytes()

        # Detect ASCII vs binary STL
        is_ascii = data.lstrip()[:5].lower() == b"solid" and b"facet" in data[:1000]

        if is_ascii:
            import re
            text = data.decode("ascii", errors="replace")
            # Parse vertices from ASCII STL
            vert_re = re.compile(
                r"vertex\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+"
                r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+"
                r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
            )
            normal_re = re.compile(
                r"facet\s+normal\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+"
                r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+"
                r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
            )
            raw_verts = [(float(m[1]), float(m[2]), float(m[3]))
                         for m in vert_re.finditer(text)]
            raw_normals = [(float(m[1]), float(m[2]), float(m[3]))
                           for m in normal_re.finditer(text)]
            n_triangles = len(raw_verts) // 3
            if n_triangles == 0:
                return None
            vertices = np.array(raw_verts, dtype=np.float32)
            normals_z = np.array([n[2] for n in raw_normals], dtype=np.float32)
        else:
            # Binary STL: 80-byte header + 4-byte triangle count + triangles
            if len(data) < 84:
                return None
            n_triangles = struct.unpack_from("<I", data, 80)[0]
            if len(data) < 84 + n_triangles * 50:
                return None

            # Extract all vertices (3 per triangle, each 3 floats)
            vertices = np.zeros((n_triangles * 3, 3), dtype=np.float32)
            normals_z = np.zeros(n_triangles, dtype=np.float32)
            for i in range(n_triangles):
                base = 84 + i * 50
                normals_z[i] = struct.unpack_from("<f", data, base + 8)[0]
                off = base + 12  # skip normal (12 bytes)
                for v in range(3):
                    x, y, z = struct.unpack_from("<fff", data, off + v * 12)
                    vertices[i * 3 + v] = [x, y, z]

        if len(vertices) == 0:
            return None

        # Isometric-ish projection: rotate 30° around X, 45° around Z
        cos45, sin45 = np.cos(np.radians(45)), np.sin(np.radians(45))
        cos30, sin30 = np.cos(np.radians(30)), np.sin(np.radians(30))

        # Rotate around Z axis
        rx = vertices[:, 0] * cos45 - vertices[:, 1] * sin45
        ry = vertices[:, 0] * sin45 + vertices[:, 1] * cos45
        rz = vertices[:, 2]

        # Rotate around X axis (tilt forward)
        px = rx
        py = ry * cos30 - rz * sin30

        # Scale to fit image with margin
        margin = 4
        x_min, x_max = px.min(), px.max()
        y_min, y_max = py.min(), py.max()
        x_range = x_max - x_min or 1.0
        y_range = y_max - y_min or 1.0
        scale = min((width - 2 * margin) / x_range,
                     (height - 2 * margin) / y_range)
        cx = (width - x_range * scale) / 2
        cy = (height - y_range * scale) / 2

        sx = ((px - x_min) * scale + cx).astype(np.int32)
        sy = ((y_max - py) * scale + cy).astype(np.int32)  # flip Y

        # Draw filled triangles
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Simple depth shading
        for i in range(n_triangles):
            i0, i1, i2 = i * 3, i * 3 + 1, i * 3 + 2
            tri = [(int(sx[i0]), int(sy[i0])),
                   (int(sx[i1]), int(sy[i1])),
                   (int(sx[i2]), int(sy[i2]))]

            # Normal-based shading (use precomputed Z component)
            nz = float(normals_z[i]) if i < len(normals_z) else 0.0
            shade = int(140 + 100 * max(0.0, min(1.0, nz)))
            color = (shade, shade, shade, 255)
            draw.polygon(tri, fill=color, outline=None)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    except Exception as exc:
        log.warning("Thumbnail rendering failed: %s", exc)
        return None


# ── Public API ─────────────────────────────────────────────────────

def gcode_to_bgcode(
    gcode_path: Path,
    bgcode_path: Path | None = None,
    *,
    stl_path: Path | None = None,
) -> Path:
    """Convert an ASCII ``.gcode`` file to binary ``.bgcode``.

    Parameters
    ----------
    gcode_path : Path
        Input ASCII G-code file.
    bgcode_path : Path, optional
        Output path.  Defaults to ``gcode_path.with_suffix('.bgcode')``.
    stl_path : Path, optional
        STL model to render thumbnails from.  If provided (and no
        thumbnails exist in the G-code), a 220×124 and a 16×16
        thumbnail are generated from the model.

    Returns
    -------
    Path to the written ``.bgcode`` file.
    """
    gcode_path = Path(gcode_path)
    if bgcode_path is None:
        bgcode_path = gcode_path.with_suffix(".bgcode")
    bgcode_path = Path(bgcode_path)

    log.info("Converting %s → %s", gcode_path.name, bgcode_path.name)

    text = gcode_path.read_text(encoding="utf-8")
    parsed = _parse_ascii_gcode(text)

    # Ensure we have required metadata (printer + print + slicer)
    if not parsed["printer_meta"]:
        log.warning("No printer metadata found — bgcode may be incomplete")
    if not parsed["slicer_meta"]:
        log.warning("No slicer metadata found — bgcode may be incomplete")

    # ── Assemble binary ──
    parts: list[bytes] = []

    # 1. File header
    parts.append(_write_file_header())

    # 2. File metadata block (optional)
    if parsed["file_meta"]:
        parts.append(_write_metadata_block(BT_FILE_METADATA, parsed["file_meta"]))

    # 3. Printer metadata block (required)
    parts.append(_write_metadata_block(BT_PRINTER_METADATA, parsed["printer_meta"]))

    # 4. Thumbnail blocks (optional)
    #    If no thumbnails in the gcode, render from STL if available.
    thumb_list = parsed["thumbnails"]
    if not thumb_list and stl_path and Path(stl_path).exists():
        log.info("Generating thumbnails from %s", Path(stl_path).name)
        for tw, th in [(220, 124), (16, 16)]:
            png_data = _render_stl_thumbnail(Path(stl_path), tw, th)
            if png_data:
                thumb_list.append({"fmt": THUMB_PNG, "w": tw, "h": th, "data": png_data})
                log.info("  Thumbnail %dx%d: %d bytes", tw, th, len(png_data))

    for thumb in thumb_list:
        parts.append(_write_thumbnail_block(
            thumb["fmt"], thumb["w"], thumb["h"], thumb["data"],
        ))

    # 5. Print metadata block (required)
    parts.append(_write_metadata_block(BT_PRINT_METADATA, parsed["print_meta"]))

    # 6. Slicer metadata block (required)
    parts.append(_write_metadata_block(BT_SLICER_METADATA, parsed["slicer_meta"]))

    # 7. G-code blocks (split into ≤ GCODE_BLOCK_SIZE chunks)
    gcode_full = "".join(parsed["gcode_lines"])
    offset = 0
    while offset < len(gcode_full):
        # Find a newline boundary near the block size limit
        end = min(offset + GCODE_BLOCK_SIZE, len(gcode_full))
        if end < len(gcode_full):
            nl = gcode_full.rfind("\n", offset, end)
            if nl > offset:
                end = nl + 1
        chunk = gcode_full[offset:end]
        parts.append(_write_gcode_block(chunk))
        offset = end

    bgcode_path.write_bytes(b"".join(parts))
    size_kb = bgcode_path.stat().st_size / 1024
    log.info("Wrote %s (%.0f KB)", bgcode_path.name, size_kb)
    return bgcode_path
