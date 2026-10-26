#!/bin/bash
# Manage the account baseline stack of one region. See docs/install.md#the-account-baseline.
# Usage: scripts/aws-account-baseline.sh <command> --region REGION [options]
#   deploy         check the template and print the change set command (no AWS call);
#                  --prepare creates and shows a change set and stops;
#                  --execute NAME checks that change set and executes it
#   import         the same two steps with a change set of the type IMPORT: the existing
#                  snapshot block of the region goes into a new stack, and its state does not change;
#                  --prepare refuses a region where the state is not block-all-sharing
#   check          report the snapshot block state and the stack status (read only)
#   selftest       test the command forms with a stub of the AWS CLI (no AWS call)
# Options: --region REGION, --profile NAME, --dry-run, --prepare, --execute NAME.
# Every AWS write needs an explicit --profile and --prepare or --execute.
# A dry run of deploy or import calls no AWS API.
# Settings: EC2_PROJECT=tenant-ec2, EC2_AWS_ADMIN_PROFILE=tenant-ec2-admin.
# The stack name bootstrap-<project>-baseline-<region> is outside the stack scope of the
# provisioner (scripts/aws-provisioner.cfn.yaml), so the administrator profile runs this script.
# The S3 account public access block has no resource type; docs/install.md gives its API call.

set +x
set -euo pipefail
umask 077
export AWS_PAGER='' AWS_CLI_AUTO_PROMPT=off
ulimit -c 0
export LC_ALL=C

here=$(cd "$(dirname "$0")" && pwd)
top=$(cd "$here/.." && pwd)
project=${EC2_PROJECT:-tenant-ec2}
profile=${EC2_AWS_ADMIN_PROFILE:-tenant-ec2-admin}
command=${1:-}
[ "$#" -eq 0 ] || shift
region='' execute=''
dry=0 prepare=0 explicit_profile=0

die() { printf 'aws-account-baseline: %s\n' "$*" >&2; exit 1; }
need_value() { [ "$#" -ge 2 ] && [ -n "$2" ] && [[ $2 != --* ]] || die "missing option value"; }
while [ "$#" -gt 0 ]; do
    case $1 in
        --region) need_value "$@"; region=$2; shift ;;
        --profile) need_value "$@"; profile=$2; explicit_profile=1; shift ;;
        --prepare) prepare=1 ;;
        --execute) need_value "$@"; execute=$2; shift ;;
        --dry-run) dry=1 ;;
        *) die "unknown option" ;;
    esac
    shift
done
case $command in deploy|import|check|selftest) ;; *) die "choose a command from the script header" ;; esac

# The self-test runs this script again with a stub of the AWS CLI first in PATH.
if [ "$command" = selftest ]; then
    [ -z "$region$execute" ] && [ "$dry$prepare$explicit_profile" = 000 ] || die "selftest takes no option"
    work=$(mktemp -d) || die "cannot make the test directory"
    trap 'rm -rf -- "$work"' EXIT
    mkdir "$work/bin"
    cat >"$work/bin/aws" <<'STUB'
#!/bin/sh
# A stub of the AWS CLI: it records each call and gives a fixed reply for each mode.
printf '%s\n' "$*" >>"$STUB_LOG"
account=111122223333
case $* in
    *"sts get-caller-identity"*) printf '{"Account": "%s"}\n' "$account" ;;
    *"ec2 get-snapshot-block-public-access-state"*) printf '{"State": "%s"}\n' "$STUB_STATE" ;;
    *"cloudformation describe-stacks"*)
        if [ "$STUB_STACK" = NONE ]; then printf '{"Stacks": []}\n'
        else printf '{"Stacks": [{"StackName": "%s", "StackStatus": "%s"}]}\n' "$STUB_NAME" "$STUB_STACK"; fi ;;
    *"cloudformation describe-stack-resources"*)
        printf '{"StackResources": [{"LogicalResourceId": "SnapshotBlockPublicAccess", "ResourceType": "AWS::EC2::SnapshotBlockPublicAccess", "ResourceStatus": "%s"}]}\n' "$STUB_RESOURCE" ;;
    *"cloudformation create-change-set"*) printf '{"Id": "x"}\n' ;;
    *"cloudformation wait"*) : ;;
    *"cloudformation describe-change-set"*)
        name=$(printf '%s\n' "$*" | sed -n 's/.*--change-set-name \([^ ]*\).*/\1/p')
        printf '{"StackName": "%s", "ChangeSetName": "%s",' "$STUB_SET_STACK" "$name"
        printf ' "StackId": "arn:aws:cloudformation:%s:%s:stack/%s/abc-1",' "$STUB_REGION" "$account" "$STUB_SET_STACK"
        printf ' "ChangeSetId": "arn:aws:cloudformation:%s:%s:changeSet/%s/def-2",' "$STUB_REGION" "$account" "$name"
        printf ' "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE", "Parameters": [],'
        printf ' "Changes": [{"ResourceChange": {"Action": "%s", "LogicalResourceId": "SnapshotBlockPublicAccess",' "$STUB_ACTION"
        printf ' "ResourceType": "AWS::EC2::SnapshotBlockPublicAccess", "PhysicalResourceId": "%s"}}]}\n' "$account" ;;
    *"cloudformation execute-change-set"*) : ;;
    *) exit 3 ;;
esac
STUB
    chmod 700 "$work/bin/aws"
    self=$here/aws-account-baseline.sh
    stack=bootstrap-$project-baseline-us-east-1
    fails=0 count=0
    # expect LABEL CODE TEXT ARGS...: the exit code is CODE and the output holds TEXT.
    expect() {
        label=$1 code=$2 text=$3
        shift 3
        count=$((count + 1))
        : >"$work/log"
        set +e
        out=$(PATH="$work/bin:$PATH" STUB_LOG="$work/log" bash "$self" "$@" 2>&1)
        rc=$?
        set -e
        if [ "$rc" -ne "$code" ] || [[ $out != *"$text"* ]] || [[ $out == *111122223333* ]]; then
            printf 'selftest: FAIL %s (exit %s)\n' "$label" "$rc" >&2
            fails=$((fails + 1))
        fi
    }
    calls() { grep -c . "$work/log" || true; }
    export STUB_STATE=block-all-sharing STUB_STACK=NONE STUB_NAME=$stack STUB_RESOURCE=CREATE_COMPLETE
    export STUB_SET_STACK=$stack STUB_REGION=us-east-1 STUB_ACTION=Import
    expect "dry run of deploy" 0 "--change-set-name baseline-" deploy --region us-east-1 --dry-run
    [ "$(calls)" = 0 ] || { printf 'selftest: FAIL the dry run of deploy calls AWS\n' >&2; fails=$((fails + 1)); }
    expect "dry run of import" 0 "--change-set-name import-" import --region us-east-1
    [ "$(calls)" = 0 ] || { printf 'selftest: FAIL the dry run of import calls AWS\n' >&2; fails=$((fails + 1)); }
    expect "no region" 1 "--region" deploy
    expect "bad region" 1 "invalid region" deploy --region 'us-east-1;x'
    expect "write without profile" 1 "--profile" import --region us-east-1 --prepare
    expect "prepare and dry run" 1 "conflict" deploy --region us-east-1 --prepare --dry-run --profile p
    expect "execute with an ARN" 1 "change set name" deploy --region us-east-1 --execute arn:aws:x --profile p
    expect "check takes no write" 1 "read only" check --region us-east-1 --prepare --profile p
    expect "import prepare" 0 "Action Import" import --region us-east-1 --prepare --profile p
    grep -q -- '--change-set-type IMPORT' "$work/log" ||
        { printf 'selftest: FAIL the import step creates no IMPORT change set\n' >&2; fails=$((fails + 1)); }
    STUB_STATE=block-new-sharing expect "import of another state" 1 "block-all-sharing" import --region us-east-1 --prepare --profile p
    STUB_STACK=CREATE_COMPLETE expect "import into a stack" 1 "already exists" import --region us-east-1 --prepare --profile p
    expect "import execute" 0 "execution starts" import --region us-east-1 --execute import-1 --profile p
    grep -q 'execute-change-set' "$work/log" ||
        { printf 'selftest: FAIL the execute step does not execute\n' >&2; fails=$((fails + 1)); }
    STUB_SET_STACK=other expect "change set of another stack" 1 "does not belong" import --region us-east-1 --execute import-1 --profile p
    grep -q 'execute-change-set' "$work/log" &&
        { printf 'selftest: FAIL a change set of another stack executes\n' >&2; fails=$((fails + 1)); }
    STUB_REGION=us-west-1 expect "change set of another region" 1 "does not belong" import --region us-east-1 --execute import-1 --profile p
    STUB_ACTION=Modify expect "deploy execute of an import step" 1 "more than the snapshot block" import --region us-east-1 --execute import-1 --profile p
    STUB_ACTION=Remove expect "a removal" 1 "only" import --region us-east-1 --execute import-1 --profile p
    STUB_ACTION=Add expect "deploy prepare" 0 "Action Add" deploy --region us-east-1 --prepare --profile p
    STUB_STACK=UPDATE_COMPLETE expect "check pass" 0 "check: pass" check --region us-east-1
    STUB_STACK=NONE expect "check with no stack" 1 "check: open" check --region us-east-1
    STUB_STATE=unblocked STUB_STACK=UPDATE_COMPLETE expect "check of an open state" 1 "check: open" check --region us-east-1
    [ "$fails" -eq 0 ] || die "selftest: $fails of $count cases fail"
    printf 'baseline selftest: pass (%s cases, stub of the AWS CLI, no AWS call)\n' "$count"
    exit 0
fi

[ -n "$region" ] || die "--region is required: one deploy for each region in use"
[[ $region =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || die "invalid region"
[[ $project =~ ^[a-z][a-z0-9-]{0,30}$ ]] || die "EC2_PROJECT must be 1 to 31 lowercase letters, digits or hyphens, starting with a letter"
[[ $profile =~ ^[a-zA-Z0-9_.-]+$ ]] || die "invalid profile name"
step=''
if [ "$command" = check ]; then
    [ "$dry$prepare" = 00 ] && [ -z "$execute" ] || die "check is read only: it takes no --prepare, --execute or --dry-run"
else
    # The owner reviews the change set between the two steps; one call never does both.
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
fi
stack=bootstrap-$project-baseline-$region
template=$here/aws-account-baseline.cfn.yaml
logical=SnapshotBlockPublicAccess
type=AWS::EC2::SnapshotBlockPublicAccess

aws_cli() { aws --profile "$profile" --region "$region" --no-cli-pager --output json "$@" 2>/dev/null; }
show_command() { printf '%q ' "$@"; printf '\n'; }
show_aws() { show_command aws --profile "$profile" --region "$region" "$@"; }

# Keep AWS replies in pipes and shell memory. Errors never echo response text.
json_value() {
    python3 -c '
import json, re, sys

class Refused(Exception):
    """A refusal with a fixed text from this script, never from the reply."""

def fail():
    raise ValueError("invalid reply")

def redact(text):
    return re.sub(r"\b[0-9]{12}\b", "<account>", text)

try:
    mode, *args = sys.argv[1:]
    data = json.load(sys.stdin)
    if mode == "status":
        # The status of one stack from a list of describe-stacks; NONE when the stack is absent.
        rows = [row for row in data["Stacks"] if row["StackName"] == args[0]]
        if len(rows) > 1 or not all(re.fullmatch(r"[A-Z_]{1,64}", str(row["StackStatus"])) for row in rows):
            fail()
        print(rows[0]["StackStatus"] if rows else "NONE")
    elif mode == "state":
        state = data["State"]
        if not re.fullmatch(r"[a-z-]{1,32}", str(state)):
            fail()
        print(state)
    elif mode == "account":
        account = str(data["Account"])
        if not re.fullmatch(r"[0-9]{12}", account):
            fail()
        print(account)
    elif mode == "resource":
        # The status of the one resource of the stack; NONE when it is absent.
        logical, kind = args
        rows = [row for row in data["StackResources"] if row["LogicalResourceId"] == logical]
        if len(rows) > 1 or (rows and rows[0]["ResourceType"] != kind):
            fail()
        print(rows[0]["ResourceStatus"] if rows else "NONE")
    elif mode in ("changeset", "changeset-id"):
        # Arguments: region, stack, change set name, change set type, logical ID, resource type.
        region, stack, name, kind, logical, rtype = args
        arn = r"arn:aws[a-z-]*:cloudformation:" + re.escape(region) + r":[0-9]{12}:"
        if (data.get("StackName") != stack or data.get("ChangeSetName") != name
                or not re.fullmatch(arn + "stack/" + re.escape(stack) + r"/[A-Za-z0-9-]+", str(data.get("StackId", "")))
                or not re.fullmatch(arn + "changeSet/" + re.escape(name) + r"/[A-Za-z0-9-]+", str(data.get("ChangeSetId", "")))):
            raise Refused("the change set does not belong to the stack of this region")
        # The reply of describe-change-set has no change set type. The action of each
        # resource change tells the kind: Import for an import, Add or Modify for a deploy.
        if data.get("Parameters"):
            raise Refused("the change set has parameters; the template has none")
        changes = data.get("Changes", [])
        allowed = ("Import",) if kind == "IMPORT" else ("Add", "Modify")
        if kind == "IMPORT" and not changes:
            raise Refused("the change set imports nothing; use deploy for a create or an update")
        for change in changes:
            row = change["ResourceChange"]
            if row.get("LogicalResourceId") != logical or row.get("ResourceType") != rtype or row.get("Action") not in allowed:
                raise Refused("the change set changes more than the snapshot block; only " + " or ".join(allowed) + " of it is allowed")
        if mode == "changeset":
            print("Stack: " + stack)
            print("Change set: " + name)
            print("Type: " + kind)
            print("Status: " + str(data.get("Status", "-")))
            print("Execution status: " + str(data.get("ExecutionStatus", "-")))
            if data.get("StatusReason"):
                print("Reason: " + redact(" ".join(str(data["StatusReason"]).split())))
            print("Resource changes: " + str(len(changes)))
            for change in changes:
                row = change["ResourceChange"]
                print(redact(" ".join(k + " " + str(row.get(k) or "-") for k in ("Action", "LogicalResourceId", "ResourceType"))))
        if data.get("Status") != "CREATE_COMPLETE" or data.get("ExecutionStatus") != "AVAILABLE":
            raise Refused("the change set is not ready: it needs the status CREATE_COMPLETE and the execution status AVAILABLE")
        if mode == "changeset-id":
            print(data["ChangeSetId"])
    else:
        fail()
except Refused as refusal:
    print("aws-account-baseline: " + str(refusal), file=sys.stderr)
    sys.exit(1)
except (ValueError, KeyError, TypeError, IndexError):
    print("aws-account-baseline: cannot read the expected data", file=sys.stderr)
    sys.exit(1)
' "$@"
}

stack_status() {
    reply=$(aws_cli cloudformation describe-stacks) || die "cannot read the stack list"
    printf '%s' "$reply" | json_value status "$stack"
}
snapshot_state() {
    reply=$(aws_cli ec2 get-snapshot-block-public-access-state) || die "cannot read the snapshot block state"
    printf '%s' "$reply" | json_value state
}

case $command in
check)
    state=$(snapshot_state) || exit 1
    status=$(stack_status) || exit 1
    printf 'Region: %s\n' "$region"
    printf 'Snapshot block state: %s\n' "$state"
    printf 'Stack %s: %s\n' "$stack" "$status"
    resource=NONE
    if [ "$status" != NONE ]; then
        reply=$(aws_cli cloudformation describe-stack-resources --stack-name "$stack") || die "cannot read the stack resources"
        resource=$(printf '%s' "$reply" | json_value resource "$logical" "$type") || exit 1
    fi
    printf 'Stack resource %s: %s\n' "$logical" "$resource"
    if [ "$state" = block-all-sharing ] &&
        [[ $status =~ ^(CREATE|UPDATE|IMPORT)_COMPLETE$ ]] &&
        [[ $resource =~ ^(CREATE|UPDATE|IMPORT)_COMPLETE$ ]]; then
        printf 'check: pass\n'
        exit 0
    fi
    printf 'check: open (the state must be block-all-sharing, held by a complete stack)\n'
    exit 1
    ;;
deploy|import)
    if [ "$command" = import ]; then kind=IMPORT; else kind=CREATE_OR_UPDATE; fi
    if [ "$step" != execute ]; then
        [ -f "$template" ] || die "the template is missing"
        printf 'Template: %s\n' "${template#"$top"/}"
        if [ "$kind" = IMPORT ]; then
            create=(cloudformation create-change-set --stack-name "$stack"
                --change-set-name "import-<UTC time>" --change-set-type IMPORT
                --template-body "file://${template#"$top"/}"
                --resources-to-import "[{\"ResourceType\": \"$type\", \"LogicalResourceId\": \"$logical\", \"ResourceIdentifier\": {\"AccountId\": \"<account-id>\"}}]"
                --tags "Key=Project,Value=$project")
            show_aws "${create[@]}"
        else
            create=(cloudformation create-change-set --stack-name "$stack"
                --change-set-name "baseline-<UTC time>" --change-set-type "<CREATE or UPDATE>"
                --template-body "file://${template#"$top"/}"
                --tags "Key=Project,Value=$project")
            show_aws "${create[@]}"
        fi
        [ "$step" = prepare ] || exit 0
        status=$(stack_status) || exit 1
        if [ "$kind" = IMPORT ]; then
            # An import makes a new stack. The setting must already be the state of the template.
            [ "$status" = NONE ] || [ "$status" = REVIEW_IN_PROGRESS ] ||
                die "the stack $stack already exists in the state $status; use deploy for a change"
            state=$(snapshot_state) || exit 1
            [ "$state" = block-all-sharing ] ||
                die "the snapshot block state of $region is $state, not block-all-sharing; use deploy instead of import"
            reply=$(aws_cli sts get-caller-identity) || die "cannot read the account of the profile"
            # The account ID is the identifier of the setting. It stays in shell memory, never on screen.
            account=$(printf '%s' "$reply" | json_value account) || exit 1
            change_name=import-$(date -u +%Y%m%d-%H%M%S)
            aws_cli cloudformation create-change-set --stack-name "$stack" \
                --change-set-name "$change_name" --change-set-type IMPORT \
                --template-body "file://$template" \
                --resources-to-import "[{\"ResourceType\": \"$type\", \"LogicalResourceId\": \"$logical\", \"ResourceIdentifier\": {\"AccountId\": \"$account\"}}]" \
                --tags "Key=Project,Value=$project" >/dev/null || die "cannot prepare the change set"
            unset account
        else
            case $status in
                NONE|REVIEW_IN_PROGRESS) change_type=CREATE ;;
                CREATE_FAILED|ROLLBACK_*) die "the stack $stack is in the state $status after a failed first create; delete it with the owner word, then prepare again" ;;
                *_IN_PROGRESS) die "the stack $stack is in the state $status; wait, then prepare again" ;;
                *_FAILED) die "the stack $stack is in the state $status; repair it before a new change set" ;;
                *_COMPLETE) change_type=UPDATE ;;
                *) die "the stack $stack is in the unknown state $status" ;;
            esac
            change_name=baseline-$(date -u +%Y%m%d-%H%M%S)
            aws_cli cloudformation create-change-set --stack-name "$stack" \
                --change-set-name "$change_name" --change-set-type "$change_type" \
                --template-body "file://$template" \
                --tags "Key=Project,Value=$project" >/dev/null || die "cannot prepare the change set"
        fi
        # The status check below decides; a failed wait leaves a change set that is not ready.
        aws_cli cloudformation wait change-set-create-complete --stack-name "$stack" --change-set-name "$change_name" >/dev/null || true
    else
        # Only the named change set of this stack. Never select another or the latest set.
        change_name=$execute
    fi
    reply=$(aws_cli cloudformation describe-change-set --stack-name "$stack" --change-set-name "$change_name") || die "cannot read the change set $change_name of $stack"
    printf '%s' "$reply" | json_value changeset "$region" "$stack" "$change_name" "$kind" "$logical" "$type" || die "nothing executes"
    if [ "$step" = prepare ]; then
        printf 'The change set waits. Nothing executes in this step.\n'
        printf 'After the owner word for the execute step, run with the same settings:\n'
        show_command scripts/aws-account-baseline.sh "$command" --region "$region" --execute "$change_name" --profile "$profile"
        exit 0
    fi
    change_id=$(printf '%s' "$reply" | json_value changeset-id "$region" "$stack" "$change_name" "$kind" "$logical" "$type") || die "nothing executes"
    show_aws cloudformation execute-change-set --stack-name "$stack" --change-set-name "$change_name"
    aws_cli cloudformation execute-change-set --stack-name "$stack" --change-set-name "$change_id" >/dev/null || die "cannot execute the change set"
    unset change_id
    printf 'Change set execution starts. Read the result before the next command:\n'
    show_command scripts/aws-account-baseline.sh check --region "$region" --profile "$profile"
    ;;
esac
