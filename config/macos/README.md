# Mac runtime qualification

The unprofiled Compose subset contains LiteLLM, Postgres, OpenKnowledge and its Entra gate.
The pi-durable entry stays behind `agentcore-pending` until an approved AgentCore release and shared pin exist.
No backend variable, capture mechanism or OpenViking substitute is supplied.
Do not enable that profile with the current lock.

## Bootstrap boundary

`host-bootstrap-macos.sh` is a zsh entrypoint for `bootstrap-runtime.py`.
`--check-forms` validates machine data and separate service settings without changes.
`--dry-run` prints static planned steps. `--root` selects a read-only filesystem fixture.
Native execution requires root on macOS. Linux execution refuses mutation.
Each failed precondition prints a static reason, not command output or secret values.
One native run holds the bootstrap lock. A second run while it is held stops with `bootstrap-busy`.
Without `--lockdown`, a native run stops after the account readback, records `lockdown pending` and exits with code 3.
`--lockdown` approves the account lockdown. It refuses without that record (`lockdown-not-pending`) and after a lockdown (`lockdown-already-done`).
After the lockdown, a run without `--lockdown` continues and does not repeat it. `--resume` refuses `--lockdown`.
A kit source without a readable commit stops with `kit-source-incomplete`; the bootstrap does not delete it.

The first-boot stage of the user data clones the approved tag and verifies its commit before calling this script.
`scripts/aws-host.sh host-bootstrap --apply` replays that stage after the environment secret is written. See [First-boot replay](../../docs/variants.md#first-boot-replay).
The user data writes `/etc/<project>/machine.json` as root, group admin, mode `0644`, without service secrets.
Use `--machine` for a nondefault project path. The default is `/etc/tenant-ec2/machine.json`.
The fields are `schemaVersion`, `project`, `variant`, `region`, `engine`, `fileSet`, `services`, `account`, `disk`, `memory` and `podman`.
Unknown fields fail, including nested fields. The PIN matches exactly one active person in the selected stage.
The memory actor and proposal project match that stage's `macos-role` entry through the renderer's `host_entry` interface.
An older renderer fails closed. Service secrets cannot supply account, disk, memory-scope or VM-sizing fields.

Service settings arrive on stdin as JSON or environment assignments.
`--env-file` accepts a mode `0600` file. The parser never evaluates shell expressions.
Project and region selectors match `machine.json`. The current subset refuses pi-durable settings, including empty values.
Selecting pi-durable still fails the upstream release gate with the current pin.
No personal AWS credentials file or profile is created.

Python 3 and the AMI's existing Homebrew and Xcode command-line tools are prerequisites.
If PyYAML is absent, native execution creates a root-owned virtual environment and installs `PyYAML==6.0.3` as a wheel.
Forms and dry-run modes never install dependencies. They report `python-yaml-required` instead.
Private kit-source tokens are not supported by this first Mac build path; the selected kits have public source pins.

## Account and disk

The bootstrap matches the EBS volume identifier against an IOKit serial and checks `diskutil` metadata.
It refuses ambiguous disks, existing foreign layouts and unexpected APFS volume identifiers.
Only an unformatted whole disk can reach `eraseDisk APFS`.
A private state record preserves format intent and the APFS volume UUID for repeat runs.
The mount readback must identify the same volume at `/Volumes/data` before runtime writes.
A resume cannot format a disk or initialize a missing VM.

Warning: the native first-boot path formats the identified blank data disk. A live run requires deployment approval.

First boot generates a random 24-character password with Python `secrets`.
The Mac generated-key helper stores it at `<Project>/macos/generated` under `EC2_MACOS_DEVELOPER_PASSWORD`.
It preserves sibling values, refuses password replacement and avoids unchanged writes.
The bootstrap reads the secret back before account creation so a retry retains the same initial password.
`sysadminctl -password -` receives it through a pseudo-terminal with echo disabled, never through an argument.
The bounded prompt loop discards output and refuses an administrator-password prompt.
The owner retrieves the secret through the administrator profile and changes the password at first login.

The `dscl` readback checks the name, home, shell, identifiers and admin membership before default-account restrictions.
The restrictions wait for `--lockdown`, after the operator check of the PIN login. See [Recovery check before the lockdown](../../docs/variants.md#recovery-check-before-the-lockdown).
The sole owner public key uses a `0700` directory and `0600` file with PIN-account ownership.
Existing `.zshrc` content survives repeat runs. New files initialize Homebrew's zsh environment.
The account policy requires password history, minimum length and a first-login change.
Repeat runs do not reset the account password or expire it again.
After the lockdown, SSH permits the PIN account only, with root and password login disabled.
The lockdown disables guest and automatic login, sets screen-lock preferences and disables default interactive accounts.
A root-owned SSH restriction still excludes default users if an initialization module recreates their keys.
An unresolved passwordless sudo rule fails the check instead of silently weakening the policy.

Not verified: the selected AMI's password prompt, first-login expiry, account policy, screen lock, recovery or reboot access.
Not verified: the real IOKit layout and APFS operations. Linux fixtures exercise decisions, not macOS commands.

## Podman lifecycle

`runtime.lock.json` pins the ARM64 Podman installer, matching applehv image and Darwin ARM64 Compose client by SHA-256.
Native installation checks both `pkgutil` signatures and `spctl` assessment before running the installer.
Homebrew ownership moves to the PIN account. Routine Homebrew, Podman and build commands run as that account.
Homebrew installs Git, Python and Node without an unattended Homebrew installer or GUI login.

The tracked `config/macos/machine.json` records the mount policy, not deployment values.
Only runtime configuration and pinned source directories under `/Volumes/data` enter the VM.
The whole home, personal credentials and engine socket stay outside application mounts.
The deployment file supplies VM sizes; candidate defaults are four CPUs, 8,192 MiB and an 80 GiB disk.
The runtime requires twenty GiB of data-volume capacity beyond the requested VM disk size.
`XDG_DATA_HOME` and `XDG_CONFIG_HOME` place machine state on the verified EBS volume.
The isolated environment selects `applehv`, connection `tenant-ec2-macos` and the pinned Compose provider.
The saved machine configuration must show exactly the two read-only mounts before the machine starts.

The build path clones exact kit tags and verifies their commits and clean working trees.
It builds ARM64 images from their Dockerfiles without patching either kit.
The gateway smoke target runs before the gateway build. OpenKnowledge receives its release and revision arguments.
Named volumes use the application UID and GID; guest namespace readback checks their ownership before service startup.
Only the selected four-service subset starts. No machine reset or volume cleanup runs automatically.

The bootstrap installs a root launchd job only after the first successful service start.
It uses private persisted inputs, runs at load and retries every sixty seconds without `KeepAlive`.
Each resume verifies the disk and account, starts the existing PIN-owned VM, refreshes credentials and reconciles services.
The job uses the bootstrap lock. Commands have deadlines and timeout cleanup terminates their process groups.
The checked-in templates stay disabled until this validated installation step fills their arguments.
The separate credentials template documents the exporter interface; it is not installed as an unordered second job.

The deployed verifier runs as `account.name`, not root.
Its Podman environment must use the same XDG paths, provider, connection and Compose provider as the bootstrap.
The verifier and host dispatch own that integration; plain `sudo -u <account.name> -H` does not set those variables.

Not verified: installer execution, headless applehv, saved-machine field compatibility, ARM64 builds or named-volume initialization.

## Credential bridge

`macos-credentials.py` runs an isolated AWS CLI instance-role chain with three bounded attempts.
It validates expiry and atomically replaces a mode `0600` file inside a root-owned `0700` directory.
Failure preserves the previous file. Both process helpers reject stale credentials and terminal output.
No personal profile, inherited key, proxy or endpoint override participates in export.

The native lifecycle stages host credentials outside the shared runtime tree.
It sends credential bytes to the guest over Podman's SSH stdin, never in arguments or Compose environment values.
The guest writer uses `podman unshare` to map the container UID and atomically replaces files inside a private directory.
The LiteLLM credential directory mounts read-only at `/run/tenant-ec2/aws`.
Its helper, SDK config and empty shared-credentials file prevent a fallback to metadata or personal credentials.
A container-user check validates helper access before Compose startup without printing its output.
The Python SDK fixture proves refresh after replacement. The Node test is a process-cache fixture, not a real SDK test.

Not verified: guest UID mapping, SELinux traversal, SSH transfer, replacement visibility or real Node SDK refresh on the selected image.
Not verified: the installed Mac AWS CLI exports instance credentials through this chain.

## Gate

The standalone model publishes only `127.0.0.1:4180` for the Entra gate.
Support images keep the digest pins of `kits.lock`; application images keep its local build names.
Registry reads establish ARM64 manifest availability for Postgres and oauth2-proxy, not runtime success.
The browser origin is `http://localhost:4180` through an encrypted SSM or SSH-over-SSM tunnel.
The gate uses a non-`__Host` cookie, `cookie_secure=false` and `reverse_proxy=false`.
Issuer, group, nonce, PKCE, header stripping and WebSockets remain enabled.
OpenKnowledge uses port 4180 and the same external origin to preserve the incoming Host header.

Not verified: OpenKnowledge host-guard acceptance, Entra login or WebSockets on the deployed subset.

## Source evidence

The design follows the supplied Mac research record and these primary sources:

- [AWS Mac AMIs and Local Network Privacy](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/macos-ami-overview.html).
- [AWS macOS initialization modules](https://github.com/aws/ec2-macos-init/blob/master/README.md).
- [Podman installation](https://podman.io/docs/installation).
- [Podman machine init](https://docs.podman.io/en/latest/markdown/podman-machine-init.1.html).
- [AWS process credential provider](https://docs.aws.amazon.com/sdkref/latest/guide/feature-process-credentials.html).
- [Entra localhost redirect URI rules](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url).
