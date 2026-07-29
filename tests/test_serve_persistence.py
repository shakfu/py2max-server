"""End-to-end regression tests for the serve/console persistence path.

These exist because the whole mocked suite passed while the feature's central
promise -- "edit live, changes are saved" -- was broken in two ways:

1. ``cmd_serve`` loaded the patch with ``Patcher.from_file(path)`` and no
   ``save_to=``, so ``Patcher._path`` stayed ``None``, ``patcher.save()``
   silently did nothing, and every console/browser edit was lost on exit.
2. ``serve_interactive`` was called without ``auto_save``, which defaults to
   False -- so the "Auto-save enabled" banner was untrue regardless of (1).

The mocks hid both: they never exercised a real ``from_file`` -> ``save()``
round trip. So these tests deliberately touch the real filesystem and the real
websocket stack instead.

(A third bug lived in the single-terminal ``--repl --log-file`` mode, which
called a ``Patcher.serve()`` that py2max had removed. That mode is gone: the
console is served in the editor page, so its tests went with it.)
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

        server = type(
            "S", (), {"port": 8000, "handler": type("H", (), {"clients": set()})()}
        )()
        ReplCommands(p, server).save()

        assert _num_boxes(patch_file) == before + 1


class TestAutoSaveWiring:
    """Bug 2 (was): the CLI must actually control auto_save, and say what it did.

    Auto-save is now opt-in. Serving a patch is a read operation until the user
    asks otherwise -- the previous default rewrote the file on the browser's
    behalf, which cost a tracked example patch.
    """

    def _capture(self, monkeypatch, argv, patch_file):
        """Run cmd_serve with serve_interactive stubbed, returning its auto_save."""
        import py2max_server.cli as cli_mod

        captured = {}

        async def fake_serve_interactive(patcher, port, auto_open, auto_save=False):
            captured["auto_save"] = auto_save
            raise KeyboardInterrupt  # unwind before the server loop starts

        monkeypatch.setattr(cli_mod, "serve_interactive", fake_serve_interactive)
        # Sync, not async: cmd_serve drives its own asyncio.run.
        cli_mod.cmd_serve(
            build_parser().parse_args(["serve", str(patch_file), "--no-open", *argv])
        )
        return captured.get("auto_save")

    def test_auto_save_is_off_by_default(self, patch_file, monkeypatch):
        assert self._capture(monkeypatch, [], patch_file) is False

    def test_auto_save_flag_turns_it_on(self, patch_file, monkeypatch):
        assert self._capture(monkeypatch, ["--auto-save"], patch_file) is True

    def test_legacy_no_save_flag_still_accepted(self, patch_file, monkeypatch):
        """--no-save now restates the default; it must not become an error."""
        assert self._capture(monkeypatch, ["--no-save"], patch_file) is False

    def test_no_save_wins_over_auto_save(self, patch_file, monkeypatch):
        """A contradictory pair resolves toward not touching the file."""
        assert (
            self._capture(monkeypatch, ["--auto-save", "--no-save"], patch_file)
            is False
        )


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
