#!/bin/sh
# The DNS records of the host of tenant-ec2: print them, or set them through one DNS
# provider. The `dns` command of scripts/aws-host.sh calls this script with the Elastic IP
# of the stack. The script also runs alone. See docs/options/dns.md.
#
# Usage: scripts/dns-records.sh [--dry-run] [--env-file <path>] [--address <address>] <command>
#   plan
#       Print the record set and change nothing. The command makes no call, with each
#       provider, and it reads no credential. Without --address, it shows a stand-in for
#       the address.
#   apply
#       Set the record set through the provider of EC2_DNS_PROVIDER. The command reads
#       each record first. It changes only a record that differs: it makes a record that
#       is not there, and it corrects a record with a different address. A record that is
#       correct gets no change, so a second run changes nothing. The provider manual makes
#       no call: the command prints each record, and the installer sets it by hand.
#   --dry-run            print each command of `apply` and make no call
#   --env-file <path>    the environment file; the default is .env of this repository
#   --address <address>  the IPv4 address of each record: the output ElasticIp of the
#                        stack. A real `apply` needs it.
#   --help               print this text
#
# The record set. One A record to the address for each name of EC2_EDGE_NAMES below
# EC2_DOMAIN, and one for the name pangolin with the option pangolin. No wildcard record.
# The order is pangolin, ok, llm, ov, agent. The TTL of a new record is 600 seconds.
#
# Settings. Each one is a name of the environment file. The script reads each name from
# the file only, never from the shell environment.
#   EC2_DOMAIN         the domain of the host: each record is one label below it
#   EC2_OPTIONS        a list of option names with commas and no blank; the known names are
#                      pangolin and copilot; the default is empty. A name that the script
#                      does not know stops it.
#   EC2_EDGE_NAMES     a list of public names with commas and no blank; the known names are
#                      ok, llm, ov and agent; the default is ok
#   EC2_DNS_PROVIDER   manual (the default), porkbun, route53 or cloudflare. The option
#                      pangolin needs porkbun.
#   The names of the provider. The head of scripts/dns/<provider>.sh gives them.
#
# A provider. One file scripts/dns/<provider>.sh holds one provider. This script loads the
# file of EC2_DNS_PROVIDER with `.`, so the file uses the functions and the names of this
# script. The file sets these names:
#   provider_names     the names of the environment file that the provider reads
#   provider_tool      the command that a real `apply` needs, or no text
#   provider_settings  a function: read and test the settings of the provider with `need`
#   provider_begin     a function: the work before the first record
#   provider_record    a function: read one record, and change it when it differs. Its
#                      arguments are the label of the record and the complete name. It
#                      prints the result with `record_result`.
# The file reads these names of this script: dry (1 in a dry run), domain, address, ttl,
# work (the private directory of a real run), value and value_missing (the result of
# `need`). It uses these functions: die, note, run, need and record_result.
#
# Secrets. The script uses no trace (`set -x`) and makes each file with the mode 0600. A
# credential of a provider stays on the development machine: it is in the environment
# file, and scripts/aws-host.sh does not write it into the secret of the host. A provider
# sends a credential in a file with the mode 0600 that the tool reads. No argument of a
# command, no address and no line of the output holds a credential. In a dry run, such a
# file is shown as a text between < and >. A message names a name, never a value.
#
# Needs: a POSIX shell with sed, python3, and the tool of the provider: curl 7.55 or later
# for porkbun and cloudflare, the AWS CLI version 2 for route53.
#
# Exit: 0 pass, 1 an error, 2 usage.

set -eu
# No trace, also when a caller starts the script with `sh -x`: a trace prints values.
set +x
umask 077

here=$(cd "$(dirname "$0")" && pwd)
top=$(dirname "$here")
ttl=600

# A line of a dry run goes to the first stdout, also from inside a command substitution.
exec 3>&1

die() {
    echo "dns-records: $*" >&2
    exit 1
}

usage() {
    sed -n '2,/^$/s/^# \{0,1\}//p' "$0"
}

# The reader of the environment file. It prints a value only for a name that the caller
# asks for, as an assignment for the shell. The functions parse, listed and options are
# the functions of scripts/aws-host.sh: scripts/check.sh compares them.
# shellcheck disable=SC2016 # the text is Python: a comment holds a name between two `
helper='
import re
import shlex
import sys

LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)")
# The known names of the three option settings. The order of a tuple is the order of each
# result: the DNS records and the checks of `verify` use it.
OPTIONS = ("pangolin", "copilot")
EDGE_NAMES = ("ok", "llm", "ov", "agent")
PROVIDERS = ("manual", "porkbun", "route53", "cloudflare")


def stop(text):
    sys.stderr.write("dns-records: " + text + "\n")
    sys.exit(1)


def parse(lines, label):
    """The NAME=value lines as a dict in the order of the file. No message holds a value."""
    found = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        match = LINE.fullmatch(line)
        if not match:
            stop("%s: line %d is not a NAME=value line" % (label, number))
        name, value = match.group(1), match.group(2).strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"\x27":
            value = value[1:-1]
        elif re.search(r"\s#", value):
            stop("%s: line %d has a blank and then # in a value with no quotes; "
                 "put the value in quotes" % (label, number))
        if name in found:
            stop("%s: line %d sets %s a second time" % (label, number, name))
        found[name] = value
    return found


def read_file(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()
    except (OSError, UnicodeError) as error:
        stop("cannot read %s: %s" % (path, getattr(error, "strerror", None) or "not UTF-8 text"))


def listed(found, name, known, default):
    """A list setting as a list in the order of `known`. No message holds a value."""
    text = found.get(name, "") or default
    items = text.split(",") if text else []
    if any(item not in known for item in items) or len(set(items)) != len(items):
        stop("%s is not a list of these names with commas and no blank, each name one time: %s"
             % (name, " ".join(known)))
    return [item for item in known if item in items]


def options(found):
    """The three option settings. Print them as assignments for the shell."""
    active = listed(found, "EC2_OPTIONS", OPTIONS, "")
    edge = listed(found, "EC2_EDGE_NAMES", EDGE_NAMES, "ok")
    provider = found.get("EC2_DNS_PROVIDER", "") or "manual"
    if provider not in PROVIDERS:
        stop("EC2_DNS_PROVIDER is not one of these names: " + " ".join(PROVIDERS))
    if "pangolin" in active and provider != "porkbun":
        stop("the option pangolin needs EC2_DNS_PROVIDER=porkbun: its certificate uses the "
             "DNS-01 challenge through Porkbun")
    print("options=%s" % shlex.quote(" ".join(active)))
    print("edge_names=%s" % shlex.quote(" ".join(edge)))
    print("dns_provider=%s" % shlex.quote(provider))
    return active


def load(path, names):
    found = parse(read_file(path), path)
    for name in names.split():
        if name in found:
            print("cfg_%s=%s" % (name, shlex.quote(found[name])))
    options(found)


load(sys.argv[1], sys.argv[2])
'

helper() {
    python3 -I -c "$helper" "$@"
}

# show <command> [<argument>...]: print one command of a dry run.
show() {
    shown='dry run:'
    for word in "$@"; do
        case $word in
            '' | *[!A-Za-z0-9_@%+=:,./-]*)
                word=\'$(printf '%s' "$word" | sed "s/'/'\\\\''/g")\'
                ;;
        esac
        shown="$shown $word"
    done
    printf '%s\n' "$shown" >&3
}

# note <text>: one line of a dry run that is not a command.
note() {
    printf 'dry run: %s\n' "$*" >&3
}

# run <command> [<argument>...]: start the command. In a dry run, print it and start nothing.
run() {
    if [ "$dry" = 1 ]; then
        show "$@"
    else
        "$@"
    fi
}

# need <NAME>: set `value` to the value of a name of the environment file. With no value,
# `value_missing` is 1: a real run stops later (stop_on_missing), and a dry run shows
# <NAME> in the place of the value.
need() {
    eval "value=\${cfg_$1:-}"
    value_missing=0
    if [ -z "$value" ]; then
        missing="$missing $1"
        value="<$1>"
        value_missing=1
    fi
}

stop_on_missing() {
    [ -n "$missing" ] || return 0
    if [ "$dry" = 1 ]; then
        note "a real run stops here: $env_file gives no value for$missing"
        missing=
    else
        die "$env_file gives no value for$missing"
    fi
}

# check_domain: stop when `domain` is not a DNS name in lower case of two labels or more.
# The name goes into a request address. The stand-in of a dry run passes in a dry run only.
check_domain() {
    [ "$domain_missing" = 0 ] || return 0
    case $domain in
        *[!a-z0-9.-]* | .* | *. | *..*) die "EC2_DOMAIN is not a DNS name in lower case" ;;
        *.*) ;;
        *) die "EC2_DOMAIN is not a DNS name of two labels or more" ;;
    esac
}

# check_address: stop when `address` is not an IPv4 address of four numbers from 0 to 255.
check_address() {
    case $address in
        *[!0-9.]* | .* | *. | *..*) die "--address needs one IPv4 address" ;;
        *.*.*.*.*) die "--address needs one IPv4 address" ;;
        *.*.*.*) ;;
        *) die "--address needs one IPv4 address" ;;
    esac
    rest=$address.
    while [ -n "$rest" ]; do
        number=${rest%%.*}
        rest=${rest#*.}
        [ ${#number} -le 3 ] && [ "$number" -le 255 ] || die "--address needs one IPv4 address"
    done
}

# record_line <complete name>: print one record in the form of a zone file.
record_line() {
    printf '%s. %s IN A %s\n' "$1" "$ttl" "$address"
}

# record_result <complete name> <result>: the one line of `apply` for a record.
record_result() {
    echo "dns: $1 A $address: $2"
}

# make_work: one private directory for the files of this run. The exit removes it.
make_work() {
    work=$(mktemp -d "${TMPDIR:-/tmp}/dns-records.XXXXXX")
    trap 'rm -rf "$work"' EXIT
    # In sh, a signal handler does not end the script; exit runs the EXIT trap. PIPE and
    # QUIT are in the list: without them, the files of the run stay.
    trap 'exit 130' INT TERM HUP PIPE QUIT
}

dry=0
env_file=
address=
command=
while [ $# -gt 0 ]; do
    case $1 in
        -h | --help) usage; exit 0 ;;
        --dry-run) dry=1 ;;
        --env-file)
            [ $# -ge 2 ] || die "--env-file needs a path"
            env_file=$2
            shift
            ;;
        --env-file=*) env_file=${1#--env-file=} ;;
        --address)
            [ $# -ge 2 ] || die "--address needs one IPv4 address"
            address=$2
            shift
            ;;
        --address=*) address=${1#--address=} ;;
        -*) usage >&2; exit 2 ;;
        *)
            [ -z "$command" ] || { usage >&2; exit 2; }
            command=$1
            ;;
    esac
    shift
done

case $command in
    plan | apply) ;;
    *) usage >&2; exit 2 ;;
esac
command -v python3 >/dev/null || die "python3 not found"

[ -n "$env_file" ] || env_file=$top/.env
[ -r "$env_file" ] ||
    die "the environment file $env_file is not there; a dry run can use: --env-file .env.example"
# The absolute path: a relative path that starts with - is an option for a command.
env_dir=$(CDPATH='' cd -- "$(dirname -- "$env_file")" && pwd) ||
    die "cannot find the directory of the environment file $env_file"
env_file=${env_dir%/}/$(basename -- "$env_file")

missing=
value=
value_missing=0
work=
# The helper sets the three option settings from the environment file.
options=
edge_names=
dns_provider=
assign=$(helper "$env_file" EC2_DOMAIN) || exit 1
# The helper quotes each value, so the assignments are safe for eval.
eval "$assign"

need EC2_DOMAIN
domain=$value
domain_missing=$value_missing
check_domain

# The record set: the name pangolin with the option pangolin, then each name of
# EC2_EDGE_NAMES. The helper gives the names in a fixed order.
names=
case " $options " in
    *' pangolin '*) names=pangolin ;;
esac
names="$names $edge_names"
count=0
for name in $names; do
    count=$((count + 1))
done

if [ -n "$address" ]; then
    check_address
elif [ "$command" = plan ] || [ "$dry" = 1 ]; then
    address='<the output ElasticIp of the stack>'
else
    die "apply needs --address <address>: the output ElasticIp of the stack. scripts/aws-host.sh dns gives it."
fi

if [ "$command" = plan ]; then
    stop_on_missing
    echo "dns: the provider is $dns_provider. The record set has $count A records:"
    for name in $names; do
        record_line "$name.$domain"
    done
    echo "dns: plan only. No call is made and nothing is changed."
    exit 0
fi

provider_file=$here/dns/$dns_provider.sh
[ -r "$provider_file" ] || die "the provider file $provider_file is not there"
provider_names=
provider_tool=
# shellcheck disable=SC2034 # the provider file reads the name: it does not run alone
dns_records_loaded=1
# shellcheck source=/dev/null # the name of the file comes from EC2_DNS_PROVIDER
. "$provider_file"
if [ -n "$provider_names" ]; then
    assign=$(helper "$env_file" "$provider_names") || exit 1
    eval "$assign"
fi
provider_settings
stop_on_missing
if [ "$dry" != 1 ] && [ -n "$provider_tool" ]; then
    command -v "$provider_tool" >/dev/null || die "$provider_tool not found"
    make_work
fi
[ "$dns_provider" = manual ] ||
    [ "$dry" = 1 ] || echo "==> the provider $dns_provider sets $count A records to $address"
provider_begin
for name in $names; do
    [ "$dry" != 1 ] || [ "$dns_provider" = manual ] ||
        note "the record $name.$domain: read it, then create it when it is not there, or edit it when its address is not $address"
    provider_record "$name" "$name.$domain"
done
