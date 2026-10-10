#!/bin/sh
# The app registration of the gate of OpenKnowledge in Microsoft Entra ID: make it or
# update it with the Azure CLI, with its security group and its two secrets.
# The gate is oauth2-proxy in the Compose service `ok-auth`. See docs/entra.md.
#
# Usage: scripts/entra-app.sh [--dry-run] [--env-file <path>] <command> [<option>]
#        scripts/entra-app.sh selftest
#   apply [--name <text>] [--group <text>] [--assign-group]
#       Make the app registration when no one has the name; else update it. Each run sets
#       the same state:
#       - one tenant only (the sign-in audience AzureADMyOrg);
#       - one redirect URI: https://ok.<EC2_DOMAIN>/oauth2/callback;
#       - the group claim: the ID token holds the ids of the security groups of the user;
#       - the delegated permission User.Read of Microsoft Graph, for a user with more than
#         200 groups;
#       - no token from the implicit flow.
#       Then make the enterprise application (the service principal) and the security
#       group, each when it is not there. Then write three names into the environment
#       file: EC2_OK_ENTRA_TENANT_ID, EC2_OK_OIDC_CLIENT_ID and EC2_OK_ENTRA_GROUP_ID.
#       The command adds no member to the group, and it makes no secret.
#       --name <text>    the name of the app registration; the default is
#                        "<EC2_PROJECT> OpenKnowledge gate"
#       --group <text>   the name of the security group; the default is
#                        "<EC2_PROJECT>-openknowledge"
#       --assign-group   a second lock in Entra ID: assign the group to the enterprise
#                        application, then set "assignment required". Entra ID then gives
#                        no token to a user outside the group. A tenant with no Entra ID
#                        P1 or P2 licence cannot assign a group: the command then stops
#                        before it sets "assignment required".
#   secrets [--days <number>] [--new-cookie-secret]
#       Make a new client secret of the app registration EC2_OK_OIDC_CLIENT_ID and write
#       it into the environment file as EC2_OK_OIDC_CLIENT_SECRET. An older client secret
#       stays valid: the command prints how to remove it. Make the cookie secret
#       EC2_OK_COOKIE_SECRET when the name has no value.
#       --days <number>        the life of the client secret, from 1 to 730; the default
#                              is 180
#       --new-cookie-secret    replace the cookie secret; each session of each user ends
#   selftest
#       Test the rules of this script on a copy of it in a temporary directory, with a
#       stub of `az` first on PATH. It makes no call to Azure, and it reads and writes no
#       environment file of this repository. The last line is "selftest: pass" or
#       "selftest: FAIL". It takes no option.
#   --dry-run          print each Azure CLI command and make no call; write nothing.
#                      With no command, a dry run prints each Azure CLI command of this
#                      script: the commands of `apply --assign-group`, then of `secrets`.
#   --env-file <path>  the environment file; the default is .env of this repository. A
#                      dry run reads .env.example when .env is not there. A real run
#                      refuses the sample file .env.example as its environment file.
#   --help             print this text
#
# Before a real run: sign in with `az login --tenant <tenant id>`, with an identity that
# can make an app registration and a group. The script uses the tenant of that session. It
# stops when EC2_OK_ENTRA_TENANT_ID of the environment file names another tenant.
#
# The environment file is the file of the development machine, in the form of
# .env.example. The script reads EC2_DOMAIN, EC2_PROJECT and the five names
# with the prefix EC2_OK_. It writes only these five names, and the file keeps its mode.
# The sample file .env.example is a tracked file, so a real run never writes into it. A
# real run stops before its first call when the environment file is that file: a file
# with the name .env.example, a link to such a file, or the same file under another name.
#
# Secrets. The script prints no secret and no id: a message names a name of the
# environment file, never a value. The client secret goes from the Azure CLI into the
# environment file through a pipe. It is in no argument of a command and in no variable
# of this script. The script uses no trace (`set -x`). A dry run shows each id as a text
# between < and >.
#
# Not verified: no run of this script made a call to Azure. Its commands are checked
# against the Azure CLI 2.91.0 with no network, and its steps against a stub of `az`
# (docs/entra.md, "What is not proved").
#
# Exit: 0 pass, 1 an error, 2 usage.
# Needs: a POSIX shell with sed, python3; for a real run, the Azure CLI (`az`).

set -eu
# No trace: a trace of the command `secrets` is not necessary, and a habit is a risk.
set +x

# Microsoft Graph and its delegated permission User.Read. The two ids are the same in
# each tenant (the example of `az ad app update --help`).
GRAPH_APP=00000003-0000-0000-c000-000000000000
USER_READ=e1fe6dd8-ba31-4d61-89e7-88639da4683d
GRAPH_URL=https://graph.microsoft.com/v1.0
# The app role of an application with no role of its own: "Default Access".
DEFAULT_ROLE=00000000-0000-0000-0000-000000000000

usage() {
    sed -n '2,/^$/s/^# \{0,1\}//p' "$0"
}

die() {
    echo "entra-app: error: $*" >&2
    exit 1
}

say() {
    echo "entra-app: $*"
}

dry=0
env_file=
command=
name=
group=
assign=0
days=180
new_cookie=0
while [ $# -gt 0 ]; do
    case $1 in
        -h | --help) usage; exit 0 ;;
        --dry-run) dry=1 ;;
        --env-file | --name | --group | --days)
            [ $# -ge 2 ] || { echo "entra-app: error: $1 needs a value" >&2; exit 2; }
            case $1 in
                --env-file) env_file=$2 ;;
                --name) name=$2 ;;
                --group) group=$2 ;;
                --days) days=$2 ;;
            esac
            shift
            ;;
        --env-file=*) env_file=${1#--env-file=} ;;
        --name=*) name=${1#--name=} ;;
        --group=*) group=${1#--group=} ;;
        --days=*) days=${1#--days=} ;;
        --assign-group) assign=1 ;;
        --new-cookie-secret) new_cookie=1 ;;
        -*) usage >&2; exit 2 ;;
        *)
            [ -z "$command" ] || { usage >&2; exit 2; }
            command=$1
            ;;
    esac
    shift
done
case $command in
    apply | secrets) ;;
    selftest)
        [ "$dry" = 0 ] && [ -z "$env_file$name$group" ] && [ "$assign" = 0 ] && [ "$days" = 180 ] &&
            [ "$new_cookie" = 0 ] || { echo "entra-app: error: selftest takes no option" >&2; exit 2; }
        ;;
    '')
        # Only a dry run has a default: each command of the script.
        [ "$dry" = 1 ] || { usage >&2; exit 2; }
        command=all
        assign=1
        ;;
    *) usage >&2; exit 2 ;;
esac
usage_error() {
    echo "entra-app: error: $*" >&2
    exit 2
}
if [ "$command" = apply ]; then
    [ "$days" = 180 ] && [ "$new_cookie" = 0 ] || usage_error "--days and --new-cookie-secret are options of secrets"
elif [ "$command" = secrets ]; then
    [ -z "$name$group" ] && [ "$assign" = 0 ] || usage_error "--name, --group and --assign-group are options of apply"
fi
case $days in
    '' | *[!0-9]*) usage_error "--days needs a number from 1 to 730" ;;
esac
[ "$days" -ge 1 ] && [ "$days" -le 730 ] || usage_error "--days needs a number from 1 to 730"

top=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
if [ -z "$env_file" ]; then
    env_file=$top/.env
    if [ "$dry" = 1 ] && [ ! -f "$env_file" ] && [ -f "$top/.env.example" ]; then
        env_file=$top/.env.example
        echo "entra-app: note: .env is not there, so the dry run reads .env.example"
    fi
fi
command -v python3 >/dev/null 2>&1 || die "python3 not found"

# is_sample <path>: the path is the sample file .env.example. That is true for a file
# with that name, for a link to such a file, and for the sample file of this repository
# under another name (a hard link).
is_sample() {
    python3 -I -c '
import os, sys

path, sample = sys.argv[1:3]
names = {os.path.basename(path), os.path.basename(os.path.realpath(path))}
same = os.path.exists(sample) and os.path.samefile(path, sample)
sys.exit(0 if same or ".env.example" in names else 1)
' "$1" "$top/.env.example"
}

if [ "$command" != selftest ]; then
    [ -f "$env_file" ] ||
        die "the environment file $env_file is not there; a dry run can use: --env-file .env.example"
    # A real run writes ids and secrets into the environment file. The sample file is a
    # tracked file, so it is never the target. This test is before each call of `az`.
    if [ "$dry" = 0 ] && is_sample "$env_file"; then
        die "the environment file is the sample file .env.example: a real run does not write into it. Copy it to .env, or give another file with --env-file"
    fi
    [ "$dry" = 1 ] || command -v az >/dev/null 2>&1 || die "the Azure CLI (az) not found"
fi

# env_tool <action> <name> [<argument>]: read or change one name of the environment
# file. A line is NAME=value; one pair of quotes around a value is removed.
#   get <name>           print the value; no line when the name is not set
#   put <name>           set the name to the one line of stdin; refuse an empty value and
#                        a value that the environment file of the host cannot hold
#   random <name>        set the name to 32 new random bytes in URL-safe base64
# A message names the name, never a value.
env_tool() {
    python3 -I -c '
import os, re, secrets, sys

action, path, name = sys.argv[1:4]
with open(path, encoding="utf-8") as handle:
    lines = handle.read().split("\n")
pattern = re.compile(r"^" + re.escape(name) + r"=(.*)$")
found = [i for i, line in enumerate(lines) if pattern.match(line)]
if len(found) > 1:
    sys.exit(f"entra-app: error: the environment file sets {name} two times")

def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"\x27":
        value = value[1:-1]
    return value

if action == "get":
    if found:
        print(unquote(pattern.match(lines[found[0]]).group(1)))
    sys.exit(0)
if action == "put":
    value = sys.stdin.read().strip("\r\n")
elif action == "random":
    value = secrets.token_urlsafe(32)
else:
    sys.exit("entra-app: error: env_tool: unknown action")
if value == "":
    sys.exit(f"entra-app: error: no value for {name}: the command before the pipe gave none")
if "\n" in value or "\x27" in value or value.endswith("\\") or re.search(r"\s", value):
    sys.exit(f"entra-app: error: the new value of {name} has a character that the environment file cannot hold")
line = f"{name}={value}"
if found:
    lines[found[0]] = line
else:
    if lines and lines[-1] == "":
        lines.insert(len(lines) - 1, line)
    else:
        lines.append(line)
mode = os.stat(path).st_mode & 0o777
temporary = path + ".entra-app.tmp"
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines))
os.chmod(temporary, mode)
os.replace(temporary, path)
' "$1" "$env_file" "$2"
}

# show <word>...: print one command of a dry run. A word with a blank or a quote gets
# single quotes.
show() {
    line=
    for word in "$@"; do
        case $word in
            *[!A-Za-z0-9_./:=@%+,-]*) word="'$(printf '%s' "$word" | sed "s/'/'\\\\''/g")'" ;;
        esac
        line="$line $word"
    done
    echo "  $line" | sed 's/^   /  /'
}

# The values of the environment file. None of them is a secret. The selftest has no
# environment file.
domain=
project=
known_tenant=
known_app=
known_group=
if [ "$command" != selftest ]; then
    domain=$(env_tool get EC2_DOMAIN)
    project=$(env_tool get EC2_PROJECT)
    known_tenant=$(env_tool get EC2_OK_ENTRA_TENANT_ID)
    known_app=$(env_tool get EC2_OK_OIDC_CLIENT_ID)
    known_group=$(env_tool get EC2_OK_ENTRA_GROUP_ID)
fi
[ -n "$project" ] || project=${EC2_PROJECT:-tenant-ec2}

# is_id <value>: the form of an id of Entra ID.
is_id() {
    printf '%s\n' "$1" | grep -Eqx '[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
}

# one_id <what> <value>: stop when the value is not exactly one id.
one_id() {
    is_id "$2" || die "the Azure CLI gives no single id for $1"
}

# again <command>...: run a command. When it fails, wait and run it again, four times at
# most. A new object of Entra ID is not on each directory server at once, so the first
# call after a create can fail.
again() {
    try=1
    while ! "$@"; do
        [ "$try" -lt 4 ] || return 1
        try=$((try + 1))
        say "the call failed; the next try is in 10 seconds"
        sleep 10
    done
}

stops=0
# stop_or_note <text>: a real run stops; a dry run says so and continues.
stop_or_note() {
    [ "$dry" = 1 ] || die "$*"
    echo "  # a real run stops here: $*"
    stops=1
}

domain_ok=1
if printf '%s\n' "$domain" | grep -Eqx '[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+'; then
    redirect=https://ok.$domain/oauth2/callback
else
    redirect="https://ok.<EC2_DOMAIN>/oauth2/callback"
    domain_ok=0
fi

# ----- apply -----

apply() {
    [ "$dry" = 0 ] || say "dry run: the commands of apply. No call is made, and nothing is written."
    [ "$domain_ok" = 1 ] || stop_or_note "EC2_DOMAIN of the environment file is not a domain name in small letters"
    [ -n "$name" ] || name="$project OpenKnowledge gate"
    [ -n "$group" ] || group="$project-openknowledge"
    # The two names are inside an OData filter between single quotes.
    for text in "$name" "$group"; do
        printf '%s\n' "$text" | grep -Eqx '[A-Za-z0-9 ._-]+' ||
            usage_error "a name has only letters, digits, blanks, dots, hyphens and underscores"
    done
    nickname=$(printf '%s' "$group" | tr -c 'A-Za-z0-9._-' '-')
    access="[{\"resourceAppId\":\"$GRAPH_APP\",\"resourceAccess\":[{\"id\":\"$USER_READ\",\"type\":\"Scope\"}]}]"

    if [ "$dry" = 1 ]; then
        app="<application id>"
        sp="<object id of the enterprise application>"
        gid="<object id of the group>"
        echo "# The tenant of the session:"
        show az account show --query tenantId --output tsv
        echo "# The app registration with the name, when it is there:"
        show az ad app list --filter "displayName eq '$name'" --query '[].appId' --output tsv
        echo "# When no app registration has the name:"
        show az ad app create --display-name "$name" --sign-in-audience AzureADMyOrg \
            --web-redirect-uris "$redirect" --query appId --output tsv
        echo "# One tenant, one redirect URI, the permission User.Read, no implicit flow:"
        show az ad app update --id "$app" --sign-in-audience AzureADMyOrg \
            --web-redirect-uris "$redirect" --enable-id-token-issuance false \
            --enable-access-token-issuance false --required-resource-accesses "$access"
        echo "# The group claim:"
        show az ad app update --id "$app" --set groupMembershipClaims=SecurityGroup
        echo "# The enterprise application, when it is there:"
        show az ad sp list --filter "appId eq '$app'" --query '[].id' --output tsv
        echo "# When it is not there:"
        show az ad sp create --id "$app" --query id --output tsv
        echo "# The security group with the name, when it is there:"
        show az ad group list --filter "displayName eq '$group'" --query '[].id' --output tsv
        echo "# When no group has the name:"
        show az ad group create --display-name "$group" --mail-nickname "$nickname" \
            --description "The members pass the gate of OpenKnowledge" --query id --output tsv
        if [ "$assign" = 1 ]; then
            echo "# --assign-group: the assignment of the group, when it is there:"
            show az rest --method GET --url "$GRAPH_URL/servicePrincipals/$sp/appRoleAssignedTo" \
                --query "value[?principalId=='$gid'].id" --output tsv
            echo "# When it is not there:"
            show az rest --method POST --url "$GRAPH_URL/servicePrincipals/$sp/appRoleAssignedTo" \
                --headers Content-Type=application/json \
                --body "{\"principalId\":\"$gid\",\"resourceId\":\"$sp\",\"appRoleId\":\"$DEFAULT_ROLE\"}"
            echo "# Then: only an assigned user or group gets a token:"
            show az ad sp update --id "$sp" --set appRoleAssignmentRequired=true
        fi
        echo "# Then the script writes EC2_OK_ENTRA_TENANT_ID, EC2_OK_OIDC_CLIENT_ID and EC2_OK_ENTRA_GROUP_ID into $env_file."
        return 0
    fi

    tenant=$(az account show --query tenantId --output tsv) ||
        die "no session of the Azure CLI: run az login --tenant <tenant id>"
    one_id "the tenant" "$tenant"
    [ -z "$known_tenant" ] || [ "$known_tenant" = "$tenant" ] ||
        die "the session of the Azure CLI is in another tenant than EC2_OK_ENTRA_TENANT_ID"

    app=$(az ad app list --filter "displayName eq '$name'" --query '[].appId' --output tsv) ||
        die "the Azure CLI does not list the app registrations"
    case $(printf '%s' "$app" | grep -c .) in
        0)
            [ -z "$known_app" ] ||
                die "EC2_OK_OIDC_CLIENT_ID has a value, but no app registration has the name; give its name with --name"
            say "make the app registration"
            app=$(az ad app create --display-name "$name" --sign-in-audience AzureADMyOrg \
                --web-redirect-uris "$redirect" --query appId --output tsv) ||
                die "the Azure CLI does not make the app registration"
            ;;
        1) say "the app registration is there: update it" ;;
        *) die "more than one app registration has the name; remove one, or give another name with --name" ;;
    esac
    one_id "the app registration" "$app"
    [ -z "$known_app" ] || [ "$known_app" = "$app" ] ||
        die "the app registration with the name is not the one of EC2_OK_OIDC_CLIENT_ID"

    say "set one tenant, the one redirect URI, the permission User.Read and no implicit flow"
    again az ad app update --id "$app" --sign-in-audience AzureADMyOrg --web-redirect-uris "$redirect" \
        --enable-id-token-issuance false --enable-access-token-issuance false \
        --required-resource-accesses "$access" --only-show-errors >/dev/null ||
        die "the Azure CLI does not update the app registration"
    say "set the group claim"
    again az ad app update --id "$app" --set groupMembershipClaims=SecurityGroup --only-show-errors >/dev/null ||
        die "the Azure CLI does not set the group claim"

    sp=$(az ad sp list --filter "appId eq '$app'" --query '[].id' --output tsv) ||
        die "the Azure CLI does not list the enterprise applications"
    if [ -z "$sp" ]; then
        say "make the enterprise application"
        sp=$(again az ad sp create --id "$app" --query id --output tsv) ||
            die "the Azure CLI does not make the enterprise application"
    fi
    one_id "the enterprise application" "$sp"

    gid=$(az ad group list --filter "displayName eq '$group'" --query '[].id' --output tsv) ||
        die "the Azure CLI does not list the groups"
    case $(printf '%s' "$gid" | grep -c .) in
        0)
            [ -z "$known_group" ] ||
                die "EC2_OK_ENTRA_GROUP_ID has a value, but no group has the name; give its name with --group"
            say "make the security group"
            gid=$(az ad group create --display-name "$group" --mail-nickname "$nickname" \
                --description "The members pass the gate of OpenKnowledge" --query id --output tsv) ||
                die "the Azure CLI does not make the group"
            ;;
        1) say "the security group is there" ;;
        *) die "more than one group has the name; give another name with --group" ;;
    esac
    one_id "the group" "$gid"
    [ -z "$known_group" ] || [ "$known_group" = "$gid" ] ||
        die "the group with the name is not the one of EC2_OK_ENTRA_GROUP_ID"

    if [ "$assign" = 1 ]; then
        assigned=$(az rest --method GET --url "$GRAPH_URL/servicePrincipals/$sp/appRoleAssignedTo" \
            --query "value[?principalId=='$gid'].id" --output tsv) ||
            die "the Azure CLI does not read the assignments of the enterprise application"
        if [ -z "$assigned" ]; then
            say "assign the group to the enterprise application"
            again az rest --method POST --url "$GRAPH_URL/servicePrincipals/$sp/appRoleAssignedTo" \
                --headers Content-Type=application/json \
                --body "{\"principalId\":\"$gid\",\"resourceId\":\"$sp\",\"appRoleId\":\"$DEFAULT_ROLE\"}" \
                --only-show-errors >/dev/null ||
                die "Entra ID does not assign the group; \"assignment required\" is not set. A group assignment needs an Entra ID P1 or P2 licence"
        fi
        say "set \"assignment required\" on the enterprise application"
        again az ad sp update --id "$sp" --set appRoleAssignmentRequired=true --only-show-errors >/dev/null ||
            die "the Azure CLI does not set \"assignment required\""
    fi

    printf '%s\n' "$tenant" | env_tool put EC2_OK_ENTRA_TENANT_ID
    printf '%s\n' "$app" | env_tool put EC2_OK_OIDC_CLIENT_ID
    printf '%s\n' "$gid" | env_tool put EC2_OK_ENTRA_GROUP_ID
    say "wrote EC2_OK_ENTRA_TENANT_ID, EC2_OK_OIDC_CLIENT_ID and EC2_OK_ENTRA_GROUP_ID into $env_file"
    say "next: add each user to the security group \"$group\", then run: scripts/entra-app.sh secrets"
}

# ----- secrets -----

make_secrets() {
    end=$(python3 -I -c 'import datetime, sys; print(datetime.date.today() + datetime.timedelta(days=int(sys.argv[1])))' "$days")
    label="$project gate, made $(python3 -I -c 'import datetime; print(datetime.date.today())')"

    if [ "$dry" = 1 ]; then
        say "dry run: the commands of secrets. No call is made, and nothing is written."
        app=$known_app
        is_id "$app" || app="<application id>"
        [ -n "$known_app" ] || stop_or_note "EC2_OK_OIDC_CLIENT_ID has no value: run the command apply first"
        echo "# A new client secret. The value goes through a pipe into $env_file, as EC2_OK_OIDC_CLIENT_SECRET:"
        show az ad app credential reset --id "$app" --append --display-name "$label" \
            --end-date "$end" --query password --output tsv --only-show-errors
        echo "# The number of client secrets of the app registration:"
        show az ad app credential list --id "$app" --query 'length(@)' --output tsv
        if [ "$new_cookie" = 1 ] || [ -z "$(env_tool get EC2_OK_COOKIE_SECRET | sed 's/./x/g')" ]; then
            echo "# Then the script writes a new EC2_OK_COOKIE_SECRET: 32 random bytes in URL-safe base64."
        else
            echo "# EC2_OK_COOKIE_SECRET has a value and keeps it."
        fi
        return 0
    fi

    [ -n "$known_app" ] || die "EC2_OK_OIDC_CLIENT_ID has no value: run the command apply first"
    is_id "$known_app" || die "EC2_OK_OIDC_CLIENT_ID is not an id of Entra ID"
    say "make a new client secret that ends on $end"
    # The secret goes from the Azure CLI into the file. This script holds it in no
    # variable. When the Azure CLI fails, it gives no value, and env_tool stops.
    az ad app credential reset --id "$known_app" --append --display-name "$label" --end-date "$end" \
        --query password --output tsv --only-show-errors | env_tool put EC2_OK_OIDC_CLIENT_SECRET ||
        die "no new client secret: EC2_OK_OIDC_CLIENT_SECRET keeps its value"
    say "wrote EC2_OK_OIDC_CLIENT_SECRET into $env_file"
    say "the client secret ends on $end: write the date down, and run this command again before it"
    count=$(az ad app credential list --id "$known_app" --query 'length(@)' --output tsv) || count=
    case $count in
        '' | 1 | *[!0-9]*) ;;
        *)
            say "the app registration has $count client secrets. An older one stays valid until its end date."
            say "after the host has the new secret, list them and remove each older one:"
            echo "  az ad app credential list --id <application id> --query '[].{keyId:keyId,name:displayName,end:endDateTime}' --output table"
            echo "  az ad app credential delete --id <application id> --key-id <key id>"
            ;;
    esac

    # Only the number of characters of the cookie secret is read here, not the value.
    if [ "$new_cookie" = 1 ] || [ -z "$(env_tool get EC2_OK_COOKIE_SECRET | sed 's/./x/g')" ]; then
        env_tool random EC2_OK_COOKIE_SECRET
        say "wrote a new EC2_OK_COOKIE_SECRET into $env_file"
    else
        say "EC2_OK_COOKIE_SECRET has a value and keeps it"
    fi
    say "next: write the environment file to the secret of the host (scripts/aws-host.sh secret)"
}

# ----- selftest -----

# The tests of this script. Each run is a run of a copy of this script in a fixture tree,
# with a stub of `az` first on PATH and with no value of the shell. The stub records each
# call and answers as the Azure CLI does for a tenant with no app registration and no
# group. Each id and each value of the stub is a fixed text of this test.
selftest() {
    fixture=$(mktemp -d "${TMPDIR:-/tmp}/entra-app-selftest.XXXXXX")
    trap 'rm -rf "$fixture"' EXIT
    trap 'exit 130' INT TERM HUP
    mkdir -p "$fixture/bin" "$fixture/tree/scripts" "$fixture/other"
    cp "$0" "$fixture/tree/scripts/entra-app.sh"
    cat >"$fixture/bin/az" <<'EOT'
#!/bin/sh
echo "$*" >>"$STUB_CALLS"
case "$1 $2 ${3:-} ${4:-}" in
    "account show "*) echo 11111111-1111-4111-8111-111111111111 ;;
    "ad app create "*) echo 22222222-2222-4222-8222-222222222222 ;;
    "ad sp create "*) echo 33333333-3333-4333-8333-333333333333 ;;
    "ad group create "*) echo 44444444-4444-4444-8444-444444444444 ;;
    "ad app credential reset") echo stub-value-of-the-selftest ;;
    "ad app credential list") echo 1 ;;
esac
EOT
    chmod 755 "$fixture/bin/az"
    # The sample file of the fixture tree. It has a domain, so that the rule of the
    # sample file is the only reason for a refusal.
    cat >"$fixture/sample" <<'EOT'
EC2_DOMAIN=ec2.selftest.test
EC2_OK_ENTRA_TENANT_ID=
EC2_OK_ENTRA_GROUP_ID=
EC2_OK_OIDC_CLIENT_ID=
EC2_OK_OIDC_CLIENT_SECRET=
EC2_OK_COOKIE_SECRET=
EOT
    cp "$fixture/sample" "$fixture/tree/.env.example"
    cp "$fixture/sample" "$fixture/other/.env.example"
    ln -s "$fixture/tree/.env.example" "$fixture/link"
    ln -s "$fixture/other/.env.example" "$fixture/other-link"

    tests=0
    failed=0
    # run <argument>...: one run of the copy. The status is in $status, the output is in
    # the file out, and the calls of `az` are in the file calls.
    run() {
        : >"$fixture/calls"
        status=0
        env -i PATH="$fixture/bin:$PATH" HOME="$fixture" STUB_CALLS="$fixture/calls" \
            sh "$fixture/tree/scripts/entra-app.sh" "$@" >"$fixture/out" 2>&1 || status=$?
    }
    # expect <text> <command>...: one test. The command prints nothing.
    expect() {
        tests=$((tests + 1))
        text=$1
        shift
        if ! "$@" >/dev/null 2>&1; then
            echo "selftest: FAIL - $text"
            failed=$((failed + 1))
        fi
    }
    # lacks <grep option> <text> <file>: the file has no line with the text.
    lacks() {
        ! grep -q "$1" -e "$2" "$3"
    }
    # refused <text>: the last run stops because of the sample file, makes no call of
    # `az` and changes no sample file.
    refused() {
        expect "$1: the run stops with the status 1" test "$status" = 1
        expect "$1: the message names the sample file" grep -qF 'the sample file .env.example' "$fixture/out"
        expect "$1: the run makes no call of az" test ! -s "$fixture/calls"
        expect "$1: the sample file of the tree has no change" cmp -s "$fixture/sample" "$fixture/tree/.env.example"
        expect "$1: the other sample file has no change" cmp -s "$fixture/sample" "$fixture/other/.env.example"
    }

    # A real run refuses the sample file as its environment file.
    run apply --env-file "$fixture/tree/.env.example"
    refused "apply with the sample file of the tree"
    run secrets --env-file "$fixture/tree/.env.example"
    refused "secrets with the sample file of the tree"
    run apply --assign-group --env-file "$fixture/tree/.env.example"
    refused "apply --assign-group with the sample file of the tree"
    run apply --env-file "$fixture/other/.env.example"
    refused "apply with a file .env.example of another directory"
    run apply --env-file "$fixture/link"
    refused "apply with a link to the sample file of the tree"
    run apply --env-file "$fixture/other-link"
    refused "apply with a link to a file .env.example of another directory"
    ln "$fixture/tree/.env.example" "$fixture/tree/.env"
    run apply
    refused "apply with .env as a second name of the sample file"
    run secrets
    refused "secrets with .env as a second name of the sample file"
    rm -f "$fixture/tree/.env"

    # A real run with no .env does not use the sample file in its place.
    run apply
    expect "apply with no .env: the run stops with the status 1" test "$status" = 1
    expect "apply with no .env: the run makes no call of az" test ! -s "$fixture/calls"
    expect "apply with no .env: the sample file has no change" cmp -s "$fixture/sample" "$fixture/tree/.env.example"

    # A dry run reads the sample file and writes nothing.
    run --dry-run
    expect "a dry run with no command exits with 0" test "$status" = 0
    expect "a dry run with no command prints 14 lines with az" test "$(grep -c '^  az ' "$fixture/out")" = 14
    expect "a dry run makes no call of az" test ! -s "$fixture/calls"
    expect "a dry run changes no sample file" cmp -s "$fixture/sample" "$fixture/tree/.env.example"
    run --dry-run apply --env-file "$fixture/tree/.env.example"
    expect "a dry run of apply with the sample file exits with 0" test "$status" = 0
    expect "a dry run of apply makes no call of az" test ! -s "$fixture/calls"

    # The control: the same runs pass with an environment file that is not the sample
    # file. So the stub works, and the refusals above come from the rule.
    cp "$fixture/sample" "$fixture/tree/.env"
    run apply --assign-group
    expect "the control apply exits with 0" test "$status" = 0
    expect "the control apply calls az" test -s "$fixture/calls"
    expect "the control apply assigns the group" grep -q '^rest --method POST ' "$fixture/calls"
    expect "the control apply sets assignment required" \
        grep -q '^ad sp update .*appRoleAssignmentRequired=true' "$fixture/calls"
    expect "the control apply writes the three ids into .env" \
        grep -qx 'EC2_OK_OIDC_CLIENT_ID=22222222-2222-4222-8222-222222222222' "$fixture/tree/.env"
    expect "the control apply prints no id" lacks -E '[0-9a-f]{8}-[0-9a-f]{4}-' "$fixture/out"
    run secrets
    expect "the control secrets exits with 0" test "$status" = 0
    expect "the control secrets writes the client secret into .env" \
        grep -qx 'EC2_OK_OIDC_CLIENT_SECRET=stub-value-of-the-selftest' "$fixture/tree/.env"
    expect "the control secrets writes a cookie secret of 43 characters" \
        grep -qEx 'EC2_OK_COOKIE_SECRET=[A-Za-z0-9_-]{43}' "$fixture/tree/.env"
    expect "the control secrets prints no secret" lacks -F stub-value-of-the-selftest "$fixture/out"
    expect "the control runs change no sample file" cmp -s "$fixture/sample" "$fixture/tree/.env.example"

    if [ "$failed" -gt 0 ]; then
        echo "selftest: FAIL ($failed of $tests tests; a stub of az, no call to Azure)"
        exit 1
    fi
    echo "selftest: pass ($tests tests; a stub of az, no call to Azure)"
}

case $command in
    apply) apply ;;
    secrets) make_secrets ;;
    selftest) selftest ;;
    all)
        apply
        make_secrets
        ;;
esac
[ "$stops" = 0 ] || say "dry run: a real run stops at a line that says so"
