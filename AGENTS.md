# Repository Guidelines

## Project Structure & Module Organization

This project provides a manual suspend hold for native Ubuntu 24.04 GNOME Desktop while a local Codex CLI turn runs. All project files live at the root:

- `codex-hold.bash`: defines `codex-hold`, clears the shared marker, and holds a GNOME suspend inhibitor until completion.
- `turn-done.sh`: notification hook that creates `/tmp/codex-turn-done` on a completion event.
- `config-snippet.toml`: example notification configuration.
- `Readme.md`: installation, usage, limitations, and verification instructions.

There are no separate source, test, or asset directories.

## Build, Test, and Development Commands

No build step or dependency manager is required. Use Bash; running the holder also requires `gnome-session-inhibit` in a desktop session.

- `source ./codex-hold.bash`: load the function into the current Bash shell.
- `codex-hold`: start the hold in a separate terminal before submitting a task.
- `bash -n turn-done.sh && bash -n codex-hold.bash`: check both scripts for syntax errors.
- `shellcheck turn-done.sh codex-hold.bash`: lint both scripts when ShellCheck is installed.
- `gnome-session-inhibit --list`: inspect active desktop inhibitors.

## Coding Style & Naming Conventions

Match the existing two-space indentation and compact Bash style. Quote variable expansions and paths; preserve explicit failure handling such as `rm ... || return`. Use descriptive, lowercase, hyphenated names. Keep executable hooks as `.sh` files and sourced Bash functions as `.bash` files. ShellCheck is the documented linter; no formatter configuration is present. Update `Readme.md` whenever installation or behavior changes.

## Testing Guidelines

No automated test framework or coverage threshold is configured. For behavior changes, check completion, unrelated-event, and empty-argument inputs to the hook. Verify stale-marker removal, failure when removal is impossible, release within one 30-second poll, and Ctrl+C cleanup. Follow the desktop verification procedure in `Readme.md` and report which checks ran. Run marker tests only when no unrelated holder is active: the marker is shared.

## Commit & Pull Request Guidelines

Git history and a PR template are unavailable in this checkout. Use concise, imperative commit subjects, for example `Fix marker cleanup before acquiring inhibitor`. Keep changes focused. PR descriptions should explain the behavior change, checks performed, and any desktop verification still needed; link relevant issues.
