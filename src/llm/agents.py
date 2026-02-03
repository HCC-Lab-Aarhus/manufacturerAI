from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json

from src.llm.client import GeminiClient, LLMClient

def project_root() -> Path:
    return Path(__file__).resolve().parents[2]

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

@dataclass
class ParamsFromPrompt:
    use_llm: bool = True

    def _client(self) -> LLMClient:
        if not self.use_llm:
            raise RuntimeError("LLM usage is disabled.")
        return GeminiClient()

    def generate(self, user_prompt: str) -> dict:
        root = project_root()
        library = read_text(root / "library" / "remote_design_rules.md")

        consultant = read_text(root / "prompts" / "consultant.md")
        extractor = read_text(root / "prompts" / "param_extractor.md")
        verifier = read_text(root / "prompts" / "verifier.md")

        client = self._client()

        consultant_system = consultant + "\n\n# --- Library ---\n" + library + "\n\n# Output JSON only."
        design_brief = client.complete_json(system=consultant_system, user=user_prompt)

        extractor_system = extractor + "\n\n# --- Library ---\n" + library + "\n\n# Output JSON only."
        extractor_user = (
            "User prompt:\n" + user_prompt + "\n\nDesignBrief JSON:\n" + json.dumps(design_brief)
        )
        params = client.complete_json(system=extractor_system, user=extractor_user)

        verifier_system = verifier + "\n\n# --- Library ---\n" + library + "\n\n# Output JSON only."
        params_verified = client.complete_json(system=verifier_system, user=json.dumps(params))
        return params_verified
