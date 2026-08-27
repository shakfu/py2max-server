# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1]

Objects can be resized in the editor. Additive only -- nothing in 0.3.0 changes behaviour.

### Added

- **Box resizing.** Select an object to get handles on its right edge, bottom edge and bottom-right corner; drag one to resize, with the live `w x h` shown in the status line. Multi-select resizes the whole selection by the same delta. A drag commits once on release, so it costs a single undo step rather than one per frame.

  Handles sit just outside the box outline, clear of the port row -- flush on the corner they would overlap the last outlet and, being drawn after the ports, intercept the clicks meant to start a connection from it.

  Only `w`/`h` change; a box never moves as a side effect of being resized. That is why no top or left handles are offered: dragging one would have to shift the box's origin too. Both ends clamp to a usable minimum, since the message schema validates types and not ranges, and the browser is not the authority.

  New protocol messages: `resize_object` (answered with a `size_update` delta) and `resize_objects` (batch, answered with full state, mirroring `update_positions`).

- Resizing is deliberately **ungated** -- every object can be resized. `docs/box-resize.md` has the full analysis; the short version is that maxref carries no resizability field and every proxy for one is unusable. UI-ness is spelled four different ways with no shared token, 32% of objects have a malformed `tag` that cannot be classified at all, and `jsui`, `bpatcher` and `playlist~` -- all resizable in Max -- carry no UI tag whatsoever. An allowlist built from that data would silently refuse legitimate edits with no override. The failure modes are asymmetric: wrongly allowing a resize is self-correcting, since Max reasserts its own size on load and undo is one press away, while wrongly blocking one just looks like a broken editor.

### Fixed

- Tab completion tests no longer pin the exact text `rlcompleter` returns. From Python 3.10 it closes the parens on a zero-argument callable (`optimize_layout()`) where 3.9 emits only `optimize_layout(`, so the assertions passed locally and failed CI on the oldest supported interpreter. They now assert on the completed name. No behaviour changed -- completion itself worked on 3.9 throughout.

## [0.3.0]

The REPL moves into the browser. The standalone terminal client is gone, and with it the two things that made it awkward to use: copying a session token by hand and keeping a second terminal open. Pressing **Console** in the editor now gives a Python prompt attached to the live patcher, with tab completion.

This is a **breaking change** for anyone running `py2max-server repl` or importing `ReplClient`, `start_repl_client`, `start_repl`, `start_repl_with_refresh`, `AutoSyncWrapper` or `BackgroundServerREPL`. The package is ~1000 lines lighter and no longer depends on `ptpython`.

### Added

- **Python console in the editor.** A Console button in the toolbar opens a prompt attached to the live patcher, with `p` bound to the `Patcher` and the usual helpers (`save`, `info`, `layout`, ...). It authenticates with the session token already embedded in the editor page and talks to the same RPC server the terminal client used, so there is nothing to copy and no second terminal. Output, tracebacks and Up/Down history all work in-page, and evaluated changes now push fresh state to every open editor rather than leaving the canvas showing the pre-eval patch.

- **Tab completion in the console**, resolved server-side by stdlib `rlcompleter` against the same namespace `eval` uses -- so it completes exactly the names that will actually resolve: `p.` attributes, the command helpers, builtins and keywords. Only the trailing dotted identifier is completed, so `x = p.opt` works mid-line. A unique match is inserted, several matches extend to their common prefix and list the candidates, and completion failures are swallowed rather than allowed to break the session.

### Removed

- **The standalone terminal REPL.** `py2max-server repl`, the `--repl` / `--log-file` single-terminal mode, `ReplClient`, `start_repl_client`, `start_repl`, `start_repl_with_refresh`, `AutoSyncWrapper`, `BackgroundServerREPL` and the `ptpython` dependency are all gone, along with `client.py` and `inline.py` (~1000 lines). The console above replaces them and removes the two things that made the terminal REPL awkward: copying a session token by hand and running a second terminal.

  `ReplCommands` stays -- it backs the console namespace. Anyone importing the removed names or scripting `py2max-server repl` will need to move to the in-editor console.

### Fixed

- **The breadcrumb walk always terminates.** Building the patcher path followed `_parent` links with no bound, so a self-referential chain would spin forever and take the event loop with it. It is now depth-limited and cycle-guarded. Found when pushing state after console evaluation hung the test suite against a mocked server whose attributes auto-create.

## [0.2.2]

### Added

- **Straighten button** in the layout sidebar. Nudges boxes sideways so patch cords run perpendicular, leaving vertical positions untouched -- a tidy-up for a layout you are otherwise happy with, rather than a re-layout. Inlets and outlets always sit on the top and bottom edges, so a cord is perpendicular exactly when its two ports share an x coordinate.

  Each box is aligned against its primary feed -- the cord landing on its lowest-numbered inlet, which is the main input by Max convention. Fan-outs and fan-ins are not special-cased; the only thing that stops a move is a collision, so where one outlet feeds two boxes at the same height the first claims the column and the second keeps its place. Boxes are walked downstream from the roots, so a shift propagates along a chain. The whole operation is a single undo step.

### Changed

- **Auto-save is now opt-in.** `py2max-server serve` no longer writes edits back to the input file automatically; pass `--auto-save` to restore that behaviour. Serving a patch is a read operation until you ask otherwise. Saving on request is unchanged and unaffected: the editor's Save button and the REPL's `save()` both still write immediately.

  The old default meant anything the browser did -- including actions the user never asked for, such as the layout-on-open described below -- reached disk about two seconds later. That combination rewrote a tracked example patch during testing, and the single-terminal REPL mode had auto-save hardcoded on regardless of any flag.

  The legacy `--no-save` flag is still accepted (hidden) so existing commands keep working, and still forces auto-save off, so `--auto-save --no-save` resolves in favour of not touching the file.

- The layout sidebar's blue left border is gone; its drop shadow already separates it from the canvas.

- The sidebar is resizable by dragging its left edge, with double-click to reset. The width lives in a `--sidebar-width` CSS variable so the panel, its hidden offset and the canvas padding stay in step, and it persists in `localStorage` next to the existing mode preference.

- Slider readouts in the layout controls sit to the right of their slider instead of underneath it, hard against the left edge.

### Fixed

- **Save writes back to the file being served.** The Save button called the *Save As* path, which serializes the patch and hands it to the browser -- so pressing Save produced a copy in `~/Downloads` and never touched the patch being edited. It now sends a `save` message; the server writes to its own path and replies `save_complete`, falling back to a browser Save As only when it has no path (an uploaded patch). With auto-save opt-in, this had left the editor unable to persist anything at all.

- **Save As is now its own toolbar button**, so saving a copy elsewhere stays available rather than being reachable only as a fallback.

- **Ports are placed the way Max places them.** Inlets and outlets were spread evenly along a box edge (`w / (count + 1)`), which insets every port; Max instead anchors the outermost ports near the corners, leaving a small margin, and divides the span between. The difference grew with box width -- on a 157px `cycle~ 440.` the two inlets sat 78px apart instead of at the ends. The previous behaviour is kept as a **Port Placement** option in the layout sidebar (Max / Even), defaulting to Max and persisted per browser; it applies on change rather than waiting for Apply Layout, since it only affects drawing.

  The geometry now lives in a single `portCenterX()` helper. It had been duplicated across five sites -- drawing, drag updates, animation interpolation and both patch cord endpoints -- which is how the drawn ports and the cords attached to them could drift apart.

- **Opening the layout panel no longer rearranges the patch.** The toolbar's Auto-Layout button applied a layout the moment the controls became visible, so merely *looking* at them repositioned every object -- and with auto-save on, that reached disk about two seconds later. It was observed rewriting the tracked `examples/example.maxpat` with layout-engine coordinates (objects pushed to negative positions) without the user asking for anything. Applying a layout is now only ever the explicit "Apply Layout" button, and `tests/test_editor_assets.py` guards against a second call site reappearing.

## [0.2.1]

A repair release. Live editing did not persist: the served patcher was never bound to its own file, so both the REPL's `save()` and the advertised auto-save silently discarded every change. The single-terminal REPL mode had been dead since v0.3.0 of py2max removed the API it called.

Requires py2max >= 0.4.1, which fixes rect handling for patches loaded from disk; without it any layout call on a served patch raises `AttributeError`.

### Fixed

- **Live edits are saved again.** `py2max-server serve` loaded the patch with `Patcher.from_file(path)` and no `save_to=`, so the patcher was never bound to its own file: `Patcher._path` stayed `None` and `patcher.save()` returned without writing. Every edit made in the REPL or the browser editor was therefore discarded on exit, and `save()` in the REPL answered "No filepath set" even though the server had been started from a real file.

- **`--no-save` now controls auto-save, and the banner tells the truth.** `serve_interactive` was called without `auto_save`, whose default is `False`, so auto-save was off even while the startup output announced "Auto-save enabled: changes will be saved to ...".

- **Single-terminal mode (`serve --repl --log-file`) starts again.** It called `Patcher.serve()`, which py2max removed in v0.3.0 when this package was split out, so the mode had been dead since then -- it failed with `RuntimeError: Server failed to start` after a 10-second timeout. It now goes through `serve_interactive` directly, and honours `--no-open`.

- **The background server shuts down cleanly.** `BackgroundServerREPL.stop()` called `asyncio.all_tasks(loop)` and cancelled from the main thread; that is not thread-safe and never woke the server's loop, so the thread outlived the REPL and kept its ports bound. Cancellation is now handed to the server's own loop with `call_soon_threadsafe`, and `stop()` waits for the thread.

- **Command history works in the remote REPL client.** The `PromptSession` was constructed inside the input loop, so every prompt got a fresh, empty history and up-arrow recall never returned anything. It is now built once per session.

- The REPL banner no longer advertises `exit()`. ptpython's own `exit()` raises a `ReplExit` that its REPL loop catches and renders as a traceback, leaving you back at the prompt; Ctrl-D is the working exit.

- **Breadcrumbs name the subpatcher you are in.** Max stores a subpatcher's name on the parent box (`p upper`), not on the nested patcher, so reading `title` alone labelled every level "Main" -- a trail reading "Main > Main > Main", correct about depth and useless about location. The label now falls back to the owning box's text with the `p`/`patcher` prefix stripped.

### Changed

- Dropped the unused `RemoteEvaluator`, spare namespace dicts and dead `configure()` hooks from the REPL client, which described a ptpython integration the client does not use -- it drives `prompt_toolkit` directly, because evaluation has to be routed over the websocket.

### Added

- `tests/test_serve_persistence.py`: end-to-end coverage for the serve/REPL path, including a real websocket round trip that edits a patch and asserts the change reaches disk. The previous suite mocked the patcher and server, so it passed while all of the above were broken.

- `tests/test_editor_protocol.py`: end-to-end coverage for the browser editor's websocket protocol against a real server, real client and real fixture patches -- create/move/connect/retitle/delete, undo/redo, batch move and delete, subpatcher navigation and editing, export, and auth rejection. Includes a fidelity guard that opens each fixture (including one with nested subpatchers), saves it with no edits, and asserts the file is unchanged; silent corruption on open+save would matter more than any missing editor feature.

## [0.2.0]

### Added

- **Inlet/outlet tooltips from the maxref database.** Hovering a port shows its name (e.g. `cycle~` inlet 0 -> "Frequency"), resolved from py2max's `maxref` object reference and cached per object. Ports fall back to a generic "Inlet N" / "Outlet N" where maxref has no usable entry (unrecognized objects, abstractions, message/comment boxes). Labels reflect the base object, so arg-driven port changes on objects like `pack` or `zl` are not specialized.

- **Undo/redo.** Undo and redo any edit (create, delete, move, connect, edit text, and group operations) at any subpatcher depth, via Undo/Redo buttons or Cmd/Ctrl-Z and Cmd/Ctrl-Shift-Z (Ctrl-Y also redoes). Implemented as server-side snapshots so the patch on the server stays the single source of truth; history is bounded and resets when a different file is opened.

- **Drag-to-connect with a live cord.** Press a port and drag a rubber-band cord to a target; compatible (opposite-type) ports highlight, and the one under the cursor is emphasized. Releasing on a compatible port makes the connection. The original two-click connect still works for a press-release without a drag.

- **Multi-select.** Rubber-band marquee selection and shift-click to toggle individual objects. Dragging moves the whole selection together and Delete removes it, each as a single undo step.

- **Create-object modal with typeahead.** Creating an object now opens an in-page dialog with a type-ahead list of common Max objects (instead of a blocking browser prompt), and places the new object where you double-clicked.

- **Server-side file picker.** The Open button now lists `.maxpat` files and folders on the server in a modal (browsable, with an "up" entry) and opens the chosen file by its real path (`list_patches` -> `open`), so the opened patch can be saved back in place. This removes the browser file-API dependency and fixes Open in Safari. The native browser upload remains as a fallback.

- **Pan and zoom.** Mouse wheel zooms toward the cursor (the point under the cursor stays fixed); Space-drag or middle-mouse pans. A "Fit" button and the `f` key re-frame the whole patch. Manual zoom/pan persists across edits to the same patcher; auto-fit re-engages when opening or navigating patchers and after Auto-Layout / Reset Layout.

- **Clickable breadcrumb.** Each ancestor in the breadcrumb is now clickable and jumps directly to that patcher (root via `navigate_to_root`, intermediate levels via a new `navigate_up` message that ascends in a single step).

### Changed

- **Dragging an object updates only that object** (and its connected patchlines) instead of re-rendering the entire canvas on every mouse move, and the dragged box keeps its selection afterward so it can be moved and then deleted without re-selecting.

- **WebSocket reconnect uses exponential backoff** (capped, with a bounded number of attempts and a give-up state) instead of retrying every 3 seconds indefinitely.

### Fixed

- **Delete/Backspace no longer acts on the canvas while typing in a form control** (the layout controls or the inline text editor) and only deletes when an object or connection is actually selected, so it no longer blocks browser back-navigation for no reason.

## [0.1.1]

### Added

- **Open a patch from the browser.** Header button opens a native file picker; the browser reads the chosen `.maxpat` and sends its contents to the server, which parses them with `Patcher.from_dict`. A path-based `open` message is also available for programmatic/REPL clients.

- **Save As via a native file dialog.** Save serializes the patch server-side (`Patcher.to_json`) and writes it through the browser: the File System Access API shows the real OS save dialog in Chrome, with a download fallback elsewhere. Replaces the previous text-prompt for a file path.

- **Edit object text in place.** Double-click a plain object to edit its text in an inline field; commits on Enter/blur, cancels on Escape (`edit_object_text`).

- **Hide Comments toggle.** Hides comment annotation nodes from the canvas and excludes them from auto-layout and view fitting. Non-destructive (comments stay in the patch and are saved); preference persists across reloads.

- **Adaptive layout canvas.** Layout/centering canvas scales with the visible object count, so small patches stay compact while large patches get room.

### Changed

- **Default layout engine is now ELK (layered) with top-to-bottom flow**, matching Max's inlet-top/outlet-bottom convention.

- **Layout panel simplified.** Compact defaults baked in; only Layout Engine and Flow Direction are shown by default, with all engine-internal parameters collapsed behind an "Advanced" disclosure.

- **Thin single-row header** that no longer wraps; a `ResizeObserver` keeps the sidebar panel and canvas aligned to the header's true height at any width.

- **Auto-open prefers Chrome** (falling back to the OS default), since the Open file-upload flow is unreliable in Safari.

- **Static assets are served with `Cache-Control: no-cache`** so editor changes are always picked up on reload.

### Fixed

- **Node labels disappearing after Auto-Layout or dragging.** `render()` leaked a duplicate `clipPath` id per box into `<defs>`; the browser resolved the label's clip to a stale, mispositioned copy and clipped the text out of view. `render()` now clears `<defs>`, and delta moves reposition the clip in `<defs>`.

- **Sidebar layout panel overlapping the header** and stretching to full height; it now sizes to its content and never overlaps the header.

- **Reset Layout** no longer restores positions from a previously open patch after opening a new file (its stored positions are cleared on open).

### Removed

- **Dead SSE "Live Preview" client** (`static/index.html`, `static/live-preview.js`, ~490 lines) and the `/index.html` route shadow. It referenced a `/events` endpoint the server never implemented and was unreachable.

### Known issues

- **Open does not work in Safari** — the native file dialog opens but the selected patch does not load. Use Chrome. A server-side file picker is planned to remove the browser file-API dependency entirely.

- Editing an object's text does not re-derive its inlet/outlet counts (e.g. changing `gate` to `gate 4`); the text updates but the ports do not.

## [0.1.0]

- Initial release: interactive WebSocket editor, remote REPL, and Python API split out of the core `py2max` library.
