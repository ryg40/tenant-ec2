# Pangolin edge

[Options](README.md) | [Core install](../install.md) | [DNS providers](dns.md)

Pangolin replaces the core edge; it does not run beside Caddy.
Set `EC2_OPTIONS=pangolin`, or add `pangolin` to a list that already contains `copilot`.
Set `EC2_DNS_PROVIDER=porkbun` or `EC2_DNS_PROVIDER=cloudflare`: the provider that answers for the apex domain.
Traefik gets the DNS-01 wildcard certificate through that provider.

The overlay `compose.pangolin.yaml` adds `pangolin`, `gerbil` and `traefik`.
Gerbil publishes 80, 443, 51820/udp and 21820/udp.
Traefik uses Gerbil's network namespace.
Gerbil keeps `NET_ADMIN`; the services otherwise drop capabilities unless explicitly required.
`EnablePangolin` opens the two UDP ports in the host security group only when this option is active.

## Files and directories

| File | Content |
| --- | --- |
| `pangolin/config/config.yml` | Pangolin configuration. |
| `pangolin/config/traefik/traefik_config.yml` | Entry points, Badger plugin and DNS-01 resolver for the wildcard certificate. |
| `pangolin/config/traefik/dynamic_config.yml` | Dashboard routes restricted to the administrator range. |
| `pangolin/blueprint.yaml` | Resources `llm`, `ok`, `ov` and `agent`, their targets and path rules. |
| `scripts/render-pangolin.sh` | Renders the four files from the environment file and creates the edge directories. |

The source files hold references such as `${EC2_DOMAIN}`.
Pangolin and Traefik do not expand those references themselves.
The renderer writes the result below `/srv/data/pangolin` before the bootstrap starts the stack.

| Directory | Consumer and data |
| --- | --- |
| `/srv/data/pangolin/config` | Pangolin configuration and data. |
| `/srv/data/pangolin/gerbil` | The Gerbil key. |
| `/srv/data/pangolin/traefik` | Traefik files and certificate store. |

Each edge service mounts only its own directory.
The renderer writes no secret into these files.
Compose supplies the Pangolin secret to `pangolin` and the credential of the DNS provider to `traefik`.
The renderer writes `EC2_DNS_PROVIDER` as the `provider` of the resolver in the Traefik file.

## Names, gates and DNS

`EC2_EDGE_NAMES` selects the blueprint resources. The sample value is `ok`; with Pangolin, an empty value stops the render.
The DNS record set adds `pangolin` for the dashboard.
The domain subtree has the form `<label>.<domain>`.
The DNS provider manages records in the parent domain's zone.
It makes no wildcard DNS record, even though the certificate is a wildcard certificate.

The dashboard answers only `EC2_ADMIN_CIDR`.
The `ok` resource targets `ok-auth:4180`; the Entra gate is its only login.
The gateway's five model paths use virtual keys.
Other gateway paths require the Pangolin login.
The agent's two refused paths remain refused.

The blueprint gives `agent` and `ov` no key-only API access: both resources ask for the Pangolin login on each path.
Not verified: this login rule on a running Pangolin. The full edge rules of the two names need a separate review before use.

The provider reads `EC2_PORKBUN_API_KEY` and `EC2_PORKBUN_SECRET_KEY` from the environment file, not the shell.
These values go into each request body through a mode 0600 temporary file, not an address or command argument.
The provider reads existing records first, creates absent records and changes records with a different address.
A correct record causes no write.
A reply without a record list stops the command.

This option is the exception to the DNS credential rule.
The host credential of its DNS provider also enters the host secret because Traefik needs DNS-01 at certificate renewal.

| Provider | Host names | Names that Traefik reads | Scope |
| --- | --- | --- | --- |
| `porkbun` | `EC2_PORKBUN_API_KEY`, `EC2_PORKBUN_SECRET_KEY` | `PORKBUN_API_KEY`, `PORKBUN_SECRET_API_KEY` | The whole Porkbun domain. |
| `cloudflare` | `EC2_CLOUDFLARE_DNS01_TOKEN` | `CF_DNS_API_TOKEN`, `CF_ZONE_API_TOKEN` | One zone; permissions Zone Read and DNS Edit. |

With `cloudflare`, `EC2_CLOUDFLARE_API_TOKEN` and `EC2_CLOUDFLARE_ZONE_ID` stay on the development machine for the `dns` command.
The host token is a new token for the host only.
The page [DNS providers](dns.md#the-option-pangolin) gives the steps to make it.

Warning: a DNS key on the public host can change the records of its zone. Give it the smallest scope: one domain at Porkbun, one zone at Cloudflare.

## Select before a deploy

1. Set `EC2_OPTIONS` in `.env` to include `pangolin`. The bootstrap selects the Pangolin overlay instead of Caddy.
2. Set `EC2_DNS_PROVIDER` to `porkbun` or `cloudflare`. Other providers do not satisfy the certificate configuration.
3. Fill the Pangolin block and the host names of the provider in `.env`. Keep the integration API key empty until initial setup.
4. Set `EC2_ACME_EMAIL` and `EC2_ADMIN_CIDR`. The renderer requires a certificate contact and an administrator range.
5. Review the selected names in `EC2_EDGE_NAMES`. Only those resources must be enabled.
6. Run the renderer dry run. It checks the edge values without writing rendered files.

```sh
sh scripts/render-pangolin.sh --env-file .env --out /tmp/pangolin-render --dry-run
```

7. Follow the [core install](../install.md) from the secret step. Add exceptions for the empty integration key and organization until initial setup finishes.

The additional first-boot arguments are `--allow-empty EC2_PANGOLIN_API_KEY --allow-empty EC2_PANGOLIN_ORG`.

The template output write sets `EC2_GERBIL_BASE_ENDPOINT` to the Elastic IP.
Without a value, the renderer uses `pangolin.` followed by `EC2_DOMAIN`.
The local site identifier comes from `EC2_PANGOLIN_SITE`; the organization comes from `EC2_PANGOLIN_ORG`.

Warning: changing edges can interrupt public access. Review the change set and certificate requirements before approval.

## First setup after boot

1. Open `/auth/initial-setup` on `https://pangolin.<EC2_DOMAIN>` from the administrator range. The page starts the initial setup.
2. Create the administrator. Use a new credential for this install.
3. Create the organization. Record its identifier in `EC2_PANGOLIN_ORG`.
4. Create a site of type `local`. Use the identifier from `EC2_PANGOLIN_SITE`.
5. Create an integration API key with blueprint apply rights. Store it as `EC2_PANGOLIN_API_KEY` through a secure channel.
6. Write the environment file to the secret again. Keep only the first-boot exceptions that still apply.
7. Run the [bootstrap](../reference/host-boot.md) again. It applies the blueprint when the API key and organization exist.

A second blueprint apply gives the same resources.
The bootstrap skips it while the key or organization is empty.
For a network outside the cloud, follow the optional [Newt site procedure](home-lab.md).
That page gives confinement rules, not an automated Newt install.
Create a Newt site in Pangolin and run its container outside the host only after approving those network limits.

Not verified: a real blueprint apply, certificate renewal or Newt tunnel on an installed host.
The [host boot limits](../reference/host-boot.md#known-limits) also apply.
