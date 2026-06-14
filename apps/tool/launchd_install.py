"""Install/uninstall/status for tg-viewer launchd agents (macOS).

Replaces the bash equivalents previously in ./tg-viewer. The .app bundle
wrapper exists because Homebrew python's unstable ad-hoc signing makes TCC
re-prompt on every restart; wrapping the daemon in a code-signed bundle
with a stable CDHash lets TCC remember consent.
"""
from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys
from pathlib import Path
from shlex import quote

DEFAULT_DAEMON_DEST_NAME = "tg_continuous"
DEFAULT_VAULT_DIR_NAME = "tg_vault"

DAEMON_LABEL = "com.tg-viewer.daemon"
WATCHER_LABEL = "com.tg-viewer.watcher"
DAEMON_BUNDLE_ID = "dev.tg-viewer.daemon"
WATCHER_BUNDLE_ID = "dev.tg-viewer.watcher"
DAEMON_DISPLAY_NAME = "TG Capture Daemon"
WATCHER_DISPLAY_NAME = "TG Capture Watcher"
DAEMON_BUNDLE_FILENAME = "TG Capture Daemon.app"
WATCHER_BUNDLE_FILENAME = "TG Capture Watcher.app"

_INFO_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTD/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>{bundle_id}</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleName</key><string>{display_name}</string>
  <key>CFBundleDisplayName</key><string>{display_name}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSBackgroundOnly</key><true/>
</dict>
</plist>
"""

_LAUNCHER_TEMPLATE = """#!/bin/bash
exec {python_path} \\
{indented_args}    "$@"
"""


def render_info_plist(bundle_id: str, display_name: str) -> str:
    return _INFO_PLIST_TEMPLATE.format(
        bundle_id=html.escape(bundle_id, quote=True),
        display_name=html.escape(display_name, quote=True),
    )


def render_launcher_script(python_path: str, args: list[str]) -> str:
    # Each arg gets its own line with a trailing backslash; "$@" on the last
    # line swallows the dangling backslash from the final arg line. shlex.quote
    # makes values with spaces, quotes, or apostrophes shell-safe.
    indented = "".join(f"    {quote(a)} \\\n" for a in args)
    return _LAUNCHER_TEMPLATE.format(
        python_path=quote(python_path), indented_args=indented
    )


_LAUNCHAGENT_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTD/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>WorkingDirectory</key><string>{working_dir}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{launcher_path}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>{log_file}</string>
  <key>StandardErrorPath</key><string>{log_file}</string>
</dict>
</plist>
"""


def render_launchagent_plist(
    label: str, launcher_path: Path, working_dir: Path, log_file: Path
) -> str:
    return _LAUNCHAGENT_PLIST_TEMPLATE.format(
        label=html.escape(label, quote=True),
        working_dir=html.escape(str(working_dir), quote=True),
        launcher_path=html.escape(str(launcher_path), quote=True),
        log_file=html.escape(str(log_file), quote=True),
    )


def build_app_bundle(
    app_path: Path,
    bundle_id: str,
    display_name: str,
    python_path: Path,
    exec_args: list[str],
) -> None:
    if app_path.exists():
        shutil.rmtree(app_path)
    macos_dir = app_path / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)

    (app_path / "Contents" / "Info.plist").write_text(
        render_info_plist(bundle_id, display_name)
    )
    launcher = macos_dir / "launcher"
    launcher.write_text(render_launcher_script(str(python_path), exec_args))
    launcher.chmod(0o755)

    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(app_path)],
        check=True,
    )


def install(repo_root: Path) -> None:
    venv_python = repo_root / ".venv" / "bin" / "python3"
    if not venv_python.exists():
        print(
            f".venv missing at {venv_python} — run ./tg-viewer setup first",
            file=sys.stderr,
        )
        sys.exit(1)

    launch_agents = Path.home() / "Library" / "LaunchAgents"
    logs_dir = Path.home() / "Library" / "Logs"
    launch_agents.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    bundles_dir = repo_root / "apps" / "launchd-bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)

    daemon_dest = repo_root / DEFAULT_DAEMON_DEST_NAME
    vault_dir = repo_root / DEFAULT_VAULT_DIR_NAME

    daemon_app = bundles_dir / DAEMON_BUNDLE_FILENAME
    watcher_app = bundles_dir / WATCHER_BUNDLE_FILENAME

    print(f"Building {daemon_app}...")
    build_app_bundle(
        daemon_app,
        DAEMON_BUNDLE_ID,
        DAEMON_DISPLAY_NAME,
        venv_python,
        [
            "-m",
            "tool.tg_daemon",
            "--repo",
            str(repo_root),
            "--dest",
            str(daemon_dest),
            "--interval",
            "300",
        ],
    )

    print(f"Building {watcher_app}...")
    build_app_bundle(
        watcher_app,
        WATCHER_BUNDLE_ID,
        WATCHER_DISPLAY_NAME,
        venv_python,
        ["-m", "tool.tg_watcher", "--vault", str(vault_dir)],
    )

    working_dir = repo_root / "apps"
    daemon_plist = launch_agents / f"{DAEMON_LABEL}.plist"
    watcher_plist = launch_agents / f"{WATCHER_LABEL}.plist"
    daemon_plist.write_text(
        render_launchagent_plist(
            DAEMON_LABEL,
            daemon_app / "Contents" / "MacOS" / "launcher",
            working_dir,
            logs_dir / "tg-viewer-daemon.log",
        )
    )
    watcher_plist.write_text(
        render_launchagent_plist(
            WATCHER_LABEL,
            watcher_app / "Contents" / "MacOS" / "launcher",
            working_dir,
            logs_dir / "tg-viewer-watcher.log",
        )
    )
    print(f"Wrote {daemon_plist}")
    print(f"Wrote {watcher_plist}")

    domain = f"gui/{os.getuid()}"
    # bootout first: bootstrap returns EIO (5) if the label is already loaded,
    # which happens on every reinstall. bootout is a no-op when not loaded.
    for label, plist in ((DAEMON_LABEL, daemon_plist), (WATCHER_LABEL, watcher_plist)):
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/{label}"], capture_output=True
        )
        bootstrap = subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist)],
            capture_output=True,
            text=True,
        )
        if bootstrap.returncode != 0:
            load = subprocess.run(
                ["launchctl", "load", str(plist)], capture_output=True, text=True
            )
            if load.returncode != 0:
                print(
                    f"Warning: failed to load {label}: "
                    f"{(bootstrap.stderr or load.stderr).strip()}",
                    file=sys.stderr,
                )

    print("Loaded both agents.")
    print()
    print("macOS will ask ONCE per agent for permission to access Telegram's")
    print("data. The popup will name the bundle (TG Capture Daemon / Watcher),")
    print("NOT generic python3 anymore — click Allow for each.")
    print()
    print("If consent doesn't persist, manually add both .app bundles to:")
    print("  System Settings → Privacy & Security → App Management")
    print(f"  (paths: {bundles_dir}/)")
    print()
    print(
        f"Logs: tail -f {Path.home()}/Library/Logs/tg-viewer-{{daemon,watcher}}.log"
    )


def uninstall() -> None:
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    domain = f"gui/{os.getuid()}"
    for label in (DAEMON_LABEL, WATCHER_LABEL):
        plist = launch_agents / f"{label}.plist"
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/{label}"], capture_output=True
        )
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        if plist.exists():
            plist.unlink()
            print(f"Removed {plist}")
    subprocess.run(["pkill", "-9", "-f", "tool.tg_daemon"], capture_output=True)
    subprocess.run(["pkill", "-9", "-f", "tool.tg_watcher"], capture_output=True)
    print("All tg-viewer launchd processes stopped.")


_STATUS_KEYWORDS = ("state", "last exit", "pid", "path")


def status() -> None:
    domain = f"gui/{os.getuid()}"
    for label in (DAEMON_LABEL, WATCHER_LABEL):
        print(f"── {label} ──")
        result = subprocess.run(
            ["launchctl", "print", f"{domain}/{label}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("  not loaded")
        else:
            shown = 0
            for line in result.stdout.splitlines():
                if shown >= 10:
                    break
                if any(k in line for k in _STATUS_KEYWORDS):
                    print(line)
                    shown += 1
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tool.launchd_install",
        description="Install/uninstall/status for tg-viewer launchd agents (macOS).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_install = sub.add_parser(
        "install", help="Install daemon + watcher as launchd agents"
    )
    # parents[2] from apps/tool/launchd_install.py → repo root.
    p_install.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repo root (default: auto-detected)",
    )
    sub.add_parser("uninstall", help="Remove the launchd agents")
    sub.add_parser("status", help="Show state of both launchd agents")

    args = parser.parse_args()
    if args.cmd == "install":
        install(args.repo)
    elif args.cmd == "uninstall":
        uninstall()
    elif args.cmd == "status":
        status()


if __name__ == "__main__":
    main()
