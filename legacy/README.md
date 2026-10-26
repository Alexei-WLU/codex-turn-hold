# Legacy marker examples

These files preserve the old notification-based approach for reference:

- [`turn-done.sh`](turn-done.sh) creates `/tmp/codex-turn-done` when its last
  argument contains `agent-turn-complete`.
- [`config-snippet.toml`](config-snippet.toml) shows the old dedicated Codex
  notification setting.

The current persistent monitor uses neither file nor the shared marker.
For installation and upgrades, follow the [current user guide](../README.md#install).
The installer still recognizes the old hook at `<CODEX_HOME>/turn-done.sh`;
archiving these examples does not change that installed path.

Do not run the old hook while an unrelated legacy holder is active: creating
the shared marker may release that holder's suspend inhibition.
