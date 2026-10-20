#!/bin/sh
# The fixed Run Command payload of `scripts/aws-host.sh host-bootstrap` (EC2_VARIANT=macos).
# The wrapper sends these lines with the document AWS-RunShellScript to the one instance of
# the Mac runtime stack. It puts two lines before them: MACOS_PROJECT=<project> and
# MACOS_PHASE=first-boot or MACOS_PHASE=lockdown. The lines hold no secret and no host value.
#
# The payload runs the first-boot stage that the user data wrote, and nothing else. The
# stage reads the environment secret on the host through the instance role, clones and
# checks the pinned snapshot and calls the bootstrap: the same steps as the first boot.
# The phase lockdown adds the operator approval of the account lockdown (--lockdown).
# See docs/variants.md#first-boot-replay and docs/variants.md#recovery-check-before-the-lockdown.
set +x
set -eu
umask 077
: "${MACOS_PROJECT:?}"
case $MACOS_PROJECT in
    [a-z][a-z0-9-]*) ;;
    *) echo 'macos first boot: project refused' >&2; exit 1 ;;
esac
# first-boot runs the stage up to the lockdown gate. lockdown also approves the lockdown.
case ${MACOS_PHASE:-} in
    first-boot) set -- ;;
    lockdown) set -- --lockdown ;;
    *) echo 'macos first boot: phase refused' >&2; exit 1 ;;
esac
etc_dir=/etc/$MACOS_PROJECT
stage=$etc_dir/macos/first-boot.zsh
for path in "$etc_dir" "$etc_dir/macos" "$stage"; do
    if [ -L "$path" ]; then
        echo 'macos first boot: stage path needs review' >&2
        exit 1
    fi
done
# The user data writes the stage before its first secret read. No stage: no replay.
if [ ! -f "$stage" ]; then
    echo 'macos first boot: stage missing; the user data stopped before it' >&2
    exit 1
fi
# Only root can change the stage: root-owned parents that no group or other user can write,
# and a root-owned regular file with mode 0700 and one link.
for path in "$etc_dir" "$etc_dir/macos"; do
    if [ "$(/usr/bin/find "$path" -maxdepth 0 -type d -user 0 ! -perm -020 ! -perm -002)" != "$path" ]; then
        echo 'macos first boot: stage owner or mode refused' >&2
        exit 1
    fi
done
if [ "$(/usr/bin/find "$stage" -maxdepth 0 -type f -user 0 -perm 700 -links 1)" != "$stage" ]; then
    echo 'macos first boot: stage owner or mode refused' >&2
    exit 1
fi
exec /bin/zsh "$stage" "$@"
