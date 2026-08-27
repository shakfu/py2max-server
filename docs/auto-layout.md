# Auto-Layout: Scope Split and Change Plan

Status: release cleanup done; client-side live layout is future work Audience: py2max and py2max-server maintainers

## Progress summary

- **Done (py2max):** removed all interactive/incremental layout machinery; `optimize_layout()` is now a batch-only, whole-patch API (see 4.1).

- **Done (py2max-server):** retired the server-side live relayout path and the `--auto-layout` flag (see 4.2, 5).

- **Dropped for the release (py2max-server):** a client-side "Live Auto-Layout" toggle was prototyped but **removed before release** -- no variant felt effective on hand-arranged patches (see 4.2). On-demand layout (the toolbar "Auto-Layout" / "Apply Layout" button) is retained and is the shipping behaviour. Live layout is deferred as future design work.

## 1. Scope boundary

Layout responsibilities split cleanly between the two projects:

- **py2max** owns **batch layout**: arrange a whole patch once, typically at the end of programmatic patch creation. Input is a complete patch; output is a good static arrangement. No notion of "an edit just happened."

- **py2max-server** owns **live / auto-layout**: incrementally re-arrange the patch in response to individual edits while a user works in the browser UI.

The current implementation violates this boundary: the server implements live layout by calling back into py2max's batch engine (`Patcher.optimize_layout(changed_objects)`) after every structured edit. That couples the interactive feature to a batch engine that was not built for it, pushes an "incremental" burden into py2max (out of its scope), and prevents the browser's engine and parameter choices from having any effect on live layout.

This document describes the target design that honors the split, plus the concrete changes required in each project.

## 2. Current state (as of this writing)

### 2.1 py2max

py2max already has incremental scaffolding in `layout/base.py`:

- `get_affected_objects(changed)` returns the changed objects plus their immediate patchline neighbours.

- `should_use_incremental(changed)` chooses incremental vs full using the ratio `len(affected) < total * INCREMENTAL_THRESHOLD` (`INCREMENTAL_THRESHOLD = 0.3`).

- A generic `_incremental_layout(affected)` nudges affected objects to nearby non-overlapping positions and runs `prevent_overlaps()`.

Per-manager behavior:

| Manager                         | Honors `changed_objects` | Mechanism                         |
| ------------------------------- | ------------------------ | --------------------------------- |
| Horizontal (default), Vertical, Grid | Yes                 | inherits base `_incremental_layout` |
| Flow                            | Yes                      | inherits base `_incremental_layout` |
| Matrix, Columnar                | No                       | always full recompute (`matrix.py`) |

Known weaknesses of the incremental path (relevant only if py2max were to keep owning live layout, which this plan rejects):

- The base `_incremental_layout` is structurally unaware: it finds a nearby free slot per object rather than placing objects by the manager's own rules (grid cells, flow order, clustering). Incremental results therefore **drift** from what a full `optimize_layout(None)` would produce.

- The `0.3` ratio threshold fails on small patches: adding one connection on a 3-object patch touches 2 objects (66% > 30%), forcing a **full** relayout on exactly the small patches used in interactive/demo sessions.

- Matrix/Columnar ignore `changed_objects` entirely.

### 2.2 py2max-server

- Server side (`websocket.py`): handles a `set_auto_layout` message, tracks an `auto_layout` flag, advertises `{"type": "auto_layout", "enabled": ...}` on connect and on change, and calls `_maybe_auto_layout(changed)` from the create / connect / delete handlers, which delegates to `patcher.optimize_layout(changed)`. The `--auto-layout` CLI flag sets the initial state.

- Browser side (`static/interactive.js`, `interactive.html`): the layout panel offers an engine selector (cola / elk / dagre), flow direction, and per-engine params. These drive a **client-side** one-shot layout (`autoLayout()` and the `colaAutoLayout` / `elkAutoLayout` / `dagreAutoLayout` methods) reachable via the toolbar "Auto-Layout" button and the "Apply Layout" button. None of these choices are sent to the server, so they have no effect on the server's live relayout.

- The browser already persists positions to the server via the `update_position` / `update_positions` messages (this is how manual drags are saved), and the server auto-saves.

Net effect today: with live auto-layout on, the server re-arranges using its default manager, ignoring every control in the layout panel; the "incremental" relayout is either a crude overlap nudge or (matrix/columnar) a full reshuffle on every edit. The feature is not useful in its current form.

## 3. Target design

**Live auto-layout is a client-side feature. py2max stays batch-only.**

Rationale: the browser already has the right engines (cola / elk / dagre), already exposes them with configurable parameters, and already has a persistence path to the server (`update_positions`). Live layout is therefore a browser concern that only needs a trigger and a good incremental mode; it should not round-trip through py2max.

Data flow (live mode on):

1. User makes a structural edit (create / connect / delete) in the browser.

2. The browser re-runs the **selected** layout engine over the patch.

3. The browser sends the resulting positions to the server via `update_positions`.

4. The server persists (auto-save), exactly as it already does for drags.

Engine notes:

- **cola (WebCola)** is the right engine for live mode: it supports online / constraint layout where unchanged nodes are pinned and only affected nodes are relaxed. That is genuine incremental layout and avoids the whole patch jumping on every edit.

- **elk / dagre** are batch hierarchical engines that re-flow the entire graph. Keep them for the one-shot "Apply Layout" button; in live mode either disable them or warn that they re-flow everything.

Consequences for the UI:

- The layout panel controls (engine, flow direction, params) become the live layout configuration and stay visible while live mode is on. (This reverses the earlier direction of hiding the panel / "Apply Layout" button under live mode.)

- The distinction between the toolbar "Auto-Layout" button (one-shot, browser) and the live toggle should be labeled so the two are not conflated.

## 4. Changes required

### 4.1 py2max (keep it batch-only) -- DONE

`Patcher.optimize_layout()` is now a batch-only, whole-patch API. The interactive/incremental machinery was removed entirely, since it only ever served the live use case that this split reassigns to the server.

Removed:

- `Patcher.optimize_layout`'s `changed_objects` parameter (now takes no args).

- `LayoutManager.should_use_incremental`, `get_affected_objects`, `get_connected_objects`, `_incremental_layout`, `_find_non_overlapping_position`, and the `INCREMENTAL_THRESHOLD` constant (`layout/base.py`).

- The `optimize_layout(changed_objects)` dispatch overrides in `layout/grid.py` and `layout/flow.py` (they now inherit the base batch-only `optimize_layout` and implement `_full_layout`; flow's `<2 objects` guard moved into `_full_layout`).

- `layout/matrix.py`'s `optimize_layout(changed_objects)` became `_full_layout` (inherits the base `optimize_layout`); `ColumnarLayoutManager` inherits it.

Note: this is a breaking signature change. `optimize_layout({ids})` now raises `TypeError`. The forwarding regression test was replaced by `test_optimize_layout_is_batch_only`. All py2max checks pass (611 tests, ruff, mypy). Warrants a minor version bump on py2max.

### 4.2 py2max-server

Server side -- DONE (retired for this release):

- Removed the live relayout path: `set_auto_layout`, `_maybe_auto_layout`, the `optimize_layout(changed_objects)` calls from the create / connect / delete handlers, the `auto_layout` handler/server params, and the `auto_layout` advertise-on-connect message.

- Dropped the `--auto-layout` CLI flag. Rationale: the flag existed only to enable the live path being removed; "one-shot layout on load" would be a different feature that silently rearranges a user's existing patch, so it is not shipped unreviewed. The browser already exposes one-shot layout via the toolbar button, and py2max's batch `optimize_layout()` remains available programmatically and from the REPL (`repl.py` calls it arg-less).

- The retired implementation is preserved as a patch (see 5) in case the future client-side work wants to reference the edit-trigger points.

Client side -- DROPPED for the release:

A "Live Auto-Layout" toggle was prototyped and then removed. The static files were reverted to their committed state; the shipping editor keeps only the on-demand "Auto-Layout" / "Apply Layout" button.

Why it was dropped -- the design space was explored and none of the variants felt effective on real (hand-arranged) Max patches:

- **Server-side incremental** via `optimize_layout(changed_objects)`: reshuffled the whole patch on small edits and ignored the browser's engine/params. (Also removed from py2max, see 4.1.)

- **Client cola with existing objects pinned:** cola ejects a single free node out of a dense pinned cluster -- to a far corner or off-canvas at negative coordinates -- so new objects flew out of view.

- **Client deterministic placement** (dock a new object next to its neighbour, else leave it): non-disruptive, but a lone object dropped into empty space has nowhere meaningful to go, so it looked like nothing happened.

- **Client full relayout on every edit:** unambiguously visible, but it reshuffles every hand-placed object and re-frames the view on each add -- disruptive, and it fights the way patches are actually built.

Conclusion: continuous automatic layout fights hand-arrangement, and a version that genuinely helps needs deeper work (real incremental layout in py2max, or a smarter interaction model). Treated as future design work, not a release blocker.

If revisited, capture a concrete interaction rule first (e.g. "place a new object just below the currently selected object; move nothing else") rather than a generic "keep the patch tidy" goal.

## 5. Release plan (sequencing)

This split is a redesign, not a polish pass, so it did not block the release.

1. **This release -- DONE.**

   - Reverted the in-progress browser toggle work (checkbox, `set_auto_layout` send, the `auto_layout` message handler / `reflectAutoLayout`, and the "Apply Layout" hiding). These were uncommitted working-tree edits.

   - Dropped the `--auto-layout` flag and the server live path (4.2).

   - py2max reduced to batch-only (4.1).

   - The retired server-side implementation (server plumbing + tests) was saved as a git patch before reverting, so nothing is permanently lost: `retired-auto-layout-feature.patch`. Re-apply with `git apply` if the edit-trigger points are useful for the client-side work. (Currently held in the session scratchpad; move it into the repo if longer-term retention is wanted.)

   - A client-side live toggle was prototyped and then **dropped** (4.2); the static files were reverted. Ship the on-demand "Auto-Layout" button only.

2. **Post-release** — if live layout is revisited, start from a concrete interaction rule (see 4.2), not a generic "keep it tidy" goal. Keep py2max batch-only per 4.1.

## 6. Open questions

Resolved:

- `--auto-layout` was **dropped** (see 4.2 for rationale).

- py2max's incremental machinery was **removed**, not kept (see 4.1).

- Client-side live auto-layout was **dropped for the release** after prototyping several variants; on-demand layout ships instead (see 4.2).
