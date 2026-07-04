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
