"""
Session management — each session is a folder on disk holding all pipeline
artifacts (design spec, placement, routing, SCAD, G-code, etc.).

Sessions are identified by a short ID (timestamp-based) and stored under
  outputs/sessions/<session_id>/

A session folder contains:
  session.json   — metadata (created, last_modified, description, pipeline_state)
  design.json    — agent's DesignSpec (once created)
  placement.json — placer output
  routing.json   — router output
  enclosure.scad / enclosure.stl
  manufacturing/ — G-code + ink SVG

This module manages creation, loading, listing, and updating of sessions.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from src.pipeline.config import DEFAULT_PRINTER


ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = ROOT / "outputs" / "sessions"


@dataclass
class Session:
    id: str
    path: Path
    created: str                         # ISO 8601
    last_modified: str                   # ISO 8601
    description: str = ""
    name: str = ""                       # LLM-generated friendly name
    printer_id: str = DEFAULT_PRINTER
    pipeline_state: dict = field(default_factory=dict)  # stage -> status
    pipeline_errors: dict = field(default_factory=dict)  # stage -> {error, reason, responsible_agent}

    def set_step_error(self, step: str, detail: dict) -> None:
        """Persist an error for a pipeline step."""
        self.pipeline_errors[step] = detail
        self.save()

    def clear_step_error(self, step: str) -> None:
        """Remove a persisted error for a pipeline step."""
        self.pipeline_errors.pop(step, None)

    def save(self) -> None:
        """Persist session metadata to session.json."""
        self.last_modified = datetime.now(timezone.utc).isoformat()
        self.path.mkdir(parents=True, exist_ok=True)
        meta = {
            "id": self.id,
            "created": self.created,
            "last_modified": self.last_modified,
            "description": self.description,
            "name": self.name,
            "printer_id": self.printer_id,
            "pipeline_state": self.pipeline_state,
            "pipeline_errors": self.pipeline_errors,
        }
        (self.path / "session.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8")

    def write_artifact(self, filename: str, data: Any) -> Path:
        """Write a JSON artifact to the session folder."""
        p = self.path / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.save()  # update last_modified
        return p

    def read_artifact(self, filename: str) -> Any | None:
        """Read a JSON artifact from the session folder. Returns None if missing."""
        p = self.path / filename
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def has_artifact(self, filename: str) -> bool:
        return (self.path / filename).exists()

    def delete_artifact(self, filename: str) -> bool:
        """Delete a JSON artifact. Returns True if it existed."""
        p = self.path / filename
        if p.exists():
            p.unlink()
            return True
        return False

    @property
    def artifacts(self) -> dict[str, bool]:
        return {
            "catalog": True,
            "design": self.has_artifact("design.json"),
            "circuit": self.has_artifact("circuit.json"),
            "placement": self.has_artifact("placement.json"),
            "routing": self.has_artifact("routing.json"),
            "bitmap": self.pipeline_state.get("bitmap") == "complete",
            "scad": self.has_artifact("enclosure.scad"),
            "compile": (self.path / "enclosure.stl").exists(),
            "gcode": self.has_artifact("enclosure.gcode"),
            "firmware": self.has_artifact("firmware.ino"),
        }

    _PIPELINE_ORDER: ClassVar[list[str]] = ["design", "circuit", "placement", "routing", "scad", "gcode", "firmware"]
    _STAGE_ARTIFACTS: ClassVar[dict[str, list[str]]] = {
        "design": ["design.json", "design_conversation.json"],
        "circuit": ["circuit.json", "circuit_conversation.json"],
        "placement": ["placement.json"],
        "routing": ["routing.json", "routing_debug.json", "trace_bitmap.txt"],
        "scad": ["enclosure.scad", "enclosure.stl"],
        "gcode": ["enclosure.gcode"],
        "firmware": ["firmware.ino"],
    }

    def invalidate_downstream(self, current_step: str) -> list[str]:
        """Delete artifacts and pipeline_state for all stages after *current_step*."""
        idx = self._PIPELINE_ORDER.index(current_step) if current_step in self._PIPELINE_ORDER else -1
        invalidated: list[str] = []
        for later in self._PIPELINE_ORDER[idx + 1:]:
            for artifact in self._STAGE_ARTIFACTS.get(later, [f"{later}.json"]):
                self.delete_artifact(artifact)
            if later in self.pipeline_state:
                del self.pipeline_state[later]
                invalidated.append(later)
            self.pipeline_errors.pop(later, None)
        return invalidated


def _generate_session_id() -> str:
    """Generate a short, unique, human-readable session ID."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def create_session(description: str = "") -> Session:
    """Create a new session with a fresh folder on disk."""
    sid = _generate_session_id()
    path = SESSIONS_DIR / sid

    # Avoid collision (rare but possible if called twice in same second)
    while path.exists():
        time.sleep(0.1)
        sid = _generate_session_id()
        path = SESSIONS_DIR / sid

    now = datetime.now(timezone.utc).isoformat()
    session = Session(
        id=sid,
        path=path,
        created=now,
        last_modified=now,
        description=description,
    )
    session.save()
    return session


def load_session(session_id: str) -> Session | None:
    """Load an existing session by ID. Returns None if not found."""
    path = SESSIONS_DIR / session_id
    meta_path = path / "session.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return Session(
        id=meta["id"],
        path=path,
        created=meta["created"],
        last_modified=meta["last_modified"],
        description=meta.get("description", ""),
        name=meta.get("name", ""),
        printer_id=meta.get("printer_id", DEFAULT_PRINTER),
        pipeline_state=meta.get("pipeline_state", {}),
        pipeline_errors=meta.get("pipeline_errors", {}),
    )


def list_sessions() -> list[dict]:
    """List all sessions, newest first. Returns lightweight metadata dicts."""
    sessions = []
    if not SESSIONS_DIR.exists():
        return sessions
    for d in sorted(SESSIONS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta_path = d / "session.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            sessions.append({
                "id": meta["id"],
                "created": meta["created"],
                "last_modified": meta["last_modified"],
                "description": meta.get("description", ""),
                "name": meta.get("name", ""),
                "printer_id": meta.get("printer_id", DEFAULT_PRINTER),
                "pipeline_state": meta.get("pipeline_state", {}),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return sessions
