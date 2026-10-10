# The Compose stack

[Install](../install.md) | [Architecture](../architecture.md) | [Options](../options/README.md)

`compose.yaml` holds six core services: `litellm`, `litellm-db`, `openviking`, `open-knowledge`, `ok-auth` and `pi-durable`.
It has no edge and no Copilot service.
An overlay supplies the selected edge.
Only the edge publishes ports; a service network joins only services that need each other.
A network that needs no internet is `internal`.
Services drop all capabilities except those explicitly needed by the edge.

## File sets

The bootstrap writes `COMPOSE_FILE` into the host environment file from `EC2_OPTIONS`.
A manual Compose command then uses the same file set.
An unknown option stops the scripts.

| `EC2_OPTIONS` | `COMPOSE_FILE` |
| --- | --- |
| Empty | `compose.yaml:compose.caddy.yaml` |
| `copilot` | `compose.yaml:compose.caddy.yaml:compose.copilot.yaml` |
| `pangolin` | `compose.yaml:compose.pangolin.yaml` |
| `pangolin,copilot` | `compose.yaml:compose.pangolin.yaml:compose.copilot.yaml` |

The rendered file sets have these counts:

| File set | Services | Networks | Internet-routed networks |
| --- | --- | --- | --- |
| Core | 7 | 12 | 3 |
| Copilot | 8 | 13 | 3 |
| Pangolin | 9 | 12 | 3 |
| Pangolin and Copilot | 10 | 13 | 3 |

Each overlay is valid only with `compose.yaml`.
A secret keeps its `:?` test in its service file.
A missing option value stops a start only when that option is on.

| File | Content |
| --- | --- |
| `compose.yaml` | The core services, service networks, bind mounts and health checks. |
| `compose.caddy.yaml` | The core edge and its edge networks. |
| `compose.pangolin.yaml` | The Pangolin edge and its edge networks. |
| `compose.copilot.yaml` | Copilot, its network and its gateway configuration. |
| `litellm/config.yaml` | The core model routes. |
| `litellm/copilot.yaml` | The models of the optional Copilot service. |
| `openviking/ov.conf` | The gateway as the embedding and vision endpoint of OpenViking. |
| `caddy/` | The core edge configuration. |
| `ok-auth/` | The Entra gate configuration. |
| `pangolin/` | The optional edge configuration and blueprint; see [Pangolin](../options/pangolin.md). |

## Models and memory

Each model call goes to `http://litellm:4000` with a virtual key.
A virtual key allows only the routes of the active file set that its service needs.
GPT-6 Luna uses reasoning effort `xhigh` only.
No route lowers the effort or sets `drop_params`.

| Route or setting | Core value | Role |
| --- | --- | --- |
| `bedrock/luna` | Instance role credential | Agent and vision model route. |
| `bedrock/embed` | Instance role credential | Embedding model route. |
| `EC2_PI_DURABLE_MODEL` | `bedrock/luna` | The agent model route. |
| `EC2_OPENVIKING_VLM_MODEL` | `bedrock/luna` | The vision model route. |
| `EC2_OPENVIKING_EMBED_MODEL` | `bedrock/embed` | The embedding model route. |
| `EC2_BEDROCK_EMBED_MODEL` | `amazon.titan-embed-text-v2:0` | The Bedrock embedding model. |
| `EC2_OPENVIKING_EMBED_DIMENSION` | `1024` | The embedding vector size. |

The [Copilot option](../options/copilot.md) adds `copilot/luna` and `copilot/embed`.
OpenViking runs without its bot.
The agent uses its own OpenViking user key, `EC2_PI_DURABLE_OPENVIKING_API_KEY`, never the root key.
The basic agent has OpenViking memory and OpenKnowledge, but no web search or desktop part.
See [home lab tools](../options/home-lab.md) for optional web search.
OpenKnowledge model calls and AgentCore are outside the core.

Not verified: LiteLLM 1.103 serves `amazon.titan-embed-text-v2:0` through the instance role on a live host.

## Edge and gate

`EC2_EDGE_NAMES` is a comma-separated list with no blanks.
Its known names are `ok`, `agent`, `llm` and `ov`; its default is `ok`.
The edge creates no route for an unselected name.

- `ok-auth` listens on plain HTTP port 4180. It is the only target of `ok` with either edge.
- The core Compose file gives `ok-auth` no edge network. The edge overlay adds that network.
- The core edge gets one certificate per name through HTTP-01 or TLS-ALPN-01. It needs no DNS provider key.
- Other core edge names answer only `EC2_ADMIN_CIDR`. The service behind each name also checks its own key.
- `llm` serves `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/models` and `/v1/messages` only.
- `agent` refuses `/` and `/healthz`. Caddy also refuses repeated slashes, backslashes, dot segments and encoded path separators with status 400.
- Pangolin uses the same selected names to enable resources in its blueprint. It leaves the login of `ok` to the Entra gate.

The [Entra page](../entra.md) describes the group rule and the agent path to OpenKnowledge.
The [Pangolin page](../options/pangolin.md) describes its dashboard, tunnel ports and path rules.

## Networks

| Network | Members | Route to the internet |
| --- | --- | --- |
| `db` | `litellm`, `litellm-db` | No |
| `llm` | `litellm`, `openviking`, `pi-durable` | No |
| `memory` | `openviking`, `pi-durable` | No |
| `knowledge` | `open-knowledge`, `ok-auth` | No |
| `knowledge-agent` | `open-knowledge`, `pi-durable` | No |
| `egress` | `litellm`, `pi-durable`; also `copilot` with that option | Yes |
| `gate-egress` | `ok-auth` only | Yes |
| `edge-core` | `caddy`, or `pangolin` and `gerbil` | Yes |
| `edge-ok` | The edge and `ok-auth` | No |
| `edge-llm` | The edge and `litellm` | No |
| `edge-ov` | The edge and `openviking` | No |
| `edge-agent` | The edge and `pi-durable` | No |
| `copilot` | `litellm`, `copilot`, with that option only | No |

Here the edge is `caddy` or `gerbil`; Traefik shares Gerbil's network namespace.
`edge-ok` uses `198.18.5.0/29`, the same range as `trusted_proxy_ips` in `ok-auth/oauth2-proxy.cfg`.
Only the edge and the gate share that range.
The gate joins `knowledge`, `gate-egress` and `edge-ok`; its Entra and Graph requests use `gate-egress`.
OpenKnowledge listens on port 80 and joins no edge network.
The agent uses `http://ok.<domain>/mcp`, an OpenKnowledge alias on `knowledge-agent`, with no Entra credential.
All complete file sets have three internet-routed networks: `egress`, `gate-egress` and `edge-core`.

## Render without a start

The sample environment file alone does not render the model: required names are empty.
The [check script](checks.md) renders with a stub environment file and starts no service.
To check a real core environment file before the bootstrap writes `COMPOSE_FILE`, run:

```sh
docker compose --env-file .env -f compose.yaml -f compose.caddy.yaml config --quiet
```

To check the selected file set on the host, run this from its clone:

```sh
sudo docker compose --env-file .env config --quiet
```

Warning: omit neither `--quiet` nor the environment file when a rendered model can contain secrets.
