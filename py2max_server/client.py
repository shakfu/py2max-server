"""Remote REPL client for py2max.

This module provides a client that connects to a running py2max-server
and provides an interactive REPL in a separate terminal, keeping server
logs separate from the REPL interface.

Usage:
    # Terminal 1: Start server
    $ py2max-server serve my-patch.maxpat

    # Terminal 2: Connect REPL
    $ py2max-server repl localhost:9000

Architecture:
    - Server runs HTTP (8000), WebSocket (8001), and REPL (9000)
    - REPL client connects to port 9000 via WebSocket
    - Commands sent to server for execution
    - Results returned and displayed in client
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

try:
    from websockets.asyncio.client import ClientConnection, connect
except ImportError:
    raise ImportError(
        "websockets package required for REPL client. "
        "Install with: pip install websockets"
    )


class ReplClient:
    """Remote REPL client that connects to py2max-server."""

    def __init__(
        self, host: str = "localhost", port: int = 9000, token: Optional[str] = None
    ):
        self.host = host
        self.port = port
        self.token = token
        self.ws: Optional[ClientConnection] = None
        self.namespace: Dict[str, Any] = {}
        self.connected = False

    async def connect(self):
        """Connect to remote REPL server."""
        uri = f"ws://{self.host}:{self.port}"
        print(f"Connecting to py2max-server at {uri}...")

        try:
            self.ws = await connect(uri)

            # Authenticate before anything else; the server eval/exec's whatever
            # it receives, so it rejects unauthenticated connections up front.
            await self.ws.send(json.dumps({"type": "auth", "token": self.token or ""}))
            auth_response = json.loads(await self.ws.recv())
            if auth_response.get("type") != "auth_success":
                raise ConnectionError(
                    auth_response.get("error", "Authentication failed")
                    + " (pass the server's token with --token)"
                )

            self.connected = True

            # Get initial state from server
            await self.ws.send(json.dumps({"type": "init"}))
            response = await self.ws.recv()
            data = json.loads(response)

            if data.get("type") == "init_response":
                info = data.get("info", {})
                print()
                print("=" * 70)
                print("py2max Remote REPL")
                print("=" * 70)
                print()
                print(f"Connected to: {self.host}:{self.port}")
                print(f"Patcher: {info.get('patcher_path', 'Unknown')}")
                print(f"Objects: {info.get('num_objects', 0)} boxes")
                print(f"Connections: {info.get('num_connections', 0)} lines")
                print()
                print("Available commands:")
                print("  save()         - Save patcher")
                print("  info()         - Show patcher info")
                print("  layout(type)   - Change layout")
                print("  optimize()     - Optimize layout")
                print("  clear()        - Clear all objects")
                print("  help_obj(name) - Show Max object help")
                print()
                print("Use Ctrl+D to exit")
                print("=" * 70)
                print()

        except Exception as e:
            print(f"Error connecting to server: {e}", file=sys.stderr)
            print()
            print("Make sure the server is running with:")
            print("  py2max-server serve <patch.maxpat>")
            print()
            raise

    async def disconnect(self):
        """Disconnect from server."""
        if self.ws:
            await self.ws.close()
            self.connected = False

    async def execute(self, code: str) -> Any:
        """Execute code on remote server.

        Args:
            code: Python code to execute

        Returns:
            Result from server
        """
        if not self.connected or self.ws is None:
            print("Not connected to server", file=sys.stderr)
            return None

        try:
            # Send code to server
            await self.ws.send(
                json.dumps({"type": "eval", "code": code, "mode": "exec"})
            )

            # Get response
            response = await self.ws.recv()
            data = json.loads(response)

            if data.get("type") == "error":
                error = data.get("error", "Unknown error")
                traceback = data.get("traceback", "")
                print(f"Error: {error}", file=sys.stderr)
                if traceback:
                    print(traceback, file=sys.stderr)
                return None

            elif data.get("type") == "result":
                result = data.get("result")
                display = data.get("display")

                # Print display if available
                if display:
                    print(display)

                return result

            else:
                print(f"Unknown response type: {data.get('type')}", file=sys.stderr)
                return None

        except Exception as e:
            print(f"Error executing code: {e}", file=sys.stderr)
            return None

    async def run_repl(self):
        """Run the interactive prompt until the user exits."""
        print("Starting REPL...")
        print()

        try:
            await self._prompt_loop()
        except (EOFError, KeyboardInterrupt):
            print()
            print("Exiting REPL...")

    async def _prompt_loop(self) -> None:
        """Read-eval-print loop; every line is executed on the remote server.

        Uses prompt_toolkit directly for line editing, syntax highlighting and
        history. (ptpython is not used here: its REPL owns evaluation, and this
        client has to route every line over the websocket instead.)
        """
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.lexers import PygmentsLexer
        from pygments.lexers.python import PythonLexer  # type: ignore[import-untyped]

        # Built once, outside the loop. A PromptSession owns the history buffer,
        # so constructing one per prompt (as this used to) handed every keystroke
        # a fresh empty history -- up-arrow recall and `enable_history_search`
        # both silently did nothing for the whole session.
        session: PromptSession[str] = PromptSession(
            message="py2max[remote]>>> ",
            lexer=PygmentsLexer(PythonLexer),
            history=InMemoryHistory(),
            enable_history_search=True,
            complete_while_typing=False,
        )

        while True:
            try:
                code = await session.prompt_async()

                if not code.strip():
                    continue

                # Handle exit
                if code.strip() in ("exit", "exit()", "quit", "quit()"):
                    break

                # Execute on server
                result = await self.execute(code)

                # Display result if not None
                if result is not None:
                    print(result)

            except EOFError:
                break
            except KeyboardInterrupt:
                print()
                continue


async def start_repl_client(
    host: str = "localhost", port: int = 9000, token: Optional[str] = None
):
    """Start remote REPL client.

    Args:
        host: Server hostname
        port: REPL server port (default: 9000)
        token: Session token printed by the server (required for auth)

    Example:
        >>> await start_repl_client("localhost", 9000, token="...")
    """
    client = ReplClient(host, port, token=token)

    try:
        await client.connect()
        await client.run_repl()
    except Exception as e:
        print(f"REPL client error: {e}", file=sys.stderr)
        return 1
    finally:
        await client.disconnect()

    return 0


__all__ = ["ReplClient", "start_repl_client"]
