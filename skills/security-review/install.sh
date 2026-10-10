#!/bin/sh
# Explicit local setup, not part of the review or the deploy path.
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$root"
# Check every destination before creating any link. Keep existing custom commands.
for dest in .claude/skills/security-review .pi/skills/security-review .pi/prompts/security-review.md; do
    case $dest in
        .pi/prompts/*) target=../../skills/security-review/security-review.md ;;
        *) target=../../skills/security-review ;;
    esac
    if [ -e "$dest" ] || [ -L "$dest" ]; then
        if [ ! -L "$dest" ] || [ "$(readlink "$dest")" != "$target" ]; then
            printf '%s\n' 'Skill setup stops: a project destination already exists. Keep it and choose the explicit invocation in the guide.' >&2
            exit 1
        fi
    fi
done
mkdir -p .claude/skills .pi/skills .pi/prompts
for dest in .claude/skills/security-review .pi/skills/security-review .pi/prompts/security-review.md; do
    case $dest in
        .pi/prompts/*) target=../../skills/security-review/security-review.md ;;
        *) target=../../skills/security-review ;;
    esac
    [ -L "$dest" ] || ln -s "$target" "$dest"
done
printf '%s\n' 'Skill setup: project links ready. Restart Claude Code or reload Pi in this clone.'
