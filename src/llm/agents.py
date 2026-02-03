from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json

from src.llm.client import MockLLMClient, OpenAICompatibleClient, GeminiClient, LLMClient

def project_root() -> Path:
    return Path(__file__).resolve().parents[2]

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

@dataclass
class ParamsFromPrompt:
    use_llm: bool = True

    def _client(self, stage: str = "legacy") -> LLMClient:
        if self.use_llm:
            try:
                c = GeminiClient()
                c.current_stage = stage  # Set stage for usage tracking
                return c
            except Exception:
                pass
        return MockLLMClient()

    def generate(self, user_prompt: str) -> dict:
        root = project_root()
        library = read_text(root / "library" / "remote_design_rules.md")

        consultant = read_text(root / "prompts" / "consultant.md")
        extractor = read_text(root / "prompts" / "param_extractor.md")
        verifier = read_text(root / "prompts" / "verifier.md")

        # Each step gets its own client with appropriate stage name
        consultant_client = self._client(stage="legacy_consultant")
        consultant_system = consultant + "\n\n# --- Library ---\n" + library + "\n\n# Output JSON only."
        design_brief = consultant_client.complete_json(system=consultant_system, user=user_prompt)

        extractor_client = self._client(stage="legacy_extractor")
        extractor_system = extractor + "\n\n# --- Library ---\n" + library + "\n\n# Output JSON only."
        extractor_user = (
            "User prompt:\n" + user_prompt + "\n\nDesignBrief JSON:\n" + json.dumps(design_brief)
        )
        params = extractor_client.complete_json(system=extractor_system, user=extractor_user)

        verifier_client = self._client(stage="legacy_verifier")
        verifier_system = verifier + "\n\n# --- Library ---\n" + library + "\n\n# Output JSON only."
        params_verified = verifier_client.complete_json(system=verifier_system, user=json.dumps(params))
        return params_verified

