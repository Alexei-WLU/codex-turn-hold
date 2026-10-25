# Persistent Codex sleep monitor

For **Ubuntu 24.04 GNOME Desktop and Codex CLI 0.164.0**, using Bash.
Run `codex-hold` once in a separate terminal and leave it running. It prevents
suspend while any conversation or sub-agent on the shared local Codex server
is working. After everything has been idle or waiting for you for **180
continuous seconds**, it releases its hold. Later work acquires a new hold
automatically. Starting the monitor during an existing task also works.

The monitor changes no power, screen-blanking, or locking settings. It inhibits
**suspend only**; your screen can still blank and lock. Releasing the hold lets
GNOME apply its existing power policy; it does not itself request suspend or
reset GNOME's inactivity timer. If that timer has already elapsed, automatic
suspend can follow quickly.

## Setup and migration

Run these commands from the repository as your normal desktop user:

```bash
sudo apt-get install python3-websocket python3-dbus
/usr/bin/python3 -B install-monitor.py
source "$HOME/.bashrc"
codex-hold
```

Use the system `/usr/bin/python3`, so Ubuntu's installed modules are available.
No pip packages, virtual environment, login service, or Codex wrapper are needed.
The command stays in the foreground until you stop it. Start it manually again
after closing that terminal, logging out, or rebooting.

The installer:

- Copies `codex-hold.bash` and `codex-monitor.py` to
  `~/.local/lib/codex-turn-hold/`.
- Backs up `.bashrc`, the Codex configuration, and any previous installed
  launcher/helper under `~/.local/state/codex-turn-hold/backups/<timestamp>/`
  (or `$XDG_STATE_HOME/codex-turn-hold/backups/`). It prints the backup path.
- Replaces the known copied function in `.bashrc` with
  `source "$HOME/.local/lib/codex-turn-hold/codex-hold.bash"`. It refuses to
  overwrite an unrecognized custom function or marked block.
- Removes the top-level `notify` setting only when its value is exactly
  `["bash", "<CODEX_HOME>/turn-done.sh"]`, the dedicated old hook. `CODEX_HOME`
  defaults to `~/.codex`. Other notification commands and configuration stay
  intact. Existing file permissions are preserved, including those of an
  already-installed launcher/helper.
- Reads both source files and validates the migrations before updating the
  installation. Symlinked configuration files, install directories, and
  installed launcher/helper files require manual review and are left untouched.

The new monitor never reads, creates, or deletes `/tmp/codex-turn-done`.
The legacy hook file may remain installed. A running Codex server might continue
to call its already-loaded old hook until its next normal restart; those markers
do not affect the new monitor. There is no need to interrupt an existing task.

If you already have the old `codex-hold` running, stop it with Ctrl+C before
starting the new monitor. Source the updated `.bashrc` in the monitor terminal;
other already-open shells may still have the old function loaded.

For development without installing:

```bash
source ./codex-hold.bash
codex-hold
```

## How the two timers work

The monitor polls every two seconds. A complete scan has a five-second deadline
shared by connection setup and every socket read/write, including fragmented
responses and WebSocket control messages. Late final responses are rejected;
slow or failed scans may delay the next poll. Every page and every loaded thread
is checked, including sub-agents. No conversation is resumed or subscribed to,
and turn history is not requested.

| Observation | Behavior |
| --- | --- |
| Any `active` thread with no waiting flags | Acquire one suspend hold; cancel the idle countdown. |
| All threads `idle`, `notLoaded`, or waiting on approval/input | If holding, begin the 180-second idle countdown. |
| Continued idle/waiting | Keep the hold until the first successful check at or after the deadline. |
| Work resumes during the countdown | Keep the same hold without interruption; discard the countdown. |
| Startup while already idle/waiting | Allow sleep immediately; do not acquire a hold. |
| Any failed check | Cancel the idle countdown and begin a separate 180-second recovery deadline. |
| Repeated failures | Keep an existing hold until that fixed deadline; retries do not extend it. |
| Reliable status returns before recovery expires | Resume normal tracking; idle starts a fresh 180-second countdown. |
| Recovery expires | Release the hold and keep retrying. Reliable active work can acquire it again. |

Waiting flags are `waitingOnApproval` and `waitingOnUserInput`. An active
sub-agent still keeps the hold when its parent is idle or waiting. A canceled
turn follows the idle timer when Codex reports it as idle.

Missing or malformed status, unknown status types/flags, `systemError`, RPC
errors, bad pagination, timeouts, or a closed Codex socket count as failed
checks. Even if another thread reports active work, an incomplete scan enters
recovery. A failure while already allowing sleep never acquires a speculative
hold. Recovered idle status after the hold expired also leaves sleep allowed.

Both timers use a monotonic clock. Codex scans during recovery are bounded by
the remaining recovery time; a result arriving at or after expiry cannot renew
the old hold. GNOME D-Bus calls have separate two-second timeouts. A Codex
disconnect triggers recovery and reconnection; a GNOME session-bus failure is
fatal and prints an error. The inhibitor is
owned by a private D-Bus connection and released on Ctrl+C, SIGTERM, SIGHUP,
or normal cleanup. GNOME removes it if the process crashes or its D-Bus
connection closes.

## Messages and stopping

Messages appear on transitions, rather than on every poll:

| Message | Meaning |
| --- | --- |
| `Active work; suspend inhibited.` | The monitor acquired its hold. |
| `All threads idle or waiting; allowing sleep in 180s if this continues.` | The idle countdown has started; the hold remains. |
| `Active work resumed; keeping suspend inhibited.` | The countdown was canceled without releasing the hold. |
| `Status unavailable (...); keeping the existing hold for up to 180s; retrying.` | Status recovery has started. |
| `Status recovered.` | A complete, valid status scan succeeded again. |
| `Sleep allowed; ...` | This monitor owns no inhibitor; it continues checking. Other applications may still inhibit suspend. |

Press **Ctrl+C in the monitor terminal** to stop it and release its hold.
Termination with `kill -TERM <pid>` also cleans up. Duplicate monitor processes
for the same user are rejected. The advisory lock is under `$XDG_RUNTIME_DIR`
or the state directory when no runtime directory is set. Its file remains after
exit; the kernel releases the lock automatically, so do not delete it manually.

## Troubleshooting and limits

- **Missing dependency:** run the package installation above. The launcher
  deliberately uses `/usr/bin/python3`, regardless of your shell's Python.
- **GNOME connection error:** run in a terminal in your logged-in GNOME session,
  as the desktop user, without `sudo`. SSH sessions and other desktop
  environments are outside the target setup.
- **Status unavailable:** check that a normal `codex` terminal is open and that
  `CODEX_HOME` matches. The default socket is
  `~/.codex/app-server-control/app-server-control.sock`. The monitor does not
  start or restart Codex. Starting it before Codex is fine: it retries while
  allowing sleep. For a read-only diagnostic that acquires no inhibitor:

  ```bash
  codex-hold --check
  ```

  This prints counts of loaded and working threads, or exits with a clear error.
  It prints no conversation titles, prompts, or history.
- **Already running:** use the existing monitor terminal. If its lock really is
  held, a process still owns it; an old lock file by itself is harmless.
- **Unexpected persistent hold:** inspect `gnome-session-inhibit --list` for
  the owner. The new app id is `codex-turn-hold`, with reason
  `Codex work or idle grace period`. Old marker holders and Codex's own
  sleep-prevention feature can own separate inhibitors.
- **Codex upgrades:** this integration targets CLI 0.164.0. New status flags or
  a changed local transport fail into bounded recovery; run the tests and
  read-only diagnostic after upgrades before relying on it.

Only the shared local server selected by `CODEX_HOME` is covered. Isolated
Codex runs, other servers/users, and commands left running after Codex becomes
idle are excluded. Activity shorter than the polling interval can be missed.
Thread enumeration is not an atomic snapshot; the next poll observes newly
loaded work. The hold cannot override forced suspend, logout, shutdown, or
every laptop lid policy. No guarantee of continued work is made during an
outage beyond the recovery deadline.

`turn-done.sh` and `config-snippet.toml` remain **legacy compatibility examples**.
The hook intentionally matches `agent-turn-complete` anywhere in its last
argument and writes the shared marker. Do not add its `notify` setting for the
persistent monitor. Custom notification commands can remain configured.

## Verification

Run the repeatable checks from the repository:

```bash
bash -n codex-hold.bash && bash -n turn-done.sh
/usr/bin/python3 -B -m unittest -v
/usr/bin/python3 -B - <<'PY'
import ast
from pathlib import Path
for path in Path('.').glob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
PY
# When installed:
shellcheck codex-hold.bash turn-done.sh
```

The standard-library tests use a simulated clock and fake transports/inhibitors.
They cover repeated turns, overlapping conversations, active sub-agents, both
waiting states, cancellation, startup during work, pagination, malformed
responses, socket closure, reconnection, both timer boundaries, recovery,
fragment deadlines, redirect rejection, duplicate locks, cleanup, and
conservative migration. Installer tests use temporary home/state directories
to check backups, permissions, repeated installs, and failures before writes.
A launcher test covers relative sourcing with `CDPATH` set. Subprocess tests
send real SIGINT, SIGTERM, and SIGHUP signals and check duplicate process locks
using fake desktop/server adapters. These checks do not touch the shared
completion marker or change any desktop setting.

For a two-turn desktop check, stay at the PC; releasing an inhibitor may permit
an already-due automatic suspend:

1. Open normal Codex and start `codex-hold` in another terminal. If everything
   is idle, it should report sleep allowed.
2. Submit a task that takes longer than two seconds. Run
   `gnome-session-inhibit --list` while it is working and confirm one
   `codex-turn-hold` suspend inhibitor.
3. After the task finishes, check that the inhibitor remains before 180 seconds
   and disappears on the first poll afterward. The monitor stays running.
4. Submit a second task. Confirm it reacquires a hold automatically. During its
   subsequent idle countdown, submit more work before 180 seconds: the same
   hold should remain, and the countdown should restart after work ends.
5. Press Ctrl+C and confirm that this monitor's inhibitor disappears. GNOME's
   power, screen-blanking, and lock settings should be unchanged.

Actual automatic suspension is not part of these checks.

### Correctness review

After the fixes to scan deadlines, recovery expiry, and installation, all 47
tests pass, along with Bash/Python syntax, TOML parsing, and whitespace checks.
Additional probes using the installed `websocket-client` 1.7.0 verified that
fragmented responses and repeated ping frames stop at the scan deadline.
The repository helper's read-only `--check` succeeded against the live Codex
0.164.0 server; locally generated schemas match the requested status fields.
ShellCheck was unavailable. Actual suspend and the two-turn desktop lifecycle
were not exercised during this review, and no installed files were updated.

### Original desktop installation verification

Installed and verified on 2026-10-24 (America/Toronto), with Ubuntu 24.04.5
GNOME Wayland, Codex CLI 0.164.0, Python 3.12.3, `python3-websocket` 1.7.0-1,
and `python3-dbus` 1.3.2-5build3.

- All 32 unit tests, Bash/Python syntax checks, TOML parsing, and
  `git diff --check` passed. ShellCheck was unavailable on this PC.
- Twelve desktop verification groups passed. These used real GNOME inhibitors
  with a simulated clock to check both 180-second deadlines, uninterrupted
  holds, repeated work, recovery, and reacquisition. A temporary independent
  suspend guard covered the verification and was removed afterward.
- A read-only probe exercised multiple pages of the actual shared Codex
  server. The implemented diagnostic read three loaded threads, including the
  active conversation, without requesting turn history.
- Real monitor processes passed duplicate detection, SIGTERM, SIGHUP,
  SIGKILL/crash cleanup, and Ctrl+C through a pseudo-terminal using the Bash
  launcher. All verification inhibitors were removed.
- Installed files match the repository. The migrated configuration differs
  only by removal of the dedicated legacy `notify`. Backups, original file
  permissions, fresh interactive Bash loading, and unchanged power, blanking,
  and locking settings were verified.

This verifies status reads and inhibitor behavior; the real-time two-turn
manual check above and actual automatic suspend were not run. No existing
Codex task was interrupted or restarted. The monitor is installed and ready
for manual startup, with no background monitor left by the verification.

This installation's backups, `manifest.json`, `verification-results.json`, and
desktop verifier are under:

```text
~/.local/state/codex-turn-hold/backups/20261025T003923.332003Z/
```

## Rollback

Stop the new monitor first. Use the backup directory printed by the installer.
`manifest.json` records original paths and whether each file existed:

```bash
backup_dir="$HOME/.local/state/codex-turn-hold/backups/<timestamp>"
cp -p "$backup_dir/bashrc" "$HOME/.bashrc"
# Only if config.toml existed; use its original path from manifest.json:
cp -p "$backup_dir/config.toml" "${CODEX_HOME:-$HOME/.codex}/config.toml"
unset -f codex-hold
source "$HOME/.bashrc"
```

Restoring these files also restores their other settings as of the backup;
merge the relevant lines instead if you made later changes. For a previous
persistent installation, restore the backed-up `codex-hold.bash` and
`codex-monitor.py` into `~/.local/lib/codex-turn-hold/`. For a first installation,
that directory can be removed once no shell sources it. The legacy hook is
left in place for rollback. Let Codex reload the restored notification setting
at its next normal restart when no task is running.

## Files and references

All project files remain at the repository root:

| File | Purpose |
| --- | --- |
| `codex-hold.bash` | Sourced Bash launcher using the system Python. |
| `codex-monitor.py` | Local status client, two timers, single-instance lock, GNOME inhibitor. |
| `install-monitor.py` | Installation, backups, and migration of known old settings. |
| `test_monitor.py` | Standard-library unit tests with simulated time. |
| `turn-done.sh`, `config-snippet.toml` | Legacy marker hook and notification example. |

The transport and metadata-only status reads follow the
[Codex app-server documentation](https://learn.chatgpt.com/docs/app-server#read-a-stored-thread-without-resuming).
The 0.164.0 schemas generated locally with `codex app-server generate-json-schema`
confirm the pagination and status fields. The GNOME hold uses `Inhibit` with
flag `4` and `Uninhibit`, as described in the
[GNOME SessionManager D-Bus documentation](https://gnome.pages.gitlab.gnome.org/gnome-session/re04.html).
