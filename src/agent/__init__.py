"""Design agent — LLM-driven device designer using Anthropic API."""

from .config import MODEL, MAX_TOKENS, THINKING_BUDGET, MAX_TURNS, TOKEN_BUDGET
from .tools import TOOLS
from .prompt import _build_system_prompt, _catalog_summary
from .messages import _serialize_content, _sanitize_messages, _prune_messages
from .core import DesignAgent, AgentEvent

__all__ = [
    # Config
    "MODEL", "MAX_TOKENS", "THINKING_BUDGET", "MAX_TURNS", "TOKEN_BUDGET",
    # Tools & prompt
    "TOOLS", "_build_system_prompt", "_catalog_summary",
    # Messages
    "_serialize_content", "_sanitize_messages", "_prune_messages",
    # Agent
    "DesignAgent", "AgentEvent",
]
