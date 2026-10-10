#!/bin/sh
# The local run of the gate of OpenKnowledge: prove the gate with no Entra ID tenant and
# with no host. See docs/entra.md, section "Prove the gate before the deploy".
#
# Usage: ok-auth/local-run.sh [--upstream auto|real|stub] [--keep] [--help]
#   --upstream real   the upstream is the image of the OpenKnowledge kit, by the name of
#                     compose.yaml. The image must be on this machine.
#   --upstream stub   the upstream is a stub that returns the headers of each request
#   --upstream auto   the default: `real` when the image is on this machine, else `stub`
#   --keep            do not remove the project and the temporary directory at the end.
#                     The script prints the two commands that remove them.
#
# What it does. It starts the services `open-knowledge` and `ok-auth` of compose.yaml in a
# Compose project of its own, with the overlay of ok-auth/local-run/. A stub is in the
# place of Entra ID, and a probe is in the place of the edge, of the agent and of two
# other clients. Each check prints one line: ok, skip or FAIL. Then the script removes
# the project.
# - The run loads compose.yaml and the core edge file compose.caddy.yaml, as a host with
#   no option does. So `ok-auth` gets the edge network `edge-ok` from the edge file, with
#   the range of that file. The run does not start `caddy`: the probe is in its place.
# - The range that the gate trusts is `trusted_proxy_ips` of ok-auth/oauth2-proxy.cfg. The
#   run compares it with the range of `edge-ok` in each of the two edge files.
# - Each value is new for one run: the tenant id, the group id, the client id and the two
#   secrets. They are in a temporary directory with the mode 0700, and the script removes
#   it. The script prints no secret.
# - No network of the run has a route to the internet. The script makes no call to Entra
#   ID, to Azure or to AWS, and it reads no credential.
# - The environment of the shell does not reach Compose.
#
# What it does not prove: the app registration, a login of a real user, the group claim
# of Entra ID, a read of Microsoft Graph, the edge and its certificate. docs/entra.md has
# the list and the step that proves the login after the first deploy.
#
# Warning: do not run it on the host of the stack. The edge network of the name `ok` has
# a fixed range, and the stack of the host uses it. Docker then refuses the network of
# the run.
#
# Exit: 0 each check passes, 1 a check fails, 2 usage or tool error.
# Needs: a POSIX shell with awk, grep and sed; python3; Docker with the Compose plugin
# 2.24 or later. The run pulls two images when they are not there: the gate image of
# kits.lock and python:3-slim.

set -eu

usage() {
    sed -n '2,/^$/s/^# \{0,1\}//p' "$0"
}

die() {
    echo "local-run: error: $*" >&2
    exit 2
}

upstream=auto
keep=0
while [ $# -gt 0 ]; do
    case $1 in
        --upstream)
            [ $# -ge 2 ] || die "--upstream needs a value"
            upstream=$2
            shift ;;
        --upstream=*) upstream=${1#--upstream=} ;;
        --keep) keep=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done
case $upstream in
    auto|real|stub) ;;
    *) die "--upstream must be auto, real or stub" ;;
esac

top=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$top"
for file in compose.yaml compose.caddy.yaml compose.pangolin.yaml .env.example ok-auth/oauth2-proxy.cfg \
    ok-auth/local-run/overlay.yaml; do
    [ -f "$file" ] || die "$file is not in the tree"
done
command -v python3 >/dev/null 2>&1 || die "python3 is not on this machine"
docker compose version >/dev/null 2>&1 || die "Docker with the Compose plugin is not on this machine"
docker info >/dev/null 2>&1 || die "the Docker daemon does not answer"

# The image of the service open-knowledge, as compose.yaml names it.
ok_image=$(awk '
    /^[^ #]/ { top = $1; service = "" }
    top != "services:" { next }
    /^  [^ #]/ { service = $1 }
    service == "open-knowledge:" && $1 == "image:" { print $2; exit }
' compose.yaml)
[ -n "$ok_image" ] || die "compose.yaml names no image for the service open-knowledge"
if [ "$upstream" = auto ]; then
    if docker image inspect "$ok_image" >/dev/null 2>&1; then
        upstream=real
    else
        upstream=stub
        echo "note: the image $ok_image is not on this machine, so the upstream is the stub"
    fi
elif [ "$upstream" = real ]; then
    docker image inspect "$ok_image" >/dev/null 2>&1 ||
        die "the image $ok_image is not on this machine; build the kit, or use --upstream stub"
fi

# The one range that the gate trusts for a forwarded header.
edge_range=$(sed -n 's/^trusted_proxy_ips *= *\[ *"\([^"]*\)" *\] *$/\1/p' ok-auth/oauth2-proxy.cfg)
case $edge_range in
    '' | *[!0-9./]*) die "ok-auth/oauth2-proxy.cfg does not give one range in trusted_proxy_ips" ;;
esac

work=$(mktemp -d "${TMPDIR:-/tmp}/ok-auth-local-run.XXXXXX")
chmod 700 "$work"
project=ok-auth-local-run-$$

# docker_compose <argument>...: Compose with the environment file of the run. The
# environment of the shell does not reach it.
docker_compose() {
    set -- docker compose --project-directory "$top" --env-file "$work/env" "$@"
    [ -z "${DOCKER_HOST:-}" ] || set -- DOCKER_HOST="$DOCKER_HOST" "$@"
    [ -z "${DOCKER_CONFIG:-}" ] || set -- DOCKER_CONFIG="$DOCKER_CONFIG" "$@"
    env -i PATH="$PATH" HOME="${HOME:-/}" "$@"
}

# compose <argument>...: Compose on the three or four files, in the project of the run:
# the core, the core edge and the overlay.
compose() {
    [ "$upstream" = real ] || set -- -f ok-auth/local-run/overlay-stub-upstream.yaml "$@"
    docker_compose -p "$project" -f compose.yaml -f compose.caddy.yaml -f ok-auth/local-run/overlay.yaml "$@"
}

# edge_ok <edge file>: print the range and the members of the network `edge-ok` in the
# model of compose.yaml with one edge file, in one line. No container starts.
edge_ok() {
    docker_compose -f compose.yaml -f "$1" config --format json 2>/dev/null | python3 -I -c '
import json, sys
model = json.load(sys.stdin)
subnet = model["networks"]["edge-ok"]["ipam"]["config"][0]["subnet"]
members = sorted(name for name, service in model["services"].items() if "edge-ok" in (service.get("networks") or {}))
print(subnet, *members)'
}

cleanup() {
    status=$?
    trap - EXIT
    if [ "$keep" = 1 ] && [ -f "$work/env" ]; then
        echo "note: the project and the directory stay. The directory holds the two secrets of the run. To remove them:"
        echo "  docker compose -p $project down --volumes --remove-orphans"
        echo "  rm -r $work"
        exit "$status"
    fi
    if [ -f "$work/env" ]; then
        compose down --volumes --remove-orphans --timeout 5 >"$work/down.out" 2>&1 ||
            echo "local-run: warning: Compose did not remove the project $project" >&2
    fi
    rm -rf "$work"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

n_ok=0
n_skip=0
n_fail=0

# check <name> <command>...: one check of this script. The command prints nothing.
check() {
    name=$1
    shift
    if "$@" >/dev/null 2>&1; then
        echo "ok   $name"
    else
        echo "FAIL $name"
    fi
}

# lacks <text> <file>: the file does not hold the text.
lacks() {
    ! grep -qF -e "$1" "$2"
}

# lacks_each <list> <file>: the file holds no line of the list.
lacks_each() {
    ! grep -qF -f "$1" "$2"
}

# has_each <file> <text>...: the file holds each text.
has_each() {
    file=$1
    shift
    for text in "$@"; do
        grep -qF -e "$text" "$file" || return 1
    done
}

# count: add the lines of one output file to the three counts.
count() {
    n_ok=$((n_ok + $(grep -c '^ok   ' "$1" || :)))
    n_skip=$((n_skip + $(grep -c '^skip ' "$1" || :)))
    n_fail=$((n_fail + $(grep -c '^FAIL ' "$1" || :)))
}

# ----- The environment file of the run -----
# Each name of .env.example. An empty name gets a word, so that compose.yaml renders. The
# names of the gate get new values. Python writes them into the file: no value is an
# argument of a command.
(umask 077 && awk '/^[A-Za-z_][A-Za-z0-9_]*=/ {
    i = index($0, "=")
    name = substr($0, 1, i - 1)
    value = substr($0, i + 1)
    if (name ~ /^(EC2_DOMAIN|EC2_OK_|COMPOSE_PROJECT_NAME$)/) next
    if (value ~ /^[ \t]*$/) value = "local-run-stub"
    print name "=" value
}' .env.example >"$work/env") || die "awk cannot read .env.example"
LOCAL_RUN_UPSTREAM=$upstream LOCAL_RUN_EDGE_RANGE=$edge_range python3 -I - "$work/env" <<'EOT' || die "python3 cannot write the environment file"
import os, secrets, sys, uuid
lines = [
    "EC2_DOMAIN=ec2.local-run.test",
    f"EC2_OK_ENTRA_TENANT_ID={uuid.uuid4()}",
    f"EC2_OK_ENTRA_GROUP_ID={uuid.uuid4()}",
    f"EC2_OK_OIDC_CLIENT_ID={uuid.uuid4()}",
    f"EC2_OK_OIDC_CLIENT_SECRET={secrets.token_urlsafe(30)}",
    f"EC2_OK_COOKIE_SECRET={secrets.token_urlsafe(32)}",
    f"LOCAL_RUN_OTHER_TENANT={uuid.uuid4()}",
    f"LOCAL_RUN_OTHER_GROUP={uuid.uuid4()}",
    f"LOCAL_RUN_UPSTREAM={os.environ['LOCAL_RUN_UPSTREAM']}",
    f"LOCAL_RUN_EDGE_RANGE={os.environ['LOCAL_RUN_EDGE_RANGE']}",
]
with open(sys.argv[1], "a") as handle:
    handle.write("\n".join(lines) + "\n")
EOT
sed -n 's/^EC2_OK_OIDC_CLIENT_SECRET=//p; s/^EC2_OK_COOKIE_SECRET=//p' "$work/env" >"$work/secrets"

echo "local run of the gate: the upstream is $upstream, the Compose project is $project"

# ----- Start -----
if ! compose config -q >"$work/config.out" 2>&1; then
    cat "$work/config.out" >&2
    die "Compose does not render compose.yaml and compose.caddy.yaml with the overlay"
fi
echo "ok   docker compose config renders compose.yaml and compose.caddy.yaml with the overlay" >"$work/script.out"
if ! compose up -d --wait --wait-timeout 180 stub-issuer open-knowledge ok-auth >"$work/up.out" 2>&1; then
    tail -n 20 "$work/up.out" >&2
    # The log of the gate holds no secret: a later check of this script proves it.
    compose logs --no-color --tail 20 ok-auth >&2 2>/dev/null || :
    die "the services of the run do not start"
fi

gate=$(compose ps -q ok-auth)
[ -n "$gate" ] || die "the run has no container of ok-auth"
inspect() {
    docker inspect "$gate" --format "$1"
}

# ----- The container of the gate -----
inspect '{{json .Config.Entrypoint}} {{json .Config.Cmd}}' >"$work/command"
{
    check "the container of ok-auth is healthy: its health check calls /ready" \
        test "$(inspect '{{.State.Health.Status}}')" = healthy
    check "ok-auth runs as the user 65532, not as root" test "$(docker exec "$gate" id -u)" = 65532
    check "ok-auth has no capability (cap_drop ALL)" test "$(inspect '{{json .HostConfig.CapDrop}}')" = '["ALL"]'
    check "ok-auth has no-new-privileges" \
        test "$(inspect '{{json .HostConfig.SecurityOpt}}')" = '["no-new-privileges:true"]'
    check "ok-auth has a read-only root file system" test "$(inspect '{{.HostConfig.ReadonlyRootfs}}')" = true
    check "ok-auth publishes no port" test -z "$(docker port "$gate")"
    check "the command of ok-auth holds no secret" lacks_each "$work/secrets" "$work/command"
} >>"$work/script.out"

# ----- The networks of OpenKnowledge -----
members() {
    docker network inspect "${project}_$1" --format '{{range .Containers}}{{.Name}} {{end}}' |
        tr ' ' '\n' | sed -n "s/^$project-\\(.*\\)-[0-9]*\$/\\1/p" | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//'
}
# networks_of <container>: print the names of the networks of a container, in one line.
networks_of() {
    docker inspect "$1" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}'
}
ok_container=$(compose ps -q open-knowledge)
{
    check "the edge network edge-ok of compose.caddy.yaml is internal" \
        test "$(docker network inspect "${project}_edge-ok" --format '{{.Internal}}')" = true
    check "the range of edge-ok in the run is the range that the gate trusts (trusted_proxy_ips)" \
        test "$(docker network inspect "${project}_edge-ok" --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}')" = "$edge_range"
    check "the one member of edge-ok in this run is the gate (the edge is not started)" test "$(members edge-ok)" = "ok-auth"
    check "compose.caddy.yaml: edge-ok has that range, and its members are caddy and the gate only" \
        test "$(edge_ok compose.caddy.yaml)" = "$edge_range caddy ok-auth"
    check "compose.pangolin.yaml: edge-ok has that range, and its members are gerbil and the gate only" \
        test "$(edge_ok compose.pangolin.yaml)" = "$edge_range gerbil ok-auth"
    check "the gate is on three networks only: its edge network, gate-egress and knowledge" \
        test "$(networks_of "$gate")" = "${project}_edge-ok ${project}_gate-egress ${project}_knowledge "
    check "the network knowledge is internal" test "$(docker network inspect "${project}_knowledge" --format '{{.Internal}}')" = true
    check "the network knowledge-agent is internal" \
        test "$(docker network inspect "${project}_knowledge-agent" --format '{{.Internal}}')" = true
    check "the members of knowledge are the gate and OpenKnowledge only" test "$(members knowledge)" = "ok-auth open-knowledge"
    check "the one member of knowledge-agent in this run is OpenKnowledge (the agent is not started)" \
        test "$(members knowledge-agent)" = "open-knowledge"
    check "OpenKnowledge is on two networks only" \
        test "$(networks_of "$ok_container")" = "${project}_knowledge ${project}_knowledge-agent "
    check "OpenKnowledge publishes no port" test -z "$(docker port "$ok_container")"
} >>"$work/script.out"

# No route out of the container: a connect to an address of a documentation range
# (RFC 5737) stops at once with "network is unreachable", not with a timeout.
if [ "$upstream" = real ]; then
    code=$(compose exec -T open-knowledge node -e '
        const s = require("net").connect({ host: "198.51.100.1", port: 80 });
        s.setTimeout(4000, () => { console.log("TIMEOUT"); process.exit(0); });
        s.on("error", (e) => { console.log(e.code); process.exit(0); });
        s.on("connect", () => { console.log("CONNECTED"); process.exit(0); });' 2>/dev/null || :)
else
    code=$(compose exec -T open-knowledge python -I -c '
import errno, socket
try:
    socket.create_connection(("198.51.100.1", 80), timeout=4)
    print("CONNECTED")
except OSError as error:
    print(errno.errorcode.get(error.errno, "TIMEOUT"))' 2>/dev/null || :)
fi
check "the container of OpenKnowledge has no route to the internet (ENETUNREACH)" test "$code" = ENETUNREACH >>"$work/script.out"
cat "$work/script.out"
count "$work/script.out"

# ----- The probes -----
for role in edge neighbour agent outsider; do
    echo "--- probe: $role"
    code=0
    compose run --rm -T "probe-$role" >"$work/probe.out" 2>"$work/probe.err" || code=$?
    cat "$work/probe.out"
    count "$work/probe.out"
    if [ "$code" -ne 0 ] && ! grep -q '^FAIL ' "$work/probe.out"; then
        tail -n 5 "$work/probe.err" >&2
        echo "FAIL the probe $role stops with the status $code"
        n_fail=$((n_fail + 1))
    fi
done

# ----- The log of the gate -----
echo "--- the log of the gate"
compose logs --no-color ok-auth >"$work/gate.log" 2>&1 || :
{
    check "the log has the address of the client behind the edge (X-Forwarded-For from the edge)" \
        grep -qF '203.0.113.7' "$work/gate.log"
    check "the log does not have the forged address of the other client" lacks 203.0.113.9 "$work/gate.log"
    check "the log names the user who passes and a user who does not" \
        has_each "$work/gate.log" member@local-run.test stranger@local-run.test
    check "the log holds no secret of the gate" lacks_each "$work/secrets" "$work/gate.log"
} >"$work/script.out"
cat "$work/script.out"
count "$work/script.out"

if [ "$n_fail" -gt 0 ]; then
    echo "local run: FAIL ($n_fail FAIL, $n_ok ok, $n_skip skip)"
    exit 1
fi
echo "local run: pass ($n_ok ok, $n_skip skip)"
