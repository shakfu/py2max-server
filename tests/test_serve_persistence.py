"""End-to-end regression tests for the serve/REPL persistence path.

These exist because the whole mocked suite passed while the feature's central
promise -- "edit live, changes are saved" -- was broken in three separate ways:

1. ``cmd_serve`` loaded the patch with ``Patcher.from_file(path)`` and no
   ``save_to=``, so ``Patcher._path`` stayed ``None``, ``patcher.save()``
   silently did nothing, and every REPL/browser edit was lost on exit.
2. ``serve_interactive`` was called without ``auto_save``, which defaults to
   False -- so the "Auto-save enabled" banner was untrue regardless of (1).
3. The single-terminal ``--repl --log-file`` mode called ``Patcher.serve()``,
   removed from py2max in v0.3.0, so that mode died on startup.

The mocks hid all three: they never exercised a real ``from_file`` -> ``save()``
round trip. So these tests deliberately touch the real filesystem and the real
websocket stack instead.
"""

import json

import pytest

from py2max import Patcher

from py2max_server.cli import _load_patcher, build_parser


@pytest.fixture
def patch_file(tmp_path):
    """A real .maxpat on disk with a known object count."""
    path = tmp_path / "live.maxpat"
    p = Patcher(path)
    osc = p.add_textbox("cycle~ 440")
    gain = p.add_textbox("gain~")
    p.add_line(osc, gain)
    p.save()
    return path


def _num_boxes(path):
    return len(json.loads(path.read_text())["patcher"]["boxes"])


class TestLoadedPatcherIsSaveable:
    """Bug 1: the loaded patcher must be bound to its own path."""

    def test_load_patcher_sets_filepath(self, patch_file):
        p = _load_patcher(patch_file)
        assert p.filepath, "loaded patcher has no filepath; save() would no-op"
        assert str(p.filepath) == str(patch_file)

    def test_save_actually_writes_to_disk(self, patch_file):
        before = _num_boxes(patch_file)
        p = _load_patcher(patch_file)
        p.add_textbox("lores~ 1000")
        p.save()
        assert _num_boxes(patch_file) == before + 1

    def test_plain_from_file_still_does_not_bind(self, patch_file):
        """Pin the upstream behaviour this fix depends on.

        If py2max ever makes ``from_file`` bind the path by itself, this test
        fails and ``_load_patcher`` can be simplified -- better that than the
        helper quietly becoming redundant.
        """
        p = Patcher.from_file(patch_file)
        assert not p.filepath

    def test_repl_save_command_reports_success(self, patch_file):
        """The REPL's save() prints a warning instead of saving when unbound."""
        from py2max_server.repl import ReplCommands

        p = _load_patcher(patch_file)
        p.add_textbox("svf~ 500")
        before = _num_boxes(patch_file)

        server = type("S", (), {"port": 8000, "handler": type("H", (), {"clients": set()})()})()
        ReplCommands(p, server).save()

        assert _num_boxes(patch_file) == before + 1


class TestAutoSaveWiring:
    """Bug 2: --no-save must actually control auto_save."""

    def test_auto_save_defaults_on(self):
        args = build_parser().parse_args(["serve", "x.maxpat"])
        assert not args.no_save, "auto-save is advertised as on by default"

    def test_serve_interactive_receives_auto_save(self, patch_file, monkeypatch):
        """cmd_serve must pass auto_save through, not leave it at its False default.

        Sync, not async: ``cmd_serve`` drives its own ``asyncio.run``.
        """
        import py2max_server.cli as cli_mod

        captured = {}

        async def fake_serve_interactive(patcher, port, auto_open, auto_save=False):
            captured["auto_save"] = auto_save
            raise KeyboardInterrupt  # unwind before the server loop starts

        monkeypatch.setattr(cli_mod, "serve_interactive", fake_serve_interactive)
        args = build_parser().parse_args(["serve", str(patch_file), "--no-open"])
        cli_mod.cmd_serve(args)

        assert captured.get("auto_save") is True

    def test_no_save_flag_disables_auto_save(self, patch_file, monkeypatch):
        import py2max_server.cli as cli_mod

        captured = {}

        async def fake_serve_interactive(patcher, port, auto_open, auto_save=False):
            captured["auto_save"] = auto_save
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_mod, "serve_interactive", fake_serve_interactive)
        args = build_parser().parse_args(
            ["serve", str(patch_file), "--no-open", "--no-save"]
        )
        cli_mod.cmd_serve(args)

        assert captured.get("auto_save") is False


class TestInlineModeUsesLiveApi:
    """Bug 3: the single-terminal mode called a py2max API that no longer exists."""

    def test_patcher_serve_is_gone(self):
        """Document the upstream removal this mode tripped over."""
        assert not hasattr(Patcher, "serve")

    def test_inline_mode_does_not_reference_patcher_serve(self):
        import inspect

        from py2max_server.inline import BackgroundServerREPL

        src = inspect.getsource(BackgroundServerREPL._run_server)
        assert "patcher.serve(" not in src
        assert "serve_interactive" in src


class TestBackgroundServerShutdown:
    """``stop()`` must actually stop the background server, from another thread."""

    def test_stop_releases_the_port_and_joins_the_thread(self, patch_file):
        import socket
        import threading
        import time

        from py2max_server.inline import BackgroundServerREPL

        port = 8781
        repl = BackgroundServerREPL(
            _load_patcher(patch_file), port=port, auto_open=False
        )
        repl.server_thread = threading.Thread(target=repl._run_server, daemon=True)
        repl.server_thread.start()

        assert repl.ready_event.wait(timeout=10), "background server never started"

        repl.stop()

        # The old stop() cancelled tasks from the wrong thread and never woke
        # the loop, so the thread stayed alive and the port stayed bound.
        repl.server_thread.join(timeout=10)
        assert not repl.server_thread.is_alive(), "server thread outlived stop()"

        for _ in range(50):  # allow the listening socket a moment to clear
            with socket.socket() as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("localhost", port))
                    return
                except OSError:
                    time.sleep(0.1)
        pytest.fail(f"port {port} still bound after stop()")


class TestRemoteReplRoundTrip:
    """The full journey: connect, edit over the wire, save, verify on disk."""

    @pytest.mark.asyncio
    async def test_edit_and_save_over_websocket(self, patch_file):
        import json as _json

        from websockets.asyncio.client import connect

        from py2max_server.rpc import start_repl_server
        from py2max_server.websocket import serve_interactive

        patcher = _load_patcher(patch_file)
        before = _num_boxes(patch_file)

        server = await serve_interactive(patcher, port=8765, auto_open=False)
        repl_server = await start_repl_server(patcher, server, port=8767)
        token = server.handler.session_token

        try:
            async with connect("ws://localhost:8767") as ws:
                await ws.send(_json.dumps({"type": "auth", "token": token}))
                assert _json.loads(await ws.recv())["type"] == "auth_success"

                async def run(code):
                    await ws.send(
                        _json.dumps({"type": "eval", "code": code, "mode": "exec"})
                    )
                    return _json.loads(await ws.recv())

                added = await run("p.add_textbox('lores~ 1000')")
                assert added["type"] == "result", added

                saved = await run("save()")
                assert saved["type"] == "result", saved
                assert "No filepath set" not in (saved.get("display") or "")
        finally:
            repl_server.close()
            await repl_server.wait_closed()
            await server.stop()

        assert _num_boxes(patch_file) == before + 1, "REPL edit never reached disk"

    @pytest.mark.asyncio
    async def test_bad_token_is_refused(self, patch_file):
        import json as _json

        from websockets.asyncio.client import connect

        from py2max_server.rpc import start_repl_server
        from py2max_server.websocket import serve_interactive

        patcher = _load_patcher(patch_file)
        server = await serve_interactive(patcher, port=8768, auto_open=False)
        repl_server = await start_repl_server(patcher, server, port=8770)

        try:
            async with connect("ws://localhost:8770") as ws:
                await ws.send(_json.dumps({"type": "auth", "token": "wrong"}))
                reply = _json.loads(await ws.recv())
                assert reply["type"] == "error"
        finally:
            repl_server.close()
            await repl_server.wait_closed()
            await server.stop()
