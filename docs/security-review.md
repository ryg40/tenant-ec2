# Advisory security review

`/security-review` gives suggestions for your install. It changes no file and approves nothing.
Suggestions block no commit, merge, or deploy; the explicit gates in `AGENTS.md`, rule 8, still apply.
The review reports proven functional defects separately from ranked suggestions.
An exit status of zero from this review is not approval.

## Set up the command

The source is `skills/security-review/SKILL.md`. Both harnesses use that source through project links.
The separate setup command writes links. The review command itself writes nothing.

1. Open a terminal at the root of your clone.
2. Run the setup command:

   ```sh
   sh skills/security-review/install.sh
   ```

   It keeps existing files and refuses a conflicting project destination.
3. Restart Claude Code or run `/reload` in Pi.
4. In Pi, approve the project resources after reading their source.
5. Enter `/security-review` from the clone root.

| Harness | Project resource | Behavior |
| --- | --- | --- |
| Claude Code | `.claude/skills/security-review` links to the skill directory. | The slash command loads the project skill when no higher-priority skill has that name. |
| Pi | `.pi/skills/security-review` links to the skill directory. | The native skill command is `/skill:security-review`. |
| Pi alias | The setup creates a link named security-review.md in `.pi/prompts/` for the bundled prompt. | `/security-review` tells the model to read this clone's skill by path. |

The links are local setup artifacts. Do not add them to a portable snapshot; publish the source and this setup command instead.
Restart setup from the clone root after moving or replacing a clone.

## A home skill with the same name

Claude Code gives an enterprise skill priority over a personal skill, and a personal skill priority over a project skill.
Thus a personal `security-review` runs instead of this project skill. The setup command does not change your home directory.
Ask Claude Code to `Read skills/security-review/SKILL.md and follow that file` when a collision exists.
Do not assume the built-in command or another skill follows this advisory contract.

Pi keeps the first skill with a given name and reports a collision. Its default loader reads user skills before project skills.
A home skill can therefore win `/skill:security-review`. The bundled `/security-review` prompt selects this clone's source by path instead.
A home prompt or an extension command named `security-review` can also intercept the short command.
Use this explicit launch when a Pi prompt collision exists:

```sh
pi --no-extensions --no-skills --skill ./skills/security-review \
  --no-prompt-templates --prompt-template ./skills/security-review/security-review.md
```

No setup step deletes, copies, or rewrites a home skill.
The text of this kit skill is original. It includes no text or reference files from a different security-review skill.

## Run the file reader directly

1. Run the default review against the sample file:

   ```sh
   sh scripts/security-review.sh
   ```

   The sample does not prove the settings of an installed host.
2. To assess your private input, pass its path:

   ```sh
   sh scripts/security-review.sh --env-file .env
   ```

   The helper inspects values internally. Its output holds names, source lines, and derived facts only.
3. Run the offline self-test when you change the reader:

   ```sh
   sh scripts/security-review.sh --self-test
   ```

   Check for the exact line `security-review self-test: pass`; the exit status is always zero, even after a failed self-test.

The reader needs Python 3 and a POSIX shell. It makes no network call and runs no cloud, Compose, or credential command.
It does not source the input or use settings inherited from your shell. It refuses links, special files, and oversized inputs.
It reports missing or unsupported input with `Not verified:` and still exits with zero.
Only the self-test creates temporary synthetic fixtures. A normal review writes no file.

The reader checks literal option selections, admin range width, required names, and conventional Compose port declarations.
It uses the active file set from the option selections. It reports conditional DNS names only for the selected provider.
An empty name is advice, not proof of a broken install: some credentials are generated after the first start.
It does not implement a full environment or YAML parser. It cannot prove interpolation, YAML merges, includes, or effective container settings.

The skill expands the review to source templates, role policies, security groups, edge routes, the Entra gate, and credential handling.
It reviews source changes but excludes private inputs and rendered files. The model never receives an environment value.
The review asks about shell overrides and extra Compose files because they can change the effective install.
No hook, test runner, or deploy script invokes the advisory review. Run it when you want advice.

## Read the result

Each suggestion has a fact, a reason, a proposed change, and a proof step.
A source fact describes a file, not the state of a running host. `Not verified:` identifies missing evidence.
Live checks need separate approval. This command does not run them.

| Rank | Meaning |
| --- | --- |
| 5 | Address first. The condition can expose a whole account or a connected private network. |
| 4 | High impact. Reduce broad credentials, public entry points, or weak isolation. |
| 3 | Material risk with a condition, or a missing operational control. |
| 2 | Additional protection, monitoring, retention, or reproducibility. |
| 1 | Small correctness or maintenance improvement. |

## Known core risks

These suggestions describe the merged core and its remaining risks. They are not proof of an exploitable running deployment.
Pangolin and Copilot are options. The Entra gate is part of the core.

The core publishes only Caddy's HTTP and HTTPS ports. Its default public name is `ok`, routed through the Entra gate.
The other selected names require the administrator address range. Model routes expose only five exact API paths, not gateway administration.
OpenViking runs without its bot. Knowledge networks are internal, and the agent uses its own network without a browser login.
Source commits are checked before builds. Durable mounts and the metadata guard must be ready before Docker starts.
The storage policy requires TLS and access points. OpenKnowledge's access-point identity now matches its service identity.
Operations can add daily EBS snapshots, log collection and a billing alarm. None proves a tested restore or a spend ceiling.

| Rank | Fact and possible effect | Suggested mitigation | Proof to obtain |
| --- | --- | --- | --- |
| 5 | The gateway needs instance credentials. A container that obtains the host role may read the combined runtime secret. | Keep metadata unavailable to other containers. Consider a separate model-only role and split build secrets from runtime secrets. | On an approved test host, deny metadata access from every non-gateway service. Check the role policy separately. |
| 4 | Broad provisioner actions and editable role trust can extend a stolen key's reach. A boundary is not a trust policy. | Prefer a separate AWS account and short sessions. Limit resource, region, trust, and service actions. | Review the effective policies and test denied operations with a scoped test identity. |
| 4 | An administrator session has broad rights. Repository hooks execute branch code with the process environment. | Use a limited bootstrap role with MFA. Run agents and checks with only task credentials. Review hook changes before execution. | Confirm the session identity and inspect the process setup without showing credentials. |
| 3 | OpenKnowledge has no native authorization. Its agent path grants full page access without a credential. | Keep browser traffic behind `ok-auth`. Restrict agent tools or add application authorization. A Host header is not authentication. | Test denied anonymous browser access and the intended agent rights with synthetic pages. |
| 3 | The gate restricts one tenant and group, but a removed member can retain a session for up to 12 hours. | Shorten sessions and configure assignment-required deliberately. Restrict gate egress where practical. | Test allowed users, rejected identities, real group claims and revocation in an approved tenant. |
| 3 | One runtime secret and retained clone credentials enlarge the effect of one secret read. | Split credentials by use. Limit read rights. Remove build-only access when the build no longer needs it. | Review resource policies and test denied reads with test identities. |
| 3 | The agent retains Internet access with search off, although all core dependencies have internal paths. Internal peers can reach unkeyed health. | Move agent egress to the search option. Protect health in the agent and retain edge path normalization and exclusions. | Test offline core operation and denied health paths with synthetic dependencies. |
| 3 | Caddy joins every service-edge network, including networks of disabled names. | Reduce unused attachments where practical. Keep knowledge reachable only through the gate from the edge. | Compare selected routes and network members. Test isolation in a fixture. |
| 3 | Stored content can instruct an agent to misuse write tools or poison later memory. Web search adds more untrusted content. | Prefer read-only tools. Enable writes per task. Keep local shell roots empty and filter remote fetch targets. | Use synthetic hostile text in an isolated test with no production credentials. |
| 3 | Client keys restrict models but lack budgets, expiry and rate limits. Core Luna has no price entry. Copilot prices are zero. | Verify accounting before relying on budgets. Add request, token and parallel-request limits. Keep Luna at `xhigh`. | Use stubs to prove limits and cost calculation before approved model requests. |
| 3 | EFS backup is off. EBS snapshots retain seven, but broad provisioner rights can delete them. Detection and alert prerequisites remain external. | Add EFS backups, independent deletion protection and intrusion detection. Confirm billing subscriptions and a restore schedule. | Restore synthetic data in an approved isolated environment. Test alerts and denied deletion. |
| 3 | Commit comparisons and replacement guards exist, but no periodic host patch policy exists. | Keep both guards and define an update window. Record source, image and restore identities. | Test commit mismatch refusal offline. Review a change set before an approved update. |
| 3 | Local scan rules and publication exceptions can change with the branch they inspect. | Review control changes independently. Keep the secret scan active and the publish set explicit. | Run the scan negative controls and review the exact publication inputs. |
| 3 | Changing the embedding option changes vector dimensions. Existing memory may no longer match the new model. | Back up and migrate or rebuild an isolated store instead of switching an existing store blindly. | Check dimensions and retrieval quality with synthetic data. |
| 2 | Environment secrets are visible to Docker administrators and inherited processes. | Limit Docker and session access. Keep private files at mode 0600 and use secret files where supported. | Check permissions and access controls without printing inspection output. |
| 2 | Gate logs can contain user names and callback codes. Journal export does not redact messages, and Caddy has no access log configuration. | Redact callback queries, limit log readers and add privacy-preserving edge logs. Track Entra secret expiry and revocation. | Check synthetic logs and rotation delivery without displaying credentials. |
| 2 | Caddy input checks accept a multiline value when one line matches. Normal host delivery rejects line breaks. | Reject control characters before configuration expansion. | Run negative fixtures with direct container environment inputs; no remote exploit is established. |
| 2 | Mutable support-image tags and build-time downloads reduce reproducibility. | Pin reviewed digests and verify downloaded tools. Build kits from the tag and commit of the lock. | Compare source and image records with the lock in an isolated build. |
| 2 | Missing memory and process limits let one service exhaust the host. Root and writable filesystems widen its rights. | Limit resources, drop capabilities, and use non-root or read-only modes where supported. | Start an isolated fixture with the proposed limits and check its health. |
| 2 | Root memory keys, retained jobs, and shared writable storage expose more data than one client needs. | Use per-client keys, key hashing where supported, retention rules, and narrow mounts. | Test client separation and retention with synthetic data. |
| 2 | OpenKnowledge uses one writer and matching ownership, but EFS behavior and restore remain unproved. | Keep one writer per access point and plan kit security updates. | Test page writes and a restore on an approved test access point. |
| 2 | Route defaults set Luna to `xhigh`, but do not prove rejection of client overrides. The vision block sets no effort. | Reject other effort values at a shared control. Do not use parameter dropping to accept an invalid request. | Inspect stubbed outgoing requests. No lower-effort provider call is needed. |
| 2 | The check enforces gate routing, exact knowledge-network members and support-image equality with the lock. Missing regression files report skips. | Keep the negative controls. Inspect skips instead of treating them as proof. | Network and pin mutations fail the check. Not verified: the same controls on a deployed host. |
| 2 | The verifier checks internal paths, not public certificates, real login, model answers or restores. SSM output can include edited settings. | Redact mismatch output and restrict log readers. Keep live acceptance checks separate from source tests. | Review synthetic SSM output and obtain separate approval for external checks. |
| 1 | Cross-region inference can move data beyond the intended region. | Select the intended inference profile and constrain the role to it. | Review the profile's routing regions and the role conditions. |

## Known option risks

| Option | Rank | Fact and possible effect | Suggested mitigation | Proof to obtain |
| --- | --- | --- | --- | --- |
| Pangolin with a Newt site | 5 | A server administrator chooses site targets. A compromised public host can request private targets. | Keep Newt in a separate network with target-specific egress rules. Disable unneeded clients and SSH. | In an approved fixture, confirm that unlisted targets remain unreachable. |
| Pangolin | 4 | The dashboard, tunnel listeners, and internal edge APIs add attack surfaces. | Use the admin allow-list, short sessions, MFA, separate edge networks, and a private Gerbil API binding. | Test dashboard denial and internal API isolation in an approved fixture. |
| Pangolin with Porkbun | 4 | DNS-01 puts a DNS credential in the TLS process. Core Caddy needs no DNS key on the host. | Prefer a dedicated challenge zone. Otherwise restrict the domain, DNS scope and source address. | Test a denied record outside the intended scope in an approved zone. |
| Pangolin model routes | 3 | Unlike Caddy, the five model API paths accept any client address before gateway key checks. Agent normalization remains a separate test. | Add an address restriction when public model access is unnecessary. Preserve gateway keys and test denied agent paths. | Use isolated requests with ambiguous paths and outside-range addresses. |
| DNS provider hookups | 3 | Updates are sequential, not atomic. Removing a selected name leaves its old record. Provider rights may cover unrelated records. | Prefer manual DNS or narrowly scoped credentials. Review partial failures and remove retired records deliberately. | Use offline fixtures first. Real API behavior and propagation need separately approved checks. |
| Pangolin | 3 | Wildcard certificate keys and setup tokens can appear in persistent state or logs. | Separate edge directories. Complete initial setup promptly and restrict log and backup readers. Plan certificate revocation. | Check permissions and the setup state without displaying tokens or keys. |
| Copilot | 3 | Login tokens and snapshots can expose the account allowance. Automated use also depends on provider terms and limits. | Use a dedicated permitted account where appropriate. Limit requests and document token revocation. Keep Bedrock available as a separate route. | Check the current plan terms and prove revocation with an approved test account. |
| Home credential stores | 4 | A broad login-shell environment or unlocked password store exposes unrelated credentials to tools and branch code. | Give each task only its required credentials. Keep privileged store keys outside agent sessions and shorten unlock periods. | Inspect names and access policies only. Check the lock state without unlocking a store. |
| Home credential stores | 3 | A wildcard reader policy may also match the provisioner secret. Copies complicate rotation. | Use exact secret paths and separate provisioner storage. Record every copy in a private rotation procedure. | With approval, prove a reader cannot fetch the provisioner secret. |
| Private kit sources | 3 | A broad source token may read unrelated repositories and remain on the host after building. | Use a read-only credential for one repository. Pass it through an askpass helper and separate build-only storage. | Test scope with an approved test credential and inspect logs for names only. |
| Optional web search | 3 | Remote content can inject instructions or fetch private targets. | Use a new scoped search credential, public-target filtering, and read-only tools by default. | Test denied private redirects and synthetic prompt injection in isolation. |
| Newt site credential | 2 | A leaked site credential lets another client pose as the site. | Restrict its storage and rotate it when the site changes. Keep private targets behind local access controls. | Revoke a test site credential in an approved fixture and check reconnection denial. |

AgentCore and model calls from OpenKnowledge are outside this kit's current core and named option set.
A later addition needs its own role, inbound identity, data-path, and credential review.

## Verification limits

Claude Code 2.1.295 expands this project skill in a scratch clone with a fresh home directory and a local API stub.
A synthetic personal skill wins the same-name test. The test sends no request to a model provider.
Pi 1.1.0 loads the project skill without frontmatter warnings and expands the short prompt through its installed loader functions.
A synthetic user skill wins Pi's native skill-name collision. The short prompt still names the kit source directly.
The setup test proves relative links, repeat runs, and preservation of a conflicting project command.
The `loaders skills/security-review/` line of `scripts/check.sh` runs these checks.
It skips without Claude Code, Node.js or the Pi package.
`CHECK_PI_PACKAGE` selects a package directory; a wrong directory fails the check.
The default uses the global npm installation.
Repeat the loader checks separately with an installed Pi package directory:

```sh
python3 skills/security-review/test-loaders.py "$(npm root -g)/@earendil-works/pi-coding-agent"
```

This test requires Claude Code, Node.js, and the installed Pi package. It uses a loopback API stub and synthetic credentials.
It grants neither harness tools and sends no model request to a provider.

The check script also runs the reader's offline self-test, and requires its printed pass line.
It never runs the advisory review itself.
The self-test covers the sample, both options, DNS selections, wide ranges, single addresses, missing files, redaction, and unchanged inputs.
Harness loader tests can prove discovery and command expansion with synthetic inputs. They do not prove that a model follows every instruction.
Not verified: any live AWS, Entra, DNS, container network, login, token revocation, or restore behavior from this advisory command.

External references: [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/) and [Claude Code skill precedence](https://code.claude.com/docs/en/skills#resolve-skills-that-share-a-name).
