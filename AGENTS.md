# Repository Guidelines

## Project Structure & Module Organization

This project provides a manually started persistent suspend monitor for native Ubuntu 24.04 GNOME Desktop and Codex CLI 0.164.0. All project files live at the root:

- `codex-hold.bash`: defines `codex-hold` and launches the helper with system Python.
- `codex-monitor.py`: polls the shared server, tracks independent 180-second idle/recovery timers, and owns one GNOME suspend inhibitor.
- `install-monitor.py`: backs up and migrates known legacy settings, and installs the launcher/helper.
- `test_monitor.py`: standard-library tests with simulated time and fake transports/inhibitors.
- `turn-done.sh`: legacy hook that creates `/tmp/codex-turn-done` on completion.
- `config-snippet.toml`: legacy notification example, unused by the monitor.
- `Readme.md`: installation, usage, limitations, and verification instructions.

There are no separate source, test, or asset directories.

## Build, Test, and Development Commands

No build step is required. Use Bash and `/usr/bin/python3`; running the monitor requires Ubuntu's `python3-websocket` and `python3-dbus` in a GNOME desktop session.

- `source ./codex-hold.bash`: load the function into the current Bash shell.
- `codex-hold`: start the persistent monitor in a separate terminal.
- `codex-hold --check`: read status once without acquiring an inhibitor.
- `/usr/bin/python3 -B -m unittest -v`: run the standard-library tests.
- `bash -n turn-done.sh && bash -n codex-hold.bash`: check both scripts for syntax errors.
- `shellcheck turn-done.sh codex-hold.bash`: lint both scripts when ShellCheck is installed.
- `gnome-session-inhibit --list`: inspect active desktop inhibitors.

## Coding Style & Naming Conventions

Match the existing two-space indentation and compact Bash style. Quote variable expansions and paths; preserve explicit failure handling such as `rm ... || return`. Use descriptive, lowercase, hyphenated names. Keep executable hooks as `.sh` files and sourced Bash functions as `.bash` files. ShellCheck is the documented linter; no formatter configuration is present. Update `Readme.md` whenever installation or behavior changes.

## Testing Guidelines

Use standard-library unittest with simulated clocks for behavior changes; no coverage threshold is configured. Verify both 180-second deadlines, uninterrupted holds when work resumes, all pages and sub-agents, malformed status, reconnection, duplicate instances, and signal cleanup. Follow the desktop verification procedure in `Readme.md` and report which checks ran. Do not trigger actual suspend or disturb existing tasks. The monitor never uses the shared marker. If changing the legacy hook, check completion, unrelated-event, and empty-argument inputs only when no unrelated old holder is active.

## Commit & Pull Request Guidelines

Git history and a PR template are unavailable in this checkout. Use concise, imperative commit subjects, for example `Fix marker cleanup before acquiring inhibitor`. Keep changes focused. PR descriptions should explain the behavior change, checks performed, and any desktop verification still needed; link relevant issues.
