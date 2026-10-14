# The host boot

[Install](../install.md) | [Host template](host-template.md) | [Compose stack](compose-stack.md) | [Move to a later snapshot](host-snapshot-move.md) | [Move under a new domain](host-domain-move.md)

Three scripts bring a new instance to a running Compose stack.
User data writes stack values, clones the portable snapshot, checks its commit and starts `scripts/host-userdata.sh`.
That script prepares the host and starts `scripts/host-bootstrap.sh` with the stack values file.
The bootstrap does the remaining work.
The scripts check existing state before repeating a step.

```sh
sudo sh /opt/tenant-ec2/scripts/host-bootstrap.sh
sh scripts/host-bootstrap.sh --dry-run
sh scripts/host-bootstrap.sh --check-forms .env
sh scripts/host-bootstrap.sh --help
```

The first command repeats the bootstrap on the host as root.
The dry run prints its steps without changing the host.
A wrong nonempty form stops even the dry run at step 2, before directory, build or service work.
Missing values still give notes, so an unfinished install can show its remaining steps.
The form-only command reads the named file without stack values, AWS credentials or host tools.
It makes no network call and changes no input file.
It uses Python 3 and the edge's shell tools; Pangolin also needs `envsubst` from gettext.
A private temporary directory is removed on exit. With Pangolin, it holds only the edge settings, never credentials.
An empty value has no form to test. This command does not prove that all required values exist.
The `secret` command calls it automatically before writing its JSON file.

## Steps

| Step | Bootstrap action | A second run |
| --- | --- | --- |
| Stack values | Reads the region, project and secret name from `/etc/tenant-ec2/stack.env`. Requires the data volume and access point mounts. | Uses the same checks. |
| Environment file | Reads Secrets Manager with the instance role. Writes a root-owned file with mode 0600 and the selected `COMPOSE_FILE`. | Keeps generated keys. Writes nothing when the content is unchanged. |
| Directories | Makes each required data volume directory with the service owner. Makes nothing on an access point. | Skips correct owners and modes. |
| Host settings | Makes Docker need the four durable mounts and the metadata guard. Loads `wireguard` only for Pangolin. Installs the metadata guard, then sets the Docker log driver to `journald`. With operations on, exports the journal to files before the start of the CloudWatch agent. | Keeps correct settings and an unchanged agent configuration. Repairs the guard rules and starts the export timer. |
| Kits | Clones each complete pin and builds one kit at a time. Records the commit and image id. | Skips only when the clone, lock, image and build record agree. |
| Edge | Renders Pangolin files only with that option. Caddy makes its routes at container start. | Keeps unchanged Pangolin files. |
| Start and keys | Starts the edge and gateway, creates virtual keys, starts OpenViking and creates the agent user key, then starts remaining services. | Keeps existing keys and unchanged services. |
| Blueprint | With Pangolin, applies the rendered blueprint through its integration API. | Skips without its key or organization. A second apply keeps the same resources. |
| Next steps | Prints the remaining manual actions. | Omits completed actions. |

An empty required core name or a placeholder stops the run before a build.
Required option names are checked only for active options.
The same applies to names read by the selected Compose file set.
An invalid edge setting also stops the run before a build.
Both secret rendering and the dry run call the same form tests.
The [deploy reference](deploy-script.md#environment-forms) lists each form and its limits.

## Paths on the installed host

These are paths of the install kit, not paths of the development machine.

| Path | Content |
| --- | --- |
| `/opt/tenant-ec2` | The portable snapshot clone. |
| `/opt/tenant-ec2/kits/<kit>` | Each pinned kit clone on the root volume. |
| `/opt/tenant-ec2/kits.lock` | The bootstrap records each built image id here. |
| `/etc/tenant-ec2/stack.env` | Stack values from user data, without secrets. |
| `/srv/data/tenant-ec2/.env` | The environment file outside every build context. A new instance reuses it. |
| `/srv/data/tenant-ec2/builds/<kit>` | One line with the last build's commit and image id. |
| `/opt/tenant-ec2/.env` | A link to the environment file for manual Compose commands. |
| `/srv/data/litellm-db` | Postgres data, owned by `999:999`, with mode 0700. |
| `/srv/data/pi-durable` | Agent state, owned by `1000:1000`, with mode 0700. |
| `/srv/data/caddy` | Core-edge certificate store, owned by `1000:1000`, with mode 0700. |
| `/srv/data/copilot` | Optional Copilot state, owned by `1000:1000`, with mode 0700. |
| `/srv/data/pangolin/` | Optional Pangolin state, owned by root, with mode 0700. |
| `/usr/local/sbin/tenant-ec2-metadata-guard` | The installed guard script. A systemd unit has the same name. |
| `/etc/systemd/system/docker.service.d/tenant-ec2-storage.conf` | Docker requires the four durable mounts and the metadata guard before it starts. |
| `/var/log/tenant-ec2/` | With operations on: the exported log files `docker.log` and `journal.log`. |
| `/var/lib/tenant-ec2/journal-export/` | With operations on: the cursor of the journal export. |

The [Pangolin page](../options/pangolin.md) describes its three separate edge directories.

## Durable mounts before Docker

The data volume and the three access points are the four durable mounts: `/srv/data` and `/srv/efs/<name>`.
No service of a file set starts on a directory of the root volume. This is true for the core and for each option.

- The first boot installs the Docker drop-in before it installs Docker. Docker starts only after the storage is mounted and the metadata guard is installed.
- `RequiresMountsFor` pulls in `/srv/data` and the three `/srv/efs/<name>` mounts, also with `nofail` in fstab.
- `BindsTo` and `After` name the mount units, with the escaped name `srv-efs-open\x2dknowledge.mount`.
- A mount unit that is not active stops Docker. A network stall on an active EFS mount is not an unmount.
- A failed mount blocks Docker. Four `ExecStartPre` checks with `mountpoint` also refuse an underlying directory at each start of the daemon.
- The bootstrap installs the same drop-in before a Docker restart. It installs the metadata guard before it changes the log driver.
- Each Compose command of the bootstrap checks the four mount points again, with each file set. A late mount never permits a start on the underlying path.
- `nofail` still lets the host boot for a repair. After the repair of a mount, a start of Docker tests the dependencies again.

## Environment file and generated keys

The environment file comes from the secret, plus the generated host keys and selected file set.
The bootstrap prints names, not values.
It disables tracing with `set +x`, including when called with `sh -x`.
It passes no secret in a command argument.
Its Python code uses `python3 -I`, so it loads no module from the current directory.

The bootstrap creates these three keys:

| Name | Scope |
| --- | --- |
| `EC2_OPENVIKING_LITELLM_API_KEY` | The model routes that OpenViking needs. |
| `EC2_PI_DURABLE_LITELLM_API_KEY` | The model route that the agent needs. |
| `EC2_PI_DURABLE_OPENVIKING_API_KEY` | The agent's OpenViking account, with role `user`. |

The stack creates a second secret, `<Project>/generated`, with only these three names.
The instance role reads and describes this secret. It can write a new version with `secretsmanager:PutSecretValue`.
It gains no write right on `<Project>/env`.
The administrator must apply the matching boundary change before the host can write.

The bootstrap reads the generated secret before the environment secret.
A nonempty generated value wins. The environment secret is second, and the old host environment file is the fallback.
A missing or empty generated secret permits first boot. The bootstrap creates the missing keys through the service APIs.
After key creation, one `put-secret-value` saves all three names. An unchanged repeat writes no new version.
The helper runs with `python3 -I`. Values use stdin and a temporary mode 0600 file, never arguments.
A failed put prints a name-only warning, keeps the environment file and continues the bootstrap.
Repair the secret or its rights before repeating the bootstrap. The host cannot create a missing secret.
A failed read warns and uses the existing fallback. Review that warning before replacing storage or changing credentials.

On the development machine, `scripts/aws-host.sh generated-status` prints each name with `present` or `absent`.
`verify` requires all three names without comparing or printing values.
On an older deployment without the generated secret, `verify` skips this check until the owner applies the host stack update.
`secret` fills empty generated names from this second secret. It keeps each explicit nonempty private-file value.
A dry run reads no secret, so it cannot prove that an empty name has a stored value.
Copy approved external credentials through a private Secrets Manager read, not through Systems Manager command output.
Bitwarden and Vault remain optional stores; see [home lab tools](../options/home-lab.md).

Before a generated key has a stored value, the `secret` command needs its `--allow-empty` exception.
With a public snapshot, it also needs the exception for `EC2_SNAPSHOT_CLONE_TOKEN`.

```sh
scripts/aws-host.sh secret \
    --allow-empty EC2_OPENVIKING_LITELLM_API_KEY \
    --allow-empty EC2_PI_DURABLE_LITELLM_API_KEY \
    --allow-empty EC2_PI_DURABLE_OPENVIKING_API_KEY \
    --allow-empty EC2_SNAPSHOT_CLONE_TOKEN < .env
```

Omit an exception when that name has a value.
A private snapshot needs its clone credential before first boot.
With `pangolin`, add both `--allow-empty EC2_PANGOLIN_API_KEY` and `--allow-empty EC2_PANGOLIN_ORG` until initial setup finishes.
The [Pangolin page](../options/pangolin.md) gives that setup.
The optional Part 2 names `EC2_KIT_SOURCE_PI_DURABLE` and `EC2_KIT_TOKEN_PI_DURABLE` need no exception.
The secret command refuses `--allow-empty` for a Part 2 name.
The [home lab page](../options/home-lab.md) explains private kit sources.

A secret value must not contain a single quote or end with a backslash.
The bootstrap writes values between single quotes in the host environment file.
Both `secret` and the bootstrap refuse an unsupported value.
A dollar sign, blank, hash, double quote or an internal backslash is supported.

## Metadata guard and logs

The metadata guard blocks the instance metadata address `169.254.169.254` for every container except the gateway.
The gateway needs the instance role for the Bedrock routes.
Its fixed address on `egress` lies outside Docker's dynamic address range.
The guard therefore needs one static exception, not a process that follows containers.
The exception remains correct while the gateway stops or starts.

The guard has the first rule in `DOCKER-USER` and a chain of its own.
Its systemd unit creates the rules before Docker starts at each boot.
`RequiredBy=docker.service` prevents Docker from starting when the guard fails.
Each bootstrap run tests the rules and repairs them when necessary.
The host itself keeps access to instance metadata.

Warning: restarting the `tenant-ec2-metadata-guard` unit also restarts Docker and its containers.

To apply the rules without restarting the unit, use:

```sh
sudo tenant-ec2-metadata-guard apply <address-of-the-gateway>
```

The Docker log driver is `journald`.
The journal holds container and host logs, with the container name as the tag.
`docker logs` still works.
The Docker `local` driver's files are daemon-only files; a log shipper cannot read them directly.

## Operations logs

The stack value `EC2_ENABLE_OPERATIONS` of `/etc/tenant-ec2/stack.env` turns this part on. It is not a value of the secret.
See [the host template](host-template.md#operations) for the template switch and the rights.

- The bootstrap installs `amazon-cloudwatch-agent` from the Amazon Linux repositories only with operations on.
- `scripts/host-journal-export.py` exports `journalctl --system --output=json` one time each minute.
- A Docker record has `CONTAINER_ID_FULL` or `CONTAINER_NAME`. It goes to `docker.log`. Each other record goes to `journal.log`.
- The files are in `/var/log/<Project>/`. Each file rotates at 10 MiB and keeps seven old files, with no compression.
- A record can exceed the limit by its own size. A high log rate can rotate a file before the agent reads it.
- The first export holds the current boot. A later export starts from a cursor in `/var/lib/<Project>/journal-export/`.
- The exporter writes the cursor after it flushes both files. A crash can repeat records. It cannot skip a record.
- Each export has a lock and a timeout of 45 seconds for the journal command. The systemd unit has a timeout of 60 seconds.
- The bootstrap runs one export before it starts the agent. A failed export stops that step.
- The agent reads the two active files and follows their rotation. It does not read the journal.
- The agent configuration asks for no change of the retention. The template sets 14 days.
- With operations off, a later bootstrap stops the export timer and the agent. The export files and the cursor stay.

Warning: a service log can hold a secret, for example the first setup token of Pangolin. Limit the readers of the log group.

## Kit builds

An incomplete tag or commit in `kits.lock` prevents that kit from cloning, building or starting.
Other selected services can still start; this is not a complete core install.
The lock pins the basic pi-durable kit at `portable-v0.1.0`, so the bootstrap includes its service.

A private kit source needs a read-only `EC2_KIT_TOKEN_<KIT>` in the host secret.
The locked agent source needs `EC2_KIT_TOKEN_PI_DURABLE` before first boot.
Set `EC2_KIT_SOURCE_<KIT>` only to override the source URL; the tag and commit stay fixed.
`GIT_ASKPASS` supplies the credential without placing it in a URL, argument or log line.
The build runs with an empty environment, so shell variables cannot change a kit pin.
Never build from a working checkout of a kit.

The image id alone does not prove a source commit.
An image name can stay the same while the source commit changes.
The bootstrap rebuilds when the build record does not name the lock commit, even if the image id still matches.
A skip needs the lock image id, the real image id and the build record to agree.

Public kit builds also write their commit into `org.opencontainers.image.revision`.
When this label exists, the bootstrap checks it before a skip and after a build.
A different label commit after a build stops the run.
The built image then does not prove the lock's source pin.

## Manual actions after first boot

1. Check `generated-status` and preserve approved external copies with the [credential procedure](../credentials.md). Never print values through Systems Manager.
2. Record the built `image_digest` values from the host lock in your controlled lock record. They identify the actual built images.
3. Complete the [Entra login checks](../entra.md). Only an allowed group member must reach OpenKnowledge.

The [Copilot login](../options/copilot.md) and [Pangolin setup](../options/pangolin.md) apply only when you select those options.
A [Newt site](../options/home-lab.md) is also optional.

## Known limits

- Not verified: the live template and boundary apply, or the first write-back on a host.
- Not verified: an existing instance receives the new right without replacement. A reviewed change set and approved host check must prove it.
- An IAM update does not run bootstrap. Write-back runs only after the new bootstrap runs.

- Not verified: a kit build on the host. An Amazon Linux 2023 container test used Docker stubs and cloned the three pinned public kits.
  That test checks their lock commits and build image names, not a real image build.
- Not verified: real service starts, the two gateway key calls, the OpenViking key call and the Pangolin blueprint apply.
- Not verified: Docker preserves an existing `DOCKER-USER` chain and containers cannot reach metadata. Container tests prove rule construction, not the host packet path.
- Not verified: Docker assigns the fixed gateway address and uses `ip_range` for other `egress` members. Compose renders the addresses; no network test proves assignment.
- Not verified: a first boot on Amazon Linux 2023, a reboot, a Docker restart, the recovery from a failed mount and the metadata packet path. Offline fixtures check the unit syntax and the order only.
- Not verified: a private kit clone with a credential.
- Not verified: CloudWatch Logs receives the exported Docker and system records on a host, also after a rotation. Offline tests prove the file routing, the cursor recovery and the rotation.
