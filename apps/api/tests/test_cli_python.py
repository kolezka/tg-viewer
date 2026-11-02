"""The tg-viewer CLI must run Python from the project venv without requiring activation."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "tg-viewer"


def _stub(path: Path, label: str, log: Path, exit_code: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "{label} $*" >> "{log}"\nexit {exit_code}\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def sandbox(tmp_path: Path):
    """A copy of the CLI with the files its dependency check expects and stub interpreters."""
    root = tmp_path / "repo"
    (root / "apps" / "api").mkdir(parents=True)
    (root / "apps" / "web" / "dist").mkdir(parents=True)
    for name in ("tg-backup.sh", "tg_appstore_decrypt.py", "postbox_parser.py"):
        (root / "apps" / "tool").mkdir(parents=True, exist_ok=True)
        (root / "apps" / "tool" / name).write_text("")
    shutil.copy2(CLI, root / "tg-viewer")
    (tmp_path / "data").mkdir()

    log = tmp_path / "calls.log"
    log.touch()
    # The PATH python3 stands in for a system interpreter without the deps.
    _stub(tmp_path / "bin" / "python3", "path-python", log, exit_code=1)

    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "CONDA_PREFIX")}
    env["PATH"] = f"{tmp_path / 'bin'}:/usr/bin:/bin"
    return root, tmp_path / "data", log, env


def _webui(root: Path, data: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(root / "tg-viewer"), "webui", str(data)],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_webui_uses_project_venv_when_not_activated(sandbox):
    root, data, log, env = sandbox
    _stub(root / ".venv" / "bin" / "python3", "venv-python", log)

    result = _webui(root, data, env)

    calls = log.read_text().splitlines()
    assert any(c.startswith("venv-python -m api ") for c in calls), (calls, result.stderr)
    assert not any(c.startswith("path-python") for c in calls), calls
    assert result.returncode == 0, result.stderr


def test_webui_falls_back_to_path_python_without_venv(sandbox):
    root, data, log, env = sandbox

    _webui(root, data, env)

    calls = log.read_text().splitlines()
    assert any(c.startswith("path-python -m api ") for c in calls), calls


def test_webui_respects_an_activated_env(sandbox):
    root, data, log, env = sandbox
    _stub(root / ".venv" / "bin" / "python3", "venv-python", log)
    env["VIRTUAL_ENV"] = "/somewhere/else"

    _webui(root, data, env)

    calls = log.read_text().splitlines()
    assert any(c.startswith("path-python -m api ") for c in calls), calls
    assert not any(c.startswith("venv-python") for c in calls), calls


def test_no_subcommand_calls_bare_python3():
    """Every interpreter call goes through $PYTHON; only venv creation may use bare python3."""
    allowed = re.compile(r"python3 -m venv ")
    bare = re.compile(r'(?<![\w/."$-])python3\s+-')
    offenders = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(CLI.read_text().splitlines(), 1)
        if bare.search(line) and not allowed.search(line) and not line.lstrip().startswith("#")
    ]
    assert offenders == []
