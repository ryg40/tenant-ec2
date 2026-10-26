#!/bin/bash
# Manage one shared memory stack. See docs/reference/memory-template.md.
# Usage: scripts/aws-memory.sh <command> --stage dev|test|prod [options]
#   deploy         check the template and print the change set command (no AWS call);
#                  with .local/memory/<stage>.identities.yaml it uses the bound template;
#                  without it, it refuses an active person with iamUser: false;
#                  --prepare creates and shows a change set and stops;
#                  --execute NAME checks that change set and executes it;
#                  both steps refuse a change set that removes an IAM role, unless --allow-role-removal
#   identity       the Cognito identity stack of the stage, with the same steps as deploy;
#                  --prepare reads the Entra values from credsync names into a RAM file
#   outputs        print outputs with account numbers hidden; --write saves private JSON;
#                  --identity reads the identity stack instead of the memory stack
#   users          list the users from the stack outputs
#   keys           list users without keys; --apply creates and saves their keys
#   rotate         preview a rotation for --user; --apply creates and saves a second key
#   check          report key count, boundary and policy presence (read only); a role has keys=none
#   bind-check     compare a reported user pool sub with the record of --oid (read only)
#   overlay        --compare: compare the identity overlay of this checkout with its saved copy
#                  in TENANT_EC2_MEMORY_<STAGE>_IDENTITIES (read only, no AWS call)
#   client-config  render the config for --actor from the saved outputs (no AWS call);
#                  --out FILE writes it to FILE and the sign-in file <profile>.signin.json beside it
#   delete-stack   show retained resources; --apply deletes the stack; prod needs --i-know
# Options: --profile NAME, --dry-run, --apply, --prepare, --execute NAME, --write, --user NAME,
# --actor ID, --identity, --out FILE, --i-know, --oid ID, --sub ID, --allow-role-removal, --compare.
# Every AWS write needs an explicit --profile and a write flag: --prepare or --execute for deploy,
# --apply for the other commands. deploy and identity refuse --apply. A dry run of deploy calls no AWS API.
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
stage='' actor='' user='' oid='' pool_sub='' out=''
apply=0 dry=0 write=0 know=0 explicit_profile=0 prepare=0 execute='' identity_outputs=0 allow_role_removal=0 compare=0

die() { printf 'aws-memory: %s\n' "$*" >&2; exit 1; }
need_value() { [ "$#" -ge 2 ] && [ -n "$2" ] && [[ $2 != --* ]] || die "missing option value"; }
while [ "$#" -gt 0 ]; do
    case $1 in
        --stage) need_value "$@"; stage=$2; shift ;;
        --profile) need_value "$@"; profile=$2; explicit_profile=1; shift ;;
        --actor) need_value "$@"; actor=$2; shift ;;
        --user) need_value "$@"; user=$2; shift ;;
        --oid) need_value "$@"; oid=$2; shift ;;
        --sub) need_value "$@"; pool_sub=$2; shift ;;
        --apply) apply=1 ;;
        --prepare) prepare=1 ;;
        --execute) need_value "$@"; execute=$2; shift ;;
        --dry-run) dry=1 ;;
        --write) write=1 ;;
        --identity) identity_outputs=1 ;;
        --out) need_value "$@"; out=$2; shift ;;
        --i-know) know=1 ;;
        --allow-role-removal) allow_role_removal=1 ;;
        --compare) compare=1 ;;
        *) die "unknown option" ;;
    esac
    shift
done
case $command in deploy|identity|outputs|users|keys|rotate|check|bind-check|client-config|overlay|delete-stack) ;; *) die "choose a command from the script header" ;; esac
case $stage in dev|test|prod) ;; *) die "--stage must be dev, test or prod" ;; esac
[[ $project =~ ^[a-z][a-z0-9-]{0,30}$ ]] || die "EC2_PROJECT must be 1 to 31 lowercase letters, digits or hyphens, starting with a letter"
[[ $region =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || die "invalid region"
[[ $profile =~ ^[a-zA-Z0-9_.-]+$ ]] || die "invalid profile name"
[[ $admin_role =~ ^[a-zA-Z0-9_+=,.@-]{1,64}$ ]] || die "invalid administrator role: use one IAM role name without a path or wildcard"
[[ $admin_user =~ ^[a-zA-Z0-9_+=,.@-]{0,64}$ ]] || die "invalid administrator user: use an empty value or one IAM user name without a path or wildcard"
[ "$apply$dry" != 11 ] || die "--apply and --dry-run conflict"
step=''
if [ "$command" = deploy ] || [ "$command" = identity ]; then
    # The owner reviews the change set between the two steps; one call never does both.
    [ "$apply" -eq 0 ] || die "$command has two steps: --prepare creates and shows the change set, then --execute <change-set-name> executes it"
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
    die "--prepare and --execute belong to deploy and identity"
fi
[ "$apply" -eq 0 ] || [ "$explicit_profile" -eq 1 ] || die "an AWS write needs --apply and --profile"
case $command in deploy|identity|keys|rotate|delete-stack) ;; *) [ "$apply$dry" = 00 ] || die "this command has no --apply or --dry-run" ;; esac
[ "$write" -eq 0 ] || [ "$command" = outputs ] || die "--write belongs to outputs"
[ "$identity_outputs" -eq 0 ] || [ "$command" = outputs ] || die "--identity belongs to outputs"
[ -z "$out" ] || [ "$command" = client-config ] || die "--out belongs to client-config"
[ "$know" -eq 0 ] || [ "$command" = delete-stack ] || die "--i-know belongs to delete-stack"
[ -z "$user" ] || [ "$command" = rotate ] || die "--user belongs to rotate"
[ -z "$actor" ] || [ "$command" = client-config ] || die "--actor belongs to client-config"
[ "$command" != rotate ] || [ -n "$user" ] || die "rotate needs --user"
[ -z "$oid$pool_sub" ] || [ "$command" = bind-check ] || die "--oid and --sub belong to bind-check"
[ "$allow_role_removal" -eq 0 ] || { [ "$command" = deploy ] && [ -n "$step" ]; } ||
    die "--allow-role-removal belongs to deploy --prepare and deploy --execute"
[ "$compare" -eq 0 ] || [ "$command" = overlay ] || die "--compare belongs to overlay"
[ "$command" != overlay ] || [ "$compare" -eq 1 ] || die "overlay needs --compare"
if [ "$command" = bind-check ]; then
    uuid='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    [[ $oid =~ $uuid ]] || die "bind-check needs --oid: the Entra object ID of the person, a lowercase GUID"
    [[ $pool_sub =~ $uuid ]] || die "bind-check needs --sub: the user pool sub that the person reported"
fi
if [ "$command" = client-config ]; then
    [[ $actor =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] || die "client-config needs a valid --actor"
fi
stack=bootstrap-$project-memory-$stage
prefix=$project-memory-$stage-
cache=$top/.local/memory/$stage.json
identity_stack=bootstrap-$project-memory-identity-$stage
identity_cache=$top/.local/memory/$stage-identity.json

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
    allowed = {"MemoryId", "MemoryArn", "BucketName", "BoundaryArn", "PersonUsers", "PersonRoles", "SubmitUser", "WorkerUser"}
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

def identity_outputs(data):
    patterns = {"UserPoolId": r"[a-z]{2}(-[a-z]+)+-[0-9]+_[A-Za-z0-9]{1,64}", "ClientId": r"[A-Za-z0-9]{1,128}",
                "Domain": r"[a-z0-9-]{1,63}\.auth\.[a-z0-9-]+\.amazoncognito\.com",
                "IdentityPoolId": r"[a-z0-9-]+:[0-9a-f-]{36}",
                "ProviderName": r"cognito-idp\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9-]+_[A-Za-z0-9]{1,64}"}
    if not isinstance(data, dict) or set(data) != set(patterns):
        fail()
    if not all(isinstance(data[k], str) and re.fullmatch(p, data[k]) for k, p in patterns.items()):
        fail()
    return data

def checked(data, kind):
    return identity_outputs(data) if kind == "identity" else outputs(data)

def write_private(path, data):
    """Replace one 0600 file in an existing directory; a link is refused."""
    path = Path(path)
    if not path.parent.is_dir() or path.parent.is_symlink() or path.is_symlink():
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
        print(json.dumps(checked(mapped, args[0] if args else ""), sort_keys=True))
    elif mode == "show":
        print(re.sub(r"\b[0-9]{12}\b", "<account>", json.dumps(checked(data, args[0] if args else ""), indent=2, sort_keys=True)))
    elif mode == "save":
        data = checked(data, args[1] if len(args) > 1 else "")
        path = Path(args[0])
        for directory in (path.parent.parent, path.parent):
            if directory.is_symlink():
                fail()
            directory.mkdir(mode=0o700, exist_ok=True)
        write_private(path, data)
    elif mode == "client-files":
        # stdin: the client config. Arguments: the output file, the saved identity outputs or "",
        # the MemoryArn, the project and the stage. The sign-in file goes beside the output file.
        target, identity_path, memory_arn, project, stage = args
        if (not isinstance(data, dict) or set(data) != {"schemaVersion", "memoryId", "region", "profile", "actorId", "projects"}
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(data["actorId"]))
                or data["profile"] != "tenant-ec2-memory-" + stage + "-person-" + data["actorId"]):
            fail()
        signin = None
        if identity_path:
            if Path(identity_path).is_symlink():
                fail()
            with open(identity_path) as stream:
                identity = identity_outputs(json.load(stream))
            region = data["region"]
            match = re.fullmatch(r"arn:(aws[a-z-]*):bedrock-agentcore:" + re.escape(region) + r":([0-9]{12}):memory/[A-Za-z0-9_-]+", memory_arn)
            role = project + "-memory-" + stage + "-person-" + data["actorId"] + "-role"
            if (not match or len(role) > 64 or not identity["IdentityPoolId"].startswith(region + ":")
                    or not identity["Domain"].endswith(".auth." + region + ".amazoncognito.com")):
                raise Refused("the identity outputs, the memory outputs and the stage region differ")
            signin = {"schemaVersion": 1, "profile": data["profile"], "actorId": data["actorId"], "region": region,
                      "domain": identity["Domain"], "clientId": identity["ClientId"],
                      "identityPoolId": identity["IdentityPoolId"], "providerName": identity["ProviderName"],
                      "roleArn": "arn:" + match.group(1) + ":iam::" + match.group(2) + ":role/" + role}
        write_private(target, data)
        print("client config: " + target)
        if signin:
            path = str(Path(target).parent / (data["profile"] + ".signin.json"))
            write_private(path, signin)
            print("sign-in file: " + path)
        else:
            print("sign-in file: none; save the identity outputs with outputs --identity --write first")
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
    elif mode == "roles":
        data = outputs(data)
        prefix = args[0] + "person-"
        roles = data.get("PersonRoles", "")
        names = [name.strip() for name in roles.split(",")] if roles else []
        for name in names:
            actor = name[len(prefix):-len("-role")]
            if not name.startswith(prefix) or not name.endswith("-role") or len(name) > 64 \
                    or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", actor):
                fail()
        if len(names) != len(set(names)):
            fail()
        print("\n".join(sorted(names)), end="")
    elif mode == "pool":
        if len(data["Stacks"]) != 1:
            fail()
        found = [r["OutputValue"] for r in data["Stacks"][0].get("Outputs", []) if r["OutputKey"] == "UserPoolId"]
        if len(found) != 1 or not re.fullmatch(r"[\w-]+_[0-9a-zA-Z]+", found[0]):
            fail()
        print(found[0], end="")
    elif mode == "bind":
        # Only the two named attributes are read. Nothing of another record is printed.
        oid, reported = args
        rows = data["Users"]
        if not isinstance(rows, list):
            fail()
        held = []
        for row in rows:
            values = {a["Name"]: a.get("Value", "") for a in row.get("Attributes", [])}
            if values.get("custom:entra_oid") == oid:
                held.append(values.get("sub", ""))
        if len(held) > 1:
            raise Refused(f"{len(held)} user pool records hold this Entra user ID; ask the person for a fresh sign-in, then check again")
        if not held:
            print("bind-check: no match (no user pool record holds this Entra user ID)")
            sys.exit(1)
        if held[0] != reported:
            print("bind-check: no match (the reported sub differs from the record of this Entra user ID)")
            sys.exit(1)
        print("bind-check: match (one record holds this Entra user ID, and its sub equals the reported sub)")
    elif mode == "role-boundary":
        row = data["Role"]
        if row["RoleName"] != args[0] or row.get("PermissionsBoundary", {}).get("PermissionsBoundaryArn") != args[1]:
            fail()
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
        # A Key with no "=" is a NoEcho parameter: it must be present, and its masked value is not compared.
        region, stack, name, *pairs = args
        expected = dict(pair.split("=", 1) for pair in pairs if "=" in pair)
        masked = {pair for pair in pairs if "=" not in pair}
        arn = r"arn:aws[a-z-]*:cloudformation:" + re.escape(region) + r":[0-9]{12}:"
        if (data.get("StackName") != stack or data.get("ChangeSetName") != name
                or not re.fullmatch(arn + "stack/" + re.escape(stack) + r"/[A-Za-z0-9-]+", str(data.get("StackId", "")))
                or not re.fullmatch(arn + "changeSet/" + re.escape(name) + r"/[A-Za-z0-9-]+", str(data.get("ChangeSetId", "")))):
            raise Refused("the change set does not belong to the stack of this stage")
        rows = data.get("Parameters", [])
        found = {row["ParameterKey"]: row.get("ParameterValue", "") for row in rows}
        if (len(found) != len(rows) or set(found) != set(expected) | masked
                or any(found[key] != value for key, value in expected.items())):
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
    elif mode == "removed-roles":
        # One line for each IAM role that the change set removes: the logical ID and the role name.
        for change in data.get("Changes", []):
            row = change["ResourceChange"]
            if row.get("Action") == "Remove" and row.get("ResourceType") == "AWS::IAM::Role":
                print(redact(" ".join(" ".join(str(row.get(k) or "-").split()) for k in ("LogicalResourceId", "PhysicalResourceId"))))
    elif mode == "status":
        # The status of one stack from a list of describe-stacks; NONE when the stack is absent.
        rows = [row for row in data["Stacks"] if row["StackName"] == args[0]]
        if len(rows) > 1 or not all(re.fullmatch(r"[A-Z_]{1,64}", str(row["StackStatus"])) for row in rows):
            fail()
        print(rows[0]["StackStatus"] if rows else "NONE")
    elif mode == "parameters":
        # Write the parameters file of the identity stack. The values come from the environment only.
        path, project, stage = args
        names = {"EntraTenantId": "TENANT_ID", "EntraClientId": "CLIENT_ID",
                 "EntraClientSecret": "CLIENT_SECRET"}
        rows = [{"ParameterKey": "Project", "ParameterValue": project},
                {"ParameterKey": "Stage", "ParameterValue": stage}]
        for key, suffix in names.items():
            rows.append({"ParameterKey": key,
                         "ParameterValue": os.environ["TENANT_EC2_MEMORY_" + stage.upper() + "_ENTRA_" + suffix]})
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        with os.fdopen(fd, "w") as stream:
            json.dump(rows, stream)
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
load_roles() {
    local names
    names=$(printf '%s' "$output" | json_value roles "$prefix") || exit 1
    roles=()
    [ -z "$names" ] || mapfile -t roles <<<"$names"
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
        # The identity overlay gives the bound template with the person roles; it is never tracked.
        template=scripts/aws-memory-$stage.cfn.yaml
        if [ -e "$top/.local/memory/$stage.identities.yaml" ]; then
            template=.local/memory/aws-memory-$stage.cfn.yaml
        else
            # Without the overlay, a person with iamUser: false gets no user and no role.
            unbound=$(python3 -c '
import sys, yaml
people = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["people"]
print(" ".join(p["actorId"] for p in people if p["status"] == "active" and p.get("iamUser", True) is False))
' "$top/config/memory/$stage.yaml") || die "cannot read config/memory/$stage.yaml"
            [ -z "$unbound" ] || die "$unbound: iamUser: false needs the overlay .local/memory/$stage.identities.yaml; this deploy leaves no access"
        fi
        printf 'Template: %s\n' "$template"
        [ -f "$top/$template" ] || die "the generated template is missing"
        deploy=(cloudformation deploy --template-file "$template"
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
    # A removed person role ends the access of a bound person, for example after a deploy with no overlay.
    removed=$(printf '%s' "$reply" | json_value removed-roles) || die "nothing executes"
    allow=()
    if [ -n "$removed" ]; then
        if [ "$allow_role_removal" -eq 0 ]; then
            {
                printf 'The change set removes these IAM roles (logical ID, role name):\n'
                printf '%s\n' "$removed" | sed 's/^/  /'
                printf 'Check the identity overlay .local/memory/%s.identities.yaml of this checkout.\n' "$stage"
                printf 'For an intended removal, run the step again with --allow-role-removal. Otherwise delete the change set:\n'
                show_aws cloudformation delete-change-set --stack-name "$stack" --change-set-name "$change_name"
            } >&2
            die "the change set removes an IAM role; nothing executes"
        fi
        printf 'The change set removes an IAM role; --allow-role-removal permits it.\n'
        allow=(--allow-role-removal)
    fi
    if [ "$step" = prepare ]; then
        printf 'The change set waits. Nothing executes in this step.\n'
        printf 'After the owner word for the execute step, run with the same settings:\n'
        show_command scripts/aws-memory.sh deploy --stage "$stage" --execute "$change_name" --profile "$profile" "${allow[@]}"
        exit 0
    fi
    change_id=$(printf '%s' "$reply" | json_value changeset-id "$region" "$stack" "$change_name" "${parameters[@]}") || die "nothing executes"
    show_aws cloudformation execute-change-set --stack-name "$stack" --change-set-name "$change_name"
    aws_cli cloudformation execute-change-set --stack-name "$stack" --change-set-name "$change_id" >/dev/null || die "cannot execute the change set"
    unset change_id
    printf 'Change set execution starts. Read the stack status before the next command:\n'
    show_aws cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].StackStatus' --output text
    ;;
identity)
    template=$here/aws-memory-identity.cfn.yaml
    names=()
    for suffix in TENANT_ID CLIENT_ID CLIENT_SECRET; do names+=("TENANT_EC2_MEMORY_${stage^^}_ENTRA_$suffix"); done
    # The NoEcho parameters have no "=": the check needs them present and ignores their masked value.
    parameters=("Project=$project" "Stage=$stage" EntraTenantId EntraClientId EntraClientSecret)
    if [ "$step" != execute ]; then
        [ -f "$template" ] || die "the identity template is missing"
        missing=0
        for name in "${names[@]}"; do
            if [ -n "${!name:-}" ]; then
                printf 'credsync name %s: set\n' "$name"
            else
                printf 'credsync name %s: missing\n' "$name"
                missing=1
            fi
        done
        create=(cloudformation create-change-set --stack-name "$identity_stack"
            --change-set-name "identity-<UTC time>" --change-set-type "<CREATE or UPDATE>"
            --template-body "file://$template" --parameters "file:///dev/shm/<mode 0600 file>"
            --tags "Key=Project,Value=$project" "Key=Stage,Value=$stage")
        show_aws "${create[@]}"
        [ "$step" = prepare ] || exit 0
        [ "$missing" -eq 0 ] || die "load the credsync names of this stage into the shell first; nothing is created"
        # A failed first create leaves a stack that takes no change set.
        reply=$(aws_cli cloudformation describe-stacks) || die "cannot read the stack list"
        status=$(printf '%s' "$reply" | json_value status "$identity_stack") || exit 1
        case $status in
            NONE|REVIEW_IN_PROGRESS) change_type=CREATE ;;
            CREATE_FAILED|ROLLBACK_*)
                printf 'The stack %s is in the state %s after a failed first create.\n' "$identity_stack" "$status" >&2
                printf 'Delete it with the owner word for the delete, then run the prepare step again:\n' >&2
                show_aws cloudformation delete-stack --stack-name "$identity_stack" >&2
                die "nothing is created" ;;
            *_IN_PROGRESS) die "the stack $identity_stack is in the state $status; wait, then run the prepare step again" ;;
            *_FAILED) die "the stack $identity_stack is in the state $status; repair it before a new change set" ;;
            *_COMPLETE) change_type=UPDATE ;;
            *) die "the stack $identity_stack is in the unknown state $status" ;;
        esac
        [ -d /dev/shm ] && [ -w /dev/shm ] && [ ! -L /dev/shm ] || die "the RAM directory /dev/shm is missing; nothing is created"
        params_file=''
        # The file holds the Entra values. Every exit path removes it: the end, an error, a signal.
        trap '[ -z "$params_file" ] || rm -f -- "$params_file"' EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
        trap 'exit 129' HUP
        params_file=$(mktemp /dev/shm/tenant-ec2-identity-params.XXXXXX) || die "cannot make the parameters file"
        chmod 600 "$params_file"
        # The values reach the writer through its environment only, never as an argument.
        (
            for name in "${names[@]}"; do export "${name?}"; done
            printf '{}' | json_value parameters "$params_file" "$project" "$stage"
        ) || die "cannot write the parameters file; nothing is created"
        change_name=identity-$(date -u +%Y%m%d-%H%M%S)
        reply=$(aws_cli cloudformation create-change-set --stack-name "$identity_stack" \
            --change-set-name "$change_name" --change-set-type "$change_type" \
            --template-body "file://$template" --parameters "file://$params_file" \
            --tags "Key=Project,Value=$project" "Key=Stage,Value=$stage") || die "cannot prepare the change set"
        rm -f -- "$params_file"
        params_file=''
        printf 'The parameters file is removed.\n'
        aws_cli cloudformation wait change-set-create-complete --stack-name "$identity_stack" --change-set-name "$change_name" >/dev/null || true
    else
        # Only the named change set of this stack. Never select another or the latest set.
        change_name=$execute
    fi
    reply=$(aws_cli cloudformation describe-change-set --stack-name "$identity_stack" --change-set-name "$change_name") || die "cannot read the change set $change_name of $identity_stack"
    printf '%s' "$reply" | json_value changeset "$region" "$identity_stack" "$change_name" "${parameters[@]}" || die "nothing executes"
    if [ "$step" = prepare ]; then
        printf 'The change set waits. Nothing executes in this step.\n'
        printf 'After the owner word for the execute step, run with the same settings:\n'
        show_command scripts/aws-memory.sh identity --stage "$stage" --execute "$change_name" --profile "$profile"
        exit 0
    fi
    change_id=$(printf '%s' "$reply" | json_value changeset-id "$region" "$identity_stack" "$change_name" "${parameters[@]}") || die "nothing executes"
    show_aws cloudformation execute-change-set --stack-name "$identity_stack" --change-set-name "$change_name"
    aws_cli cloudformation execute-change-set --stack-name "$identity_stack" --change-set-name "$change_id" >/dev/null || die "cannot execute the change set"
    unset change_id
    printf 'Change set execution starts. Read the stack status before the next command:\n'
    show_aws cloudformation describe-stacks --stack-name "$identity_stack" --query 'Stacks[0].StackStatus' --output text
    ;;
outputs)
    if [ "$identity_outputs" -eq 1 ]; then
        reply=$(aws_cli cloudformation describe-stacks --stack-name "$identity_stack") || die "cannot read the identity stack outputs"
        output=$(printf '%s' "$reply" | json_value outputs identity) || exit 1
        [ "$write" -eq 0 ] || printf '%s' "$output" | json_value save "$identity_cache" identity
        printf '%s' "$output" | json_value show identity
        exit 0
    fi
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
    load_roles
    for name in "${roles[@]}"; do
        boundary_state=missing policy_state=missing
        if reply=$(aws_cli iam get-role --role-name "$name") &&
            printf '%s' "$reply" | json_value role-boundary "$name" "$boundary" >/dev/null 2>&1; then
            boundary_state=ok
        fi
        if reply=$(aws_cli iam list-role-policies --role-name "$name") &&
            printf '%s' "$reply" | json_value policy person >/dev/null 2>&1; then
            policy_state=ok
        fi
        printf '%s: keys=none boundary=%s policy=%s\n' "$name" "$boundary_state" "$policy_state"
        [ "$boundary_state:$policy_state" = ok:ok ] || failed=1
    done
    exit "$failed"
    ;;
bind-check)
    reply=$(aws_cli cloudformation describe-stacks --stack-name "bootstrap-$project-memory-identity-$stage") ||
        die "cannot read the outputs of the identity stack"
    pool=$(printf '%s' "$reply" | json_value pool) || exit 1
    reply=$(aws_cli cognito-idp list-users --user-pool-id "$pool") || die "cannot list the users of the user pool"
    printf '%s' "$reply" | json_value bind "$oid" "$pool_sub"
    ;;
overlay)
    # The saved copy comes from credential sync. Only the result word is printed, never an identity ID.
    python3 -c '
import os, sys, yaml
path, name, stage = sys.argv[1:]
saved = os.environ.get(name, "")
if not saved.strip():
    print("overlay " + stage + ": no saved copy")
    sys.exit(1)
try:
    with open(path, encoding="utf-8") as stream:
        local = yaml.safe_load(stream)
except FileNotFoundError:
    local = None
except Exception:
    print("aws-memory: cannot read the overlay of this checkout", file=sys.stderr)
    sys.exit(1)
try:
    copy = yaml.safe_load(saved)
except Exception:
    copy = None
same = local is not None and local == copy
print("overlay " + stage + ": " + ("equal" if same else "differs"))
sys.exit(0 if same else 1)
' "$top/.local/memory/$stage.identities.yaml" "TENANT_EC2_MEMORY_${stage^^}_IDENTITIES" "$stage"
    ;;
client-config)
    [ -f "$cache" ] && [ ! -L "$cache" ] || die "save the outputs with outputs --write first"
    memory_id=$(json_value field MemoryId <"$cache") || exit 1
    [ -f "$here/aws-memory.py" ] || die "the memory renderer is missing"
    if [ -z "$out" ]; then
        python3 "$here/aws-memory.py" client-config --stage "$stage" --actor "$actor" --memory-id "$memory_id"
        exit 0
    fi
    document=$(python3 "$here/aws-memory.py" client-config --stage "$stage" --actor "$actor" --memory-id "$memory_id") || exit 1
    signin_source=''
    if [ -e "$identity_cache" ] || [ -L "$identity_cache" ]; then
        [ -f "$identity_cache" ] && [ ! -L "$identity_cache" ] || die "the saved identity outputs are not a regular file"
        signin_source=$identity_cache
    fi
    memory_arn=$(json_value field MemoryArn <"$cache") || exit 1
    printf '%s' "$document" | json_value client-files "$out" "$signin_source" "$memory_arn" "$project" "$stage"
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
