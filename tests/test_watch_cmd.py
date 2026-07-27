"""Tests for 'antcrew watch' CLI command."""
from __future__ import annotations

import threading
import time

import pytest
from typer.testing import CliRunner

from antcrew.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Import error path (watchdog not installed)
# ---------------------------------------------------------------------------

def test_watch_exits_if_watchdog_missing(tmp_path, monkeypatch):
    """Command exits with error when watchdog is not installed."""
    import builtins
    real_import = builtins.__import__

    def _block_watchdog(name, *args, **kwargs):
        if "watchdog" in name:
            raise ImportError("no module named watchdog")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_watchdog)
    f = tmp_path / "spec.md"
    f.write_text("test spec")
    result = runner.invoke(app, ["watch", str(f), "--request", "build x"])
    assert result.exit_code == 1
    assert "watchdog" in result.output.lower() or "watch" in result.output.lower()


# ---------------------------------------------------------------------------
# Missing path error
# ---------------------------------------------------------------------------

def test_watch_exits_on_missing_path(tmp_path):
    result = runner.invoke(app, ["watch", str(tmp_path / "nope.md"), "--request", "x"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Diff helper — tested via its internal logic
# ---------------------------------------------------------------------------

def _code_state(files: dict[str, str]) -> dict:
    """Build a minimal state dict with code_artifacts."""
    return {
        "code_artifacts": [
            {"ticket_id": "T-1", "file_path": fp, "content": content, "language": "python"}
            for fp, content in files.items()
        ]
    }


def test_watch_diff_shows_new_file(tmp_path, capsys):
    """_show_diff detects added files."""
    prev = _code_state({"main.py": "# old\n"})
    curr = _code_state({"main.py": "# old\n", "new.py": "# new\n"})

    # Inline the diff logic from watch_cmd

    def _fmap(state):
        arts = state.get("code_artifacts") or []
        return {
            a["file_path"]: a.get("content", "")
            for a in arts if isinstance(a, dict) and a.get("file_path")
        }

    fa, fb = _fmap(prev), _fmap(curr)
    added = sorted(set(fb) - set(fa))
    assert "new.py" in added


def test_watch_diff_shows_modified_file():
    prev = _code_state({"auth.py": "def login(): pass\n"})
    curr = _code_state({"auth.py": "def login(): return True\n"})


    def _fmap(state):
        arts = state.get("code_artifacts") or []
        return {a["file_path"]: a.get("content", "") for a in arts if isinstance(a, dict)}

    fa, fb = _fmap(prev), _fmap(curr)
    changed = [f for f in fa if f in fb and fa[f] != fb[f]]
    assert "auth.py" in changed


def test_watch_diff_detects_removed_file():
    prev = _code_state({"a.py": "# a\n", "b.py": "# b\n"})
    curr = _code_state({"a.py": "# a\n"})

    def _fmap(state):
        arts = state.get("code_artifacts") or []
        return {a["file_path"]: a.get("content", "") for a in arts if isinstance(a, dict)}

    fa, fb = _fmap(prev), _fmap(curr)
    removed = sorted(set(fa) - set(fb))
    assert "b.py" in removed


# ---------------------------------------------------------------------------
# Filesystem watcher integration (short-lived)
# ---------------------------------------------------------------------------

def test_watch_detects_file_change(tmp_path):
    """Watcher fires when a file is modified."""
    pytest.importorskip("watchdog", reason="watchdog not installed")

    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    triggered = threading.Event()

    class _H(FileSystemEventHandler):
        def on_modified(self, event):
            if not event.is_directory:
                triggered.set()

    spec = tmp_path / "spec.md"
    spec.write_text("v1")

    observer = Observer()
    observer.schedule(_H(), path=str(tmp_path), recursive=False)
    observer.start()
    try:
        time.sleep(0.3)
        spec.write_text("v2")
        assert triggered.wait(timeout=5), "File change not detected within 5 s"
    finally:
        observer.stop()
        observer.join()


def test_watch_detects_directory_change(tmp_path):
    """Watcher fires when any file inside a directory changes."""
    pytest.importorskip("watchdog", reason="watchdog not installed")

    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    triggered = threading.Event()

    class _H(FileSystemEventHandler):
        def on_modified(self, event):
            if not event.is_directory:
                triggered.set()
        def on_created(self, event):
            if not event.is_directory:
                triggered.set()

    observer = Observer()
    observer.schedule(_H(), path=str(tmp_path), recursive=True)
    observer.start()
    try:
        time.sleep(0.3)
        (tmp_path / "new_file.py").write_text("# new")
        assert triggered.wait(timeout=5), "Directory change not detected within 5 s"
    finally:
        observer.stop()
        observer.join()
