# Credentials and settings

The sample environment file lists the names that the stack reads. Each secret starts empty. Each deployment uses new credentials.

Keep the environment file private with mode 0600. Send its content to the deploy script through stdin, never as an argument.

The host uses its instance role for AWS calls. Never give it the provisioner access key or an administrator session.

## The AWS provisioner

`scripts/aws-provisioner.cfn.yaml` defines the provisioner and its permissions boundary. The boundary limits the roles that the provisioner creates.

A profile without a credential helper defaults to `EC2_CREDENTIAL_STORES=aws`. It stores the access key in the AWS CLI profile only.

That mode writes no helper line, shell exports or external copy. Existing `credential-process` helper lines work unchanged, without a storage setting or migration.

If a profile has a helper, `key` stops before any key operation unless you explicitly select `aws` or `home-lab`.

| Command | Purpose and scope |
| --- | --- |
| `scripts/aws-provisioner.sh deploy` | Create the IAM stack with the administrator profile. Run only with approval. |
| `scripts/aws-provisioner.sh key` | Make a key, save the profile, then delete older keys. Run only with approval. |
| `scripts/aws-provisioner.sh verify` | Test the profile rights. This command creates and deletes test roles. |
| `scripts/aws-provisioner.sh selftest` | Test storage with isolated stubs. It calls no cloud or real secret store. |

The script uses the normal AWS configuration and credentials files. `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` can select different files.

It keeps unrelated profiles. It removes conflicting credential settings from the selected profile. Each file has mode 0600 after the write.

Warning: Changing storage mode changes the selected profile. Keep a working administrator session until verification finishes.

| Setting | Purpose | How to choose it and smallest scope |
| --- | --- | --- |
| `EC2_PROJECT` | Resource name prefix; default `tenant-ec2`. | Use the prefix of the provisioner template. |
| `EC2_AWS_PROFILE` | Provisioner profile; default is the project name. | Use only the scoped provisioner identity. |
| `EC2_AWS_ADMIN_PROFILE` | IAM bootstrap profile; default ends in `-admin`. | Use a temporary administrator session, not the root user. |
| `EC2_CREDENTIAL_STORES` | Storage mode: `aws` or `home-lab`. | Keep `aws` for the core. The [home lab page](options/home-lab.md) describes the optional mode. |

Not verified: a live helper call or a live key rotation with this version. Isolated stubs prove that the old helper needs no migration.

## Core secret names

A virtual key is a gateway credential limited to selected model routes. Make a separate virtual key for each service.

Use `openssl rand -hex 32` for a new random secret unless its row requires another format. Do not put command output in a transcript.

| Name | Purpose | How to make it and smallest scope |
| --- | --- | --- |
| `EC2_LITELLM_MASTER_KEY` | Gateway administration. | Generate a random secret. Give it only to the gateway and the bootstrap. |
| `EC2_LITELLM_SALT_KEY` | Gateway encryption salt. | Generate a separate random secret. Keep it stable while encrypted database values exist. |
| `EC2_LITELLM_POSTGRES_PASSWORD` | Gateway database login. | Generate a separate random secret. Use it only for this database user. |
| `EC2_LITELLM_UI_PASSWORD` | Gateway UI login. | Generate a separate random secret. Keep the UI off public model routes. |
| `EC2_OPENVIKING_ROOT_API_KEY` | Memory server administration. | Generate a random secret. Do not give it to the agent. |
| `EC2_OPENVIKING_LITELLM_API_KEY` | Memory embedding and vision calls. | The bootstrap creates it through `/key/generate` on the gateway. It allows each active embedding and Luna route. |
| `EC2_PI_DURABLE_API_KEY` | Agent client bearer. | Generate a random secret. Give it only to clients of this agent. |
| `EC2_PI_DURABLE_LITELLM_API_KEY` | Agent model calls. | The bootstrap creates this virtual key. It allows each active Luna route and sets no budget; set a budget in the gateway when you need a spending limit. |
| `EC2_PI_DURABLE_OPENVIKING_API_KEY` | Agent memory calls. | The bootstrap creates one agent account through the OpenViking admin API and uses its user key, not the root key. |
| `EC2_OK_ENTRA_TENANT_ID` | Login tenant identifier. Not a secret. | `scripts/entra-app.sh apply` writes it. Use one tenant, not the multi-tenant `common` issuer. |
| `EC2_OK_ENTRA_GROUP_ID` | Object id of the one permitted security group. Not a secret. | `scripts/entra-app.sh apply` creates the group and writes its id. Only a member of this group passes the gate. |
| `EC2_OK_OIDC_CLIENT_ID` | Login application identifier. Not a secret. | `scripts/entra-app.sh apply` creates a dedicated single-tenant application with the callback `https://ok.<domain>/oauth2/callback` and writes its id. |
| `EC2_OK_OIDC_CLIENT_SECRET` | Login application credential. | `scripts/entra-app.sh secrets` creates it. Its default life is 180 days: renew it before its end date. Give it only to the gate. |
| `EC2_OK_COOKIE_SECRET` | Login cookie encryption. | `scripts/entra-app.sh secrets` generates it: 32 random bytes in URL-safe base64. The output of `openssl rand -base64 32` alone is not valid: with a `+` or a `/` in it, the gate does not start. Use it only for this gate. |

The five gate names are core names. The three identifiers are not secrets, but they identify a deployment: keep them out of tracked files.

The gate checks the group itself. `scripts/entra-app.sh apply --assign-group` also requires application assignment in Entra ID. That second lock needs an Entra ID P1 or P2 licence.

The script writes into one environment file only: `.env`, or the file of `--env-file`. It refuses the sample file `.env.example`. The [Entra page](entra.md) has the same steps by hand, the form of the cookie secret and the renewal of each secret.

Not verified: a live app registration or a live group login. No call to Azure ran.

## Generated-key storage

`<Project>/env` holds installer configuration. `<Project>/generated` holds only the three bootstrap-generated service keys in the table above.
The host can read both secrets. It can write versions of the generated secret only; it cannot create secrets.
The role and its boundary must both allow `PutSecretValue` on that secret before write-back works.

The bootstrap saves the keys after creation. A failed put warns, keeps the mode 0600 host file and continues.
A repeat prefers the generated secret, then the environment secret, then the existing host file.
A missing or empty generated secret permits creation of the missing service keys.
`scripts/aws-host.sh secret` fills only empty generated names from the generated secret before its normal environment-secret write.
An explicit nonempty private-file value stays unchanged. For rotation, reconcile both secrets and the service database under a separate approval.

`scripts/aws-host.sh generated-status` prints the three names with `present` or `absent`, never a value.
`verify` requires all three names when `<Project>/generated` exists; without that secret it prints a skip line. Presence proves neither credential validity nor agreement with a service database.
Use a private Secrets Manager read for approved external copies. Never print a key into Systems Manager output or a transcript.
The kit does not write Bitwarden or Vault during bootstrap.

Not verified: the live template and boundary apply, or the first write-back on a host.

## Core settings

These values are not credentials. They still identify a deployment and must stay out of tracked examples.

| Name | Purpose | How to choose it and scope |
| --- | --- | --- |
| `EC2_DOMAIN` | Domain subtree for service names. | Use a domain you control. Put only the needed records below it. |
| `COMPOSE_PROJECT_NAME` | Compose resource prefix. | Use a name unique on the machine. |
| `COMPOSE_FILE` | Active Compose file set. | Let the bootstrap derive it from `EC2_OPTIONS`. For `macos`, the secret writer sets `compose.macos.yaml`. |
| `EC2_TZ` | Container time zone. | Keep `Etc/UTC` unless the deployment needs another zone. |
| `EC2_LITELLM_UI_USERNAME` | Gateway UI user. | Choose a deployment-specific user. This is not a model key. |
| `EC2_AWS_REGION` | AWS region. | Select a region allowed by the provisioner and the model. |
| `EC2_STACK_NAME` | Host stack name. | Keep the provisioner prefix. |
| `EC2_AMI_SSM_PARAMETER` | Amazon Linux image parameter. | Use the public x86_64 parameter from the template. |
| `EC2_INSTANCE_TYPE` | Host instance size. | Choose enough memory for all active services. |
| `EC2_BEDROCK_LUNA_MODEL` | Bedrock inference profile. | Use the profile allowed by the instance role. Keep reasoning effort at `xhigh`. |
| `EC2_BEDROCK_EMBED_MODEL` | Bedrock embedding model. | Use the model allowed by the role; the core selects Titan Text Embeddings V2. |
| `EC2_VPC_CIDR` | VPC address range. | Choose a private range outside the Compose network range. |
| `EC2_SNAPSHOT_REPOSITORY` | Snapshot clone URL. | Use HTTPS without a credential in the URL. |
| `EC2_SNAPSHOT_TAG` | Immutable snapshot tag. | Use an approved portable release. |
| `EC2_SNAPSHOT_COMMIT` | Snapshot commit check. | Copy the full commit ID of that tag. |
| `EC2_SSH_PUBLIC_KEY_FILE` | Administrator public key input. | Generate a new key pair for this deployment. Keep the private key off the host. |
| `EC2_DATA_VOLUME_SIZE` | Data volume size in GiB. | Size it for databases and token files. |
| `EC2_ENABLE_BUCKET` | Optional private S3 bucket. | Turn it on only when the provisioner allows its resources. |
| `EC2_ENABLE_OPERATIONS` | Snapshots, logs and alarm resources. | Turn it on only when the provisioner allows them. |
| `EC2_ALARM_EMAIL` | Billing alarm recipient. | Choose an attended mailbox. Confirm the subscription. |
| `EC2_ALARM_THRESHOLD` | Billing alarm limit in USD. | Select a limit below the largest acceptable bill. |
| `EC2_BUCKET` | Bucket name from stack outputs. | Let `outputs --write` set it. Never add an account ID as a default. |
| `EC2_PI_DURABLE_MODEL` | Agent gateway route. | The core uses `bedrock/luna`. Select a Copilot route only with that option. |
| `EC2_PI_DURABLE_THINKING` | Agent reasoning effort. | Keep `xhigh`. |
| `EC2_OPENVIKING_EMBED_MODEL` | Memory embedding route. | The core uses `bedrock/embed`. |
| `EC2_OPENVIKING_VLM_MODEL` | Memory vision route. | The core uses `bedrock/luna`. |
| `EC2_OPENVIKING_EMBED_DIMENSION` | Embedding vector size. | Match the selected model; the core selects 1024. |
| `EC2_ACME_EMAIL` | Certificate account contact. | Choose an attended mailbox. The core needs no DNS credential. |
| `EC2_ADMIN_CIDR` | Administrator source range. | Use the smallest range that reaches SSH and restricted routes. |
| `EC2_OPTIONS` | Named options, separated by commas. | Empty selects the core. Known options are `pangolin` and `copilot`. |
| `EC2_EDGE_NAMES` | Public service names. | Default `ok`; add only needed names from `agent`, `llm` and `ov`. |
| `EC2_DNS_PROVIDER` | Record provider. | Keep `manual`, or select `porkbun`, `route53` or `cloudflare`. |

## Optional names

An option needs its values only when enabled. DNS credentials stay on the development machine, except the Porkbun pair for Pangolin DNS-01.

| Name | Purpose | How to make it and smallest scope |
| --- | --- | --- |
| `EC2_COPILOT_MASTER_KEY` | Copilot service bearer. | Generate a random secret. Give it only to Copilot and the gateway. |
| `EC2_PANGOLIN_SERVER_SECRET` | Pangolin server secret. | Generate a random secret. Keep it with the server database backup. |
| `EC2_PANGOLIN_API_KEY` | Blueprint apply. | Make a key in the Pangolin dashboard for the selected organization only. |
| `EC2_PANGOLIN_ORG` | Blueprint organization. | Use the organization created during initial setup. |
| `EC2_PANGOLIN_SITE` | Local target site. | Use the local site identifier from Pangolin. |
| `EC2_GERBIL_BASE_ENDPOINT` | Public tunnel endpoint. | Use the Elastic IP or its DNS name. Never use a home address as a default. |
| `EC2_PORKBUN_API_KEY` | DNS API key. | Create a new pair for the one domain and DNS operations only. Restrict source addresses where possible. |
| `EC2_PORKBUN_SECRET_KEY` | DNS API secret. | Use the matching pair. It can still change each record of the authorized domain. |
| `EC2_CLOUDFLARE_API_TOKEN` | DNS API credential. | Allow DNS edits on one zone only. Keep it on the development machine. |
| `EC2_CLOUDFLARE_ZONE_ID` | DNS zone identifier, not a secret. | Use the one zone of the token. Keep it off the host. |
| `EC2_ROUTE53_PROFILE` | Installer AWS profile name, not a secret. | Allow record reads for one zone and UPSERT of the selected A records only. Use no provisioner profile. |
| `EC2_ROUTE53_HOSTED_ZONE_ID` | DNS hosted zone identifier, not a secret. | Use the one zone allowed by the profile. Keep it off the host. |
| `EC2_KIT_SOURCE_PI_DURABLE` | Private source override. | Use a reviewed HTTPS repository URL without credentials. Keep the lock pin unchanged. |
| `EC2_KIT_TOKEN_PI_DURABLE` | Private kit clone credential. | Use read-only access to that repository only, with an expiry date. |
| `EC2_SNAPSHOT_CLONE_TOKEN` | Private snapshot clone credential. | Use read-only access to that repository only. Leave it empty for a public source. |
| `EC2_KIT_SOURCE_LITELLM` | Gateway source override. | Use a reviewed mirror that holds the same tag and commit. |
| `EC2_KIT_SOURCE_OPENVIKING` | Memory source override. | Use a reviewed mirror that holds the same tag and commit. |
| `EC2_KIT_SOURCE_OPEN_KNOWLEDGE` | Knowledge source override. | Use a reviewed mirror that holds the same tag and commit. |
| `EC2_PI_DURABLE_WEB_SEARCH_MCP_URL` | Optional web search endpoint. | Use a trusted endpoint reachable from the agent. Leave it empty for the basic agent. |
| `EC2_PI_DURABLE_WEB_SEARCH_MCP_TOKEN` | Optional web search bearer. | Make a credential for this agent only. Leave it empty when search is off. |

The provider and gate changes can add names. Their environment blocks define those names before the release candidate publishes this reference.

The Copilot device login, Pangolin administrator password and Newt site credential are not environment names of the core.

DNS provider names stay off the host, except the Porkbun pair with `pangolin`.
The [DNS page](options/dns.md) gives the required provider rights.

Warning: a private clone token can remain in the host secret. Limit its scope and review removal after use.
Warning: Pangolin needs the Porkbun pair for certificate renewal. Keep its scope limited to the required domain.

## Owner records

The private development checkout keeps the original requests and credential inventory.

The portable reference holds no inventory of another deployment and no secret value.
