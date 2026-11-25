#!/bin/sh
# Standalone experiment. No access credentials, no wiki writes.
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
if ! .venv/bin/python -c 'import requests, bs4' 2>/dev/null; then
    .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python -m unittest discover -s tests -q
mkdir -p reports-live
# Keep the report even if retrieval is incomplete. Do not fail before replay.
rc=0
.venv/bin/python pilot_full.py "$@" --out reports-live/full-work.json || rc=$?
replay_rc=0
.venv/bin/python pilot_full.py "$@" --offline --replay-report reports-live/full-work.json --out reports-live/full-work-replay.json || replay_rc=$?
printf '\nRapport principal : %s\nRapport de reprise : %s\nCodes retour : en ligne=%s hors ligne=%s\n' "$PWD/reports-live/full-work.json" "$PWD/reports-live/full-work-replay.json" "$rc" "$replay_rc"
if [ "$rc" -ne 0 ] || [ "$replay_rc" -ne 0 ]; then exit 1; fi
