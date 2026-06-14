"""Tests for apps/tool/launchd_install.py."""
import pytest
from pathlib import Path

from tool import launchd_install


def test_render_info_plist_contains_required_keys():
    out = launchd_install.render_info_plist("dev.test.x", "Test X")
    assert "<key>CFBundleIdentifier</key><string>dev.test.x</string>" in out
    assert "<key>CFBundleExecutable</key><string>launcher</string>" in out
    assert "<key>CFBundleName</key><string>Test X</string>" in out
    assert "<key>CFBundleDisplayName</key><string>Test X</string>" in out
    assert "<key>LSBackgroundOnly</key><true/>" in out
    assert "<key>LSUIElement</key><true/>" in out
    assert out.startswith('<?xml version="1.0"')


def test_render_launcher_script_quotes_args_and_appends_dollar_at():
    out = launchd_install.render_launcher_script(
        "/usr/bin/python3", ["-m", "tool.x", "--flag", "value"]
    )
    lines = out.splitlines()
    assert lines[0] == "#!/bin/bash"
    # shlex.quote leaves shell-safe tokens unquoted.
    assert lines[1] == "exec /usr/bin/python3 \\"
    assert "    -m \\" in lines
    assert "    tool.x \\" in lines
    assert "    --flag \\" in lines
    assert "    value \\" in lines
    assert '    "$@"' in lines


def test_render_launcher_script_quotes_unsafe_values():
    """A path containing an apostrophe must be shell-safely quoted, not
    naively wrapped in single quotes (which would break/inject)."""
    import subprocess

    out = launchd_install.render_launcher_script(
        "/weird path/python", ["-m", "tool.x", "--dest", "/repo's dir/x"]
    )
    # Must be syntactically valid bash despite the embedded apostrophe.
    r = subprocess.run(["bash", "-n"], input=out, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


def test_render_launcher_script_is_byte_stable():
    """TCC remembers consent against the bundle's CDHash, which depends on
    byte-stable launcher contents. Two calls with identical args must
    produce identical bytes."""
    a = launchd_install.render_launcher_script("/p", ["-m", "tool.x"])
    b = launchd_install.render_launcher_script("/p", ["-m", "tool.x"])
    assert a == b


def test_render_launchagent_plist_includes_label_paths_and_keepalive(tmp_path):
    launcher = tmp_path / "App.app" / "Contents" / "MacOS" / "launcher"
    log_file = tmp_path / "x.log"
    out = launchd_install.render_launchagent_plist(
        label="com.test.x",
        launcher_path=launcher,
        working_dir=tmp_path / "apps",
        log_file=log_file,
    )
    assert "<key>Label</key><string>com.test.x</string>" in out
    assert f"<string>{launcher}</string>" in out
    assert f"<string>{tmp_path / 'apps'}</string>" in out
    assert f"<key>StandardOutPath</key><string>{log_file}</string>" in out
    assert f"<key>StandardErrorPath</key><string>{log_file}</string>" in out
    assert "<key>KeepAlive</key><true/>" in out
    assert "<key>RunAtLoad</key><true/>" in out
    assert "<key>ThrottleInterval</key><integer>10</integer>" in out


from unittest.mock import patch, MagicMock


def test_build_app_bundle_creates_layout_and_signs(tmp_path):
    app = tmp_path / "Test.app"
    with patch.object(launchd_install.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0)
        launchd_install.build_app_bundle(
            app_path=app,
            bundle_id="dev.test.x",
            display_name="Test",
            python_path=Path("/usr/bin/python3"),
            exec_args=["-m", "tool.x"],
        )

    info_plist = app / "Contents" / "Info.plist"
    launcher = app / "Contents" / "MacOS" / "launcher"
    assert info_plist.exists()
    assert "dev.test.x" in info_plist.read_text()
    assert launcher.exists()
    assert launcher.stat().st_mode & 0o111  # executable bit set
    assert "tool.x" in launcher.read_text()
    run.assert_called_once_with(
        ["codesign", "--force", "--deep", "--sign", "-", str(app)],
        check=True,
    )


def test_build_app_bundle_is_reproducible(tmp_path):
    """Same inputs → identical launcher bytes (load-bearing for TCC consent)."""
    app = tmp_path / "Test.app"
    with patch.object(launchd_install.subprocess, "run"):
        launchd_install.build_app_bundle(
            app, "dev.test.x", "Test", Path("/p/python3"), ["-m", "tool.x"]
        )
        first = (app / "Contents" / "MacOS" / "launcher").read_bytes()
        launchd_install.build_app_bundle(
            app, "dev.test.x", "Test", Path("/p/python3"), ["-m", "tool.x"]
        )
        second = (app / "Contents" / "MacOS" / "launcher").read_bytes()
    assert first == second


def test_install_builds_bundles_writes_plists_and_calls_launchctl(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python3").touch()

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    with patch.object(launchd_install.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        launchd_install.install(repo)

    daemon_plist = fake_home / "Library" / "LaunchAgents" / "com.tg-viewer.daemon.plist"
    watcher_plist = (
        fake_home / "Library" / "LaunchAgents" / "com.tg-viewer.watcher.plist"
    )
    assert daemon_plist.exists()
    assert watcher_plist.exists()
    assert "com.tg-viewer.daemon" in daemon_plist.read_text()
    assert "com.tg-viewer.watcher" in watcher_plist.read_text()

    daemon_app = repo / "apps" / "launchd-bundles" / "TG Capture Daemon.app"
    watcher_app = repo / "apps" / "launchd-bundles" / "TG Capture Watcher.app"
    assert (daemon_app / "Contents" / "Info.plist").exists()
    assert (watcher_app / "Contents" / "Info.plist").exists()

    bootstrap_calls = [
        c for c in run.call_args_list if c.args[0][:2] == ["launchctl", "bootstrap"]
    ]
    assert len(bootstrap_calls) == 2
    # bootout fires before bootstrap so reinstall is idempotent (otherwise
    # bootstrap returns EIO when the label is already loaded).
    bootout_calls = [
        c for c in run.call_args_list if c.args[0][:2] == ["launchctl", "bootout"]
    ]
    assert len(bootout_calls) == 2


def test_install_warns_when_bootstrap_and_load_both_fail(
    tmp_path, monkeypatch, capsys
):
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python3").touch()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    def fake_run(cmd, **kwargs):
        if cmd[0] == "codesign":
            return MagicMock(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["launchctl", "bootout"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["launchctl", "bootstrap"]:
            return MagicMock(returncode=5, stdout="", stderr="Input/output error")
        if cmd[:2] == ["launchctl", "load"]:
            return MagicMock(returncode=5, stdout="", stderr="load failed")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(launchd_install.subprocess, "run", side_effect=fake_run):
        launchd_install.install(repo)

    err = capsys.readouterr().err
    assert err.count("Warning: failed to load") == 2
    assert "Input/output error" in err


def test_install_exits_when_venv_missing(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    with patch.object(launchd_install.subprocess, "run"):
        with pytest.raises(SystemExit) as ei:
            launchd_install.install(repo)
    assert ei.value.code != 0
    assert ".venv missing" in capsys.readouterr().err


def test_uninstall_removes_plists_and_calls_bootout(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    launch_agents = fake_home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    daemon_plist = launch_agents / "com.tg-viewer.daemon.plist"
    watcher_plist = launch_agents / "com.tg-viewer.watcher.plist"
    daemon_plist.write_text("<plist/>")
    watcher_plist.write_text("<plist/>")

    monkeypatch.setenv("HOME", str(fake_home))

    with patch.object(launchd_install.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0)
        launchd_install.uninstall()

    assert not daemon_plist.exists()
    assert not watcher_plist.exists()

    bootout_calls = [
        c for c in run.call_args_list if c.args[0][:2] == ["launchctl", "bootout"]
    ]
    assert len(bootout_calls) == 2
    pkill_calls = [c for c in run.call_args_list if c.args[0][0] == "pkill"]
    assert len(pkill_calls) == 2


def test_uninstall_is_idempotent_when_plists_already_gone(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    with patch.object(launchd_install.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=1)  # bootout fails (not loaded)
        launchd_install.uninstall()  # must not raise


def test_status_prints_per_label_and_filters_launchctl_output(capsys):
    fake_output = (
        "    state = running\n"
        "    pid = 12345\n"
        "    path = /Users/x/Library/...\n"
        "    program = /Users/x/.../launcher\n"  # filtered out
        "    last exit code = 0\n"
    )
    with patch.object(launchd_install.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stdout=fake_output, stderr="")
        launchd_install.status()

    out = capsys.readouterr().out
    assert "── com.tg-viewer.daemon ──" in out
    assert "── com.tg-viewer.watcher ──" in out
    assert "state = running" in out
    assert "pid = 12345" in out
    assert "last exit code = 0" in out
    assert "program = /Users/x/.../launcher" not in out  # not in keyword filter


def test_status_reports_not_loaded_when_launchctl_fails(capsys):
    with patch.object(launchd_install.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
        launchd_install.status()
    out = capsys.readouterr().out
    assert out.count("  not loaded") == 2


import subprocess as _sp
import sys as _sys


def test_module_help_lists_three_subcommands():
    """Sanity: `python -m tool.launchd_install --help` lists install/uninstall/status."""
    result = _sp.run(
        [_sys.executable, "-m", "tool.launchd_install", "--help"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),  # repo/apps
    )
    assert result.returncode == 0, result.stderr
    assert "install" in result.stdout
    assert "uninstall" in result.stdout
    assert "status" in result.stdout


def test_main_dispatches_to_install(monkeypatch):
    called = {}
    monkeypatch.setattr(launchd_install, "install", lambda repo: called.setdefault("repo", repo))
    monkeypatch.setattr(_sys, "argv", ["tool.launchd_install", "install"])
    launchd_install.main()
    assert "repo" in called


def test_main_dispatches_to_uninstall(monkeypatch):
    called = {"v": False}
    monkeypatch.setattr(launchd_install, "uninstall", lambda: called.__setitem__("v", True))
    monkeypatch.setattr(_sys, "argv", ["tool.launchd_install", "uninstall"])
    launchd_install.main()
    assert called["v"]


def test_main_dispatches_to_status(monkeypatch):
    called = {"v": False}
    monkeypatch.setattr(launchd_install, "status", lambda: called.__setitem__("v", True))
    monkeypatch.setattr(_sys, "argv", ["tool.launchd_install", "status"])
    launchd_install.main()
    assert called["v"]
