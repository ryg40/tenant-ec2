# Checks

[Install](../install.md) | [Compose stack](compose-stack.md) | [Secret handling](../secret-handling.md)

`scripts/check.sh` runs source checks, local container checks and offline regression tests over its own tree.
An absent file kind gives a skip line.
Each memory, operations, host replace, storage, verifier and publish regression line reports a skip when its test file is absent.
The number of result lines depends on the templates and stage files in the tree.
The pre-commit hook runs the check script on the exact staged copy, before the scan.

```sh
sh scripts/check.sh
CHECK_OFFLINE=1 sh scripts/check.sh
sh scripts/check.sh --help
```

The offline form makes no external network call.
Its Caddy test still uses a local container and HTTP stubs.
It neither pulls nor builds a missing check image.

## Mac source coverage

All tracked CloudFormation templates include the Mac allocation and runtime templates in the existing lint loop.
The zsh bootstrap gets `zsh -n`, never shellcheck's Bash parser.
The standalone Mac Compose model uses Docker Compose config with isolated stub inputs; no application image starts.
The check script runs `test-macos-host.py`, `test-macos-infra.py`, `test-macos-bootstrap.py`, `test-macos-credentials.py` and `test-macos-compose.py` from `scripts/`.
The credential tests require Python's `botocore`; the Node refresh test remains a fixture, not a real Node SDK test.
`scripts/check-macos-machine.py` requires Python's `jsonschema` and validates the tracked candidate against `scripts/macos-machine.schema.json`.
The deployment machine contract remains the bootstrap's separate closed schema, reused by the deployed verifier.

Verifier regressions preserve all homelab combinations and add documented Podman JSON shapes, selected-service checks, account metadata and redirect redaction.
Development-only Podman fixture cases report an explicit skip in a portable export without those fixtures.
A malformed state, absent health, wrong variant/file set, public gate binding or missing service fails before internal probes.
The operator-only `--tunnel-check` follows no redirect and emits no URL or response body.

Not verified: Mac execution, VM forwarding, native account restrictions, credential transport, Entra login, paid model access or memory operations.
A source check never qualifies the dormant agent or bypasses its upstream release dependency.

## Tools and files

| Check | Files | Tool |
| --- | --- | --- |
| `cfn-lint` | Each `*.cfn.yaml` and `*.cfn.yml`. | `cfn-lint`; install with `pipx install cfn-lint`. |
| `validate-template` | The same templates. | AWS CLI version 2, profile `tenant-ec2`. This read-only call is skipped without the profile or with `CHECK_OFFLINE=1`. |
| `memory render` | Each `config/memory/*.yaml`, compared with its generated stage template. | Python 3 and PyYAML through `scripts/aws-memory.py check --stage <stage>`; skip when the renderer or stage files are absent. |
| `memory stack` | Schema refusals, memory and file scopes, administrator exceptions, policy sizes and deterministic templates. | `python3 scripts/test-memory-stack.py`; needs PyYAML. No AWS call. |
| `memory deploy` | Deploy previews, the prepare and execute steps, parameter forwarding, write gates, keys and output handling with isolated stubs. | `python3 scripts/test-memory-deploy.py`; no AWS call or real key creation. |
| `shellcheck` | Each `*.sh`, executable shell text under `scripts/`, and each Git hook. | `shellcheck` 0.10.0 or later. |
| `compose` | All four file sets, service and network memberships, port publishing, required names and option isolation. It compares each environment name of the agent and its value with the release contract, with `off` and `mcp`. | Docker with the Compose plugin and a stub environment file. |
| `gateway routes` | Core and Copilot route names, configuration and model settings. | Shell tools: `grep`, `sed` and `awk`. |
| `blueprint` | Selected Pangolin resources, the gate target and path rules. | `envsubst` through `scripts/render-pangolin.sh`. |
| `core edge` | The Caddy configuration and request paths, administrator range, refused paths and missing-name behavior. | Docker; local stubs replace the service targets. |
| `render` | The host template with each `EnablePangolin` and `EnableBucket` value; security group ports and instance-role limits. | Python 3 and PyYAML. |
| `dry run` | `scripts/aws-host.sh --dry-run` for `deploy`, `secret`, `dns` and `verify`, with the core and each option set. | Python 3 and command stubs. |
| `secret` | Secret filtering, generated-key recovery and write-back, presence checks, first-boot exceptions, environment rendering and the verifier's file-set test. | Python 3 and an AWS stub. No secret-store write. |
| `providers` | DNS plans, creation, correction, repeat runs, refused credentials and private credential files for every provider. | Python 3 and command stubs with fake zones. |
| Bootstrap dry run | `scripts/host-bootstrap.sh --dry-run` with a fixture in place of `/`. | Python 3, `envsubst` and command stubs. |
| `environment forms` | Four lines, one per file set. Shared form tests, safe messages, first-boot exceptions and refusal before a secret write. | Python 3, edge shell tools and `envsubst`; an AWS stub records writes. |
| `start` | The real bootstrap Compose command for each file set, including `--remove-orphans` and the health wait. | `awk` and a Docker stub. It starts no container. |
| `option contract` | Agreement on Compose files, necessary names and services across the check, bootstrap, deploy script and verifier. | Python 3 and command stubs. It tests the agent with and without a pin. |
| `selftest scripts/aws-provisioner.sh` | Provisioner helper fixtures, including the credential-store selection. | The script's `selftest` command with isolated stubs. |
| `selftest scripts/entra-app.sh` | Entra registration helper fixtures. | The script's `selftest` command with an Azure CLI stub. |
| `selftest scripts/security-review.sh` | The advisory reader's fixtures, not a security review. | The script's `--self-test` command. The printed pass line is required because the exit status is always zero. |
| `loaders` | Claude Code and Pi load the review skill in a new clone. | Git, Node.js, Claude Code and the Pi package. A local API stub replaces the model provider. |
| `selftest scripts/build-diagrams.py` | Invalid scene geometry and binding fixtures. | Python 3 and `--self-test`; no npm install. |
| `selftest scripts/dns-migration.py` | The helper of the [DNS migration](dns-migration.md): a missing or empty plan, host records, preserved TTLs, the zone comparison, record-type probes, the gate before the switch and the zone rebuild. | Python 3 and `--self-test`; fixtures of its own and no network call. |
| `selftest scripts/build-architecture-html.py` | The builder of the interactive page: invalid graph fixtures, a stable build, the layout, the edge routes and the compare step. | Python 3 and `--selftest`; fixtures of its own and no network call. |
| `scenes` | Excalidraw loads each scene and compares its SVG export. | Python Playwright with Chromium, npm and the pinned packages. |
| `architecture` | `docs/architecture.html` is the build of `docs/diagrams/architecture-graph.json`. | Python 3 and `--check`; no network call, also with `CHECK_OFFLINE=1`. |
| `selftest scripts/archify-aws-look.py` | The AWS look: one block in each page, the light default, the frame spacing, the frame titles, and no frame pass for a workflow view. | Python 3 and `--selftest`; fixtures of its own and no network call. |
| `aws look docs/aws-*.html` | Each archify view of `docs/` holds the current AWS look. | Python 3 and `--check`; no network call, also with `CHECK_OFFLINE=1`. It does not run archify. |
| `local run` | The real gate image with a stub identity provider and upstream. | Docker, Python 3 and `ok-auth/local-run.sh --upstream stub`. |
| `operations regression` | The templates, the IAM limits, the journal routing, the cursor, the rotation, the agent files and the bootstrap switch. | `python3 -I scripts/test-operations.py`; needs PyYAML. No network call. |
| `host replace regression` | The pinned image `AmiId`, the refusal of an empty `EC2_AMI_ID`, `current-image` and `new-image`, and the two steps of `deploy --replace-instance`, each with `--prepare` and `--execute <name>`: the refusal of a step with no word, the order of the parts, the stop after the change set of a prepare, the refusal without the unmount proof, the snapshot wait and its reuse, the strict change set of step 1, the execute checks (the stack, the status, the changes, the mount read again), the proofs of step 2 and the dry runs. | `python3 -I scripts/test-host-replace.py`; needs PyYAML. A stub of the AWS CLI answers each call. No network call. |
| `storage regression` | The order of the first boot, a missing or late mount, the checks at a restart, the fstab paths, the unit syntax and the Git ownership. It runs the Compose command of the bootstrap for each of the four file sets. | `python3 -I scripts/test-storage.py`; needs PyYAML and the systemd analysis tools. The fixture with numeric ids needs root. No service and no mount runs. |
| `verifier regression` | The state of a deployed container, the internal HTTP status and authentication, redirects, deadlines, safe errors and the SSM commands. One test for each value of `EC2_OPTIONS`: the verifier checks the services of that file set only. | `python3 -I scripts/test-verify-deployed.py`; needs PyYAML, and `curl` and Node for the complete probe fixtures. No live service and no AWS call. |
| `publish regression` | The private exclusions, the export of the publish set and the reader rules, when the development tests exist. | Python's unittest runner. It also runs on the staged copy with no Git metadata. A snapshot has no development tests and reports a skip. |
| `scan` | The tracked files. | `scripts/scan.sh tree`. |

Each check prints `ok`, `skip (<reason>)` or `FAIL`.
The script exits with status 1 on a failure or a missing required tool.
A missing required tool is never a pass.
Missing optional loader or scene tools give a skip, and the script can still pass.
A wrong `CHECK_PI_PACKAGE` directory fails instead of skipping.

The script searches the whole tree except top-level `kits/`, `.git/`, `.local/`, `data/`, `state/` and `secrets/`.
Under `scripts/`, a first line of `#!/bin/sh` or `#!/bin/bash` also identifies a shell file without a `.sh` suffix.
A different name or first line can leave a file outside these checks.

## Template and Compose checks

The `memory render` step runs after template validation, once for each stage file in `config/memory/` with the `.yaml` suffix.
The stage name comes from the file name, without the suffix.
It runs `python3 scripts/aws-memory.py check --stage <stage>` and refuses a stale generated template.
It makes no AWS call and does not rewrite the generated files.
The step skips with a note when `scripts/aws-memory.py` does not exist yet, or when no stage file exists.
When the renderer and stage files exist, a missing Python tool fails the check.
The existing template discovery includes each generated `scripts/aws-memory-<stage>.cfn.yaml` automatically.
Thus cfn-lint, size checks and read-only AWS validation cover those templates without a separate file list.

The adjacent `memory stack` and `memory deploy` steps run the two memory regression scripts in online and offline checks.
Each step skips only when its test script is absent; a missing Python tool or failed test fails the gate.
They also run on the exact staged copy in the commit hook.
The development tests of `publish regression` also test the `memory render` step and the tag keys of the memory templates.
The tag key test refuses a resource tag key that differs from a stack tag key of the deploy helper only in case.
A snapshot has no development tests, so it has no tag key test.

A template larger than 51200 bytes fails, also in offline mode.
`aws cloudformation deploy` sends a larger template through S3; the provisioner has no right to upload a template object.

Compose overlays are not checked alone.
The four selected file sets are listed in the [Compose reference](compose-stack.md#file-sets).
A Compose variable absent from `.env.example` fails the check.

A Compose `env_file:` that names anything other than `.env.example` also fails before Compose runs.
This prevents a check from reading a private file whose values might appear in an error.
The check reads Compose source text, not files that the model includes.
The shell environment does not reach Compose; the explicit stub replaces the working `.env`.

The model check also fails on these conditions:

- `gate-egress` is internal or has any member other than the gate.
- `knowledge` has members other than OpenKnowledge and the gate, or lacks either one.
- `knowledge-agent` has members other than OpenKnowledge and the agent, or lacks either one.
- A kit image name differs from `kits.lock`, or a support image tag or digest differs from the lock.

These checks enforce the lock's current pins. They do not add a digest where the lock has none.

## Deploy dry run

The dry run checks exercise `deploy`, `secret`, `dns` and `verify` with the sample and populated stubs.
The option-aware checks also exercise the core and each selected option.
Stubs of `aws` and `curl` appear first on `PATH`; they record and refuse calls.

The check fails on any of these results:

- The dry run exits with an error or prints no expected command.
- It calls an external service or leaves a file.
- It prints a fake Part 1 value other than the public domain name.
- The `secret` dry run accepts a single quote or a final backslash in a value.
- The file set of `verify` is not the file set of `EC2_OPTIONS`, or `verify` does not give that file set and the public names to `scripts/verify-deployed.py`.
- `verify` lets an error text of Compose reach the output of the host.

The check prints no captured dry run output.
The shell environment does not reach the script.
The DNS provider checks use stubs, not live zones.

## Bootstrap dry run

The check runs `scripts/host-bootstrap.sh --dry-run --root <fixture>` in a new temporary directory.
The fixture contains stack values and fake required environment values.
The three generated keys stay empty, as at first boot.

Stubs replace `docker`, `aws`, `systemctl`, `modprobe`, `iptables`, `git` and `curl`.
They answer read-only questions and refuse calls that would change state.

The check fails if the dry run:

- Exits with an error or omits one of its nine steps for valid forms.
- Accepts an unknown option instead of stopping at step 2.
- Changes the fixture, the lock or the host, or leaves a file.
- Prints a fake secret or a trace when called with `sh -x`.
- Uses a `placeholder` function that differs from the deploy script's function.
- Does not name the durable mounts of Docker before its first start of a service, for one of the four file sets.
- Does not name the export of the journal before the start of the CloudWatch agent, in the run with operations on.

The check prints no captured dry run output.
A dry run proves neither a real build nor a real service start.
The [host boot reference](host-boot.md#known-limits) keeps those limits.

## Generated-key checks

The `secret` line also runs the generated-key regression with the same AWS stub.
The stub records reads and writes for both secrets. It refuses a secret value in an argument or a payload without mode 0600.
The fixtures exercise missing and empty generated storage, a repeat with an empty host file, and recovery from the host file.
They check one JSON write with only the three names and no new version when content stays unchanged.
A failed put emits synthetic values on both streams. The production helper suppresses them and bootstrap continues with its environment file intact.
The checks compare captured output and stub logs with the synthetic values. A match fails without printing the value.
The deploy-script cases check empty-name filling, explicit-value preservation, name-only status and absent-name verification failure.
On an older deployment without the generated secret, `verify` skips this check until the owner applies the host stack update.
The template tests require an exact resource reference for the write grant and unchanged read-only environment actions.
The operations regression checks the exact generated-secret boundary grant alongside the existing S3 grants.

Not verified: the live template and boundary apply, or the first write-back on a host. No stub proves effective AWS permissions.

## Environment forms

Each `environment forms <file set>` line runs `scripts/test-env-forms.py` for its options.
The tests use a restricted tool path, an empty environment and new synthetic fixtures.
They exercise `host-bootstrap.sh --check-forms`, `secret`, bootstrap rendering and the bootstrap dry run.

The check fails when any of these conditions occurs:

- A malformed administrator range, certificate address, domain, thinking level, model route, vector size, search selector or list passes.
- With Pangolin, a malformed tunnel endpoint or site identifier passes.
- A message lacks the setting or form rule, or prints the malformed value.
- The test changes its input, leaves a temporary file, calls a host tool or makes an external call.
- `secret` calls AWS or writes its JSON file with a malformed value, even if cleanup removes that file.
- A first-boot exception fails, or `--allow-empty` permits a malformed nonempty value.
- The bootstrap writes a malformed environment file, or its dry run continues beyond step 2 with a malformed form.
- Copilot routes pass with Copilot off, or valid vector boundaries fail.

A Python audit hook records JSON file writes before cleanup. A valid write proves that the observer runs.
The tests also prove that `sh -x` prints no credential and that empty values receive no form test.
The existing option-contract line still tests required names and both MCP bridge names.
The form test does not replace those presence tests.

## Start and option contract

The `start` line runs the bootstrap's real `compose` and `up` functions with a Docker stub.
It fails if a start lacks `--remove-orphans`, the health wait, or an ordered Compose file of the selected set.
A dry run's printed command alone cannot prove these facts.

The `option contract` line compares four readers of `EC2_OPTIONS` for each file set.
They are the check script, bootstrap, deploy script and deployed verifier.
It fails when their file sets differ or the bootstrap needs a name that `secret` does not require.
It includes active edge settings and the two web search names with `EC2_PI_DURABLE_WEB_SEARCH=mcp`.
It tests each of the two names alone.
An unsupported value of the setting must stop `secret` and the bootstrap dry run.
It compares the services that bootstrap starts with both verification service lists.
It tests the agent with a pin and with no pin.
It fails if an unpinned agent gets no skip, or verification accepts a missing service that bootstrap starts.

## Gate checks

The check script runs both commands. You can also run them separately:

| Command | What it checks | Requirements |
| --- | --- | --- |
| `sh scripts/entra-app.sh selftest` | App registration, secret creation, dry runs and refusal to write the sample file, with an Azure CLI stub. | Python 3; no Azure call. |
| `sh ok-auth/local-run.sh --upstream stub` | The actual gate image with a local issuer, Graph and upstream stubs. Tests login, group refusal, token checks, cookies and network paths. | Docker and Python 3; no cloud credential. |

The local gate run starts no edge and proves no real Entra login.
It reads both edge files and uses their fixed `edge-ok` range.
Run only one local gate check at a time, away from an installed stack.
The fixed range can conflict with another run or with the deployed edge.
The [Entra page](../entra.md#prove-the-gate-before-the-deploy) lists each local assertion and its limits.

## Diagram checks

Run `python3 -I scripts/build-diagrams.py --self-test` for the invalid-geometry fixtures.
Run `python3 -I scripts/build-diagrams.py --check` for the five scenes and matching SVG exports.
The [architecture page](../architecture.md#rebuild-and-check) gives tool requirements and the rebuild command.
The check script runs both commands as `selftest scripts/build-diagrams.py` and `scenes docs/diagrams/`.
Offline mode skips `scenes` unless `CHECK_DIAGRAM_NPM_DIR` supplies the pinned packages.
The scene line also skips without Python Playwright with Chromium or without npm.

Run `python3 -I scripts/build-architecture-html.py --selftest` for the fixtures of the interactive page.
Run `python3 -I scripts/build-architecture-html.py --check` to compare the committed page with a new build of its source.
The check script runs both commands as `selftest scripts/build-architecture-html.py` and `architecture docs/architecture.html`.
Both need Python 3 only, so offline mode runs them too.
The [architecture page](../architecture.md#interactive-page) gives the build command.

Run `python3 -I scripts/archify-aws-look.py --selftest` for the fixtures of the AWS look.
Run `python3 -I scripts/archify-aws-look.py --check docs/aws-core.html docs/aws-memory.html docs/aws-mac.html docs/memory-terminal.html` to find a view without the current look.
The check script runs both commands as `selftest scripts/archify-aws-look.py` and `aws look docs/aws-*.html`.
The [architecture page](../architecture.md#how-the-aws-views-are-built) gives the build of the views with archify.

## Hooks and fallback tools

A manual run reads the working tree, including files before `git add`.
The hook exports the index into a temporary directory and runs that copy of the check script.
Thus it checks the proposed commit, not unstaged fixes.
A staged finding refuses a commit even when the working file is correct.
An unstaged file changes nothing in the hook's copy.

The hook sets `CHECK_OFFLINE=1`, `CHECK_NO_SCAN=1` and `CHECK_NO_LOCAL_RUN=1`.
It runs its own scan of staged changes after the checks.
The local gate run stays outside the hook because its fixed network range prevents concurrent runs.

| Setting | Effect |
| --- | --- |
| `CHECK_NO_LOCAL_RUN=1` | Skips the local gate run. Omit it for one manual run when no other gate run uses the fixed range. |
| `CHECK_PI_PACKAGE` | Names the installed Pi package directory for loader tests. The default uses the global npm package directory. |
| `CHECK_DIAGRAM_NPM_DIR` | Names a directory with the exact npm package versions required by `scripts/build-diagrams.py`. The scene check then needs no network call. |

The loader line skips without Node.js, Claude Code or the Pi package.
Install the missing tool to remove that skip; use `CHECK_PI_PACKAGE` when Pi is not in the default directory.

Without local `cfn-lint` or `shellcheck`, the script uses Docker.
It uses `koalaman/shellcheck:stable` and a local `tenant-ec2-cfn-lint` image built from `python:3-slim` and PyPI.
The two Docker Hub base images are pinned by digest.
The script reports a fallback with a `note:` line.

## Publication checks

Publication has separate checks from the static check script:

```sh
python3 scripts/publish_check.py
python3 scripts/publish_check.py --public
sh scripts/scan.sh --level fail tree
```

The [publishing page](../publishing.md) defines the publish set and approval rule.
The public reader check refuses private planning references, machine-specific paths and dated records of one host.
The [security review](../security-review.md) gives suggestions only; it does not replace or disable the secret scan.
