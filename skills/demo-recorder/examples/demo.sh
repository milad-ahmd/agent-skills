#!/usr/bin/env bash
# Record: asciinema rec demo.cast --overwrite -c 'bash examples/demo.sh'
# Render: agg --font-size 22 demo.cast demo.gif
PROMPT='$ '
DELAY=0.05
show() { local s="$1" d="${2:-$DELAY}" i; printf '%s' "$PROMPT"; for ((i=0; i<${#s}; i++)); do printf '%s' "${s:i:1}"; sleep "$d"; done; sleep 0.4; printf '\n'; }
clear
# Demo for a CLI called "mytool". One task, about 20 seconds.
# Setup runs hidden: a throwaway HOME and a sample project, so no real config or tokens show up.
eval 'export HOME="$(mktemp -d)"' >/dev/null 2>&1
eval 'cp -r examples/sample-project /tmp/sample-project && cd /tmp/sample-project' >/dev/null 2>&1
clear
show 'mytool --version'
eval 'mytool --version'
sleep 2
show 'mytool scan .'
eval 'mytool scan .'
sleep 2
sleep 1
show 'mytool scan . --fix'
eval 'mytool scan . --fix'
sleep 2
# The payoff: the second scan is clean.
show 'mytool scan .'
eval 'mytool scan .'
sleep 2
printf '%s' "$PROMPT"
sleep 3
