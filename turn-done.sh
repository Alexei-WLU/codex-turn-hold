#!/bin/bash
# Legacy marker hook; the persistent monitor does not use this file.
# Codex appends its notification JSON as the last command-line argument.
case "${@: -1}" in
  *agent-turn-complete*) touch /tmp/codex-turn-done ;;
esac
