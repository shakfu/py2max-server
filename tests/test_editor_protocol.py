"""End-to-end tests for the browser editor's websocket protocol.

`test_websocket.py` covers the handlers with mocked patchers and sockets. These
drive the real thing instead: a real server, a real websocket client, real
fixtures on disk. That distinction mattered -- the mocked suite stayed green
through four bugs in the serve/REPL path (see `test_serve_persistence.py`),
because it never exercised a real load/edit/save round trip.

The most important case here is fidelity: opening a patch and saving it with no
edits must leave the file byte-identical. Silent corruption on open+save would
be worse than any missing editor feature.
"""

import json
import shutil
from pathlib import Path

import pytest

from py2max import Patcher

from py2max_server.websocket import serve_interactive

# Reuse py2max's own fixtures: real patches, including nested subpatchers.
FIXTURES = Path(__file__).resolve().parents[2] / "py2max" / "tests" / "data"

pytestmark = pytest.mark.skipif(
    not FIXTURES.is_dir(), reason="py2max fixture patches not available"
)


async def recv_of(ws, types, timeout=10.0):
    """Read until a message of one of `types` (or an error) arrives."""
    import asyncio

    while True:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            return {"type": "<timeout>"}
        if msg.get("type") in types or msg.get("type") == "error":
            return msg


class EditorSession:
    """A running server plus an authenticated websocket client."""

    def __init__(self, patch_path, port):
        self.patch_path = Path(patch_path)
        self.port = port
        self.server = None
        self.ws = None
        self._ctx = None

    async def __aenter__(self):
        from websockets.asyncio.client import connect

        patcher = Patcher.from_file(self.patch_path, save_to=str(self.patch_path))
        self.server = await serve_interactive(
            patcher, port=self.port, auto_open=False, auto_save=True
        )
        self._ctx = connect(f"ws://localhost:{self.port + 1}")
        self.ws = await self._ctx.__aenter__()
        await self.ws.send(
            json.dumps({"type": "auth", "token": self.server.handler.session_token})
        )
        await recv_of(self.ws, ("auth_success",))
        self.state = await recv_of(self.ws, ("update",))
        return self

    async def __aexit__(self, *exc):
        if self._ctx is not None:
            await self._ctx.__aexit__(*exc)
        if self.server is not None:
            await self.server.stop()

    async def send(self, **msg):
        await self.ws.send(json.dumps(msg))

    async def expect(self, *types):
        return await recv_of(self.ws, types)

    def on_disk(self):
        return json.loads(self.patch_path.read_text())


def _copy_fixture(name, tmp_path):
    dst = tmp_path / name
    shutil.copy(FIXTURES / name, dst)
    return dst


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,port",
    [("simple.maxpat", 9200), ("complex.maxpat", 9210), ("nested.maxpat", 9220)],
)
async def test_open_and_save_is_lossless(name, port, tmp_path):
    """Opening a patch and saving it untouched must not change the file."""
    path = _copy_fixture(name, tmp_path)
    original = json.loads(path.read_text())

    async with EditorSession(path, port) as s:
        await s.send(type="save")
        ack = await s.expect("save_complete", "save_as_required")
        assert ack["type"] == "save_complete", ack

    assert json.loads(path.read_text()) == original, f"{name} was altered by open+save"


@pytest.mark.asyncio
async def test_core_edit_operations(tmp_path):
    """create / move / connect / retitle / delete, each reflected in the broadcast."""
    path = tmp_path / "edit.maxpat"
    p = Patcher(path)
    osc = p.add_textbox("cycle~ 440")
    gain = p.add_textbox("gain~")
    p.add_line(osc, gain)
    p.save()

    async with EditorSession(path, 9230) as s:
        ids = [b["id"] for b in s.state["boxes"]]
        assert len(ids) == 2

        await s.send(type="create_object", text="lores~ 1000", x=300, y=200)
        st = await s.expect("update")
        assert len(st["boxes"]) == 3
        new = next(b for b in st["boxes"] if "lores~" in b["text"])
        rect = new["patching_rect"]
        assert [rect["x"], rect["y"]] == [300, 200]

        # position updates come back as a delta, not a full state
        await s.send(type="update_position", box_id=ids[0], x=42, y=84)
        delta = await s.expect("position_update", "update")
        assert delta["type"] == "position_update"
        assert (delta["x"], delta["y"]) == (42.0, 84.0)

        await s.send(type="create_connection", src_id=ids[0], dst_id=new["id"])
        st = await s.expect("update")
        assert len(st["lines"]) == 2

        await s.send(type="edit_object_text", box_id=new["id"], text="svf~ 2000")
        st = await s.expect("update")
        assert "svf~" in next(b for b in st["boxes"] if b["id"] == new["id"])["text"]

        await s.send(type="delete_object", box_id=new["id"])
        st = await s.expect("update")
        assert len(st["boxes"]) == 2


@pytest.mark.asyncio
async def test_edits_reach_disk(tmp_path):
    path = tmp_path / "persist.maxpat"
    p = Patcher(path)
    p.add_textbox("cycle~ 440")
    p.save()

    async with EditorSession(path, 9240) as s:
        await s.send(type="create_object", text="lores~ 1000", x=300, y=200)
        await s.expect("update")
        await s.send(type="save")
        assert (await s.expect("save_complete", "save_as_required"))[
            "type"
        ] == "save_complete"

        disk = s.on_disk()["patcher"]

    texts = [b["box"].get("text", "") for b in disk["boxes"]]
    assert any("lores~" in t for t in texts), texts


@pytest.mark.asyncio
async def test_undo_redo(tmp_path):
    path = tmp_path / "undo.maxpat"
    p = Patcher(path)
    p.add_textbox("cycle~ 440")
    p.save()

    async with EditorSession(path, 9250) as s:
        box_id = s.state["boxes"][0]["id"]

        await s.send(type="edit_object_text", box_id=box_id, text="saw~ 220")
        st = await s.expect("update")
        assert "saw~" in st["boxes"][0]["text"]

        await s.send(type="undo")
        st = await s.expect("update")
        assert "cycle~" in st["boxes"][0]["text"], "undo did not revert the edit"

        await s.send(type="redo")
        st = await s.expect("update")
        assert "saw~" in st["boxes"][0]["text"], "redo did not re-apply the edit"


@pytest.mark.asyncio
async def test_batch_move_and_delete_are_single_undo_steps(tmp_path):
    path = tmp_path / "batch.maxpat"
    p = Patcher(path)
    a = p.add_textbox("cycle~ 440")
    b = p.add_textbox("gain~")
    c = p.add_textbox("ezdac~")
    p.add_line(a, b)
    p.add_line(b, c)
    p.save()

    async with EditorSession(path, 9260) as s:
        ids = [x["id"] for x in s.state["boxes"]]

        await s.send(
            type="update_positions",
            positions=[
                {"box_id": ids[0], "x": 10, "y": 20},
                {"box_id": ids[1], "x": 110, "y": 120},
            ],
        )
        st = await s.expect("update")
        rects = {x["id"]: x["patching_rect"] for x in st["boxes"]}
        assert [rects[ids[0]]["x"], rects[ids[0]]["y"]] == [10, 20]
        assert [rects[ids[1]]["x"], rects[ids[1]]["y"]] == [110, 120]

        await s.send(type="delete_connection", src_id=ids[0], dst_id=ids[1])
        st = await s.expect("update")
        assert len(st["lines"]) == 1

        await s.send(type="delete_objects", box_ids=ids[:2])
        st = await s.expect("update")
        assert len(st["boxes"]) == 1

        await s.send(type="undo")
        st = await s.expect("update")
        assert len(st["boxes"]) == 3, "batch delete did not undo as one step"


@pytest.mark.asyncio
async def test_subpatcher_navigation_and_editing(tmp_path):
    path = _copy_fixture("nested.maxpat", tmp_path)

    def count_subs(pd):
        return sum(
            1 + count_subs(b["box"]["patcher"])
            for b in pd.get("boxes", [])
            if "patcher" in b["box"]
        )

    subs_before = count_subs(json.loads(path.read_text())["patcher"])

    async with EditorSession(path, 9270) as s:
        sub = next(b for b in s.state["boxes"] if b.get("has_subpatcher"))

        await s.send(type="navigate_to_subpatcher", box_id=sub["id"])
        inner = await s.expect("update")
        assert len(inner["patcher_path"]) == 2, inner["patcher_path"]

        await s.send(type="create_object", text="svf~ 300", x=20, y=20)
        added = await s.expect("update")
        assert len(added["boxes"]) == len(inner["boxes"]) + 1

        await s.send(type="navigate_to_root")
        root = await s.expect("update")
        # the breadcrumb includes the root itself, so depth 1 means "at root"
        assert len(root["patcher_path"]) == 1, root["patcher_path"]

        await s.send(type="save")
        await s.expect("save_complete", "save_as_required")
        disk = s.on_disk()["patcher"]

    assert count_subs(disk) == subs_before, "subpatcher structure lost on save"

    def texts_in_subs(pd):
        out = []
        for b in pd.get("boxes", []):
            if "patcher" in b["box"]:
                out += [
                    x["box"].get("text", "")
                    for x in b["box"]["patcher"].get("boxes", [])
                ]
                out += texts_in_subs(b["box"]["patcher"])
        return out

    assert any("svf~" in t for t in texts_in_subs(disk)), (
        "object added inside a subpatcher did not persist there"
    )


@pytest.mark.asyncio
async def test_breadcrumb_names_the_subpatcher(tmp_path):
    """A `p upper` box means the breadcrumb should say "upper", not "Main".

    Max stores a subpatcher's name on the *parent box*, not the nested patcher,
    so reading `title` alone rendered every level "Main" -- correct about depth,
    useless about location.
    """
    path = _copy_fixture("nested.maxpat", tmp_path)

    async with EditorSession(path, 9280) as s:
        sub = next(b for b in s.state["boxes"] if b.get("has_subpatcher"))
        assert sub["text"] == "p upper", sub["text"]

        await s.send(type="navigate_to_subpatcher", box_id=sub["id"])
        inner = await s.expect("update")

    assert inner["patcher_path"] == ["Main", "upper"], inner["patcher_path"]


@pytest.mark.asyncio
async def test_export_patch_returns_valid_json(tmp_path):
    path = tmp_path / "export.maxpat"
    p = Patcher(path)
    p.add_textbox("cycle~ 440")
    p.save()

    async with EditorSession(path, 9290) as s:
        await s.send(type="export_patch")
        msg = await s.expect("patch_content")

    assert msg["type"] == "patch_content", msg
    assert json.loads(msg["content"])["patcher"]["boxes"]


@pytest.mark.asyncio
async def test_unauthenticated_connection_is_refused(tmp_path):
    from websockets.asyncio.client import connect

    path = tmp_path / "auth.maxpat"
    p = Patcher(path)
    p.add_textbox("cycle~ 440")
    p.save()

    patcher = Patcher.from_file(path, save_to=str(path))
    server = await serve_interactive(patcher, port=9300, auto_open=False)
    try:
        async with connect("ws://localhost:9301") as ws:
            await ws.send(json.dumps({"type": "auth", "token": "wrong"}))
            msg = await recv_of(ws, ("error", "auth_success"))
            assert msg["type"] == "error", msg
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_unknown_message_type_is_rejected(tmp_path):
    path = tmp_path / "bad.maxpat"
    p = Patcher(path)
    p.add_textbox("cycle~ 440")
    p.save()

    async with EditorSession(path, 9310) as s:
        await s.send(type="definitely_not_a_real_message")
        msg = await s.expect("error")
        assert msg["type"] == "error"


@pytest.mark.asyncio
async def test_resize_object_changes_size_and_keeps_position(tmp_path):
    """Resizing must alter w/h only -- the box must not drift."""
    path = tmp_path / "resize.maxpat"
    p = Patcher(path)
    p.add_textbox("scope~")
    p.save()

    async with EditorSession(path, 9320) as s:
        box = s.state["boxes"][0]
        before = box["patching_rect"]

        await s.send(type="resize_object", box_id=box["id"], w=240, h=180)
        delta = await s.expect("size_update", "update")

        assert delta["type"] == "size_update", delta
        assert (delta["w"], delta["h"]) == (240.0, 180.0)

        await s.send(type="save")
        assert (await s.expect("save_complete", "save_as_required"))[
            "type"
        ] == "save_complete"
        disk = s.on_disk()["patcher"]["boxes"][0]["box"]["patching_rect"]

    assert list(disk) == [before["x"], before["y"], 240.0, 180.0], (
        f"expected position preserved and size changed, got {disk}"
    )


@pytest.mark.asyncio
async def test_resize_is_clamped_to_a_usable_minimum(tmp_path):
    """A box must never be shrunk to the point of being unselectable."""
    from py2max_server.websocket import MIN_BOX_SIZE

    path = tmp_path / "clamp.maxpat"
    p = Patcher(path)
    p.add_textbox("scope~")
    p.save()

    async with EditorSession(path, 9330) as s:
        box_id = s.state["boxes"][0]["id"]
        await s.send(type="resize_object", box_id=box_id, w=0, h=-40)
        delta = await s.expect("size_update", "update")

    assert delta["w"] == MIN_BOX_SIZE
    assert delta["h"] == MIN_BOX_SIZE


@pytest.mark.asyncio
async def test_resize_is_undoable(tmp_path):
    path = tmp_path / "undo_resize.maxpat"
    p = Patcher(path)
    p.add_textbox("scope~")
    p.save()

    async with EditorSession(path, 9340) as s:
        box = s.state["boxes"][0]
        original = dict(box["patching_rect"])

        await s.send(type="resize_object", box_id=box["id"], w=240, h=180)
        await s.expect("size_update", "update")

        await s.send(type="undo")
        st = await s.expect("update")

    restored = st["boxes"][0]["patching_rect"]
    assert (restored["w"], restored["h"]) == (original["w"], original["h"])


@pytest.mark.asyncio
async def test_batch_resize_is_a_single_undo_step(tmp_path):
    path = tmp_path / "batch_resize.maxpat"
    p = Patcher(path)
    p.add_textbox("scope~")
    p.add_textbox("gain~")
    p.save()

    async with EditorSession(path, 9350) as s:
        ids = [b["id"] for b in s.state["boxes"]]

        await s.send(
            type="resize_objects",
            sizes=[
                {"box_id": ids[0], "w": 200, "h": 150},
                {"box_id": ids[1], "w": 90, "h": 40},
            ],
        )
        st = await s.expect("update")
        rects = {b["id"]: b["patching_rect"] for b in st["boxes"]}
        assert (rects[ids[0]]["w"], rects[ids[0]]["h"]) == (200, 150)
        assert (rects[ids[1]]["w"], rects[ids[1]]["h"]) == (90, 40)

        # One message, so one Cmd-Z puts both back.
        await s.send(type="undo")
        st = await s.expect("update")

    rects = {b["id"]: b["patching_rect"] for b in st["boxes"]}
    assert (rects[ids[0]]["w"], rects[ids[0]]["h"]) != (200, 150)
    assert (rects[ids[1]]["w"], rects[ids[1]]["h"]) != (90, 40)


@pytest.mark.asyncio
async def test_resize_does_not_corrupt_the_rest_of_the_patch(tmp_path):
    """A resize must touch one rect and nothing else in the file."""
    path = _copy_fixture("complex.maxpat", tmp_path)
    original = json.loads(path.read_text())

    async with EditorSession(path, 9360) as s:
        target = s.state["boxes"][0]["id"]
        await s.send(type="resize_object", box_id=target, w=321, h=123)
        await s.expect("size_update", "update")
        await s.send(type="save")
        await s.expect("save_complete", "save_as_required")

    after = json.loads(path.read_text())

    def rects(doc):
        return {
            b["box"]["id"]: b["box"].get("patching_rect")
            for b in doc["patcher"]["boxes"]
        }

    before_rects, after_rects = rects(original), rects(after)
    assert set(before_rects) == set(after_rects), "boxes were added or lost"

    changed = [k for k in before_rects if before_rects[k] != after_rects[k]]
    assert changed == [target], f"resize altered more than the target box: {changed}"

    # Everything other than the boxes list must be byte-identical.
    del original["patcher"]["boxes"], after["patcher"]["boxes"]
    assert original == after, "resize altered unrelated patch fields"
