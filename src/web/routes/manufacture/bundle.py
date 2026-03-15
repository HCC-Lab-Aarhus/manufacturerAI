from __future__ import annotations

import io
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from src.web.routes._deps import load_session_or_404

router = APIRouter()


@router.get("/sessions/{sid}/manufacture/bundle")
async def download_bundle(sid: str):
    s = load_session_or_404(sid)
    files = {
        "enclosure_staged.gcode": s.path / "enclosure_staged.gcode",
        "trace_bitmap.txt": s.path / "trace_bitmap.txt",
    }
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        raise HTTPException(404, f"Missing manufacturing files: {', '.join(missing)}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in files.items():
            zf.write(path, name)
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
