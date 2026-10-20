#!/bin/bash
# Manage one shared memory stack. See docs/reference/memory-template.md.
# Usage: scripts/aws-memory.sh <command> --stage dev|test|prod [options]
#   deploy         check the template and print the change set command (no AWS call);
#                  --prepare creates and shows a change set and stops;
#                  --execute NAME checks that change set and executes it
#   outputs        print outputs with account numbers hidden; --write saves private JSON
#   users          list the users from the stack outputs
#   keys           list users without keys; --apply creates and saves their keys
#   rotate         preview a rotation for --user; --apply creates and saves a second key
#   check          report key count, boundary and policy presence (read only)
#   client-config  render the config for --actor from the saved outputs (no AWS call)
#   delete-stack   show retained resources; --apply deletes the stack; prod needs --i-know
# Options: --profile NAME, --dry-run, --apply, --prepare, --execute NAME, --write, --user NAME,
# --actor ID, --i-know.
# Every AWS write needs an explicit --profile and a write flag: --prepare or --execute for deploy,
# --apply for the other commands. deploy refuses --apply. A dry run of deploy calls no AWS API.
# Settings: EC2_PROJECT=tenant-ec2, EC2_AWS_REGION=us-east-1,
# EC2_AWS_ADMIN_PROFILE=tenant-ec2-admin, EC2_MEMORY_ADMINISTRATOR_ROLE=Administrator,
# EC2_MEMORY_ADMINISTRATOR_USER= (optional IAM user name; require MFA separately).

set +x
set -euo pipefail
umask 077
export AWS_PAGER='' AWS_CLI_AUTO_PROMPT=off
ulimit -c 0
export LC_ALL=C

here=$(cd "$(dirname "$0")" && pwd)
top=$(cd "$here/.." && pwd)
project=${EC2_PROJECT:-tenant-ec2}
region=${EC2_AWS_REGION:-us-east-1}
profile=${EC2_AWS_ADMIN_PROFILE:-tenant-ec2-admin}
admin_role=${EC2_MEMORY_ADMINISTRATOR_ROLE:-Administrator}
admin_user=${EC2_MEMORY_ADMINISTRATOR_USER:-}
command=${1:-}
[ "$#" -eq 0 ] || shift
stage='' actor='' user=''
apply=0 dry=0 write=0 know=0 explicit_profile=0 prepare=0 execute=''

die() { printf 'aws-memory: %s\n' "$*" >&2; exit 1; }
need_value() { [ "$#" -ge 2 ] && [ -n "$2" ] && [[ $2 != --* ]] || die "missing option value"; }
while [ "$#" -gt 0 ]; do
    case $1 in
        --stage) need_value "$@"; stage=$2; shift ;;
        --profile) need_value "$@"; profile=$2; explicit_profile=1; shift ;;
        --actor) need_value "$@"; actor=$2; shift ;;
        --user) need_value "$@"; user=$2; shift ;;
        --apply) apply=1 ;;
        --prepare) prepare=1 ;;
        --execute) need_value "$@"; execute=$2; shift ;;
        --dry-run) dry=1 ;;
        --write) write=1 ;;
        --i-know) know=1 ;;
        *) die "unknown option" ;;
    esac
    shift
done
case $command in deploy|outputs|users|keys|rotate|check|client-config|delete-stack) ;; *) die "choose a command from the script header" ;; esac
case $stage in dev|test|prod) ;; *) die "--stage must be dev, test or prod" ;; esac
[[ $project =~ ^[a-z][a-z0-9-]{0,30}$ ]] || die "EC2_PROJECT must be 1 to 31 lowercase letters, digits or hyphens, starting with a letter"
[[ $region =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || die "invalid region"
[[ $profile =~ ^[a-zA-Z0-9_.-]+$ ]] || die "invalid profile name"
[[ $admin_role =~ ^[a-zA-Z0-9_+=,.@-]{1,64}$ ]] || die "invalid administrator role: use one IAM role name without a path or wildcard"
[[ $admin_user =~ ^[a-zA-Z0-9_+=,.@-]{0,64}$ ]] || die "invalid administrator user: use an empty value or one IAM user name without a path or wildcard"
[ "$apply$dry" != 11 ] || die "--apply and --dry-run conflict"
step=''
if [ "$command" = deploy ]; then
    # The owner reviews the change set between the two steps; one call never does both.
    [ "$apply" -eq 0 ] || die "deploy has two steps: --prepare creates and shows the change set, then --execute <change-set-name> executes it"
    [ "$prepare" -eq 0 ] || step=prepare
    if [ -n "$execute" ]; then
        [ -z "$step" ] || die "--prepare and --execute conflict"
        step=execute
        [[ $execute =~ ^[A-Za-z][A-Za-z0-9-]{0,127}$ ]] || die "--execute needs the change set name that --prepare printed, not an ARN"
    fi
    if [ -n "$step" ]; then
        [ "$dry" -eq 0 ] || die "--$step and --dry-run conflict"
        [ "$explicit_profile" -eq 1 ] || die "an AWS write needs --$step and --profile"
    fi
elif [ "$prepare" -eq 1 ] || [ -n "$execute" ]; then
    die "--prepare and --execute belong to deploy"
fi
[ "$apply" -eq 0 ] || [ "$explicit_profile" -eq 1 ] || die "an AWS write needs --apply and --profile"
case $command in deploy|keys|rotate|delete-stack) ;; *) [ "$apply$dry" = 00 ] || die "this command has no --apply or --dry-run" ;; esac
[ "$write" -eq 0 ] || [ "$command" = outputs ] || die "--write belongs to outputs"
[ "$know" -eq 0 ] || [ "$command" = delete-stack ] || die "--i-know belongs to delete-stack"
[ -z "$user" ] || [ "$command" = rotate ] || die "--user belongs to rotate"
[ -z "$actor" ] || [ "$command" = client-config ] || die "--actor belongs to client-config"
[ "$command" != rotate ] || [ -n "$user" ] || die "rotate needs --user"
if [ "$command" = client-config ]; then
    [[ $actor =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] || die "client-config needs a valid --actor"
fi
stack=bootstrap-$project-memory-$stage
prefix=$project-memory-$stage-
cache=$top/.local/memory/$stage.json

aws_cli() { aws --profile "$profile" --region "$region" --no-cli-pager --output json "$@" 2>/dev/null; }
show_command() { printf '%q ' "$@"; printf '\n'; }
show_aws() { show_command aws --profile "$profile" --region "$region" "$@"; }

# Keep AWS replies in pipes and shell memory. Errors never echo response text.
json_value() {
    python3 -c '
import json, os, re, sys, tempfile
from pathlib import Path

class Refused(Exception):
    """A refusal with a fixed text from this script, never from the reply."""

def fail():
    raise ValueError("invalid reply")

def redact(text):
    return re.sub(r"\b[0-9]{12}\b", "<account>", text)

def outputs(data):
    allowed = {"MemoryId", "MemoryArn", "BucketName", "BoundaryArn", "PersonUsers", "SubmitUser", "WorkerUser"}
    if not isinstance(data, dict) or not set(data) <= allowed:
        fail()
    if not all(isinstance(v, str) for v in data.values()):
        fail()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", data.get("MemoryId", "")):
        fail()
    for key in ("MemoryArn", "BoundaryArn"):
        if not data.get(key, "").startswith("arn:"):
            fail()
    return data

try:
    mode, *args = sys.argv[1:]
    data = json.load(sys.stdin)
    if mode == "outputs":
        if len(data["Stacks"]) != 1:
            fail()
        rows = data["Stacks"][0].get("Outputs", [])
        mapped = {r["OutputKey"]: r["OutputValue"] for r in rows}
        if len(rows) != len(mapped):
            fail()
        print(json.dumps(outputs(mapped), sort_keys=True))
    elif mode == "show":
        print(re.sub(r"\b[0-9]{12}\b", "<account>", json.dumps(outputs(data), indent=2, sort_keys=True)))
    elif mode == "save":
        data = outputs(data)
        path = Path(args[0])
        for directory in (path.parent.parent, path.parent):
            if directory.is_symlink():
                fail()
            directory.mkdir(mode=0o700, exist_ok=True)
        if path.is_symlink():
            fail()
        fd, name = tempfile.mkstemp(prefix=".outputs-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    elif mode == "users":
        data = outputs(data)
        prefix = args[0]
        names = []
        people = data.get("PersonUsers", "")
        for name in people.split(",") if people else []:
            name = name.strip()
            if not name.startswith(prefix + "person-") or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name[len(prefix + "person-"):]):
                fail()
            names.append(name)
        for key, suffix in (("SubmitUser", "submit"), ("WorkerUser", "worker")):
            name = data.get(key, "")
            if name:
                if name != prefix + suffix:
                    fail()
                names.append(name)
        if len(names) != len(set(names)) or any(len(n) > 64 for n in names):
            fail()
        print("\n".join(sorted(names)), end="")
    elif mode == "field":
        print(outputs(data).get(args[0], ""))
    elif mode == "keys":
        rows = data["AccessKeyMetadata"]
        if not isinstance(rows, list):
            fail()
        names = []
        for row in rows:
            if row["UserName"] != args[0] or not re.fullmatch(r"[A-Z0-9]{16,128}", row["AccessKeyId"]):
                fail()
            if row["Status"] not in ("Active", "Inactive"):
                fail()
            names.append(row["AccessKeyId"])
        if len(set(names)) != len(names):
            fail()
        print("\n".join(names), end="")
    elif mode == "pair":
        row = data["AccessKey"]
        if row["UserName"] != args[0] or row["Status"] != "Active":
            fail()
        if not re.fullmatch(r"[A-Z0-9]{16,128}", row["AccessKeyId"]):
            fail()
        if not re.fullmatch(r"[A-Za-z0-9/+=]{40}", row["SecretAccessKey"]):
            fail()
        print(row["AccessKeyId"] + "\t" + row["SecretAccessKey"], end="")
    elif mode == "boundary":
        row = data["User"]
        if row["UserName"] != args[0] or row.get("PermissionsBoundary", {}).get("PermissionsBoundaryArn") != args[1]:
            fail()
    elif mode == "policy":
        if args[0] == "person":
            if "agentcore-memory" not in data["PolicyNames"]:
                fail()
        else:
            # A service has a customer managed policy, never an AWS managed one.
            account = args[1].split(":")[4]
            if not any(p["PolicyArn"].split(":")[4] == account for p in data["AttachedPolicies"]):
                fail()
    elif mode in ("changeset", "changeset-id"):
        # Arguments: region, stack, change set name, then Key=Value for each expected parameter.
        region, stack, name, *pairs = args
        expected = dict(pair.split("=", 1) for pair in pairs)
        arn = r"arn:aws[a-z-]*:cloudformation:" + re.escape(region) + r":[0-9]{12}:"
        if (data.get("StackName") != stack or data.get("ChangeSetName") != name
                or not re.fullmatch(arn + "stack/" + re.escape(stack) + r"/[A-Za-z0-9-]+", str(data.get("StackId", "")))
                or not re.fullmatch(arn + "changeSet/" + re.escape(name) + r"/[A-Za-z0-9-]+", str(data.get("ChangeSetId", "")))):
            raise Refused("the change set does not belong to the stack of this stage")
        rows = data.get("Parameters", [])
        found = {row["ParameterKey"]: row.get("ParameterValue", "") for row in rows}
        if len(found) != len(rows) or found != expected:
            raise Refused("the change set parameters differ from the current settings of this stage")
        if mode == "changeset":
            print("Stack: " + stack)
            print("Change set: " + name)
            print("Status: " + str(data.get("Status", "-")))
            print("Execution status: " + str(data.get("ExecutionStatus", "-")))
            if data.get("StatusReason"):
                print("Reason: " + redact(" ".join(str(data["StatusReason"]).split())))
            changes = data.get("Changes", [])
            print("Resource changes: " + str(len(changes)))
            for change in changes:
                row = change["ResourceChange"]
                print(redact(" ".join(str(row.get(k, "-")) for k in ("Action", "LogicalResourceId", "ResourceType", "Replacement"))))
        if data.get("Status") != "CREATE_COMPLETE" or data.get("ExecutionStatus") != "AVAILABLE":
            raise Refused("the change set is not ready: it needs the status CREATE_COMPLETE and the execution status AVAILABLE")
        if mode == "changeset-id":
            print(data["ChangeSetId"])
    else:
        fail()
except Refused as refusal:
    print("aws-memory: " + str(refusal), file=sys.stderr)
    sys.exit(1)
except (ValueError, KeyError, TypeError, IndexError, OSError):
    print("aws-memory: cannot read or store the expected data", file=sys.stderr)
    sys.exit(1)
' "$@"
}

load_outputs() {
    local reply
    reply=$(aws_cli cloudformation describe-stacks --stack-name "$stack") || die "cannot read stack outputs"
    output=$(printf '%s' "$reply" | json_value outputs) || exit 1
}
load_users() {
    local names
    load_outputs
    names=$(printf '%s' "$output" | json_value users "$prefix") || exit 1
    users=()
    [ -z "$names" ] || mapfile -t users <<<"$names"
}
load_keys() {
    local reply names
    reply=$(aws_cli iam list-access-keys --user-name "$1") || die "cannot read key metadata for $1"
    names=$(printf '%s' "$reply" | json_value keys "$1") || exit 1
    keys=()
    [ -z "$names" ] || mapfile -t keys <<<"$names"
}
create_key() {
    local name=$1 pair id secret suffix store
    pair=$(aws_cli iam create-access-key --user-name "$name") || die "key creation failed; check $name before retrying"
    pair=$(printf '%s' "$pair" | json_value pair "$name") || die "invalid key response; check $name before retrying"
    IFS=$'\t' read -r id secret <<<"$pair"
    suffix=${name#"$prefix"}
    suffix=${suffix//-/_}
    store=TENANT_EC2_MEMORY_${stage^^}_${suffix^^}
    if ! { printf '%s' "$id" | bw-session-update.sh "${store}_KEY_ID" >/dev/null 2>&1 &&
           printf '%s' "$secret" | bw-session-update.sh "${store}_SECRET" >/dev/null 2>&1; }; then
        if aws_cli iam delete-access-key --user-name "$name" --access-key-id "$id" >/dev/null; then
            die "sync failed for $name; the new key is deleted; repair the saved pair before retrying"
        fi
        die "sync failed for $name; the new key may remain; inspect IAM and repair the saved pair"
    fi
    unset pair id secret
}

case $command in
deploy)
    parameters=("Project=$project" "Stage=$stage" "AdministratorRole=$admin_role" "AdministratorUser=$admin_user")
    if [ "$step" != execute ]; then
        [ -f "$here/aws-memory.py" ] || die "the memory renderer is missing"
        python3 "$here/aws-memory.py" check --stage "$stage"
        [ -f "$here/aws-memory-$stage.cfn.yaml" ] || die "the generated template is missing"
        deploy=(cloudformation deploy --template-file "scripts/aws-memory-$stage.cfn.yaml"
            --stack-name "$stack" --capabilities CAPABILITY_NAMED_IAM --no-execute-changeset
            --no-fail-on-empty-changeset --tags "Project=$project" "Stage=$stage"
            --parameter-overrides "${parameters[@]}")
        show_aws "${deploy[@]}"
        [ "$step" = prepare ] || exit 0
        cd "$top"
        reply=$(aws_cli "${deploy[@]}") || die "cannot prepare the change set"
        if [[ $reply == *'No changes to deploy.'* ]]; then
            printf 'No changes to deploy. No change set waits for execution.\n'
            exit 0
        fi
        # The CLI returns the exact new change set in its review command. Never select the latest set.
        change_set=$(printf '%s' "$reply" | python3 -c '
import re, sys
matches = re.findall(r"--change-set-name (arn:[A-Za-z0-9-]+:cloudformation:[a-z0-9-]+:[0-9]{12}:changeSet/[A-Za-z0-9-]+/[A-Za-z0-9-]+)", sys.stdin.read())
if len(matches) != 1:
    sys.exit(1)
print(matches[0])
') || die "cannot identify the new change set; nothing executes"
        change_name=${change_set#*:changeSet/}
        change_name=${change_name%%/*}
        # The deploy command waits for the creation too. The status check below decides.
        aws_cli cloudformation wait change-set-create-complete --stack-name "$stack" --change-set-name "$change_set" >/dev/null || true
    else
        # Only the named change set of this stack. Never select another or the latest set.
        change_set=$execute change_name=$execute
    fi
    reply=$(aws_cli cloudformation describe-change-set --stack-name "$stack" --change-set-name "$change_set") || die "cannot read the change set $change_name of $stack"
    printf '%s' "$reply" | json_value changeset "$region" "$stack" "$change_name" "${parameters[@]}" || die "nothing executes"
    if [ "$step" = prepare ]; then
        printf 'The change set waits. Nothing executes in this step.\n'
        printf 'After the owner word for the execute step, run with the same settings:\n'
        show_command scripts/aws-memory.sh deploy --stage "$stage" --execute "$change_name" --profile "$profile"
        exit 0
    fi
    change_id=$(printf '%s' "$reply" | json_value changeset-id "$region" "$stack" "$change_name" "${parameters[@]}") || die "nothing executes"
    show_aws cloudformation execute-change-set --stack-name "$stack" --change-set-name "$change_name"
    aws_cli cloudformation execute-change-set --stack-name "$stack" --change-set-name "$change_id" >/dev/null || die "cannot execute the change set"
    unset change_id
    printf 'Change set execution starts. Read the stack status before the next command:\n'
    show_aws cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].StackStatus' --output text
    ;;
outputs)
    load_outputs
    if [ "$write" -eq 1 ]; then
        printf '%s' "$output" | json_value save "$cache"
    fi
    printf '%s' "$output" | json_value show
    ;;
users)
    load_users
    for name in "${users[@]}"; do printf '%s\n' "$name"; done
    ;;
keys|rotate)
    if [ "$apply" -eq 1 ]; then
        command -v bw-session-update.sh >/dev/null || die "bw-session-update.sh is missing; no key is created"
    fi
    load_users
    if [ "$command" = rotate ]; then
        found=0
        for name in "${users[@]}"; do [ "$name" != "$user" ] || found=1; done
        [ "$found" -eq 1 ] || die "--user is not a user of this stack"
        load_keys "$user"
        [ "${#keys[@]}" -eq 1 ] || die "rotation needs exactly one key"
        old=${keys[0]}
        printf '%s\n' "$user"
        [ "$apply" -eq 1 ] || exit 0
        create_key "$user"
        show_aws iam delete-access-key --user-name "$user" --access-key-id "$old"
    else
        pending=()
        for name in "${users[@]}"; do
            load_keys "$name"
            [ "${#keys[@]}" -le 1 ] || die "$name has more than one key; finish rotation first"
            [ "${#keys[@]}" -ne 0 ] || pending+=("$name")
        done
        for name in "${pending[@]}"; do
            printf '%s\n' "$name"
            [ "$apply" -eq 0 ] || create_key "$name"
        done
    fi
    ;;
check)
    load_users
    boundary=$(printf '%s' "$output" | json_value field BoundaryArn)
    failed=0
    for name in "${users[@]}"; do
        load_keys "$name"
        count=${#keys[@]}
        boundary_state=missing policy_state=missing
        if reply=$(aws_cli iam get-user --user-name "$name") &&
            printf '%s' "$reply" | json_value boundary "$name" "$boundary" >/dev/null 2>&1; then
            boundary_state=ok
        fi
        if [[ $name == "$prefix"person-* ]]; then
            operation=list-user-policies kind=person
        else
            operation=list-attached-user-policies kind=service
        fi
        if reply=$(aws_cli iam "$operation" --user-name "$name") &&
            printf '%s' "$reply" | json_value policy "$kind" "$boundary" >/dev/null 2>&1; then
            policy_state=ok
        fi
        printf '%s: keys=%s boundary=%s policy=%s\n' "$name" "$count" "$boundary_state" "$policy_state"
        [ "$count:$boundary_state:$policy_state" = 1:ok:ok ] || failed=1
    done
    exit "$failed"
    ;;
client-config)
    [ -f "$cache" ] && [ ! -L "$cache" ] || die "save the outputs with outputs --write first"
    memory_id=$(json_value field MemoryId <"$cache") || exit 1
    [ -f "$here/aws-memory.py" ] || die "the memory renderer is missing"
    python3 "$here/aws-memory.py" client-config --stage "$stage" --actor "$actor" --memory-id "$memory_id"
    ;;
delete-stack)
    [ "$stage" != prod ] || [ "$know" -eq 1 ] || die "prod deletion needs --i-know"
    load_outputs
    printf 'Retained resources:\nMemory: '
    printf '%s' "$output" | json_value field MemoryId
    bucket=$(printf '%s' "$output" | json_value field BucketName)
    [ -z "$bucket" ] || printf 'Bucket: %s\n' "$bucket"
    printf 'Remove user access keys separately before stack deletion. No memory record or bucket object is deleted here.\n'
    show_aws cloudformation delete-stack --stack-name "$stack"
    [ "$apply" -eq 0 ] || aws_cli cloudformation delete-stack --stack-name "$stack" >/dev/null || die "cannot delete the stack"
    ;;
esac
