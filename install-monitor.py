#!/usr/bin/python3
"""Install the monitor for the current desktop user, preserving migration backups."""

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib


# Match the distributed legacy body exactly; custom shell code needs manual care.
LEGACY_FUNCTION = '''codex-hold() {
  rm -f /tmp/codex-turn-done || return

  gnome-session-inhibit \\
    --inhibit suspend \\
    --reason "Codex task running" \\
    --app-id codex \\
    bash -c 'until [ -e /tmp/codex-turn-done ]; do sleep 30; done'
}
'''
SOURCE = 'source "$HOME/.local/lib/codex-turn-hold/codex-hold.bash"\n'
BEGIN = "# >>> Codex turn hold >>>\n"
END = "# <<< Codex turn hold <<<\n"


def migrate_bashrc(text):
    block = BEGIN + SOURCE + END
    if BEGIN in text or END in text:
        if text.count(BEGIN) != 1 or text.count(END) != 1:
            raise ValueError("Ambiguous Codex turn hold markers in .bashrc")
        start = text.index(BEGIN)
        finish = text.index(END, start) + len(END)
        old = text[start + len(BEGIN):finish - len(END)]
        # Allow old explanatory comments without accepting changes to the commands.
        body = "\n".join(line for line in old.splitlines() if not line.startswith("#")) + "\n"
        if body not in (LEGACY_FUNCTION, SOURCE):
            raise ValueError("Custom Codex turn hold block in .bashrc; refusing to overwrite it")
        text = text[:start] + text[finish:]
        insert = start
    elif LEGACY_FUNCTION in text:
        if text.count(LEGACY_FUNCTION) != 1:
            raise ValueError("Multiple old codex-hold functions in .bashrc")
        insert = text.index(LEGACY_FUNCTION)
        text = text[:insert] + text[insert + len(LEGACY_FUNCTION):]
    else:
        insert = len(text)
        block = "\n" + block
    # Check outside the replaced block too, so another definition cannot override it.
    if re.search(r"(?m)^\s*(?:function\s+)?codex-hold\s*(?:\(\)|\{)", text):
        raise ValueError("Unrecognized codex-hold function in .bashrc; refusing to shadow it")
    if "codex-hold.bash" in text:
        raise ValueError("An additional codex-hold source statement exists in .bashrc")
    return text[:insert] + block + text[insert:]


def migrate_config(text, hook):
    before = tomllib.loads(text)
    # Only the exact top-level legacy command belongs to this installer.
    if before.get("notify") != ["bash", str(hook)]:
        return text, False
    lines = text.splitlines(keepends=True)
    expected = {key: value for key, value in before.items() if key != "notify"}
    # Try only a notify assignment; parsing and comparing the entire result
    # proves that unrelated configuration (including multiline values) survives.
    for start, line in enumerate(lines):
        if not re.match(r'''^\s*(?:notify|"notify"|'notify')\s*=''', line):
            continue
        for end in range(start + 1, len(lines) + 1):
            try:
                value = tomllib.loads("".join(lines[start:end]))
            except tomllib.TOMLDecodeError:
                continue
            if value != {"notify": before["notify"]}:
                break
            candidate = "".join(lines[:start] + lines[end:])
            if tomllib.loads(candidate) == expected:
                return candidate, True
    raise ValueError("Cannot safely remove the dedicated notify setting")


def atomic_write(path, data, mode):
    # A sibling temporary file allows one rename to expose the complete new file.
    fd, temporary = tempfile.mkstemp(prefix=".codex-hold-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install():
    if os.getuid() == 0:
        raise ValueError("Run this installer as your desktop user, without sudo")
    for name, package in (("websocket", "python3-websocket"), ("dbus", "python3-dbus")):
        if importlib.util.find_spec(name) is None:
            raise ValueError(f"Missing dependency. Run: sudo apt-get install {package}")
    root = Path(__file__).resolve().parent
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    bashrc = home / ".bashrc"
    config = codex_home / "config.toml"
    destination = home / ".local/lib/codex-turn-hold"
    for path in (bashrc, config, destination):
        if path.is_symlink():
            raise ValueError(f"Review symlink before installation: {path}")
    # Build both migrations and validate Bash before changing installed files.
    old_bashrc = bashrc.read_text() if bashrc.exists() else ""
    new_bashrc = migrate_bashrc(old_bashrc)
    old_config = config.read_text() if config.exists() else ""
    new_config, removed_notify = migrate_config(old_config, codex_home / "turn-done.sh")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".bash") as check:
        check.write(new_bashrc)
        check.flush()
        subprocess.run(["bash", "-n", check.name], check=True)

    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local/state")
    backup = state / "codex-turn-hold/backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup.mkdir(mode=0o700, parents=True)
    originals = {"bashrc": bashrc, "config.toml": config}
    for name in ("codex-hold.bash", "codex-monitor.py"):
        originals[name] = destination / name
    # Record missing originals too, so rollback can distinguish a first install.
    manifest = {"removed_notify": removed_notify, "files": {}}
    for name, path in originals.items():
        exists = path.exists()
        manifest["files"][name] = {"path": str(path), "existed": exists}
        if exists:
            shutil.copy2(path, backup / name)
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Backups: {backup}", flush=True)

    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    # Install the helper before making the source statement available.
    for name in ("codex-monitor.py", "codex-hold.bash"):
        atomic_write(destination / name, (root / name).read_bytes(), 0o644)
    atomic_write(bashrc, new_bashrc.encode(), bashrc.stat().st_mode & 0o777 if bashrc.exists() else 0o644)
    if removed_notify:
        atomic_write(config, new_config.encode(), config.stat().st_mode & 0o777)
    print(f"Installed: {destination}")
    print("Removed dedicated legacy notify setting." if removed_notify else "Preserved existing Codex notification settings.")
    print('Load in the monitor terminal: source "$HOME/.bashrc"')
    print("Then run: codex-hold")


if __name__ == "__main__":
    try:
        install()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Installation failed: {exc}") from exc
