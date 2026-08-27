# Box Resizing: Plan and Gating Recommendation

Status: **implemented** (0.3.1). The recommendation in section 4 was adopted -- resizing is ungated. Section 5 describes what was built, with two deviations noted inline: handles are right/bottom/corner only, and a batch resize broadcasts full state rather than a delta. Audience: py2max and py2max-server maintainers

## Summary

The browser editor can move, connect, retitle, create and delete boxes, but it cannot change a box's **size**. This is not a design decision -- it is simply unimplemented, and nothing in either project records it as deferred or out of scope.

The open question is whether resizing should be offered for every object or restricted to objects Max itself considers resizable. **Recommendation: allow resizing on every object, with no allowlist.** The data needed to build a trustworthy allowlist does not exist in `maxref`, and an unreliable allowlist is worse than none -- it silently blocks legitimate edits with no way for a user to override.

## 1. Current state

### 1.1 There is no resize path anywhere

- **Protocol:** `MESSAGE_SCHEMAS` in `websocket.py` defines 20 message types. None carries a width or height. The closest are `update_position` / `update_positions`, which are strictly `{box_id, x, y}`.

- **Server:** the string `resize` does not appear in `py2max_server/*.py`.

- **Editor:** no resize handles in `static/interactive.js`. The only `resize` occurrences are the header `ResizeObserver` and the sidebar drag handle.

- **History:** no mention in `CHANGELOG.md`, `TODO.md`, `docs/`, or any commit.

### 1.2 Size is already first-class everywhere else

This is what makes the gap conspicuous rather than principled:

- `get_patcher_state_json()` sends true dimensions: `patching_rect: {x, y, w, h}`.

- The editor consumes them (`box.width = box.patching_rect.w`) and feeds them to the layout engines as node sizes.

- py2max carries real per-class defaults from maxref -- `scope~` is 130x130, `function` 200x100, `multislider` 20x140, `dial` 40x40, not a uniform 60x22.

- py2max has a regression test (`tests/test_layout_box_dims.py`) whose sole purpose is asserting that optimizers **preserve real box dimensions**, added after layouts were found squashing UI objects to text-box size.

So the whole stack treats size as meaningful, structural data. Only the editor has no affordance to change it.

### 1.3 Why size preservation in `handle_update_position` is not evidence of intent

The move handler rebuilds the rect keeping `w`/`h`:

```python
# Rect is a NamedTuple (immutable), create new one
box.patching_rect = Rect(float(x), float(y), rect.w, rect.h)
```

The comment explains `Rect` immutability, not a decision that size is fixed. Preserving `w`/`h` is incidental to moving a box.

## 2. Who actually needs this

For `newobj` text boxes, resizing barely matters -- Max auto-sizes them to their text. It matters for UI objects, where dimensions carry meaning: `scope~`, `function`, `multislider`, `dial`, `live.gain~`, `comment`, `panel`, `bpatcher`.

`examples/example.maxpat` alone contains `scope~` and `live.gain~`, both freely resizable in Max and both frozen in this editor.

## 3. The gating question: can we tell what is resizable?

To restrict resizing we would need a reliable "is this object resizable" signal. maxref does not provide one, and the near-miss proxies do not survive contact with the data.

### 3.1 There is no resizability field

`get_object_info()` returns: `attributes`, `category`, `description`, `digest`, `examples`, `inlets`, `metadata`, `methods`, `misc`, `module`, `name`, `objargs`, `outlets`, `palette`, `parameter`, `seealso`. None encodes resizability or size constraints.

### 3.2 The UI tag is the obvious proxy, and it is not trustworthy

`metadata.tag` marks some objects as UI. Measured across all 1175 objects:

| Finding | Count |
| --- | ---: |
| Total maxref objects | 1175 |
| Tagged UI under *any* vocabulary | 70 |
| Objects whose `tag` is a bare string, not a list | 377 |

Three problems, each independently disqualifying:

**Inconsistent vocabulary.** UI-ness is spelled four different ways, with no shared token -- a substring match on `U/I` silently misses every `live.*` object:

| Tag value | Objects |
| --- | ---: |
| `U/I` | 36 |
| `Live UI Objects` | 17 |
| `MSP U/I` | 14 |
| `Jitter UI` | 4 |

**Malformed data.** 377 objects (32% of the corpus) have `tag` as a plain string rather than a list, so iterating it yields single characters. This is why a naive tag census reports nonsense tags like `E`, `r`, `T` and `_` with 90-270 hits each. Those 377 objects cannot be classified by tag at all until the parser is fixed. *(Worth fixing in py2max regardless of this feature -- it is a maxref parsing bug, not just an obstacle here.)*

**False negatives among exactly the objects that need resizing.** Spot checks:

| Object | Resizable in Max | Tagged UI | Notes |
| --- | --- | --- | --- |
| `jsui` | yes | **no** | falls in the 377 malformed-tag set (`'Max'`) |
| `bpatcher` | yes | **no** | falls in the 377 malformed-tag set (`'Max'`) |
| `playlist~` | yes | **no** | well-formed tags, but only `MSP`, `Audio`, `Soundfiles` |
| `scope~` | yes | yes | `MSP U/I` |
| `live.gain~` | yes | yes | `Live UI Objects` |

An allowlist derived from tags would block `jsui`, `bpatcher` and `playlist~` -- three of the objects users most want to resize -- with no user-facing escape hatch. The failure is silent and looks like a bug in the editor.

### 3.3 The alternative, hand-curation, ages badly

A hand-maintained list would work initially and then rot: it must be revisited for every Max release, and it cannot cover third-party externals or abstractions, which have no maxref entry at all and are precisely the objects a patch author is most likely to have sized by hand.

## 4. Recommendation

**Allow resizing on all objects. Do not gate it.**

Reasoning:

1. **The gating data does not exist.** Section 3 shows every available signal is incomplete, inconsistent, or malformed for a third of the corpus.

2. **The failure modes are asymmetric.** Wrongly *allowing* a resize produces a patch Max may render a little oddly, and the user can undo it or drag it back. Wrongly *blocking* one produces an editor that ignores the user with no recourse and no explanation.

3. **Max is already the authority, and it is forgiving.** `patching_rect` is just four numbers in the file. Max reads what it is given and non-resizable objects reassert their own size on load, so an odd value is self-correcting rather than corrupting.

4. **It matches the editor's existing posture.** The editor already lets you connect any outlet to any inlet without consulting maxref's inlet/outlet validation. Adding a strict gate only for size would be inconsistent.

Cheap mitigations that get most of the safety without an allowlist:

- Clamp to a sane minimum (say 8x8) so a box cannot be made unselectable.

- Keep resize on the same undo stack as every other edit, so a mistake is one Cmd-Z away.

- Optionally show the live `w x h` while dragging, so the user sees what they are committing.

If a gate is ever wanted, the honest place for it is a **soft** one: still allow the resize, but flag unusual results in the existing patch linting rather than blocking the interaction.

## 5. Implementation sketch

Small on the server, moderate on the client. The existing undo/persistence machinery does most of the work.

### 5.1 Protocol

```python
"resize_object":  {"box_id": str, "w": (int, float), "h": (int, float)},
"resize_objects": {"sizes": list},   # batch, mirrors update_positions
```

### 5.2 Server handler

Mirrors `handle_update_position` almost exactly:

1. `self._push_undo()` -- one entry per gesture, matching move semantics.

2. Rebuild the rect preserving position: `Rect(rect.x, rect.y, w, h)`, with the same `Rect`-vs-list branch the move handler already has.

3. Broadcast a `size_update` delta (`{box_id, w, h}`), following the `position_update` precedent rather than resending whole state.

4. `await self.schedule_save()` -- debounced persistence for free.

Clamp `w`/`h` to the minimum server-side as well as client-side; the schema validator checks types, not ranges, and the socket is not the browser's to trust.

### 5.3 Client

The larger piece:

- Draw handles on the selected box (corners, or corners plus edges).

- Drag to resize, honouring the current zoom/pan transform when converting pointer deltas to patch coordinates.

- Send `resize_object` on release, not per frame, matching how the drag-to-move path batches.

- Extend multi-select to `resize_objects` so a group resize is a single undo step, as group move already is.

**As built**, with two deviations:

- Handles are right (`e`), bottom (`s`) and bottom-right (`se`) only. A top or left handle has to move the box's origin as well as change its size, and the server's resize deliberately preserves position -- so offering one would mean either a second message per drag or a handle that silently does half its job. A guard in `tests/test_editor_assets.py` keeps `n`/`w`/`nw`/`ne`/`sw` out.

- `resize_objects` broadcasts full state rather than a `size_update` delta, matching `update_positions`. Only the single-box path uses the delta.

### 5.4 Testing

The mocked `test_websocket.py` would not catch a regression here. Follow `test_editor_protocol.py` instead: drive a real server over a real websocket, resize, assert the broadcast delta, save, and assert the new `w`/`h` on disk. Add the resized patch to the existing open-and-save fidelity check so a resize cannot quietly corrupt unrelated fields.

## 6. Related (resolved)

Opening the layout sidebar used to call `autoLayout()` immediately, so merely *viewing* the layout controls rearranged the patch and -- with auto-save on -- wrote it to disk. This was observed rewriting `examples/example.maxpat` with layout-engine coordinates. That auto-apply has since been removed: applying a layout is now only ever the explicit "Apply Layout" button, guarded by `tests/test_editor_assets.py`.

This mattered for the feature above because hand-sized boxes are exactly what an unrequested auto-layout destroys. With geometry now stable unless the user asks for a change, resize work can proceed without that hazard.
