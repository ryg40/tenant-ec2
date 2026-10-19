# Variants

A variant selects a complete host design, not a Compose overlay.
`EC2_VARIANT=homelab` is the existing Linux default. `EC2_VARIANT=macos` selects the conditional Mac design.
Both use one portable snapshot and one `kits.lock`.

A Dedicated Host is physical EC2 capacity reserved for one account.
A Podman machine is a Linux virtual machine that runs containers for the macOS Podman client.
AWS describes EC2 Mac as bare metal without the Nitro hypervisor; its first Linux guest is not nested virtualization inside an EC2 VM.
That distinction does not qualify applehv on the selected AMI. An AMI is the image used to start an EC2 instance.

## Comparison

| Part | homelab | macos |
| --- | --- | --- |
| Host | Amazon Linux 2023, x86_64 | `mac2-m2.metal`, arm64 Mac, reviewed and pinned AMI |
| Engine | Docker | PIN-owned Podman machine, explicit applehv provider |
| Compose | `compose.yaml` with one edge and optional Copilot | Standalone `compose.macos.yaml`; no overlays |
| Current services | Gateway, Postgres, OpenViking, OpenKnowledge, gate, agent and edge | Gateway, Postgres, OpenKnowledge and gate |
| Agent | Existing pinned OpenViking backend | Disabled `agentcore-pending` profile until a compatible portable kit release and pin |
| Memory | OpenViking; separate optional stage stacks | Stage AgentCore and S3 policy for the host; no personal key on the Mac |
| Storage | Encrypted EBS and EFS access points | Encrypted root/data EBS; APFS at `/Volumes/data`; guest named volumes |
| Browser | Public HTTPS through Caddy or Pangolin | HTTP localhost through an encrypted operator tunnel |
| Network | Public edge, restricted SSH | No inbound rules; ephemeral public IPv4 for outbound access and SSM |
| Options | Pangolin, Newt, Copilot, DNS providers | None of those services or public DNS |
| Login | Existing Linux administration | One PIN-derived interactive local administrator, zsh |
| Compute billing | Instance running time | Dedicated Host allocation, minimum 24 billable hours |

The local-Podman canary is the selected path. No remote Linux or native-service fallback starts automatically.
The official pinned Podman installer is selected, not Homebrew's Podman package.
The existing provisioner receives an opt-in Mac boundary and a separate session role; the homelab boundary stays separate.
The deployment owner is the Dedicated Host release operator.
No allocation or deployment follows from reading this page or passing source checks.

Not verified: applehv startup, Linux guest networking, ARM64 builds, credential refresh, or headless launchd recovery on the selected EC2 Mac AMI.
Full runtime readiness remains blocked until the agent kit has an approved portable AgentCore release and the shared lock changes.
The first AgentCore backend contract supports recall only, not memory writes or automatic capture.
A host policy may permit proposal writes; that permission does not implement a writer in the agent.
Never enable the dormant profile or patch a kit checkout to bypass this dependency.

## Two template sets and account ownership

The account owner uses an MFA administrator session for the provisioner and memory stacks.
MFA means multi-factor authentication. The provisioner is a narrower deployment identity, not the administrator.
The two template sets use the same AWS account. They do not create a second AWS account.

| Template set | Owner | Responsibility |
| --- | --- | --- |
| Provisioner and stage memory | Administrator | Deploy `scripts/aws-provisioner.cfn.yaml` and the selected generated memory template; admit the Mac role. |
| Mac allocation and runtime | Provisioner | Create the allocation stack, then the runtime stack through `scripts/aws-host.sh` after approval. |

The stage file has one active person and a matching `hostRoles` admission for `macos-role`.
Its host actor differs from every person actor. The admission names one proposal project and its approved memory and file scope.
The administrator deploys that admission before enabling the Mac boundary.
The boundary limits the role's identity policy; it grants no permissions by itself.
Memory currently has no supported AgentCore resource policy. The account's other IAM grants still need review.
See [memory stacks](memory-stack.md) and the [Mac template reference](reference/host-template-macos.md).

## Prepare and install

These steps describe an approval-gated canary, not a proven unattended install.
Keep deployment identifiers, prices and credentials in ignored private state, never tracked examples.

1. Clone the approved portable tag and compare its commit. Use the shared snapshot, not a variant-specific release branch.
2. Prepare the administrator-owned memory stage and optional Mac provisioner settings. Confirm the actor, proposal project and boundary scope match.
3. Copy `.env.example` to a mode `0600` environment file. Set `EC2_VARIANT=macos` and keep `EC2_OPTIONS` and `EC2_EDGE_NAMES` empty.
4. Clear OpenViking, Pangolin, Copilot and public-domain settings. Keep DNS manual and do not run the DNS command for this variant.
5. Select `EC2_MACOS_PERSON`, `EC2_MACOS_DEVELOPER_PIN`, `EC2_MACOS_AGENT`, `EC2_MACOS_PROPOSAL_PROJECT` and `EC2_MACOS_MEMORY_STAGE`. The person actor and uppercase PIN must match the stage registry.
6. Set `EC2_MACOS_RUNTIME_LOCK=config/macos/runtime.lock.json`. Its qualification-required status remains a live-test limit, not a runtime pass.
7. Supply the owner public key through `EC2_SSH_PUBLIC_KEY_FILE`. Keep its private key and all personal IAM keys off the host.
8. Prepare new service and Entra credentials in the private environment file. Register exactly `http://localhost:4180/oauth2/callback` and the allowed group.
9. Confirm regional capacity, quota and an official hourly rate. Record the currency, region, source and timestamp in the `EC2_MACOS_PRICE_*` settings.
10. Preview allocation with `scripts/aws-host.sh --dry-run host-allocate`. No preview authorizes billing or creates resources.
11. After separate approval, run `scripts/aws-host.sh host-allocate --apply`. It records allocation ownership and cost privately and refuses duplicate allocation.
12. Review a compatible arm64 AMI and its minimum root disk. Record `ami.imageId`, `parameter`, `region`, `architecture`, `rootVolumeMinGiB` and `reviewedAt` in `.local/macos/<runtime-stack>.json`.
13. Set `EC2_MACOS_AMI_SSM_PARAMETER` to that reviewed public parameter. Deploy uses the recorded image, not an automatic latest-image lookup.
14. Preview `scripts/aws-host.sh --dry-run deploy`. Review the exact parameters and any replacement or removal.
15. After approval, run `scripts/aws-host.sh deploy --apply`. The runtime stack creates its own empty Secrets Manager objects.
16. Run `scripts/aws-host.sh outputs --write`. The output values stay in private state rather than the terminal summary.
17. After approval, run `scripts/aws-host.sh secret --apply < .env`. It writes only selected service settings to `<Project>/macos/env`.
18. Resume bootstrap through the approved administrative recovery path after the secret is populated. Initial user data can stop safely on the empty secret.
19. Retrieve the generated developer password privately with the administrator profile. Change it at the first login.
20. Run `scripts/aws-host.sh verify`. Review its health-only result and all separate live qualification limits.

The Mac secret fixes `COMPOSE_FILE=compose.macos.yaml`; bootstrap validates and renders that exact setting for the verifier.

The runtime stack has no `InstanceType` parameter: it fixes `mac2-m2.metal` internally.
Only the allocation template exposes the instance type, restricted to that value.
The [Mac template reference](reference/host-template-macos.md) lists the complete parameter contract.
Defaults allocate four guest CPUs, 8,192 MiB RAM and an 80 GiB VM disk, not the larger research starting estimate.
The bootstrap requires twenty GiB beyond that VM disk size on the data volume.
No automatic fallback resolves a failed VM, account setup or AMI privacy prompt.

## Person, PIN and local account

`actorId` identifies a person in memory namespaces. The PIN is a person number, not a password or login factor.
An uppercase PIN such as `XYZ1000` produces account `xyz1000` and home `/Users/xyz1000`.
The shell is `/bin/zsh`. The bootstrap preserves an existing `.zshrc`; a new one initializes Homebrew.
The PIN account owns routine Homebrew, Git, Python, Node and Podman work.
The AMI supplies AWS CLI, SSM Agent, Homebrew and Xcode command-line tools; full Xcode is separate.

The account is the sole interactive human account, not the only directory record.
System accounts remain. The bootstrap verifies the new account before disabling `ec2-user` and `ssm-user` interactive access.
It removes their SSH authorization, disables their accounts and sets noninteractive shells.
Its SSH rules allow the PIN user only and disable root and password SSH login.
Run Command remains an administrative noninteractive recovery path.
A sudo administrator can change these local controls; they are not an IAM isolation boundary or MDM compliance claim.
MDM means mobile device management, which this kit does not deploy.

The first password lives only in Secrets Manager at `${Project}/macos/generated`, key `EC2_MACOS_DEVELOPER_PASSWORD`.
The bootstrap sends it to the native password prompt, never a command argument or log.
It sets a minimum length, history and first-login change policy, without resetting changed passwords on reruns.
Not verified: password expiry, GUI screen lock, default-account exclusion, SSM Run As and recovery after reboot on the selected AMI.
Directory readback proves metadata, not exclusive login access.

## Personal memory credentials

The selected policy installs no personal memory key on the Mac.
Host services use temporary instance-role credentials through the scoped credential bridge.
That bridge does not use the person's profile or make the host role equivalent to the person.

A separately approved alternative is the [memory-stack hand-over](memory-stack.md#keys) to the PIN account.
Its local profile alias must be exactly `pi-memory-person-<actorId>`, not a PIN or host-agent alias.
That alias is a local credential selection name, not the IAM user name.
Store it only in `~/.aws/credentials`, owned by the PIN account with mode `0600`; use mode `0700` for `~/.aws`.
Never put a key value in documentation, shell initialization, Compose, machine data, a ticket or a log.
Do not hand over a key before checking that the developer home and credentials directory stay outside guest mounts.
This alternative changes the selected no-personal-key policy and needs separate approval.

## Tunnel and verification

The gate publishes only `127.0.0.1:4180`. OpenKnowledge has no published port.
SSM means AWS Systems Manager; its sessions avoid opening inbound network ports.
The Mac session role permits the stack's PIN shell document, local port forwarding and SSH-over-SSM only.
It does not permit remote-host forwarding or an arbitrary default shell document.

1. Run `scripts/aws-host.sh tunnel --apply` from the operator machine after access approval. Keep that tunnel open while using the browser.
2. Open `http://localhost:4180`. Localhost means the operator machine through the encrypted tunnel, not a public Mac address.
3. Optionally run the bounded redirect check from the operator clone. It checks the first response without following the Entra URL.

```sh
python3 -I scripts/verify-deployed.py --variant macos --engine podman --tunnel-check
```

4. Complete allowed-user and rejected-group login checks manually after approval. The redirect check does not prove authentication, group enforcement or WebSockets.

The gate uses a non-`__Host` cookie with `cookie_secure=false`, because the browser origin is HTTP localhost.
The tunnel encrypts transit, but another local process can target that origin. Do not expose or reuse this origin on a public interface.
Entra ignores the port when matching localhost redirects; do not register multiple callbacks that differ only by port.

The host wrapper runs the verifier as the PIN account, never root's Podman identity.
It checks the protected deployment machine schema, exact `COMPOSE_FILE`, runtime Compose copy, selected kit pins and four selected services.
It uses Podman's documented JSON inspection, not Docker's inspection templates.
It checks gate-only loopback bindings and account metadata without reading authorized-key contents or passwords.
It uses `/Volumes/data/runtime/compose.macos.yaml`, `/Volumes/data/service.env` and the bootstrap's isolated Podman environment.
It makes no paid model call, memory write, public DNS request, application build or image smoke run.
Not verified: host-side forwarding from the Podman guest; inspection reports the engine's bindings, not an external reachability test.

## Cost and shutdown

Let H be the confirmed hourly host rate and B the billable allocation hours.
Host compute costs approximately `max(24, B) * H`; storage, requests, public IPv4, transfer and model charges are additional.
The unconfirmed planning rate for M2 in N. Virginia is $0.878 per hour, not an official quote.
At that provisional rate, 24 billable host hours cost $21.072 before other resources.
The operator must confirm the official rate before allocation. A short canary does not remove the allocation minimum.

| State | Host billing and action |
| --- | --- |
| Running | The allocated host is billed; the instance adds no second host compute charge. |
| Stopping or pending scrub | AWS pauses host billing during pending scrubbing; do not promise this removes the minimum. |
| Stopped, host available | Ordinary allocated idle time remains billable. |
| Released | Future On-Demand host billing ends; retained resources still need review. |

Warning: stopping does not release the Dedicated Host or end ordinary allocated-host billing.

1. Preview the stop with `scripts/aws-host.sh --dry-run stop`. Confirm the selected private allocation state.
2. After approval, run `scripts/aws-host.sh stop --apply`. A successful request does not prove the stop or scrubbing is complete.
3. Wait for the instance to stop and host scrubbing to finish. Apple-silicon scrubbing can take up to 4.5 hours.
4. Confirm the minimum allocation period and exact host in private state. Set `releaseHostId` to its reviewed `hostId`.
5. Preview `scripts/aws-host.sh --dry-run host-release`. The release path checks ownership, age, availability and attachments.
6. After approval, run `scripts/aws-host.sh host-release --apply`. It deletes only the allocation stack and checks that the host is released.
7. Record the released state or the remaining chargeable host as an open action. The deployment owner remains responsible for release completion.

`start --apply` requires the same still-allocated, available host. It refuses a released or wrong host.
A released host identifier cannot be reused; the stopped instance still has host tenancy.
Do not use raw release commands against CloudFormation-owned allocation.

| Retained item | Required review |
| --- | --- |
| Runtime stack and stopped instance | Release does not delete them. Any later replacement needs separate approval. |
| Root EBS | `DeleteOnTermination=false`; stopping and host release preserve its account and secret-bearing files. |
| Data EBS and guest named volumes | Retained encrypted APFS and VM disk; no automatic wipe, reset or volume cleanup. |
| Snapshots | Existing snapshots remain separately managed and potentially billable. |
| Secrets Manager objects | Runtime environment and generated values remain retained after stack deletion. |
| Stage AgentCore and S3 | Administrator-owned resources outlive the Mac runtime; no record deletion runs here. |

## Decision sources

Each row names the documentation section that supports the design, not proof of deployment.

| Decision | Documentation section and URL | Meaning |
| --- | --- | --- |
| Allocate M2 physical capacity before launch. | [Launch a Mac instance, Allocate the Dedicated Host](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/mac-instance-launch.html) | The instance needs a previously allocated Mac host. Regional availability is not current zone capacity. |
| Treat applehv as unqualified, not prohibited nested virtualization. | [Amazon EC2 Mac FAQs, macOS Amazon Machine Images](https://aws.amazon.com/ec2/instance-types/mac/faqs/) and [Nested virtualization, Introduction](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html) | Bare-metal Mac is distinct from a VM running inside a virtual EC2 instance. Neither page qualifies this Podman runtime. |
| Discover and pin a supported arm64 AMI. | [Retrieve macOS AMI IDs, latest major macOS AMI](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/macos-ami-ids-parameter-store.html) and [macOS AMIs, Default packages and Approve Local Network Privacy policies](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/macos-ami-overview.html) | Public parameters locate images; privacy prompts, installed tools and compatibility still need an AMI check. |
| Use encrypted EBS, not FileVault or the internal SSD. | [Amazon EC2 Mac instances, Considerations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-mac-instances.html) | EBS supports this storage path; FileVault can prevent boot. EFS is excluded here, not declared unsupported. |
| Keep allocation and runtime ownership separate. | [AWS::EC2::Host, Properties](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-host.html) and [AWS::EC2::Instance, HostId, Tenancy and Affinity](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-instance.html) | Explicit placement lets allocation release stay separate from runtime retention. Host recovery and maintenance are enabled. |
| Price allocation, stop and release separately. | [Dedicated Hosts Pricing, On-Demand Pricing](https://aws.amazon.com/ec2/dedicated-hosts/pricing/) and [Stop or terminate, Release the Dedicated Host](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/mac-instance-stop.html) | Minimum billing, pending scrub and release eligibility differ. The regional planning rate remains unconfirmed. |
| Refuse reuse after release. | [Release an Amazon EC2 Dedicated Host](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/dedicated-hosts-releasing.html) | Released host identifiers cannot be reused; retained instances and storage require separate handling. |
| Retain durable data and secrets. | [DeletionPolicy, RetainExceptOnCreate](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-deletionpolicy.html) and [UpdateReplacePolicy, Retain](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-updatereplacepolicy.html) | Initial rollback and later deletion have different retention behavior. Retention is not a backup. |
| Use a separate optional boundary. | [Permissions boundaries for IAM entities](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html) | Both the identity policy and boundary must allow the action; local sudo is not restricted by IAM. |
| Admit host memory access through identities, not a Memory resource policy. | [PutResourcePolicy, Introduction](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_PutResourcePolicy.html) | This resource-policy feature supports Runtime and Gateway only. Other account identities remain an administrator concern. |
| Keep inbound ports closed and select a PIN session. | [Mac FAQs, EC2 Mac instance connectivity](https://aws.amazon.com/ec2/instance-types/mac/faqs/), [Run As support](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-preferences-run-as.html), and [Session document schema, inputs](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-schema.html) | SSM gives a route without inbound SSH. Run As requires the existing account and does not fall back. |
| Restrict session documents through a separate role. | [Start a session, SSH and port forwarding](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html) and [AssumeRole, Permissions](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html) | The operator obtains a short session role rather than widening the host's runtime role. |
| Deliver temporary host credentials to the guest. | [Process credential provider](https://docs.aws.amazon.com/sdkref/latest/guide/feature-process-credentials.html) and [export-credentials, Description](https://docs.aws.amazon.com/cli/latest/reference/configure/export-credentials.html) | SDKs consume temporary credentials through a process. This custom Mac-to-guest bridge still needs refresh qualification. |
| Keep personal credentials an explicit hand-over. | [Manage access keys for IAM users](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html) | A personal key is long-lived and separate from temporary instance-role credentials. |

Podman sources: [Installation, macOS](https://podman.io/docs/installation), [machine init, volume mounts](https://docs.podman.io/en/v5.6.2/markdown/podman-machine-init.1.html), and [compose, external provider](https://docs.podman.io/en/v5.6.2/markdown/podman-compose.1.html).
Initialization sources: [ec2-macos-init, Userdata and User Management](https://github.com/aws/ec2-macos-init#userdata).
Browser source: [Entra redirect URI rules, Localhost exceptions](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url).
