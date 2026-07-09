"""Tests for the WebSocket interactive server."""

import asyncio
import pytest

from py2max import Patcher


class TestWebSocketServer:
    """Tests for WebSocket interactive server."""

    @pytest.mark.asyncio
    async def test_import(self):
        """Test that server module can be imported."""
        from py2max_server import (
            InteractiveWebSocketHandler,
            InteractivePatcherServer,
            serve_interactive,
        )

        assert InteractiveWebSocketHandler is not None
        assert InteractivePatcherServer is not None
        assert serve_interactive is not None

    @pytest.mark.asyncio
    async def test_server_init(self):
        """Test server initialization."""
        from py2max_server import InteractivePatcherServer

        p = Patcher("test.maxpat")
        server = InteractivePatcherServer(p, port=9000, auto_open=False)

        assert server.patcher is p
        assert server.port == 9000
        assert server.ws_port == 9001
        assert server.auto_open is False
        assert server._running is False

    @pytest.mark.asyncio
    async def test_server_start_stop(self):
        """Test starting and stopping server."""
        from py2max_server import InteractivePatcherServer

        p = Patcher("test.maxpat")
        server = InteractivePatcherServer(p, port=9002, auto_open=False)

        # Start server
        await server.start()
        assert server._running is True
        assert server.ws_server is not None
        assert server.http_server is not None

        # Give servers time to start
        await asyncio.sleep(0.2)

        # Stop server
        await server.shutdown()
        assert server._running is False

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test using server as async context manager."""
        from py2max_server import InteractivePatcherServer

        p = Patcher("test.maxpat")

        async with InteractivePatcherServer(p, port=9003, auto_open=False) as server:
            await server.start()
            assert server._running is True
            await asyncio.sleep(0.1)

        # Server should be stopped after exiting context
        assert server._running is False

    @pytest.mark.asyncio
    async def test_serve_interactive(self):
        """Test serve_interactive convenience function."""
        from py2max_server import serve_interactive

        p = Patcher("test.maxpat")
        server = await serve_interactive(p, port=9004, auto_open=False)

        assert server._running is True
        assert server.patcher is p

        await server.shutdown()

    @pytest.mark.asyncio
    async def test_handler_broadcast(self):
        """Test handler broadcast functionality."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        p.add_textbox("cycle~ 440")

        handler = InteractiveWebSocketHandler(p)

        # Should not raise error with no clients
        await handler.broadcast({"type": "update", "boxes": [], "lines": []})

        assert len(handler.clients) == 0

    @pytest.mark.asyncio
    async def test_notify_update(self):
        """Test notify_update sends updates."""
        from py2max_server import InteractivePatcherServer

        p = Patcher("test.maxpat")
        server = InteractivePatcherServer(p, port=9006, auto_open=False)
        await server.start()

        # Add objects
        p.add_textbox("cycle~ 440")

        # Should not raise error even with no clients
        await server.notify_update()

        await server.shutdown()


class TestWebSocketHandler:
    """Tests for WebSocket handler message processing."""

    @pytest.mark.asyncio
    async def test_handle_update_position(self):
        """Test handling position update message."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        box = p.add_textbox("cycle~ 440")

        handler = InteractiveWebSocketHandler(p)

        # Update position
        await handler.handle_update_position({"box_id": box.id, "x": 100, "y": 200})

        # Check position was updated
        assert box.patching_rect.x == 100
        assert box.patching_rect.y == 200

    @pytest.mark.asyncio
    async def test_handle_create_object(self):
        """Test handling object creation message."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        handler = InteractiveWebSocketHandler(p)

        initial_count = len(p._boxes)

        # Create object
        await handler.handle_create_object({"text": "gain~", "x": 150, "y": 250})

        # Check object was created
        assert len(p._boxes) == initial_count + 1
        new_box = p._boxes[-1]
        assert new_box.text == "gain~"
        assert new_box.patching_rect.x == 150
        assert new_box.patching_rect.y == 250

    @pytest.mark.asyncio
    async def test_handle_create_connection(self):
        """Test handling connection creation message."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        box1 = p.add_textbox("cycle~ 440")
        box2 = p.add_textbox("gain~")

        handler = InteractiveWebSocketHandler(p)

        initial_count = len(p._lines)

        # Create connection
        await handler.handle_create_connection(
            {"src_id": box1.id, "dst_id": box2.id, "src_outlet": 0, "dst_inlet": 0}
        )

        # Check connection was created
        assert len(p._lines) == initial_count + 1
        new_line = p._lines[-1]
        assert new_line.src == box1.id
        assert new_line.dst == box2.id

    @pytest.mark.asyncio
    async def test_handle_delete_object(self):
        """Test handling object deletion message."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        box = p.add_textbox("cycle~ 440")
        box_id = box.id

        handler = InteractiveWebSocketHandler(p)

        # Delete object
        await handler.handle_delete_object({"box_id": box_id})

        # Check object was deleted
        assert len(p._boxes) == 0

    @pytest.mark.asyncio
    async def test_handle_delete_connection(self):
        """Test handling connection deletion message."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        box1 = p.add_textbox("cycle~ 440")
        box2 = p.add_textbox("gain~")
        p.add_line(box1, box2)

        handler = InteractiveWebSocketHandler(p)

        # Delete connection
        await handler.handle_delete_connection(
            {"src_id": box1.id, "dst_id": box2.id, "src_outlet": 0, "dst_inlet": 0}
        )

        # Check connection was deleted
        assert len(p._lines) == 0

    @pytest.mark.asyncio
    async def test_handle_open_valid_file(self, tmp_path):
        """Opening a valid patch file swaps in its contents."""
        from py2max_server import InteractiveWebSocketHandler

        # Write a patch file to disk to open later.
        src_path = tmp_path / "src.maxpat"
        src = Patcher(str(src_path))
        src.add_textbox("cycle~ 440")
        src.add_textbox("gain~")
        src.save()

        # Start the handler on a different, initially-empty patcher.
        original = Patcher(str(tmp_path / "orig.maxpat"))
        handler = InteractiveWebSocketHandler(original)

        await handler.handle_open({"filepath": str(src_path)})

        # The served patcher was replaced with the loaded file's contents.
        assert handler.patcher is not original
        assert handler.root_patcher is handler.patcher
        assert len(handler.patcher._boxes) == 2

    @pytest.mark.asyncio
    async def test_handle_open_missing_file(self, tmp_path):
        """Opening a missing file reports an error and leaves the patcher intact."""
        from py2max_server import InteractiveWebSocketHandler

        original = Patcher(str(tmp_path / "orig.maxpat"))
        handler = InteractiveWebSocketHandler(original)

        # Capture broadcasts (no real clients are connected).
        sent: list = []

        async def capture(msg):
            sent.append(msg)

        handler.broadcast = capture

        await handler.handle_open({"filepath": str(tmp_path / "does-not-exist.maxpat")})

        # Patcher is unchanged and an error was reported.
        assert handler.patcher is original
        assert any(m.get("type") == "open_error" for m in sent)

    def test_open_message_validates(self):
        """The 'open' message type requires a filepath string."""
        from py2max_server.websocket import validate_message

        ok, _ = validate_message({"type": "open", "filepath": "foo.maxpat"})
        assert ok

        ok, err = validate_message({"type": "open"})
        assert not ok
        assert "filepath" in err

    @pytest.mark.asyncio
    async def test_handle_open_content_valid(self, tmp_path):
        """Opening from uploaded file contents swaps in the parsed patcher."""
        import json

        from py2max_server import InteractiveWebSocketHandler

        # Build a patch and serialize it the way a .maxpat file is stored.
        src_path = tmp_path / "uploaded.maxpat"
        src = Patcher(str(src_path))
        src.add_textbox("cycle~ 440")
        src.add_textbox("gain~")
        src.save()
        content = src_path.read_text(encoding="utf8")
        assert "patcher" in json.loads(content)  # sanity: it is a .maxpat wrapper

        original = Patcher(str(tmp_path / "orig.maxpat"))
        handler = InteractiveWebSocketHandler(original)

        await handler.handle_open_content(
            {"filename": "uploaded.maxpat", "content": content}
        )

        assert handler.patcher is not original
        assert handler.root_patcher is handler.patcher
        assert len(handler.patcher._boxes) == 2

    @pytest.mark.asyncio
    async def test_handle_open_content_invalid_json(self, tmp_path):
        """Malformed upload content reports an error and keeps the patcher."""
        from py2max_server import InteractiveWebSocketHandler

        original = Patcher(str(tmp_path / "orig.maxpat"))
        handler = InteractiveWebSocketHandler(original)

        sent: list = []

        async def capture(msg):
            sent.append(msg)

        handler.broadcast = capture

        await handler.handle_open_content(
            {"filename": "bad.maxpat", "content": "{not valid json"}
        )

        assert handler.patcher is original
        assert any(m.get("type") == "open_error" for m in sent)

    def test_open_content_message_validates(self):
        """The 'open_content' message requires filename and content strings."""
        from py2max_server.websocket import validate_message

        ok, _ = validate_message(
            {"type": "open_content", "filename": "a.maxpat", "content": "{}"}
        )
        assert ok

        ok, err = validate_message({"type": "open_content", "filename": "a.maxpat"})
        assert not ok
        assert "content" in err

    @pytest.mark.asyncio
    async def test_handle_edit_object_text(self):
        """Editing an object's text updates it and broadcasts new state."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        box = p.add_textbox("cycle~ 440")

        handler = InteractiveWebSocketHandler(p)

        sent = []

        async def capture(msg):
            sent.append(msg)

        handler.broadcast = capture

        await handler.handle_edit_object_text({"box_id": box.id, "text": "cycle~ 880"})

        assert box.text == "cycle~ 880"
        assert any(m.get("type") == "update" for m in sent)

    def test_edit_object_text_validates(self):
        """The 'edit_object_text' message requires box_id and text strings."""
        from py2max_server.websocket import validate_message

        ok, _ = validate_message(
            {"type": "edit_object_text", "box_id": "obj-1", "text": "gate 3"}
        )
        assert ok

        ok, err = validate_message({"type": "edit_object_text", "box_id": "obj-1"})
        assert not ok
        assert "text" in err

    @pytest.mark.asyncio
    async def test_handle_export_patch(self):
        """Exporting sends serialized .maxpat text back to the requesting client."""
        import json

        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        p.add_textbox("cycle~ 440")

        handler = InteractiveWebSocketHandler(p)

        # Capture the direct (per-connection) reply.
        sent = []

        class FakeWS:
            async def send(self, msg):
                sent.append(json.loads(msg))

        await handler.handle_export_patch(FakeWS())

        assert len(sent) == 1
        assert sent[0]["type"] == "patch_content"
        assert sent[0]["filename"].endswith(".maxpat")
        # Content is a full .maxpat wrapper.
        assert "patcher" in json.loads(sent[0]["content"])

    def test_export_patch_validates(self):
        """The 'export_patch' message needs no fields."""
        from py2max_server.websocket import validate_message

        ok, _ = validate_message({"type": "export_patch"})
        assert ok

    @pytest.mark.asyncio
    async def test_handle_list_patches_lists_files_and_dirs(self, tmp_path):
        """The picker lists patch files and subdirectories, skipping other files."""
        import json

        from py2max_server import InteractiveWebSocketHandler

        # A patch, a non-patch file (should be excluded), and a subdirectory.
        (tmp_path / "a.maxpat").write_text("{}", encoding="utf8")
        (tmp_path / "b.maxpat").write_text("{}", encoding="utf8")
        (tmp_path / "notes.txt").write_text("hi", encoding="utf8")
        (tmp_path / "sub").mkdir()

        handler = InteractiveWebSocketHandler(Patcher(str(tmp_path / "orig.maxpat")))

        sent = []

        class FakeWS:
            async def send(self, msg):
                sent.append(json.loads(msg))

        await handler.handle_list_patches(FakeWS(), {"directory": str(tmp_path)})

        assert len(sent) == 1
        reply = sent[0]
        assert reply["type"] == "patch_list"
        assert reply["directory"] == str(tmp_path.resolve())
        assert reply["parent"] == str(tmp_path.resolve().parent)

        names = [e["name"] for e in reply["entries"]]
        assert "notes.txt" not in names  # non-patch file excluded
        assert "a.maxpat" in names and "b.maxpat" in names
        assert "sub" in names

        # Directories sort before files.
        dir_entry = next(e for e in reply["entries"] if e["name"] == "sub")
        assert dir_entry["is_dir"] is True
        assert reply["entries"][0]["is_dir"] is True

    @pytest.mark.asyncio
    async def test_handle_list_patches_skips_hidden(self, tmp_path):
        """Hidden entries (dotfiles/dotdirs) are omitted from the listing."""
        import json

        from py2max_server import InteractiveWebSocketHandler

        (tmp_path / "visible.maxpat").write_text("{}", encoding="utf8")
        (tmp_path / ".hidden.maxpat").write_text("{}", encoding="utf8")
        (tmp_path / ".hiddendir").mkdir()

        handler = InteractiveWebSocketHandler(Patcher(str(tmp_path / "orig.maxpat")))
        sent = []

        class FakeWS:
            async def send(self, msg):
                sent.append(json.loads(msg))

        await handler.handle_list_patches(FakeWS(), {"directory": str(tmp_path)})

        names = [e["name"] for e in sent[0]["entries"]]
        assert names == ["visible.maxpat"]

    @pytest.mark.asyncio
    async def test_handle_list_patches_defaults_to_current_patch_dir(self, tmp_path):
        """With no directory given, the picker lists the current patch's folder."""
        import json

        from py2max_server import InteractiveWebSocketHandler

        (tmp_path / "sibling.maxpat").write_text("{}", encoding="utf8")
        patch_path = tmp_path / "current.maxpat"
        patch_path.write_text("{}", encoding="utf8")

        handler = InteractiveWebSocketHandler(Patcher(str(patch_path)))
        sent = []

        class FakeWS:
            async def send(self, msg):
                sent.append(json.loads(msg))

        await handler.handle_list_patches(FakeWS(), {})

        assert sent[0]["directory"] == str(tmp_path.resolve())
        names = [e["name"] for e in sent[0]["entries"]]
        assert "sibling.maxpat" in names

    @pytest.mark.asyncio
    async def test_handle_list_patches_bad_directory_falls_back(self, tmp_path):
        """A nonexistent requested directory falls back to the default, not error."""
        import json

        from py2max_server import InteractiveWebSocketHandler

        patch_path = tmp_path / "current.maxpat"
        patch_path.write_text("{}", encoding="utf8")
        handler = InteractiveWebSocketHandler(Patcher(str(patch_path)))

        sent = []

        class FakeWS:
            async def send(self, msg):
                sent.append(json.loads(msg))

        await handler.handle_list_patches(
            FakeWS(), {"directory": str(tmp_path / "no-such-dir")}
        )

        assert sent[0]["type"] == "patch_list"
        assert sent[0]["directory"] == str(tmp_path.resolve())

    def test_list_patches_validates(self):
        """The 'list_patches' message needs no fields (directory is optional)."""
        from py2max_server.websocket import validate_message

        ok, _ = validate_message({"type": "list_patches"})
        assert ok

        ok, _ = validate_message({"type": "list_patches", "directory": "/tmp"})
        assert ok


class TestUndoRedo:
    """Tests for server-side undo/redo snapshots and batch mutations."""

    @pytest.mark.asyncio
    async def test_undo_create_object(self):
        """Undo removes a just-created object; redo brings it back."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        handler = InteractiveWebSocketHandler(p)

        await handler.handle_create_object({"text": "cycle~ 440", "x": 10, "y": 20})
        assert len(handler.patcher._boxes) == 1

        await handler.handle_undo()
        assert len(handler.patcher._boxes) == 0

        await handler.handle_redo()
        assert len(handler.patcher._boxes) == 1
        assert handler.patcher._boxes[0].text == "cycle~ 440"

    @pytest.mark.asyncio
    async def test_undo_delete_object_restores_lines(self):
        """Undoing a delete restores the box and its connected lines."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        b1 = p.add_textbox("cycle~ 440")
        b2 = p.add_textbox("gain~")
        p.add_line(b1, b2)
        handler = InteractiveWebSocketHandler(p)

        await handler.handle_delete_object({"box_id": b1.id})
        assert len(handler.patcher._boxes) == 1
        assert len(handler.patcher._lines) == 0

        await handler.handle_undo()
        assert len(handler.patcher._boxes) == 2
        assert len(handler.patcher._lines) == 1

    @pytest.mark.asyncio
    async def test_undo_edit_object_text(self):
        """Undo reverts an in-place text edit."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        box = p.add_textbox("cycle~ 440")
        handler = InteractiveWebSocketHandler(p)

        await handler.handle_edit_object_text({"box_id": box.id, "text": "cycle~ 880"})
        assert handler.patcher._boxes[0].text == "cycle~ 880"

        await handler.handle_undo()
        assert handler.patcher._boxes[0].text == "cycle~ 440"

    @pytest.mark.asyncio
    async def test_undo_noop_when_empty(self):
        """Undo/redo with empty stacks are safe no-ops."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        p.add_textbox("cycle~ 440")
        handler = InteractiveWebSocketHandler(p)

        await handler.handle_undo()  # nothing recorded yet
        await handler.handle_redo()
        assert len(handler.patcher._boxes) == 1

    @pytest.mark.asyncio
    async def test_new_mutation_clears_redo(self):
        """A fresh mutation after an undo discards the redo stack."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        handler = InteractiveWebSocketHandler(p)

        await handler.handle_create_object({"text": "a", "x": 0, "y": 0})
        await handler.handle_undo()
        assert handler.redo_stack  # redo now available

        await handler.handle_create_object({"text": "b", "x": 0, "y": 0})
        assert not handler.redo_stack  # discarded by the new mutation

    @pytest.mark.asyncio
    async def test_undo_preserves_filepath(self, tmp_path):
        """Restoring a snapshot keeps the on-disk path so Save still works."""
        from py2max_server import InteractiveWebSocketHandler

        path = tmp_path / "keep.maxpat"
        p = Patcher(str(path))
        handler = InteractiveWebSocketHandler(p)

        await handler.handle_create_object({"text": "a", "x": 0, "y": 0})
        await handler.handle_undo()

        restored = getattr(handler.root_patcher, "_path", None) or getattr(
            handler.root_patcher, "filepath", None
        )
        assert str(restored) == str(path)

    @pytest.mark.asyncio
    async def test_update_positions_batch_is_one_undo_step(self):
        """A group move is a single undo step covering every moved box."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        b1 = p.add_textbox("a")
        b2 = p.add_textbox("b")
        handler = InteractiveWebSocketHandler(p)

        await handler.handle_update_positions(
            {
                "positions": [
                    {"box_id": b1.id, "x": 300, "y": 400},
                    {"box_id": b2.id, "x": 500, "y": 600},
                ]
            }
        )

        def rect_of(box):
            r = box.patching_rect
            return (r.x, r.y) if hasattr(r, "x") else (r[0], r[1])

        assert rect_of(handler.patcher._boxes[0]) == (300, 400)
        assert rect_of(handler.patcher._boxes[1]) == (500, 600)
        assert len(handler.undo_stack) == 1

        await handler.handle_undo()
        # Both boxes revert together.
        assert rect_of(handler.patcher._boxes[0]) != (300, 400)
        assert rect_of(handler.patcher._boxes[1]) != (500, 600)

    @pytest.mark.asyncio
    async def test_delete_objects_batch_is_one_undo_step(self):
        """A group delete is a single undo step restoring all boxes."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat")
        b1 = p.add_textbox("a")
        b2 = p.add_textbox("b")
        p.add_textbox("c")
        handler = InteractiveWebSocketHandler(p)

        await handler.handle_delete_objects({"box_ids": [b1.id, b2.id]})
        assert len(handler.patcher._boxes) == 1
        assert len(handler.undo_stack) == 1

        await handler.handle_undo()
        assert len(handler.patcher._boxes) == 3

    @pytest.mark.asyncio
    async def test_undo_inside_subpatcher_keeps_view(self):
        """Undo while inside a subpatcher restores state and keeps the view."""
        from py2max_server import InteractiveWebSocketHandler

        p = Patcher("test.maxpat", title="Main")
        sub_box = p.add_subpatcher("p sub")
        sub = sub_box.subpatcher
        handler = InteractiveWebSocketHandler(p)

        await handler.handle_navigate_to_subpatcher({"box_id": sub_box.id})
        assert handler.view_path == [sub_box.id]

        # Create an object inside the subpatcher, then undo it.
        await handler.handle_create_object({"text": "cycle~ 440", "x": 0, "y": 0})
        assert len(handler.patcher._boxes) == len(sub._boxes)
        created = len(handler.patcher._boxes)

        await handler.handle_undo()
        # Still viewing the subpatcher (by path), with the object removed.
        assert handler.view_path == [sub_box.id]
        assert len(handler.patcher._boxes) == created - 1

    def test_undo_redo_and_batch_messages_validate(self):
        """undo/redo need no fields; batch ops require their list field."""
        from py2max_server.websocket import validate_message

        assert validate_message({"type": "undo"})[0]
        assert validate_message({"type": "redo"})[0]
        assert validate_message({"type": "update_positions", "positions": []})[0]
        assert validate_message({"type": "delete_objects", "box_ids": []})[0]
        assert not validate_message({"type": "update_positions"})[0]
        assert not validate_message({"type": "delete_objects"})[0]


class TestPortLabels:
    """Tests for maxref-derived inlet/outlet labels in the patch state."""

    def test_object_name_resolution(self):
        from py2max_server.websocket import _object_name_from_box

        # Text box: object is the first token.
        assert _object_name_from_box("cycle~ 440", "newobj") == "cycle~"
        # UI object: name comes from maxclass when text is empty.
        assert _object_name_from_box("", "number") == "number"
        # Non-object boxes get no name (so no maxref lookup).
        assert _object_name_from_box("0.5", "message") == ""
        assert _object_name_from_box("hello", "comment") == ""

    def test_label_from_entry_prefers_digest_then_type(self):
        from py2max_server.websocket import _label_from_entry

        assert _label_from_entry({"digest": "Frequency", "type": "signal"}) == "Frequency"
        assert _label_from_entry({"digest": "", "type": "signal"}) == "signal"
        # Placeholder types are suppressed to empty.
        assert _label_from_entry({"digest": "", "type": "INLET_TYPE"}) == ""
        assert _label_from_entry({"digest": "", "type": ""}) == ""

    def test_state_includes_labels_for_known_object(self):
        from py2max_server.websocket import get_patcher_state_json

        p = Patcher("test.maxpat")
        p.add_textbox("cycle~ 440")
        state = get_patcher_state_json(p)
        box = state["boxes"][0]

        assert box["inlet_labels"] == ["Frequency", "Phase (0-1)"]
        # Outlet has no digest -> falls back to its type.
        assert box["outlet_labels"] == ["signal"]
        # Labels are sized to the port counts.
        assert len(box["inlet_labels"]) == box["inlet_count"]
        assert len(box["outlet_labels"]) == box["outlet_count"]

    def test_state_omits_labels_for_non_object(self):
        from py2max_server.websocket import get_patcher_state_json

        p = Patcher("test.maxpat")
        p.add_comment("just a note")
        state = get_patcher_state_json(p)
        box = state["boxes"][0]

        assert "inlet_labels" not in box
        assert "outlet_labels" not in box

    def test_state_handles_unknown_object_gracefully(self):
        from py2max_server.websocket import get_patcher_state_json

        p = Patcher("test.maxpat")
        p.add_textbox("zzznotanobject~ 1")
        state = get_patcher_state_json(p)
        box = state["boxes"][0]

        # No maxref entry -> no labels, but the box still serializes fine.
        assert "inlet_labels" not in box
        assert box["text"] == "zzznotanobject~ 1"
