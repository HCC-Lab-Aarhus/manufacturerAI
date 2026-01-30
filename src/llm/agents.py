from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from src.llm.client import MockLLMClient, OpenAICompatibleClient, LLMClient

def project_root() -> Path:
    return Path(__file__).resolve().parents[3]

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

@dataclass
class ParamsFromPrompt:
    use_llm: bool = True

    def _client(self) -> LLMClient:
        if self.use_llm:
            try:
                c = OpenAICompatibleClient()
                # If env vars are missing, OpenAICompatibleClient will error during call.
                return c
            except Exception:
                pass
        return MockLLMClient()

    def generate(self, user_prompt: str) -> dict:
        root = project_root()
        extractor = read_text(root / "prompts" / "param_extractor.md")
        library = read_text(root / "library" / "remote_design_rules.md")
        system = extractor + "\n\n# --- Library ---\n" + library + "\n\n# Output JSON only."

        client = self._client()
        return client.complete_json(system=system, user=user_prompt)
