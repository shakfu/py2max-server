"""Command line interface for py2max-server.

Provides the ``serve`` and ``repl`` subcommands (formerly ``py2max serve`` /
``py2max repl``) for the interactive WebSocket editor and remote REPL.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from py2max import Patcher
from py2max.core.common import Rect

from . import serve_interactive, start_background_server_repl, start_repl_server
from .client import start_repl_client


def _coerce_rect(patcher: Patcher) -> None:
    """Normalize a list/tuple patcher rect into a Rect (round-trip from JSON)."""
    rect = getattr(patcher, "rect", None)
    if isinstance(rect, (list, tuple)) and len(rect) == 4:
        patcher.rect = Rect(*rect)


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the interactive WebSocket server for a patcher."""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    patcher = Patcher.from_file(input_path)
    _coerce_rect(patcher)

    try:
        print(f"Starting server for: {input_path}")
        print(f"HTTP server: http://localhost:{args.port}")
        print(f"WebSocket server: ws://localhost:{args.port + 1}")
        print("Interactive editing enabled - changes sync bidirectionally")
        if not args.no_save:
            print(f"Auto-save enabled: changes will be saved to {input_path}")
        if args.repl:
            print("REPL mode enabled - type 'commands()' for help")
        print("Press Ctrl+C to stop")

        async def run_server():
            # Single-terminal mode: background server + inline REPL with log redirect.
            if args.repl and args.log_file:
                log_file_path = Path(args.log_file)
                await start_background_server_repl(
                    patcher, port=args.port, log_file=log_file_path
                )
                return

            # Client-server mode (default).
            server = await serve_interactive(patcher, args.port, not args.no_open)

            repl_port = args.port + 2  # HTTP=port, WS=port+1, REPL=port+2
            repl_server = await start_repl_server(patcher, server, port=repl_port)
            token = getattr(server.handler, "session_token", None)
            connect_cmd = f"py2max-server repl localhost:{repl_port}"
            if token:
                connect_cmd += f" --token {token}"

            print()
            print("=" * 70)
            print("REPL server started")
            print(f"Connect with: {connect_cmd}")
            print("=" * 70)
            print()

            if args.repl:
                print("WARNING: --repl flag without --log-file is deprecated.")
                print("For single-terminal mode, use: --repl --log-file server.log")
                print(
                    "For client-server mode (recommended), in a separate terminal run:"
                )
                print(f"  {connect_cmd}")
                print()

            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping server...")
                repl_server.close()
                await repl_server.wait_closed()
                await server.stop()

        asyncio.run(run_server())
        return 0

    except KeyboardInterrupt:
        print("\nStopping server...")
        return 0
    except Exception as e:
        print(f"Error starting server: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


def cmd_repl(args: argparse.Namespace) -> int:
    """Connect to a remote REPL server."""
    server = args.server
    if ":" in server:
        host, port_str = server.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            print(f"Invalid port number: {port_str}", file=sys.stderr)
            return 1
    else:
        host = server
        port = 9000

    # Resolve the session token (CLI flag takes precedence over env var).
    token = args.token or os.environ.get("PY2MAX_REPL_TOKEN")
    if not token:
        print(
            "Error: a session token is required. Pass --token <token> "
            "(printed by the server) or set PY2MAX_REPL_TOKEN.",
            file=sys.stderr,
        )
        return 1

    try:
        return asyncio.run(start_repl_client(host, port, token=token))
    except KeyboardInterrupt:
        print("\nDisconnected.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="py2max-server",
        description="Interactive WebSocket server and remote REPL for py2max patches.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser(
        "serve", help="Start interactive server with live preview"
    )
    serve_parser.add_argument("input", help="Input .maxpat file")
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP server port (default: 8000, WebSocket on port+1)",
    )
    serve_parser.add_argument(
        "--no-open", action="store_true", help="Don't automatically open browser"
    )
    serve_parser.add_argument(
        "--no-save", action="store_true", help="Disable auto-save on changes"
    )
    serve_parser.add_argument(
        "--repl",
        action="store_true",
        help="Start interactive REPL for live patch editing",
    )
    serve_parser.add_argument(
        "--log-file",
        help="Redirect server logs to file "
        "(enables single-terminal REPL mode when used with --repl)",
    )
    serve_parser.set_defaults(func=cmd_serve)

    repl_parser = subparsers.add_parser("repl", help="Connect to remote REPL server")
    repl_parser.add_argument(
        "server",
        nargs="?",
        default="localhost:9000",
        help="Server address (default: localhost:9000)",
    )
    repl_parser.add_argument(
        "--token",
        default=None,
        help="Session token printed by the server "
        "(or set PY2MAX_REPL_TOKEN). Required for authentication.",
    )
    repl_parser.set_defaults(func=cmd_repl)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
