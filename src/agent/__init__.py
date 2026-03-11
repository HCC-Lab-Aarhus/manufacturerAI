"""Design agent — LLM-driven device designer using Anthropic API."""

from .config import MODEL, MAX_TOKENS, THINKING_BUDGET, MAX_TURNS, TOKEN_BUDGET
from .tools import CIRCUIT_TOOLS, DESIGN_TOOLS
from .prompt import _build_circuit_prompt, _build_design_prompt, _catalog_summary, build_circuit_user_prompt
from .messages import _serialize_content, _sanitize_messages, _prune_messages
from .core import CircuitAgent, DesignAgent, AgentEvent

__all__ = [
    # Config
    "MODEL", "MAX_TOKENS", "THINKING_BUDGET", "MAX_TURNS", "TOKEN_BUDGET",
    # Tools & prompt
    "CIRCUIT_TOOLS", "DESIGN_TOOLS",
    "_build_circuit_prompt", "_build_design_prompt", "_catalog_summary",
    "build_circuit_user_prompt",
    # Messages
    "_serialize_content", "_sanitize_messages", "_prune_messages",
    # Agents
    "CircuitAgent", "DesignAgent", "AgentEvent",
]
