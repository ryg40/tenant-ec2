# Home lab options

The core needs an AWS account, an Entra tenant and a domain. It needs no home network, external credential store or search service.

Each option needs a separate choice. An option never permits a production credential from another deployment on the cloud host.

## Newt site

A Newt site connects private targets to the optional Pangolin edge. Outbound initiation does not limit the targets that Newt can reach.

| Requirement | Rule |
| --- | --- |
| A named target | Create no site until one required target is known. A client of the public gateway needs no home site. |
| Network confinement | Use a separate network or VLAN. Permit outbound traffic only to the edge and the named target ports. |
| Newt settings | Set `DISABLE_CLIENTS=true` and `DISABLE_SSH=true`. Mount no Docker socket and do not use native mode. |
| Site identity | Create a separate site credential for each target group. Keep it outside the cloud environment file. |
| Verification | Test that a connection to an unlisted private address fails. Test that the named target still works. |

Warning: Control of the cloud Pangolin server lets an attacker select Newt targets. The home firewall must enforce the target limit.

Not verified: Newt confinement or any tunnel connection. This page installs no home service.

## Agent web search

The basic agent has no web search. An optional search backend needs a reachable endpoint and its own bearer credential.

Set `EC2_PI_DURABLE_WEB_SEARCH=mcp` to select search.
Set `EC2_PI_DURABLE_WEB_SEARCH_MCP_URL` and `EC2_PI_DURABLE_WEB_SEARCH_MCP_TOKEN` for that backend.
The core value `EC2_PI_DURABLE_WEB_SEARCH=off` reads neither backend value.

Keep both empty when search is off. Do not use a dead address as the off switch.

| Requirement | Rule |
| --- | --- |
| Endpoint | Use a service dedicated to this agent. Permit only the required network path. |
| Credential | Give the agent a scoped bearer, not an administrator key. |
| Content | Treat retrieved pages as untrusted input, not instructions. |
| Cost | Limit request rates and the model budget. |

Warning: Search content can contain instructions that try to misuse the agent's memory and knowledge tools.

Not verified: search disabled behavior in the pending basic-agent release, or a request to a live search backend.

## Credential stores

A profile without a credential helper defaults to `EC2_CREDENTIAL_STORES=aws`. Only its AWS CLI profile stores the key.

`EC2_CREDENTIAL_STORES=home-lab` enables the optional shell save command, Bitwarden integration and Vault copy. Use this setting for `key` and `vault`.

If a profile has a helper, `key` requires an explicit choice: `EC2_CREDENTIAL_STORES=aws` or `EC2_CREDENTIAL_STORES=home-lab`.

Without that choice, it stops before any key operation. The default cannot move a helper profile to another store by accident.

| Setting | Purpose | Required scope |
| --- | --- | --- |
| `EC2_SECRET_SAVE` | Save command; default `bw-session-update.sh`. It reads a name argument and the value from stdin. | Save only the two provisioner names. |
| `TENANT_EC2_AWS_ACCESS_KEY_ID` | Optional stored key ID. | The scoped provisioner, never the administrator. |
| `TENANT_EC2_AWS_SECRET_ACCESS_KEY` | Optional stored secret key. | The same provisioner identity. |
| `EC2_VAULT_SECRET` | Optional KV v2 destination, separate from the workload secret. | An exact path readable only by provisioning operators. |
| `VAULT_PROVISIONER_TOKEN` | Token read by the optional copy helper. | Write and read that secret only. Never use a root token. |
| `VAULT_ADDR` | Installer-selected Vault service. | Keep the service off public workload routes. |

The optional mode saves both values, writes a `credential_process` profile, deletes older keys, then writes and compares the Vault copy.

A new home-lab profile embeds `EC2_CREDENTIAL_STORES=home-lab` in its helper command. The helper also works without that setting.

A missing Vault token leaves no Vault copy. The script reports this condition. Treat the optional storage setup as incomplete until the copy succeeds.

Existing helper profiles work unchanged. No migration is necessary. The `credential-process` command is a read path and writes no store.

Do not run `credential-process` in a transcript. It returns credentials to the AWS CLI through stdout.

Warning: A login shell can load unrelated secrets. Run this option in a dedicated environment with only the required credentials.

Warning: Key rotation deletes older keys after the local save. It does not wait for the Vault copy.

Not verified: real store writes, a live helper call, or a Vault policy that denies access from a workload reader.

## Private snapshot source

The host clones the portable snapshot before it can run the bootstrap.
Use `EC2_SNAPSHOT_REPOSITORY` for its HTTPS URL without a credential.
Set `EC2_SNAPSHOT_TAG` and `EC2_SNAPSHOT_COMMIT` to the reviewed tag and commit.
For a private repository, set `EC2_SNAPSHOT_CLONE_TOKEN` to a new read-only credential for that repository only.
Write the host secret before the first deploy, without an empty-value exception for this name.
User data reads the credential through the instance role and supplies it through `GIT_ASKPASS`.
A public repository needs no clone credential.

Not verified: a private snapshot clone through this helper on a host.

## Private kit source

A source override changes the clone location, not the reviewed tag or commit in `kits.lock`.

| Requirement | Rule |
| --- | --- |
| Source URL | Set `EC2_KIT_SOURCE_<KIT>` to a reviewed HTTPS repository. Put no credential in the URL. |
| Clone credential | Set `EC2_KIT_TOKEN_<KIT>` to a read-only credential for one repository, with an expiry date. |
| Source identity | Check the tag against the full commit in the lock before a build. |
| Secret delivery | Use the bootstrap's credential helper. Put no token in a command argument or log. |
| Retention | Review removal from the environment and host secret after the build. |

Warning: A broad repository token can expose private development history. Never reuse a token from another deployment.

Not verified: a private clone or removal of build credentials after use.
