# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
