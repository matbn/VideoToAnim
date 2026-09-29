"""Ponto de entrada: sobe o servidor local com um comando.

Uso:
    python run.py            # http://127.0.0.1:8000
    python run.py --port 9000
    python run.py --reload   # recarrega ao editar o codigo
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="video2mixamo - servidor local")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    import uvicorn

    print(f"video2mixamo em http://{args.host}:{args.port}", flush=True)
    if args.reload:
        uvicorn.run(
            "server.app:app", host=args.host, port=args.port,
            reload=True, log_level=args.log_level,
        )
    else:
        from server.app import app

        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
