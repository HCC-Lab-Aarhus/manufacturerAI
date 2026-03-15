from __future__ import annotations

import io
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from src.web.routes._deps import load_session_or_404
from src.web.routes.manufacture.bitmap import _generate_bitmap_lines

router = APIRouter()


@router.get("/sessions/{sid}/manufacture/bundle")
async def download_bundle(sid: str):
    s = load_session_or_404(sid)
    gcode_path = s.path / "enclosure.gcode"
    if not gcode_path.exists():
        raise HTTPException(404, "Missing manufacturing file: enclosure_staged.gcode")

    bitmap_text = '\n'.join(_generate_bitmap_lines(s))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(gcode_path, "enclosure.gcode")
        zf.writestr("trace_bitmap.txt", bitmap_text)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{s.name or s.id}_bundle.zip"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.get("/sessions/{sid}/manufacture/print-job")
async def download_print_job(sid: str):
    s = load_session_or_404(sid)
    path = s.path / "print_job.json"
    if not path.exists():
        raise HTTPException(404, "print_job.json not found")
    return FileResponse(path, filename="print_job.json", media_type="application/json")
