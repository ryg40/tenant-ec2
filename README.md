# tenant-ec2

An install kit for one Amazon Linux 2023 x86_64 EC2 host.
CloudFormation makes the host, its network, its storage and its identity.
Docker Compose runs the services from the tags and commits in [kits.lock](kits.lock).

The core needs an AWS account, a Microsoft Entra tenant and a domain name.
It needs no home lab, Copilot account or DNS provider key.

![The core AWS components](docs/diagrams/01-aws-core.svg)

Open the [interactive architecture page](docs/architecture.html) for all five diagrams as one graph, with search, tours and a path finder.

Read the [architecture](docs/architecture.md) for the service networks, the login path and the install flow.

## The core

| Part | Content |
| --- | --- |
| AWS | The provisioner, host-role boundary, VPC, security groups, EC2 instance, Elastic IP, EBS data volume, EFS access points and Secrets Manager secret. |
| Gateway | LiteLLM and Postgres, with `bedrock/luna` at `xhigh` and `bedrock/embed`. The instance role supplies the AWS credential. |
| Memory | OpenViking, with embeddings and vision through the gateway. |
| Knowledge | OpenKnowledge behind the Microsoft Entra ID gate. |
| Agent | A basic pi-durable agent, with OpenViking memory and OpenKnowledge. No web search or desktop part. |
| Core edge | Caddy on ports 80 and 443, with a certificate for each selected name. Only `ok` is public by default. |
| Security review | `/security-review` gives suggestions. It does not block the install. The secret scan remains a gate. |

## The options

An option stays off until you select it. Do not edit a Compose file to select an option.

| Option | Switch | Page |
| --- | --- | --- |
| Pangolin edge | Add `pangolin` to `EC2_OPTIONS`. It replaces the core edge and needs `EC2_DNS_PROVIDER=porkbun`. | [Pangolin](docs/options/pangolin.md) |
| Copilot model routes | Add `copilot` to `EC2_OPTIONS`. | [Copilot](docs/options/copilot.md) |
| DNS provider | Set `EC2_DNS_PROVIDER` to `manual`, `porkbun`, `route53` or `cloudflare`. The default is `manual`. | [DNS](docs/options/dns.md) |
| Home lab tools | Optional Newt site, web search, credential stores and private kit sources. | [Home lab](docs/options/home-lab.md) |

## Quick path

Follow the [install guide](docs/install.md) for the commands and checks.

1. Clone a portable snapshot at its tag. Install and check the Git hooks.
2. Prepare the provisioner. Its key stays off the host.
3. Fill the environment file. Register the Entra application and its group gate.
4. Write the Secrets Manager secret. The first boot reads it.
5. Deploy the stack after approval. Set the DNS record to the Elastic IP.
6. Wait for the bootstrap. Run the checks and complete the first login.
7. Run the advisory security review. Review each suggestion before public use.

Warning: a deploy creates billed AWS resources. No command on this page authorizes a deploy.

Not verified: a complete live install of the core. The reference pages keep the specific limits of the checks.
The lock pins pi-durable at `portable-v0.1.0`; the bootstrap includes the agent, and `verify` requires its running service.
Its source is private. Set `EC2_KIT_TOKEN_PI_DURABLE` as the [private kit source procedure](docs/options/home-lab.md#private-kit-source) describes.
Do not deploy when a required kit has no pin in the lock.
Not verified: a private agent-kit clone or a complete install with the agent on the host.

## Pages

| Page | Content |
| --- | --- |
| [Install](docs/install.md) | The core install, in order. |
| [Architecture](docs/architecture.md) | Five diagrams, with their Excalidraw scenes. |
| [Entra gate](docs/entra.md) | The application registration, group rule and login checks. |
| [Options](docs/options/README.md) | The switches and the option pages. |
| [Host template](docs/reference/host-template.md) | The AWS components, parameters and storage rules. |
| [Compose stack](docs/reference/compose-stack.md) | The services, file sets, networks and model routes. |
| [Host boot](docs/reference/host-boot.md) | The user data, bootstrap, generated keys and repeat runs; [move to a later snapshot](docs/reference/host-snapshot-move.md). |
| [Deploy script](docs/reference/deploy-script.md) | Commands, update guards and known limits. |
| [DNS migration](docs/reference/dns-migration.md) | The move of the DNS zone of a domain to Porkbun, with each record, before the Pangolin certificate. |
| [Host domain move](docs/reference/host-domain-move.md) | The move of the running host under a new domain subtree, with each check and each way back. |
| [Checks](docs/reference/checks.md) | Source checks, dry runs, local gate tests, diagrams and hook behavior. |
| [Credentials](docs/credentials.md) | The names, stores and provisioner procedure. |
| [Security review](docs/security-review.md) | The advisory review and the public risk list. |
| [Glossary](GLOSSARY.md) | The words of the install kit. |
| [Branches](docs/branches.md) | The development branch and portable snapshots. |
| [Remotes](docs/remotes.md) | What each remote receives. |
| [Publishing](docs/publishing.md) | The portable snapshot procedure. |
| [Secret handling](docs/secret-handling.md) | The scan, hooks and deny lists. |
| [Agent rules](AGENTS.md) | Rules for an agent in this repository. |
| [Kit lock](kits.lock) | The pinned kit sources, builds and checks. |

The private development checkout holds the original requests and planning record.
The detailed security register stays in the private development checkout.
docs/agents/issue-tracker.md holds the development work contract, not an install step.
