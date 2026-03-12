"""Design agent — LLM-driven device designer using Anthropic API."""

from .config import MODEL, MAX_TOKENS, THINKING_BUDGET, MAX_TURNS, TOKEN_BUDGET
from .tools import CIRCUIT_TOOLS, DESIGN_TOOLS
from .prompt import build_circuit_prompt, build_design_prompt, catalog_summary, build_circuit_user_prompt
from .messages import serialize_content, sanitize_messages, prune_messages
from .core import CircuitAgent, DesignAgent, AgentEvent

__all__ = [
    # Config
    "MODEL", "MAX_TOKENS", "THINKING_BUDGET", "MAX_TURNS", "TOKEN_BUDGET",
    # Tools & prompt
    "CIRCUIT_TOOLS", "DESIGN_TOOLS",
    "build_circuit_prompt", "build_design_prompt", "catalog_summary",
    "build_circuit_user_prompt",
    # Messages
    "serialize_content", "sanitize_messages", "prune_messages",
    # Agents
    "CircuitAgent", "DesignAgent", "AgentEvent",
]
