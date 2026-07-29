"""Command line interface for py2max-server.

Provides the ``serve`` subcommand, which runs the browser editor and its
Python console.

There was once a separate ``repl`` subcommand and a single-terminal ``--repl``
mode. Both are gone: the console lives in the editor page, which authenticates
with the token already embedded there, so it needs no second terminal and no
token to copy.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from py2max import Patcher

from . import serve_interactive, start_repl_server


def _load_patcher(input_path: Path) -> Patcher:
    """Load a patch bound to its own path, so saving writes back to it.

    ``save_to`` is what sets ``Patcher._path``; without it ``patcher.save()``
    (and therefore both the editor's Save button and the server's auto-save)
    silently does nothing and every edit is lost on exit.
    """
    return Patcher.from_file(input_path, save_to=str(input_path))


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the interactive WebSocket server for a patcher."""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    patcher = _load_patcher(input_path)

    # Opt-in, and the legacy --no-save still forces it off.
    auto_save = bool(args.auto_save) and not args.no_save

    try:
        print(f"Starting server for: {input_path}")
        print(f"HTTP server: http://localhost:{args.port}")
        print(f"WebSocket server: ws://localhost:{args.port + 1}")
        print("Interactive editing enabled - changes sync bidirectionally")
        if auto_save:
            print(f"Auto-save ON: edits are written back to {input_path}")
        else:
            print("Auto-save OFF: press Save in the editor to write changes")
        print("Press Ctrl+C to stop")

        async def run_server():
            server = await serve_interactive(
                patcher,
                args.port,
                not args.no_open,
                auto_save=auto_save,
            )

            # HTTP=port, editor WebSocket=port+1, console=port+2. The editor
            # page opens the console socket itself when you press Console.
            console_port = args.port + 2
            console_server = await start_repl_server(
                patcher, server, port=console_port
            )

            print()
            print("=" * 70)
            print(f"Python console available in the editor (port {console_port})")
            print("Press the Console button in the toolbar")
            print("=" * 70)
            print()

            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping server...")
                console_server.close()
                await console_server.wait_closed()
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="py2max-server",
        description="Interactive browser editor for py2max patches.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser(
        "serve", help="Start the browser editor for a patch"
    )
    serve_parser.add_argument("input", help="Input .maxpat file")
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP server port (default: 8000; WebSocket on port+1, console on port+2)",
    )
    serve_parser.add_argument(
        "--no-open", action="store_true", help="Don't automatically open browser"
    )
    # Off by default: serving a patch should not rewrite it. Edits are saved on
    # request (Save in the editor); opt in here to have them written back
    # automatically a couple of seconds after each change.
    serve_parser.add_argument(
        "--auto-save",
        action="store_true",
        help="Write edits back to the input file automatically (default: off)",
    )
    # Retained (hidden) so existing commands keep working now that it restates
    # the default. Still honoured as a hard off, so `--auto-save --no-save`
    # resolves the contradiction in favour of not touching the file.
    serve_parser.add_argument("--no-save", action="store_true", help=argparse.SUPPRESS)
    serve_parser.set_defaults(func=cmd_serve)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
