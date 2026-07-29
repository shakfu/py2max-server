"""Interactive browser editor for py2max patches.

Serves a Max patch to a browser-based editor that syncs over WebSocket, with a
Python console for evaluating code against the live patcher.

Modules:
- websocket: HTTP + WebSocket server backing the editor
- rpc: console server (code evaluation), on its own port
- repl: command helpers exposed in the console namespace
- cli: the ``py2max-server serve`` entry point
"""

__version__ = "0.3.1"

from .websocket import (
    InteractivePatcherServer,
    InteractiveWebSocketHandler,
    get_patcher_state_json,
    serve_interactive,
)
from .repl import ReplCommands
from .rpc import ReplServer, start_repl_server

__all__ = [
    # WebSocket server
    "InteractivePatcherServer",
    "InteractiveWebSocketHandler",
    "get_patcher_state_json",
    "serve_interactive",
    # Console
    "ReplCommands",
    "ReplServer",
    "start_repl_server",
]
