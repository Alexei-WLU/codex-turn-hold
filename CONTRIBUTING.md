# Contributing

The supported environment is native Ubuntu 24.04 GNOME with Codex CLI 0.164.0.
Other desktop environments and Codex versions are unverified. Keep changes
focused and update the [user guide](README.md) when installation or behavior
changes.

## Project layout

The launcher (`codex-hold.bash`), monitor (`codex-monitor.py`), installer
(`install-monitor.py`), and test suite (`test_monitor.py`) live together at the
root. The installer and tests find sibling files by their paths, so preserve
this arrangement. Historical marker examples live in [`legacy/`](legacy/README.md).

Use two spaces for Bash and four for Python, matching the existing code.
Quote shell paths and expansions, preserve explicit error handling, and use LF
line endings. No build step or formatter is required.

## Local checks

The automated tests need only Bash and Ubuntu's system Python. They use
simulated clocks, fake Codex/GNOME adapters, and temporary installation fixtures;
they do not need Codex, a desktop session, or the monitor's third-party Python
packages. Signal tests send signals only to their own child processes.

Install the shell linter if needed:

```bash
sudo apt-get install shellcheck
```

Then run from the repository root:

```bash
/usr/bin/python3 -B -m unittest -v
bash -n codex-hold.bash
bash -n legacy/turn-done.sh
shellcheck --shell=bash codex-hold.bash legacy/turn-done.sh
git diff --check
```

Tests cover the independent idle and recovery deadlines, uninterrupted holds
when work resumes, paginated status and sub-agents, malformed responses,
reconnection, duplicate instances, signal cleanup, and installer migration.
Add or adjust tests for behavior changes using the existing simulated clocks
and isolated fixtures.

The [CI workflow](.github/workflows/ci.yml) runs the same unittest, syntax, and
ShellCheck commands on pushes and pull requests using Ubuntu 24.04 and system
Python. It has a five-minute timeout, read-only repository permissions, and a
commit-pinned checkout action with credential persistence disabled.

## Desktop verification

For runtime changes, follow [Verify](README.md#verify) in the user guide and
report which observations were completed. Automated tests do not establish
that a real desktop inhibitor works. Stay at the computer, preserve power
settings, and do not trigger suspend or interrupt existing tasks.

The legacy hook writes a shared marker. Run it only when specifically testing
legacy behavior and when no unrelated old holder is active. Moving the
archived files does not change the installer's migration target:
`<CODEX_HOME>/turn-done.sh`.

## Pull requests

Describe the problem, resulting behavior, checks performed, and any desktop
verification still needed. Link relevant issues and use concise, imperative
commit subjects. Keep local configuration, credentials, scratch notes, and
generated caches out of commits. The project is [MIT licensed](LICENSE).
