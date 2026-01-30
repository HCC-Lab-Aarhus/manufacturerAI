from __future__ import annotations
import json
from pathlib import Path
from src.design.models import RemoteParams
from src.design.validators import validate_and_fix_params
from src.llm.agents import ParamsFromPrompt
from src.blender.runner import BlenderRunner
from src.core.reporting import write_report

class Orchestrator:
    def __init__(self, blender_bin: str | None = None):
        self.blender = BlenderRunner(blender_bin=blender_bin)

    def run_from_params_file(self, params_path: Path, out_dir: Path) -> None:
        params_raw = json.loads(params_path.read_text(encoding="utf-8"))
        (out_dir / "params_raw.json").write_text(json.dumps(params_raw, indent=2), encoding="utf-8")

        fixed, issues = validate_and_fix_params(params_raw)
        (out_dir / "params_validated.json").write_text(json.dumps(fixed, indent=2), encoding="utf-8")

        params = RemoteParams.model_validate(fixed)
        self.blender.generate_stls(params_path=(out_dir / "params_validated.json"), out_dir=out_dir)

        write_report(out_dir=out_dir, params=params, issues=issues)

    def run_from_prompt(self, text: str, out_dir: Path, use_llm: bool = True) -> None:
        generator = ParamsFromPrompt(use_llm=use_llm)
        params_raw = generator.generate(text)

        (out_dir / "params_raw.json").write_text(json.dumps(params_raw, indent=2), encoding="utf-8")

        fixed, issues = validate_and_fix_params(params_raw)
        (out_dir / "params_validated.json").write_text(json.dumps(fixed, indent=2), encoding="utf-8")

        params = RemoteParams.model_validate(fixed)
        self.blender.generate_stls(params_path=(out_dir / "params_validated.json"), out_dir=out_dir)

        write_report(out_dir=out_dir, params=params, issues=issues)
