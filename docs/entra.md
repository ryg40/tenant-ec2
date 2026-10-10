# The Entra ID gate of OpenKnowledge

OpenKnowledge has no login of its own. The service `ok-auth` is its login gate: oauth2-proxy with Microsoft Entra ID. A user passes the gate only as a member of one security group in one Entra tenant.

This page tells you how to make the app registration, which names to set, how to prove the gate and how to change it to another provider.

## The parts

| Part | Where | Role |
| --- | --- | --- |
| The gate | The service `ok-auth` of [`compose.yaml`](../compose.yaml): oauth2-proxy 7.15.5, pinned by tag and digest in [`kits.lock`](../kits.lock) | It listens on port 4180 with plain HTTP. It is the only target of the public name `ok`. |
| The settings of the gate | [`ok-auth/oauth2-proxy.cfg`](../ok-auth/oauth2-proxy.cfg) | Each fixed setting: the cookie, the trust in the edge, the upstream. It holds no secret. |
| The values of one install | Five names of the environment file (section [The names](#the-names)) | The tenant, the group, the app registration and the two secrets. |
| The app registration | Your Entra tenant | It names the redirect URI and gives the client id and the client secret. |
| The security group | Your Entra tenant | Its members pass the gate. |
| The script | [`scripts/entra-app.sh`](../scripts/entra-app.sh) | It makes the app registration, the group and the secrets with the Azure CLI. |
| The local run | [`ok-auth/local-run.sh`](../ok-auth/local-run.sh) | It proves the gate with no Entra tenant and no host. |

The diagram [`03-entra-login`](diagrams/03-entra-login.svg) shows the login path.

### The login path

1. The browser asks for `https://ok.<domain>/`. The edge ends TLS and sends the request to `ok-auth:4180`.
2. The request has no session. The gate answers with a redirect to `https://login.microsoftonline.com/<tenant id>/oauth2/v2.0/authorize`.
3. The user signs in at Entra ID. Entra ID sends the browser back to `https://ok.<domain>/oauth2/callback` with a code.
4. The gate changes the code for an ID token at Entra ID. It sends the client secret and the PKCE verifier. This call goes out through the network `gate-egress`.
5. The gate checks the token: the signature, the issuer of your tenant, the client id, the nonce and the end time.
6. The gate checks the group: the claim `groups` of the token must hold the id of your group.
7. The gate sets the session cookie `__Host-ok` and sends the browser back to its page.
8. For each later request, the gate reads the cookie and sends the request to `open-knowledge:80` on the network `knowledge`.

A user who is not in the group gets the status 403 and no cookie. A token that fails a check of step 5 gets the status 403 or 500 and no cookie.

## The names

The five names are core names. They are in Part 1 of [`.env.example`](../.env.example), so each one needs a value.

| Name | Value | Secret | Source |
| --- | --- | --- | --- |
| `EC2_OK_ENTRA_TENANT_ID` | The id of your Entra tenant | No | `scripts/entra-app.sh apply`, or "Directory (tenant) ID" of the app registration |
| `EC2_OK_ENTRA_GROUP_ID` | The object id of the security group | No | `scripts/entra-app.sh apply`, or "Object ID" of the group |
| `EC2_OK_OIDC_CLIENT_ID` | The application (client) id of the app registration | No | `scripts/entra-app.sh apply`, or "Application (client) ID" |
| `EC2_OK_OIDC_CLIENT_SECRET` | The client secret of the app registration | Yes | `scripts/entra-app.sh secrets`, or the "Value" of a new client secret |
| `EC2_OK_COOKIE_SECRET` | The key of the session cookie: 32 random bytes in URL-safe base64 | Yes | `scripts/entra-app.sh secrets` |

- The gate also reads `EC2_DOMAIN`. Its public URL is `https://ok.<EC2_DOMAIN>`, and its redirect URI is `https://ok.<EC2_DOMAIN>/oauth2/callback`.
- The public name `ok` must be in `EC2_EDGE_NAMES`. It is the default.
- The three ids are not secrets, but they identify your tenant. Do not put them into a tracked file.

## Make the app registration with the script

The script needs the Azure CLI (`az`) and `python3` on the development machine. It reads and writes the environment file of the development machine, `.env`. That file is a copy of `.env.example`.

1. Set `EC2_DOMAIN` in `.env`. The script makes the redirect URI from it.
2. Run `scripts/entra-app.sh --dry-run apply`. It prints each Azure CLI command and makes no call.
3. Run `az login --tenant <tenant id>`. Sign in with an identity that can make an app registration and a group.
4. Run `scripts/entra-app.sh apply`. It makes the app registration, the enterprise application and the security group, and it writes the three ids into `.env`.
5. Add each user to the security group. The default name of the group is `tenant-ec2-openknowledge`.
6. Run `scripts/entra-app.sh secrets`. It writes the client secret and the cookie secret into `.env`.
7. Write down the end date that the script prints. The client secret stops on that date.
8. Run `az logout`. The session of the Azure CLI is not necessary after this.
9. Write the host secret with the [first-boot exceptions](reference/host-boot.md#environment-file-and-generated-keys) when needed. The host gets the five gate names from it.

Facts:

- The script prints no secret and no id. A message names a name of the environment file, never a value.
- A real run never writes into the sample file `.env.example`. It stops before its first call when the environment file is that file, a link to it, or the same file under another name. A dry run can read the sample file.
- `scripts/entra-app.sh selftest` tests that rule, the dry run and the two commands with a stub of `az`. It makes no call to Azure.
- A second run of `apply` changes nothing that is correct. It sets the same state again.
- `apply` stops when the session of the Azure CLI is in another tenant than `EC2_OK_ENTRA_TENANT_ID`.
- `apply --name <text>` and `apply --group <text>` select other names for the app registration and the group.
- For an existing registration or group with another name, supply that name. Without stored ids, the default name can create another object.
- The commands are `apply`, `secrets` and `selftest`. The script has no registration review command.
- `apply --assign-group` adds a second lock in Entra ID (section [The group](#the-group)).
- Not verified: no run of the script made a call to Azure (section [What is not proved](#what-is-not-proved)).

## Make the app registration by hand

Use the Microsoft Entra admin centre. Not verified: the names of the menus on the day of your install.

1. Open "App registrations" and select "New registration".
2. Give a name, for example `tenant-ec2 OpenKnowledge gate`.
3. For the account types, select the option for this directory only (single tenant).
4. For the redirect URI, select the platform "Web" and give `https://ok.<domain>/oauth2/callback`.
5. Select "Register". Copy "Application (client) ID" into `EC2_OK_OIDC_CLIENT_ID` and "Directory (tenant) ID" into `EC2_OK_ENTRA_TENANT_ID`.
6. Open "Authentication". Make sure that the two boxes of the implicit grant are empty.
7. Open "Token configuration" and select "Add groups claim". Select "Security groups" and keep "Group ID" for the ID token.
8. Open "API permissions". Make sure that the delegated permission `User.Read` of Microsoft Graph is in the list.
9. Open "Certificates & secrets" and select "New client secret". Give an end date.
10. Copy the "Value" of the new secret into `EC2_OK_OIDC_CLIENT_SECRET`. The page shows it one time only. Do not copy the "Secret ID".
11. Open "Groups" and select "New group". Select the type "Security" and give a name.
12. Add each user as a member. Copy the "Object ID" of the group into `EC2_OK_ENTRA_GROUP_ID`.
13. Make the cookie secret (section [The secrets](#the-secrets)).

## The group

- Only the id of one group is the test of the gate. The name of a user and its mail domain are not a test.
- A user who is a member through another group also has the id in the token. Not verified: this is the behaviour of the claim for security groups in the Entra documents; no run proved it.
- A guest of your tenant is a user of your tenant. A guest passes only as a member of the group.
- A user of another tenant does not pass. Three parts refuse such a user: the app registration is for one tenant, the login address holds your tenant id, and the gate refuses a token with another issuer.
- A user who leaves the group keeps a session for 12 hours at most. Then the gate asks for a new login.
- A user with more than 200 groups has no group list in the token. The gate then reads the groups from Microsoft Graph with the permission `User.Read`. The user consents to it at the first login, or an administrator consents for all users.

The second lock: `scripts/entra-app.sh apply --assign-group` assigns the group to the enterprise application and sets "assignment required". Entra ID then gives no token to a user outside the group. This needs an Entra ID P1 or P2 licence. Without the licence, the command stops before it sets "assignment required".

## The secrets

| Secret | Form | Renew |
| --- | --- | --- |
| `EC2_OK_OIDC_CLIENT_SECRET` | The value that Entra ID makes | Before its end date: run `scripts/entra-app.sh secrets` again. The default life is 180 days; `--days <number>` changes it. |
| `EC2_OK_COOKIE_SECRET` | 32 random bytes in URL-safe base64, 43 or 44 characters | Only when it leaks: run `scripts/entra-app.sh secrets --new-cookie-secret`. Each session of each user then ends. |

- To make the cookie secret by hand, run `openssl rand -base64 32 | tr -- '+/' '-_'` and put the output into `.env`.
- Warning: the output of `openssl rand -base64 32` alone is not valid when it has the character `+` or `/`. oauth2-proxy then does not start. Its message is "cookie_secret must be 16, 24, or 32 bytes".
- A new client secret does not stop an older one. After the host has the new value, remove each older one. `scripts/entra-app.sh secrets` prints the two commands.
- After a change of a secret, run `scripts/aws-host.sh secret < .env`. Then render the environment file on the host again and start `ok-auth` again.
- No secret of the gate is in a file of the repository or in an argument of a command. The two secrets are environment values of the container, as each secret of this stack.

## Prove the gate before the deploy

The local run proves the gate on the development machine. It needs Docker and `python3`. It makes no call to Entra ID, to Azure or to AWS, and it reads no credential.

1. Run `sh ok-auth/local-run.sh`. The last line must be `local run: pass`.
2. On a machine with no image of the OpenKnowledge kit, the run uses a stub upstream by itself. `--upstream stub` selects it by hand.

Warning: do not run it on the host of the stack. The edge network of the name `ok` has a fixed range, and the stack of the host uses it.

The run starts the services `open-knowledge` and `ok-auth` as `compose.yaml` has them. It also loads the core edge file `compose.caddy.yaml`, so the gate has the edge network `edge-ok` of that file. It does not start `caddy`: a probe is in the place of the edge. A stub answers in the place of Entra ID and of Microsoft Graph, on a network with no route to the internet. The gate keeps its real issuer URL. What the run proves:

| Subject | Proved |
| --- | --- |
| The container | `ok-auth` is healthy, runs as the user 65532, has no capability, a read-only root file system and no published port. |
| No session | A request gets the redirect to the login of the one tenant. The paths `/ready` and `/ping` answer 200. A JSON request and the path `/mcp` get 401. |
| The login request | It has the client id, the redirect URI, the scope `openid profile User.Read`, a PKCE challenge and a nonce. |
| The cookie | The session cookie and the cookie of the login request are `Secure` and `HttpOnly`, with `SameSite=Lax`, the prefix `__Host-` and no `Domain`. The session ends after 12 hours. |
| The group | A member passes. A user outside the group and a token with no group claim get 403. |
| The tenant | A token of another tenant does not pass, also with the group id in it. |
| The token | A token for another application, with a wrong nonce, with a wrong signature, after its end time or with no user name does not pass. |
| More than 200 groups | A member passes with the groups of the Graph stub, on two pages. A user outside the group does not pass. A refusal of Graph stops the login. |
| The edge | The gate reads a forwarded header from the range of the edge only. A client of the network of OpenKnowledge cannot set one. |
| The edge network | The network `edge-ok` of the run has the range of `trusted_proxy_ips`. Each of the two edge files gives that range, and its members there are the edge and the gate only. |
| The upstream | With the kit image: OpenKnowledge answers through the gate, gives the editor a `wss://` address, and the WebSocket of the editor opens. With the stub: the upstream gets the host name of the browser and the name of the user, and no token and no forged user header. |
| The networks | OpenKnowledge is on two internal networks with no route to the internet. A service of another network has no path to it and none to the gate. |
| The agent | The agent path of section [The path of the agent](#the-path-of-the-agent) works with no login. The service name alone gets 403. |
| The log | The test checks that the log contains none of its configured fake credential values. A login log can still contain a user name and a one-time code. |

## The first login

Do these steps after the first deploy. They prove what the local run cannot prove: your app registration, your group and the edge.

1. On the host, run `docker compose ps ok-auth`. The state must be `healthy`.
2. On another machine, run `curl -sS -o /dev/null -D - https://ok.<domain>/`. The status must be 302, and `Location` must start with `https://login.microsoftonline.com/<tenant id>/`.
3. Open `https://ok.<domain>/` in a browser with a user who is a member of the group. After the login, the browser must show OpenKnowledge.
4. Open a page and change a line. The change must stay after a reload. This proves the WebSocket of the editor through the edge and the gate.
5. Open the same address in a private window with a user of your tenant who is not in the group. The result must be an error page with the status 403.
6. On the host, run `docker compose logs ok-auth | grep -c AuthSuccess`. The count must be 1 or more.
7. Open `https://ok.<domain>/oauth2/sign_out`. The session ends.

Step 3 is the proof of the login. Step 5 is the proof of the group check.

## What is not proved

- Not verified: an app registration in a real tenant. No call to Azure runs in the work on this gate.
- Not verified: `scripts/entra-app.sh` against Azure. Its 14 commands parse in the Azure CLI 2.91.0 with no network, and its steps pass against a stub of `az` (`scripts/entra-app.sh selftest`).
- Not verified: the group claim of a real token of Entra ID, the consent to `User.Read`, and the read of the real Microsoft Graph.
- Not verified: `apply --assign-group` and its need for a licence.
- Not verified: the edge in front of the gate, its certificate and its forwarded headers. The local run has a probe in the place of the edge.
- Not verified: the route of the gate to Entra ID through `gate-egress` on the host, and the metadata guard for that network.
- Not verified: the pi-durable agent itself on the agent path. The local run has a probe in its place, with the same URL.

Section [The first login](#the-first-login) proves the first, the third and the fifth item on the host.

## The path of the agent

The gate is a login of a browser, so the agent cannot pass it. The decision: the agent reaches OpenKnowledge on an internal network of the stack, and not through the gate.

- The network `knowledge-agent` has two members: `open-knowledge` and `pi-durable`. It is internal.
- The agent calls `http://ok.<domain>/mcp`. On that network the public name is an alias of `open-knowledge`, and OpenKnowledge listens on port 80.
- The reason for the name: OpenKnowledge admits a request only with the host name of its public URL. A call to `http://open-knowledge/mcp` gets the status 403.
- The call is plain HTTP between two containers of the host. It does not leave the host and does not pass the edge.
- The agent holds no credential of Entra ID, and the gate has no exception for the path `/mcp`.

Why not a machine credential of Entra ID for the agent: the gate then needs a second way in, with bearer tokens, and the agent needs a secret of your tenant. That path is more to guard, and it gives the agent the same access.

What follows from the decision:

- OpenKnowledge has no login, so the agent reads, changes and deletes each page with no credential. Give the agent only tasks that can have this access.
- The gate and the agent are on no common network.
- OpenKnowledge can call the port of the agent on `knowledge-agent`. The API of the agent checks its own bearer.

## The settings that keep the gate closed

| Property | Setting | Where |
| --- | --- | --- |
| One tenant only | The issuer `https://login.microsoftonline.com/<tenant id>/v2.0`; never `common`; no `insecure_oidc_skip_issuer_verification` | `compose.yaml`, `oauth2-proxy.cfg` |
| One group only | `OAUTH2_PROXY_ALLOWED_GROUPS` with one id | `compose.yaml` |
| A cookie for the name `ok` only | `cookie_name = "__Host-ok"`, `cookie_secure`, `cookie_httponly`, no `cookie_domains` | `oauth2-proxy.cfg` |
| A session of 12 hours | `cookie_expire = "12h"`, no refresh | `oauth2-proxy.cfg` |
| A forwarded header only from the edge | `reverse_proxy = true`, `trusted_proxy_ips = ["198.18.5.0/29"]` | `oauth2-proxy.cfg` |
| No path around the login | No `skip_auth_routes`, `skip_auth_regex`, `trusted_ips`, `skip_jwt_bearer_tokens` | `oauth2-proxy.cfg` |
| No token for OpenKnowledge | `pass_access_token = false`, `pass_authorization_header = false`, `pass_basic_auth = false` | `oauth2-proxy.cfg` |
| A container with no rights | `cap_drop: [ALL]`, `no-new-privileges`, the user 65532, `read_only: true`, no `ports:` | `compose.yaml` |

The edge network of the name `ok` is `edge-ok`. It must have the range `198.18.5.0/29`, and its members must be the edge and `ok-auth` only. Each edge file sets both: `compose.caddy.yaml` and `compose.pangolin.yaml`. `sh scripts/check.sh` and the local run compare the range of the two files with `trusted_proxy_ips`. With another range, the gate still asks for the login, but it does not read the address of the client from the edge.

Risks that stay. Each one is a suggestion for your own review:

- The network `gate-egress` has a route to the whole internet, not only to Entra ID.
- The two secrets are environment values. The root user of the host reads them with `docker inspect`.
- The session cookie holds the tokens of the user, encrypted with the cookie secret.
- The log of the gate holds the name of each user and the one-time code of each login.

## Change the gate to another OIDC provider

The gate works with each OpenID Connect provider that puts the groups of a user into the ID token. Not verified: no run used another provider.

1. Make a client at the provider with the redirect URI `https://ok.<domain>/oauth2/callback`. Put its id and its secret into `EC2_OK_OIDC_CLIENT_ID` and `EC2_OK_OIDC_CLIENT_SECRET`.
2. In `ok-auth/oauth2-proxy.cfg`, set `provider = "oidc"`.
3. In the same file, set `scope` to the scopes of the provider that give the user name and the groups, for example `openid profile email groups`.
4. In the same file, set `oidc_groups_claim` to the name of the groups claim of the provider. Set `oidc_email_claim` to a claim that each token has.
5. In `compose.yaml`, set `OAUTH2_PROXY_OIDC_ISSUER_URL` of `ok-auth` to the issuer URL of the provider. Use the issuer of one realm or one tenant only.
6. Put the group into `EC2_OK_ENTRA_GROUP_ID`, in the form that the claim has: a name or an id. Put the name of the provider into `EC2_OK_ENTRA_TENANT_ID`: the stack needs a value for each name.
7. Keep each other setting: the cookie, the trust in the edge, and no path around the login.
8. Log in with a member of the group and with a user outside the group (section [The first login](#the-first-login)).

The local run and `scripts/entra-app.sh` are for Entra ID only. With `provider = "oidc"` the local run does not pass as it is.

## Faults

| Sign | Cause | Correction |
| --- | --- | --- |
| `ok-auth` starts again and again; the log says "cookie_secret must be 16, 24, or 32 bytes" | The cookie secret has a wrong form | Section [The secrets](#the-secrets) |
| `ok-auth` starts again and again; the log says "failed to discover OIDC configuration" | No route to Entra ID, or a wrong tenant id | Check `EC2_OK_ENTRA_TENANT_ID` and the outbound route of the host |
| Entra ID shows the error AADSTS50011 | The redirect URI of the app registration is not `https://ok.<domain>/oauth2/callback` | Run `scripts/entra-app.sh apply` again |
| The status 500 after the login; the log says "invalid_client" | A wrong or old client secret | Run `scripts/entra-app.sh secrets`, then write the secret of the host again |
| The status 403 after the login; the log says "unauthorized" | The user is not in the group, or the app registration has no group claim | Add the user to the group, or run `scripts/entra-app.sh apply` again |
| The status 500 after the login; the log says "invalid response from microsoft graph" | The user has more than 200 groups, and `User.Read` has no consent | An administrator consents to `User.Read` for the app registration |
| Entra ID shows the error AADSTS50105 | "Assignment required" is set, and the user or the group is not assigned | Assign the group to the enterprise application |
| The browser comes back to the login again and again | The browser does not keep the cookie: the page is not HTTPS | The edge must serve the name `ok` with a certificate |
| The agent gets the status 403 "Host header not allowed" | The agent does not use the public name | Keep `OPENKNOWLEDGE_MCP_URL` as `compose.yaml` has it |

Checked with the gate image: the two start faults, the status 403 with "unauthorized" and the refusal of Microsoft Graph, the last two against the stub of the local run. Not verified: each other row. No run made a fault against Entra ID, so no run showed an AADSTS number.
