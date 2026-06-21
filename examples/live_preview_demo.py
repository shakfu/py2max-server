"""Demonstration of the py2max-server live preview server.

This example shows how to use the interactive server to see patches update in
real time as you build them in Python, over the WebSocket transport.

Usage:
    python examples/live_preview_demo.py
    python examples/live_preview_demo.py context

Features demonstrated:
    - Starting the interactive server (serve_interactive)
    - Real-time updates as objects are added (await server.notify_update())
    - Automatic browser opening
    - Async context manager for automatic cleanup

For an interactive REPL against a live patch, see repl_quickstart.py.
"""

import asyncio
import sys

from py2max import Patcher

from py2max_server import serve_interactive


async def demo_basic_live_preview():
    """Basic live preview demonstration."""
    print("=" * 70)
    print("py2max-server Live Preview Demo - Basic")
    print("=" * 70)
    print()

    # Create a patcher and start the live server.
    print("1. Creating patcher and starting live preview server...")
    p = Patcher("live_demo.maxpat", layout="grid")

    server = await serve_interactive(p, port=8000, auto_open=True)
    print("   Server started at: http://localhost:8000")
    print("   Browser should have opened automatically")
    print()

    # Add objects with delays so the browser updates are visible.
    print("2. Adding objects (watch the browser update in real-time)...")

    print("   Adding metro...")
    metro = p.add_textbox("metro 500")
    await server.notify_update()
    await asyncio.sleep(1)

    print("   Adding cycle~...")
    osc = p.add_textbox("cycle~ 440")
    await server.notify_update()
    await asyncio.sleep(1)

    print("   Adding gain~...")
    gain = p.add_textbox("gain~ 0.5")
    await server.notify_update()
    await asyncio.sleep(1)

    print("   Adding ezdac~...")
    dac = p.add_textbox("ezdac~")
    await server.notify_update()
    await asyncio.sleep(1)

    # Add connections.
    print()
    print("3. Adding connections...")
    p.add_line(metro, osc)
    p.add_line(osc, gain)
    p.add_line(gain, dac)
    p.add_line(gain, dac, inlet=1)
    await server.notify_update()
    await asyncio.sleep(1)

    # Optimize layout.
    print()
    print("4. Optimizing layout...")
    p.optimize_layout()
    await server.notify_update()
    await asyncio.sleep(2)

    print()
    print("=" * 70)
    print("Demo complete! The patch is visible in your browser.")
    print("The server will continue running. Press Ctrl+C to stop.")
    print("=" * 70)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
        await server.stop()


async def demo_context_manager():
    """Demonstration of the async context manager for automatic cleanup."""
    print("=" * 70)
    print("py2max-server Live Preview - Context Manager Demo")
    print("=" * 70)
    print()
    print("This demo shows how to use the server as an async context manager")
    print("for automatic cleanup when the context exits.")
    print()

    p = Patcher("context_demo.maxpat", layout="grid")

    print("1. Using context manager for automatic cleanup...")
    print()

    # auto_open=False keeps this usable in automated/headless runs.
    async with await serve_interactive(p, port=8002, auto_open=False) as server:
        print("   Server started at: http://localhost:8002")
        print("   Building patch...")

        osc = p.add_textbox("cycle~ 440")
        gain = p.add_textbox("gain~ 0.5")
        dac = p.add_textbox("ezdac~")
        p.add_line(osc, gain)
        p.add_line(gain, dac)
        p.optimize_layout()
        await server.notify_update()
        await asyncio.sleep(0.5)

        print()
        print("   Patch complete! Exiting context...")

    # Server is automatically stopped here.
    print()
    print("=" * 70)
    print("Context exited - server automatically stopped!")
    print("=" * 70)
    print()


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "basic"
    if mode == "context":
        asyncio.run(demo_context_manager())
    elif mode in ("basic", ""):
        asyncio.run(demo_basic_live_preview())
    elif mode == "interactive":
        print(
            "For an interactive REPL against a live patch, run "
            "repl_quickstart.py or `py2max-server serve <patch> --repl`."
        )
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python examples/live_preview_demo.py [basic|context]")


if __name__ == "__main__":
    main()
