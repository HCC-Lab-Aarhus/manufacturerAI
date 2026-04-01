"""LLM agent package — design, circuit, and setup agents."""

from .core import AgentEvent, DesignAgent, CircuitAgent, SetupAgent
from .tools import DESIGN_TOOLS, CIRCUIT_TOOLS, SETUP_TOOLS
from .prompt import (
    catalog_summary,
    build_design_prompt,
    build_circuit_prompt,
    build_circuit_user_prompt,
    build_setup_prompt,
)
from .messages import serialize_content, sanitize_messages, prune_messages, strip_thinking_blocks
from .config import MODEL, MAX_TOKENS, MAX_TURNS, TOKEN_BUDGET, MODELS, get_model

__all__ = [
    # Core
    "AgentEvent",
    "DesignAgent",
    "CircuitAgent",
    "SetupAgent",
    # Tools
    "DESIGN_TOOLS",
    "CIRCUIT_TOOLS",
    "SETUP_TOOLS",
    # Prompts
    "catalog_summary",
    "build_design_prompt",
    "build_circuit_prompt",
    "build_circuit_user_prompt",
    "build_setup_prompt",
    # Messages
    "serialize_content",
    "sanitize_messages",
    "prune_messages",
    # Config
    "MODEL",
    "MAX_TOKENS",
    "MAX_TURNS",
    "TOKEN_BUDGET",
]
