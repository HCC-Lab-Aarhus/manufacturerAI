"""
Web server -- lightweight FastAPI app that dispatches pipeline stages.

Run:  python -m uvicorn src.web.server:app --reload --port 8000
  or: python -m src.web.server
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.web.routes import api_router


def _load_env():
    root = Path(__file__).resolve().parents[2]
    for name in (".env", ".env.local"):
        p = root / name
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v

_load_env()

import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)-5s %(name)s: %(message)s",
)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)

app = FastAPI(title="ManufacturerAI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn
    print("Starting ManufacturerAI server on http://localhost:8000")
    uvicorn.run("src.web.server:app", host="127.0.0.1", port=8000, reload=True)

