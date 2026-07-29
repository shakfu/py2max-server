# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Live edits are saved again.** `py2max-server serve` loaded the patch with `Patcher.from_file(path)` and no `save_to=`, so the patcher was never bound to its own file: `Patcher._path` stayed `None` and `patcher.save()` returned without writing. Every edit made in the REPL or the browser editor was therefore discarded on exit, and `save()` in the REPL answered "No filepath set" even though the server had been started from a real file.

- **`--no-save` now controls auto-save, and the banner tells the truth.** `serve_interactive` was called without `auto_save`, whose default is `False`, so auto-save was off even while the startup output announced "Auto-save enabled: changes will be saved to ...".

- **Single-terminal mode (`serve --repl --log-file`) starts again.** It called `Patcher.serve()`, which py2max removed in v0.3.0 when this package was split out, so the mode had been dead since then -- it failed with `RuntimeError: Server failed to start` after a 10-second timeout. It now goes through `serve_interactive` directly, and honours `--no-open`.

- **The background server shuts down cleanly.** `BackgroundServerREPL.stop()` called `asyncio.all_tasks(loop)` and cancelled from the main thread; that is not thread-safe and never woke the server's loop, so the thread outlived the REPL and kept its ports bound. Cancellation is now handed to the server's own loop with `call_soon_threadsafe`, and `stop()` waits for the thread.

- **Command history works in the remote REPL client.** The `PromptSession` was constructed inside the input loop, so every prompt got a fresh, empty history and up-arrow recall never returned anything. It is now built once per session.

- The REPL banner no longer advertises `exit()`. ptpython's own `exit()` raises a `ReplExit` that its REPL loop catches and renders as a traceback, leaving you back at the prompt; Ctrl-D is the working exit.

### Changed

- Dropped the unused `RemoteEvaluator`, spare namespace dicts and dead `configure()` hooks from the REPL client, which described a ptpython integration the client does not use -- it drives `prompt_toolkit` directly, because evaluation has to be routed over the websocket.

### Added

- `tests/test_serve_persistence.py`: end-to-end coverage for the serve/REPL path, including a real websocket round trip that edits a patch and asserts the change reaches disk. The previous suite mocked the patcher and server, so it passed while all of the above were broken.

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
