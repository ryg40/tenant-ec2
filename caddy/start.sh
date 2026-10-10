#!/bin/sh
# Start the core edge. This script is the command of the service `caddy`
# (compose.caddy.yaml). It runs in the container, as the user of the service.
#
# Usage: start.sh [--print]
#   --print   print the configuration that the script would start; start nothing
#
# The script reads four names from the environment of the container:
#   EC2_DOMAIN       the domain subtree; necessary
#   EC2_EDGE_NAMES   the public names: a list with commas and no blank. The
#                    known names are ok, agent, llm and ov; necessary
#   EC2_ADMIN_CIDR   the one IPv4 range that reaches each name but ok, as
#                    <address>/<prefix length> with a prefix length from 16 to
#                    32; necessary
#   EC2_ACME_EMAIL   the address of the certificate account; can be empty
# It reads no secret.
#
# Caddy puts the value of a name into the text of its configuration before it
# reads that text. So the script tests the form of each value first, and it
# stops on a value that does not have the form of its kind. It stops also on a
# name of the list that it does not know.
#
# The configuration is a short file in the temporary directory. It imports the
# files of the directory of this script:
#   options.caddy        the global options
#   common.caddy         the shared parts, the health check and the answer for
#                        each name that has no route
#   sites/<name>.caddy   one public name. Only a name of the list is imported,
#                        so a name outside the list has no route.
# Caddy runs in place of this script, so it gets each signal of the container.
#
# Exit: 1 a value is missing or not valid, 2 usage.

set -eu

KNOWN="ok agent llm ov"

die() {
    echo "caddy-start: error: $*" >&2
    exit 1
}

# valid <name> <value> <pattern>: the message gives no value.
valid() {
    [ -n "$2" ] || die "$1 has no value"
    if printf '%s\n' "$2" | grep -Eqx -- "$3"; then
        return 0
    fi
    die "$1 is not valid: the value does not have the form of its kind"
}

print=0
case ${1:-} in
    '') ;;
    --print) print=1 ;;
    *) sed -n '2,/^$/s/^# \{0,1\}//p' "$0" >&2; exit 2 ;;
esac
[ $# -le 1 ] || { sed -n '2,/^$/s/^# \{0,1\}//p' "$0" >&2; exit 2; }

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

domain=${EC2_DOMAIN:-}
names=${EC2_EDGE_NAMES:-}
cidr=${EC2_ADMIN_CIDR:-}
email=${EC2_ACME_EMAIL:-}

valid EC2_DOMAIN "$domain" '[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?'
octet='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
valid EC2_ADMIN_CIDR "$cidr" "$octet\\.$octet\\.$octet\\.$octet/(1[6-9]|2[0-9]|3[0-2])"
[ -z "$email" ] || valid EC2_ACME_EMAIL "$email" '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+'
valid EC2_EDGE_NAMES "$names" '[a-z0-9]+(,[a-z0-9]+)*'

sites=
old_ifs=$IFS
IFS=,
for name in $names; do
    case " $KNOWN " in
        *" $name "*) ;;
        *) die "EC2_EDGE_NAMES holds a name that the edge does not know; the known names are: $KNOWN" ;;
    esac
    case " $sites " in
        *" $name "*) die "EC2_EDGE_NAMES holds a name two times" ;;
    esac
    [ -f "$here/sites/$name.caddy" ] || die "the file sites/$name.caddy is not there"
    sites="$sites $name"
done
IFS=$old_ifs

# configuration: print the text that Caddy reads.
configuration() {
    echo "{"
    echo "	import $here/options.caddy"
    [ -z "$email" ] || echo "	email $email"
    echo "}"
    echo "import $here/common.caddy"
    for name in $sites; do
        echo "import $here/sites/$name.caddy"
    done
}

if [ "$print" = 1 ]; then
    configuration
    exit 0
fi

file=${TMPDIR:-/tmp}/Caddyfile
configuration >"$file"
echo "caddy-start: the public names are:$sites"
exec caddy run --config "$file" --adapter caddyfile
