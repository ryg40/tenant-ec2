# Move a running host to a later snapshot

[Host boot](host-boot.md) | [Host template](host-template.md) | [Deploy script](deploy-script.md)

Use an approved Systems Manager (SSM) shell to replace the snapshot clone, then repeat the bootstrap.
This is the method that the kit supports as written, through its existing bootstrap entry point.
It needs no CloudFormation update for a snapshot-only change.

Not verified: a bootstrap repeat with a changed snapshot commit. A complete move and the checks below must prove it.

An instance replacement is the second method.
The kit does not provide a complete replacement procedure or a switch that forces replacement.
Do not treat an update of the snapshot parameters as an installation of the new snapshot on an existing instance.

Warning: both methods interrupt services. A source check gives no approval to change a host, a secret or a cloud resource.

## Source evidence

The line numbers refer to the scripts that accompany this page.

| Claim | Source |
| --- | --- |
| The bootstrap supports a repeat from the installed clone. | [host-bootstrap.sh](../../scripts/host-bootstrap.sh#L6), line 6: its command interface; line 1783: its final result. |
| The bootstrap uses the directory that holds its script. | [host-bootstrap.sh](../../scripts/host-bootstrap.sh#L863), line 863: `repo` comes from the script path. |
| First boot checks the snapshot commit before any script from that clone runs. | [aws-host.cfn.yaml](../../scripts/aws-host.cfn.yaml#L773), line 773: the comparison with `SnapshotCommit`. |
| First boot calls the host preparation script. | [aws-host.cfn.yaml](../../scripts/aws-host.cfn.yaml#L777), line 777: the `exec` command. |
| The bootstrap requires the four durable mounts. | [host-bootstrap.sh](../../scripts/host-bootstrap.sh#L1041), line 1041: the mount loop; line 958: the check before Compose. |
| Only the three generated keys use the generated secret and the old environment fallback. | [host-bootstrap.sh](../../scripts/host-bootstrap.sh), `render`: the loop over `MADE`. |
| A replacement reuses one separately attached data volume, not a copy for each instance. | [aws-host.cfn.yaml](../../scripts/aws-host.cfn.yaml#L453), line 456: the attachment names `Instance`; line 457: it names `DataVolume`. |
| The replacement flag permits a replacement; it does not request one. | [aws-host.sh](../../scripts/aws-host.sh#L1715), line 1715: the guard tests `replace`; line 1860: the flag sets it. |

Not verified: cloud-init runs user data only at first boot on the installed host.
The template states that contract at line 722; it has no update-time command to change an existing clone.
A live update must compare the host commit, not infer it from stack status.

## Before either method

1. Obtain approval for the method, downtime, secret writes and recovery plan. Keep each approval separate from a source check.
2. Review the old and new snapshot files on the development machine. Record both approved tags and their exact commits privately.
3. Compare the kit pins, Compose files, environment names and host preparation scripts. Keep the same project, options, storage, network and public names for this move.
4. Stop if the move needs IAM, storage, operating system or option changes. Those changes need their own reviewed procedure.
5. Check the existing installation with its current verifier. Record each failure and skip before the move.
6. Prepare application-consistent backups of EBS and all three EFS access points. Test the restore before proceeding.
7. Preserve the old private environment file and both Secrets Manager versions through the approved credential procedure. Never print generated keys through SSM.
8. Check each kit's data migration and downgrade rules. Stop if the old binaries cannot read the resulting data and no tested restore exists.
9. Prepare the new private environment file from the old values and the new sample. Set the approved snapshot tag and commit.
10. Add any newly required clone credential through the [private source procedure](../options/home-lab.md#private-kit-source). Do not place a token in a URL.

The daily EBS snapshots are crash-consistent, not an application-consistent database backup.
The template disables automatic EFS backup at line 349.
Neither a Git clone nor a saved environment file backs up service data.

Warning: new binaries can migrate a database before a health check fails. Returning to old code does not undo that migration.

Not verified: backup restoration and data compatibility between the selected snapshots. A restore test and each kit's migration checks must prove them.

Run these commands on the development machine from the approved new snapshot clone.
Use a private environment file outside the clone. Do not source it as shell code.

```sh
set +x
printf 'Private environment file for the new snapshot: '
IFS= read -r move_env
test -f "$move_env" || exit 1
sh scripts/host-bootstrap.sh --check-forms "$move_env"
```

The form check must exit with status 0.
It does not prove required values, credential access or a successful build.
Write the complete secret only after separate approval, using the [secret procedure](deploy-script.md#secret).
Use its named empty-value exceptions only where they apply.
Do not use a blanket exception for the newly required private kit credential.

Not verified: the new secret is complete and its clone credentials work. The secret validation and the authenticated clones must prove them.

## Method 1: change the clone and repeat bootstrap

### Preconditions

- All common checks and approvals are complete.
- An approved SSM session reaches the existing instance. The old stack runs from the standard installed clone path.
- The new snapshot needs no change to packages, mount definitions or stack resources.
- The host has space for both snapshot clones and image builds. The old clone and images remain until acceptance.
- No other operator, bootstrap or deployment changes this host during the move.
- Both secrets are ready before bootstrap reads them. Nonempty values in `<Project>/generated` agree with the current service keys.
- The generated-secret role grant and boundary change have their own approved application before write-back.

Not verified: the live template and boundary apply, or the first write-back on a host.
This snapshot-only procedure does not create the new secret or grant its write right.

Not verified: SSM access, free space and the four correct mount identities on the installed host.
Check them privately before the first change. A mount-point test proves presence, not the identity of its storage.

### Commands

Each block in this section runs in the same root shell of the approved SSM session.
Use `sudo -i` to enter that shell.
The paths come from the install kit; they are not paths of a development machine.
Not verified: these shell commands on the installed host. Run them under the approved maintenance and recovery plan.
Stop at the first failed command. Keep the session and its variables until acceptance or recovery.

1. Select the installed paths. The project prompt also supports an installation with a different project prefix.

```sh
set +x
set -eu
umask 077
unset GIT_TRACE GIT_TRACE_PACKET GIT_TRACE_CURL GIT_CURL_VERBOSE
printf 'Installed project name: '
IFS= read -r project
printf '%s\n' "$project" | grep -Eqx '[a-z][a-z0-9-]{2,30}' || exit 1
repo=/opt/$project
stack_env=/etc/$project/stack.env
state=/srv/data/$project
test -d "$repo/.git"
test -f "$stack_env"
test "$(readlink "$repo/.env")" = "$state/.env"
for mount in /srv/data /srv/efs/open-knowledge /srv/efs/openviking /srv/efs/shared; do
    mountpoint -q "$mount"
done
```

2. Save the old identity and configuration privately. This recovery record is not a service-data backup.

```sh
recovery=$(mktemp -d "$state/snapshot-move.XXXXXX")
chmod 700 "$recovery"
git -C "$repo" rev-parse HEAD >"$recovery/old-commit"
cp -p "$stack_env" "$recovery/stack.env"
cp -p "$state/.env" "$recovery/host.env"
cp -p "$repo/kits.lock" "$recovery/kits.lock"
git -C "$repo" status --porcelain >"$recovery/old-status"
```

Review local changes privately before continuing.
The bootstrap changes `image_digest` in `kits.lock`; this is expected.
Keep any other intentional changes in the old clone, and review their effect on the move.
Do not carry an unreviewed script change into the new clone.

3. Enter the new snapshot identity. Compare it with the approved release record, not with a value discovered from the tag alone.

```sh
printf 'Approved snapshot HTTPS URL without a credential: '
IFS= read -r snapshot_url
printf 'Approved snapshot tag: '
IFS= read -r snapshot_tag
printf 'Approved snapshot commit: '
IFS= read -r snapshot_commit
printf '%s\n' "$snapshot_url" | grep -Eqx 'https://[A-Za-z0-9.-]+(/[A-Za-z0-9._-]+)+' || exit 1
printf '%s\n' "$snapshot_commit" | grep -Eqx '[0-9a-f]{40}' || exit 1
git check-ref-format "refs/tags/$snapshot_tag"
printf '%s\n' "$snapshot_tag" >"$recovery/new-tag"
printf '%s\n' "$snapshot_commit" >"$recovery/new-commit"
```

4. Clone into a separate directory and check its commit. A failed clone leaves the running clone untouched.

The temporary credential helper follows the first-boot helper's contract.
It reads only the snapshot token through the instance role. It never writes the token into a file.
Git output goes into a private log because an error can contain the private source URL.

```sh
candidate=$(mktemp -d "/opt/$project-candidate.XXXXXX")
chmod 755 "$candidate"
printf '%s\n' "$candidate" >"$recovery/candidate-path"
(
    umask 022
    helper_dir=$(mktemp -d)
    trap 'rm -rf "$helper_dir"' EXIT
    trap 'exit 1' HUP INT TERM
    cat >"$helper_dir/askpass" <<'SH'
#!/bin/sh
set +x
case $1 in
    Username*) printf '%s\n' x-access-token ;;
    *)
        region=$(awk -F= '$1 == "EC2_AWS_REGION" {print $2}' "$MOVE_STACK_ENV")
        secret_id=$(awk -F= '$1 == "EC2_SECRET_ID" {print $2}' "$MOVE_STACK_ENV")
        aws secretsmanager get-secret-value --region "$region" --secret-id "$secret_id" \
            --query SecretString --output text --no-cli-pager |
            python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("EC2_SNAPSHOT_CLONE_TOKEN", ""))'
        ;;
esac
SH
    chmod 700 "$helper_dir/askpass"
    MOVE_STACK_ENV="$stack_env" GIT_ASKPASS="$helper_dir/askpass" GIT_TERMINAL_PROMPT=0 \
        git -c credential.helper= clone --quiet --depth 1 --branch "$snapshot_tag" \
        -- "$snapshot_url" "$candidate" >"$recovery/clone.log" 2>&1
)
test "$(git -C "$candidate" rev-parse HEAD)" = "$snapshot_commit"
test -f "$candidate/scripts/host-bootstrap.sh"
test -z "$(git -C "$candidate" status --porcelain)"
```

Not verified: this authenticated snapshot clone on the installed host. The successful clone and exact commit comparison must prove it.

5. Stop the old Compose stack after approval. This closes writers before backups and prevents old bind mounts from surviving the directory move.

```sh
cd "$repo"
env -i PATH="$PATH" HOME="$HOME" docker compose --env-file "$state/.env" down \
    >"$recovery/stop.log" 2>&1
```

Do not add `--volumes` or remove any data directory.
Complete the approved consistent backup while all writers, including external EFS clients, are stopped.
Do not continue until that backup is usable.

6. Put the checked clone at the original path. The original path preserves the default Compose project name and the verifier's expected path.

```sh
old_repo=$repo.before-move
test ! -e "$old_repo"
printf '%s\n' "$old_repo" >"$recovery/old-repo-path"
cd /
mv "$repo" "$old_repo"
mv "$candidate" "$repo"
```

If the second move fails, move `old_repo` back before proceeding.
Do not run Compose or bootstrap from the saved clone under its temporary name.
Keep the recovery directory path in your private operator record for a new SSM session.

7. Repeat bootstrap from the new clone. Capture its log privately and check the exit status.

```sh
test "$(git -C "$repo" rev-parse HEAD)" = "$snapshot_commit"
sh "$repo/scripts/host-bootstrap.sh" --stack-env "$stack_env" \
    >"$recovery/bootstrap-new.log" 2>&1
```

Not verified: this bootstrap completes with the changed snapshot and the service data stays usable.
The exit status, private log, kit pins and service checks below must prove it.
A partial failure can change keys, files, images and services. Use the recovery procedure rather than assuming nothing changed.

### What the repeat preserves and changes

| Item | Behavior in the source |
| --- | --- |
| EBS and EFS data | The bootstrap requires existing mounts. It does not format storage or restore data. Service starts can migrate their data. |
| Host environment | The bootstrap renders it again from both secrets and derives `COMPOSE_FILE`. The old file supplies only missing generated keys. |
| Generated keys | The generated secret wins, then the environment secret, then the old file. Gateway keys receive model-list updates and can be recreated. |
| Stack values | The bootstrap reads the existing file. It does not update its snapshot tag or commit, nor the CloudFormation parameters. |
| Kits and images | The new clone starts without kit clones. The bootstrap clones pins and rebuilds unless every clone, lock, image and build-record check agrees. |
| Host settings | It repairs mount dependencies, metadata rules and logging. A log-driver change can restart Docker. |
| Edge and containers | It renders selected edge files, starts the selected services and removes Compose orphans. It applies the Pangolin blueprint when configured. |
| Secrets Manager | The bootstrap writes changed keys only to `<Project>/generated`. A failed put warns and keeps the host file. Check `generated-status` privately. |

The fresh clone avoids overwriting the old lock's generated image records or any ignored kit clones.
It can require a full build even when a kit's source pin stays unchanged.
Keep both old and new built image records privately.

### Checks after the move

1. Compare the host's `HEAD` with `snapshot_commit` again. A successful stack update is not this proof.
2. Compare each kit clone's commit with its `source_commit` in the new host lock. Record the image ids and build records privately.
3. Check the environment link and durable mounts. Confirm the same EBS volume and EFS access points through the approved storage inventory.
4. Run the new snapshot's verifier on the development machine. It must exit with status 0.

```sh
scripts/aws-host.sh --env-file "$move_env" verify
```

5. Inspect both service verification parts. With the agent pin, both must require the running `pi-durable` service without an unpinned-agent skip.
6. Run the approved application checks for existing data. Include database records, agent state, knowledge data, memory data and selected option state.
7. Record public routes, certificates and Entra login checks separately. Do not expose a new name or weaken authentication for verification.
8. Preserve the final environment and image records privately. Record that the running clone differs from the stack's first-boot snapshot parameters.

Not verified: the live verifier, service data, agent memory and knowledge calls, public routes, certificates and login behavior after the move.
Each corresponding live check must prove its own result.
The verifier skips the edge-to-agent request because that request needs a bearer or a model call.
Do not report that path as proved by a service health result.
A model answer needs separate approval for a paid call. Luna uses `xhigh`; do not set `drop_params`.

### Way back

Warning: restoring an earlier backup loses writes made after that backup. Obtain approval before restoring data or credentials.

1. Stop the new stack in its original path. Keep its failure log private.

```sh
cd "$repo"
env -i PATH="$PATH" HOME="$HOME" docker compose --env-file "$state/.env" down \
    >"$recovery/stop-new.log" 2>&1
```

If bootstrap fails before it renders a usable environment, stop here and review the container state through the recovery plan.
Do not swap a clone while any container still binds its files.

2. Restore compatible service data through the approved backup procedure if the new binaries migrated it. Keep every writer stopped.
3. Restore both earlier secret versions through the approved credential procedure. Reconcile generated keys with the restored databases before bootstrap.
4. Recover the saved clone and host environment in the SSM root shell. Use the saved paths if the original session ends.

```sh
failed_repo=$repo.failed-move
test ! -e "$failed_repo"
test -d "$old_repo/.git"
cd /
mv "$repo" "$failed_repo"
mv "$old_repo" "$repo"
cp -p "$recovery/host.env" "$state/.env"
cp -p "$recovery/stack.env" "$stack_env"
test "$(git -C "$repo" rev-parse HEAD)" = "$(cat "$recovery/old-commit")"
sh "$repo/scripts/host-bootstrap.sh" --stack-env "$stack_env" \
    >"$recovery/bootstrap-old.log" 2>&1
```

5. Run the earlier snapshot's verifier on the development machine. Use the restored private environment file.

```sh
printf 'Earlier snapshot clone on the development machine: '
IFS= read -r earlier_clone
printf 'Earlier private environment file: '
IFS= read -r earlier_env
cd "$earlier_clone"
scripts/aws-host.sh --env-file "$earlier_env" verify
```

6. Repeat the application checks. Record every skip, including an agent without a source pin.

Not verified: recovery to the earlier snapshot and restoration of each service's data and keys.
A recovery rehearsal with the selected versions must prove it.
The old bootstrap reads the current secret again; copying `host.env` alone cannot restore its old settings.
Do not prune images or remove recovery copies until both acceptance and recovery retention are approved.

## Method 2: replace the instance

### Preconditions

- All common checks and approvals are complete, including a tested backup outside the root volume.
- A reviewed infrastructure plan explicitly replaces `Instance` while preserving the same data volume, file system, access points and Elastic IP.
- The plan prevents concurrent writers and orders the old volume detach before the new attachment, in the same availability zone.
- The plan includes failure recovery if SSM, first boot or the attachment fails.
- The new secret exists before first boot, with the snapshot and kit clone credentials.

Not verified: a safe replacement plan with these properties. The kit has no detach-order or replacement-forcing workflow.
Stop here until a separate reviewed procedure supplies it. Do not improvise a force detach, stack deletion or storage replacement.

`deploy --replace-instance` is consent for a replacement already in the change set.
It does not force a replacement when only the snapshot tag and commit change.
The template's single `DataVolumeAttachment` refers to both the old storage and the changing instance.
Retention of storage is not proof that CloudFormation attaches it in the required order.

### Commands

These commands describe the deploy portion only, after the missing infrastructure plan is reviewed and approved.
Run them from the approved new snapshot clone on the development machine, using `move_env` from the common preparation.

1. Print the deploy inputs without a cloud call. This is not a preview of an actual change set.

```sh
scripts/aws-host.sh --dry-run --env-file "$move_env" deploy --replace-instance
```

Not verified: these inputs produce a replacement. A separately reviewed change set must show the actual resource changes.

2. Read the reviewed change set through the approved execution path. Supply its identifiers privately.

```sh
printf 'Provisioner profile: '
IFS= read -r profile
printf 'AWS region: '
IFS= read -r region
printf 'Stack name: '
IFS= read -r stack
printf 'Reviewed change set name: '
IFS= read -r change_set
aws --profile "$profile" --region "$region" cloudformation describe-change-set \
    --stack-name "$stack" --change-set-name "$change_set" \
    --query 'Changes[].ResourceChange.[Action,LogicalResourceId,ResourceType,Replacement]' \
    --output table
```

Not verified: the change set retains the original storage and replaces only the intended resources.
The approved infrastructure plan must prove that before execution.

3. Stop all writers through the approved SSM shell and complete the consistent backups. Use Method 1's stop command from the installed clone.

Not verified: every writer stops and the backup restores. The application and restore checks must prove both before replacement.

4. Deploy only under the approved attachment and recovery plan. Do not supply `--allow-remove` for a snapshot move.

```sh
scripts/aws-host.sh --env-file "$move_env" deploy --replace-instance
```

Warning: the script creates another change set and executes it without a confirmation prompt when its guards pass.

This command does not execute the reviewed change set named above.
Use it only when the separate plan approves this behavior and proves the same resource changes.
Otherwise stop and require the [proposed deploy guards](deploy-script.md#snapshot-move-guard-proposals).

Not verified: the replacement, volume attachment and first boot complete. Stack events, mount identities and the first-boot log must prove them.

### Checks after the move

1. Open an approved SSM session to the new instance. Confirm that its instance identity differs from the earlier one.
2. Compare the installed snapshot commit with the approved commit. First boot must pass that comparison before running its scripts.
3. Confirm the original volume and access-point identities at all four mounts. Confirm that the environment file still holds the preserved generated keys.
4. Complete every check from Method 1. Run `scripts/aws-host.sh --env-file "$move_env" verify` from the new development clone.

Not verified: SSM access and exact commit selection after replacement. The new session and host commit check must prove them.
Not verified: reuse of the old environment file, keys and storage after replacement. Private comparisons must prove them.
Not verified: the state of each service after an instance replacement. The verifier and application checks must prove it.
Not verified: public routes, certificates, login and agent integrations after replacement. Run the separate approved checks from Method 1.

The template deletes the old root volume on termination at line 718.
The new instance builds its images again; a build record on EBS does not preserve an image from the old root volume.

### Way back

1. Stop the replacement's writers if the instance is reachable. Preserve failure evidence privately before recovery.
2. Restore the earlier secret and compatible service data through the approved recovery plan. Keep the retained storage identities unchanged.
3. Use Method 1 to restore the earlier approved snapshot on the reachable replacement. Clone and check the earlier tag and commit instead of the later pair.
4. Run the earlier bootstrap and verifier from their normal paths. Confirm the earlier service data and record all skips.

Not verified: a return through Method 1 on the replacement instance. The recovery rehearsal must prove data and binary compatibility.

When the replacement is unreachable, use the separately approved infrastructure recovery plan.
A deploy from the earlier snapshot clone is only the deploy portion of that plan:

```sh
printf 'Earlier snapshot clone on the development machine: '
IFS= read -r earlier_clone
printf 'Earlier private environment file: '
IFS= read -r earlier_env
cd "$earlier_clone"
scripts/aws-host.sh --dry-run --env-file "$earlier_env" deploy --replace-instance
```

Not verified: another replacement returns the stack to the earlier installation.
The same replacement, attachment-order and change-set review requirements apply before any real deploy.
A CloudFormation rollback does not reverse service database migrations or recover the terminated root volume.
