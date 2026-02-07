from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PrinterStatus:
    connected: bool
    details: str


def _flag_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "printer_connected.flag"


def get_printer_status() -> PrinterStatus:
    env = os.environ.get("PRINTER_CONNECTED", "").strip().lower()
    if env in {"1", "true", "yes"}:
        return PrinterStatus(connected=True, details="Environment override")

    if _flag_path().exists():
        return PrinterStatus(connected=True, details="Flag file present")

    return PrinterStatus(connected=False, details="No printer detected")


def queue_print_job(stl_path: Path) -> str:
    """
    Placeholder print queue: copies the STL into outputs/print_jobs with a timestamp.
    Replace with real printer integration when available.
    """
    root = Path(__file__).resolve().parents[2]
    out_dir = root / "outputs" / "print_jobs"
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = out_dir / f"job_{stamp}_{stl_path.name}"
    target.write_bytes(stl_path.read_bytes())
    return target.stem
