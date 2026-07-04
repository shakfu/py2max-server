# py2max-server

Interactive WebSocket server and remote REPL for live editing of [py2max](https://github.com/shakfu/py2max) Max/MSP patches.

This package was split out of the core `py2max` library so the patch generator stays a small, dependency-light, offline tool. Install this package only when you want a browser-based live editor or an interactive REPL.

## Installation

```bash
pip install py2max-server
```

This pulls in `py2max` (the core generator) plus `websockets` and `ptpython`.

## Usage

### Browser live editor

```bash
py2max-server serve my-patch.maxpat
```

Opens a browser-based editor that syncs bidirectionally with the patch file:
- HTTP server on `http://localhost:8000`

- WebSocket on `ws://localhost:8001`

- Remote REPL on `localhost:8002`

### Remote REPL

In a second terminal, connect a REPL client (the token is printed by the server, or set `PY2MAX_REPL_TOKEN`):

```bash
py2max-server repl localhost:8002 --token <session-token>
```

### Python API

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

## Security

The remote REPL executes code (`eval`/`exec`) sent over its socket. Every connection must authenticate with the session token the server generates; an unauthenticated connection is refused. Bind to `localhost` and treat the token as a secret.

## Relationship to py2max

`py2max` (core) generates `.maxpat` files offline and has no knowledge of this server. `py2max-server` depends on `py2max` and operates on `Patcher` objects. Generating patches needs only `py2max`; serving them needs this package.
