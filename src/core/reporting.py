from __future__ import annotations
from pathlib import Path
from src.design.models import RemoteParams

def write_report(out_dir: Path, params: RemoteParams, issues: list[str]) -> None:
    lines = []
    lines.append("# Manufacturing report (MVP)")
    lines.append("")
    lines.append("## Final parameters")
    lines.append("```json")
    lines.append(params.model_dump_json(indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Validation / auto-fixes")
    if issues:
        for it in issues:
            lines.append(f"- {it}")
    else:
        lines.append("- No issues detected.")
    lines.append("")
    lines.append("## Notes")
    lines.append("- This MVP generates a rounded rectangular shell with button holes.")
    lines.append("- Add switch sockets / wiring channels in v2 by extending the Blender generator.")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
