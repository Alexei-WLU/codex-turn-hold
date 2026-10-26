# codex-turn-hold

A manually started suspend monitor for **Codex CLI on Ubuntu GNOME**.

Run `codex-hold` in a separate terminal. It keeps the computer awake while any
conversation or sub-agent on Codex's shared local server is working. After all
work has been idle or waiting for you for **180 continuous seconds**, it allows
sleep again. The monitor stays running and automatically protects later work.

Screen blanking, locking, and power settings stay unchanged. GNOME controls
actual suspension; sleep can follow immediately when the hold is released if
its inactivity timeout has already elapsed.

## Requirements

- Native **Ubuntu 24.04 with GNOME**, a local desktop session, and Bash.
- **Codex CLI 0.164.0**, already installed and working. Check with
  `codex --version`; other versions are unverified.
- Ubuntu's system Python (`/usr/bin/python3`) and the packages installed below.

No build step, pip environment, Codex wrapper, or notification hook is needed.

## Install

Download this repository using GitHub's **Code → Download ZIP**, then extract it
(or clone it). Open a Bash terminal in the directory containing
`install-monitor.py` and run:

```bash
sudo apt-get update
sudo apt-get install python3-websocket python3-dbus
/usr/bin/python3 -B install-monitor.py
source "$HOME/.bashrc"
```

Run the installer and monitor as your desktop user, **without `sudo`**.
If you use a custom `CODEX_HOME`, export the same value used by Codex before
installing and running the monitor. The default is `~/.codex`.

The installer:

- Copies the launcher and helper into `~/.local/lib/codex-turn-hold/` and adds
  a marked source block to `~/.bashrc`.
- Backs up `~/.bashrc`, Codex's `config.toml`, and any previous launcher/helper under
  `~/.local/state/codex-turn-hold/backups/<timestamp>/` (or under
  `$XDG_STATE_HOME/codex-turn-hold/backups/`). It prints the backup directory.
- Preserves existing file permissions and refuses ambiguous custom shell
  definitions or symlinked installation/configuration files.

**Upgrading from the old marker script:** stop the old holder first. Installation
replaces its known function and removes only the dedicated
`notify = ["bash", "<CODEX_HOME>/turn-done.sh"]` setting. Other notification
commands remain. The old hook and notification example are archived in
[`legacy/`](legacy/README.md); the new monitor never uses `/tmp/codex-turn-done`.

## Use

In a separate terminal, start:

```bash
codex-hold
```

Run Codex normally in another terminal. You can start the monitor before Codex
or during a task. Leave its terminal open; **Ctrl+C** stops it and releases the
hold. Restart it manually after closing the terminal, logging out, or rebooting.
There is no autostart.

The monitor checks every **2 seconds**, covering every loaded conversation and
sub-agent. Waiting for approval or a user reply counts as idle; a working
sub-agent still keeps the hold. Each complete scan has a **5-second deadline**;
slow or failed scans can delay the next poll.

| Situation | Behavior |
| --- | --- |
| Any thread is working | Acquire or retain one suspend hold. |
| All threads become idle or waiting | Release the existing hold on the first successful idle check at or after 180 seconds. |
| Work resumes during that countdown | Keep the same hold and cancel the countdown. |
| Status becomes unavailable | Retry; retain an existing hold for up to 180 seconds from the first failed check. Repeated failures do not extend this deadline. |
| Recovery deadline expires | Release the hold and keep retrying. Later active work can acquire it again. |
| Startup finds idle or unavailable status | Allow sleep immediately while continuing to monitor. |

A failed check cancels the idle countdown. If reliable idle status returns
before recovery expires, a fresh 180-second idle countdown starts. Malformed
status or WebSocket handshake responses also enter this recovery path.

## Verify

With Codex open, read its status without acquiring a hold:

```bash
codex-hold --check
```

Example: `{"loadedThreads": 2, "workingThreads": 1}`. This reads status once,
works alongside the monitor, and does not request turn history.

To check the desktop hold while a task is working:

```bash
gnome-session-inhibit --list
```

Look for `codex-turn-hold`. Stay at the computer and check that its hold remains
during work and the idle countdown, disappears after 180 seconds of continuous
idle, and returns for a later task. Resuming work during the countdown should
keep the hold uninterrupted. Ctrl+C should remove it.

## Troubleshooting

- **`codex-hold: command not found`:** use Bash and run `source "$HOME/.bashrc"`,
  or open a new Bash terminal after installation.
- **`Status unavailable ... No such file or directory`:** expected before Codex
  starts. The monitor retries automatically. If it persists, check that Codex is
  open and both terminals use the same `CODEX_HOME`. The default socket is
  `~/.codex/app-server-control/app-server-control.sock`.
- **GNOME connection error:** run in your local GNOME desktop as your normal
  user. Restart the monitor after a GNOME session-manager restart or failure.
- **Already running:** use the existing monitor terminal. Do not delete its
  lock file; an old file alone does not prevent startup.
- **Sleep remains blocked:** inspect `gnome-session-inhibit --list`; other
  applications, an old holder, or Codex itself may have separate holds.

## Limits

Only the shared server selected by `CODEX_HOME` is covered. Isolated runs,
other users/servers, and commands continuing after Codex reports idle are
excluded. Short activity can fall between polls; new threads may be seen on
the next scan.

The hold cannot override forced suspend, logout, shutdown, or every lid policy.
An outage beyond the recovery deadline permits sleep despite possible ongoing
work. After upgrading Codex, repeat the status and desktop checks above.

## Update or remove

To update, download the updated repository, rerun the installer, and source
`~/.bashrc`. Restart the monitor when it is safe to release its hold; running
processes keep using the old code until restarted.

To remove, stop the monitor and delete the block between
`# >>> Codex turn hold >>>` and `# <<< Codex turn hold <<<` from `~/.bashrc`,
including both marker lines. Then run:

```bash
unset -f codex-hold
rm -r -- "$HOME/.local/lib/codex-turn-hold"
```

Run `unset -f codex-hold` in other open Bash terminals too.

For rollback, use the backup's `manifest.json` to copy saved files to their
original paths, merging any later configuration edits. Then run
`unset -f codex-hold` and `source "$HOME/.bashrc"`. Backups can be kept after removal.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project layout, local checks,
GitHub Actions workflow, and pull request guidance.

## License

[MIT](LICENSE), copyright (c) 2026 Alex.
