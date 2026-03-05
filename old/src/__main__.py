"""
ManufacturerAI — entry point.

Usage:
    python -m src serve          # start web server on :8000
    python -m src serve --port 3000
"""

import sys


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "serve"

    if cmd == "serve":
        port = 8000
        host = "127.0.0.1"
        for i, a in enumerate(args):
            if a == "--port" and i + 1 < len(args):
                port = int(args[i + 1])
            elif a == "--host" and i + 1 < len(args):
                host = args[i + 1]

        from src.web.server import main as serve
        serve(host=host, port=port)
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python -m src serve [--port PORT] [--host HOST]")
        sys.exit(1)


if __name__ == "__main__":
    main()
