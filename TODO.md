# TODO

The interactive server and REPL were split out of the core [`py2max`](https://github.com/shakfu/py2max) library into this package (py2max 0.3.0). These items were tracked in py2max's TODO and moved here.

## High Priority

### Security & Reliability

- [ ] Move session token from HTML source to HttpOnly cookies

- [ ] Add rate limiting on WebSocket messages

- [ ] Fix race condition in `_debounced_save` (use snapshot during save)

## Medium Priority

### Server & WebSocket

- [ ] Make WebSocket port configurable (currently hardcoded as `port + 1`)

- [ ] Add REPL reconnection logic on transient failures

- [ ] Add command history persistence across sessions

- [ ] Implement diff-based SVG updates (not full re-render)

- [ ] Use proper logging instead of `print()` for tokens

### Live auto-layout (client-side)

Scope split: py2max owns batch (end-of-creation) layout; py2max-server owns live, per-edit layout. The earlier server-side incremental path (driving `Patcher.optimize_layout(changed_objects)` from the edit handlers) was retired because it leaned on py2max's batch engine, ignored the browser's engine/param choices, and reshuffled the whole patch on small edits. See [`docs/auto-layout.md`](docs/auto-layout.md) for the full plan.

A client-side live/continuous auto-layout toggle was prototyped and **dropped before release** -- no variant felt effective on hand-arranged patches (details in [`docs/auto-layout.md`](docs/auto-layout.md)). The shipping editor keeps only the on-demand "Auto-Layout" / "Apply Layout" button, which works well.

Deferred as future design work:

- [ ] Decide a concrete interaction rule for live placement (e.g. "place a new object just below the selected one; move nothing else") before building again.

- [ ] Real incremental layout would need engine-side support, not a full whole-patch relayout on each edit.

## Low Priority

### Testing

- [ ] Improve server test coverage (62% -> 80%+)

- [ ] Add WebSocket integration tests

### Documentation

- [ ] Add JSDoc to `interactive.js`
