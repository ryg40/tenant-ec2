# Move the running host under a new domain

[Host boot](host-boot.md) | [Host template](host-template.md) | [Deploy script](deploy-script.md) | [DNS records](../options/dns.md) | [DNS migration](dns-migration.md) | [Move to a later snapshot](host-snapshot-move.md)

Use this page when a deployed host moves from one domain subtree (`EC2_DOMAIN`) to another.
The stack, the Elastic IP, the data volume, the file system and the secret stay.
The public names, the edge certificate and the Entra redirect URI change.
The method is a secret write and a bootstrap run.
The stack parameter `Domain` follows when a change set permits it (branches A and B), or later (variant C).
The change set of step 2.2 and the owner select one of three paths: the replacement (branch A), the restart (branch B) or no stack update (variant C).

Two facts of the source bind each step:

- The services read `EC2_DOMAIN` from the secret, not from the stack values file. Without a secret write, the services keep the old domain.
- The expected result of a change of `UserData` alone is a restart of the instance, not a replacement. Not verified: this restart on a stack. A replacement happens when the change set replaces `Instance` for another cause. One run with the new `Domain` alone showed such a cause: a new image id.

So step [2.2](#22-the-read-only-plans) reads the change set before any stop.
Its result selects [branch A, the replacement](#31-branch-a-the-replacement), [branch B, the restart](#32-branch-b-the-restart), or [variant C, no stack update](#33-variant-c-no-stack-update): the secret write and a bootstrap repeat.

Warning: the move stops each service of the host. A source check gives no approval to change a host, a secret, a DNS record or a cloud resource.

Warning: this page gives no approval. [Section 7](#7-the-approval-table) lists each live step.

One run of variant C on one stack ran a part of this page. [Section 8](#8-not-verified) names each fact that it proved.
Not verified: each other step of this page.

## 1. Purpose and preconditions

### Where the host reads the domain

| Place | Written by | Read by |
| --- | --- | --- |
| `EC2_DOMAIN` of the private environment file | The operator. | `secret`, `deploy`, `dns`, `verify` and `scripts/entra-app.sh`. |
| `EC2_DOMAIN` of the secret `<Project>/env` | `scripts/aws-host.sh secret`. It is a name of Part 1 of `.env.example`. | The bootstrap, when it renders the host environment file. |
| `EC2_DOMAIN` of the host environment file `/srv/data/<project>/.env` | The bootstrap, at each run. | Compose (`compose.yaml`), `scripts/render-pangolin.sh` and `caddy/start.sh`. The file is on the data volume, so a new instance finds the old file until the bootstrap writes it again. |
| The stack parameter `Domain` | `deploy`. | The user data only. It writes the value as `EC2_DOMAIN` into `/etc/<project>/stack.env` at the first boot. |
| `EC2_DOMAIN` of `/etc/<project>/stack.env` | The user data, one time. | No script of the kit. The bootstrap reads the project, the region, the secret name, the operations switch and the log group only. |
| The host records | `scripts/aws-host.sh dns`. | The public resolvers. |
| The redirect URI of the Entra app | `scripts/entra-app.sh apply`. | The Entra login of the gate `ok-auth`. |
| The Pangolin files below `/srv/data/pangolin` | The bootstrap, through the renderer. | Pangolin and Traefik. |

The secret write and a bootstrap run move the services.
The stack parameter makes the stack values file of a later first boot agree with the secret.

### What a change of `Domain` does to the stack

- In the template, `Domain` occurs only in the user data of `Instance`.
- The AWS reference of `AWS::EC2::Instance` gives `UserData` the update type "Some interruptions": CloudFormation restarts an instance with an EBS root volume. It does not replace it.
- So the expected result of a change of `UserData` alone is one row `Modify Instance` with the replacement `False`. The restart keeps the root volume, the data volume attachment and the Elastic IP. Not verified: this row and this restart on a stack. One run with the new `Domain` alone gave the replacement `True`, because the image id changed too.
- The user data runs at the first boot only. After a restart, the stack values file keeps the old domain.
- The change set replaces `Instance` only when another property needs a replacement. In this template, a new image id is such a cause: `AmiParameter` names the newest Amazon Linux 2023 image.
- `deploy --replace-instance` permits a replacement. It does not request one.
- `deploy` executes a change set without a replacement, with no prompt. A plan through `deploy` is safe only when the change set replaces the instance: then the guard refuses it.

Proven on one stack: an update reads `AmiParameter` again and gives a new image id. One run of step 2.2 showed `ImageId` with `Always`. The change set of step 2.2 shows it.
Not verified: cloud-init runs no user data again after the restart of branch B.

### Branches

| Change set row of `Instance` | Branch | Result |
| --- | --- | --- |
| `Modify`, replacement `True` | A, the replacement | A new instance, a new root volume and a new first boot. The data volume and the Elastic IP move to it. |
| `Modify`, replacement `False` | B, the restart | The same instance restarts. A bootstrap repeat renders the new domain. The stack values file keeps the old domain. |
| `Modify`, replacement `True` for another cause, and the owner declines the replacement | Variant C, no stack update ([section 3.3](#33-variant-c-no-stack-update)) | No `deploy`. The services move through the secret and a bootstrap repeat. The stack parameter `Domain` keeps the old subtree until the next replacement. |
| `Conditional`, or a row of another resource | None | Stop. Ask the owner. |

On a stack whose template takes the image id from the public SSM parameter, a newer image makes every update a replacement. Then only branch A or variant C exist.

This page gives no method to cause a replacement.
Branch A applies only when the change set shows a replacement for another cause, such as a new image id.
Branch B needs a decision of the owner when the approved plan names a replacement.

### Preconditions

Each line must be true. A line that is not true stops the procedure.

1. The new parent domain answers through the default name servers of Porkbun. Otherwise, move its zone first with the [DNS migration](dns-migration.md).
2. API access is on for the new parent domain in the Porkbun account. The Porkbun pair of the private file reaches it: the limit of the key includes it.
3. The new subtree is one label below the new parent domain, as the [Porkbun provider](../options/dns.md#the-provider-porkbun) requires.
4. The label is free: the zone has no record with the label and no record below it. A wildcard record of the parent domain can answer for it; an exact record wins over the wildcard. The owner accepts the label below the wildcard.
5. A CAA record of the new parent domain, when one exists, permits Let's Encrypt.
6. The secret `<Project>/env` holds each value that a first boot needs: the three generated keys, the snapshot clone token of a private snapshot and each private kit token of the host lock. Each token is still valid.
7. The Entra app exists with the one redirect URI of the old subtree. The owner can sign in to the tenant with the Azure CLI.
8. The stack status is `CREATE_COMPLETE` or `UPDATE_COMPLETE`.
9. `EC2_ENABLE_BUCKET` and `EC2_ENABLE_OPERATIONS` are `false`, in the private file and in the stack.
10. A recent `scripts/aws-host.sh verify` passes. Its skip lines are recorded.
11. A backup of the private environment file exists, with the mode 0600, outside every clone.
12. The development machine has a clone of the snapshot that the host runs, at its tag and commit: the host clone. Each kit command of this page runs there.
13. No other operator, bootstrap or deploy changes the host during the move.

Not verified: the secret holds each value of line 6 before step 2.4. Step 2.4 reads the names.

### Who runs which step

| Actor | Steps |
| --- | --- |
| The owner | Gives one separate approval for each live step. Decides each branch and each decision row. Opens the Porkbun account and signs in to Azure. |
| The lead | Runs each read, each approved write, each kit command and each host command. |
| An agent | Can act as the lead. It does not open the Porkbun account or the Azure login. It does not answer in the place of the owner. |

### Rules for each command

- A domain, an address, an account id, a resource id and a key are host values. They stay in the private directory and the terminal. They go into no tracked file, no commit message, no tracker text and no report.
- No command of this page prints a secret value. A secret check prints names, `present`, `absent`, `same` or `different`.
- Stop at the first failed command. Stop also at each result that the step does not name.
- No step removes a record, a volume, a snapshot or a secret version without a decision of the owner.

### Host blocks without a session plugin

Each block of this page that runs in the root shell of a Systems Manager session can also run as one command of the document `AWS-RunShellScript`.
Use this form when the development machine has no session plugin.
One run of variant C on one stack ran each host block in this form, with the provisioner profile.

The provisioner policy permits it. In `scripts/aws-provisioner.cfn.yaml`, the statement `SsmSession` permits `ssm:SendCommand` on the instances of the account and on the documents `AWS-*`, in the regions of the policy.
The statement `SsmRead` permits `ssm:GetCommandInvocation`.
The `verify` command of the deploy script uses the same document.

The rules of this form:

- The document runs the lines of the block as one shell script, as root, with no terminal and no input. So `sudo -i` and the new `sh` shell are not necessary.
- A block cannot read an input with `read`. The move shell writes each input as an assignment line at the start of the block file. Each other line of the block stays. [Section 3.3](#33-variant-c-no-stack-update) shows this for the block of step E3.
- Systems Manager keeps the parameters and the output of each command in its command history. So a block holds no secret value, and its output holds states and counts only. Each host block of this page prints states and counts only.
- `StandardOutputContent` holds the first 24000 characters of the output.

Define the two functions in the move shell, after the block of step 2.1.

```sh
printf '%s\n' "$project" | grep -Eqx '[a-z][a-z0-9-]{2,30}'
ssm() {
    aws --profile "$profile" --region "$region" --no-cli-pager ssm "$@"
}
# host_run <block file> <execution timeout in seconds>: run one block on the instance of the stack.
# It prints the status, the output of the block and the count of error lines. It prints no id.
host_run() {
    instance=$(cfn describe-stack-resource --stack-name "$stack" --logical-resource-id Instance \
        --query StackResourceDetail.PhysicalResourceId --output text)
    python3 -I - "$1" "$2" >"$move_dir/command.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    commands = handle.read().splitlines()
print(json.dumps({"commands": commands, "executionTimeout": [sys.argv[2]]}))
PY
    command_id=$(ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
        --comment "$project: domain move" --timeout-seconds 600 \
        --parameters "file://$move_dir/command.json" --query Command.CommandId --output text)
    waited=0
    misses=0
    status=Pending
    while [ "$waited" -le $(($2 + 660)) ]; do
        sleep 10
        waited=$((waited + 10))
        if status=$(ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
            --query Status --output text 2>/dev/null); then
            misses=0
        else
            misses=$((misses + 1))
            [ "$misses" -lt 6 ] || break
            status=Pending
        fi
        case $status in
            Success | Failed | TimedOut | Cancelled) break ;;
        esac
    done
    echo "command: $status"
    ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
        --query StandardOutputContent --output text
    errors=$(ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
        --query StandardErrorContent --output text)
    case $errors in
        '' | None) echo "error lines: 0" ;;
        *) echo "error lines: $(printf '%s\n' "$errors" | grep -c '')" ;;
    esac
    [ "$status" = Success ]
}
```

The execution timeout is the time limit of the block on the host. The agent stops the block at that time.
`--timeout-seconds` is the time limit for the start of the command on the instance.
The invocation can need some seconds before Systems Manager knows it, so the poll accepts five failed reads in a row.
The poll ends at `Success`, `Failed`, `TimedOut` or `Cancelled`. Then the function reads `StandardOutputContent`.

Each status other than `Success` stops the procedure. The function then ends the move shell with a nonzero status.
The count of error lines must be 0. Read the error text privately with the same `get-command-invocation` call and `--query StandardErrorContent`: it can hold a host value.

The next block is a probe. It changes nothing on the host.

```sh
cat >"$move_dir/probe.sh" <<'EOF'
set +x
set -eu
echo "user: $(id -un)"
if mountpoint -q /srv/data; then
    echo "data volume: mounted"
else
    echo "data volume: not mounted"
fi
EOF
host_run "$move_dir/probe.sh" 600
```

The expected lines: `command: Success`, `user: root`, `data volume: mounted` and `error lines: 0`.

### An earlier snapshot on the host

A replacement runs the user data and the bootstrap of the snapshot that the stack names, not of this clone.
The stack parameters `SnapshotTag` and `SnapshotCommit` stay.
These differences matter when the host runs a snapshot without the generated secret:

| Part | Snapshot without the generated secret | Snapshot with the generated secret |
| --- | --- | --- |
| Generated keys at a bootstrap | The environment secret wins, then the old host file. The bootstrap never writes to Secrets Manager. | `<Project>/generated` wins, then the environment secret, then the old host file. The bootstrap writes changed keys to `<Project>/generated`. |
| A key that the bootstrap makes | It is in the host file only. The next run takes the old value of the secret again, and the gateway makes another key. Preserve the new value and write the secret before any repeat. | It goes to `<Project>/generated`. A failed write keeps the host file. |
| `secret` | It reads stdin only. | It fills empty generated names from `<Project>/generated`. |
| `verify` | It has no generated-secret check. | It requires the three names, or it skips the check when the secret does not exist. |
| `generated-status` | The command does not exist. | It prints `present` or `absent` for each generated name. |
| Template | No `GeneratedSecret` resource. | `GeneratedSecret` and one role statement for it. A `deploy` from such a clone adds them to an older stack. That change needs the boundary change first and its own approval. |
| User data, `scripts/host-userdata.sh`, the edge files, the renderer and the deploy guards | The same. | The same. |

So run `deploy` from the host clone. Its template is the template of the stack, and step 2.2 proves it.

## 2. Before the change

### 2.1 Start the move shell and prepare the environment file

Each block of sections 2 to 4 that runs on the development machine runs in this one shell.
Go to the host clone first. Then start one new shell with the command `sh`.
A failed command then ends that shell, not your login shell.
The blocks need a POSIX shell, `python3`, `dig`, `openssl` and the AWS CLI version 2.

```sh
set +x
set -eu
umask 077
host_clone=$(pwd)
test -f "$host_clone/scripts/aws-host.cfn.yaml"
printf 'Provisioner profile: '
IFS= read -r profile
printf 'AWS region: '
IFS= read -r region
printf 'Stack name: '
IFS= read -r stack
printf 'Installed project name: '
IFS= read -r project
printf 'Private environment file of the running host: '
IFS= read -r old_env
printf 'New private environment file: '
IFS= read -r move_env
printf 'Private directory: '
IFS= read -r move_dir
printf 'Old domain subtree: '
IFS= read -r old_domain
printf 'New domain subtree: '
IFS= read -r new_domain
test -f "$old_env"
test ! -e "$move_env"
test -d "$move_dir"
chmod 700 "$move_dir"
printf '%s\n' "$new_domain" | grep -Eqx '([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}'
cfn() {
    aws --profile "$profile" --region "$region" --no-cli-pager cloudformation "$@"
}
ec2() {
    aws --profile "$profile" --region "$region" --no-cli-pager ec2 "$@"
}
```

The domain test is the pattern of the template parameter `Domain`: lower case only.

The next block makes the new file from the old one. The old file stays unchanged as the way back.
`cp -p` keeps the mode 0600.

```sh
cp -p "$old_env" "$move_env"
python3 -I - "$move_env" "$new_domain" <<'PY'
import sys

path, domain = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    lines = handle.read().split("\n")
places = [i for i, line in enumerate(lines) if line.startswith("EC2_DOMAIN=")]
if len(places) != 1:
    sys.exit("EC2_DOMAIN must occur exactly one time")
lines[places[0]] = "EC2_DOMAIN=" + domain
with open(path, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines))
print("EC2_DOMAIN: changed")
PY
sh scripts/host-bootstrap.sh --check-forms "$move_env"
python3 -I - "$move_env" "$old_domain" <<'PY'
import re
import sys

old = sys.argv[2].lower()
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        found = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line.rstrip("\n"))
        if found and old in found.group(2).lower():
            print("holds the old domain:", found.group(1))
print("scan: done")
PY
```

The form check must exit with the status 0 and print nothing.
The scan prints each name whose value still holds the old subtree. The expected result is no such line.
`EC2_ACME_EMAIL` can be such a name. It is the certificate contact and needs no change while its mailbox works.
`EC2_GERBIL_BASE_ENDPOINT` holds the Elastic IP after `outputs --write`. With an empty value, the renderer uses `pangolin.` below the new subtree.
Change each other listed name only after a decision of the owner.

Approval: none. The block changes a private file only.

### 2.2 The read-only plans

The first block compares the template of the stack with the template of the host clone.

```sh
cfn get-template --stack-name "$stack" --query TemplateBody --output text >"$move_dir/stack-template.yaml"
python3 -I - "$move_dir/stack-template.yaml" scripts/aws-host.cfn.yaml <<'PY'
import sys

texts = [open(path, encoding="utf-8").read().rstrip("\n") for path in sys.argv[1:3]]
print("template: same" if texts[0] == texts[1] else "template: different")
PY
```

The result must be `template: same`. A different template stops the procedure: the clone is not the host clone.

The second block prints the new record set and the deploy inputs.

```sh
scripts/aws-host.sh --env-file "$move_env" dns --plan
scripts/aws-host.sh --dry-run --env-file "$move_env" deploy
```

`dns --plan` reads the Elastic IP from the stack. It calls no DNS provider.
Each line must name the new subtree and the stack address.
The dry run makes no AWS call. Its `Domain` must be the new subtree.
Each other parameter must equal the current stack parameter, also `SnapshotTag` and `SnapshotCommit`. Compare them privately.

The third block makes one change set with the stack template and the previous values, except `Domain`.
It reads the change set and deletes it. It changes no resource.

```sh
plan=domain-move-plan-$(date -u +%Y%m%d%H%M%S)
set --
for key in $(cfn describe-stacks --stack-name "$stack" --query 'Stacks[0].Parameters[].ParameterKey' --output text); do
    if [ "$key" = Domain ]; then
        set -- "$@" "ParameterKey=Domain,ParameterValue=$new_domain"
    else
        set -- "$@" "ParameterKey=$key,UsePreviousValue=true"
    fi
done
cfn create-change-set --stack-name "$stack" --change-set-name "$plan" --use-previous-template \
    --capabilities CAPABILITY_NAMED_IAM --parameters "$@" >/dev/null
cfn wait change-set-create-complete --stack-name "$stack" --change-set-name "$plan" ||
    cfn describe-change-set --stack-name "$stack" --change-set-name "$plan" --query StatusReason --output text
cfn describe-change-set --stack-name "$stack" --change-set-name "$plan" \
    --query 'Changes[].ResourceChange.[Action,LogicalResourceId,ResourceType,Replacement]' --output table
cfn describe-change-set --stack-name "$stack" --change-set-name "$plan" \
    --query 'Changes[?ResourceChange.LogicalResourceId==`Instance`].ResourceChange.Details[].[Target.Name,Target.RequiresRecreation,CausingEntity]' \
    --output table
cfn delete-change-set --stack-name "$stack" --change-set-name "$plan"
```

The create call prints the change set id. The block discards it, because it holds the account id.
The first table gives each changed resource and its replacement value.
The second table gives each changed property of `Instance` and its cause.

| Result | Meaning |
| --- | --- |
| `Instance` with `True`, and a property of `Instance` with `Always`, such as `ImageId` from `AmiParameter`, or with `Conditionally` from another cause | Branch A. A cause other than `Domain` replaces the instance. One run showed `ImageId` with `Always` and `UserData` with `Conditionally` from `Domain`. |
| `Instance` with `False`, `UserData` with `Conditionally` from `Domain`, and no other property with `Always` or `Conditionally` | Branch B. |
| `DataVolumeAttachment` and `ElasticIpAssociation` with `True` or `Conditional` | Expected in branch A only. They follow the new instance id. |
| Another resource, or a status reason with no change | Stop. Ask the owner. |

For `UserData`, `RequiresRecreation` `Conditionally` means that the recreation depends on the instance: an EBS root volume restarts, and an instance store root is replaced.

Record the branch and both tables in the private directory.

In branch A only, the fourth block runs the guarded plan of the deploy script.
It makes its own change set from the private file, prints it and refuses it.

```sh
if scripts/aws-host.sh --env-file "$move_env" deploy; then
    echo "deploy plan: EXECUTED"
else
    echo "deploy plan: refused"
fi
```

The expected end is the refusal `the change set is refused: it replaces the instance`, then `deploy plan: refused`.
Its table must hold the same rows as the table of the third block.

Warning: when its change set does not replace the instance, this command executes it with no prompt. The instance then restarts with the services in operation. Run it only after the third block shows branch A.

Proven on one stack: the provisioner policy permits `get-template`, `describe-stacks`, `create-change-set` with `--use-previous-template`, `describe-change-set` and `delete-change-set`. One run read their answers in the forms that the first three blocks read.
Not verified: the change set calls of `deploy` in the fourth block.

### 2.3 The host records at the new subtree

The Elastic IP stays in both branches, so the records can exist before the change.
The running edge has no route for the new names yet. A request to them gets no service.

```sh
scripts/aws-host.sh --env-file "$move_env" dns
```

The command reads each record at Porkbun first. It makes each record that is not there. It deletes no record.
A refused first read stops the command before a write. That read proves the access of the pair to the new parent domain.
The expected result is one line `done (create)` for each host record. A new try after a way back gives `no change`.

The next block asks two public resolvers. It prints counts only.

```sh
address=$(cfn describe-stacks --stack-name "$stack" \
    --query "Stacks[0].Outputs[?OutputKey=='ElasticIp'].OutputValue" --output text)
scripts/aws-host.sh --env-file "$move_env" dns --plan |
    awk '{print $1}' | grep -E '^[a-z0-9.-]+\.$' | sed 's/\.$//' >"$move_dir/host-names"
for resolver in 1.1.1.1 8.8.8.8; do
    right=0
    hosts=0
    while read -r name; do
        hosts=$((hosts + 1))
        [ "$(dig +short "@$resolver" A "$name" </dev/null)" != "$address" ] || right=$((right + 1))
    done <"$move_dir/host-names"
    echo "host records at $resolver: $right of $hosts answer the stack address"
done
```

Each line must count each host record, for example `5 of 5` with the option `pangolin` and four edge names.
The TTL of a new record is 600 seconds. Not verified: both resolvers answer with the new record within 600 seconds. Repeat the block after a wait.

The block reads the zone-file lines of `dns --plan`: `<name>. 600 IN A <address>`, one for each host record.
Not verified: after the host records exist, the wildcard of the parent domain stops to answer for the label itself and for other names below it. RFC 4592 gives this rule; Porkbun can differ.

### 2.4 The secret

`EC2_DOMAIN` is a name of the secret. The bootstrap renders it into the host environment file.
So this write is necessary in both branches.
No other name of the kit holds the subtree, except the names that the scan of step 2.1 lists.
The Porkbun pair stays the same pair. A new pair from the owner changes its two names too.

Use the same `--allow-empty` exceptions as the last approved secret write, and no other.
Before the Pangolin first setup, these are the two Pangolin exceptions:

```sh
scripts/aws-host.sh secret --allow-empty EC2_PANGOLIN_API_KEY --allow-empty EC2_PANGOLIN_ORG <"$move_env"
```

The running host does not change: the bootstrap reads the secret only when it runs.

Warning: from this write on, each bootstrap run renders the new subtree. Do not run the bootstrap on the old instance, except for the way back.

The next block reads the secret in one process. It prints names and states only.

```sh
aws --profile "$profile" --region "$region" --no-cli-pager secretsmanager get-secret-value \
    --secret-id "$project/env" --query SecretString --output text |
    python3 -I -c '
import json
import sys

new = sys.argv[1]
data = json.load(sys.stdin)
for name in ("EC2_OPENVIKING_LITELLM_API_KEY", "EC2_PI_DURABLE_LITELLM_API_KEY",
             "EC2_PI_DURABLE_OPENVIKING_API_KEY", "EC2_SNAPSHOT_CLONE_TOKEN"):
    print(name, "present" if data.get(name) else "absent")
print("EC2_DOMAIN", "same" if data.get("EC2_DOMAIN") == new else "different")
' "$new_domain"
```

Each generated name must be `present`. `EC2_DOMAIN` must be `same`.
The clone token must be `present` for a private snapshot. A public snapshot can have `absent`.
An `absent` generated name stops the procedure: a first boot then makes a new key.

Not verified: the clone token and each kit token are still valid. A read-only `git ls-remote` with the token proves it; the owner approves that read separately.

### 2.5 The stop of the services and the clean unmount

The window of the move starts here. The block runs on the old instance, in the root shell of an approved Systems Manager session.
Use `sudo -i` to enter that shell. Then start one new shell with the command `sh`.
The [form without a session plugin](#host-blocks-without-a-session-plugin) can run it too.

The first block is the form of branches A and B.

```sh
set +x
set -eu
umask 077
printf 'Installed project name: '
IFS= read -r project
printf '%s\n' "$project" | grep -Eqx '[a-z][a-z0-9-]{2,30}'
repo=/opt/$project
state=/srv/data/$project
test -d "$repo/.git"
for mount in /srv/data /srv/efs/open-knowledge /srv/efs/openviking /srv/efs/shared; do
    mountpoint -q "$mount"
done
move=$state/domain-move
install -d -m 0700 "$move"
findmnt -n -o UUID /srv/data >"$move/data-uuid"
git -C "$repo" rev-parse HEAD >"$move/old-commit"
cp -p "/etc/$project/stack.env" "$move/stack.env.before"
install -m 0600 "$state/.env" "$move/env.before"
cd "$repo"
env -i PATH="$PATH" HOME="$HOME" docker compose --env-file "$state/.env" down >"$move/stop.log" 2>&1
cd /
systemctl stop docker.socket docker.service
sync
umount /srv/data
if mountpoint -q /srv/data; then
    echo "data volume: still mounted"
    exit 1
fi
echo "data volume: unmounted"
```

The block must print `data volume: unmounted`.
The files of `domain-move` stay on the data volume. They prove its identity after the change.
`env.before` is a copy of the host environment file before the move, with the mode 0600. It is the way back of that file.
It holds secret values. Remove it after the owner closes the way back.
`down` removes the containers and the networks. It removes no data directory: do not add `--volumes`.
The stop of Docker also stops each writer of the three access points.
`umount` stops with `target is busy` when a process holds the volume. Then find it with `fuser -vm /srv/data` and stop.

The reasons: the AWS reference of `AWS::EC2::VolumeAttachment` asks for an unmount before a detach.
CloudFormation usually creates a replacement resource before it deletes the old one.
A clean unmount keeps the file system consistent in each branch and for the snapshot of step 2.6.
In branch B, the restart also stops the services. The stop and unmount still give a consistent snapshot.

The second block is the form of [variant C](#33-variant-c-no-stack-update). It stops the services only.

```sh
set +x
set -eu
umask 077
printf 'Installed project name: '
IFS= read -r project
printf '%s\n' "$project" | grep -Eqx '[a-z][a-z0-9-]{2,30}'
repo=/opt/$project
state=/srv/data/$project
test -d "$repo/.git"
for mount in /srv/data /srv/efs/open-knowledge /srv/efs/openviking /srv/efs/shared; do
    mountpoint -q "$mount"
done
move=$state/domain-move
install -d -m 0700 "$move"
findmnt -n -o UUID /srv/data >"$move/data-uuid"
git -C "$repo" rev-parse HEAD >"$move/old-commit"
cp -p "/etc/$project/stack.env" "$move/stack.env.before"
install -m 0600 "$state/.env" "$move/env.before"
env -i PATH="$PATH" HOME="$HOME" docker compose --project-directory "$repo" --env-file "$state/.env" down \
    >"$move/stop.log" 2>&1
sync
echo "services: stopped"
```

The block must print `services: stopped`.
It does not stop Docker and it does not unmount the data volume.
The reasons: in variant C the volume stays attached, and the bootstrap repeat of section 3.3 needs it mounted.
The bootstrap starts the services through Docker.
A stopped file set and `sync` give a consistent snapshot in step 2.6.
`--project-directory` gives Compose the clone of the host, so the block needs no `cd`.
One run on one stack proved this form through `send-command`.
Not verified: no process other than the file set writes to the data volume during the snapshot.

### 2.6 The snapshot of the data volume

The block runs in the move shell. It saves the ids in the private directory.

```sh
cfn describe-stack-resource --stack-name "$stack" --logical-resource-id DataVolume \
    --query StackResourceDetail.PhysicalResourceId --output text >"$move_dir/data-volume"
cfn describe-stack-resource --stack-name "$stack" --logical-resource-id Instance \
    --query StackResourceDetail.PhysicalResourceId --output text >"$move_dir/old-instance"
ec2 create-snapshot --volume-id "$(cat "$move_dir/data-volume")" \
    --description "$project data volume before the domain move" \
    --tag-specifications "ResourceType=snapshot,Tags=[{Key=Project,Value=$project},{Key=Name,Value=$project-domain-move}]" \
    --query SnapshotId --output text >"$move_dir/snapshot"
ec2 wait snapshot-completed --snapshot-ids "$(cat "$move_dir/snapshot")"
echo "snapshot: completed"
```

The waiter can end before a large first snapshot completes. Then run the `wait` line again.
The snapshot has no tag `Backup`, so the daily snapshot policy never deletes it.

Warning: the snapshot is billed until its removal. Its removal is a decision row of section 5.

The snapshot holds the data volume only. The kit has no backup of the file system.
The move changes no file of the access points while the same images run.

### 2.7 The Entra redirect URI

The app keeps one redirect URI. The next command replaces it with the URI of the new subtree.
The login at the old names then ends. So this step runs in the window, after the stop of the services.

1. The owner signs in: `az login --tenant <tenant id>`. The script uses the tenant of that session.
2. Print the commands with no call:

```sh
scripts/entra-app.sh --dry-run --env-file "$move_env" apply
```

3. Run the same command without `--dry-run`. Give the same `--name`, `--group` and `--assign-group` options as the first run.

```sh
scripts/entra-app.sh --env-file "$move_env" apply
```

4. Compare the three ids that the command writes with the old file. The block prints `same` or `different` for each id.

```sh
python3 -I - "$old_env" "$move_env" <<'PY'
import re
import sys

def read(path):
    values = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            found = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line.rstrip("\n"))
            if found:
                values[found.group(1)] = found.group(2)
    return values

old, new = read(sys.argv[1]), read(sys.argv[2])
for name in ("EC2_OK_ENTRA_TENANT_ID", "EC2_OK_OIDC_CLIENT_ID", "EC2_OK_ENTRA_GROUP_ID"):
    print(name, "same" if old.get(name) == new.get(name) else "different")
PY
```

5. The owner signs out: `az logout`.

Each id must be `same`. A `different` id stops the procedure: the command found another app or group.
The [Entra page](../entra.md#make-the-app-registration-with-the-script) gives the behavior of a repeat run.

### 2.8 The detach of the data volume (branch A only)

Run this block only in branch A, after `snapshot: completed`.

```sh
ec2 detach-volume --volume-id "$(cat "$move_dir/data-volume")" \
    --instance-id "$(cat "$move_dir/old-instance")" >/dev/null
ec2 wait volume-available --volume-ids "$(cat "$move_dir/data-volume")"
echo "data volume: available"
```

The reason: the template names the risk that the new attachment fails while the old instance holds the volume.
CloudFormation usually creates the new attachment before it deletes the old one. Only a free volume can attach then.
This answers the attachment-order gap of the [deploy guards](deploy-script.md#snapshot-move-guard-proposals) for this move. It is not a guard of the script.

Warning: never add `--force`. A forced detach can damage the file system. Step 2.5 must print `data volume: unmounted` first.

In branch B, do not detach. The restart keeps the attachment, and no step of the stack attaches the volume again.

Not in variant C: the instance stays, and the bootstrap repeat of section 3.3 needs the volume attached and mounted.

## 3. The change

### 3.1 Branch A: the replacement

Run the deploy from the host clone, in the move shell:

```sh
scripts/aws-host.sh --env-file "$move_env" deploy --replace-instance
```

The command makes a new change set, prints it and executes it with no prompt.
The printed table must equal the table of step 2.2. When it differs, the execution has started: follow the checks of section 4 and the recovery below.

What CloudFormation does:

| Resource | Action |
| --- | --- |
| `Instance` | A new instance from the current image, with the new user data. It has a new root volume. |
| `DataVolumeAttachment` | A new attachment of the same volume to the new instance, at `/dev/xvdf`. The volume is free after step 2.8. |
| `ElasticIpAssociation` | A new association: the same Elastic IP moves to the new instance. The host records stay correct. |
| The cleanup | CloudFormation deletes the old attachment and the old association. It terminates the old instance. Its root volume is deleted. |
| `DataVolume`, `FileSystem`, the access points, `ElasticIp`, the secret | No change. |

The time:

- The stack update ends when the cleanup ends. The script waits for it. Not verified: the time of the stack update.
- The record of the first deploy of this kit gives about one hour for its first boot, with the kit builds. A replacement builds each image again: the images were on the old root volume. Not verified: the time of the first boot of a replacement.
- The subnet gives no public address at the start. The user data of `Instance` in `scripts/aws-host.cfn.yaml` waits up to 180 seconds for the Elastic IP before the package install and the clone.
- `scripts/host-userdata.sh` waits up to 600 seconds for the data volume. It formats no volume with a file system: it mounts the old one.

The proof of the first boot runs on the new instance, in the root shell of an approved Systems Manager session.
The id of the new instance is the physical id of `Instance` in the stack.

```sh
cloud-init status --wait
echo "bootstrap done lines: $(grep -c '^host-bootstrap: done$' /var/log/cloud-init-output.log || true)"
echo "bootstrap error lines: $(grep -c '^host-bootstrap: error:' /var/log/cloud-init-output.log || true)"
```

The result must be one done line and no error line.
Read the log privately. It can hold a name of the domain.
The [host boot page](host-boot.md#steps) gives the nine steps of the bootstrap.

Not verified: the delete of the old attachment after the detach of step 2.8. CloudFormation can report a failed delete in the cleanup. Check 2 of section 4 proves the attachment after `UPDATE_COMPLETE`.

#### When the update fails

The script ends with `the update of the stack ... failed`. CloudFormation rolls back.
The rollback deletes the new instance and each new attachment and association. The stack names the old ones again.

| Part | Expected state after the rollback |
| --- | --- |
| Stack | `UPDATE_ROLLBACK_COMPLETE`. A later `deploy` accepts this status. |
| Old instance | Runs, without the data volume. Docker is stopped. |
| Data volume | Not attached: step 2.8 detached it, and the rollback deletes a new attachment. |
| Elastic IP | Not verified: it can end with no association. Then the old instance has no public address and no Systems Manager connection. |
| Secret | It holds the new subtree. |

Not verified: each state of this table.

The next block reads the state. It prints states only.

```sh
echo "stack: $(cfn describe-stacks --stack-name "$stack" --query 'Stacks[0].StackStatus' --output text)"
instance=$(cfn describe-stack-resource --stack-name "$stack" --logical-resource-id Instance \
    --query StackResourceDetail.PhysicalResourceId --output text)
[ "$instance" = "$(cat "$move_dir/old-instance")" ] && echo "instance: old" || echo "instance: other"
volume=$(cat "$move_dir/data-volume")
echo "data volume: $(ec2 describe-volumes --volume-ids "$volume" --query 'Volumes[0].State' --output text)"
address=$(cfn describe-stacks --stack-name "$stack" \
    --query "Stacks[0].Outputs[?OutputKey=='ElasticIp'].OutputValue" --output text)
holder=$(ec2 describe-addresses --public-ips "$address" --query 'Addresses[0].InstanceId' --output text)
[ "$holder" = "$instance" ] && echo "address: on the stack instance" || echo "address: not on the stack instance"
```

The recovery, after the approval of the owner:

1. With `address: not on the stack instance`, associate the address with the old instance.

```sh
allocation=$(ec2 describe-addresses --public-ips "$address" --query 'Addresses[0].AllocationId' --output text)
ec2 associate-address --allocation-id "$allocation" --instance-id "$instance" >/dev/null
```

2. With `data volume: available`, attach it at the device of the template.

```sh
ec2 attach-volume --volume-id "$volume" --instance-id "$instance" --device /dev/xvdf >/dev/null
ec2 wait volume-in-use --volume-ids "$volume"
```

3. In the root shell of a Systems Manager session on the old instance, mount the volume from its line in `/etc/fstab`: `mount /srv/data`.
4. Decide with the owner: a new try of section 3, or the [way back](#6-the-way-back). A new try starts again at step 2.2.

`UPDATE_ROLLBACK_FAILED` stops the procedure. The deploy script refuses that status.
A `continue-update-rollback` call is a separate step with its own approval.

Not verified: the state after a rollback, and the association of the Elastic IP after it.

### 3.2 Branch B: the restart

Start this branch only after a decision of the owner.

```sh
scripts/aws-host.sh --env-file "$move_env" deploy
```

The guard does not refuse a change set without a replacement, so the script executes it with no prompt.
CloudFormation stops the instance, writes the new user data and starts it again.
The root volume, the data volume attachment and the Elastic IP stay.
After the start, `/etc/fstab` mounts the data volume and Docker starts. No container runs: step 2.5 removed them.

Do not add `--replace-instance` in this branch.
A newer image between step 2.2 and this command can add a replacement. Without the option, the guard refuses that change set while the volume is still attached.

Then run the bootstrap repeat in the root shell of an approved Systems Manager session.
Use the block of [step E3 of the DNS migration](dns-migration.md#e3-repeat-the-bootstrap).
The block must print `bootstrap: done`.
The bootstrap renders the new subtree from the secret and writes the Pangolin files again.
Then it starts each service of the file set.

The stack values file keeps the old subtree. No script of the kit reads that line.
Record this difference with the move.

Not verified: the restart of an instance through a user data update, and the mount and Docker start after it on this host.

### 3.3 Variant C: no stack update

Start this variant only after a decision of the owner.
It applies when the change set of step 2.2 replaces `Instance` for another cause, and the owner declines the replacement.

Variant C runs no `deploy`. The stack, the instance, its root volume, the data volume attachment and the Elastic IP stay.
The order: the steps 2.1 to 2.4, the variant C form of step 2.5, the steps 2.6 and 2.7, the bootstrap repeat below, then the checks of section 4.
Do not run step 2.8, section 3.1 or section 3.2.

The bootstrap repeat runs on the running instance, with the block of [step E3 of the DNS migration](dns-migration.md#e3-repeat-the-bootstrap).
In the [form without a session plugin](#host-blocks-without-a-session-plugin), the move shell writes the block file.
The assignment of `project` replaces the `printf` and `read` pair. Each other line is the line of step E3.

```sh
{
    printf 'project=%s\n' "$project"
    cat <<'EOF'
set +x
set -eu
umask 077
printf '%s\n' "$project" | grep -Eqx '[a-z][a-z0-9-]{2,30}'
repo=/opt/$project
stack_env=/etc/$project/stack.env
state=/srv/data/$project
test -d "$repo/.git"
test -f "$stack_env"
bootstrap_log=$(mktemp "$state/dns-bootstrap.XXXXXX")
if sh "$repo/scripts/host-bootstrap.sh" --stack-env "$stack_env" >"$bootstrap_log" 2>&1; then
    echo "bootstrap: done"
else
    echo "bootstrap: FAILED"
    exit 1
fi
EOF
} >"$move_dir/bootstrap.sh"
host_run "$move_dir/bootstrap.sh" 3600
```

The expected lines: `command: Success`, `bootstrap: done` and `error lines: 0`.
The bootstrap renders the new subtree from the secret, writes the Pangolin files again and starts each service of the file set.
The execution timeout of 3600 seconds is the default of the document. Not verified: the time of a bootstrap repeat.

Then run the checks of section 4. The expected lines of variant C:

| Check | Expected lines |
| --- | --- |
| 1 | The stack status before the move, `parameter Domain: other` and `instance: same`. |
| 3 | `data volume: the old file system`, `snapshot commit: same`, `stack values: old subtree` and `host environment file: new subtree`. |
| 4 | `made lines: 0`, `kept gateway keys: 2`, `kept OpenViking key: 1` and `secret gaps: 0`, on a snapshot without the generated secret with the option `pangolin`. |
| 5 | One certificate with `wildcard name: yes`, no ACME error line, and `Verify return code: 0 (ok)`. |
| 6 | `verify: pass`, with the skip lines from before the move. |

Check 4 reads the log file of the bootstrap repeat: the newest file with a name that starts with `dns-bootstrap.` in the data directory of the project.
In the form without a session plugin, its assignment line is `log=$(ls -t "/srv/data/$project"/dns-bootstrap.* | head -n 1)`.
One run on one stack gave each expected line of checks 3 to 6. The store showed the certificate within two minutes of the bootstrap.

#### The drift

The stack parameter `Domain` and the stack values file keep the old subtree. The secret and the host environment file hold the new subtree.
This difference is the drift of variant C.
No script of the kit reads the line `EC2_DOMAIN` of the stack values file, and `verify` does not compare it.
Record the drift on the ticket, with no host value.

The next replacement ends the drift: a `deploy` with the new private file sets the new `Domain`, and the user data of the new first boot writes the new subtree.
Such a replacement is the [move to a later snapshot](host-snapshot-move.md) or any `deploy --replace-instance`.
Run it with the new private file. With the old file, the stack parameter and the stack values file keep the old subtree.

## 4. The checks after the boot

1. Read the stack in the move shell. It prints states only.

```sh
echo "stack: $(cfn describe-stacks --stack-name "$stack" --query 'Stacks[0].StackStatus' --output text)"
domain=$(cfn describe-stacks --stack-name "$stack" \
    --query "Stacks[0].Parameters[?ParameterKey=='Domain'].ParameterValue" --output text)
[ "$domain" = "$new_domain" ] && echo "parameter Domain: new" || echo "parameter Domain: other"
instance=$(cfn describe-stack-resource --stack-name "$stack" --logical-resource-id Instance \
    --query StackResourceDetail.PhysicalResourceId --output text)
[ "$instance" = "$(cat "$move_dir/old-instance")" ] && echo "instance: same" || echo "instance: new"
```

The stack must be `UPDATE_COMPLETE` and the parameter `new`. The instance is `new` in branch A and `same` in branch B.
In variant C the stack is unchanged: its status stays the status before the move, `parameter Domain: other` is expected, and the instance is `same`.

2. Read the attachment and the address. It proves the cleanup of branch A left the new attachment in place.

```sh
volume=$(cat "$move_dir/data-volume")
holder=$(ec2 describe-volumes --volume-ids "$volume" --query 'Volumes[0].Attachments[0].InstanceId' --output text)
[ "$holder" = "$instance" ] && echo "data volume: on the stack instance" || echo "data volume: not on the stack instance"
address=$(cfn describe-stacks --stack-name "$stack" \
    --query "Stacks[0].Outputs[?OutputKey=='ElasticIp'].OutputValue" --output text)
holder=$(ec2 describe-addresses --public-ips "$address" --query 'Addresses[0].InstanceId' --output text)
[ "$holder" = "$instance" ] && echo "address: on the stack instance" || echo "address: not on the stack instance"
```

Both lines must say `on the stack instance`.

3. Check the host in the root shell of an approved Systems Manager session, in a new `sh` shell.

```sh
set +x
set -eu
printf 'Installed project name: '
IFS= read -r project
printf 'New domain subtree: '
IFS= read -r new_domain
state=/srv/data/$project
for mount in /srv/data /srv/efs/open-knowledge /srv/efs/openviking /srv/efs/shared; do
    mountpoint -q "$mount"
done
[ "$(findmnt -n -o UUID /srv/data)" = "$(cat "$state/domain-move/data-uuid")" ] &&
    echo "data volume: the old file system" || echo "data volume: OTHER"
[ "$(git -C "/opt/$project" rev-parse HEAD)" = "$(cat "$state/domain-move/old-commit")" ] &&
    echo "snapshot commit: same" || echo "snapshot commit: other"
line=$(grep '^EC2_DOMAIN=' "/etc/$project/stack.env" || true)
[ "$line" = "EC2_DOMAIN=$new_domain" ] && echo "stack values: new subtree" || echo "stack values: old subtree"
python3 -I - "$state/.env" "$new_domain" <<'PY'
import sys

new = sys.argv[2]
with open(sys.argv[1], encoding="utf-8") as handle:
    lines = [line.rstrip("\n") for line in handle if line.startswith("EC2_DOMAIN=")]
values = [line.split("=", 1)[1].strip("'") for line in lines]
print("host environment file:", "new subtree" if values == [new] else "OTHER")
PY
```

The expected lines: `the old file system`, `snapshot commit: same` and `host environment file: new subtree`.
The stack values line says `new subtree` in branch A and `old subtree` in branch B and in variant C.
The file `data-uuid` came from the old instance, so the first line proves the identity of the volume.

4. Check that the services kept the preserved keys. In branch A the log is `/var/log/cloud-init-output.log`; in branch B it is the log file of the bootstrap repeat.

```sh
printf 'Bootstrap log: '
IFS= read -r log
echo "made lines: $(grep -c '^host-bootstrap: made the' "$log" || true)"
echo "kept gateway keys: $(grep -c 'has a value: the virtual key of' "$log" || true)"
echo "kept OpenViking key: $(grep -c 'has a value: the OpenViking key of the agent is there' "$log" || true)"
echo "secret gaps: $(grep -c 'the secret has no value for:' "$log" || true)"
```

These lines are the lines of a snapshot without the generated secret. The expected counts are 0, 2, 1 and 0.
A `made` line means a new key. On such a snapshot, the new value is in the host file only. Preserve it through the [credential procedure](../credentials.md) and write the secret before any bootstrap repeat.
On a snapshot with the generated secret, use `scripts/aws-host.sh generated-status` from its clone.

5. Check the certificate with both blocks of [step E4 of the DNS migration](dns-migration.md#e4-check-the-certificate).
The store must show one certificate with `wildcard name: yes`. The TLS check must print `Verify return code: 0 (ok)` for a host record of the new subtree.
A restart of Traefik is a separate approval, as step E4 says.

6. Run `verify` from the host clone in the move shell.

```sh
scripts/aws-host.sh --env-file "$move_env" verify
```

The command must end with `verify: pass`. Its skip lines must equal the skip lines recorded before the move.

7. Run the resolver block of step 2.3 again. Each line must count each host record.

One run of variant C on one stack gave the expected lines of checks 3 to 6.
Not verified: each check of this section in branch A and in branch B, and checks 1, 2 and 7 in variant C.

## 5. The steps after

Each step has its own approval.

1. The [first setup of Pangolin](../options/pangolin.md#first-setup-after-boot), at `https://pangolin.<new EC2_DOMAIN>/auth/initial-setup` from the administrator range. Then the secret write with the new key and organization, and a bootstrap repeat.
2. The [first login of Copilot](../options/copilot.md#first-login), with that option. Token files of an earlier login stay on the data volume.
3. The Entra login test: [section 8 of the install guide](../install.md#8-first-login-and-security-review) and the [first login](../entra.md#the-first-login). An allowed user enters; a user outside the group is refused.
4. The decision on the old records, below.
5. The decision on the snapshot of step 2.6, after the acceptance: keep it for some days, or delete it.
6. The record on the ticket: the branch, the times, the result of each check and each skip line. No host value. Test the text with the deny check of the tracker before the post.

| Decision | Choices | Effect |
| --- | --- | --- |
| The host records of the old subtree, at each provider that holds them | Keep them until the acceptance; delete them after it; keep them longer for the way back. | A kept record still points to the Elastic IP. The edge has no route for the old names. |
| API access of the old parent domain at Porkbun | Off when no record of it needs the pair; on for the way back. | The pair on the host then reaches the new parent domain only. |
| The snapshot of step 2.6 | Keep; delete. | A kept snapshot is billed. |

Warning: delete the old records before any release of the Elastic IP. A record that points to a released address sends its traffic to the next holder of that address.

After the acceptance, the new private file is the file of the host. Keep the old file until the owner closes the way back.

Not verified: Pangolin takes the new base domain of its configuration when its database is from a first start under the old subtree.

## 6. The way back

Warning: a restore of the snapshot loses each write after step 2.5. Get the approval of the owner before a restore.

| Stop after | Way back |
| --- | --- |
| Step 2.1 or 2.2 | Nothing changed at a provider. Delete the new private file. |
| Step 2.3 | The new records do no harm. Delete them in the Porkbun account after a decision, or keep them for a new try. |
| Step 2.4 | Write the secret from the old file, with the same exceptions: `scripts/aws-host.sh secret ... <"$old_env"`. |
| Step 2.5 | Write the old secret first. Then, on the old instance, run `mount /srv/data` and `systemctl start docker`, and run the bootstrap repeat. |
| Step 2.5, variant C form | Write the old secret first. Then run the bootstrap repeat of section 3.3: the volume is still mounted and Docker runs. |
| Step 2.7 | Run `scripts/entra-app.sh --env-file "$old_env" apply` with the same options. The owner signs in first. |
| Step 2.8 | Attach the volume to the old instance, as in step 2 of the recovery of section 3.1. Then continue as for step 2.5. |
| A failed update | The recovery of [section 3.1](#when-the-update-fails). Then continue as for step 2.5. |
| Section 3.3, after the bootstrap repeat | Write the secret from the old file. Run `scripts/entra-app.sh --env-file "$old_env" apply` with the same options; the owner signs in first. Then run the bootstrap repeat of section 3.3 again. The stack parameter never changed, so no stack step follows. |
| A completed change | The domain way back below. |
| Damaged data | The snapshot of step 2.6, through a separate reviewed procedure. |

The domain way back after a completed change:

1. Write the secret from the old file. The old subtree is then the value that the bootstrap renders.
2. Run `scripts/entra-app.sh --env-file "$old_env" apply`. The old redirect URI returns.
3. Confirm that the old host records still answer the stack address at two public resolvers.
4. Run the bootstrap repeat on the running instance, with the block of [step E3 of the DNS migration](dns-migration.md#e3-repeat-the-bootstrap). The host environment file then holds the old subtree.
5. Run the checks of section 4 with the old subtree and the old private file.
6. Bring the stack parameter back with sections 2.2 and 3 and the old private file. The change set selects the branch again: a restart, or a second replacement when it replaces `Instance`.

In variant C, skip step 6: the stack parameter never changed.

A restore of the snapshot makes a new volume with a new id.
The stack and the stack values file name the old id, and a later first boot looks for that id.
So a restore needs its own reviewed procedure.

Not verified: each way back of this section.

## 7. The approval table

Each row is one separate question to the owner. An approval covers the one row only.
Step 2.1 and the dry run of step 2.2 make no call and need no row.

| Step | Actor | Exact command or form | What it changes | Way back |
| --- | --- | --- | --- | --- |
| Precondition 2 | Owner | In the Porkbun account, the owner turns API access on for the new parent domain. When the key pair has a list of domains, the owner adds the new parent domain to it. Not verified: the list of the key pair. | API access of the new parent domain, and the list of the key pair when it has one. | The toggle off. |
| Section 1, probe | Lead | The probe block of section 1 through `ssm send-command`. | Nothing. | Not necessary. |
| 2.2, template | Lead | `cloudformation get-template` in the form of step 2.2. | Nothing. | Not necessary. |
| 2.2, record plan | Lead | `scripts/aws-host.sh dns --plan`. | Nothing. It reads the stack. | Not necessary. |
| 2.2, change set | Lead | The third block of step 2.2: `create-change-set --use-previous-template`, `describe-change-set`, `delete-change-set`. | One change set until its deletion. No resource. | `delete-change-set` when the block stops early. |
| 2.2, deploy plan | Lead | `scripts/aws-host.sh deploy` with no option. Branch A only. | One change set until the guard deletes it. It executes the change set when it has no replacement. | Section 3.2 for an execution. |
| 2.3, records | Lead | `scripts/aws-host.sh dns` with the new private file. | New A records below the new subtree at Porkbun. | The removal of each new record in the Porkbun account. |
| 2.3, resolvers | Lead | `dig` to two public resolvers. | Nothing. | Not necessary. |
| 2.4, write | Lead | `scripts/aws-host.sh secret` with the new private file and the same exceptions. | A new version of the secret. | The same command with the old file. |
| 2.4, read | Lead | `secretsmanager get-secret-value` in the form of step 2.4. It prints states only. | Nothing. | Not necessary. |
| 2.5 | Lead | The first block of step 2.5 in a Systems Manager session. Branches A and B. Not in variant C. | It stops each service and unmounts the data volume. | Step 2.5 of section 6. |
| 2.5, variant C | Lead | The second block of step 2.5, through `ssm send-command` in the form of section 1, or in a Systems Manager session. Variant C only. | It stops each service. The data volume stays mounted and Docker runs. | The row "Step 2.5, variant C form" of section 6. |
| 2.6 | Lead | `ec2 create-snapshot` in the form of step 2.6. | One billed snapshot. | The removal of the snapshot after a decision. |
| 2.7 | Owner and lead | `az login`, then `scripts/entra-app.sh apply` with the new private file. | The one redirect URI of the app. | The same command with the old file. |
| 2.8 | Lead | `ec2 detach-volume` with no `--force`. Branch A only. Not in variant C. | The data volume is free. | `ec2 attach-volume` to the old instance. |
| 3.1 | Lead | `scripts/aws-host.sh deploy --replace-instance`. Not in variant C. | A new instance; the old one and its root volume end. The volume and the address move. | Section 6, the domain way back. |
| 3.1, recovery | Lead | `ec2 associate-address`, `ec2 attach-volume`, `mount /srv/data`. Only after a failed update. Not in variant C. | The address and the volume return to the old instance. | Not necessary. |
| 3.2 | Lead | `scripts/aws-host.sh deploy` with no option. Branch B only. Not in variant C. | A restart of the instance with new user data. | Section 6, the domain way back. |
| 3.2, bootstrap | Lead | The block of step E3 of the DNS migration. Branch B only. Not in variant C. | The host environment file and the services. | Section 6, the domain way back. |
| 3.3, bootstrap | Lead | The block of step E3 of the DNS migration, through `ssm send-command` in the form of section 3.3. Variant C only. | The host environment file and the services. | The row "Section 3.3, after the bootstrap repeat" of section 6. |
| 4 | Lead | The read blocks of section 4 and `scripts/aws-host.sh verify`. | Nothing. | Not necessary. |
| 4, Traefik | Lead | `docker compose restart traefik` in the form of step E4 of the DNS migration. Only with no certificate. | Each public route stops during the restart. Not verified: the length of the stop. | Not necessary. |
| 5 | Owner and lead | The Pangolin first setup, the Copilot login and the Entra login test, each on its page. | Each as its page says. | Each as its page says. |
| 5, decisions | Owner | The removal of an old record, the API access of the old parent domain, the removal of the snapshot. | Each as the decision table says. | A new record or a new access setting. A deleted snapshot has no way back. |
| 6 | Lead | The commands of section 6, one approval for each row used. | Each as section 6 says. | The forward step again. |

## 8. Not verified

One run of variant C on one stack ran the change set of step 2.2, the steps 2.4 to 2.7, the bootstrap repeat of section 3.3 and the checks of section 4.
No replacement and no restart ran.

Proven on one stack, by one run of variant C:

- An update reads `AmiParameter` again, and a new image id replaces the instance. The change set of step 2.2 for a new `Domain` alone showed `Instance` with the replacement `True`, `ImageId` with `Always`, and `UserData` with `Conditionally` from `Domain`.
- The provisioner profile runs a host block through `ssm send-command` with `AWS-RunShellScript`, and `get-command-invocation` reads its output.
- The bootstrap repeat with the new subtree on a running host printed `bootstrap: done`. It kept the keys: made 0, kept gateway keys 2, kept OpenViking key 1, secret gaps 0.
- The data volume stayed mounted with the old file system through the variant C form of step 2.5 and the bootstrap repeat.
- The edge got the wildcard certificate of the new subtree at the first try. The store showed one certificate with `wildcard name: yes` within two minutes of the bootstrap. The log had no ACME error line, and the TLS check printed `Verify return code: 0 (ok)`.
- `verify` passed with the skip lines from before the move.

The stack:

- One run with the new `Domain` alone gave `Modify Instance` with the replacement `True`, because the image id changed. The replacement `False` is expected only when no other property changes. Not verified: the replacement `False` on a stack.
- Not verified: the replacement creates the new attachment before it deletes the old one, on this stack.
- Not verified: the attachment succeeds after the detach of step 2.8, and the cleanup leaves it in place.
- Not verified: the order of the new instance start and the move of the Elastic IP. The user data of the template waits up to 180 seconds for the address.
- Not verified: the rollback of a failed update, and the association of the Elastic IP after it.
- Not verified: the restart of branch B, and that cloud-init runs no user data again after it.
- Proven on one stack: the provisioner policy permits `get-template`, `describe-stacks`, `describe-stack-resource`, `create-change-set` with `--use-previous-template`, `describe-change-set` and `delete-change-set`.
- Proven on one stack: the provisioner policy permits `create-snapshot`, `describe-snapshots` through `wait snapshot-completed`, `ssm send-command`, `ssm get-command-invocation` and `secretsmanager get-secret-value`.
- One run showed the forms of the answers of these calls that the blocks read.
- Not verified: the provisioner policy permits the calls that no run made, and their answers have the forms that the blocks read. These calls are `deploy` in both forms, `detach-volume`, `attach-volume`, `associate-address` and `continue-update-rollback`.
- Not verified: the Traefik restart of check 5. One run needed no restart.

The host:

- Not verified: the bootstrap of a snapshot without the generated secret keeps the three keys at a new first boot. The source says so: the secret values win, the gateway keeps a key that its database knows, and the OpenViking key is not checked again. The key lines of check 4 must prove it.
- Not verified: the database of the gateway still knows each key after the move. A key that it does not know gets a new value, with the loop of section 1.
- Not verified: the first block of step 2.5 with its stop and its unmount, and the mount blocks on the host.
- Not verified: the first boot after a replacement mounts the old file system and formats nothing.
- Not verified: Pangolin takes the new base domain when its database is from a start under the old subtree.
- Not verified: the edge gets the wildcard certificate of the new subtree at the first try in branch A or in branch B.
- Not verified: the stored data of OpenKnowledge and OpenViking holds no absolute address of the old subtree.
- Not verified: variant C on a stack with the generated secret.

DNS and the accounts:

- Proven by one run: the first read of `scripts/aws-host.sh dns` reached the new parent domain with the Porkbun pair of the host, before the record writes. Precondition 2 stays: the owner turns API access on first.
- Not verified: the wildcard record of the new parent domain and its answers after the host records exist.
- Not verified: a CAA record of the new parent domain.
- Not verified: the clone token and each kit token of the secret are still valid.
- Proven by one run: a repeat run of `scripts/entra-app.sh apply` with the new private file updated the one redirect URI and kept the three ids.
- Not verified: the Entra login test of section 5.

The limits of [the DNS migration](dns-migration.md#8-not-verified), of [the DNS scripts](../options/dns.md#not-verified), of [the Pangolin edge](../options/pangolin.md) and of [the host boot](host-boot.md#known-limits) apply too.
