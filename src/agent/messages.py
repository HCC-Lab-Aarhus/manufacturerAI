"""Message helpers — serialization, sanitization, and pruning."""

from __future__ import annotations


# Fields the API accepts for each content block type
_ALLOWED_FIELDS = {
    "thinking": {"type", "thinking", "signature"},
    "text":     {"type", "text"},
    "tool_use": {"type", "id", "name", "input"},
    "tool_result": {"type", "tool_use_id", "content", "is_error"},
}

# Lookup tools whose results are safe to prune from old turns
_LOOKUP_TOOLS = {"list_components", "get_component"}


def _serialize_content(content: list) -> list[dict]:
    """Convert API response content blocks to serializable dicts.

    The Anthropic SDK returns pydantic model instances with extra fields
    (parsed_output, citations, caller, etc.) that the API rejects on
    re-submission.  We whitelist only the fields the API accepts per
    block type.
    """
    result = []
    for block in content:
        if hasattr(block, "model_dump"):
            d = block.model_dump()
        elif isinstance(block, dict):
            d = block
        else:
            d = {"type": "text", "text": str(block)}

        allowed = _ALLOWED_FIELDS.get(d.get("type"), set())
        if allowed:
            d = {k: v for k, v in d.items() if k in allowed}
        result.append(d)
    return result


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """Clean a saved conversation so every content block only contains
    fields the Anthropic API accepts."""
    clean = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            msg = {**msg, "content": _serialize_content(content)}
        clean.append(msg)
    return clean


def _prune_messages(messages: list[dict], keep_recent_turns: int = 6) -> list[dict]:
    """Shrink the context sent to the API by replacing old informational
    tool results with a stub, without touching the saved history on disk.

    For assistant turns older than `keep_recent_turns`:
    - list_components / get_component tool_result content is replaced
      with "[pruned]" (the pairing id is preserved so the API stays happy)
    - submit_design tool calls + results are always kept verbatim
    - All user text prompts and assistant text / thinking blocks are kept
    """
    assistant_indices = [i for i, m in enumerate(messages) if m["role"] == "assistant"]

    if len(assistant_indices) <= keep_recent_turns:
        return messages

    cutoff_msg_index = assistant_indices[-keep_recent_turns]

    # Collect tool_use ids for lookup tools in OLD turns only
    prunable_ids: set[str] = set()
    for msg in messages[:cutoff_msg_index]:
        if msg["role"] == "assistant" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if (
                    block.get("type") == "tool_use"
                    and block.get("name") in _LOOKUP_TOOLS
                    and "id" in block
                ):
                    prunable_ids.add(block["id"])

    if not prunable_ids:
        return messages

    result = []
    for idx, msg in enumerate(messages):
        if idx >= cutoff_msg_index:
            result.append(msg)
            continue

        content = msg.get("content")
        if msg["role"] == "user" and isinstance(content, list):
            new_content = [
                (
                    {"type": "tool_result", "tool_use_id": b["tool_use_id"], "content": "[pruned]"}
                    if b.get("type") == "tool_result" and b.get("tool_use_id") in prunable_ids
                    else b
                )
                for b in content
            ]
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)

    return result
