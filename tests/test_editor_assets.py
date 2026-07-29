"""Static guards on the browser editor's assets.

There is no JS test harness in this project, so these are source-level checks.
They exist for one class of bug with real consequences: an editor action that
mutates the patch without the user asking, which -- because auto-save is on --
is written to disk within two seconds and destroys hand-placed geometry.

Deliberately count-and-locate rather than match formatting, so reindenting or
rewording the surrounding code does not produce a false failure.
"""

from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "py2max_server" / "static"

pytestmark = pytest.mark.skipif(
    not (STATIC / "interactive.js").is_file(), reason="editor assets not present"
)


def _js() -> str:
    return (STATIC / "interactive.js").read_text(encoding="utf-8")


def test_layout_is_applied_from_exactly_one_place():
    """Applying a layout must be an explicit user action, not a side effect.

    Opening the layout-controls panel used to call ``autoLayout()`` immediately,
    so merely *looking* at the controls rearranged every object and auto-saved
    the result over the .maxpat. A second call site is the likely shape of that
    regression returning.
    """
    js = _js()
    assert js.count("this.autoLayout()") == 1, (
        "expected exactly one autoLayout() call site (the Apply Layout button); "
        "a new one probably applies a layout as a side effect of some other action"
    )


def test_the_one_layout_call_belongs_to_the_apply_button():
    js = _js()
    call = js.index("this.autoLayout()")

    # The handler this call sits in is identified by the nearest preceding
    # getElementById(...) -- i.e. the element whose listener we are inside.
    marker = "getElementById('"
    owner_start = js.rindex(marker, 0, call) + len(marker)
    owner = js[owner_start : js.index("'", owner_start)]

    assert owner == "apply-layout-btn", (
        f"autoLayout() is wired to '{owner}', not the explicit Apply Layout "
        "button; layout must never be applied as a side effect"
    )


def _strip_block_comments(text: str) -> str:
    """Drop ``/* ... */`` comments, preserving length-independent structure.

    Only block comments: line comments are left alone because ``//`` also
    appears inside string literals (URLs), and stripping those naively would
    corrupt the source these checks read.
    """
    out = []
    i = 0
    while i < len(text):
        start = text.find("/*", i)
        if start == -1:
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = text.find("*/", start + 2)
        if end == -1:
            break
        i = end + 2
    return "".join(out)


def _balanced_block(text: str, from_index: int) -> str:
    """Return the ``{...}`` block that opens at or after ``from_index``.

    Brace counting is enough here: the blocks under test contain no braces
    inside string literals or comments. It is used instead of scanning to the
    next marker because the handler's own body references other elements, which
    truncated an earlier version of this check and made it vacuously pass.
    """
    start = text.index("{", from_index)
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError("unbalanced braces while isolating handler block")


def test_panel_toggle_does_not_move_objects():
    """The auto-layout toolbar button toggles the panel and nothing more."""
    js = _js()
    handler = _balanced_block(js, js.index("if (autoLayoutBtn)"))

    for forbidden in ("autoLayout(", "update_position", "sendMessage"):
        assert forbidden not in handler, (
            f"the auto-layout panel toggle references {forbidden!r}; opening the "
            "panel must not mutate or transmit patch state"
        )


def test_save_writes_back_to_the_served_file():
    """Save must ask the server to save, not hand a copy to the browser.

    It was wired to ``handleSave()`` -- the Save As path, which serializes the
    patch and lets the browser write it -- so pressing Save produced a file in
    ~/Downloads and never touched the patch being edited. With auto-save now
    opt-in, that made the editor unable to persist anything at all.
    """
    js = _js()
    handler = _balanced_block(js, js.index("if (saveBtn)"))

    assert "type: 'save'" in handler, "Save does not send a save message"
    assert "handleSave" not in handler, (
        "Save is wired to the Save As path again; it must write back to the "
        "file the server is serving"
    )


def test_save_as_remains_reachable():
    """Save As is still available as its own action, not only as a fallback."""
    js = _js()
    assert "save-as-btn" in js
    handler = _balanced_block(js, js.index("if (saveAsBtn)"))
    assert "handleSave" in handler


def test_port_geometry_has_a_single_source_of_truth():
    """Every consumer of port geometry must go through portCenterX.

    Drawing, drag updates, animation interpolation and patch cord endpoints all
    need identical numbers; the formula was duplicated across five sites, where
    changing one left cords visibly detached from the ports they connect to.
    """
    js = _js()
    assert js.count("portCenterX(x, w, index, count) {") == 1, (
        "portCenterX should be defined exactly once"
    )

    # Match full signatures: a bare name also hits the call sites, and an
    # earlier version of this test isolated an unrelated object literal and
    # passed for the wrong reason.
    definitions = (
        "getPortPosition(box, portIndex, isOutlet) {",
        "getPortPositionFromCoords(x, y, w, h, portIndex, portCount, isOutlet) {",
    )
    for signature in definitions:
        assert signature in js, f"signature moved or changed: {signature}"
        block = _balanced_block(js, js.index(signature))
        assert "portCenterX" in block, (
            f"{signature.split('(')[0]} computes port geometry on its own"
        )

    # In executable code the even-spacing formula may appear only inside
    # portCenterX. Comments are stripped first: the helper documents the
    # formula in prose, which is not a second implementation.
    code = _strip_block_comments(js)
    helper = _balanced_block(code, code.index("portCenterX(x, w, index, count) {"))
    formula = "w / (count + 1)"
    outside = code.count(formula) - helper.count(formula)
    assert outside == 0, (
        f"the even-spacing formula appears {outside} time(s) outside "
        "portCenterX; port geometry must not be recomputed elsewhere"
    )


def test_straighten_is_one_undo_step():
    """Straighten moves many boxes, so it must send one batched message.

    Sending update_position per box would push a separate undo entry for each,
    leaving the user to press Cmd-Z once per moved object to get back.
    """
    js = _js()
    html = (STATIC / "interactive.html").read_text(encoding="utf-8")

    assert 'id="straighten-btn"' in html
    block = _balanced_block(js, js.index("straightenLayout() {"))
    assert "'update_positions'" in block, (
        "straighten does not send a batched move; undo would need one press per object"
    )
    assert "'update_position'" not in block.replace("'update_positions'", ""), (
        "straighten sends per-box moves as well as the batch"
    )


def test_straighten_only_declines_on_collision():
    """A move is refused when it would stack boxes, and for no other reason.

    An earlier version also skipped fan-outs and fan-ins as "ambiguous", which
    left obviously-straightenable cords crooked: where an outlet feeds two
    boxes, aligning the first is right, and the second is stopped by the
    collision check on its own.
    """
    js = _js()
    block = _balanced_block(js, js.index("straightenLayout() {"))
    assert "wouldOverlap" in block, "straighten does not check for collisions"
    assert "blocked" in block, "straighten does not report what it declined to move"
    # Aligning against the primary (lowest-numbered) inlet, not a fan-out count.
    assert "dst_inlet" in block
    assert "outFanout" not in block, "fan-out special-casing is back"


def test_straighten_settles_sources_before_destinations():
    """Roots are drained before unreachable boxes are seeded.

    Seeding every unvisited box up front (to cover feedback loops) queued
    downstream boxes ahead of their sources, so a chain aligned against
    positions that had not settled yet and the cascade came out wrong.
    """
    js = _js()
    block = _balanced_block(js, js.index("straightenLayout() {"))
    first_drain = block.index("drain();")
    seed_loop = block.index("if (!visited.has(id))")
    assert first_drain < seed_loop, (
        "unreached boxes are seeded before the root pass drains; downstream "
        "boxes would align against sources that have not moved yet"
    )


def test_both_port_placement_modes_are_offered():
    """The Max-style placement is a choice, not a silent replacement."""
    html = (STATIC / "interactive.html").read_text(encoding="utf-8")
    assert 'id="port-placement"' in html
    assert 'value="max"' in html and 'value="even"' in html
