# Options

[Front page](../../README.md) | [Core install](../install.md) | [Architecture](../architecture.md)

These options belong to `EC2_VARIANT=homelab` only.
The [Mac variant](../variants.md) is a standalone design, not another option or overlay.
It requires empty `EC2_OPTIONS` and `EC2_EDGE_NAMES` and has no public DNS command.

The homelab core needs no option.
`EC2_OPTIONS` accepts a comma-separated list with no blanks.
The known names are `pangolin` and `copilot`; the default is empty.
An unknown name stops the scripts.

| Option | Switch | Purpose |
| --- | --- | --- |
| [Pangolin](pangolin.md) | `EC2_OPTIONS=pangolin` | Replaces Caddy with Pangolin, Gerbil and Traefik. Adds tunnel ports and a blueprint. Needs Porkbun for its certificate. |
| [Copilot](copilot.md) | `EC2_OPTIONS=copilot` | Adds the Copilot service and the `copilot/luna` and `copilot/embed` routes. |
| Both | `EC2_OPTIONS=pangolin,copilot` | Selects both overlays without editing Compose files. |
| [DNS provider](dns.md) | `EC2_DNS_PROVIDER` | `manual` prints the records; `porkbun`, `route53` and `cloudflare` manage them. |
| [Home lab tools](home-lab.md) | Settings on that page | Adds a Newt site, agent web search, optional credential stores or private kit sources. |

## Related core settings

`EC2_EDGE_NAMES=ok` is the default with either edge.
The list accepts `ok`, `agent`, `llm` and `ov`, with commas and no blanks.
It selects which names receive routes and A records.
The Pangolin option also adds the name `pangolin`.

With Caddy, each selected name other than `ok` answers only `EC2_ADMIN_CIDR` and keeps its service key check.
With Pangolin, `ok` still uses the Entra gate, not a second Pangolin login.
Do not enable another name without reviewing its path rules.

`EC2_ENABLE_BUCKET` and `EC2_ENABLE_OPERATIONS` are template switches, not names in `EC2_OPTIONS`.
Keep them off until the provisioner and boundary have their required rights.
See the [host template](../reference/host-template.md).

## Environment file blocks

The sample environment file groups values by purpose:

| Marker | Requirement |
| --- | --- |
| `# ===== Part 1: the names of the core =====` | Required core names, subject to the documented first-boot exceptions. |
| `# ===== Part 2: settings =====` | Settings and portable defaults. |
| `# ===== Part 3: the names of the options =====` | Option, DNS provider and Mac variant blocks. |
| `# ----- Variant macos -----` | Read only with `EC2_VARIANT=macos`. See [Variants](../variants.md#prepare-and-install). |
| `# ----- Option pangolin -----` | Empty names are required only when Pangolin is selected. |
| `# ----- Option copilot -----` | Empty names are required only when Copilot is selected. |
| `# ----- DNS provider porkbun -----` | Read by `dns` for Porkbun. Also sent to the host only when Pangolin needs DNS-01. |
| `# ----- DNS provider cloudflare -----` | Read by `dns` for Cloudflare; kept off the host. |
| `# ----- DNS provider route53 -----` | Read by `dns` for Route 53; kept off the host. |

The [deploy reference](../reference/deploy-script.md) explains secret filtering.
The [Compose reference](../reference/compose-stack.md) lists the four file sets.
AgentCore is not a homelab option; the Mac design uses separate stage admission and a pending compatible agent kit.
OpenKnowledge model calls remain outside both designs.
