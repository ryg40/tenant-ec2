# Mac host templates

[Variants](../variants.md) | [Deploy script](deploy-script.md) | [Host boot](host-boot.md)

A Dedicated Host is physical EC2 capacity allocated to one account.
The Mac variant uses separate allocation and runtime CloudFormation stacks in the same AWS account.
The administrator owns the provisioner and memory templates. The provisioner owns these two Mac stacks after approval.
This page records source behavior, not a qualified deployment.

## Allocation ownership

`scripts/aws-macos-allocation.cfn.yaml` owns one `AWS::EC2::Host`.
Its default stack name is `<Project>-macos-allocation`.
Auto-placement is off; host recovery and maintenance are on.
The host has `Project`, `Variant=macos` and `Purpose=macos-allocation` tags.
It has no retention policy: the guarded release command deletes this stack only after release eligibility checks.
CloudFormation does not promise to wait out the Mac minimum allocation period during rollback.

| Parameter | Type and default | Meaning |
| --- | --- | --- |
| `Project` | String, `tenant-ec2` | Names the project resources and ownership tags. |
| `AvailabilityZone` | AWS availability-zone name | Reviewed zone with the requested capacity; no automatic fallback. |
| `InstanceType` | String, `mac2-m2.metal` only | The requested M2 physical host type. |

| Output | Meaning |
| --- | --- |
| `HostId` | The allocated host identifier; keep the value in private local state. |
| `AvailabilityZone` | The zone that must also contain the runtime and data volume. |

## Runtime ownership

`scripts/aws-host-macos.cfn.yaml` owns the Mac instance on the existing host, not the host allocation.
Its default stack name is `<Project>-macos-host`.
It creates a VPC, subnet, internet gateway, route, outbound-only security group, encrypted data volume, secrets, instance role and session document.
The subnet gives the instance an ephemeral public IPv4 address for outbound access. There is no Elastic IP or inbound rule.
There is no NAT gateway, EFS, public edge, DNS integration or optional operations stack in this variant.

The instance fixes `mac2-m2.metal`, host tenancy, host affinity and the supplied `DedicatedHostId`.
Both root and data storage use encrypted gp3 EBS. FileVault is not enabled.
Root has `DeleteOnTermination=false`. Data uses `RetainExceptOnCreate` and `UpdateReplacePolicy: Retain`.
The runtime secrets use the same retention policies as data.
The selected image's disk requirement must be checked before using the root-size default.

## Runtime parameters

The template has exactly these parameters. The helper rejects a different template interface.

| Parameter | Default or limit | Purpose |
| --- | --- | --- |
| `Project` | `tenant-ec2`; lowercase project name | Names resources, secrets and the protected machine file. |
| `DedicatedHostId` | Required host identifier | Uses the separately owned allocation. |
| `AvailabilityZone` | Required | Must match that host and data volume. |
| `AmiId` | Required image identifier | Reviewed arm64 Mac image; no latest-image resolution in the template. |
| `VpcCidr` | Required IPv4 range, /16 through /26 | Network and single subnet range. |
| `RootVolumeSize` | 100 GiB; 100 through 1000 | Encrypted root volume; also respect the image's actual minimum. |
| `DataVolumeSize` | 150 GiB; 100 through 1000 | Encrypted APFS data volume and persistent VM storage. |
| `PodmanProvider` | `applehv` only | Explicit Linux VM provider. |
| `PodmanCpus` | 4; 1 through 8 | Guest CPU count. |
| `PodmanMemoryMiB` | 8192; 1024 through 16384 | Guest memory. |
| `PodmanDiskGiB` | 80; 20 through 1024 | Guest disk; bootstrap also checks data-volume headroom. |
| `SshPublicKey` | One Ed25519 or RSA public key | Owner authorization for the PIN account; never a private key. |
| `SnapshotRepository` | Credential-free HTTPS URL | Approved shared portable snapshot source. |
| `SnapshotTag` | Required tag | Snapshot ref to clone. |
| `SnapshotCommit` | Exact 40-digit lowercase commit | Prevents a changed tag from silently changing code. |
| `MemoryStage` | `dev`, `test` or `prod` | Stage registry and host admission. |
| `MemoryArn` | Required; `NoEcho` | Exact stage memory identity for the host policy. |
| `MemoryBucket` | Required; `NoEcho` | Exact stage file bucket for the host policy. |
| `AgentActorId` | Required lowercase identifier | Service actor, distinct from every person actor. |
| `ProposalProject` | Required lowercase identifier | Project scope of the host admission. |
| `DeveloperPin` | `XYZ` and four digits | Active person's PIN, from which the local account is derived. |

`NoEcho` hides parameter display; it does not make a resource identifier a secret or remove it from every AWS policy view.
There is no runtime `InstanceType` parameter or `AmiSsmParameter` parameter.
The deploy helper passes the pinned image as `AmiId` after checking private review metadata.

| Runtime output | Meaning |
| --- | --- |
| `InstanceId` | Mac instance selected for lifecycle and SSM operations. |
| `HostId` | Existing allocation used by this runtime. |
| `DataVolumeId` | Retained data volume, identified before any formatting. |
| `HostRoleArn` | Instance role with the separate Mac boundary. |
| `SessionDocumentName` | Stack-owned document with the PIN account as Run As user. |
| `DeveloperAccount` | Lowercase PIN used for the local account and verifier. |

## Provisioner opt-in and memory

The administrator updates `scripts/aws-provisioner.cfn.yaml` with the following optional inputs.
An unset switch leaves the Mac runtime role disabled without widening the homelab boundary.

| Provisioner parameter | Meaning |
| --- | --- |
| `EnableMacos` | Defaults to `false`; enables the reviewed Mac policies and session role. |
| `MacosMemoryArn` | Exact admitted stage memory. |
| `MacosMemoryBucketName` | Exact admitted stage bucket. |
| `MacosAgentActorId` | Host service actor matching the stage admission. |
| `MacosProposalProject` | Proposal project matching the stage admission. |

The optional outputs are `MacosBoundaryArn` and `MacosSessionRoleArn`.
The existing `UserName`, `BoundaryArn` and `SnapshotRoleArn` outputs remain unchanged.
The existing region-scoped `ec2:*` grant already includes host allocation; the opt-in does not invent that permission.
With `EnableMacos=true`, the provisioner gains only `cloudformation:DescribeStacks` on administrator stacks `bootstrap-<Project>` and `bootstrap-<Project>-memory-*`, with no write right on either.
The owner must approve an administrator deployment of this template before the new read works.
The Mac role must retain the exact Mac boundary. Explicit denies prevent substitution with the homelab boundary or boundary removal.
The provisioner cannot edit the administrator-owned session role.

The session role has no runtime secret, model or memory permissions.
Its boundary and identity policy admit only project-tagged Mac targets and three exact session documents.
They are the PIN shell document, `AWS-StartPortForwardingSession` and `AWS-StartSSHSession`.
The original provisioner cannot use its broad direct StartSession grant for Mac targets.
Run Command remains a separate administrative recovery capability; it does not inherit Session Manager Run As.
The verifier wrapper explicitly switches to the PIN account before using Podman.

The host role reads `<Project>/macos/env`, reads and updates `<Project>/macos/generated`, and uses scoped model and admitted memory/file actions.
The boundary grants no standalone right; the host identity policy must allow each action too.
The generated secret also holds `EC2_MACOS_DEVELOPER_PASSWORD`, which the owner changes at first login.
The current agent remains disabled. The first compatible AgentCore release provides recall only; its unused proposal grant proves no write capability.

## Bootstrap documents

User data writes `/etc/<project>/machine.json`, owned by root, group admin, mode `0644`.
Then it writes the first-boot stage `/etc/<project>/macos/first-boot.zsh`, owned by root, group wheel, mode `0700`, and runs it.
The stage reads the environment secret, clones and checks the pinned snapshot, and calls the bootstrap.
`host-bootstrap --apply` runs the same stage again; see [First-boot replay](../variants.md#first-boot-replay).
The stage accepts one argument, `--lockdown`, and passes it to the bootstrap. Without it, the bootstrap stops before the account lockdown with exit code 3.
The stage then prints `lockdown pending` and does not write `first-boot.done`; see [Recovery check before the lockdown](../variants.md#recovery-check-before-the-lockdown).
The machine document contains no service secrets. Its fields are:

| Field | Meaning |
| --- | --- |
| `schemaVersion`, `project`, `variant`, `region`, `engine` | Version 1, selected project/region, `macos`, `podman`. |
| `fileSet`, `services` | Exactly `compose.macos.yaml` and the four active services while the agent gate remains closed. |
| `account` | `pin`, derived `name`, `/Users/<name>` home, `/bin/zsh` shell and owner `sshPublicKey`. |
| `disk` | Exact `volumeId` and `/Volumes/data` mount point. |
| `memory` | `stage`, `agentActorId`, `proposalProject`, and `/run/tenant-ec2/memory.json` config path. |
| `podman` | `provider`, `cpus`, `memoryMiB`, and `diskGiB`. |

The bootstrap's shared closed schema rejects unknown or duplicate fields and mismatched stage admission.
The verifier reuses that deployment schema and requires execution as the selected PIN account.
The tracked `config/macos/machine.json` is different: it is a public candidate mount policy, not a deployment document.
`scripts/macos-machine.schema.json` validates that tracked candidate's exact mounts, dormant profile and no-personal-key policy.
Do not replace it with private account or volume values.

Service settings arrive separately from Secrets Manager as root-owned private JSON.
The bootstrap validates that JSON, then writes PIN-owned `/Volumes/data/service.env` with mode `0600`.
It stages `/Volumes/data/runtime/compose.macos.yaml`; the verifier compares it with the snapshot copy before using it.
See [Mac runtime qualification](../../config/macos/README.md) for credential transport, disk checks and launchd ordering.

## Lifecycle guards

`host-allocate`, `host-release`, `stop`, `start`, `deploy`, `secret`, `session`, `tunnel`, `ssh` and `host-bootstrap` require explicit `--apply` for live action.
A preview makes no AWS call, provider call, temporary file or mutation.
`outputs --write` records private output state; it is not a deployment.
`verify` is read-only service evidence through SSM, not a model or memory acceptance run.
`host-bootstrap` sends one fixed `AWS-RunShellScript` payload to the stack instance only after the scope checks, an online SSM check and a valid environment secret.
Only `host-bootstrap --apply --lockdown` approves the account lockdown. `recovery-check` prints the operator check before it and makes no AWS call.

Allocation checks ownership tags, existing stacks and duplicate hosts before creating a new allocation stack.
Deploy checks stage admission, boundary scope, exact template parameters, reviewed AMI metadata and existing host attachments.
Instance replacement needs `--replace-instance`; protected removal needs `--allow-remove` in addition to `--apply`.
Changing the selected person on an existing runtime is refused.
Release requires an exact `releaseHostId` confirmation, ownership, minimum age, availability and stopped or terminated attachments.
It deletes the allocation stack only and checks the host's released state.
A pending scrub or ineligible host is refused, not hidden behind an automatic long sleep.

Warning: stopping does not release the Dedicated Host. Follow the [cost and shutdown procedure](../variants.md#cost-and-shutdown).
Warning: first boot can format the identified blank data disk. Replacement, retention and any later cleanup need explicit review.

Not verified: live IAM enforcement, allocation, AMI startup, APFS matching, account transition, credential refresh, Podman startup, reboot recovery or completed release.
Not verified: the PIN key login and Run As session before the lockdown, and the recovery steps after it.
Not verified: full Entra login, allowed-group refusal, browser WebSockets, model requests or AgentCore recall.

## AWS sources

| Decision | Documentation section and URL |
| --- | --- |
| Separate host ownership and explicit placement | [AWS::EC2::Host, Properties](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-host.html); [AWS::EC2::Instance, HostId, Affinity and Tenancy](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-instance.html) |
| Mac compatibility, EBS encryption, recovery and maintenance | [Amazon EC2 Mac instances, Considerations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-mac-instances.html) |
| Stop, pending scrub and minimum release age | [Stop or terminate your Mac instance, Release the Dedicated Host](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/mac-instance-stop.html) |
| Retained data, root disk and secrets | [DeletionPolicy, Options](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-deletionpolicy.html); [UpdateReplacePolicy, Retain](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-updatereplacepolicy.html); [EBS block device, DeleteOnTermination](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-ec2-instance-ebs.html) |
| Administrator-owned boundary and temporary session | [Permissions boundaries for IAM entities](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html); [AssumeRole, Permissions](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html) |
| PIN Run As and session documents | [Session document schema, inputs](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-schema.html); [Run As support for Linux and macOS](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-preferences-run-as.html) |
| SSM without inbound SSH | [Mac FAQs, EC2 Mac instance connectivity](https://aws.amazon.com/ec2/instance-types/mac/faqs/); [Start a session, SSH and port forwarding](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html) |
| Public AMI lookup and explicit pin | [Retrieve macOS AMI IDs, latest major macOS AMI](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/macos-ami-ids-parameter-store.html) |
| Identity admission rather than Memory resource policy | [PutResourcePolicy, Introduction](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_PutResourcePolicy.html) |

The [variants decision table](../variants.md#decision-sources) explains these AWS terms and their operational limits for new AWS users.
