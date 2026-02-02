import argparse
from pathlib import Path
from src.core.orchestrator import Orchestrator

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="remote_gdt", description="LLM → JSON → Blender → STL (parametric remote)")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="Generate STL from a params.json file (no LLM needed)")
    g.add_argument("--params", required=True, help="Path to params.json")
    g.add_argument("--out", required=True, help="Output directory")
    g.add_argument("--blender-bin", default=None, help="Path to blender executable (optional)")

    pr = sub.add_parser("prompt", help="Generate params from a text prompt, then generate STL")
    pr.add_argument("text", help="Natural language prompt")
    pr.add_argument("--out", required=True, help="Output directory")
    pr.add_argument("--blender-bin", default=None, help="Path to blender executable (optional)")
    pr.add_argument("--no-llm", action="store_true", help="Disable LLM; use rule-based defaults/parser")

    sv = sub.add_parser("serve", help="Start the web frontend server")
    sv.add_argument("--host", default="127.0.0.1", help="Host to bind")
    sv.add_argument("--port", type=int, default=8000, help="Port to bind")

    return p

def main() -> int:
    args = build_parser().parse_args()
    orch = Orchestrator(blender_bin=args.blender_bin)

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.cmd == "generate":
        orch.run_from_params_file(Path(args.params), out_dir)
        print(f"✅ Generated outputs in: {out_dir}")
        return 0

    if args.cmd == "prompt":
        orch.run_from_prompt(args.text, out_dir, use_llm=(not args.no_llm))
        print(f"✅ Generated outputs in: {out_dir}")
        return 0

    if args.cmd == "serve":
        from src.web.server import main as serve_main
        serve_main(host=args.host, port=args.port)
        return 0

    return 2
