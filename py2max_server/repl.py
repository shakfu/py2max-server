"""Command helpers exposed to the Python console.

These back the names available in the browser console (`save`, `info`,
`layout`, ...) -- ``rpc.ReplServer`` puts them in the evaluation namespace.

This module used to also host a ptpython-based terminal REPL. That was removed
along with the standalone client: the console is served in the editor itself,
which needs no second terminal, no token to copy and no ptpython dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from py2max.core import Patcher

    from .websocket import InteractivePatcherServer


class ReplCommands:
    """Custom command functions for py2max REPL.

    These functions provide IPython-style magic command functionality
    without requiring IPython as a dependency.
    """

    def __init__(self, patcher: "Patcher", server: "InteractivePatcherServer"):
        self.patcher = patcher
        self.server = server

    def save(self) -> None:
        """Save patcher to disk.

        Example:
            >>> save()
            ✓ Saved: demo.maxpat
        """
        if self.patcher.filepath:
            self.patcher.save()
            print(f"✓ Saved: {self.patcher.filepath}")
        else:
            print("⚠ No filepath set - use p.save_as('path.maxpat')")

    def info(self, verbose: bool = False) -> None:
        """Show patcher information.

        Args:
            verbose: If True, show detailed box and connection information

        Example:
            >>> info()
            Patcher: demo.maxpat
            Objects: 5 boxes
            Connections: 4 lines
            Layout: grid (horizontal)
            Server: http://localhost:8000 (1 client)

            >>> info(verbose=True)
            # Shows detailed box and connection information
        """
        filepath = self.patcher.filepath or "Untitled"
        boxes = len(self.patcher._boxes)
        lines = len(self.patcher._lines)
        layout = type(self.patcher._layout_mgr).__name__
        clients = len(self.server.handler.clients)

        print()
        print("=" * 70)
        print(f"Patcher: {filepath}")
        print("=" * 70)
        print(f"Objects: {boxes} boxes")
        print(f"Connections: {lines} lines")
        print(f"Layout: {layout}")
        print(
            f"Server: http://localhost:{self.server.port} ({clients} client{'s' if clients != 1 else ''})"
        )

        if verbose and boxes > 0:
            print()
            print("-" * 70)
            print("BOXES:")
            print("-" * 70)

            for box in self.patcher._boxes:
                box_id = getattr(box, "id", "unknown")
                maxclass = getattr(box, "maxclass", "unknown")
                text = getattr(box, "text", None)
                pos = getattr(box, "patching_rect", None)

                # Format position
                pos_str = ""
                if pos:
                    # Handle both Rect objects and lists
                    if hasattr(pos, "x"):
                        pos_str = f" @ [{int(pos.x)}, {int(pos.y)}]"
                    elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
                        pos_str = f" @ [{int(pos[0])}, {int(pos[1])}]"

                # Format text/description
                if text:
                    desc = f"{maxclass}: '{text}'"
                else:
                    desc = maxclass

                print(f"  [{box_id}] {desc}{pos_str}")

        if verbose and lines > 0:
            print()
            print("-" * 70)
            print("CONNECTIONS:")
            print("-" * 70)

            for line in self.patcher._lines:
                src = line.source if hasattr(line, "source") else []
                dst = line.destination if hasattr(line, "destination") else []

                if len(src) >= 2 and len(dst) >= 2:
                    src_id = src[0]
                    src_outlet = src[1]
                    dst_id = dst[0]
                    dst_inlet = dst[1]

                    # Try to get box names for better readability
                    src_box = self.patcher._objects.get(src_id)
                    dst_box = self.patcher._objects.get(dst_id)

                    src_name = ""
                    dst_name = ""

                    if src_box:
                        src_text = getattr(src_box, "text", None)
                        if src_text:
                            # Truncate long text
                            src_name = (
                                f" ({src_text[:20]}...)"
                                if len(src_text) > 20
                                else f" ({src_text})"
                            )

                    if dst_box:
                        dst_text = getattr(dst_box, "text", None)
                        if dst_text:
                            dst_name = (
                                f" ({dst_text[:20]}...)"
                                if len(dst_text) > 20
                                else f" ({dst_text})"
                            )

                    print(
                        f"  [{src_id}]{src_name} outlet {src_outlet} -> [{dst_id}]{dst_name} inlet {dst_inlet}"
                    )

        print("=" * 70)
        print()

    def layout(self, layout_type: str = "grid") -> None:
        """Change layout manager.

        Args:
            layout_type: Layout type ('topdown', 'horizontal', 'vertical',
                        'grid', 'flow', 'columnar', 'matrix')

        Example:
            >>> layout('flow')
            ✓ Layout changed to: flow
        """
        try:
            self.patcher.set_layout_mgr(layout_type)
            print(f"✓ Layout changed to: {layout_type}")
        except Exception as e:
            print(f"✗ Error changing layout: {e}")

    def optimize(self) -> None:
        """Optimize layout positioning.

        Example:
            >>> optimize()
            ✓ Layout optimized
        """
        self.patcher.optimize_layout()
        print("✓ Layout optimized")

    def clear(self) -> None:
        """Clear all objects from patcher.

        Example:
            >>> clear()
            ✓ Cleared 5 objects and 4 connections
        """
        boxes = len(self.patcher._boxes)
        lines = len(self.patcher._lines)
        self.patcher._boxes.clear()
        self.patcher._lines.clear()
        self.patcher._objects.clear()
        self.patcher._node_ids.clear()
        self.patcher._edge_ids.clear()
        print(f"✓ Cleared {boxes} objects and {lines} connections")

    def clients(self) -> None:
        """Show connected WebSocket clients.

        Example:
            >>> clients()
            Connected clients: 2
            - Client 1: ws://localhost:8001
            - Client 2: ws://localhost:8001
        """
        client_list = list(self.server.handler.clients)
        count = len(client_list)
        print(f"Connected clients: {count}")
        if count > 0:
            for i, client in enumerate(client_list, 1):
                # Get client info if available
                remote = getattr(client, "remote_address", ("unknown", "unknown"))
                print(f"  - Client {i}: {remote[0]}:{remote[1]}")

    def help_obj(self, obj_name: Optional[str] = None) -> None:
        """Show Max object help.

        Args:
            obj_name: Max object name (e.g., 'cycle~', 'gain~')

        Example:
            >>> help_obj('cycle~')
            cycle~: Sinusoidal oscillator
            ...
        """
        if not obj_name:
            print("Usage: help_obj('object_name')")
            print("Example: help_obj('cycle~')")
            return

        try:
            from py2max.maxref import get_object_help

            help_text = get_object_help(obj_name)
            if help_text:
                print(help_text)
            else:
                print(f"⚠ No help found for: {obj_name}")
        except Exception as e:
            print(f"✗ Error getting help: {e}")

    def commands(self) -> None:
        """Show available REPL commands.

        Example:
            >>> commands()
            Available commands:
            ...
        """
        print()
        print("=" * 70)
        print("Available commands:")
        print("=" * 70)
        print()
        print("Objects:")
        print("  p              - Patcher object")
        print("  server         - WebSocket server object")
        print()
        print("Commands:")
        print("  save()              - Save patcher to disk")
        print("  info(verbose=False) - Show patcher information")
        print("                        Use info(True) for detailed box/connection list")
        print("  layout(type)        - Change layout manager")
        print("  optimize()          - Optimize layout positioning")
        print("  clear()             - Clear all objects")
        print("  clients()           - Show connected WebSocket clients")
        print("  help_obj(name)      - Show Max object help")
        print("  commands()          - Show this help")
        print()
        print("Examples:")
        print("  >>> osc = p.add('cycle~ 440')")
        print("  >>> gain = p.add('gain~')")
        print("  >>> p.link(osc, gain)")
        print("  >>> info()          # Quick summary")
        print("  >>> info(True)      # Detailed view with boxes and connections")
        print("  >>> save()")
        print("=" * 70)
        print()


__all__ = ["ReplCommands"]
