# The deploy script

[Install](../install.md) | [Host template](host-template.md) | [Host boot](host-boot.md)

`scripts/aws-host.sh` deploys the stack and runs the steps around a deploy.
Its defaults are provisioner profile `tenant-ec2`, region `us-east-1` and stack name `tenant-ec2-host`.
The head of the script gives detailed usage.

```sh
scripts/aws-host.sh [--dry-run] [--env-file <path>] <command>
scripts/aws-host.sh --help
```

| Command | Result |
| --- | --- |
| `secret` | Reads the environment file on stdin and writes the JSON secret `tenant-ec2/env`. |
| `deploy` | Creates or updates the stack from `scripts/aws-host.cfn.yaml`. |
| `deploy --replace-instance --step 1\|2 --prepare` and `--execute <name>` | Replaces the instance in two change sets, each with a prepare run and an execute run. See [Instance replacement in two steps](#instance-replacement-in-two-steps). |
| `current-image` | Prints the image id of the running instance as `EC2_AMI_ID=<id>`. `--write` sets it in the environment file. |
| `new-image` | Prints the image id of `EC2_AMI_SSM_PARAMETER` as `EC2_AMI_ID=<id>`. `--write` sets it in the environment file. |
| `outputs` | Prints stack outputs as `NAME=value`. `--write` updates supported output names in the environment file. |
| `dns` | Prints or sets the selected A records through `scripts/dns-records.sh`. |
| `dns --plan` | Reads the Elastic IP from the stack and prints the record set. It changes nothing and calls no provider. |
| `generated-status` | Prints the three generated key names of `<Project>/generated` with `present` or `absent`, never a value. |
| `verify` | Checks the generated key names first, then shows stack status, then runs `docker compose ps`, the file set check, the service list and `scripts/verify-deployed.py` on the host through SSM. No model request runs. |

## Mac dispatch

Set `EC2_VARIANT=macos` for the separate [Mac install flow](../variants.md#prepare-and-install).
The command table above and later Linux details describe homelab only.
Mac uses `<Project>-macos-allocation` and `<Project>-macos-host`, with private state under `.local/macos/`.
Its secrets are `<Project>/macos/env` and `<Project>/macos/generated`, not the homelab namespace.
Account, disk and memory scope come from the stack-written machine document, never the service secret.

Mac adds `host-allocate`, `host-release`, `stop`, `start`, `session`, `tunnel`, `ssh`, `host-bootstrap` and `recovery-check`.
Live lifecycle, secret, deployment, access and replay commands require `--apply`; dry runs make no AWS call or mutation.
`outputs --write` saves private output values. Ordinary output reports names, not values.
`dns` is refused for Mac, including its plan and dry-run forms.
The optional Mac boundary and session role must match the admitted memory scope before deployment or access.

`verify` uses `AWS-RunShellScript`, validates the protected machine document and switches to its PIN account.
It invokes the deployed snapshot verifier with `--repo /opt/<project>/macos/snapshot --project <project>`, which selects `/etc/<project>/machine.json` independently of the directory name.
It also passes `--variant macos --engine podman --file-set compose.macos.yaml --edge-names ''`.
Without `--project`, the repository directory name remains the default; homelab verification stays unchanged.
The verifier supplies the bootstrap's isolated Podman environment, runtime Compose path and private service environment path.
It checks the four selected services; the dormant agent is not a successful fifth-service check.
It requires the exact `COMPOSE_FILE` and matching runtime copy and makes no model or memory request.
The wrapper withholds remote output; approved host-side use prints only fixed check labels.

`host-bootstrap --apply` replays the first-boot stage after `secret --apply`. See [First-boot replay](../variants.md#first-boot-replay).
It runs the scope checks of `session` and needs the running stack instance online in Systems Manager.
It reads `<Project>/macos/env` and refuses an empty secret or a secret that fails the bootstrap service forms. It prints the form code, never a value.
It sends the tracked payload `scripts/macos-first-boot-replay.sh` with `AWS-RunShellScript` to that one instance, after two lines: `MACOS_PROJECT=<project>` and `MACOS_PHASE=first-boot`.
It polls the command invocation for at most 190 minutes and prints only static status lines of the stage and the bootstrap.
A status other than `Success` fails the command with the status name.
Without `--lockdown`, the first phase stops after the PIN account readback. The command then prints `first phase complete; lockdown pending` and no `pass`.
`host-bootstrap --apply --lockdown` sends the same payload with the phase `lockdown`. The host refuses it without a `lockdown pending` record and after a lockdown.
See [Recovery check before the lockdown](../variants.md#recovery-check-before-the-lockdown).

`recovery-check` prints the operator check before the lockdown: the Run As session and the owner-key SSH login through `ssh --apply`.
It makes no AWS call and writes no file. It refuses `--apply`.
`ssh --apply` writes its cost lines to standard error, so an SSH client can use it as its `ProxyCommand`.

The Mac secret writer fixes `COMPOSE_FILE=compose.macos.yaml`; bootstrap requires that exact value and renders it into the private service environment.
Do not weaken the comparison or infer a successful deployment from parser tests.
Not verified: the live Mac command path, session restrictions and complete service hand-over.
See [Mac template guards](host-template-macos.md#lifecycle-guards) and [cost and shutdown](../variants.md#cost-and-shutdown).

## Settings and options

The default environment file is `.env` of this clone.
`--env-file <path>` selects another file.
The `secret` command is the exception: it reads the file on stdin only and refuses `--env-file`.
A value in the file wins over a shell value.
The sample environment file lists the names.

The three option settings `EC2_OPTIONS`, `EC2_DNS_PROVIDER` and `EC2_EDGE_NAMES` come from the environment file only.
The script never reads them from the shell environment.
The same file is the source of the secret, and the host reads the same names from the secret.

| Setting | Default | Effect |
| --- | --- | --- |
| `EC2_OPTIONS` | Empty | Selects `pangolin` or `copilot`, separated by commas with no blanks. Unknown names stop the script. |
| `EC2_DNS_PROVIDER` | `manual` | Selects `manual`, `porkbun`, `route53` or `cloudflare`. |
| `EC2_EDGE_NAMES` | `ok` | Selects names from `ok`, `agent`, `llm` and `ov`, separated by commas with no blanks. |
| `EC2_BEDROCK_EMBED_MODEL` | `amazon.titan-embed-text-v2:0` | Sets template parameter `BedrockEmbedModel`. |
| `EC2_AMI_ID` | Empty | Sets the pinned image `AmiId`. `deploy` refuses an empty value. |
| `EC2_AMI_SSM_PARAMETER` | The public Amazon Linux 2023 x86_64 parameter | Only `new-image` reads it. |

The Pangolin option sets `EnablePangolin=true` and requires the provider `porkbun` or `cloudflare`.
Each command stops when `EC2_OPTIONS` holds `pangolin` and `EC2_DNS_PROVIDER` is neither `porkbun` nor `cloudflare`.
Other options do not open the tunnel ports.
A change of `EC2_OPTIONS` needs `deploy` for the security group, then `secret` and a new run of the bootstrap for the host.
See the [option index](../options/README.md).

## Secret

The command reads stdin only:

```sh
scripts/aws-host.sh secret < .env
```

It writes one JSON object with names as keys, including settings.
It excludes each name of a DNS provider block, except the host names of the provider when Pangolin needs DNS-01 certificates: the Porkbun pair, or `EC2_CLOUDFLARE_DNS01_TOKEN`.
It prints each name that it excludes.
The user data and bootstrap read this JSON form.

An empty required core name or placeholder stops the command before any write.
A core name is a name of Part 1 of the sample environment file.
The same rule applies to empty names in an active option block, and to the host names of the provider with Pangolin.
An inactive option does not require its credentials.
Only `dns` requires the selected DNS provider block, except for the host names of the provider with Pangolin.

These names also need a nonempty value:

- Each name that an active Compose file reads as `${NAME:?...}`.
- With Pangolin: `EC2_EDGE_NAMES`, `EC2_ACME_EMAIL`, `EC2_ADMIN_CIDR` and `EC2_PANGOLIN_SITE` for `scripts/render-pangolin.sh`.
- With `EC2_PI_DURABLE_WEB_SEARCH=mcp`: `EC2_PI_DURABLE_WEB_SEARCH_MCP_URL` and `EC2_PI_DURABLE_WEB_SEARCH_MCP_TOKEN`.

`EC2_PI_DURABLE_WEB_SEARCH` must be `off` or `mcp`. Another value stops the command.

Settings receive no placeholder test: a default from `.env.example` can be correct.
The command stops before a write with one message that names each missing required name.
It calls `scripts/host-bootstrap.sh --check-forms -` with the parsed input on stdin before writing its JSON file.
The bootstrap uses these same form tests at step 2.
A wrong nonempty form stops even `secret --dry-run`, with a nonzero status.
The message names the setting and rule, never the value.
No AWS call runs before this test passes.

A placeholder includes `changeme`, `test`, `<your key>`, `${NAME}` or a value containing `example.com`.
The script's `placeholder` function defines the complete list.
The bootstrap holds the same function, and the check script compares the two.
`--allow-empty <NAME>` accepts an empty value by its exact name.
The name must be a name of Part 1, a name of an option block or one host name of a DNS provider: the Porkbun pair or `EC2_CLOUDFLARE_DNS01_TOKEN`.
The flag accepts no Part 2 setting, including a required Compose or edge setting.
For a name of an inactive option, the flag changes nothing.
It accepts an empty value only; a nonempty placeholder still fails.
See the [first-boot command](host-boot.md#environment-file-and-generated-keys) for generated keys and the optional snapshot credential.
With Pangolin, the first run also needs `--allow-empty EC2_PANGOLIN_API_KEY --allow-empty EC2_PANGOLIN_ORG`; see [Pangolin](../options/pangolin.md).

The command refuses a single quote or a final backslash in a secret value.
The host environment file cannot represent those values in its quoted form.
The bootstrap would refuse them too.

The command prints no secret value.
It supplies the JSON object to AWS in a temporary file with mode 0600, through `--secret-string file://...`.
It removes the file on exit; no secret appears in an argument.

## Environment forms

For a read-only form test on the development machine:

```sh
sh scripts/host-bootstrap.sh --check-forms .env
```

The command uses no stack file, cloud credential or host-only tool. It makes no network call.
Python 3 and the edge's shell tools are required. Pangolin also needs `envsubst` from gettext.
It removes its private temporary directory on exit. Only edge settings enter its temporary file, never credentials.
The test changes no input file. Success has status 0 and no output.
A wrong form has status 1 and names the setting and rule.

| Setting | Nonempty form |
| --- | --- |
| `EC2_OPTIONS` | Known option names separated by commas, with no blanks or duplicates. |
| `EC2_EDGE_NAMES` | `ok`, `agent`, `llm` or `ov`, separated by commas, with no blanks or duplicates. |
| `EC2_DOMAIN` | Letters, digits, dots and hyphens; starts and ends with a letter or digit. |
| `EC2_ADMIN_CIDR` | IPv4 address with prefix length 16 through 32. |
| `EC2_ACME_EMAIL` | Local part with letters, digits, dots, underscores, percent signs, plus signs or hyphens; then `@` and a domain. |
| `EC2_PI_DURABLE_THINKING` | `xhigh` only. |
| `EC2_PI_DURABLE_MODEL`, `EC2_OPENVIKING_VLM_MODEL` | `bedrock/luna`, or `copilot/luna` with Copilot on. |
| `EC2_OPENVIKING_EMBED_MODEL` | `bedrock/embed`, or `copilot/embed` with Copilot on. |
| `EC2_OPENVIKING_EMBED_DIMENSION` | Integer from 10 through 99999, without leading zeros. This tests form, not model compatibility. |
| `EC2_PI_DURABLE_WEB_SEARCH` | `off` or `mcp`. The required-value test also needs both bridge names with `mcp`. |
| `EC2_GERBIL_BASE_ENDPOINT` | With Pangolin: letters, digits, dots and hyphens; starts and ends with a letter or digit. Empty uses the dashboard name. |
| `EC2_PANGOLIN_SITE` | With Pangolin: letters, digits, underscores and hyphens; starts and ends with a letter or digit. |

The certificate domain part allows letters, digits, dots and hyphens, as the edge scripts require.
The edge scripts remain the single source of their form tests. The bootstrap holds the model, vector, list and search tests.
An empty value has no form to test. `secret` still checks required values, placeholders and the permitted `--allow-empty` names.
The Pangolin first-boot exceptions stay unchanged.
The form test proves no DNS resolution, certificate, model access or live service behavior.
It does not validate every CloudFormation parameter or every service-specific credential format.

## Deploy and update guards

`deploy` uses `aws cloudformation deploy`, capability `CAPABILITY_NAMED_IAM` and tag `Project=tenant-ec2`.
It fills template parameters from the environment file and reads the public key from `EC2_SSH_PUBLIC_KEY_FILE`.
It sets `EnablePangolin` from `EC2_OPTIONS` and `BedrockEmbedModel` from `EC2_BEDROCK_EMBED_MODEL`.
The instance role can invoke that embedding model and the Luna model, and no other model.
The gateway reads the same name from the secret, so keep one value in `.env` for both commands.
It refuses a snapshot URL with a user name or credential before `@`.
That URL appears in output and arguments, so a private clone must use `EC2_SNAPSHOT_CLONE_TOKEN` instead.

The image is pinned. `deploy` passes `AmiId=$EC2_AMI_ID` and refuses an empty `EC2_AMI_ID`.
The message names the two ways to fill it:

- `scripts/aws-host.sh current-image --write` reads the image of the running instance with `ec2 describe-instances`. An update with this value replaces nothing for the image. Use it for a stack that exists.
- `scripts/aws-host.sh new-image --write` reads `EC2_AMI_SSM_PARAMETER` with `ssm get-parameter`. A new value replaces the instance at the next deploy, so the next deploy needs the two steps below.

Without `--write`, each command prints the value and changes no file.
An update does not read the public image parameter again. So only a new `EC2_AMI_ID`, or another property that needs a replacement, replaces the instance.

An update first creates and prints a change set.
A `Replacement` value of `True` or `Conditional` counts as a replacement, with one exception: `Conditional` on `Instance` whose only changed targets are `UserData` or `Tags`. CloudFormation restarts an instance with an EBS root volume for its user data and does not replace it (the reference of `AWS::EC2::Instance`, `UserData`). The plain `deploy` accepts that change set and prints a warning line about the restart. The user data runs at the first boot only, so a restart does not run the new user data.
The script deletes and refuses the change set unless you approve each applicable case:

| Guard | Required flag | Owner words |
| --- | --- | --- |
| Replacement of `Instance`. | `deploy --replace-instance --step 1 --prepare`, `--step 1 --execute <name>`, then the same two forms of `--step 2` | Two words for each step: prepare, then execute. |
| Removal of `Instance`, `DataVolume`, `FileSystem` or `ElasticIp`. | `deploy --allow-remove` | One word. |
| Replacement of `DataVolume` or `FileSystem`. | `deploy --allow-remove` | One word. |

Each flag accepts only its own case.
A change set that meets both cases needs both flags.
`deploy` without `--replace-instance` refuses each change set that replaces `Instance`.
`deploy --replace-instance` without `--step`, `--prepare` or `--execute` stops before any AWS call and names the four forms.
The refusal of a replacement names `--step 1 --prepare`. When the stack already has `AttachInstance=false` (step 1 is done), it names `--step 2 --prepare`.

The plain `deploy` keeps its one run: it makes the change set, shows it, checks it and executes it.
This ticket does not change the owner rule for the plain host deploy.
`--prepare` and `--execute` are options of the two replacement steps only.

Warning: an instance replacement loses every file on the root volume. The Elastic IP, data volume and file system stay.

A change of the user data alone, for example of `Domain` or `EnableOperations`, does not replace the instance.
The CloudFormation reference of `UserData` says: with an EBS root volume, an update of the user data restarts the instance ("Some interruptions").
The user data runs only at the first boot, so the restart does not run it again.
The [domain move procedure](host-domain-move.md) reads the change set before any stop, and it orders the data volume detach for a replacement.

Warning: `--allow-remove` can detach retained storage from the stack. Replacement storage starts empty even when the old storage stays in the account.

Keep `EC2_ENABLE_BUCKET` and `EC2_ENABLE_OPERATIONS` at `false` until the provisioner and boundary have their required rights.
A deploy with either switch on fails without those rights.

## Instance replacement in two steps

One change set cannot replace the instance.
CloudFormation makes the new `DataVolumeAttachment` before it deletes the old one, and EC2 refuses it while the old instance holds the volume (proven live on 2026-10-26).
So `deploy --replace-instance` uses two change sets.
The template parameter `AttachInstance` (default `true`) is the switch: `false` removes `DataVolumeAttachment` and `ElasticIpAssociation`.

Each step has two runs. Each run has its own owner word:

| Owner word | Command | Result |
| --- | --- | --- |
| prepare (step 1) | `scripts/aws-host.sh deploy --replace-instance --step 1 --prepare` | Stops the services, unmounts `/srv/data`, makes the snapshot, then makes and shows the change set of step 1. It stops with the name of the change set and the execute command. |
| execute (step 1) | `scripts/aws-host.sh deploy --replace-instance --step 1 --execute <name>` | Checks the change set and the unmount again, executes the change set and proves the detach. |
| prepare (step 2) | `scripts/aws-host.sh deploy --replace-instance --step 2 --prepare` | Makes and shows the change set of step 2. It stops with the name of the change set and the execute command. |
| execute (step 2) | `scripts/aws-host.sh deploy --replace-instance --step 2 --execute <name>` | Checks the change set again, executes it and proves the new attachments. |

`<name>` is the name that the prepare run prints, for example `awscli-cloudformation-package-deploy-<number>`. An ARN is refused.
Use the same environment file in the four runs. The printed command names it when it is not `.env`.
A step without `--prepare` or `--execute` stops before any AWS call and names the four forms.

The dry runs print the same forms:

```sh
scripts/aws-host.sh --dry-run deploy --replace-instance
scripts/aws-host.sh --dry-run deploy --replace-instance --step 1 --prepare
scripts/aws-host.sh --dry-run deploy --replace-instance --step 1 --execute <name>
scripts/aws-host.sh --dry-run deploy --replace-instance --step 2 --prepare
scripts/aws-host.sh --dry-run deploy --replace-instance --step 2 --execute <name>
```

The first dry run prints the four forms. `--step 1` or `--step 2` with no word prints the two forms of that step.

Warning: the site is down from step 1 prepare to the end of the first boot after step 2 execute.

Before step 1, the stack needs the pinned image and the parameter `AttachInstance`.
For a stack of an older template: run `current-image --write`, then `deploy`. This update replaces nothing for the image.
Then run `new-image --write`, or change another property that needs a replacement.

### Step 1 prepare: stop, snapshot, change set

| Part | Action | Proof; a failed proof stops the run |
| --- | --- | --- |
| 0 | Makes the change set of step 2, shows it and deletes it. | `Instance` has the replacement `True`. With `False` or `Conditional`, the run stops before any change. |
| a | One `AWS-RunShellScript` command to the old instance: export the host journal with `scripts/host-journal-export.py` to `/srv/data/journal-before-replace/<time>`, `docker compose stop`, `systemctl stop docker.socket docker.service`, `umount /srv/data`. | The status is `Success` and the output has the line `replace: /srv/data is unmounted`. Without it, no snapshot and no change set. A failed `umount` prints the output of `fuser -vm /srv/data`: the processes that hold the mount. |
| b | `ec2 create-snapshot` of the data volume with the tags `Project`, `Backup` and `Name=<project>-data-before-replace`, then `ec2 wait snapshot-completed`. | The state `completed`. The script runs the wait up to 8 times, about 80 minutes. The state `error` stops the run. |
| c | A change set from the template of the stack (`get-template`) with `AttachInstance=false`. Each other parameter keeps its value in the stack. | The change set removes `DataVolumeAttachment` and `ElasticIpAssociation` and has no other change. Else the run deletes it and stops. |

The run then stops. The change set waits; nothing is detached.
The run prints the name of the change set and the command of step 1 execute.

A repeated prepare finds `/srv/data` unmounted at the start: part a prints `replace: /srv/data is not mounted at the start` and makes no journal export.
In that case part b uses the newest `completed` snapshot of the data volume with the tag `Name=<project>-data-before-replace` again, when it is younger than one hour.
Else part b makes a new snapshot.
A repeated prepare after a mount (for example after the recovery below) exports the journal again and makes a new snapshot.

Part c uses the template of the stack, not the file of the checkout. So a change of the checkout cannot reach the old instance in this step.
Step 1 also removes the address association: the site is already down, and step 2 makes one new association with the new instance.
Not verified: whether CloudFormation can move the association in one update of `InstanceId`. The documentation says only that the update needs a replacement. The safe choice removes it in step 1.

### Step 1 execute: check and detach

The run checks, in this order, and stops at the first failed check with no execute:

1. The stack has `AttachInstance=true`.
2. `describe-change-set` of the stack with the name gives the stack name of the stack, the status `CREATE_COMPLETE` and the execution status `AVAILABLE`.
3. The change set has `AttachInstance=false`.
4. The change set still removes exactly `DataVolumeAttachment` and `ElasticIpAssociation` and has no other change.
5. One `AWS-RunShellScript` command proves that `/srv/data` is still unmounted and Docker is not active: the status `Success` and the line `replace: /srv/data is still unmounted`.

The proof of check 5 is a new read on the host, not a record of the prepare run.
Between the two words, a restart of the old host mounts the volume again by `fstab`, and Docker starts again.
A file of the prepare run cannot see that; only a new read proves the state at execute time.

Then the run executes the change set and waits for `UPDATE_COMPLETE`.
After it, the data volume must have no attachment (`ec2 describe-volumes`).
A refused change set stays. The message names its `delete-change-set` command. After a failed check 5, run step 1 prepare again: it unmounts the volume again and makes a new change set.

### Step 2 prepare: the change set

The stack must have `AttachInstance=false`; else the run stops and names step 1.
The change set comes from the template of the checkout and the environment file, with `AttachInstance=true`.
It must replace `Instance` with `True`. It also adds the two attachments.
A removal of `Instance`, `DataVolume`, `FileSystem` or `ElasticIp`, or a replacement of `DataVolume` or `FileSystem`, needs `--allow-remove`.
Else the run deletes the change set and stops.
The run then stops with the name of the change set and the command of step 2 execute.

### Step 2 execute: replace and attach

The run checks the stack (`AttachInstance=false`) and the change set: the stack name, `CREATE_COMPLETE`, `AVAILABLE`, `AttachInstance=true`, the replacement `True` of `Instance`, and no removal or replacement of the data or the address.
A refused change set stays. Then the run executes the change set.
After `UPDATE_COMPLETE`, the script proves:

1. The output `InstanceId` is a new instance.
2. `ec2 describe-volumes`: the data volume is `attached` to the new instance.
3. `ec2 describe-addresses`: the Elastic IP is on the new instance. The DNS records stay correct.

Then wait for the first boot and the bootstrap, and run `scripts/aws-host.sh verify`.
The first boot mounts the data volume by its file system. It never formats a volume with a file system.

### A stop in a step

| Stop | State | Next step |
| --- | --- | --- |
| Step 1 prepare, part 0 or a | Nothing is changed, or the services are stopped. | One SSM command `mount /srv/data && systemctl start docker.socket docker.service`, then the bootstrap; or run step 1 prepare again. A second run finds the volume unmounted and goes on. |
| Step 1 prepare, part b or c | The services are stopped; the volume is unmounted and still attached. | The same as above. The snapshot stays. |
| Between step 1 prepare and step 1 execute | The services are stopped; the change set waits. | Step 1 execute, or the recovery above. After the recovery, delete the waiting change set with the printed command. |
| Step 1 execute, a failed check | Nothing is executed. The change set stays. | Read the message. Run step 1 prepare again, or the recovery above. |
| Step 2 prepare or execute, a refusal | The volume and the address are not attached. | `current-image --write`, then `deploy`: it attaches both again to the old instance. Then restart the instance once, so that it mounts `/srv/data` and starts Docker. |
| Step 2 execute, a failed update | The rollback keeps the old instance; the volume and the address are not attached. | The same as above. |

A waiting change set does not end. Delete each change set that you do not execute.

Not verified: the four runs and the recovery paths on a live stack. The tests of `scripts/test-host-replace.py` use a stub of the AWS CLI.
Not verified: the journal export ends within its limit of 45 seconds on a host with a long journal. When it fails, part a stops before any service stops.
Not verified: `fuser` is installed on the host image. When it is not, the failure line says `replace: fuser gives no list`.
Not verified: the text form of `describe-snapshots` with the query of part b on the live account (the snapshot id, a tab, the start time).

## Snapshot move guard proposals

The [snapshot move procedure](host-snapshot-move.md) uses a checked clone change and a bootstrap repeat first.
The current deploy script has these gaps for a snapshot move:

- It does not compare changed snapshot parameters with the installed host commit. An update can finish without installing the new snapshot.
- `--replace-instance` permits replacement; it does not force it. Step 1 refuses an update that does not replace the instance, so a snapshot change alone takes no step of it.
- The plain `deploy` (`deploy_update`) creates and executes its own change set. After the guards pass, it executes the change set without another approval prompt. The two replacement steps have a separate prepare and execute.

The two steps of `--replace-instance` order the detach of the data volume before the new attachment, with the recovery checks above.
Proposed separate changes: refuse snapshot-only updates without an explicit host-move plan; separate the change-set creation of the plain deploy from the execution of its reviewed identity.
These are proposals, not implemented guards. Do not assume that a deploy flag supplies them.

Not verified: a live snapshot update, replacement or recovery under these conditions. A reviewed live procedure must prove each result.

## Outputs and DNS

`outputs --write` preserves the environment file mode.
With Pangolin, it sets `EC2_GERBIL_BASE_ENDPOINT` to the Elastic IP.
When a bucket exists, it sets `EC2_BUCKET` to the bucket name.
The host gets file system and access point ids from user data, not this write.
Repeat `secret` after an output write so the host receives changed settings.

The DNS record set contains one A record per selected edge name, plus `pangolin` when that option is on.
Each record points to the Elastic IP under `EC2_DOMAIN`.
There is no wildcard DNS record.
The default provider, `manual`, prints the records and changes nothing.
`dns --plan` prints the same record set with each provider and makes no provider call.
Both forms read the Elastic IP from the stack, so the stack must exist.
The core edge gets a certificate for a name only after the name has its A record.

The provider implementations live under `scripts/dns/`.
A provider credential stays on the development machine unless Pangolin needs its host names for DNS-01.
See [DNS providers](../options/dns.md) for profiles, scopes and provider behavior.
See [Pangolin](../options/pangolin.md) for the provider request handling and certificate exception.

## Verify and dry run

`verify` first checks the three generated key names on the development machine. A missing name stops it; an absent `<Project>/generated` secret gives a skip line.
Then it sends one SSM command through `AWS-RunShellScript`.
It has four parts: `docker compose ps`, the file-set test, the service list and `scripts/verify-deployed.py`.
Its inputs are the snapshot path, Compose file set, service names and public names. They include no secret.
It executes no `verify` command from `kits.lock`, and its host probes make no public request.
It waits for completion and prints the result.
An `EC2_PROJECT` that is not a safe directory name stops the script before a call.
The commands print no error text of Compose: such a text can hold a line of the environment file.

`verify` checks the file set of `EC2_OPTIONS` only.
The file set is the list of Compose files for that value, as the [Compose stack](compose-stack.md) page gives it.
The bootstrap writes the same list into `COMPOSE_FILE` of the host environment file.
`verify` reads `COMPOSE_FILE` from that file on the host and compares it with its own list.
A difference fails the check: write the secret and run the bootstrap again after a change of `EC2_OPTIONS`.
`verify` then tests that each service of the file set runs, except a kit service without a complete source pin.
It reads `kits.lock` of the host, not the development clone.
A kit without a source tag or commit gets a skip line, for example:

```text
service pi-durable: skip (its kit has no pin in kits.lock of the host, so the bootstrap starts no service of it)
```
The bootstrap starts no service for that kit.
The deployed verifier accepts this state only for the agent kit; another unpinned kit still makes verification fail.
It does not test a service of an inactive option.

Then `verify` runs `scripts/verify-deployed.py` of the host snapshot. It gives the file set and `EC2_EDGE_NAMES` to the verifier as its inputs:

```sh
python3 -I scripts/verify-deployed.py --repo . --file-set <COMPOSE_FILE form> --edge-names <names>
```

The verifier holds no list of file sets and no list of services.
It reads the services from the Compose files of the file set, and it checks these services only.
With the core, it checks no service of Pangolin and no Copilot service, and it reads no file of an option.
It compares the two inputs with `COMPOSE_FILE` and `EC2_EDGE_NAMES` of the host environment file. A difference fails the check.
A run by hand on the host needs no `--file-set` and no `--edge-names`: the verifier then takes the two values of the host environment file.
A service of the file set with no check in the verifier fails the check: add its check with the service.

The verifier checks existing containers with `docker exec` and internal HTTP requests. It makes no temporary service and no image.
It reads the deployed `kits.lock`. It uses the stack environment file for Compose, not a kit-local `.env`. It does not start or repair a service.

| Check | Runs with | Proof and limit |
| --- | --- | --- |
| Each service of the file set | Each file set | One running, healthy container for each required service. An unpinned agent gets a skip. A required service that is down stops the verifier before a network request. |
| `caddy` | No option `pangolin` | Its internal health listener. No certificate and no public route proof. |
| `pangolin`, `gerbil`, `traefik` | The option `pangolin` | Internal API health with the exact status 200, internal health and ping. No tunnel and no TLS proof. |
| `litellm-db` | Each file set | Database readiness. No storage write proof. |
| `litellm` | Each file set | Internal health. A refused request with no key and with an invalid key. A model listing with each route of the route file of the file set. The core has `bedrock/luna` and `bedrock/embed`. |
| `copilot` | The option `copilot` | The path from the gateway to the service, with a refused request with no key. The listing adds `copilot/luna` and `copilot/embed`. |
| `openviking` | Each file set | `/health` with `healthy: true`, a refused status request with no key, and the gateway health on the internal network. |
| `open-knowledge` | Each file set | Read access to the deployed `/data/.ok` and the internal `/readyz` on the port of its `PORT` setting. No EFS write proof. |
| `ok-auth` | Each file set | The ready endpoint of the gate. No login proof: no request goes to the Entra tenant. |
| `pi-durable` | Each file set | A skip line with no source pin in `kits.lock`. With a pin: a healthy container, a refused `/v1/` request with no bearer, and the setting `xhigh`. |
| Each name of `EC2_EDGE_NAMES` | Each file set | The path from the edge to the service of the name, on the internal network `edge-<name>`. The member of the edge is `caddy` or, with the option `pangolin`, `gerbil`. The name `agent` gets a skip line: the agent has no path without a bearer and without a model call. |
| A public name that is off | Each file set | A skip line. |
| The blueprint | The option `pangolin` | Each resource is on only through its reference to `EC2_EDGE_NAMES`. A resource that is always on fails the check. |
| Public routes, certificates, logins, model answers | Never | A skip line for each. A health check and a model listing prove no provider access and no answer. A Luna probe must use `xhigh`. |

A bearer value stays inside its container process. The verifier sends fixed probe code on stdin. No secret is in a command argument or in the SSM text.
It prints no raw command output, no response body and no error text. Its Python and Node requests have a deadline and refuse a redirect.
A Docker command of the verifier gets only `PATH` and `HOME`. Without `HOME`, Python takes the home directory of the current account.
The standalone image smoke of a kit stays separate. It does not prove the deployed container or its storage.

```sh
python3 -I scripts/test-verify-deployed.py
scripts/aws-host.sh --dry-run --env-file .env.example verify
```

The first command runs the tests of the verifier with stubs of Docker and of the AWS CLI. One test covers each value of `EC2_OPTIONS`.

`--dry-run` prints commands and makes no AWS, DNS provider or SSM call.
It replaces a file containing secrets with a placeholder between angle brackets.
An empty setting appears as `<NAME>`, with a line explaining what stops a real run.
It works with the sample:

```sh
scripts/aws-host.sh --dry-run --env-file .env.example deploy
```

## First deploy order

1. Fill `.env`. The [install guide](../install.md) explains the core values. Run `scripts/aws-host.sh new-image --write` for `EC2_AMI_ID`.
2. Run `scripts/aws-host.sh --dry-run deploy`. Check the commands before approval.
3. Write the secret with the [first-boot exceptions](host-boot.md#environment-file-and-generated-keys). User data needs it before first boot.
4. Run `scripts/aws-host.sh deploy` after approval. It creates billed resources.
5. Run `scripts/aws-host.sh outputs --write`. It records applicable stack outputs.
6. Write the secret again with the same required exceptions. It receives the output changes.
7. Run `scripts/aws-host.sh dns`. The Elastic IP now exists. Use `dns --plan` first to read the record set without a change.
8. Run `scripts/aws-host.sh verify` after the bootstrap ends. Inspect each failure rather than treating stack status as service health.

## Known limits

The original script tests use AWS CLI and HTTP stubs, not a live stack.

- Not verified: a real deploy, a real secret write, a real DNS call and a real SSM command.
- Not verified: the provisioner policy permits every change set call with both its id and `--stack-name`.
- Not verified: the host has the snapshot at `/opt/tenant-ec2` with the link `.env` to the host environment file. The verifier needs no kit clone.
- Not verified: the checks of the verifier pass against real containers. Offline fixtures prove the pass paths and the failure paths only.
- Not verified: the path check from `gerbil` reaches a service with the option `pangolin`. Traefik uses the network of `gerbil`.
- Not verified: on the host, `docker compose ps --status running --services` prints each running service of the file set.
- Not verified: on the host, `.env` of the clone is the link to the host environment file, and the SSM command can read it.

The core exposes only `ok` by default.
`verify` makes no public request, so it needs no other public name.
A skip line is not proof that a route works, and not proof that it failed.
Do not expose another name or weaken the gate for a check.
Both the service list and the deployed verifier skip an agent with no source pin in the host lock.
The current lock pins the agent, so the bootstrap includes it and both checks require its running service.
The skip behavior remains for older snapshots with no agent pin.
Not verified: this skip path in an SSM command on Amazon Linux 2023. Offline stubs prove the command logic only.
