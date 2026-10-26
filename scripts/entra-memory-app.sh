#!/bin/sh
# The Entra app of one memory stage: the OIDC provider of the Cognito user pool of the
# stage. Make it or reuse it with the Azure CLI, assign the named users, and put its
# tenant id, client id and client secret into credsync. See docs/entra.md, section
# "The memory app of a stage". The gate of OpenKnowledge has its own script,
# scripts/entra-app.sh.
#
# Usage: scripts/entra-memory-app.sh [--dry-run] --stage <stage> [<option>]... apply
#        scripts/entra-memory-app.sh [--dry-run] --stage <stage> [--project <name>] --assign-user <object id>
#        scripts/entra-memory-app.sh selftest
#   apply
#       Make the app registration when no one has the name; else reuse it. Each run sets
#       the same state:
#       - one tenant only (the sign-in audience AzureADMyOrg);
#       - one Web redirect URI, the callback of the Cognito domain of the stage:
#         https://<project>-memory-<stage>.auth.<region>.amazoncognito.com/oauth2/idpresponse;
#       - the delegated permissions openid, profile and User.Read of Microsoft Graph,
#         with the ids of the scopes read from the tenant;
#       - no token from the implicit flow.
#       Then it makes the enterprise application (the service principal) when it is not
#       there, gives admin consent for all users (one AllPrincipals grant), sets
#       "assignment required" and assigns each --user directly. It removes no assignment.
#       Then it writes three credsync names (bw-session-update.sh, value on stdin):
#         TENANT_EC2_MEMORY_<STAGE>_ENTRA_TENANT_ID
#         TENANT_EC2_MEMORY_<STAGE>_ENTRA_CLIENT_ID
#         TENANT_EC2_MEMORY_<STAGE>_ENTRA_CLIENT_SECRET
#       The client secret is made only when the app has no client secret, or with
#       --rotate. A re-run makes no second secret.
#   --assign-user <object id>
#       Assign one user directly to the enterprise application of the stage, when the
#       user has no assignment: the step 7 of apply for one object id. The app
#       registration and the enterprise application must exist: run apply first. It
#       changes no other setting and writes nothing to credsync. It takes no command,
#       and of the options only --stage, --project and --dry-run.
#   selftest
#       Test this script on a copy of it in a temporary directory, with a stub of `az`
#       and of `bw-session-update.sh` first on PATH. It makes no call to Azure and no
#       write to credsync. The last line is "selftest: pass" or "selftest: FAIL".
#
# Options of apply:
#   --stage <stage>      the stage: a word of small letters and digits, for example dev
#   --project <name>     the project; the default is EC2_PROJECT of the shell, else
#                        tenant-ec2
#   --region <region>    the AWS region of the Cognito domain; the default is the line
#                        region: of config/memory/<stage>.yaml
#   --user <id or UPN>   a user to assign: an object id or a user principal name. Give
#                        it more than one time for more users. A user comes from the
#                        command line only, never from a tracked file.
#   --rotate             make a new client secret also when the app has one. An older
#                        secret stays valid until its end date: the script prints how
#                        to remove it.
#   --days <number>      the life of a new client secret, from 1 to 180; the default is
#                        180 (six months or less)
#   --dry-run            print each step and each Azure CLI command; make no call and
#                        write nothing
#   --help               print this text
#
# Before a real run: sign in with `az login --tenant <tenant id>`, with an identity that
# can make an app registration and grant admin consent. The script uses the tenant of
# that session.
#
# Secrets. The script prints no secret and no id: a message names a step or a credsync
# name, never a value. A user is named by its place on the command line. The client
# secret goes from the Azure CLI through a pipe to the standard input of
# bw-session-update.sh. It is in no argument of a command and in no variable of this
# script. The script uses no trace (`set -x`).
#
# Exit: 0 pass, 1 an error, 2 usage.
# Needs: a POSIX shell with sed, python3; for a real run, the Azure CLI (`az`) and
# bw-session-update.sh of credsync.

set -eu
# No trace: a trace would show the values of the steps.
set +x

# Microsoft Graph has the same application id in each tenant.
GRAPH_APP=00000003-0000-0000-c000-000000000000
GRAPH_URL=https://graph.microsoft.com/v1.0
SCOPES="openid profile User.Read"
# The app role of an application with no role of its own: "Default Access".
DEFAULT_ROLE=00000000-0000-0000-0000-000000000000
CREDSYNC=bw-session-update.sh

usage() {
    sed -n '2,/^$/s/^# \{0,1\}//p' "$0"
}

die() {
    echo "entra-memory-app: error: $*" >&2
    exit 1
}

usage_error() {
    echo "entra-memory-app: error: $*" >&2
    exit 2
}

say() {
    echo "entra-memory-app: $*"
}

dry=0
command=
stage=
project=
region=
users=
user_count=0
assign=
rotate=0
days=180
while [ $# -gt 0 ]; do
    case $1 in
        -h | --help) usage; exit 0 ;;
        --dry-run) dry=1 ;;
        --rotate) rotate=1 ;;
        --stage | --project | --region | --user | --assign-user | --days)
            [ $# -ge 2 ] || usage_error "$1 needs a value"
            case $1 in
                --stage) stage=$2 ;;
                --project) project=$2 ;;
                --region) region=$2 ;;
                --days) days=$2 ;;
                --assign-user)
                    # An object id only. The form keeps the value out of a quote of a query.
                    [ -z "$assign" ] || usage_error "--assign-user takes one user"
                    printf '%s\n' "$2" | grep -Eqx '[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}' ||
                        usage_error "--assign-user needs the object id of a user"
                    assign=$2
                    ;;
                --user)
                    # An object id or a user principal name. The form keeps the value
                    # out of a quote of a query and out of a path of Graph.
                    printf '%s\n' "$2" | grep -Eqx '[A-Za-z0-9][A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+)?' ||
                        usage_error "--user needs an object id or a user principal name"
                    users="$users $2"
                    user_count=$((user_count + 1))
                    ;;
            esac
            shift
            ;;
        -*) usage >&2; exit 2 ;;
        *)
            [ -z "$command" ] || { usage >&2; exit 2; }
            command=$1
            ;;
    esac
    shift
done

top=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

if [ -n "$assign" ]; then
    [ -z "$command$region$users" ] && [ "$rotate" = 0 ] && [ "$days" = 180 ] ||
        usage_error "--assign-user takes no command, and of the options only --stage, --project and --dry-run"
    command="assign-user"
fi
case $command in
    apply) ;;
    # Only the option --assign-user selects this command.
    assign-user) [ -n "$assign" ] || { usage >&2; exit 2; } ;;
    selftest)
        [ "$dry" = 0 ] && [ -z "$stage$project$region$users" ] && [ "$rotate" = 0 ] &&
            [ "$days" = 180 ] || usage_error "selftest takes no option"
        ;;
    *) usage >&2; exit 2 ;;
esac
command -v python3 >/dev/null 2>&1 || die "python3 not found"

if [ "$command" != selftest ]; then
    printf '%s\n' "$stage" | grep -Eqx '[a-z][a-z0-9]{0,15}' ||
        usage_error "--stage needs a word of small letters and digits, for example dev"
    [ -n "$project" ] || project=${EC2_PROJECT:-tenant-ec2}
    printf '%s\n' "$project" | grep -Eqx '[a-z][a-z0-9-]{0,30}[a-z0-9]' ||
        usage_error "--project needs small letters, digits and hyphens"
fi
if [ "$command" = assign-user ] && [ "$dry" = 0 ]; then
    command -v az >/dev/null 2>&1 || die "the Azure CLI (az) not found"
fi
if [ "$command" = apply ]; then
    if [ -z "$region" ] && [ -f "$top/config/memory/$stage.yaml" ]; then
        region=$(sed -n 's/^region:[[:space:]]*\([a-z0-9-]*\)[[:space:]]*$/\1/p' "$top/config/memory/$stage.yaml" | head -n 1)
    fi
    printf '%s\n' "$region" | grep -Eqx '[a-z]{2}(-[a-z]+)+-[0-9]' ||
        usage_error "no AWS region: give --region, or set region: in config/memory/$stage.yaml"
    case $days in
        '' | *[!0-9]*) usage_error "--days needs a number from 1 to 180" ;;
    esac
    [ "$days" -ge 1 ] && [ "$days" -le 180 ] || usage_error "--days needs a number from 1 to 180"
    if [ "$dry" = 0 ]; then
        command -v az >/dev/null 2>&1 || die "the Azure CLI (az) not found"
        command -v "$CREDSYNC" >/dev/null 2>&1 || die "$CREDSYNC of credsync not found"
    fi
fi

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

# is_id <value>: the form of an id of Entra ID.
is_id() {
    printf '%s\n' "$1" | grep -Eqx '[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
}

# one_id <what> <value>: stop when the value is not exactly one line that is an id.
one_id() {
    case $2 in
        *"
"*) die "the Azure CLI gives no single id for $1" ;;
    esac
    is_id "$2" || die "the Azure CLI gives no single id for $1"
}

# again <command>...: run a command. When it fails, wait and run it again, five times at
# most. A new object of Entra ID is not on each directory server at once. The message
# goes to stderr, so that it never becomes a part of a captured value.
again() {
    try=1
    while ! "$@"; do
        [ "$try" -lt 5 ] || return 1
        try=$((try + 1))
        say "the call failed; the next try is in 10 seconds" >&2
        sleep 10
    done
}

# to_credsync <name>: give the one line of stdin to bw-session-update.sh on its stdin,
# with no line end. Refuse an empty value, so that a failed command before the pipe
# writes nothing. The value is in no argument and is never printed. Exit 1: no value;
# exit 3: credsync did not write the value.
to_credsync() {
    python3 -I -c '
import subprocess, sys

tool, name = sys.argv[1:3]
value = sys.stdin.read().strip("\r\n")
if value == "" or "\n" in value:
    sys.exit(f"entra-memory-app: error: no single value for {name}: the command before the pipe gave none")
result = subprocess.run([tool, name], input=value, text=True, stdout=subprocess.DEVNULL)
if result.returncode != 0:
    print(f"entra-memory-app: error: {tool} did not write {name}", file=sys.stderr)
    sys.exit(3)
' "$CREDSYNC" "$1"
}

# assign_one <label> <user>: assign the user directly to the enterprise application $sp,
# when it has no assignment. The label names the user in each message; no id is printed.
assign_one() {
    oid=$(az ad user show --id "$2" --query id --output tsv) ||
        die "$1 of the command line: Entra ID does not know it"
    one_id "$1 of the command line" "$oid"
    assigned=$(az rest --method GET --url "$GRAPH_URL/servicePrincipals/$sp/appRoleAssignedTo" \
        --query "value[?principalId=='$oid'].id" --output tsv) ||
        die "the Azure CLI does not read the assignments of the enterprise application"
    if [ -z "$assigned" ]; then
        again az rest --method POST --url "$GRAPH_URL/servicePrincipals/$sp/appRoleAssignedTo" \
            --headers Content-Type=application/json \
            --body "{\"principalId\":\"$oid\",\"resourceId\":\"$sp\",\"appRoleId\":\"$DEFAULT_ROLE\"}" \
            --output none ||
            die "Entra ID does not assign $1 of the command line"
        say "  $1: assigned"
    else
        say "  $1: already assigned"
    fi
}

# show_assign <user> <object id>: print the commands of assign_one for the dry run, with
# the two texts in place of the values.
show_assign() {
    show az ad user show --id "$1" --query id --output tsv
    show az rest --method GET --url "$GRAPH_URL/servicePrincipals/$sp/appRoleAssignedTo" \
        --query "value[?principalId=='$2'].id" --output tsv
    echo "# When the user is not assigned:"
    show az rest --method POST --url "$GRAPH_URL/servicePrincipals/$sp/appRoleAssignedTo" \
        --headers Content-Type=application/json \
        --body "{\"principalId\":\"$2\",\"resourceId\":\"$sp\",\"appRoleId\":\"$DEFAULT_ROLE\"}" \
        --output none
}

# ----- apply -----

apply() {
    stage_upper=$(printf '%s' "$stage" | tr '[:lower:]' '[:upper:]')
    prefix=TENANT_EC2_MEMORY_${stage_upper}_ENTRA
    name="$project memory $stage"
    redirect="https://$project-memory-$stage.auth.$region.amazoncognito.com/oauth2/idpresponse"
    end=$(python3 -I -c 'import datetime, sys; print(datetime.date.today() + datetime.timedelta(days=int(sys.argv[1])))' "$days")
    label="$project memory $stage, made $(python3 -I -c 'import datetime; print(datetime.date.today())')"

    if [ "$dry" = 1 ]; then
        dry_run
        return 0
    fi

    say "step 1: the tenant of the session"
    tenant=$(az account show --query tenantId --output tsv) ||
        die "no session of the Azure CLI: run az login --tenant <tenant id>"
    one_id "the tenant" "$tenant"

    say "step 2: the ids of the scopes $SCOPES of Microsoft Graph in this tenant"
    graph_sp=$(az ad sp show --id "$GRAPH_APP" --query id --output tsv) ||
        die "the Azure CLI does not read the service principal of Microsoft Graph"
    one_id "Microsoft Graph" "$graph_sp"
    entries=
    for scope in $SCOPES; do
        sid=$(az ad sp show --id "$GRAPH_APP" \
            --query "oauth2PermissionScopes[?value=='$scope'].id" --output tsv) ||
            die "the Azure CLI does not read the scope $scope"
        one_id "the scope $scope" "$sid"
        entries="$entries${entries:+,}{\"id\":\"$sid\",\"type\":\"Scope\"}"
    done
    access="[{\"resourceAppId\":\"$GRAPH_APP\",\"resourceAccess\":[$entries]}]"

    say "step 3: the app registration \"$name\""
    app=$(az ad app list --filter "displayName eq '$name'" --query '[].appId' --output tsv) ||
        die "the Azure CLI does not list the app registrations"
    case $(printf '%s' "$app" | grep -c .) in
        0)
            app=$(az ad app create --display-name "$name" --sign-in-audience AzureADMyOrg \
                --web-redirect-uris "$redirect" --required-resource-accesses "$access" \
                --query appId --output tsv --only-show-errors) ||
                die "the Azure CLI does not make the app registration"
            say "  app registration: made"
            ;;
        1) say "  app registration: there, reused" ;;
        *) die "more than one app registration has the name \"$name\"; remove one" ;;
    esac
    one_id "the app registration" "$app"
    again az ad app update --id "$app" --sign-in-audience AzureADMyOrg --web-redirect-uris "$redirect" \
        --enable-id-token-issuance false --enable-access-token-issuance false \
        --required-resource-accesses "$access" --only-show-errors >/dev/null ||
        die "the Azure CLI does not update the app registration"
    say "  one tenant, the redirect URI of the stage, the three permissions, no implicit flow: set"

    say "step 4: the enterprise application"
    sp=$(az ad sp list --filter "appId eq '$app'" --query '[].id' --output tsv) ||
        die "the Azure CLI does not list the enterprise applications"
    if [ -z "$sp" ]; then
        sp=$(again az ad sp create --id "$app" --query id --output tsv --only-show-errors) ||
            die "the Azure CLI does not make the enterprise application"
        say "  enterprise application: made"
    else
        say "  enterprise application: there"
    fi
    one_id "the enterprise application" "$sp"

    say "step 5: admin consent for all users ($SCOPES)"
    grants=$(az rest --method GET \
        --url "$GRAPH_URL/oauth2PermissionGrants?\$filter=clientId eq '$sp' and resourceId eq '$graph_sp'" \
        --query "value[?consentType=='AllPrincipals'].[id,scope]" --output tsv) ||
        die "the Azure CLI does not read the consent grants"
    case $(printf '%s' "$grants" | grep -c .) in
        0)
            again az rest --method POST --url "$GRAPH_URL/oauth2PermissionGrants" \
                --headers Content-Type=application/json \
                --body "{\"clientId\":\"$sp\",\"consentType\":\"AllPrincipals\",\"resourceId\":\"$graph_sp\",\"scope\":\"$SCOPES\"}" \
                --output none ||
                die "the Azure CLI does not grant admin consent"
            say "  admin consent: granted"
            ;;
        1)
            gid=$(printf '%s\n' "$grants" | cut -f 1)
            have=$(printf '%s\n' "$grants" | cut -f 2- | tr '\t' ' ' | tr ' ' '\n' | grep . | sort | tr '\n' ' ')
            # shellcheck disable=SC2086 # one word for each scope
            want=$(printf '%s\n' $SCOPES | sort | tr '\n' ' ')
            if [ "$have" = "$want" ]; then
                say "  admin consent: there, with the three scopes"
            else
                printf '%s\n' "$gid" | grep -Eqx '[A-Za-z0-9_-]+' || die "the Azure CLI gives no id of the consent grant"
                again az rest --method PATCH --url "$GRAPH_URL/oauth2PermissionGrants/$gid" \
                    --headers Content-Type=application/json --body "{\"scope\":\"$SCOPES\"}" --output none ||
                    die "the Azure CLI does not change the consent grant"
                say "  admin consent: there, scopes set to $SCOPES"
            fi
            ;;
        *) die "more than one admin consent grant for Microsoft Graph; remove one" ;;
    esac

    say "step 6: \"assignment required\" on the enterprise application"
    again az ad sp update --id "$sp" --set appRoleAssignmentRequired=true --only-show-errors >/dev/null ||
        die "the Azure CLI does not set \"assignment required\""
    say "  assignment required: set"

    say "step 7: the direct assignment of $user_count named user(s)"
    [ "$user_count" -gt 0 ] || say "  no --user: the script assigns nobody and keeps each assignment"
    n=0
    for user in $users; do
        n=$((n + 1))
        assign_one "user $n" "$user"
    done

    say "step 8: the tenant id and the client id into credsync"
    printf '%s\n' "$tenant" | to_credsync "${prefix}_TENANT_ID"
    say "  wrote ${prefix}_TENANT_ID"
    printf '%s\n' "$app" | to_credsync "${prefix}_CLIENT_ID"
    say "  wrote ${prefix}_CLIENT_ID"

    say "step 9: the client secret"
    count=$(az ad app credential list --id "$app" --query 'length(@)' --output tsv) ||
        die "the Azure CLI does not list the client secrets"
    case $count in
        '' | *[!0-9]*) die "the Azure CLI gives no number of client secrets" ;;
    esac
    if [ "$count" -gt 0 ] && [ "$rotate" = 0 ]; then
        say "  client secret: there ($count), kept; no new secret. --rotate makes a new one"
        # Only the presence of a value in the environment is tested, never the value.
        python3 -I -c 'import os, sys; sys.exit(0 if os.environ.get(sys.argv[1]) else 1)' \
            "${prefix}_CLIENT_SECRET" ||
            say "  note: ${prefix}_CLIENT_SECRET has no value in this shell; when credsync lacks it, run again with --rotate"
    else
        # The secret goes from the Azure CLI into credsync. This script holds it in no
        # variable. When the Azure CLI fails, it gives no value, and to_credsync stops.
        az ad app credential reset --id "$app" --append --display-name "$label" --end-date "$end" \
            --query password --output tsv --only-show-errors | to_credsync "${prefix}_CLIENT_SECRET" || {
            [ $? != 3 ] || die "Entra ID has a new client secret, but credsync did not store it: run again with --rotate"
            die "no new client secret in credsync: ${prefix}_CLIENT_SECRET keeps its value"
        }
        say "  client secret: made, ends on $end; wrote ${prefix}_CLIENT_SECRET"
        say "  write the end date down, and run again with --rotate before it"
        if [ "$count" -gt 0 ]; then
            say "  an older client secret stays valid until its end date. After the stage has the new one, remove it:"
            echo "    az ad app credential list --id <application id> --query '[].{keyId:keyId,name:displayName,end:endDateTime}' --output table"
            echo "    az ad app credential delete --id <application id> --key-id <key id>"
        fi
    fi
    say "done: the app of the memory stage $stage"
}

# dry_run: print each step and its commands with the ids as texts between < and >.
dry_run() {
    say "dry run: the steps of apply for the stage $stage. No call is made, and nothing is written."
    app="<application id>"
    sp="<object id of the enterprise application>"
    graph_sp="<object id of Microsoft Graph>"
    echo "# Step 1: the tenant of the session:"
    show az account show --query tenantId --output tsv
    echo "# Step 2: the ids of the scopes $SCOPES of Microsoft Graph in this tenant:"
    show az ad sp show --id "$GRAPH_APP" --query id --output tsv
    for scope in $SCOPES; do
        show az ad sp show --id "$GRAPH_APP" --query "oauth2PermissionScopes[?value=='$scope'].id" --output tsv
    done
    access="[{\"resourceAppId\":\"$GRAPH_APP\",\"resourceAccess\":[{\"id\":\"<openid>\",\"type\":\"Scope\"},{\"id\":\"<profile>\",\"type\":\"Scope\"},{\"id\":\"<User.Read>\",\"type\":\"Scope\"}]}]"
    echo "# Step 3: the app registration with the name, when it is there:"
    show az ad app list --filter "displayName eq '$name'" --query '[].appId' --output tsv
    echo "# When no app registration has the name:"
    show az ad app create --display-name "$name" --sign-in-audience AzureADMyOrg \
        --web-redirect-uris "$redirect" --required-resource-accesses "$access" \
        --query appId --output tsv --only-show-errors
    echo "# One tenant, the redirect URI of the stage, the three permissions, no implicit flow:"
    show az ad app update --id "$app" --sign-in-audience AzureADMyOrg --web-redirect-uris "$redirect" \
        --enable-id-token-issuance false --enable-access-token-issuance false \
        --required-resource-accesses "$access" --only-show-errors
    echo "# Step 4: the enterprise application, when it is there:"
    show az ad sp list --filter "appId eq '$app'" --query '[].id' --output tsv
    echo "# When it is not there:"
    show az ad sp create --id "$app" --query id --output tsv --only-show-errors
    echo "# Step 5: the admin consent grant, when it is there:"
    show az rest --method GET \
        --url "$GRAPH_URL/oauth2PermissionGrants?\$filter=clientId eq '$sp' and resourceId eq '$graph_sp'" \
        --query "value[?consentType=='AllPrincipals'].[id,scope]" --output tsv
    echo "# When it is not there:"
    show az rest --method POST --url "$GRAPH_URL/oauth2PermissionGrants" \
        --headers Content-Type=application/json \
        --body "{\"clientId\":\"$sp\",\"consentType\":\"AllPrincipals\",\"resourceId\":\"$graph_sp\",\"scope\":\"$SCOPES\"}" \
        --output none
    echo "# When it is there with other scopes:"
    show az rest --method PATCH --url "$GRAPH_URL/oauth2PermissionGrants/<id of the grant>" \
        --headers Content-Type=application/json --body "{\"scope\":\"$SCOPES\"}" --output none
    echo "# Step 6: only an assigned user gets a token:"
    show az ad sp update --id "$sp" --set appRoleAssignmentRequired=true --only-show-errors
    echo "# Step 7: for each of the $user_count --user values, its object id and its assignment:"
    show_assign "<user n>" "<object id of user n>"
    echo "# Step 8: the two ids go on stdin into credsync:"
    echo "  <tenant id> | $CREDSYNC ${prefix}_TENANT_ID"
    echo "  <application id> | $CREDSYNC ${prefix}_CLIENT_ID"
    echo "# Step 9: the number of client secrets:"
    show az ad app credential list --id "$app" --query 'length(@)' --output tsv
    if [ "$rotate" = 1 ]; then
        echo "# --rotate: a new client secret that ends on $end. The value goes through a pipe into credsync:"
    else
        echo "# When the number is 0: a new client secret that ends on $end. The value goes through a pipe into credsync:"
    fi
    show az ad app credential reset --id "$app" --append --display-name "$label" --end-date "$end" \
        --query password --output tsv --only-show-errors
    echo "  | $CREDSYNC ${prefix}_CLIENT_SECRET"
}

# ----- assign-user -----

# assign_user: the step 7 of apply for the one --assign-user value. It finds the app and
# the enterprise application by the name of the stage and makes neither.
assign_user() {
    name="$project memory $stage"
    if [ "$dry" = 1 ]; then
        say "dry run: the steps of --assign-user for the stage $stage. No call is made, and nothing is written."
        app="<application id>"
        sp="<object id of the enterprise application>"
        echo "# Step 1: the app registration with the name:"
        show az ad app list --filter "displayName eq '$name'" --query '[].appId' --output tsv
        echo "# Step 2: the enterprise application of the app registration:"
        show az ad sp list --filter "appId eq '$app'" --query '[].id' --output tsv
        echo "# Step 3: the user and its assignment:"
        show_assign "<object id of the user>" "<object id of the user>"
        return 0
    fi

    say "step 1: the app registration \"$name\""
    app=$(az ad app list --filter "displayName eq '$name'" --query '[].appId' --output tsv) ||
        die "the Azure CLI does not list the app registrations"
    case $(printf '%s' "$app" | grep -c .) in
        1) ;;
        0) die "no app registration has the name \"$name\": run apply first" ;;
        *) die "more than one app registration has the name \"$name\"; remove one" ;;
    esac
    one_id "the app registration" "$app"

    say "step 2: the enterprise application"
    sp=$(az ad sp list --filter "appId eq '$app'" --query '[].id' --output tsv) ||
        die "the Azure CLI does not list the enterprise applications"
    [ -n "$sp" ] || die "the app registration has no enterprise application: run apply first"
    one_id "the enterprise application" "$sp"

    say "step 3: the direct assignment of the --assign-user value"
    assign_one "the user" "$assign"
    say "done: the user of the memory stage $stage; no other change"
}

# ----- selftest -----

# The tests of this script. Each run is a run of a copy of this script in a fixture tree,
# with a stub of `az` and of bw-session-update.sh first on PATH and with no value of the
# shell. The stub of `az` keeps the state of a tenant in files: the first run makes each
# object, a later run finds it. Each id and each value of the stub is a fixed text.
selftest() {
    fixture=$(mktemp -d "${TMPDIR:-/tmp}/entra-memory-app-selftest.XXXXXX")
    trap 'rm -rf "$fixture"' EXIT
    trap 'exit 130' INT TERM HUP
    mkdir -p "$fixture/bin" "$fixture/tree/scripts" "$fixture/tree/config/memory" "$fixture/state"
    cp "$0" "$fixture/tree/scripts/entra-memory-app.sh"
    printf 'schemaVersion: 1\nstage: dev\nregion: us-east-1\n' >"$fixture/tree/config/memory/dev.yaml"
    cat >"$fixture/bin/az" <<'EOT'
#!/bin/sh
# Each argument on its own line, then a separator line.
for word in "$@"; do printf '%s\n' "$word"; done >>"$STUB_CALLS"
echo "----" >>"$STUB_CALLS"
s=$STUB_STATE
all="$*"
case "$all" in
    "account show "*) echo 11111111-1111-4111-8111-111111111111 ;;
    "ad sp show "*"value=='openid'"*) echo 55555555-5555-4555-8555-555555555551 ;;
    "ad sp show "*"value=='profile'"*) echo 55555555-5555-4555-8555-555555555552 ;;
    "ad sp show "*"value=='User.Read'"*) echo 55555555-5555-4555-8555-555555555553 ;;
    "ad sp show "*) echo 66666666-6666-4666-8666-666666666666 ;;
    "ad app list "*) [ ! -f "$s/app" ] || echo 22222222-2222-4222-8222-222222222222 ;;
    "ad app create "*) : >"$s/app"; echo 22222222-2222-4222-8222-222222222222 ;;
    "ad app update "*) ;;
    "ad sp list "*) [ ! -f "$s/sp" ] || echo 33333333-3333-4333-8333-333333333333 ;;
    "ad sp create "*)
        # A new app is not on each directory server at once: one failed try.
        if [ -f "$s/sp.late" ]; then rm -f "$s/sp.late"; echo "stub az: no such app" >&2; exit 1; fi
        : >"$s/sp"; echo 33333333-3333-4333-8333-333333333333 ;;
    "ad sp update "*) ;;
    "rest --method GET "*oauth2PermissionGrants*)
        [ ! -f "$s/grant" ] || printf 'grant-id-1\t%s\n' "$(cat "$s/grant")" ;;
    "rest --method POST "*oauth2PermissionGrants*) echo "openid profile User.Read" >"$s/grant" ;;
    "rest --method PATCH "*oauth2PermissionGrants*) echo "openid profile User.Read" >"$s/grant" ;;
    "ad user show --id "*)
        case $5 in
            *@*) echo 77777777-7777-4777-8777-777777777771 ;;
            *) printf '%s\n' "$5" ;;
        esac ;;
    "rest --method GET "*appRoleAssignedTo*)
        oid=$(printf '%s' "$all" | sed -n "s/.*principalId=='\([^']*\)'.*/\1/p")
        ! grep -qx "$oid" "$s/assigned" 2>/dev/null || echo assignment-id ;;
    "rest --method POST "*appRoleAssignedTo*)
        printf '%s\n' "$all" | sed -n 's/.*"principalId":"\([^"]*\)".*/\1/p' >>"$s/assigned" ;;
    "ad app credential list "*) cat "$s/secrets" 2>/dev/null || echo 0 ;;
    "ad app credential reset "*)
        n=$(cat "$s/secrets" 2>/dev/null || echo 0); echo $((n + 1)) >"$s/secrets"
        echo stub-secret-of-the-selftest ;;
    *) echo "stub az: no answer for: $all" >&2; exit 1 ;;
esac
EOT
    cat >"$fixture/bin/bw-session-update.sh" <<'EOT'
#!/bin/sh
for word in "$@"; do printf '%s\n' "$word"; done >>"$STUB_CALLS"
echo "----" >>"$STUB_CALLS"
[ $# = 1 ] || exit 2
value=$(cat)
[ -n "$value" ] || exit 1
printf '%s' "$value" >"$STUB_STATE/credsync.$1"
echo "$1" >>"$STUB_STATE/credsync.writes"
EOT
    cat >"$fixture/bin/sleep" <<'EOT'
#!/bin/sh
# No wait; the call is recorded.
printf 'sleep\n%s\n----\n' "$*" >>"$STUB_CALLS"
EOT
    chmod 755 "$fixture/bin/az" "$fixture/bin/bw-session-update.sh" "$fixture/bin/sleep"

    tests=0
    failed=0
    # run <argument>...: one run of the copy. The status is in $status, stdout in out,
    # stderr in err, and the arguments of each stub call in the file calls.
    run() {
        : >"$fixture/calls"
        rm -f "$fixture/state/credsync.writes"
        status=0
        env -i PATH="$fixture/bin:$PATH" HOME="$fixture" STUB_CALLS="$fixture/calls" \
            STUB_STATE="$fixture/state" \
            sh "$fixture/tree/scripts/entra-memory-app.sh" "$@" >"$fixture/out" 2>"$fixture/err" || status=$?
    }
    # expect <text> <command>...: one test.
    expect() {
        tests=$((tests + 1))
        text=$1
        shift
        if ! "$@" >/dev/null 2>&1; then
            echo "selftest: FAIL - $text"
            failed=$((failed + 1))
        fi
    }
    lacks() {
        ! grep -q "$1" -e "$2" "$3"
    }
    calls_of() {
        grep -c -x -e "$1" "$fixture/calls" || :
    }
    secret=stub-secret-of-the-selftest
    name=TENANT_EC2_MEMORY_DEV_ENTRA
    no_secret_shown() {
        expect "$1: no secret on stdout" lacks -F "$secret" "$fixture/out"
        expect "$1: no secret on stderr" lacks -F "$secret" "$fixture/err"
        expect "$1: no secret in an argument of a stub call" lacks -F "$secret" "$fixture/calls"
        expect "$1: no id on stdout" lacks -E '[0-9a-f]{8}-[0-9a-f]{4}-' "$fixture/out"
    }

    # Usage errors stop before a call.
    run apply
    expect "apply with no --stage exits with 2" test "$status" = 2
    run --stage dev --days 181 apply
    expect "--days 181 exits with 2" test "$status" = 2
    run --stage dev --user "a'b" apply
    expect "a --user with a quote exits with 2" test "$status" = 2
    run --stage prod apply
    expect "a stage with no region exits with 2" test "$status" = 2
    expect "the usage errors make no call" test ! -s "$fixture/calls"
    other=77777777-7777-4777-8777-777777777773
    run --stage dev --assign-user someone@example.test
    expect "an --assign-user value that is no object id exits with 2" test "$status" = 2
    run --stage dev --assign-user "$other" apply
    expect "--assign-user with a command exits with 2" test "$status" = 2
    run --stage dev --assign-user "$other" --user someone@example.test
    expect "--assign-user with --user exits with 2" test "$status" = 2
    run --stage dev assign-user
    expect "the command assign-user with no --assign-user exits with 2" test "$status" = 2
    expect "the usage errors of --assign-user make no call" test ! -s "$fixture/calls"

    # The dry run shows each step and makes no call.
    run --dry-run --stage dev --user someone@example.test apply
    expect "the dry run exits with 0" test "$status" = 0
    expect "the dry run makes no call" test ! -s "$fixture/calls"
    expect "the dry run shows the redirect of the stage" grep -qF \
        'https://tenant-ec2-memory-dev.auth.us-east-1.amazoncognito.com/oauth2/idpresponse' "$fixture/out"
    expect "the dry run shows the pipe into credsync" grep -qF "| bw-session-update.sh ${name}_CLIENT_SECRET" "$fixture/out"
    expect "the dry run names no user" lacks -F someone@example.test "$fixture/out"
    run --dry-run --stage dev --assign-user "$other"
    expect "the dry run of --assign-user exits with 0" test "$status" = 0
    expect "the dry run of --assign-user makes no call" test ! -s "$fixture/calls"
    expect "the dry run of --assign-user names no user" lacks -F "$other" "$fixture/out"

    # --assign-user before apply: no app, so it makes nothing and assigns nobody.
    run --stage dev --assign-user "$other"
    expect "--assign-user with no app exits with 1" test "$status" = 1
    expect "--assign-user with no app makes no object" test "$(calls_of create)" = 0
    expect "--assign-user with no app assigns nobody" lacks -x POST "$fixture/calls"

    # The first run makes each object.
    run --stage dev --user someone@example.test --user 77777777-7777-4777-8777-777777777772 apply
    expect "the first run exits with 0" test "$status" = 0
    expect "the first run makes the app" test "$(calls_of create)" = 2
    expect "the app is single tenant" grep -qx AzureADMyOrg "$fixture/calls"
    expect "the app has the redirect of the stage" grep -qx \
        'https://tenant-ec2-memory-dev.auth.us-east-1.amazoncognito.com/oauth2/idpresponse' "$fixture/calls"
    expect "the app name is the name of the stage" grep -qx 'tenant-ec2 memory dev' "$fixture/calls"
    expect "the permissions hold the three scope ids of the tenant" grep -qF \
        '{"id":"55555555-5555-4555-8555-555555555551","type":"Scope"},{"id":"55555555-5555-4555-8555-555555555552","type":"Scope"},{"id":"55555555-5555-4555-8555-555555555553","type":"Scope"}' "$fixture/calls"
    expect "the admin consent is one AllPrincipals grant" grep -qF '"consentType":"AllPrincipals"' "$fixture/calls"
    expect "assignment required is set" grep -qx 'appRoleAssignmentRequired=true' "$fixture/calls"
    expect "the two users are assigned" test "$(grep -c . "$fixture/state/assigned")" = 2
    expect "one client secret is made" test "$(cat "$fixture/state/secrets")" = 1
    expect "the end date is 180 days or less ahead" python3 -I -c '
import datetime, sys
lines = open(sys.argv[1]).read().split("\n")
ends = [lines[i + 1] for i, line in enumerate(lines) if line == "--end-date"]
days = (datetime.date.fromisoformat(ends[0]) - datetime.date.today()).days
sys.exit(0 if len(ends) == 1 and 1 <= days <= 180 else 1)
' "$fixture/calls"
    expect "credsync gets the secret on stdin" test "$(cat "$fixture/state/credsync.${name}_CLIENT_SECRET")" = "$secret"
    expect "credsync gets the tenant id" test "$(cat "$fixture/state/credsync.${name}_TENANT_ID")" = 11111111-1111-4111-8111-111111111111
    expect "credsync gets the client id" test "$(cat "$fixture/state/credsync.${name}_CLIENT_ID")" = 22222222-2222-4222-8222-222222222222
    no_secret_shown "the first run"

    # A re-run reuses each object and makes no second secret.
    run --stage dev --user someone@example.test apply
    expect "the re-run exits with 0" test "$status" = 0
    expect "the re-run makes no app and no enterprise application" test "$(calls_of create)" = 0
    expect "the re-run grants no second consent" lacks -x 'POST' "$fixture/calls"
    expect "the re-run makes no client secret" test "$(cat "$fixture/state/secrets")" = 1
    expect "the re-run calls no credential reset" lacks -x reset "$fixture/calls"
    expect "the re-run writes the two ids only" test "$(grep -c . "$fixture/state/credsync.writes")" = 2
    no_secret_shown "the re-run"

    # --assign-user adds one assignment and changes nothing else.
    before=$(grep -c . "$fixture/state/assigned")
    run --stage dev --assign-user "$other"
    expect "the --assign-user run exits with 0" test "$status" = 0
    expect "the --assign-user run assigns the user" grep -qx "$other" "$fixture/state/assigned"
    expect "the --assign-user run adds one assignment" test "$(grep -c . "$fixture/state/assigned")" = $((before + 1))
    expect "the --assign-user run makes no object" test "$(calls_of create)" = 0
    expect "the --assign-user run changes no setting" lacks -x update "$fixture/calls"
    expect "the --assign-user run reads no client secret" lacks -x credential "$fixture/calls"
    expect "the --assign-user run writes nothing into credsync" test ! -f "$fixture/state/credsync.writes"
    no_secret_shown "the --assign-user run"
    run --stage dev --assign-user "$other"
    expect "a second --assign-user run exits with 0" test "$status" = 0
    expect "a second --assign-user run assigns nobody" lacks -x POST "$fixture/calls"

    # --rotate makes a second secret.
    rm -f "$fixture/state/credsync.${name}_CLIENT_SECRET"
    run --stage dev --rotate --days 90 apply
    expect "the rotate run exits with 0" test "$status" = 0
    expect "the rotate run makes one more client secret" test "$(cat "$fixture/state/secrets")" = 2
    expect "the rotate run writes the secret into credsync" test "$(cat "$fixture/state/credsync.${name}_CLIENT_SECRET")" = "$secret"
    expect "the rotate run prints how to remove the older secret" grep -qF 'az ad app credential delete' "$fixture/out"
    no_secret_shown "the rotate run"

    # A consent grant with other scopes gets the three scopes.
    echo "User.Read" >"$fixture/state/grant"
    run --stage dev apply
    expect "a grant with other scopes is changed" grep -qx PATCH "$fixture/calls"
    expect "the grant has the three scopes after the run" grep -qx 'openid profile User.Read' "$fixture/state/grant"

    # A failed secret writes nothing into credsync.
    rm -f "$fixture/state/credsync.${name}_CLIENT_SECRET"
    sed 's/echo stub-secret-of-the-selftest/exit 1/' "$fixture/bin/az" >"$fixture/az.fail"
    cp "$fixture/bin/az" "$fixture/az.good"
    cp "$fixture/az.fail" "$fixture/bin/az"
    run --stage dev --rotate apply
    cp "$fixture/az.good" "$fixture/bin/az"
    expect "a failed credential reset exits with 1" test "$status" = 1
    expect "a failed credential reset writes no secret" test ! -f "$fixture/state/credsync.${name}_CLIENT_SECRET"

    # A failed write of credsync after a new secret tells to run again with --rotate.
    # shellcheck disable=SC2016 # the text is a line of the stub, not for this shell
    sed 's/^\[ -n "\$value" \] || exit 1$/case $1 in *_CLIENT_SECRET) exit 1 ;; esac/' \
        "$fixture/bin/bw-session-update.sh" >"$fixture/credsync.fail"
    cp "$fixture/bin/bw-session-update.sh" "$fixture/credsync.good"
    cp "$fixture/credsync.fail" "$fixture/bin/bw-session-update.sh"
    run --stage dev --rotate apply
    cp "$fixture/credsync.good" "$fixture/bin/bw-session-update.sh"
    expect "a failed credsync write exits with 1" test "$status" = 1
    expect "a failed credsync write says: run again with --rotate" grep -qF 'run again with --rotate' "$fixture/err"

    # A first try of the enterprise application that fails: the next try gives a clean id.
    rm -f "$fixture/state/sp"
    : >"$fixture/state/sp.late"
    run --stage dev --user someone@example.test apply
    expect "the run with a retry exits with 0" test "$status" = 0
    expect "the run with a retry waits once" test "$(calls_of sleep)" = 1
    expect "the run with a retry makes the enterprise application" test -f "$fixture/state/sp"
    expect "no later call holds the retry message" lacks -F 'the call failed' "$fixture/calls"
    expect "the consent query holds the clean id" grep -qF \
        "clientId eq '33333333-3333-4333-8333-333333333333' and" "$fixture/calls"
    expect "assignment required goes to the clean id" python3 -I -c '
import sys
calls = open(sys.argv[1]).read().split("----\n")
ok = [c for c in calls if c.startswith("ad\nsp\nupdate\n--id\n33333333-3333-4333-8333-333333333333\n")]
sys.exit(0 if len(ok) == 1 else 1)
' "$fixture/calls"
    no_secret_shown "the run with a retry"

    if [ "$failed" -gt 0 ]; then
        echo "selftest: FAIL ($failed of $tests tests; a stub of az and of credsync, no call to Azure)"
        exit 1
    fi
    echo "selftest: pass ($tests tests; a stub of az and of credsync, no call to Azure)"
}

case $command in
    apply) apply ;;
    assign-user) assign_user ;;
    selftest) selftest ;;
esac
