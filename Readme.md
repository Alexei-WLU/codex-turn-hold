# Codex turn hold

For **native Ubuntu 24.04 GNOME Desktop**, using Bash and local Codex CLI.
Manually prevent automatic suspend during a Codex turn, then release the hold
when Codex reports that the turn has completed. Codex stays open.

Keep Ubuntu's **Automatic suspend: On, 30 minutes** setting. The screen may
still blank or lock; this setup inhibits suspend only.

## Files

| File | Purpose |
| --- | --- |
| `turn-done.sh` | Codex notification hook; creates `/tmp/codex-turn-done`. |
| `codex-hold.bash` | Bash function; holds GNOME suspend until the marker appears. |
| `config-snippet.toml` | The one setting to merge into your Codex configuration. |

The marker is shared, and the holder checks it every 30 seconds.

## One-time setup

Extract `codex-turn-hold.zip` and open a terminal in the extracted
`codex-turn-hold` folder. Run the setup as your normal desktop user.

### 1. Install the hook

```bash
mkdir -p "$HOME/.codex"
install -m 755 turn-done.sh "$HOME/.codex/turn-done.sh"
```

This copies the hook and makes it executable, so it can also be run directly.

### 2. Configure Codex

Add this line to your existing user-level `~/.codex/config.toml`, **above the
first `[section]` header**:

```toml
notify = ["bash", "/home/YOU/.codex/turn-done.sh"]
```

Replace `/home/YOU` with your actual home path (`echo "$HOME"` displays it).
Do not use `~` or `$HOME` inside the TOML string. Keep your other settings.
Running the hook through `bash` means it still works if the file ever loses its
executable bit. Codex ignores `notify` in project-level `.codex/config.toml`
files, so it must be in the user-level file.

If you already have `notify` configured, keep that command and add
`touch /tmp/codex-turn-done` to its existing `agent-turn-complete` handler
instead. Do not add a second `notify` key. Write the marker before any desktop
alert command that might fail.

### 3. Add the Bash function

Copy the function in `codex-hold.bash` into `~/.bashrc`, replacing any earlier
`codex-hold` definition. Load it in the terminal where you will run the holder:

```bash
source "$HOME/.bashrc"
```

Exit and reopen Codex after changing its configuration, when no task is running.

## Usage for each long turn

1. Start Codex normally with `codex` and leave it waiting for your task.
2. In a second terminal, run:

   ```bash
   codex-hold
   ```

   It normally waits silently. Leave this terminal open. If it reports an error
   or returns immediately, check the error before relying on the hold.
3. **Now submit your task in Codex.** Start the holder before submitting the
   task so it cannot erase a completion marker that has already arrived.
4. When Codex completes the turn, the hook creates the marker. The holder exits
   on its next check (within about 30 seconds), and Ubuntu's normal
   automatic-suspend policy resumes.

   **Expect a fast suspend.** GNOME counts idle time from your last keyboard or
   mouse input, not from the release. If you have been away longer than the
   30-minute delay, the PC suspends within seconds of the holder exiting, not
   30 minutes later. This is normal. Codex is still open at its prompt when you
   wake the machine.
5. Run `codex-hold` again **before** submitting the next long turn.

There is no need for a wrapper around the Codex process. This setup does not
depend on Codex's experimental **Prevent sleep while running** toggle (in
`/experimental`). That feature is new, and GNOME's automatic suspend does not
honor systemd-level inhibitors taken by the same user, so it is not relied on
here.

## Quick verification on your PC

Before relying on it for a long task:

1. Start `codex-hold`.
2. In another terminal, run `gnome-session-inhibit --list`. Look for the
   inhibition with reason **Codex task running**.
3. Ask Codex: **Reply with DONE.** Wait for it to finish.
4. Within about 30 seconds, the holder should return to its shell prompt.
   Run `gnome-session-inhibit --list` again; that inhibition should be gone.

If `gnome-session-inhibit` is missing, install its Ubuntu package:

```bash
sudo apt install gnome-session-bin
```

If the hold remains after Codex finishes, check the configured absolute path
and that Codex was restarted after the setting was added. You can also run the
hook by hand; it should create the marker:

```bash
bash "$HOME/.codex/turn-done.sh" '{"type":"agent-turn-complete"}'
ls -l /tmp/codex-turn-done
```

## Limits and manual stop

- If you cancel a task, Codex crashes, or the notification fails, the holder
  may keep running. Press **Ctrl+C in the holder's terminal** to release it.
- An approval or input prompt during a turn does not necessarily end that turn.
  The automatic release follows the completion notification.
- Any completion that invokes this hook can release the shared hold. Suspend
  timing afterward is controlled by Ubuntu, not this script.
- The supplied hook matches `agent-turn-complete` anywhere in its final
  argument. Unrelated or malformed input containing that text also creates the
  marker. This behavior is retained intentionally; use Codex notification
  payloads with the hook.

## Verification and references

Earlier script verification on 2026-10-24: both shell files passed `bash -n`
and ShellCheck 0.11 with no findings; the TOML snippet parses, and placing it
below a `[section]` header was confirmed to misfile the key under that section.
`turn-done.sh` was run with a Codex-style `agent-turn-complete` payload, with
another event type, and with
no arguments. `codex-hold` was exercised against a stand-in
`gnome-session-inhibit` that checks option order: a stale marker is cleared at
start, the hold releases within one 30-second poll after the marker appears, an
unremovable marker makes the function fail before inhibiting, and Ctrl+C through
a pseudo-terminal terminates the holder. Both reference links were fetched and
confirm the `notify` and `gnome-session-inhibit` behaviour described above.

The quick check above verifies actual notification delivery and inhibitor
registration on your PC. Actual automatic suspension was not tested here.

### Desktop installation verification

Installed and verified on the target Ubuntu 24.04.5 GNOME Wayland desktop on
2026-10-24, using Codex CLI 0.164.0 and `gnome-session-bin` 46.0-1ubuntu4.
The supplied scripts were installed unchanged. AC automatic suspend is enabled
with a 1,800-second timeout; all other power, screen blanking, and locking
settings were preserved.

Eleven verification groups passed, including Bash syntax, TOML parsing,
preservation of existing Codex settings and file permissions, exact hook-copy
comparison, and loading the supplied function in a fresh interactive Bash
shell. ShellCheck was unavailable on this desktop and was not rerun.

- Completion, unrelated-event, empty-argument, and direct hook execution
  behaved as expected. The accepted broad text matching was also reproduced.
- The holder cleared a stale marker, registered a real GNOME suspend-only
  inhibitor, and released it 29.44 seconds after the hook ran. An unremovable
  marker prevented inhibition, and Ctrl+C through a terminal removed the
  inhibitor.
- A fresh, ephemeral, read-only Codex session replied `DONE` in 5.91 seconds.
  Its actual notification created the marker, and the holder released
  24.62 seconds after the marker was observed. The running Codex daemon was
  not restarted.

These checks took 60.33 seconds. They verified notification delivery and the
GNOME inhibitor lifecycle without triggering an actual suspend. Configuration
backups, original power settings, and `verification-results.json` are kept
outside the repository under `~/.local/state/codex-turn-hold/backups/`.

- [Codex notify documentation](https://learn.chatgpt.com/docs/config-file/config-advanced#notifications)
- [Ubuntu 24.04 gnome-session-inhibit manual](https://manpages.ubuntu.com/manpages/noble/man1/gnome-session-inhibit.1.html)
