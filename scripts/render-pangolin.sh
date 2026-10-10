#!/bin/sh
# Render the Pangolin configuration of the host from the environment file. Only
# the option `pangolin` needs it (compose.pangolin.yaml).
# See docs/reference/compose-stack.md and docs/options/pangolin.md.
#
# Usage: scripts/render-pangolin.sh [--env-file <file>] [--out <directory>] [--dry-run]
#
# Pangolin and Traefik read their configuration from files, and they do not
# expand an environment name in these files. So the files of pangolin/ in this
# repository hold references such as ${EC2_DOMAIN}, and this script writes the
# files with the values:
#   pangolin/config/config.yml                   -> <out>/config/config.yml
#   pangolin/config/traefik/traefik_config.yml   -> <out>/traefik/config/traefik_config.yml
#   pangolin/config/traefik/dynamic_config.yml   -> <out>/traefik/config/dynamic_config.yml
#   pangolin/blueprint.yaml                      -> <out>/blueprint.yaml
# <out> is the edge directory of the data volume. It holds one directory for
# each edge service, and compose.pangolin.yaml binds each one into its service
# only:
#   <out>/config                the configuration and the database of Pangolin
#   <out>/gerbil                the key file of Gerbil
#   <out>/traefik/config        the two files of Traefik, read-only in Traefik
#   <out>/traefik/letsencrypt   the certificate store of Traefik
# The script makes each of them. No container mounts <out> itself, so no
# container reads blueprint.yaml: the bootstrap applies it.
#
# The script reads six names from the environment file and no name from the
# environment of the shell:
#   EC2_DOMAIN                the domain subtree; necessary
#   EC2_EDGE_NAMES            the public names: a list with commas and no blank.
#                             The known names are ok, agent, llm and ov;
#                             necessary. It says which resources of the
#                             blueprint are on.
#   EC2_ACME_EMAIL            the address of the certificate account; necessary
#   EC2_ADMIN_CIDR            the one IPv4 range that reaches the dashboard of
#                             Pangolin, as <address>/<prefix length> with a
#                             prefix length from 16 to 32; necessary
#   EC2_GERBIL_BASE_ENDPOINT  the public address of the tunnels; with no value
#                             the script uses pangolin.<EC2_DOMAIN>
#   EC2_PANGOLIN_SITE         the identifier of the local site of the host;
#                             necessary
# It reads no secret. The server secret and the two Porkbun values go to the
# containers through compose.pangolin.yaml, not through a file.
#
# The resources. The blueprint has one resource for each known public name. The
# script makes four values from EC2_EDGE_NAMES, one for each resource: `true`
# for a name of the list and `false` for each other name. The blueprint reads
# them as ${EDGE_OK}, ${EDGE_AGENT}, ${EDGE_LLM} and ${EDGE_OV} in the line
# `enabled` of each resource. They are not names of the environment file. So a
# name outside the list is a resource that is off: it has no route. The script
# stops on a name of the list that it does not know.
#
# The bootstrap runs this script before it starts the Compose stack. A second
# run with the same environment file gives the same files. Start the services
# pangolin, gerbil and traefik again after a change of a rendered file.
#
# Exit: 0 the files are written (or named, with --dry-run), 1 a value is
# missing or not valid, 2 usage or tool error.
#
# Tools: envsubst (the package gettext), awk, sed.

set -eu

# The names that a file of pangolin/ can use. envsubst replaces only these. The
# last four are the values that this script makes from EC2_EDGE_NAMES.
# shellcheck disable=SC2016 # envsubst reads the names as text
NAMES='${EC2_DOMAIN} ${EC2_ACME_EMAIL} ${EC2_ADMIN_CIDR} ${EC2_GERBIL_BASE_ENDPOINT} ${EC2_PANGOLIN_SITE} ${EDGE_OK} ${EDGE_AGENT} ${EDGE_LLM} ${EDGE_OV}'
# The public names that EC2_EDGE_NAMES can hold: one resource of the blueprint each.
KNOWN="ok agent llm ov"

usage() {
    cat <<'EOT'
usage: scripts/render-pangolin.sh [--env-file <file>] [--out <directory>] [--dry-run]

Render the files of pangolin/ with the values of the environment file:
  config/config.yml, traefik/config/traefik_config.yml,
  traefik/config/dynamic_config.yml and blueprint.yaml in the output directory.
Make the directory of each edge service there: config, gerbil and traefik.

  --env-file <file>   the environment file (default: .env of this tree)
  --out <directory>   the output directory (default: /srv/data/pangolin)
  --dry-run           name each file; write nothing

Names: EC2_DOMAIN, EC2_EDGE_NAMES, EC2_ACME_EMAIL, EC2_ADMIN_CIDR,
EC2_GERBIL_BASE_ENDPOINT, EC2_PANGOLIN_SITE.
Exit: 0 pass, 1 a value is missing or not valid, 2 usage or tool error.
EOT
}

die() {
    echo "render-pangolin: error: $1" >&2
    exit "${2:-2}"
}

top=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
env_file=$top/.env
out=/srv/data/pangolin
dry_run=0

while [ $# -gt 0 ]; do
    case $1 in
        -h|--help) usage; exit 0 ;;
        --env-file) [ $# -ge 2 ] || die "--env-file needs a file"; env_file=$2; shift 2 ;;
        --out) [ $# -ge 2 ] || die "--out needs a directory"; out=$2; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        *) usage >&2; exit 2 ;;
    esac
done

command -v envsubst >/dev/null 2>&1 || die "the command envsubst is missing; install the package gettext"
[ -f "$env_file" ] || die "the environment file does not exist: $env_file"
[ -n "$out" ] || die "--out needs a directory"

# env_value <name>: the value of the last line `<name>=<value>` of the
# environment file, without one pair of quotes around it.
env_value() {
    awk -v name="$1" '
        index($0, name "=") == 1 { value = substr($0, length(name) + 2); found = 1 }
        END { if (found) print value }
    ' "$env_file" | sed -e 's/\r$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\\(.*\\)'\$/\\1/"
}

# valid <name> <value> <pattern>: a value goes into YAML text between quotes,
# so it must hold only the characters of its kind. The message gives no value.
valid() {
    [ -n "$2" ] || die "$1 has no value in the environment file" 1
    if printf '%s\n' "$2" | grep -Eqx -- "$3"; then
        return 0
    fi
    die "$1 is not valid: the value does not have the form of its kind" 1
}

EC2_DOMAIN=$(env_value EC2_DOMAIN)
EC2_ACME_EMAIL=$(env_value EC2_ACME_EMAIL)
EC2_ADMIN_CIDR=$(env_value EC2_ADMIN_CIDR)
EC2_GERBIL_BASE_ENDPOINT=$(env_value EC2_GERBIL_BASE_ENDPOINT)
EC2_PANGOLIN_SITE=$(env_value EC2_PANGOLIN_SITE)
edge_names=$(env_value EC2_EDGE_NAMES)

valid EC2_DOMAIN "$EC2_DOMAIN" '[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?'
valid EC2_ACME_EMAIL "$EC2_ACME_EMAIL" '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+'
# One IPv4 range. The prefix length is from 16 to 32, so the open range and each
# wide range are refused. An empty value stops the script: without the range,
# the dashboard of Pangolin would answer each address.
octet='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
valid EC2_ADMIN_CIDR "$EC2_ADMIN_CIDR" "$octet\\.$octet\\.$octet\\.$octet/(1[6-9]|2[0-9]|3[0-2])"
# With no value: the dashboard name. It has an A record to the address of the host.
[ -n "$EC2_GERBIL_BASE_ENDPOINT" ] || EC2_GERBIL_BASE_ENDPOINT=pangolin.$EC2_DOMAIN
valid EC2_GERBIL_BASE_ENDPOINT "$EC2_GERBIL_BASE_ENDPOINT" '[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?'
valid EC2_PANGOLIN_SITE "$EC2_PANGOLIN_SITE" '[A-Za-z0-9]([A-Za-z0-9_-]*[A-Za-z0-9])?'
valid EC2_EDGE_NAMES "$edge_names" '[a-z0-9]+(,[a-z0-9]+)*'
# One value for each resource of the blueprint: `true` for a name of the list.
EDGE_OK=false
EDGE_AGENT=false
EDGE_LLM=false
EDGE_OV=false
seen=
old_ifs=$IFS
IFS=,
for name in $edge_names; do
    case " $KNOWN " in
        *" $name "*) ;;
        *) die "EC2_EDGE_NAMES holds a name that this script does not know; the known names are: $KNOWN" 1 ;;
    esac
    case " $seen " in
        *" $name "*) die "EC2_EDGE_NAMES holds a name two times" 1 ;;
    esac
    seen="$seen $name"
    case $name in
        ok) EDGE_OK=true ;;
        agent) EDGE_AGENT=true ;;
        llm) EDGE_LLM=true ;;
        ov) EDGE_OV=true ;;
    esac
done
IFS=$old_ifs
export EC2_DOMAIN EC2_ACME_EMAIL EC2_ADMIN_CIDR EC2_GERBIL_BASE_ENDPOINT EC2_PANGOLIN_SITE
export EDGE_OK EDGE_AGENT EDGE_LLM EDGE_OV

# render <source below pangolin/> <target below the output directory> <mode>
render() {
    src=$top/pangolin/$1
    dst=$out/$2
    [ -f "$src" ] || die "the source file does not exist: pangolin/$1"
    if [ "$dry_run" = 1 ]; then
        echo "render-pangolin: would write $dst (mode $3) from pangolin/$1"
        return 0
    fi
    mkdir -p "$(dirname -- "$dst")"
    tmp=$dst.tmp.$$
    # The file never has a mode wider than its final mode.
    (umask 077 && envsubst "$NAMES" <"$src" >"$tmp") || { rm -f "$tmp"; die "envsubst failed on pangolin/$1"; }
    # A reference that stays has a name that the list NAMES of this script lacks.
    # shellcheck disable=SC2016 # the pattern is the text of a reference
    if grep -q '\${[A-Za-z_][A-Za-z0-9_]*}' "$tmp"; then
        rm -f "$tmp"
        die "pangolin/$1 uses an environment name that this script does not render" 1
    fi
    chmod "$3" "$tmp"
    mv -f "$tmp" "$dst"
    echo "render-pangolin: wrote $dst"
}

# private_dir <directory below the output directory>: make it with mode 0700.
# A directory that exists keeps its mode and its owner.
private_dir() {
    if [ "$dry_run" = 1 ]; then
        echo "render-pangolin: would make $out/$1 (mode 0700) when it does not exist"
        return 0
    fi
    [ -d "$out/$1" ] || (umask 077 && mkdir -p "$out/$1")
}

# One directory for each edge service. Each container runs as root with no
# capability, so the owner of each directory must be the user of this script:
# root on the host.
# Pangolin: its configuration file, and its database, which it makes itself.
private_dir config
# Gerbil: it makes its key file here.
private_dir gerbil
# Traefik: its two files, and the certificate store, in which it makes acme.json.
private_dir traefik
private_dir traefik/config
private_dir traefik/letsencrypt

render config/config.yml config/config.yml 0640
render config/traefik/traefik_config.yml traefik/config/traefik_config.yml 0644
render config/traefik/dynamic_config.yml traefik/config/dynamic_config.yml 0644
render blueprint.yaml blueprint.yaml 0644
