"""Session naming — generate a short LLM-derived name from conversation."""

from __future__ import annotations

import anthropic

from src.session import Session


def generate_session_name(sess: Session) -> str | None:
    """Generate a short name for the session from its conversation.
    Returns None on failure.
    """
    conversation = sess.read_artifact("conversation.json")
    if not conversation or not isinstance(conversation, list):
        return None

    # Build a compact summary: only user text and assistant text blocks
    # (skip tool_use, tool_result, thinking blocks)
    summary_parts = []
    for msg in conversation:
        role = msg.get("role", "")
        content = msg.get("content")
        if role == "user" and isinstance(content, str):
            summary_parts.append(f"User: {content}")
        elif role == "assistant" and isinstance(content, list):
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    summary_parts.append(f"Assistant: {block['text']}")

    if not summary_parts:
        return None

    # Join the summary parts into a single string for the model prompt
    summary = "\n".join(summary_parts)

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=30,
            messages=[{
                "role": "user",
                "content": (
                    "Generate a short name (2-4 words, no quotes) for this device design conversation.\n\n"
                    f"{summary}\n\n"
                    "Reply with ONLY the name, nothing else."
                ),
            }],
        )
        name = response.content[0].text.strip().strip('"\'')
        sess.name = name
        sess.save()
        return name
    except Exception:
        return None
