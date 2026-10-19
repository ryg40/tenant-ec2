# Install the core

This page installs `EC2_VARIANT=homelab` on one Amazon Linux 2023 x86_64 EC2 host.
For `macos`, use the separate [variant install flow](variants.md#prepare-and-install).
The Mac path has different account, storage, secret, billing and tunnel requirements; do not reuse these Linux steps.
It uses Bedrock, Caddy and the Entra gate without any option.
Read the [architecture](architecture.md) before creating resources.

Warning: the provisioner, secret, application registration, deploy and DNS steps change external systems. Run them only with approval.

Not verified: a complete live core install. The [reference pages](../README.md#pages) list the checks and their limits.

## What you need

| Requirement | Check |
| --- | --- |
| AWS account | An administrator can create the scoped provisioner and boundary. Bedrock model access must permit the selected Luna and embedding models. |
| Entra tenant | You can create an application registration and a security group, configure group claims and grant required consent. |
| Domain name | You can set A records. The core uses HTTP-01 or TLS-ALPN-01 and needs no DNS provider key. |
| Development machine | A POSIX shell, Git, Python 3, AWS CLI version 2, `curl`, OpenSSH, OpenSSL and `envsubst` from gettext. |
| Static checks | Docker with Compose, `cfn-lint` and `shellcheck` 0.10.0 or later. See [fallback tools](reference/checks.md#hooks-and-fallback-tools). |
| Entra automation | Azure CLI if you use `scripts/entra-app.sh`; the [Entra page](entra.md) also describes the registration requirements. |
| Portable snapshot | An approved clone URL, tag and exact commit. Every required kit needs a complete pin in `kits.lock`. |
| Credential storage | A secure local store for new values. The core does not require Bitwarden or Vault. |

The host uses the instance role, not the provisioner's access key.
The kit defaults to `us-east-1` and `t3a.large`.
Use `m7i-flex.xlarge` if kit builds need more memory.

## 1. Clone and hooks

Replace angle-bracket placeholders before running a command.
The snapshot URL must contain no credential.

1. Clone the approved portable tag. This gives the reviewed install kit without development history.

```sh
git clone --branch <portable-tag> --single-branch <snapshot-https-url> tenant-ec2
```

2. Enter the clone. Each development-machine command below runs from this directory.

```sh
cd tenant-ec2
```

3. Compare the clone commit with the approved commit. A different commit is not the approved kit.

```sh
git rev-parse HEAD
```

4. Install the hooks. They scan commits and pushes.

```sh
scripts/install-hooks.sh
```

5. Check the hooks. The command must report that they are current.

```sh
scripts/install-hooks.sh --check
```

6. Run the scan self-test. It must detect its synthetic secret.

```sh
sh scripts/scan.sh selftest
```

7. Check every required kit pin in `kits.lock`. Stop if a required kit lacks its source tag or commit.

The lock pins the agent at `portable-v0.1.0`. The bootstrap includes it, and `verify` requires its running service.
The agent source is private. Set `EC2_KIT_TOKEN_PI_DURABLE` before writing the host secret.
Follow the [private kit source procedure](options/home-lab.md#private-kit-source); a source override is unnecessary for the locked repository.
Not verified: a private agent-kit clone or a complete install with the agent on the host.
A snapshot clone has no development work tracker or local host-value deny list.
A clean scan without that list does not prove the absence of your own host values.

## 2. Prepare the provisioner

The provisioner is the scoped AWS identity for the host stack.
Its boundary limits the roles that it creates.
The [credentials page](credentials.md) gives the full procedure and key storage choices.

1. Check `aws --version`. It must be version 2 with the `login` command.
2. Set the administrator profile region. A browser login needs a region.

```sh
aws configure set region us-east-1 --profile tenant-ec2-admin
```

3. Set its output format. The scripts read JSON.

```sh
aws configure set output json --profile tenant-ec2-admin
```

4. Start an administrator login. Use an IAM administrator, not the account root identity.

```sh
aws login --remote --profile tenant-ec2-admin
```

5. Complete the browser login. The CLI receives a temporary session.
6. Deploy the provisioner stack after approval. It creates the scoped identity and boundary.

```sh
EC2_CREDENTIAL_STORES=aws scripts/aws-provisioner.sh deploy
```

7. Create the provisioner key in the AWS CLI profile. The explicit `aws` storage mode excludes optional stores.

```sh
EC2_CREDENTIAL_STORES=aws scripts/aws-provisioner.sh key
```

The script stores this key in the AWS CLI credentials file, outside `.env` and Git.
It keeps unrelated profiles and gives the credentials file mode 0600.
Not verified: live key creation or rotation through this storage mode.

8. Verify the provisioner. The final line must be `verify: pass`.

```sh
EC2_CREDENTIAL_STORES=aws scripts/aws-provisioner.sh verify
```

9. End the administrator session. Subsequent host commands use the provisioner profile.

```sh
aws logout --profile tenant-ec2-admin
```

Warning: rotating a provisioner key can invalidate older sessions. Check the credential procedure before running its key command.

For optional external credential stores, use [home lab tools](options/home-lab.md).

## 3. Prepare the environment file

The development copy of `.env` supplies the secret.
The bootstrap renders a separate host copy from that secret.
Neither copy belongs in Git.

1. Restrict permissions for new files in this shell. The private environment file must not be readable by other users.

```sh
umask 077
```

2. Copy the sample. It defines each name without a secret value.

```sh
cp .env.example .env
```

3. Set the file mode. The file must have mode 0600.

```sh
chmod 600 .env
```

4. Create a dedicated SSH key pair. Keep the private key outside the clone.

```sh
ssh-keygen -t ed25519 -f "$HOME/.ssh/tenant-ec2"
```

5. Fill the host settings in `.env`. Use your own values rather than example names.

| Setting | Core value or required input |
| --- | --- |
| `EC2_OPTIONS` | Empty. |
| `EC2_DNS_PROVIDER` | `manual`. |
| `EC2_EDGE_NAMES` | `ok`. |
| `EC2_DOMAIN` | Your domain subtree. |
| `EC2_ACME_EMAIL` | Optional certificate contact address for the core. |
| `EC2_ADMIN_CIDR` | Your administrator IPv4 range, with prefix length 16 to 32. |
| `EC2_VPC_CIDR` | Your VPC range, with prefix length 16 to 26. Keep it outside `198.18.0.0/15`, which Compose uses. |
| `EC2_SSH_PUBLIC_KEY_FILE` | The absolute path of the new `.pub` file. |
| `EC2_SNAPSHOT_REPOSITORY` | The approved HTTPS clone URL without a credential. |
| `EC2_SNAPSHOT_TAG` | The approved portable tag. |
| `EC2_SNAPSHOT_COMMIT` | Its exact 40-digit commit. |
| `EC2_PI_DURABLE_MODEL` | `bedrock/luna`. |
| `EC2_PI_DURABLE_THINKING` | `xhigh`. |
| `EC2_OPENVIKING_EMBED_MODEL` | `bedrock/embed`. |
| `EC2_OPENVIKING_VLM_MODEL` | `bedrock/luna`. |
| `EC2_BEDROCK_EMBED_MODEL` | `amazon.titan-embed-text-v2:0`. |
| `EC2_OPENVIKING_EMBED_DIMENSION` | `1024`. |
| `EC2_ENABLE_BUCKET`, `EC2_ENABLE_OPERATIONS` | `false` until the provisioner and boundary allow them. |

6. Generate new core secret values using the [credential procedure](credentials.md). Do not reuse credentials from another install.
7. Leave the three generated service keys empty. The [bootstrap](reference/host-boot.md#environment-file-and-generated-keys) creates them after service start.

The core names include the gateway master key, salt, database password, UI password, OpenViking root key and agent bearer.
The Entra names are filled in the next section.
An empty option block does not need placeholder secrets.

For an older environment file, compare every name with `.env.example` before writing the secret.
Add `EC2_OPTIONS`, `EC2_DNS_PROVIDER`, `EC2_EDGE_NAMES`, `EC2_BEDROCK_EMBED_MODEL` and `EC2_PI_DURABLE_WEB_SEARCH`.
Add `EC2_OPENVIKING_EMBED_MODEL`, `EC2_OPENVIKING_VLM_MODEL` and `EC2_OPENVIKING_EMBED_DIMENSION`.
Add `EC2_OK_ENTRA_TENANT_ID`, `EC2_OK_ENTRA_GROUP_ID`, `EC2_OK_OIDC_CLIENT_ID`, `EC2_OK_OIDC_CLIENT_SECRET` and `EC2_OK_COOKIE_SECRET`.
The `secret` command stops before a write with one message that names each missing required name.
It also tests each nonempty form with the same tests as bootstrap step 2, before writing its JSON file or calling AWS.
A wrong form stops both `secret` and the bootstrap dry run with a nonzero status.
The message names the setting and its rule, not the value.
Missing values still give notes in the bootstrap dry run, so it can show the steps of an unfinished install.
Do not treat that dry run as proof that every required value is present.
For a private source, follow the [private source option](options/home-lab.md) instead of embedding a credential in a URL.

Warning: a secret value cannot contain a single quote or end with a backslash. The secret command refuses these values.

The [Pangolin](options/pangolin.md), [Copilot](options/copilot.md) and [DNS provider](options/dns.md) pages contain all option-specific steps.

## 4. Register the Entra application

The application belongs to your Entra tenant and serves this install only.
The redirect URI is `https://ok.<EC2_DOMAIN>/oauth2/callback` with your real domain subtree.
Use the [Entra procedure](entra.md) for the exact setting names and the supported automation commands.

1. Register a single-tenant web application. Use the exact HTTPS callback URI above.
2. Create a security group for allowed users. Record the group's object id, not its display name.
3. Add a test user to that group. This identity tests the allowed login path.
4. Configure the application's group claim and required consent. The gate must receive the allowed group's id.
5. Create a new application credential. Transfer its value directly to the private environment file and your secure store.
6. Generate the gate cookie secret as described on the Entra page. It must meet oauth2-proxy's size requirements.
7. Fill the Entra core names in `.env`. Use the tenant id, client id, application credential, group id and cookie secret.
8. Check the tenant, callback and group in the Entra admin centre. A successful create does not prove a successful login.

Alternatively, use `scripts/entra-app.sh apply` and `scripts/entra-app.sh secrets` as described on the [Entra page](entra.md).
For an existing registration with another name, give `apply --name <text>` its exact name.
For an existing group with another name, use `--group <text>`.
The script has no review command; `selftest` checks isolated stubs, not your registration.

The target of `ok` is always `ok-auth:4180`, never the OpenKnowledge service directly.
The gate uses the same login path with either edge.

## 5. Write the secret

1. Run the deploy dry run. Check the selected region, stack, snapshot and instance size.

```sh
scripts/aws-host.sh --dry-run deploy
```

2. Run the static checks. They render Compose with stubs, without reading private values or requiring the generated service keys.

```sh
sh scripts/check.sh
```

The final line must report a pass.
Do not invent temporary production keys merely to make a direct Compose render pass.

The next `secret` command includes the form test, so no separate validation step is required.
For a form-only test without a secret write or network call, run:

```sh
sh scripts/host-bootstrap.sh --check-forms .env
```

This command tests nonempty forms only; `secret` also checks required names and placeholders.
For example, a malformed administrator range produces this message and exits with status 1:

```text
host-bootstrap: error: EC2_ADMIN_CIDR must be an IPv4 address with a prefix length from 16 to 32
```

3. Write the Secrets Manager secret after approval. This first-boot form assumes a public portable snapshot.

```sh
scripts/aws-host.sh secret \
    --allow-empty EC2_OPENVIKING_LITELLM_API_KEY \
    --allow-empty EC2_PI_DURABLE_LITELLM_API_KEY \
    --allow-empty EC2_PI_DURABLE_OPENVIKING_API_KEY \
    --allow-empty EC2_SNAPSHOT_CLONE_TOKEN < .env
```

Omit the snapshot exception when using a private snapshot with a clone credential.
The secret must exist before the deploy because first boot reads it.
A core secret contains no DNS provider credential.

## 6. Deploy and set DNS

1. Run the deploy after approval. Inspect any change set before allowing a replacement.

```sh
scripts/aws-host.sh deploy
```

2. Read and write the supported stack outputs. The command reports the Elastic IP for DNS.

```sh
scripts/aws-host.sh outputs --write
```

3. Repeat the secret command from section 5. It preserves applicable output changes for the host.
4. Print the required DNS record. The manual provider changes no DNS zone.

```sh
scripts/aws-host.sh dns
```

5. Set the printed A record at your DNS provider. In the core, only `ok.<EC2_DOMAIN>` points to the Elastic IP.
6. Check the public DNS answer. It must match the Elastic IP before certificate issuance can complete.

Use the [DNS provider option](options/dns.md) only when you want the script to change DNS records.

Warning: `--replace-instance` loses root-volume files. `--allow-remove` can separate retained storage from the stack; see the [deploy guards](reference/deploy-script.md#deploy-and-update-guards).

## 7. Wait for boot and verify

User data checks the portable snapshot commit before it starts any kit script.
The bootstrap mounts storage, builds pinned kits, starts services and makes service keys.
Certificate issuance may need a bootstrap repeat after DNS becomes visible.

1. Open a Systems Manager session to the instance shown in the stack outputs. This gives a host shell without copying the provisioner key.
2. Read the first-boot log in that session. Resolve errors before calling the install complete.

```sh
sudo tail -n 80 /var/log/cloud-init-output.log
```

3. Repeat the bootstrap only if it stopped before DNS was ready. It checks existing state before repeating work.

```sh
sudo sh /opt/tenant-ec2/scripts/host-bootstrap.sh
```

4. Run the verify command from the development clone. Inspect each line of the result, not just the stack status.

```sh
scripts/aws-host.sh verify
```

5. Preserve the generated service keys through the [credential procedure](credentials.md). The bootstrap does not write them back to Secrets Manager.
6. Record the built image ids from the host lock in your controlled lock record. They identify the builds used by this install.

Not verified: the checks of `verify` pass on a host.
They use internal requests only, so they need no public name but the names that you select.
A skip line names each fact that `verify` does not prove: a login, a model answer, a public route.
Do not add a public route or disable a gate for a check.
See the [verify limits](reference/deploy-script.md#known-limits).

## 8. First login and security review

1. Open `https://ok.<EC2_DOMAIN>` with your actual domain subtree. The first request must enter the Entra login path.
2. Sign in as the allowed test user. The gate must admit that user to OpenKnowledge.
3. Test a user outside the allowed group in a separate browser session. The gate must refuse access.
4. Run the remaining [Entra checks](entra.md). They cover bypass paths and the agent's access to OpenKnowledge.
5. Install the kit's review command from the development clone. The setup keeps existing files and refuses a conflicting project destination.

```sh
sh skills/security-review/install.sh
```

6. Reload your coding agent as the [security review page](security-review.md) directs. Pi also requires approval of project resources.
7. Run `/security-review` from the clone. Check command precedence if a personal command has the same name.
8. Run the optional file reader for your private environment file. It prints derived facts, not values.

```sh
sh scripts/security-review.sh --env-file .env
```

9. Review the ranked suggestions. The advisory review blocks nothing, but it does not disable the secret scan or your approval rules.

Not verified: the live login, group refusal, model calls and agent memory calls. Run these checks in your approved install before use.
