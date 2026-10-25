# Source this file from ~/.bashrc; leave codex-hold running in one terminal.
# Resolve the helper when sourced, so later directory changes do not affect it.
_CODEX_HOLD_HELPER="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/codex-monitor.py" || return

# Exec replaces only the function's subshell, keeping the calling shell open.
# System Python supplies Ubuntu's packaged websocket and dbus modules.
codex-hold() (
  exec /usr/bin/python3 -B "$_CODEX_HOLD_HELPER" "$@"
)
