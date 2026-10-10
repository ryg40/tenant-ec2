#!/bin/sh
# Close the instance metadata address for the containers of the host, with one exception
# for the gateway. The bootstrap (scripts/host-bootstrap.sh) installs a copy of this script
# as /usr/local/sbin/tenant-ec2-metadata-guard and starts it with one systemd unit.
#
# Usage: scripts/host-metadata-guard.sh [--dry-run] apply <address of the gateway>
#        scripts/host-metadata-guard.sh [--dry-run] close
#        scripts/host-metadata-guard.sh check [<address of the gateway>]
#        scripts/host-metadata-guard.sh status
#   apply    make the rules with the exception for the one address, when they are not
#            there. The systemd unit runs it before Docker starts, at each boot.
#   close    make the rules with no exception: no container reaches the address
#   check    exit 0 when the rules are the rules of `apply` with this address, or of
#            `close` with no address; exit 1 when they are not. It prints nothing.
#   status   print the rules
#   --dry-run  print each iptables command that changes a rule; change nothing
# No command needs Docker.
#
# Why: the instance role is the only AWS credential of the host. The metadata address
# 169.254.169.254 gives the role to each process that reaches it. A container on a network
# with a route to the internet reaches the address through the host. Only the gateway
# needs the role: its route to Amazon Bedrock has no key.
#
# The rules, in the table `filter`:
#   DOCKER-USER       -d 169.254.169.254/32 -j TENANT-EC2-IMDS   the first rule of the chain
#   TENANT-EC2-IMDS   -s <address of the gateway>/32 -j RETURN   no rule with `close`
#   TENANT-EC2-IMDS   -j DROP
# Docker sends each packet that a container routes through the host to the chain
# DOCKER-USER first, and Docker does not remove a rule of that chain. A packet of the host
# itself does not pass the chain. So the AWS CLI and the SSM agent of the host keep the
# metadata address.
#
# The gateway is the container of the Compose service `litellm`. compose.yaml gives it
# one fixed address on the network `egress`, outside the range from which Docker gives an
# address to another container. So the exception is one static rule, and no process must
# follow the containers: the rule is correct while the gateway runs, while it does not
# run, and after Docker starts again. The bootstrap reads the address from compose.yaml
# and writes it into the unit.
# `apply` and `close` put the jump in the first place of DOCKER-USER again when another
# rule is before it. They put a DROP rule first, then they remove the old rules, then
# `apply` adds the exception. At each moment the chain refuses each address that is not
# the address of the gateway.
#
# The unit: Docker needs it (`RequiredBy=docker.service`). When the rules cannot be made,
# Docker does not start, so no container runs with an open metadata address.
# Warning: do not start the unit again by hand (`systemctl restart`): systemd then starts
# Docker and each container again. Run this script by hand: it changes the rules at once.
#
# IPv6: the rules are IPv4 only. The template does not start the IPv6 metadata address,
# and no Compose network has IPv6.
#
# This script reads no secret: its one value is the address in its argument.
#
# Exit: 0 pass, 1 an error or a failed `check`, 2 usage.
# Needs: iptables.

set -eu

ADDRESS=169.254.169.254
CHAIN=TENANT-EC2-IMDS
JUMP="-A DOCKER-USER -d $ADDRESS/32 -j $CHAIN"
# Wait this number of seconds for the lock of iptables.
LOCK_WAIT=10

say() {
    echo "metadata-guard: $*"
}

die() {
    echo "metadata-guard: error: $*" >&2
    exit 1
}

usage() {
    sed -n '2,/^$/s/^# \{0,1\}//p' "$0"
}

dry_run=0
if [ "${1:-}" = --dry-run ]; then
    dry_run=1
    shift
fi
command=${1:-}
gateway=${2:-}
case $command in
    -h | --help) usage; exit 0 ;;
    apply) [ $# -eq 2 ] || { usage >&2; exit 2; } ;;
    check) [ $# -le 2 ] || { usage >&2; exit 2; } ;;
    close | status) [ $# -eq 1 ] || { usage >&2; exit 2; } ;;
    *) usage >&2; exit 2 ;;
esac
if [ -n "$gateway" ]; then
    # The exact text that iptables prints, so that `check` can compare.
    octet='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
    printf '%s\n' "$gateway" | grep -Eqx "$octet\\.$octet\\.$octet\\.$octet" ||
        die "the address of the gateway is not an IPv4 address"
fi

# The text of `iptables -S` for the chain with the correct rules.
if [ -n "$gateway" ]; then
    want="-N $CHAIN
-A $CHAIN -s $gateway/32 -j RETURN
-A $CHAIN -j DROP"
else
    want="-N $CHAIN
-A $CHAIN -j DROP"
fi

# ipt <argument>...: one iptables command that changes a rule.
ipt() {
    if [ "$dry_run" = 1 ]; then
        say "would run: iptables -w $LOCK_WAIT $*"
        return 0
    fi
    iptables -w "$LOCK_WAIT" "$@"
}

# rules <chain>: the rules of a chain as text, or nothing when the chain is not there.
rules() {
    iptables -w "$LOCK_WAIT" -S "$1" 2>/dev/null || true
}

# The chain has the correct rules, and the jump is one time in DOCKER-USER, in the first
# place. The first line of the text of a chain is its name, so the first rule is line 2.
correct() {
    [ "$(rules "$CHAIN")" = "$want" ] || return 1
    user=$(rules DOCKER-USER)
    [ "$(printf '%s\n' "$user" | sed -n 2p)" = "$JUMP" ] || return 1
    [ "$(printf '%s\n' "$user" | grep -cFx -- "$JUMP")" -eq 1 ]
}

# Make the two chains and the jump when they are not there. A new chain gets the DROP rule.
ensure() {
    [ -n "$(rules DOCKER-USER)" ] || ipt -N DOCKER-USER
    if [ -z "$(rules "$CHAIN")" ]; then
        ipt -N "$CHAIN"
        ipt -A "$CHAIN" -j DROP
    fi
    # The jump must be the first rule: an earlier rule of another tool can accept a
    # packet before the jump reads it.
    [ "$(rules DOCKER-USER | sed -n 2p)" = "$JUMP" ] || ipt -I DOCKER-USER 1 -d "$ADDRESS/32" -j "$CHAIN"
    if [ "$dry_run" = 1 ]; then
        return 0
    fi
    # Remove each older jump below the first rule: rule n is line n + 1 of the text.
    while :; do
        number=$(rules DOCKER-USER | awk -v jump="$JUMP" 'NR > 2 && $0 == jump { print NR - 1; exit }')
        [ -n "$number" ] || break
        ipt -D DOCKER-USER "$number"
    done
}

apply() {
    # No change when the rules are correct: the gateway then loses no request.
    ! correct || return 0
    ensure
    if [ "$(rules "$CHAIN")" != "$want" ]; then
        # The DROP rule goes first. From here the chain refuses each address.
        ipt -I "$CHAIN" 1 -j DROP
        if [ "$dry_run" = 1 ]; then
            say "would remove each older rule of $CHAIN"
        else
            while iptables -w "$LOCK_WAIT" -D "$CHAIN" 2 2>/dev/null; do :; done
        fi
        [ -z "$gateway" ] || ipt -I "$CHAIN" 1 -s "$gateway/32" -j RETURN
    fi
    [ "$dry_run" = 0 ] || return 0
    correct || die "the rules are not correct after the change"
    if [ -n "$gateway" ]; then
        say "the metadata address is closed; the one exception is the address of the gateway"
    else
        say "the metadata address is closed; there is no exception"
    fi
}

case $command in
    apply | close)
        apply
        ;;
    check)
        correct
        ;;
    status)
        iptables -w "$LOCK_WAIT" -S DOCKER-USER
        iptables -w "$LOCK_WAIT" -S "$CHAIN"
        ;;
esac
