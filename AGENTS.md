# Repository Guidelines

## Project Structure & Module Organization

This project provides a manually started persistent suspend monitor for native Ubuntu 24.04 GNOME Desktop and Codex CLI 0.164.0. The runtime scripts, installer, and tests live at the root:

- `codex-hold.bash`: defines `codex-hold` and launches the helper with system Python.
- `codex-monitor.py`: polls the shared server, tracks independent 180-second idle/recovery timers, and owns one GNOME suspend inhibitor.
- `install-monitor.py`: backs up and migrates known legacy settings, and installs the launcher/helper.
- `test_monitor.py`: standard-library tests with simulated time and fake transports/inhibitors.
- `legacy/`: archived marker hook and notification example, unused by the monitor; see its README.
- `README.md`: installation, usage, limitations, and verification instructions.
- `CONTRIBUTING.md`: development checks and contribution guidance.
- `.github/workflows/ci.yml`: Ubuntu 24.04 tests, Bash syntax checks, and ShellCheck.

Keep the launcher and helper beside the installer: installation reads these sibling files. There are no separate source or test directories. Local scratch notes (`sleep_test`), Serena configuration, and other local tool/configuration directories are ignored; preserve local copies when changing tracking.

## Build, Test, and Development Commands

No build step is required. Use Bash and `/usr/bin/python3`; running the monitor requires Ubuntu's `python3-websocket` and `python3-dbus` in a GNOME desktop session.

- `source ./codex-hold.bash`: load the function into the current Bash shell.
- `codex-hold`: start the persistent monitor in a separate terminal.
- `codex-hold --check`: read status once without acquiring an inhibitor.
- `/usr/bin/python3 -B -m unittest -v`: run the standard-library tests.
- `bash -n codex-hold.bash && bash -n legacy/turn-done.sh`: check both scripts for syntax errors.
- `shellcheck --shell=bash codex-hold.bash legacy/turn-done.sh`: lint both scripts; install ShellCheck to run the full CI checks locally.
- `gnome-session-inhibit --list`: inspect active desktop inhibitors.

## Coding Style & Naming Conventions

Match the existing two-space indentation and compact Bash style; Python uses four spaces. Quote variable expansions and paths; preserve explicit failure handling such as `rm ... || return`. Use descriptive, lowercase, hyphenated names. Keep executable hooks as `.sh` files and sourced Bash functions as `.bash` files. ShellCheck is the documented linter; no formatter configuration is present. Text files use LF line endings. Update `README.md` whenever installation or behavior changes.

## Testing Guidelines

Use standard-library unittest with simulated clocks for behavior changes; no coverage threshold is configured. Verify both 180-second deadlines, uninterrupted holds when work resumes, all pages and sub-agents, malformed status, reconnection, duplicate instances, and signal cleanup. Follow the desktop verification procedure in `README.md` and report which checks ran. Do not trigger actual suspend or disturb existing tasks. The monitor never uses the shared marker. If changing the legacy hook, check completion, unrelated-event, and empty-argument inputs only when no unrelated old holder is active. The installer still migrates the installed `<CODEX_HOME>/turn-done.sh` path; moving the archived example must not change that migration target.

## Commit & Pull Request Guidelines

Git history is available; no PR template is provided. Use concise, imperative commit subjects, for example `Fix marker cleanup before acquiring inhibitor`. Keep changes focused. PR descriptions should explain the behavior change, checks performed, and any desktop verification still needed; link relevant issues. Commit verified changes locally and push only when requested.
