#!/bin/sh
# Use the hooks in scripts/git-hooks (pre-commit, pre-merge-commit, pre-push)
# in this repository.
#
# The script copies scripts/git-hooks/dispatch to <git-common-dir>/hooks, once
# for each hook. This is the default hook directory of Git, so core.hooksPath
# stays unset: scripts/publish_portable.py refuses a repository with a set
# core.hooksPath. The directory is shared, so the hooks apply to the main
# checkout and to each worktree. The dispatcher runs scripts/git-hooks/<hook>
# of the working tree where the hook runs, so each worktree uses the hooks and
# scripts/scan.sh of its own branch. On a branch that never had the scan, the
# dispatcher runs no scan and says why; if the branch has the scan in the
# index or in HEAD but not in the working tree, it refuses.
# See docs/secret-handling.md.
#
# Usage: scripts/install-hooks.sh [--check|--uninstall]
#   --check      change nothing; exit 0 if the hooks are installed and current, else 1
#   --uninstall  remove the dispatcher copies that this script wrote

set -eu

HOOKS="pre-commit pre-merge-commit pre-push"
# The second line of scripts/git-hooks/dispatch starts with this text.
MARK="# tenant-ec2 hook dispatcher"

usage() {
    echo "usage: $0 [--check|--uninstall]" >&2
    exit 2
}

mode=install
case $# in
    0) ;;
    1) case $1 in
           --check) mode=check ;;
           --uninstall) mode=uninstall ;;
           *) usage ;;
       esac ;;
    *) usage ;;
esac

git rev-parse --git-dir >/dev/null 2>&1 || {
    echo "error: not inside a Git repository" >&2
    exit 2
}

top=$(git rev-parse --show-toplevel)
# Relative to the top of the working tree in each Git version.
common=$(cd -- "$top" && CDPATH='' cd -- "$(git rev-parse --git-common-dir)" && pwd -P)
dir=$common/hooks
source=$top/scripts/git-hooks/dispatch
current=$(git config --get core.hooksPath || true)

# ours <file>: the file is a copy of a dispatcher of this script (any version).
ours() {
    [ -f "$1" ] && sed -n 2p "$1" | grep -q "^$MARK"
}

case $mode in
    check)
        if [ -n "$current" ]; then
            echo "not installed: core.hooksPath is '$current'; Git does not read $dir" >&2
            exit 1
        fi
        for h in $HOOKS; do
            if [ ! -x "$dir/$h" ] || ! cmp -s "$source" "$dir/$h"; then
                echo "not current: $dir/$h differs from scripts/git-hooks/dispatch; run $0" >&2
                exit 1
            fi
        done
        echo "ok: hooks in $dir"
        exit 0
        ;;
    uninstall)
        for h in $HOOKS; do
            if ours "$dir/$h"; then
                rm -f "$dir/$h"
                echo "removed $dir/$h"
            elif [ -e "$dir/$h" ]; then
                echo "kept $dir/$h: this script did not write it"
            fi
        done
        ;;
    install)
        if [ -n "$current" ]; then
            echo "error: core.hooksPath is '$current'; unset it first (git config --unset core.hooksPath)" >&2
            exit 1
        fi
        [ -f "$source" ] || {
            echo "error: scripts/git-hooks/dispatch is missing" >&2
            exit 1
        }
        # Never replace a hook of another origin.
        for h in $HOOKS; do
            if [ -e "$dir/$h" ] && ! ours "$dir/$h"; then
                echo "error: $dir/$h exists and this script did not write it; move it away first" >&2
                exit 1
            fi
        done
        mkdir -p "$dir"
        for h in $HOOKS; do
            cp "$source" "$dir/$h.tmp"
            chmod 755 "$dir/$h.tmp"
            mv -f "$dir/$h.tmp" "$dir/$h"
        done
        echo "hooks installed in $dir (main checkout and all worktrees)"
        ;;
esac
