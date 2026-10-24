# Add this function to ~/.bashrc.
# Start it BEFORE submitting a task in Codex; leave its terminal open.
codex-hold() {
  rm -f /tmp/codex-turn-done || return

  gnome-session-inhibit \
    --inhibit suspend \
    --reason "Codex task running" \
    --app-id codex \
    bash -c 'until [ -e /tmp/codex-turn-done ]; do sleep 30; done'
}
