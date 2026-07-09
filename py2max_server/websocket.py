"""Interactive WebSocket server for py2max with bidirectional communication.

This module provides a WebSocket-based server for real-time interactive
editing of Max patches in the browser.

Features:
    - Bidirectional WebSocket communication
    - Real-time updates (Python → Browser)
    - Interactive editing (Browser → Python)
    - Drag-and-drop object repositioning
    - Connection drawing
    - Object creation from browser
    - Token-based authentication for WebSocket connections

Example:
    >>> from py2max import Patcher
    >>> p = Patcher('demo.maxpat')
    >>> await p.serve_interactive()  # Opens browser with interactive editor
    >>> # Edit in browser - changes sync back to Python!
"""

from __future__ import annotations

import asyncio
import http.server
import json
import secrets
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Set

try:
    import websockets
    from websockets.asyncio.server import Server, ServerConnection, serve
except ImportError:
    raise ImportError(
        "websockets package required for interactive server. "
        "Install with: pip install websockets"
    )

if TYPE_CHECKING:
    from py2max.core import Patcher


# Message validation schemas
# Each schema defines required fields and their expected types
# Values can be a single type or a tuple of types for isinstance() checks
MESSAGE_SCHEMAS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "update_position": {"box_id": str, "x": (int, float), "y": (int, float)},
    "create_object": {"text": str},  # x, y optional
    "create_connection": {
        "src_id": str,
        "dst_id": str,
    },  # src_outlet, dst_inlet optional
    "delete_object": {"box_id": str},
    "delete_connection": {"src_id": str, "dst_id": str},
    "save": {},  # No required fields
    "save_as": {"filepath": str},
    "open": {"filepath": str},  # Open a file by server-side path
    "open_content": {"filename": str, "content": str},  # Open uploaded file text
    "list_patches": {},  # List patch files/dirs for the server-side file picker
    "export_patch": {},  # Request serialized patch text for client-side Save As
    "edit_object_text": {"box_id": str, "text": str},  # Rename/edit an object
    "navigate_to_subpatcher": {"box_id": str},
    "navigate_to_parent": {},
    "navigate_to_root": {},
    "navigate_up": {"levels": int},  # Ascend N parents in one step (breadcrumb)
    "undo": {},
    "redo": {},
    "update_positions": {"positions": list},  # Batch move (multi-select group drag)
    "delete_objects": {"box_ids": list},  # Batch delete (multi-select)
}

# Maximum lengths for string fields to prevent abuse
MAX_STRING_LENGTHS: dict[str, int] = {
    "box_id": 256,
    "src_id": 256,
    "dst_id": 256,
    "text": 10000,  # Max object text length
    "filepath": 4096,  # Max filepath length
    "filename": 4096,  # Max uploaded filename length
}

# Maximum size for an uploaded patch payload (bytes/chars of JSON text).
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# File extensions the server-side file picker treats as openable patches.
PATCH_EXTENSIONS = (".maxpat", ".maxhelp", ".amxd", ".rbnopat")

# Coordinate bounds
COORDINATE_BOUNDS = {"min": -100000, "max": 100000}


class ValidationError(Exception):
    """Raised when message validation fails."""

    pass


def _coerce_patcher_rect(patcher: Any) -> None:
    """Normalize a list/tuple patcher rect into a Rect (mirrors CLI loading)."""
    rect = getattr(patcher, "rect", None)
    if isinstance(rect, (list, tuple)) and len(rect) == 4:
        from py2max.core.common import Rect

        patcher.rect = Rect(*rect)


# maxclasses that are not Max objects (message/comment/UI containers) -> no maxref.
_NO_MAXREF_CLASSES = {"message", "comment"}
# Placeholder port types the maxref parser leaves un-substituted; not useful labels.
_PLACEHOLDER_TYPES = {"INLET_TYPE", "OUTLET_TYPE", ""}
# Cache maxref lookups by object name (values: {"inlets": [...], "outlets": [...]}
# or None when the object has no usable maxref entry).
_PORT_LABEL_CACHE: dict[str, Optional[dict]] = {}


def _label_from_entry(entry: dict) -> str:
    """Best human label for one inlet/outlet: prefer the digest, else the type."""
    if not isinstance(entry, dict):
        return ""
    digest = (entry.get("digest") or "").strip()
    if digest:
        return digest
    port_type = (entry.get("type") or "").strip()
    if port_type not in _PLACEHOLDER_TYPES:
        return port_type
    return ""


def _port_labels_for_object(objname: str) -> Optional[dict]:
    """Return {"inlets": [str...], "outlets": [str...]} from maxref, or None.

    Results (including misses) are cached per object name. Any missing/placeholder
    label becomes an empty string so the client can fall back to a generic name.
    """
    if not objname:
        return None
    if objname in _PORT_LABEL_CACHE:
        return _PORT_LABEL_CACHE[objname]

    labels: Optional[dict] = None
    try:
        from py2max import maxref

        info = maxref.get_object_info(objname)
        if isinstance(info, dict):
            inlets = info.get("inlets") or []
            outlets = info.get("outlets") or []
            in_labels = [_label_from_entry(e) for e in inlets]
            out_labels = [_label_from_entry(e) for e in outlets]
            if any(in_labels) or any(out_labels):
                labels = {"inlets": in_labels, "outlets": out_labels}
    except Exception:
        labels = None

    _PORT_LABEL_CACHE[objname] = labels
    return labels


def _object_name_from_box(text: str, maxclass: str) -> str:
    """Resolve the Max object name used for a maxref lookup.

    Text boxes carry the object in their text ("cycle~ 440" -> "cycle~"); UI
    objects carry it in maxclass ("number", "gain~"). Non-object boxes return "".
    """
    if maxclass in _NO_MAXREF_CLASSES:
        return ""
    text = (text or "").strip()
    if text:
        return text.split()[0]
    return maxclass or ""


def _sized_labels(labels: list, count: int) -> list:
    """Pad/truncate a label list to exactly ``count`` entries (missing -> "")."""
    out = [labels[i] if i < len(labels) else "" for i in range(max(0, count))]
    return out


# Browsers to try, in order, before falling back to the OS default. Chrome is
# preferred: the editor's Open (file-upload) flow is unreliable in Safari.
_PREFERRED_BROWSERS = ("chrome", "google-chrome", "chromium")


def _open_in_browser(url: str) -> None:
    """Open ``url`` in a preferred browser, falling back to the OS default."""
    for name in _PREFERRED_BROWSERS:
        try:
            webbrowser.get(name).open(url)
            print(f"Opened in {name}")
            return
        except webbrowser.Error:
            continue
    webbrowser.open(url)


def validate_message(data: dict) -> tuple[bool, Optional[str]]:
    """Validate an incoming WebSocket message.

    Args:
        data: The parsed message data

    Returns:
        Tuple of (is_valid, error_message)
    """
    message_type = data.get("type")

    # Check message type exists and is known
    if not message_type:
        return False, "Missing 'type' field"

    if not isinstance(message_type, str):
        return False, "'type' must be a string"

    if message_type not in MESSAGE_SCHEMAS:
        return False, f"Unknown message type: {message_type}"

    schema = MESSAGE_SCHEMAS[message_type]

    # Check required fields
    for field, expected_type in schema.items():
        if field not in data:
            return False, f"Missing required field: {field}"

        value = data[field]

        # Check type (expected_type can be a single type or tuple of types)
        if isinstance(expected_type, tuple):
            if not isinstance(value, expected_type):
                type_names = " or ".join(t.__name__ for t in expected_type)
                return False, f"Field '{field}' must be {type_names}"
        else:
            if not isinstance(value, expected_type):
                return False, f"Field '{field}' must be {expected_type.__name__}"

        # String length validation
        if isinstance(value, str) and field in MAX_STRING_LENGTHS:
            if len(value) > MAX_STRING_LENGTHS[field]:
                return (
                    False,
                    f"Field '{field}' exceeds max length ({MAX_STRING_LENGTHS[field]})",
                )

            # Check for null bytes or control characters (except newline, tab)
            if any(ord(c) < 32 and c not in "\n\t\r" for c in value):
                return False, f"Field '{field}' contains invalid control characters"

        # Coordinate bounds validation
        if field in ("x", "y") and isinstance(value, (int, float)):
            if value < COORDINATE_BOUNDS["min"] or value > COORDINATE_BOUNDS["max"]:
                return (
                    False,
                    f"Field '{field}' out of bounds ({COORDINATE_BOUNDS['min']} to {COORDINATE_BOUNDS['max']})",
                )

    # Validate optional fields if present
    if message_type == "update_position":
        # x and y are required and already validated above
        pass

    if message_type == "create_object":
        # Validate optional x, y if present
        for field in ("x", "y"):
            if field in data:
                value = data[field]
                if not isinstance(value, (int, float)):
                    return False, f"Optional field '{field}' must be a number"
                if value < COORDINATE_BOUNDS["min"] or value > COORDINATE_BOUNDS["max"]:
                    return False, f"Optional field '{field}' out of bounds"

    if message_type == "create_connection":
        # Validate optional outlet/inlet indices
        for field in ("src_outlet", "dst_inlet"):
            if field in data:
                value = data[field]
                if not isinstance(value, int):
                    return False, f"Optional field '{field}' must be an integer"
                if value < 0 or value > 255:  # Max 256 inlets/outlets
                    return False, f"Optional field '{field}' out of range (0-255)"

    if message_type == "delete_connection":
        for field in ("src_outlet", "dst_inlet"):
            if field in data:
                value = data[field]
                if not isinstance(value, int):
                    return False, f"Optional field '{field}' must be an integer"
                if value < 0 or value > 255:
                    return False, f"Optional field '{field}' out of range (0-255)"

    if message_type == "open_content":
        # Length-check the payload here rather than via MAX_STRING_LENGTHS so we
        # skip the expensive per-character control-char scan on a large upload.
        content = data.get("content", "")
        if len(content) > MAX_CONTENT_LENGTH:
            return (
                False,
                f"Field 'content' exceeds max length ({MAX_CONTENT_LENGTH})",
            )

    return True, None


def get_patcher_state_json(patcher: Optional["Patcher"]) -> dict:
    """Convert patcher to JSON state for browser.

    Args:
        patcher: The patcher to convert

    Returns:
        Dictionary with boxes and lines data
    """
    if not patcher:
        return {
            "type": "update",
            "boxes": [],
            "lines": [],
            "patcher_path": [],
            "patcher_title": "Untitled",
        }

    boxes = []
    for box in patcher._boxes:
        box_data = {
            "id": getattr(box, "id", ""),
            "text": getattr(box, "text", ""),
            "maxclass": getattr(box, "maxclass", "newobj"),
            "patching_rect": {"x": 0, "y": 0, "w": 100, "h": 22},
        }

        # Check if box has a subpatcher
        has_patcher = hasattr(box, "subpatcher") and box.subpatcher is not None
        box_data["has_subpatcher"] = has_patcher

        # Get patching_rect
        rect = getattr(box, "patching_rect", None)
        if rect:
            if hasattr(rect, "x"):
                box_data["patching_rect"] = {
                    "x": rect.x,
                    "y": rect.y,
                    "w": rect.w,
                    "h": rect.h,
                }
            elif isinstance(rect, (list, tuple)) and len(rect) >= 4:
                box_data["patching_rect"] = {
                    "x": rect[0],
                    "y": rect[1],
                    "w": rect[2],
                    "h": rect[3],
                }

        # Get inlet/outlet counts
        inlet_count = 0
        outlet_count = 0

        # Try get_inlet_count() method first
        if hasattr(box, "get_inlet_count"):
            try:
                inlet_count = box.get_inlet_count()
            except Exception:
                pass

        # If get_inlet_count() returned None, try numinlets attribute (from loaded files)
        if inlet_count is None and hasattr(box, "numinlets"):
            inlet_count = getattr(box, "numinlets", 0)

        box_data["inlet_count"] = inlet_count or 0

        # Try get_outlet_count() method first
        if hasattr(box, "get_outlet_count"):
            try:
                outlet_count = box.get_outlet_count()
            except Exception:
                pass

        # If get_outlet_count() returned None, try numoutlets attribute (from loaded files)
        if outlet_count is None and hasattr(box, "numoutlets"):
            outlet_count = getattr(box, "numoutlets", 0)

        box_data["outlet_count"] = outlet_count or 0

        # Attach maxref-derived port labels (for hover tooltips) when available.
        # Sized to the actual port counts; empty strings where maxref has nothing.
        objname = _object_name_from_box(box_data["text"], box_data["maxclass"])
        labels = _port_labels_for_object(objname)
        if labels is not None:
            box_data["inlet_labels"] = _sized_labels(
                labels["inlets"], box_data["inlet_count"]
            )
            box_data["outlet_labels"] = _sized_labels(
                labels["outlets"], box_data["outlet_count"]
            )

        boxes.append(box_data)

    lines = []
    for line in patcher._lines:
        line_data = {
            "src": getattr(line, "src", ""),
            "dst": getattr(line, "dst", ""),
            "src_outlet": 0,
            "dst_inlet": 0,
        }

        source = getattr(line, "source", None)
        if source and len(source) > 1:
            line_data["src_outlet"] = source[1]

        destination = getattr(line, "destination", None)
        if destination and len(destination) > 1:
            line_data["dst_inlet"] = destination[1]

        lines.append(line_data)

    # Build patcher path (breadcrumb trail)
    patcher_path: list[str] = []
    current: Any = patcher
    while current:
        title = getattr(current, "title", None) or "Main"
        patcher_path.insert(0, title)
        current = getattr(current, "_parent", None)

    # Get current patcher title
    patcher_title = getattr(patcher, "title", None) or getattr(
        patcher, "_path", "Untitled"
    )
    if patcher_title and hasattr(patcher_title, "name"):
        patcher_title = patcher_title.name

    # Get filepath for save/save-as logic
    filepath = None
    if hasattr(patcher, "filepath") and patcher.filepath:
        filepath = str(patcher.filepath)
    elif hasattr(patcher, "_path") and patcher._path:
        filepath = str(patcher._path)

    return {
        "type": "update",
        "boxes": boxes,
        "lines": lines,
        "patcher_path": patcher_path,
        "patcher_title": str(patcher_title),
        "filepath": filepath,
    }


class InteractiveHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler for serving static files with token injection."""

    # Class variable to store the session token
    session_token: Optional[str] = None

    def __init__(self, *args, **kwargs):
        # Static files are bundled inside this package (py2max_server/static/)
        static_dir = Path(__file__).parent / "static"
        super().__init__(*args, directory=str(static_dir), **kwargs)

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/":
            self.serve_interactive_html()
        else:
            super().do_GET()

    def end_headers(self):
        """Disable caching of editor assets.

        The editor is a live dev tool served from disk; browsers (Safari in
        particular) otherwise cache interactive.js/.html heuristically and miss
        code changes on reload. Force revalidation on every request.
        """
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def serve_interactive_html(self):
        """Serve the interactive editor HTML with injected session token."""
        # Static files are bundled inside this package (py2max_server/static/)
        html_file = Path(__file__).parent / "static" / "interactive.html"
        if html_file.exists():
            html_content = html_file.read_text(encoding="utf-8")

            # Inject session token into HTML
            if self.session_token:
                token_script = f'<script>window.PY2MAX_SESSION_TOKEN = "{self.session_token}";</script>'
                # Inject before </head> or at the beginning of <body>
                if "</head>" in html_content:
                    html_content = html_content.replace(
                        "</head>", f"{token_script}\n</head>"
                    )
                else:
                    html_content = token_script + html_content

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # Add security headers
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-XSS-Protection", "1; mode=block")
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))
        else:
            self.send_error(404, "interactive.html not found")

    def log_message(self, format, *args):
        """Suppress logging."""
        pass


class InteractiveWebSocketHandler:
    """WebSocket handler for interactive patcher editing with authentication."""

    #: Cap on retained undo/redo snapshots (each is a full patch serialization).
    MAX_HISTORY = 30

    def __init__(self, patcher: Optional["Patcher"], auto_save: bool = False):
        self.root_patcher = patcher  # Keep reference to root patcher
        self.patcher = patcher  # Current patcher being viewed
        self.clients: Set[ServerConnection] = set()
        self._lock = asyncio.Lock()
        self._save_task: Optional[asyncio.Task[Any]] = None  # Track pending save task
        self.auto_save = auto_save  # Auto-save configuration
        # Undo/redo history. Each entry is a full snapshot of the root patcher
        # plus the current view path, so any mutation (single or batch, at any
        # subpatcher depth) is reversible without per-op inverse logic.
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        # Current view as a list of subpatcher box ids from the root, so the view
        # can be re-derived after a snapshot restore rebuilds the patcher tree.
        self.view_path: list[str] = []
        # Generate a secure session token
        self.session_token = secrets.token_urlsafe(32)
        print(f"WebSocket session token: {self.session_token}")

    def verify_token(self, token: str) -> bool:
        """Verify authentication token using constant-time comparison.

        Args:
            token: The token to verify

        Returns:
            True if token is valid, False otherwise
        """
        if not token or not self.session_token:
            return False
        # Use constant-time comparison to prevent timing attacks
        return secrets.compare_digest(token, self.session_token)

    async def register(self, websocket: ServerConnection):
        """Register a new client connection."""
        async with self._lock:
            self.clients.add(websocket)
            print(f"Client connected. Total clients: {len(self.clients)}")

    async def unregister(self, websocket: ServerConnection):
        """Unregister a client connection."""
        async with self._lock:
            self.clients.discard(websocket)
            print(f"Client disconnected. Total clients: {len(self.clients)}")

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        if not self.clients:
            return

        message_str = json.dumps(message)
        async with self._lock:
            # Send to all clients concurrently
            await asyncio.gather(
                *[client.send(message_str) for client in self.clients],
                return_exceptions=True,
            )

    async def handle_client(self, websocket: ServerConnection):
        """Handle a client WebSocket connection with authentication."""
        try:
            # First message must be authentication token
            auth_message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            auth_str = (
                auth_message.decode("utf-8")
                if isinstance(auth_message, bytes)
                else auth_message
            )

            try:
                auth_data = json.loads(auth_str)
                if auth_data.get("type") != "auth" or not self.verify_token(
                    auth_data.get("token", "")
                ):
                    await websocket.send(
                        json.dumps(
                            {"type": "error", "message": "Authentication failed"}
                        )
                    )
                    await websocket.close(1008, "Unauthorized")
                    print("Client authentication failed")
                    return
            except (json.JSONDecodeError, KeyError):
                await websocket.send(
                    json.dumps(
                        {"type": "error", "message": "Invalid authentication message"}
                    )
                )
                await websocket.close(1008, "Invalid auth format")
                print("Client sent invalid authentication message")
                return

            # Authentication successful
            await self.register(websocket)
            await websocket.send(json.dumps({"type": "auth_success"}))
            print("Client authenticated successfully")

            # Send initial state
            if self.patcher:
                state = get_patcher_state_json(self.patcher)
                await websocket.send(json.dumps(state))

            # Listen for messages from client
            async for message in websocket:
                msg_str = (
                    message.decode("utf-8") if isinstance(message, bytes) else message
                )
                await self.handle_message(websocket, msg_str)

        except asyncio.TimeoutError:
            print("Client authentication timeout")
            await websocket.close(1008, "Authentication timeout")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister(websocket)

    async def handle_message(self, websocket: ServerConnection, message: str):
        """Handle incoming message from client."""
        try:
            data = json.loads(message)

            # Validate message before processing
            is_valid, error_msg = validate_message(data)
            if not is_valid:
                print(f"Message validation failed: {error_msg}")
                await websocket.send(
                    json.dumps(
                        {"type": "error", "message": f"Validation error: {error_msg}"}
                    )
                )
                return

            message_type = data.get("type")

            if message_type == "update_position":
                await self.handle_update_position(data)
            elif message_type == "create_object":
                await self.handle_create_object(data)
            elif message_type == "create_connection":
                await self.handle_create_connection(data)
            elif message_type == "delete_object":
                await self.handle_delete_object(data)
            elif message_type == "delete_connection":
                await self.handle_delete_connection(data)
            elif message_type == "save":
                await self.handle_save()
            elif message_type == "save_as":
                await self.handle_save_as(data)
            elif message_type == "open":
                await self.handle_open(data)
            elif message_type == "open_content":
                await self.handle_open_content(data)
            elif message_type == "list_patches":
                await self.handle_list_patches(websocket, data)
            elif message_type == "export_patch":
                await self.handle_export_patch(websocket)
            elif message_type == "edit_object_text":
                await self.handle_edit_object_text(data)
            elif message_type == "navigate_to_subpatcher":
                await self.handle_navigate_to_subpatcher(data)
            elif message_type == "navigate_to_parent":
                await self.handle_navigate_to_parent()
            elif message_type == "navigate_to_root":
                await self.handle_navigate_to_root()
            elif message_type == "navigate_up":
                await self.handle_navigate_up(data)
            elif message_type == "undo":
                await self.handle_undo()
            elif message_type == "redo":
                await self.handle_redo()
            elif message_type == "update_positions":
                await self.handle_update_positions(data)
            elif message_type == "delete_objects":
                await self.handle_delete_objects(data)

        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}")
            await websocket.send(
                json.dumps({"type": "error", "message": "Invalid JSON format"})
            )
        except Exception as e:
            print(f"Error handling message: {e}")
            await websocket.send(
                json.dumps({"type": "error", "message": f"Server error: {str(e)}"})
            )

    async def handle_update_position(self, data: dict):
        """Handle object position update from browser."""
        if not self.patcher:
            return

        box_id = data.get("box_id")
        x = data.get("x")
        y = data.get("y")

        # Find box and update position
        for box in self.patcher._boxes:
            if box.id == box_id:
                self._push_undo()
                # Update position
                if hasattr(box, "patching_rect"):
                    rect = box.patching_rect
                    if hasattr(rect, "x"):
                        # Rect is a NamedTuple (immutable), create new one
                        from py2max.core.common import Rect

                        box.patching_rect = Rect(
                            float(x) if x is not None else 0.0,
                            float(y) if y is not None else 0.0,
                            rect.w,
                            rect.h,
                        )
                    elif isinstance(rect, list):
                        rect[0] = x
                        rect[1] = y

                # Send delta update instead of full state (performance optimization)
                await self.broadcast(
                    {
                        "type": "position_update",
                        "box_id": box_id,
                        "x": float(x) if x is not None else 0.0,
                        "y": float(y) if y is not None else 0.0,
                    }
                )

                # Schedule debounced save
                await self.schedule_save()
                break

    async def schedule_save(self):
        """Schedule a debounced save after 2 seconds of no updates (if auto-save enabled)."""
        if not self.auto_save:
            return  # Auto-save disabled

        # Cancel previous save task if exists
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()

        # Schedule new save task
        self._save_task = asyncio.create_task(self._debounced_save())

    async def _debounced_save(self):
        """Save patch after delay (debounced)."""
        try:
            await asyncio.sleep(2.0)  # Wait 2 seconds
            if (
                self.patcher
                and hasattr(self.patcher, "filepath")
                and self.patcher.filepath
            ):
                self.patcher.save()
                print(f"Auto-saved: {self.patcher.filepath}")
        except asyncio.CancelledError:
            # Task was cancelled (new position update came in)
            pass
        except Exception as e:
            print(f"Error during auto-save: {e}")

    async def handle_create_object(self, data: dict):
        """Handle object creation from browser."""
        if not self.patcher:
            return

        text = data.get("text", "newobj")
        x = data.get("x", 100)
        y = data.get("y", 100)

        self._push_undo()

        # Create new object
        box = self.patcher.add_textbox(text)

        # Set position
        if hasattr(box, "patching_rect"):
            rect = box.patching_rect
            if hasattr(rect, "x"):
                # Rect is a NamedTuple (immutable), create new one
                from py2max.core.common import Rect

                box.patching_rect = Rect(x, y, rect.w, rect.h)
            elif isinstance(rect, list):
                rect[0] = x
                rect[1] = y

        # Broadcast update to all clients
        state = get_patcher_state_json(self.patcher)
        await self.broadcast(state)

        # Schedule auto-save
        await self.schedule_save()

    async def handle_create_connection(self, data: dict):
        """Handle connection creation from browser."""
        if not self.patcher:
            return

        src_id = data.get("src_id")
        dst_id = data.get("dst_id")
        src_outlet = data.get("src_outlet", 0)
        dst_inlet = data.get("dst_inlet", 0)

        # Find source and destination boxes
        src_box = None
        dst_box = None

        for box in self.patcher._boxes:
            if box.id == src_id:
                src_box = box
            if box.id == dst_id:
                dst_box = box

        if src_box and dst_box:
            self._push_undo()
            # Create connection
            self.patcher.add_line(src_box, dst_box, outlet=src_outlet, inlet=dst_inlet)  # type: ignore[arg-type]

            # Broadcast update to all clients
            state = get_patcher_state_json(self.patcher)
            await self.broadcast(state)

            # Schedule auto-save
            await self.schedule_save()

    async def handle_delete_object(self, data: dict):
        """Handle object deletion from browser."""
        if not self.patcher:
            return

        box_id = data.get("box_id")

        # Find and remove box
        for i, box in enumerate(self.patcher._boxes):
            if box.id == box_id:
                self._push_undo()
                self.patcher._boxes.pop(i)

                # Also remove any connected lines
                self.patcher._lines = [
                    line
                    for line in self.patcher._lines
                    if line.src != box_id and line.dst != box_id
                ]

                # Broadcast update to all clients
                state = get_patcher_state_json(self.patcher)
                await self.broadcast(state)

                # Schedule auto-save
                await self.schedule_save()
                break

    async def handle_delete_connection(self, data: dict):
        """Handle connection deletion from browser."""
        if not self.patcher:
            return

        src_id = data.get("src_id")
        dst_id = data.get("dst_id")
        src_outlet = data.get("src_outlet", 0)
        dst_inlet = data.get("dst_inlet", 0)

        # Find and remove matching line
        for i, line in enumerate(self.patcher._lines):
            source = getattr(line, "source", [None, 0])
            destination = getattr(line, "destination", [None, 0])
            if (
                line.src == src_id
                and line.dst == dst_id
                and source[1] == src_outlet
                and destination[1] == dst_inlet
            ):
                self._push_undo()
                self.patcher._lines.pop(i)

                # Broadcast update to all clients
                state = get_patcher_state_json(self.patcher)
                await self.broadcast(state)

                # Schedule auto-save
                await self.schedule_save()
                break

    async def handle_save(self):
        """Handle manual save request from browser."""
        if not self.root_patcher:  # Save root patcher
            return

        try:
            if hasattr(self.root_patcher, "filepath") and self.root_patcher.filepath:
                self.root_patcher.save()
                print(f"Saved: {self.root_patcher.filepath}")

                # Notify clients that save completed
                await self.broadcast(
                    {
                        "type": "save_complete",
                        "filepath": str(self.root_patcher.filepath),
                    }
                )
            else:
                # No filepath set - request filename from client
                print("No filepath set, requesting filename from client")
                await self.broadcast(
                    {"type": "save_as_required", "message": "Please enter a filename"}
                )
        except Exception as e:
            print(f"Error saving: {e}")
            await self.broadcast({"type": "save_error", "message": str(e)})

    async def handle_save_as(self, data: dict):
        """Handle save-as request with specified filepath."""
        if not self.root_patcher:
            return

        filepath = data.get("filepath", "")
        if not filepath:
            await self.broadcast(
                {"type": "save_error", "message": "No filepath provided"}
            )
            return

        try:
            # Ensure .maxpat extension
            if not filepath.endswith((".maxpat", ".maxhelp", ".rbnopat")):
                filepath = filepath + ".maxpat"

            # Set the filepath on the patcher
            from pathlib import Path

            self.root_patcher._path = Path(filepath)

            # Save the patcher
            self.root_patcher.save()
            print(f"Saved as: {filepath}")

            # Notify clients that save completed
            await self.broadcast(
                {
                    "type": "save_complete",
                    "filepath": filepath,
                }
            )

            # Also update the state to include the new filepath
            state = get_patcher_state_json(self.patcher)
            state["filepath"] = filepath
            await self.broadcast(state)

        except Exception as e:
            print(f"Error saving: {e}")
            await self.broadcast({"type": "save_error", "message": str(e)})

    async def handle_open(self, data: dict):
        """Handle a request to open a different patch file from disk.

        Replaces the served patcher (both the root and the current view) with the
        contents of the given file, then broadcasts the new state to all clients.
        """
        filepath = data.get("filepath", "").strip()
        if not filepath:
            await self.broadcast(
                {"type": "open_error", "message": "No filepath provided"}
            )
            return

        path = Path(filepath)
        if not path.exists() or not path.is_file():
            await self.broadcast(
                {"type": "open_error", "message": f"File not found: {filepath}"}
            )
            return

        try:
            from py2max import Patcher

            patcher = Patcher.from_file(path)
            _coerce_patcher_rect(patcher)
        except Exception as e:
            print(f"Error opening {filepath}: {e}")
            await self.broadcast(
                {"type": "open_error", "message": f"Could not open: {e}"}
            )
            return

        # Swap in the newly loaded patcher as both root and current view.
        self.root_patcher = patcher
        self.patcher = patcher
        self._reset_view_and_history()
        print(f"Opened: {filepath}")

        state = get_patcher_state_json(self.patcher)
        await self.broadcast(state)

    def _default_listing_dir(self) -> Path:
        """Directory the file picker shows first.

        Prefer the folder of the currently-loaded patch (so Open starts where the
        user is working); fall back to the process working directory.
        """
        path = None
        if self.root_patcher is not None:
            path = getattr(self.root_patcher, "_path", None) or getattr(
                self.root_patcher, "filepath", None
            )
        if path:
            try:
                parent = Path(str(path)).expanduser().resolve().parent
                if parent.is_dir():
                    return parent
            except (OSError, ValueError):
                pass
        return Path.cwd()

    async def handle_list_patches(
        self, websocket: ServerConnection, data: dict
    ) -> None:
        """List patch files and subdirectories for the server-side file picker.

        Replies to the requesting client only (not a broadcast) with the resolved
        directory, its parent (for "up" navigation), and the entries: openable
        patch files plus subdirectories to descend into. This avoids the browser
        file API entirely -- which fixes Open in Safari -- and preserves the real
        filesystem path so the opened patch can be saved back in place.
        """
        requested = data.get("directory")
        try:
            if isinstance(requested, str) and requested.strip():
                base = Path(requested).expanduser().resolve()
                if not base.is_dir():
                    base = self._default_listing_dir()
            else:
                base = self._default_listing_dir()
        except (OSError, ValueError):
            base = self._default_listing_dir()

        try:
            children = list(base.iterdir())
        except (OSError, PermissionError) as e:
            await websocket.send(
                json.dumps(
                    {
                        "type": "patch_list_error",
                        "message": f"Cannot read directory: {e}",
                    }
                )
            )
            return

        dirs: list[dict] = []
        files: list[dict] = []
        for child in children:
            name = child.name
            if name.startswith("."):
                continue  # Skip hidden entries
            try:
                if child.is_dir():
                    dirs.append({"name": name, "path": str(child), "is_dir": True})
                elif child.is_file() and child.suffix.lower() in PATCH_EXTENSIONS:
                    files.append({"name": name, "path": str(child), "is_dir": False})
            except OSError:
                continue  # Broken symlink or otherwise unreadable entry

        dirs.sort(key=lambda e: e["name"].lower())
        files.sort(key=lambda e: e["name"].lower())

        # No parent entry when already at the filesystem root.
        parent = None if base.parent == base else str(base.parent)

        await websocket.send(
            json.dumps(
                {
                    "type": "patch_list",
                    "directory": str(base),
                    "parent": parent,
                    "entries": dirs + files,
                }
            )
        )

    async def handle_open_content(self, data: dict):
        """Handle a patch opened from uploaded file contents (browser file picker).

        The browser cannot pass a filesystem path, so it sends the chosen file's
        text. A .maxpat is JSON, so we parse it and build a patcher from the
        embedded "patcher" object, mirroring Patcher.from_file.
        """
        filename = (data.get("filename") or "untitled.maxpat").strip()
        content = data.get("content", "")

        try:
            maxpat = json.loads(content)
            if isinstance(maxpat, dict) and "patcher" in maxpat:
                patcher_dict = maxpat["patcher"]
            else:
                patcher_dict = maxpat  # tolerate a bare patcher object

            from py2max import Patcher

            patcher = Patcher.from_dict(patcher_dict)
            _coerce_patcher_rect(patcher)
        except Exception as e:
            print(f"Error opening {filename}: {e}")
            await self.broadcast(
                {"type": "open_error", "message": f"Could not open {filename}: {e}"}
            )
            return

        # Swap in the newly loaded patcher as both root and current view.
        self.root_patcher = patcher
        self.patcher = patcher
        self._reset_view_and_history()
        print(f"Opened (uploaded): {filename}")

        state = get_patcher_state_json(self.patcher)
        await self.broadcast(state)

    async def handle_export_patch(self, websocket: ServerConnection):
        """Serialize the patch and send its text to the requesting client.

        Used for a browser-side "Save As" (the browser writes the file via a
        native dialog); the browser cannot pass a server path for an in-place save.
        """
        if not self.root_patcher:
            return

        try:
            content = self.root_patcher.to_json()

            # Suggest a filename from the current path or title.
            filename = "patch.maxpat"
            path = getattr(self.root_patcher, "_path", None) or getattr(
                self.root_patcher, "filepath", None
            )
            if path:
                filename = Path(str(path)).name
            else:
                title = getattr(self.root_patcher, "title", None)
                if title:
                    filename = f"{title}.maxpat"

            await websocket.send(
                json.dumps(
                    {
                        "type": "patch_content",
                        "filename": filename,
                        "content": content,
                    }
                )
            )
        except Exception as e:
            print(f"Error exporting patch: {e}")
            await websocket.send(json.dumps({"type": "save_error", "message": str(e)}))

    async def handle_edit_object_text(self, data: dict):
        """Update the text of an existing object (rename/edit in place)."""
        if not self.patcher:
            return

        box_id = data.get("box_id")
        text = data.get("text", "")

        for box in self.patcher._boxes:
            if box.id == box_id:
                self._push_undo()
                # Box.text is a read-only property backed by __dict__["text"]
                # (file-loaded) or _kwds["text"] (programmatic). Update both so
                # the getter and serialization agree.
                box.__dict__["text"] = text
                kwds = getattr(box, "_kwds", None)
                if isinstance(kwds, dict):
                    kwds["text"] = text
                # Broadcast the new state to all clients.
                state = get_patcher_state_json(self.patcher)
                await self.broadcast(state)
                await self.schedule_save()
                break

    async def handle_navigate_to_subpatcher(self, data: dict):
        """Handle navigation to a subpatcher."""
        if not self.patcher:
            return

        box_id = data.get("box_id")
        if not box_id:
            return

        # Find box with matching ID
        for box in self.patcher._boxes:
            if box.id == box_id:
                # Check if box has a subpatcher
                if hasattr(box, "subpatcher") and box.subpatcher is not None:
                    # Navigate to subpatcher
                    self.patcher = box.subpatcher
                    self.view_path.append(box.id)
                    box_text = getattr(box, "text", box.id)
                    print(f"Navigated to subpatcher: {box_text}")

                    # Send updated state to all clients
                    state = get_patcher_state_json(self.patcher)
                    await self.broadcast(state)
                    break

    async def handle_navigate_to_parent(self):
        """Handle navigation to parent patcher."""
        if not self.patcher:
            return

        # Get parent patcher
        parent = getattr(self.patcher, "_parent", None)
        if parent:
            self.patcher = parent
            if self.view_path:
                self.view_path.pop()
            print("Navigated to parent patcher")

            # Send updated state to all clients
            state = get_patcher_state_json(self.patcher)
            await self.broadcast(state)
        else:
            print("Already at root patcher")

    async def handle_navigate_to_root(self):
        """Handle navigation to root patcher."""
        if not self.root_patcher:
            return

        self.patcher = self.root_patcher
        self.view_path = []
        print("Navigated to root patcher")

        # Send updated state to all clients
        state = get_patcher_state_json(self.patcher)
        await self.broadcast(state)

    async def handle_navigate_up(self, data: dict):
        """Ascend ``levels`` parent patchers in a single step.

        Used by the clickable breadcrumb to jump to an arbitrary ancestor without
        firing one message per level. Walking stops early at the root, so an
        over-large ``levels`` is harmless.
        """
        if not self.patcher:
            return

        levels = data.get("levels", 0)
        target = self.patcher
        for _ in range(max(0, int(levels))):
            parent = getattr(target, "_parent", None)
            if parent is None:
                break
            target = parent

        if target is not self.patcher:
            self.patcher = target
            # Trim the resolved view path by the same number of levels.
            trim = min(max(0, int(levels)), len(self.view_path))
            if trim:
                self.view_path = self.view_path[:-trim]
            print(f"Navigated up {levels} level(s)")
            state = get_patcher_state_json(self.patcher)
            await self.broadcast(state)

    # ------------------------------------------------------------------ #
    # Undo/redo (server-side snapshots)
    # ------------------------------------------------------------------ #

    def _resolve_view(self, path: list[str]) -> tuple[Any, list[str]]:
        """Walk ``path`` (subpatcher box ids) from the root to a live patcher.

        Returns the deepest reachable patcher and the portion of the path that
        resolved. Stops early if a hop no longer exists (e.g. an undo removed the
        subpatcher we were inside), so the view falls back to the nearest
        surviving ancestor rather than dangling.
        """
        patcher = self.root_patcher
        resolved: list[str] = []
        for box_id in path:
            found = None
            for box in getattr(patcher, "_boxes", []):
                if box.id == box_id and getattr(box, "subpatcher", None) is not None:
                    found = box.subpatcher
                    break
            if found is None:
                break
            patcher = found
            resolved.append(box_id)
        return patcher, resolved

    def _reset_view_and_history(self) -> None:
        """Reset view path and clear undo/redo (e.g. after opening a new file)."""
        self.view_path = []
        self.undo_stack.clear()
        self.redo_stack.clear()

    def _snapshot(self) -> Optional[dict]:
        """Capture the root patcher + current view as a restorable snapshot."""
        if self.root_patcher is None:
            return None
        return {
            "json": self.root_patcher.to_json(),
            "view_path": list(self.view_path),
        }

    def _restore_snapshot(self, snap: dict) -> None:
        """Replace the patcher tree from a snapshot and re-derive the view."""
        from py2max import Patcher

        # Preserve the on-disk path: to_json() does not carry it, so a naive
        # restore would lose where Save writes to.
        old_path = getattr(self.root_patcher, "_path", None)

        maxpat = json.loads(snap["json"])
        if isinstance(maxpat, dict) and "patcher" in maxpat:
            patcher_dict = maxpat["patcher"]
        else:
            patcher_dict = maxpat
        patcher = Patcher.from_dict(patcher_dict)
        _coerce_patcher_rect(patcher)

        if old_path is not None:
            patcher._path = old_path

        self.root_patcher = patcher
        self.patcher, self.view_path = self._resolve_view(list(snap.get("view_path", [])))

    def _push_undo(self) -> None:
        """Record the current state before a mutation; clears the redo stack."""
        snap = self._snapshot()
        if snap is None:
            return
        self.undo_stack.append(snap)
        if len(self.undo_stack) > self.MAX_HISTORY:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    async def handle_undo(self) -> None:
        """Restore the previous snapshot (no-op if the undo stack is empty)."""
        if not self.undo_stack:
            print("Nothing to undo")
            return
        current = self._snapshot()
        if current is not None:
            self.redo_stack.append(current)
            if len(self.redo_stack) > self.MAX_HISTORY:
                self.redo_stack.pop(0)
        self._restore_snapshot(self.undo_stack.pop())
        print("Undo")
        await self.broadcast(get_patcher_state_json(self.patcher))
        await self.schedule_save()

    async def handle_redo(self) -> None:
        """Re-apply the most recently undone snapshot."""
        if not self.redo_stack:
            print("Nothing to redo")
            return
        current = self._snapshot()
        if current is not None:
            self.undo_stack.append(current)
            if len(self.undo_stack) > self.MAX_HISTORY:
                self.undo_stack.pop(0)
        self._restore_snapshot(self.redo_stack.pop())
        print("Redo")
        await self.broadcast(get_patcher_state_json(self.patcher))
        await self.schedule_save()

    # ------------------------------------------------------------------ #
    # Batch mutations (used by multi-select group move/delete)
    # ------------------------------------------------------------------ #

    async def handle_update_positions(self, data: dict) -> None:
        """Move several boxes at once (one undo step for the whole group)."""
        if not self.patcher:
            return
        positions = data.get("positions") or []
        if not isinstance(positions, list) or not positions:
            return

        by_id = {p.get("box_id"): p for p in positions if isinstance(p, dict)}
        boxes = [b for b in self.patcher._boxes if b.id in by_id]
        if not boxes:
            return

        self._push_undo()
        from py2max.core.common import Rect

        for box in boxes:
            p = by_id[box.id]
            x = p.get("x")
            y = p.get("y")
            if hasattr(box, "patching_rect"):
                rect = box.patching_rect
                if hasattr(rect, "x"):
                    box.patching_rect = Rect(
                        float(x) if x is not None else 0.0,
                        float(y) if y is not None else 0.0,
                        rect.w,
                        rect.h,
                    )
                elif isinstance(rect, list):
                    rect[0] = x
                    rect[1] = y

        await self.broadcast(get_patcher_state_json(self.patcher))
        await self.schedule_save()

    async def handle_delete_objects(self, data: dict) -> None:
        """Delete several boxes (and their lines) at once as one undo step."""
        if not self.patcher:
            return
        box_ids = data.get("box_ids") or []
        if not isinstance(box_ids, list) or not box_ids:
            return
        targets = {bid for bid in box_ids if isinstance(bid, str)}
        present = [b for b in self.patcher._boxes if b.id in targets]
        if not present:
            return

        self._push_undo()
        self.patcher._boxes = [b for b in self.patcher._boxes if b.id not in targets]
        self.patcher._lines = [
            line
            for line in self.patcher._lines
            if line.src not in targets and line.dst not in targets
        ]
        await self.broadcast(get_patcher_state_json(self.patcher))
        await self.schedule_save()

    async def notify_update(self):
        """Notify all clients of a patcher update."""
        if self.patcher:
            state = get_patcher_state_json(self.patcher)
            await self.broadcast(state)


class InteractivePatcherServer:
    """WebSocket server for interactive patcher editing.

    Can be used as a context manager for automatic cleanup:

        >>> async with p.serve_interactive() as server:
        ...     await asyncio.sleep(10)  # Server runs
        # Server automatically stopped
    """

    def __init__(
        self,
        patcher: "Patcher",
        port: int = 8000,
        auto_open: bool = True,
        auto_save: bool = False,
    ):
        """Initialize the interactive server.

        Args:
            patcher: Patcher instance to serve
            port: HTTP/WebSocket server port (default: 8000)
            auto_open: Automatically open browser (default: True)
            auto_save: Automatically save changes after 2 seconds (default: False)
        """
        self.patcher = patcher
        self.port = port
        self.ws_port = port + 1  # WebSocket on different port
        self.auto_open = auto_open
        self.handler = InteractiveWebSocketHandler(patcher, auto_save=auto_save)
        self.ws_server: Optional[Server] = None
        self.http_server: Optional[http.server.HTTPServer] = None
        self.http_thread: Optional[threading.Thread] = None
        self._running = False

    async def start(self):
        """Start the HTTP and WebSocket servers.

        Returns:
            self: For method chaining
        """
        if self._running:
            print("Server already running")
            return self

        # Set the session token in the HTTP handler class
        InteractiveHTTPHandler.session_token = self.handler.session_token

        # Start HTTP server in background thread
        self.http_server = http.server.HTTPServer(
            ("localhost", self.port), InteractiveHTTPHandler
        )
        self.http_thread = threading.Thread(
            target=self.http_server.serve_forever, daemon=True
        )
        self.http_thread.start()

        # Start WebSocket server
        self.ws_server = await serve(
            self.handler.handle_client,
            "localhost",
            self.ws_port,
            # Allow larger frames so uploaded patches (open_content) aren't
            # rejected by the 1 MB default.
            max_size=MAX_CONTENT_LENGTH + 1024 * 1024,
        )
        self._running = True

        url = f"http://localhost:{self.port}"
        print(f"Interactive server started: {url}")
        print(f"WebSocket endpoint: ws://localhost:{self.ws_port}/ws")

        # Open browser
        if self.auto_open:
            await asyncio.sleep(0.5)  # Give server time to start
            _open_in_browser(url)

        return self

    async def stop(self):
        """Stop the servers."""
        await self.shutdown()

    async def shutdown(self):
        """Shutdown the servers gracefully."""
        if not self._running:
            return

        # Stop WebSocket server
        if self.ws_server:
            self.ws_server.close()
            await self.ws_server.wait_closed()

        # Stop HTTP server
        if self.http_server:
            self.http_server.shutdown()
            self.http_server.server_close()

        self._running = False
        print("Interactive server stopped")

        # Clear clients
        self.handler.clients.clear()

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.shutdown()
        return False

    async def notify_update(self):
        """Notify all connected clients of an update."""
        await self.handler.notify_update()


async def serve_interactive(
    patcher: "Patcher",
    port: int = 8000,
    auto_open: bool = True,
    auto_save: bool = False,
) -> InteractivePatcherServer:
    """Start an interactive WebSocket server for a patcher.

    The server can be used as an async context manager:

        >>> async with serve_interactive(p) as server:
        ...     await asyncio.sleep(10)
        # Server automatically stopped

    Or managed manually:

        >>> server = await serve_interactive(p)
        >>> # ... interact ...
        >>> await server.shutdown()

    Args:
        patcher: Patcher instance to serve
        port: WebSocket server port (default: 8000)
        auto_open: Automatically open browser (default: True)
        auto_save: Automatically save changes after 2 seconds (default: False)

    Returns:
        InteractivePatcherServer instance
    """
    server = InteractivePatcherServer(patcher, port, auto_open, auto_save)
    await server.start()
    return server


__all__ = [
    "InteractiveWebSocketHandler",
    "InteractivePatcherServer",
    "serve_interactive",
]
