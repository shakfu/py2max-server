# py2max-server

Browser-based live editor for [py2max](https://github.com/shakfu/py2max) Max/MSP patches, with a Python console attached to the running patch.

This package was split out of the core `py2max` library so the patch generator stays a small, dependency-light, offline tool. Install this only when you want to edit patches in a browser.

## Installation

```bash
pip install py2max-server
```

This pulls in `py2max` (the core generator, 0.4.1 or newer) and `websockets`. Python 3.9 or newer.

> **Upgrading from 0.2.x:** the standalone terminal REPL is gone in 0.3.0. `py2max-server repl` and the `--repl` / `--log-file` flags have been removed, along with `ReplClient` and friends -- the console now lives in the editor itself. See the [changelog](CHANGELOG.md).

## Usage

```bash
py2max-server serve my-patch.maxpat
```

Opens a browser editor for the patch:

- HTTP server on `http://localhost:8000`

- WebSocket on `ws://localhost:8001`

- Python console on `ws://localhost:8002` (used by the editor page itself)

Use `--port` to move all three, and `--no-open` to skip launching a browser.

### Saving

**Serving a patch does not modify it.** Nothing is written until you ask:

- **Save** writes back to the file being served.

- **Save As** writes a copy elsewhere, through a browser file dialog.

- `--auto-save` opts into writing edits back automatically, a couple of seconds after each change.

Auto-save is off by default because an editor action you did not intend should not be able to reach disk.

### Editing

- Drag objects to move them; rubber-band or shift-click to select several, then drag, resize or delete as a group.

- Select an object to get resize handles on its right edge, bottom edge and bottom-right corner. Any object can be resized, though it matters most for UI objects like `scope~`, `function` and `live.gain~`; Max reasserts its own size for objects that are not resizable.

- Drag from one port to another to connect; click a cord to select it.

- Double-click empty canvas to create an object, with type-ahead over common Max objects.

- Double-click an object to edit its text -- or, if it is a subpatcher, to descend into it. The breadcrumb walks back out, as does **Parent** and `Esc`.

- `Delete` removes the selection. Undo/redo with Cmd/Ctrl-Z and Cmd/Ctrl-Shift-Z (or Cmd/Ctrl-Y), at any subpatcher depth.

- `F` fits the patch to the window, the wheel zooms, and space-drag pans.

- **Open** loads another `.maxpat` from the server; **Hide Comments** removes comment boxes from view without deleting them.

There is no keyboard shortcut for saving -- use the **Save** button.

### Layout

The sidebar offers three layout engines (ELK, Dagre, WebCola) with their parameters. Nothing moves until you press **Apply Layout**; **Reset Layout** returns objects to where they were when the patch loaded.

**Straighten** nudges objects sideways so patch cords run perpendicular, leaving vertical positions alone -- a tidy-up rather than a re-layout. Each object aligns to its primary feed, and a move that would collide with another object is skipped.

**Port Placement** switches between Max's own port spacing (anchored near the box corners) and an even spread along the edge.

### Python console

Press **Console** in the toolbar for a Python prompt attached to the live patcher:

```python
>>> len(p._boxes)
8
>>> osc = p.add_textbox('cycle~ 440')
>>> p.add_line(osc, p.find('gain~'))
>>> commands()
```

`p` is the `Patcher`. Edits appear on the canvas immediately; press Save to write them to disk. `commands()` lists the helpers (`save`, `info`, `layout`, `optimize`, `clear`, `help_obj`).

Tab completes against the live namespace -- `p.` attributes, the helpers, builtins -- inserting a unique match or extending to the common prefix and listing the candidates. Up/Down walk the history.

The console runs in the editor page, so there is no second terminal and no token to copy.

## Python API

```python
import asyncio
from py2max import Patcher
from py2max_server import serve_interactive

p = Patcher("demo.maxpat")
p.add("cycle~ 440")

async def main():
    server = await serve_interactive(p, port=8000, auto_open=True)
    await asyncio.sleep(60)
    await server.stop()

asyncio.run(main())
```

`serve_interactive` starts the editor only. The console is a second server on its own port, so add `start_repl_server` if you want the Console button to connect -- the `serve` command does this for you:

```python
from py2max_server import serve_interactive, start_repl_server

server = await serve_interactive(p, port=8000, auto_open=True)
console = await start_repl_server(p, server, port=8002)   # editor expects port + 2
...
console.close()
await console.wait_closed()
await server.stop()
```

Pass `auto_save=True` to `serve_interactive` for the equivalent of `--auto-save`.

## Security

The console executes code (`eval`/`exec`) sent over its socket. Every connection -- editor and console alike -- must present the session token the server generates at startup; unauthenticated connections are refused.

That token is embedded in the editor page, so anything able to fetch `http://localhost:8000` can obtain it. **Bind to localhost and treat the whole server as trusted-local-user-only.** Do not expose these ports to a network.

## Relationship to py2max

`py2max` (core) generates `.maxpat` files offline and has no knowledge of this server. `py2max-server` depends on `py2max` and operates on `Patcher` objects. Generating patches needs only `py2max`; editing them in a browser needs this package.

## Development

```bash
make serve          # serve examples/example.maxpat
make demo           # serve a scratch copy, leaving the tracked example alone
make test
make qa             # lint, format, typecheck, test
```
