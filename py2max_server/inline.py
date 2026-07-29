"""Inline REPL with background server and log redirection.

This module provides an inline REPL mode where the server runs in the
background with logs redirected to a file, allowing a clean single-terminal
experience.

Usage:
    $ py2max-server serve my-patch.maxpat --repl --log-file server.log

Features:
    - Single terminal (no need for separate client terminal)
    - Server logs redirected to file
    - Clean REPL interface
    - Can tail log file in separate terminal if needed

Architecture:
    ┌─────────────────────────────────────┐
    │ Single Terminal                     │
    │                                     │
    │ ┌─────────────────────────────────┐ │
    │ │ Server (background thread)      │ │
    │ │ - HTTP: 8000                    │ │
    │ │ - WebSocket: 8001               │ │
    │ │ - Logs → server.log             │ │
    │ └─────────────────────────────────┘ │
    │                                     │
    │ ┌─────────────────────────────────┐ │
    │ │ REPL (foreground, ptpython)     │ │
    │ │ py2max[demo.maxpat]>>>          │ │
    │ │ [clean, no logs]                │ │
    │ └─────────────────────────────────┘ │
    └─────────────────────────────────────┘

    Optional: $ tail -f server.log (in another terminal)
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from py2max.core import Patcher
    from .websocket import InteractivePatcherServer


def setup_file_logging(log_file: Path, level: int = logging.INFO) -> logging.Handler:
    """Setup file logging for server.

    Args:
        log_file: Path to log file
        level: Logging level (default: INFO)

    Returns:
        File handler that was added
    """
    # Get root logger
    root_logger = logging.getLogger()

    # Remove existing handlers (console output)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add file handler
    file_handler = logging.FileHandler(log_file, mode="a")
    file_handler.setLevel(level)

    # Format with timestamp
    formatter = logging.Formatter(
        "[%(asctime)s] - %(levelname)s - %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    file_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.setLevel(level)

    return file_handler


def restore_console_logging():
    """Restore console logging (remove file handler)."""
    root_logger = logging.getLogger()

    # Remove file handlers
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            handler.close()
            root_logger.removeHandler(handler)

    # Add console handler back
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "\x1b[97;20m%(asctime)s\x1b[0m - "
        "\x1b[38;20m%(levelname)s\x1b[0m - "
        "\x1b[97;20m%(name)s\x1b[0m - "
        "\x1b[38;20m%(message)s\x1b[0m",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


async def start_inline_repl(
    patcher: "Patcher",
    server: "InteractivePatcherServer",
    log_file: Optional[Path] = None,
) -> None:
    """Start inline REPL with server running in background.

    Args:
        patcher: Patcher instance
        server: Server instance (already started)
        log_file: Optional log file path (default: outputs/py2max_server.log)

    Example:
        >>> p = Patcher('demo.maxpat')
        >>> server = await serve_interactive(p, port=8000)
        >>> await start_inline_repl(p, server, Path('server.log'))
    """
    from .repl import start_repl

    # Setup log file
    if log_file is None:
        log_file = Path("outputs/py2max_server.log")

    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Redirect logging to file
    file_handler = setup_file_logging(log_file)

    # Print info about logging
    print()
    print("=" * 70)
    print("py2max Inline REPL (Single Terminal Mode)")
    print("=" * 70)
    print()
    print(f"Server logs redirected to: {log_file}")
    print(f"To monitor logs: tail -f {log_file}")
    print()
    print("Starting REPL...")
    print()

    try:
        # Start REPL in foreground
        await start_repl(patcher, server)

    finally:
        # Restore console logging
        file_handler.close()
        restore_console_logging()


class BackgroundServerREPL:
    """REPL with server running in background thread.

    This class manages the server lifecycle in a background thread
    while the REPL runs in the foreground.
    """

    def __init__(
        self,
        patcher: "Patcher",
        port: int = 8000,
        log_file: Optional[Path] = None,
        auto_open: bool = True,
        auto_save: bool = False,
    ):
        self.patcher = patcher
        self.port = port
        self.auto_open = auto_open
        self.auto_save = auto_save
        self.log_file = log_file or Path("outputs/py2max_server.log")
        self.server: Optional[InteractivePatcherServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.ready_event = threading.Event()
        # The long-lived task owning the server, so stop() has something
        # concrete to cancel from the main thread.
        self._server_task: Optional[asyncio.Task] = None

    def notify_update_sync(self):
        """Synchronously notify server (running in background thread) of updates.

        This method is called from the REPL (main thread) and schedules
        the notify_update coroutine in the server's event loop.
        """
        if self.loop and self.server:
            # Schedule coroutine in the server's event loop
            future = asyncio.run_coroutine_threadsafe(
                self.server.notify_update(), self.loop
            )
            # Wait for completion (with timeout to avoid hanging)
            try:
                future.result(timeout=2.0)
            except Exception as e:
                print(f"Warning: Failed to notify server: {e}", file=sys.stderr)

    def _run_server(self):
        """Run server in background thread."""
        # Create new event loop for this thread
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def run():
            # Start server. py2max removed ``Patcher.serve()`` in v0.3.0 when
            # this package was split out, so go through serve_interactive
            # directly -- the old call left this whole mode dead.
            from .websocket import serve_interactive

            self.server = await serve_interactive(
                self.patcher,
                port=self.port,
                auto_open=self.auto_open,
                auto_save=self.auto_save,
            )

            self._server_task = asyncio.current_task()

            # Signal that server is ready
            self.ready_event.set()

            # Keep running
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                # Shutdown requested from stop(); release the ports before the
                # loop closes, then let the cancellation finish unwinding.
                await self.server.stop()
                raise

        # Run server
        try:
            self.loop.run_until_complete(run())
        except asyncio.CancelledError:
            pass  # normal shutdown path
        except Exception as e:
            print(f"Server error: {e}", file=sys.stderr)
        finally:
            self.loop.close()

    async def start(self):
        """Start server in background and REPL in foreground."""
        # Setup logging
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = setup_file_logging(self.log_file)

        # Start server in background thread
        self.server_thread = threading.Thread(
            target=self._run_server,
            daemon=True,
        )
        self.server_thread.start()

        # Wait for server to be ready
        self.ready_event.wait(timeout=10)

        if not self.ready_event.is_set():
            raise RuntimeError("Server failed to start")

        print()
        print("=" * 70)
        print("py2max Inline REPL (Single Terminal Mode)")
        print("=" * 70)
        print()
        print("Server running:")
        print(f"  HTTP: http://localhost:{self.port}")
        print(f"  WebSocket: ws://localhost:{self.port + 1}")
        print()
        print(f"Server logs: {self.log_file}")
        print(f"To monitor: tail -f {self.log_file}")
        print()
        print("Starting REPL...")
        print()

        try:
            # Start REPL in foreground with refresh() function
            from .repl import start_repl_with_refresh

            assert self.server is not None, "Server failed to start"
            await start_repl_with_refresh(
                self.patcher, self.server, self.notify_update_sync
            )

        finally:
            # Cleanup
            self.stop()
            file_handler.close()
            restore_console_logging()

    def stop(self):
        """Stop the background server and wait for its thread to unwind.

        Called from the main (REPL) thread, so the cancellation has to be
        handed to the server's own loop via ``call_soon_threadsafe``. The
        previous version called ``asyncio.all_tasks(self.loop)`` and cancelled
        directly from this thread, which is not thread-safe and -- because it
        never woke the loop -- left the server running and the ports held.
        """
        loop, task = self.loop, self._server_task
        if loop is None or task is None or loop.is_closed():
            return

        loop.call_soon_threadsafe(task.cancel)
        if self.server_thread is not None:
            self.server_thread.join(timeout=5)


async def start_background_server_repl(
    patcher: "Patcher",
    port: int = 8000,
    log_file: Optional[Path] = None,
    auto_open: bool = True,
    auto_save: bool = False,
) -> None:
    """Start REPL with server in background (single terminal mode).

    Args:
        patcher: Patcher instance
        port: Server port (default: 8000)
        log_file: Log file path (default: outputs/py2max_server.log)
        auto_open: Open a browser on the editor page (honours ``--no-open``)
        auto_save: Write edits back to the file automatically (opt in; the
            default leaves the patch on disk untouched until an explicit save)

    Example:
        >>> p = Patcher('demo.maxpat')
        >>> await start_background_server_repl(p, port=8000)
    """
    repl = BackgroundServerREPL(
        patcher, port, log_file, auto_open=auto_open, auto_save=auto_save
    )
    await repl.start()


__all__ = [
    "start_inline_repl",
    "start_background_server_repl",
    "BackgroundServerREPL",
    "setup_file_logging",
    "restore_console_logging",
]
