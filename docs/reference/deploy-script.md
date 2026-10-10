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
| `outputs` | Prints stack outputs as `NAME=value`. `--write` updates supported output names in the environment file. |
| `dns` | Prints or sets the selected A records through `scripts/dns-records.sh`. |
| `dns --plan` | Reads the Elastic IP from the stack and prints the record set. It changes nothing and calls no provider. |
| `verify` | Shows stack status, then runs `docker compose ps`, the file set check, the service list and `scripts/verify-deployed.py` on the host through SSM. No model request runs. |

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

The Pangolin option sets `EnablePangolin=true` and requires the Porkbun provider.
Each command stops when `EC2_OPTIONS` holds `pangolin` and `EC2_DNS_PROVIDER` is not `porkbun`.
Other options do not open the tunnel ports.
A change of `EC2_OPTIONS` needs `deploy` for the security group, then `secret` and a new run of the bootstrap for the host.
See the [option index](../options/README.md).

## Secret

The command reads stdin only:

```sh
scripts/aws-host.sh secret < .env
```

It writes one JSON object with names as keys, including settings.
It excludes each name of a DNS provider block, except the Porkbun pair when Pangolin needs DNS-01 certificates.
It prints each name that it excludes.
The user data and bootstrap read this JSON form.

An empty required core name or placeholder stops the command before any write.
A core name is a name of Part 1 of the sample environment file.
The same rule applies to empty names in an active option block, and to the Porkbun pair with Pangolin.
An inactive option does not require its credentials.
Only `dns` requires the selected DNS provider block, except for the Porkbun pair with Pangolin.

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
The name must be a name of Part 1, a name of an option block or one name of the Porkbun pair.
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

An update first creates and prints a change set.
A `Replacement` value of `True` or `Conditional` counts as a replacement.
The script deletes and refuses the change set unless you approve each applicable case:

| Guard | Required flag |
| --- | --- |
| Replacement of `Instance`. | `deploy --replace-instance` |
| Removal of `Instance`, `DataVolume`, `FileSystem` or `ElasticIp`. | `deploy --allow-remove` |
| Replacement of `DataVolume` or `FileSystem`. | `deploy --allow-remove` |

Each flag accepts only its own case.
A change set that meets both cases needs both flags.

Warning: `--replace-instance` loses every file on the root volume. The Elastic IP, data volume and file system stay.

Warning: `--allow-remove` can detach retained storage from the stack. Replacement storage starts empty even when the old storage stays in the account.

Keep `EC2_ENABLE_BUCKET` and `EC2_ENABLE_OPERATIONS` at `false` until the provisioner and boundary have their required rights.
A deploy with either switch on fails without those rights.

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
A provider credential stays on the development machine unless Pangolin needs its Porkbun pair for DNS-01.
See [DNS providers](../options/dns.md) for profiles, scopes and provider behavior.
See [Pangolin](../options/pangolin.md) for the Porkbun request handling and certificate exception.

## Verify and dry run

`verify` sends one SSM command through `AWS-RunShellScript`.
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

1. Fill `.env`. The [install guide](../install.md) explains the core values.
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
The first snapshot starts no agent service. A later snapshot adds the pinned agent.
Not verified: this skip path in an SSM command on Amazon Linux 2023. Offline stubs prove the command logic only.
