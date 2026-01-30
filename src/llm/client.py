from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
import os, json, re
import requests

class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> dict:
        ...

@dataclass
class MockLLMClient:
    """Offline fallback: a tiny heuristic parser that returns a params-like dict."""
    def complete_json(self, system: str, user: str) -> dict:
        text = user.lower()
        out = {
            "remote": {"length_mm": 180, "width_mm": 45, "thickness_mm": 18, "wall_mm": 1.6, "corner_radius_mm": 6},
            "buttons": {"rows": 4, "cols": 3, "diam_mm": 9, "spacing_mm": 3,
                        "margin_top_mm": 20, "margin_bottom_mm": 18, "margin_side_mm": 6,
                        "hole_clearance_mm": 0.25}
        }
        m = re.search(r"(\d+)\s*buttons", text)
        if m:
            out["buttons"]["button_count"] = int(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:long|length)", text)
        if m:
            out["remote"]["length_mm"] = float(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:wide|width)", text)
        if m:
            out["remote"]["width_mm"] = float(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:thick|thickness)", text)
        if m:
            out["remote"]["thickness_mm"] = float(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:button|buttons)", text)
        if m:
            out["buttons"]["diam_mm"] = float(m.group(1))
        return out

@dataclass
class OpenAICompatibleClient:
    """Example client for OpenAI-compatible chat endpoints.

    Configure environment variables:
      - LLM_BASE_URL (e.g. https://api.openai.com/v1)
      - LLM_API_KEY
      - LLM_MODEL

    This is intentionally minimal and may require adaptation to your provider.
    """
    base_url: str = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    api_key: str = os.environ.get("LLM_API_KEY", "")
    model: str = os.environ.get("LLM_MODEL", "")

    def complete_json(self, system: str, user: str) -> dict:
        if not (self.base_url and self.api_key and self.model):
            raise RuntimeError("LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL must be set.")

        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "temperature": 0.2
        }
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise ValueError("Model did not return JSON.")
        return json.loads(match.group(0))
