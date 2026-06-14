"""CLI entry: `python -m api <data_dir> --host --port`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from api.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the tg-viewer web UI")
    parser.add_argument("data_dir", help="Directory containing decrypted parsed_data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--account", help="Only load this account-{id} directory")
    args = parser.parse_args()

    if not Path(args.data_dir).exists():
        print(f"ERROR: Data directory not found: {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    print("\n🚀 Starting Telegram Data Web UI (FastAPI)")
    print(f"📂 Data directory: {args.data_dir}")
    if args.account:
        print(f"👤 Account filter: {args.account}")
    print(f"🌐 URL: http://{args.host}:{args.port}")
    print(f"📖 OpenAPI docs: http://{args.host}:{args.port}/docs")
    print("\nPress Ctrl+C to stop\n")

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"WARNING: binding to non-loopback host {args.host!r} — this server has NO "
            "authentication and will expose decrypted Telegram data to anyone on the network.",
            file=sys.stderr,
        )

    app = create_app(args.data_dir, account=args.account)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
