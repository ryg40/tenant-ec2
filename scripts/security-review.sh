#!/bin/sh
# Advice only. Read file values inside the helper; return only redacted facts.
set +x
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Not verified: Python 3 is missing. The advisory review cannot run.'
    exit 0
fi
here=$(CDPATH='' cd -- "$(dirname -- "$0")/.." 2>/dev/null && pwd)
if [ -z "$here" ]; then
    printf '%s\n' 'Not verified: the kit directory is unavailable.'
    exit 0
fi
if ! python3 -I "$here/skills/security-review/review.py" "$here" "$@" 2>/dev/null; then
    printf '%s\n' 'Not verified: the advisory reader could not finish. No approval follows from this result.'
fi
exit 0
