from fastapi import APIRouter

from src.web.routes.sessions import router as _sessions
from src.web.routes.catalog import router as _catalog
from src.web.routes.design import router as _design
from src.web.routes.circuit import router as _circuit
from src.web.routes.manufacture import router as _manufacture
from src.web.routes.setup import router as _setup
from src.web.routes.debug import router as _debug

api_router = APIRouter(prefix="/api/v2")
api_router.include_router(_sessions)
api_router.include_router(_catalog)
api_router.include_router(_design)
api_router.include_router(_circuit)
api_router.include_router(_manufacture)
api_router.include_router(_setup)
api_router.include_router(_debug)

__all__ = ["api_router"]
