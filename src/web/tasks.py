"""Background task registry — runs agents and pipeline steps detached from HTTP connections.

Each task is identified by a composite key (session_id, task_type) where task_type
is one of: "design", "circuit", "placement", "routing", "bitmap", "scad", "compile", "gcode".

Agent tasks accumulate SSE-style events in an in-memory buffer that clients can
subscribe to at any offset, enabling reconnect-and-resume.

Pipeline tasks just track status: "running" | "done" | "error".
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ── Task state ────────────────────────────────────────────────────

@dataclass
class AgentTask:
    """A running or completed agent (design / circuit) background task."""
    status: str = "running"                    # running | done | error
    events: list[dict[str, Any]] = field(default_factory=lambda: [])
    error: str | None = None
    asyncio_task: asyncio.Task[Any] | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    last_save_cursor: int = 0

    def append_event(self, etype: str, data: dict[str, Any]) -> None:
        self.events.append({"type": etype, "data": data})

    def finish(self, status: str = "done", error: str | None = None) -> None:
        self.status = status
        self.error = error


@dataclass
class PipelineTask:
    """A running or completed pipeline step (placement, routing, etc.)."""
    status: str = "running"
    message: str = ""
    error: str | None = None
    result: Any = None
    detail: dict[str, Any] | None = None


# ── Registry ──────────────────────────────────────────────────────

_lock = threading.Lock()
_agent_tasks: dict[tuple[str, str], AgentTask] = {}       # (sid, "design"|"circuit") -> AgentTask
_pipeline_tasks: dict[tuple[str, str], PipelineTask] = {}  # (sid, step) -> PipelineTask


def get_agent_task(sid: str, agent: str) -> AgentTask | None:
    with _lock:
        return _agent_tasks.get((sid, agent))


def set_agent_task(sid: str, agent: str, task: AgentTask) -> None:
    with _lock:
        _agent_tasks[(sid, agent)] = task


def remove_agent_task(sid: str, agent: str) -> None:
    with _lock:
        _agent_tasks.pop((sid, agent), None)


def get_pipeline_task(sid: str, step: str) -> PipelineTask | None:
    with _lock:
        return _pipeline_tasks.get((sid, step))


def set_pipeline_task(sid: str, step: str, task: PipelineTask) -> None:
    with _lock:
        _pipeline_tasks[(sid, step)] = task


def remove_pipeline_task(sid: str, step: str) -> None:
    with _lock:
        _pipeline_tasks.pop((sid, step), None)
