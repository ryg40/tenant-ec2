#!/bin/sh
# Run the static checks of the project over the files of the tree that holds
# this script. The hook pre-commit runs the script on a copy of the index.
# See docs/reference/checks.md.
#
# Usage: scripts/check.sh [--help]
#
# The script searches the whole tree for the files below. It does not search
# the directories kits/, .git/, .local/, data/, state/ and secrets/ at the top
# of the tree. A file with a different name is not checked.
#   template      *.cfn.yaml and *.cfn.yml
#   shell script  *.sh; under scripts/, also each file whose first line is
#                 #!/bin/sh or #!/bin/bash; each file of scripts/git-hooks/
#   Compose file  compose*.yaml and compose*.yml
#
# The checks, in this order. Each check prints one line with its result:
# ok, skip (<reason>) or FAIL.
#   1. cfn-lint           each template
#   2. validate-template  each template, with `aws cloudformation
#                         validate-template` and the provisioner profile. It is
#                         a read-only call to AWS. It is skipped with
#                         CHECK_OFFLINE=1 and on a machine without the profile.
#                         A template larger than 51200 bytes gives FAIL, also
#                         with no call: `aws cloudformation deploy` sends such
#                         a template through S3, and the provisioner has no template
#                         upload object right.
#   2a. memory render     scripts/aws-memory.py check for each stage file in
#                         config/memory/*.yaml. A stale template gives FAIL.
#                         Skip when the renderer or stage files are absent.
#   2b. memory stack      scripts/test-memory-stack.py: schema, policy and template tests.
#   2c. memory deploy     scripts/test-memory-deploy.py: offline deploy and key stubs.
#                         Each test step skips when its script is absent.
#   3. shellcheck         each shell script. The first line gives the dialect.
#                         A file of scripts/git-hooks/ has the dialect sh: the
#                         dispatcher runs a hook with sh.
#   4. compose            each file set of the contract as one Compose model:
#                         compose.yaml with one edge file, with and without
#                         compose.copilot.yaml. A file but compose.yaml is an
#                         overlay, so no file is checked alone. The
#                         environment is a stub of .env.example for a host
#                         with the options of the file set: each empty name
#                         that such a host needs gets the value ./stub, as
#                         scripts/publish_portable.py does. A name of an
#                         option that is off stays empty. The environment of
#                         the shell does not reach the command, and --env-file
#                         replaces the .env of the tree. These results give
#                         FAIL:
#                         - A file of the set names a file in `env_file:` that
#                           is not .env.example. Compose would read that file,
#                           and an error could print a value. Thus the script
#                           does not run Compose on the set. The script reads
#                           the text of the Compose file only, not a file that
#                           it includes.
#                         - Compose says that a variable is not set: a
#                           Compose file uses a name that .env.example lacks.
#                         - The model is not the model of the contract: its
#                           services; a port on a service that is not the
#                           edge; a service that keeps a capability or lacks
#                           no-new-privileges; a network with a route to the
#                           internet other than egress, edge-core and the
#                           network of the gate; the network of the gate
#                           (gate-egress) is internal, or it has a member
#                           that is not the gate; the members of knowledge
#                           are not OpenKnowledge and the gate, or the members
#                           of knowledge-agent are not OpenKnowledge and the
#                           agent; a range of edge-ok that is not the range
#                           that the gate trusts; the fixed address of the
#                           gateway on egress; a path from the gateway to the
#                           Copilot service without the option copilot; an
#                           image that is not the image of kits.lock: the
#                           image name of a kit, and the tag and the digest of
#                           a support image. The agent environment must match
#                           the release names and values with off and mcp.
#                         - A file set with an option renders with no value
#                           for the names of its options.
#                         - A Compose file is in no file set.
#                         One more line is the check of the gateway routes:
#                         FAIL for `drop_params` in a file, for a route of
#                         GPT-6 Luna without the effort xhigh, and for a
#                         route to the service copilot in the route file of
#                         the core.
#                         One more line is the check of the Pangolin
#                         blueprint: scripts/render-pangolin.sh renders it
#                         with two lists of public names. FAIL when a
#                         resource is on for a name outside EC2_EDGE_NAMES,
#                         when `ok` has a login of Pangolin or a target that
#                         is not the gate, and when a path rule of `llm` or of
#                         `agent` is lost.
#   5. core edge          the routes of caddy/, in the Caddy image of kits.lock
#                         and on no network. The container has the user and
#                         the limits of the service `caddy`. caddy/start.sh
#                         writes the configuration, as on the host, and
#                         `caddy validate` reads it. Then Caddy serves it with
#                         one difference: a local authority makes the
#                         certificates. A stub is in place of each service: it
#                         answers with its name and with the path that it
#                         gets. The test sends requests to the ports 80 and
#                         443 and compares each answer. These results give
#                         FAIL: a name of EC2_EDGE_NAMES has no route, or a
#                         name outside the list has one; `ok` does not go to
#                         the gate; `llm` serves a path that is not one of the
#                         five model paths; `agent` serves `/` or `/healthz`,
#                         also for a path that is not in its plain form; a
#                         name other than `ok` answers an address outside
#                         EC2_ADMIN_CIDR; caddy/start.sh accepts a value that
#                         does not have the form of its kind. The check is
#                         skipped with a missing image and CHECK_OFFLINE=1:
#                         that is a missing tool.
#   6. the deploy script, the host template and the DNS providers. Four lines.
#      Each run of a script has an empty shell environment, and a stub of `aws`
#      and of `curl` is first on PATH. The check prints no output of a run. It
#      makes no network call. The scripts and the sample environment file under
#      test are the files of the tree. .env.example must have the layout of the
#      contract: three parts, and in Part 3 one block for each option and for
#      each DNS provider.
#      render            the host template with each value of EnablePangolin and
#                        of EnableBucket, as CloudFormation resolves its
#                        conditions. FAIL: the security group has a rule that
#                        is not a TCP rule without the option, or other UDP
#                        ports than 51820 and 21820 with it; the instance role
#                        has an action with a wildcard, a managed policy, or a
#                        Bedrock resource that is not the Luna model or the
#                        embedding model; the two Bedrock models of .env.example
#                        are not the models of the role. Needs the Python
#                        module yaml.
#      dry run           scripts/aws-host.sh --dry-run for deploy, secret, dns
#                        and verify: with .env.example, and with an environment
#                        file for the core and for each option set. The stub
#                        records a call and refuses it. FAIL: a dry run exits
#                        with an error, it prints no line of a command that it
#                        must show, it shows a service, a record or a parameter
#                        of an option that is off, it makes a call, it leaves a
#                        file, or it prints a fake value. More results give
#                        FAIL:
#                        - The file set of `verify` is not the file set of the
#                          contract for the value of EC2_OPTIONS, or its
#                          services are not the services of the Compose files
#                          of that set. `verify` does not compare COMPOSE_FILE
#                          of the host with that set. `verify` does not give
#                          that set and the public names to
#                          scripts/verify-deployed.py, or it lets an error
#                          text of Compose reach the output of the host.
#                        - `deploy` does not give EC2_BEDROCK_EMBED_MODEL to
#                          the parameter BedrockEmbedModel.
#                        - `secret` gets no --allow-empty in a run for an
#                          option set: a name of an option that is off must
#                          need no value. A name of Part 1 and an empty name
#                          of an active option must stop a real run, and
#                          --allow-empty must accept each of them. The first
#                          run with the option pangolin has two empty names.
#                        - A run that must stop does not stop: an option name,
#                          an edge name or a provider that the script does not
#                          know, the option pangolin with another provider
#                          than porkbun, a value with a single quote and a
#                          value with a backslash at its end.
#                        - The bootstrap names a command of the deploy script
#                          that the script does not have.
#      secret            a real run of `secret` for the core, for each option
#                        set and for the first run with the option pangolin. The
#                        stub keeps the file of the secret. FAIL: the secret
#                        lacks a name of Part 1 or a value of the environment
#                        file, or it holds a name or a value of a DNS provider
#                        block. The Porkbun pair with the option pangolin is
#                        the one exception. Then the Python text of
#                        scripts/host-bootstrap.sh renders the environment file
#                        of the host from that secret. FAIL: the bootstrap
#                        refuses the secret; COMPOSE_FILE of that file is not
#                        the file set of the contract; the file set test of
#                        `verify` fails on that file, or passes on the file of
#                        another option set.
#      providers         scripts/dns-records.sh with each provider and a stub
#                        that keeps the records of a fake zone. FAIL: `plan`
#                        does not print the record set or makes a call; `apply`
#                        changes a record that is correct or does not change
#                        one that differs; a second `apply` changes a record; a
#                        credential is in an argument of a call or in a file
#                        with another mode than 0600; a credential that the
#                        provider refuses does not stop the script; the provider
#                        porkbun does not make the calls of the earlier `dns`
#                        command; `dns` for the core does not give one record
#                        for each name of EC2_EDGE_NAMES; the names of a
#                        provider file are not the names of its block of
#                        .env.example.
#   7. dry run, bootstrap scripts/host-bootstrap.sh --dry-run with --root: a
#                         fixture in the temporary directory is in place of /.
#                         One run for each file set of the contract: the core,
#                         copilot, pangolin, and the two. A fixture holds a
#                         stack values file and an environment file with
#                         EC2_OPTIONS and a fake value for each empty name of
#                         Part 1 of .env.example and of the Part 3 blocks of
#                         its options. The names of each other option stay
#                         empty, and the three names that the bootstrap makes
#                         stay empty, as at a first boot. A stub of docker,
#                         aws, systemctl, modprobe, iptables, git and curl is
#                         first on PATH: it answers a question that changes
#                         nothing, and it records and refuses each other call.
#                         The environment of the shell does not reach the
#                         script. These results give FAIL: a dry run exits
#                         with an error, it does not print each of its nine
#                         steps, its test of the fixture fails, its value of
#                         COMPOSE_FILE is not the file set of the contract, it
#                         names a step of an option that is off (the core run
#                         must name no Pangolin step, no WireGuard module and
#                         no Copilot route), it lacks a step of an option that
#                         is on, it makes a call that changes the host, it
#                         changes a fixture or kits.lock, it leaves a file, it
#                         prints a fake value, a run with `sh -x` prints a
#                         trace, or the function `placeholder` of the
#                         bootstrap is not equal to the function of
#                         scripts/aws-host.sh. Two more fixtures must not
#                         pass the test of the bootstrap: the option pangolin
#                         with the values of the core only, and an option name
#                         that the bootstrap does not know. More results give
#                         FAIL: a run does not name the durable mounts of
#                         Docker before its first start of a service; a run
#                         of the core with the operations of the stack on
#                         does not name the export of the journal before the
#                         start of the CloudWatch agent. The check prints no
#                         output of a dry run.
#      environment forms Four lines, one per file set, from test-env-forms.py.
#                         FAIL: a wrong form passes; a message holds a value or lacks
#                         its rule; the test changes input or calls an external tool;
#                         secret writes a wrong form; first-boot exceptions fail;
#                         the bootstrap dry run continues after a wrong form.
#   8. start, bootstrap   the functions `compose` and `up` of
#                         scripts/host-bootstrap.sh, as a run that is not a dry
#                         run, with a stub of docker that keeps its arguments
#                         and starts nothing. One start for each file set. A
#                         dry run prints a text only, so this is the test of
#                         the real command. FAIL: the Compose command of a
#                         start has no --remove-orphans, or it does not name
#                         each Compose file of the file set in the order of
#                         COMPOSE_FILE, or it does not wait for the health.
#   9. option contract    Four parts read EC2_OPTIONS: the bootstrap, the deploy
#                         script, the verifier and this script. One line proves
#                         that they agree, for each of the four file sets. It
#                         uses the outputs of the checks 6 and 7.
#                         - The Compose files: the file set of `verify`,
#                           COMPOSE_FILE of the bootstrap and the file set that
#                           the verifier accepts are the file set of this
#                           script.
#                         - The necessary names: `secret` and the test of the
#                           bootstrap get an environment file with no value.
#                           Each one names the names that it needs. Then a dry
#                           run of the bootstrap gets a fixture with no value
#                           for one setting of the script of the active edge.
#                           FAIL: the bootstrap needs a name that `secret`
#                           does not need. The same is true for the two
#                           web search names with the web search mcp. Each
#                           name is tested alone. Both values pass together.
#                           An unsupported selector stops secret and the
#                           bootstrap dry run, including a new literal alias.
#                         - The services: the commands of `verify` run in a
#                           directory that is in place of the clone of the
#                           host, with a stub of docker. The services that run
#                           are the services that a dry run of the bootstrap
#                           starts. Two states of kits.lock: the agent kit
#                           with a pin and with no pin. FAIL: the commands do
#                           not pass; the services that `verify` reports as
#                           running or the services that the verifier checks
#                           are not the services that the bootstrap starts;
#                           the agent with no pin gets no skip line; the
#                           commands pass when a service that the bootstrap
#                           starts does not run.
#  10. self-tests         the self-test of each script that has one, with its
#                         own fixtures and stubs. Ten lines:
#                         selftest   scripts/aws-provisioner.sh,
#                                    scripts/entra-app.sh,
#                                    scripts/build-diagrams.py,
#                                    scripts/dns-migration.py and
#                                    scripts/build-architecture-html.py
#                         selftest   scripts/security-review.sh --self-test. The
#                                    script always exits with 0, so the test is
#                                    the line that it prints. The review itself
#                                    is never a part of this script or of a
#                                    hook: it gives suggestions only.
#                         loaders    skills/security-review/test-loaders.py:
#                                    Claude Code and Pi load the skill of a new
#                                    clone, with a stub of the model API on this
#                                    machine. Skip without the command claude,
#                                    the command node or the Pi package.
#                         scenes     scripts/build-diagrams.py --check:
#                                    Excalidraw loads each scene of
#                                    docs/diagrams/, and each image is the
#                                    export of its scene. npm reads its
#                                    registry, so CHECK_OFFLINE=1 gives a skip.
#                                    Skip without the Python module playwright
#                                    with Chromium, or without npm.
#                         architecture
#                                    scripts/build-architecture-html.py --check:
#                                    docs/architecture.html is the build of
#                                    docs/diagrams/architecture-graph.json. Needs
#                                    python3 only; no network call and no skip
#                                    for CHECK_OFFLINE=1.
#                         local run  ok-auth/local-run.sh --upstream stub: the
#                                    gate starts in a Compose project of its
#                                    own, with a stub in place of Entra ID. The
#                                    run makes a Docker network with a fixed
#                                    range, so two runs at the same time on one
#                                    machine do not work. Skip with
#                                    CHECK_NO_LOCAL_RUN=1 (the hook sets it).
#  11. operations         scripts/test-operations.py: offline template and IAM limits,
#                         journal export, cursor recovery, rotation and agent files
#  12. storage            scripts/test-storage.py: durable startup, systemd syntax and
#                         EFS/Git ownership in isolated fixtures; needs systemd tools
#                         and root for the numeric ownership fixture
#  13. verifier           scripts/test-verify-deployed.py: existing-container and internal
#                         endpoint checks with offline Docker, HTTP, Node and AWS
#                         fixtures; one test for each file set of the contract: the
#                         verifier checks the services of that file set only
#  14. publish            development publish tests when present; absent in runtime exports
#  15. scan               scripts/scan.sh tree (docs/secret-handling.md)
# Each of the four test lines 11 to 14 gives a skip line when its test file is
# not in the copy.
#
# A kind of file that does not exist yet gives a skip line. The script uses no
# Git command: it reads each file of its tree. By hand, that is the working
# tree, so the script also checks a file before `git add`, and each worktree
# checks its own files. In the hook, that is a copy of the index, so the script
# checks the content of the commit and nothing else.
#
# Exit: 0 pass, 1 a finding or a missing tool, 2 usage or tool error.
#
# Needs: a POSIX shell with awk, find, grep, sed and sort; cfn-lint (`pipx
# install cfn-lint`); shellcheck 0.10.0 or later; Docker with the Compose
# plugin for a Compose file, for the core edge and for the scan; the AWS CLI
# version 2 for validate-template; python3 for the Compose check, for each dry
# run and for each check of the deploy script; the Python module yaml for the
# render check; envsubst (the package gettext) for the dry run of the bootstrap.
# Three lines of the self-tests need a tool of an optional part, and give a
# skip line with its name when the tool is not there: the loader tests need
# Git, Node.js, the command claude and the Pi package; the scene check needs the
# Python module playwright with Chromium, and npm. Such a skip is not a missing
# tool: the script can pass with it.
# Without the cfn-lint or the shellcheck command, the script uses Docker:
# - shellcheck: the image koalaman/shellcheck:stable of Docker Hub, pinned by
#   its digest;
# - cfn-lint: no image of its maintainers exists, so the script builds the
#   local image tenant-ec2-cfn-lint:<version> from python:3-slim, pinned by its
#   digest, and PyPI.
# The script says so in a line that starts with "note:". A check with no tool
# prints "skip (tool missing)". The script then exits with 1 and names the
# tool. A missing tool is never a pass.
#
# Environment:
#   CHECK_OFFLINE=1         no network call: no validate-template, no pull and
#                           no build of an image (the hook sets it)
#   CHECK_NO_SCAN=1         no scan at the end; only for a caller that runs
#                           scripts/scan.sh itself (the hook sets it)
#   CHECK_NO_LOCAL_RUN=1    no local run of the gate: the run makes a Docker
#                           network with a fixed range, so two runs at the
#                           same time on one machine do not work (the hook
#                           sets it)
#   CHECK_PI_PACKAGE        the directory of the Pi package for the loader
#                           tests; the default is the package
#                           @earendil-works/pi-coding-agent in the global
#                           directory of npm
#   CHECK_DIAGRAM_NPM_DIR   a directory with the pinned npm packages of
#                           scripts/build-diagrams.py (its option --npm-dir);
#                           the scene check then makes no network call
#   CHECK_ENGINE            auto (default), binary or docker: the source of
#                           cfn-lint and shellcheck
#   CHECK_SHELLCHECK_IMAGE  replaces the pinned shellcheck image
#   CHECK_CFN_LINT_IMAGE    replaces the local image; the script pulls this
#                           image and builds nothing
#   EC2_PROJECT, EC2_AWS_PROFILE, EC2_AWS_REGION
#                           the profile and the region of validate-template,
#                           as in scripts/aws-provisioner.sh (tenant-ec2,
#                           us-east-1)

set -eu

# The release of cfn-lint in the local image. A cfn-lint command of the
# machine can be a different release.
CFN_LINT_VERSION=1.57.2
# The scripts use `test && test || die`. A shellcheck before this release
# reports each such line as SC2015.
SHELLCHECK_MIN=0.10.0
# `validate-template --template-body` takes no larger template, and
# `aws cloudformation deploy` sends a larger template through S3. The
# provisioner has no template upload object right, so a larger template is a finding.
TEMPLATE_BODY_MAX=51200
# The images of Docker Hub, each pinned by its digest: a tag moves, a digest
# does not. To renew a pin, run `docker buildx imagetools inspect <tag>` and
# write the digest of the index and the date here.
# koalaman/shellcheck:stable, 2026-10-09 (shellcheck 0.11.0)
SHELLCHECK_IMAGE=koalaman/shellcheck:stable@sha256:bb596a0d169b85ddd81d8b6d3a2ff6d5baf5fca10b97f575ebc647c3dff62b3d
# python:3-slim, 2026-10-09
CFN_LINT_BASE_IMAGE=python:3-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2

usage() {
    cat <<'EOT'
usage: scripts/check.sh [--help]

Run the static checks of the project over the files of the tree that holds
this script:
  cfn-lint           each *.cfn.yaml and *.cfn.yml
  validate-template  the same templates, with the AWS profile tenant-ec2;
                     a template larger than 51200 bytes gives FAIL
  memory render      scripts/aws-memory.py check for each config/memory/*.yaml;
                     skip when the renderer or stage files are absent
  memory stack       python3 scripts/test-memory-stack.py: schema and policy tests
  memory deploy      python3 scripts/test-memory-deploy.py: offline administration tests
  shellcheck         each *.sh; under scripts/, each file with the first line
                     #!/bin/sh or #!/bin/bash; each file of scripts/git-hooks/
  compose            each file set of the contract as one Compose model, with
                     a stub of .env.example for its options; FAIL for an
                     env_file that is not .env.example, for a variable that
                     is not set, for a model that is not the model of the
                     contract (services, ports, capabilities, networks) and
                     for an option name that has no test; then the gateway
                     routes: FAIL for drop_params and for a route of GPT-6
                     Luna without the effort xhigh
  core edge          the routes of caddy/ in the Caddy image of kits.lock, on
                     no network, with a stub in place of each service; FAIL
                     for a route that EC2_EDGE_NAMES does not name, for a path
                     that a path rule must stop, and for an answer to an
                     address outside EC2_ADMIN_CIDR
  render             the host template with each value of EnablePangolin; FAIL
                     for a UDP rule without the option and for a wildcard or
                     another model in the instance role
  dry run            scripts/aws-host.sh --dry-run for deploy, secret, dns and
                     verify, for the core and for each option set; FAIL for a
                     call to aws or curl, for a printed fake value and for a
                     file set that is not the file set of the contract
  secret             a real run of `secret` with a stub of aws; FAIL for a name
                     of a DNS provider in the secret and for a secret that the
                     bootstrap refuses
  providers          scripts/dns-records.sh with each provider and a stub of
                     its tool; FAIL for a change of a correct record and for a
                     credential in an argument
  dry run            scripts/host-bootstrap.sh --dry-run with a fixture in
                     place of /, for the core and for each set of options;
                     FAIL for a step of an option that is off, for a call
                     that changes the host, for a changed file, for a printed
                     fake value and for a trace
  environment forms  four file sets: wrong forms stop before a secret write;
                     safe messages, first-boot exceptions and a strict dry run
  start              the real start of scripts/host-bootstrap.sh with a stub of
                     docker; FAIL for a start with no --remove-orphans and for
                     a start that does not name each Compose file of the set
  option contract    the bootstrap, the deploy script, the verifier and this
                     script agree for each file set: on the Compose files, on
                     the necessary names and on the services, with and with
                     no pin of the agent kit
  selftest           the self-test of scripts/aws-provisioner.sh, of
                     scripts/entra-app.sh, of scripts/security-review.sh, of
                     scripts/build-diagrams.py, of scripts/dns-migration.py and
                     of scripts/build-architecture-html.py
  loaders            the loader tests of the security review skill; skip
                     without Claude Code, Node.js or the Pi package
  scenes             Excalidraw loads each scene of docs/diagrams/; skip with
                     CHECK_OFFLINE=1 and without Playwright or npm
  architecture       docs/architecture.html is the build of its graph source;
                     python3 only, no network call
  local run          the local run of the gate with a stub in place of Entra ID;
                     skip with CHECK_NO_LOCAL_RUN=1
  operations         offline operations, IAM and journal-export regressions
  storage            offline startup, systemd and Git ownership regressions
  verifier           offline deployed-service verifier regressions, for each file
                     set of the contract
  macos             dedicated host/infra/bootstrap/credentials tests, standalone Compose,
                    candidate JSON schema (jsonschema), and zsh syntax; no Mac runtime claim
  publish            development publish tests when present; skip in runtime exports
  scan               scripts/scan.sh tree

The search covers the whole tree without kits/, .git/, .local/, data/, state/
and secrets/. Each check prints one line: ok, skip (<reason>) or FAIL.
Exit: 0 pass, 1 a finding or a missing tool, 2 usage or tool error.

Environment:
  CHECK_OFFLINE=1   no network call (no validate-template, no image pull or build)
  CHECK_NO_SCAN=1   no scan; only for a caller that runs scripts/scan.sh itself
  CHECK_NO_LOCAL_RUN=1  no local run of the gate (a hook sets it)
  CHECK_ENGINE      auto (default), binary or docker, for cfn-lint and shellcheck

Tools: cfn-lint, shellcheck 0.10.0 or later, Docker with the Compose plugin,
the AWS CLI version 2, python3 with the module yaml, envsubst. The head of
this file gives the details.
EOT
}

case $# in
    0) ;;
    1) case $1 in
           -h|--help) usage; exit 0 ;;
           *) usage >&2; exit 2 ;;
       esac ;;
    *) usage >&2; exit 2 ;;
esac

CHECK_ENGINE=${CHECK_ENGINE:-auto}
case $CHECK_ENGINE in
    auto|binary|docker) ;;
    *) echo "check: error: CHECK_ENGINE must be auto, binary or docker" >&2; exit 2 ;;
esac
offline=0
[ "${CHECK_OFFLINE:-0}" != 1 ] || offline=1
project=${EC2_PROJECT:-tenant-ec2}
profile=${EC2_AWS_PROFILE:-$project}
region=${EC2_AWS_REGION:-us-east-1}
shellcheck_image=${CHECK_SHELLCHECK_IMAGE:-$SHELLCHECK_IMAGE}
cfn_lint_image=${CHECK_CFN_LINT_IMAGE:-tenant-ec2-cfn-lint:$CFN_LINT_VERSION}

# The tree that holds this script: the working tree, or the copy of the index
# that the hook makes. The script finds the tree from its own path and uses no
# Git command, so the tree does not have to be a Git repository.
top=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$top"

work=$(mktemp -d "${TMPDIR:-/tmp}/check.XXXXXX")
trap 'rm -rf "$work"' EXIT
# In sh, a signal handler does not end the script; exit runs the EXIT trap.
trap 'exit 130' INT TERM HUP

n_ok=0
n_skip=0
n_fail=0
missing=

# result <check> ok|skip|FAIL [<detail>]: the one line of a check.
result() {
    case $2 in
        ok) n_ok=$((n_ok + 1)) ;;
        skip) n_skip=$((n_skip + 1)) ;;
        *) n_fail=$((n_fail + 1)) ;;
    esac
    printf '%s: %s%s\n' "$1" "$2" "${3:+ ($3)}"
}

# no_tool <check> <tool>: the check has no tool. The script fails at the end.
no_tool() {
    case ",$missing," in
        *", $2,"*) ;;
        *) missing="$missing, $2" ;;
    esac
    result "$1" skip "tool missing"
}

note() {
    echo "note: $*"
}

die() {
    echo "check: error: $*" >&2
    exit 2
}

# run <check> <command>...: run one check. The output of the command shows
# only on a failure.
run() {
    label=$1
    shift
    if "$@" >"$work/out" 2>&1 </dev/null; then
        result "$label" ok
    else
        cat "$work/out" >&2
        result "$label" FAIL
    fi
}

# --- tools ------------------------------------------------------------------

docker_state=
# docker_ok: the Docker daemon answers. The answer is kept.
docker_ok() {
    if [ -z "$docker_state" ]; then
        if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
            docker_state=yes
        else
            docker_state=no
        fi
    fi
    [ "$docker_state" = yes ]
}

# bind_mount <path>: a read-only --mount value for <path> at the same path.
# The value is CSV and each field is quoted, so a path may hold a blank, a
# comma or a colon.
bind_mount() {
    q=$(printf '%s' "$1" | sed 's/"/""/g')
    printf 'type=bind,"source=%s","target=%s",readonly' "$q" "$q"
}

# in_image <image> <argument>...: run the command of the image on the working
# tree, read-only and with no network.
in_image() {
    image=$1
    shift
    docker run --rm --network none --mount "$(bind_mount "$top")" -w "$top" "$image" "$@"
}

# version_ge <a> <b>: the release <a> is <b> or later.
version_ge() {
    [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | head -n 1)" = "$2" ]
}

# The source of each tool: binary, docker or missing.
shellcheck_from=missing
cfn_lint_from=missing

select_shellcheck() {
    case $CHECK_ENGINE in
        auto|binary)
            if command -v shellcheck >/dev/null 2>&1; then
                v=$(shellcheck --version 2>/dev/null | sed -n 's/^version: *v\{0,1\}\([0-9][0-9.]*\).*/\1/p')
                if [ -n "$v" ] && version_ge "$v" "$SHELLCHECK_MIN"; then
                    shellcheck_from=binary
                    return 0
                fi
                note "the shellcheck command is release ${v:-unknown}; the check needs $SHELLCHECK_MIN or later"
            fi ;;
    esac
    case $CHECK_ENGINE in
        auto|docker)
            if docker_ok; then
                if docker image inspect "$shellcheck_image" >/dev/null 2>&1 ||
                    { [ "$offline" -eq 0 ] && docker pull -q "$shellcheck_image" >/dev/null 2>&1; }; then
                    shellcheck_from=docker
                    note "shellcheck runs from the Docker image $shellcheck_image"
                fi
            fi ;;
    esac
}

select_cfn_lint() {
    case $CHECK_ENGINE in
        auto|binary)
            if command -v cfn-lint >/dev/null 2>&1; then
                cfn_lint_from=binary
                return 0
            fi ;;
    esac
    case $CHECK_ENGINE in
        auto|docker)
            if docker_ok; then
                if docker image inspect "$cfn_lint_image" >/dev/null 2>&1; then
                    cfn_lint_from=docker
                elif [ "$offline" -eq 1 ]; then
                    :
                elif [ -n "${CHECK_CFN_LINT_IMAGE:-}" ]; then
                    ! docker pull -q "$cfn_lint_image" >/dev/null 2>&1 || cfn_lint_from=docker
                else
                    note "the image $cfn_lint_image is built now: cfn-lint $CFN_LINT_VERSION of PyPI on $CFN_LINT_BASE_IMAGE"
                    if printf 'FROM %s\nRUN pip install --no-cache-dir --quiet cfn-lint==%s\nENTRYPOINT ["cfn-lint"]\n' \
                        "$CFN_LINT_BASE_IMAGE" "$CFN_LINT_VERSION" | docker build -q -t "$cfn_lint_image" - >/dev/null 2>&1; then
                        cfn_lint_from=docker
                    fi
                fi
                [ "$cfn_lint_from" != docker ] || note "cfn-lint runs from the Docker image $cfn_lint_image"
            fi ;;
    esac
}

shellcheck_cmd() {
    if [ "$shellcheck_from" = docker ]; then
        in_image "$shellcheck_image" "$@"
    else
        shellcheck "$@"
    fi
}

cfn_lint_cmd() {
    if [ "$cfn_lint_from" = docker ]; then
        in_image "$cfn_lint_image" "$@"
    else
        cfn-lint "$@"
    fi
}

# has_profile <name>: the AWS CLI configuration of this user has the profile.
has_profile() {
    config=${AWS_CONFIG_FILE:-${HOME:-}/.aws/config}
    credentials=${AWS_SHARED_CREDENTIALS_FILE:-${HOME:-}/.aws/credentials}
    { [ -f "$config" ] && grep -qFx "[profile $1]" "$config"; } ||
        { [ -f "$credentials" ] && grep -qFx "[$1]" "$credentials"; }
}

validate_template() {
    aws cloudformation validate-template --profile "$profile" --region "$region" \
        --cli-connect-timeout 10 --cli-read-timeout 30 --template-body "file://$1"
}

# env_file_names <Compose file>: print each file name that the text of the
# Compose file gives in an `env_file` entry, one name in each line. A form
# that this reader does not know is printed as it is, so it is never equal to
# the name of the sample file.
env_file_names() {
    awk '
    function trim(s) {
        gsub(/^[ \t]+|[ \t\r]+$/, "", s)
        if (s ~ /^".*"$/ || s ~ /^\047.*\047$/) s = substr(s, 2, length(s) - 2)
        return s
    }
    function emit(s) {
        s = trim(s)
        if (s != "") print s
    }
    {
        line = $0
        sub(/(^|[ \t])#.*$/, "", line)
        if (line ~ /^[ \t\r]*$/) next
        match(line, /^[ \t]*/)
        indent = RLENGTH
        if (in_list) {
            item = substr(line, indent + 1)
            if (indent > key_col || (indent == key_col && item ~ /^-/)) {
                sub(/^-[ \t]*/, "", item)
                if (item ~ /^["\047]?path["\047]?[ \t]*:/) {
                    sub(/^["\047]?path["\047]?[ \t]*:/, "", item)
                    emit(item)
                } else if (item !~ /^["\047]?(required|format)["\047]?[ \t]*:/) {
                    emit(item)
                }
                next
            }
            in_list = 0
        }
        if (match(line, /(^|[ \t{,])["\047]?env_file["\047]?[ \t]*:/)) {
            key_col = RSTART - 1
            if (substr(line, RSTART, 1) ~ /[ \t{,]/) key_col++
            value = trim(substr(line, RSTART + RLENGTH))
            if (value == "") {
                in_list = 1
            } else if (value ~ /^\[[^][{}]*\]$/) {
                n = split(substr(value, 2, length(value) - 2), part, ",")
                for (i = 1; i <= n; i++) emit(part[i])
            } else {
                print value
            }
        }
    }' "$1"
}

# The file sets of the contract: one line for each value of EC2_OPTIONS, in the
# form <options>|<files>. scripts/host-bootstrap.sh makes the same list from
# EC2_OPTIONS; the dry run check of the bootstrap compares the two.
FILE_SETS='|compose.yaml compose.caddy.yaml
copilot|compose.yaml compose.caddy.yaml compose.copilot.yaml
pangolin|compose.yaml compose.pangolin.yaml
pangolin,copilot|compose.yaml compose.pangolin.yaml compose.copilot.yaml'

# stub_env <options> <file>: write a stub of .env.example for a host with these
# options. An empty name gets the value ./stub, as scripts/publish_portable.py
# does, when the host needs the name:
#   - a name of Part 1 or of Part 2;
#   - a name of the block `Option <name>` of Part 3, when the option is on;
#   - a name of the block `DNS provider porkbun`, with the option pangolin: the
#     secret holds no other name of a DNS provider block.
# Each other empty name stays empty.
stub_env() {
    awk -v options="$1" '
    function needed() {
        if (part != 3) return 1
        if (kind == "option") return index("," options ",", "," block ",") > 0
        if (kind == "dns") return block == "porkbun" && index("," options ",", ",pangolin,") > 0
        return 0
    }
    /^# ===== Part [0-9]/ { part = $4; sub(/:.*$/, "", part); kind = ""; block = "" }
    /^# ----- Option [a-z0-9]+ -----/ { kind = "option"; block = $4 }
    /^# ----- DNS provider [a-z0-9]+ -----/ { kind = "dns"; block = $5 }
    /^[A-Za-z_][A-Za-z0-9_]*=/ {
        i = index($0, "=")
        name = substr($0, 1, i - 1)
        value = substr($0, i + 1)
        if (name == "EC2_OPTIONS") value = options
        else if (value ~ /^[ \t]*$/ && needed()) value = "./stub"
        print name "=" value
    }' .env.example >"$2"
}

# compose_model <environment file> <Compose file>...: print the model of a file
# set as JSON. The environment of the shell does not reach the command, and
# --env-file replaces the .env of the tree. Each value of the model is a value
# of the stub.
compose_model() {
    stub=$1
    shift
    set -- "$@" --
    while [ "$1" != -- ]; do
        set -- "$@" -f "$top/$1"
        shift
    done
    shift
    set -- docker compose --project-directory "$top" "$@" --env-file "$stub" config --format json
    set -- PATH="$PATH" HOME="${HOME:-/}" "$@"
    [ -z "${DOCKER_CONFIG:-}" ] || set -- DOCKER_CONFIG="$DOCKER_CONFIG" "$@"
    [ -z "${DOCKER_HOST:-}" ] || set -- DOCKER_HOST="$DOCKER_HOST" "$@"
    (cd "$work" && env -i "$@")
}

# The test of the model of one file set. Arguments: the JSON file of the model,
# the options, the tree. It prints one line for each problem.
compose_model_py='
import ipaddress
import json
import re
import sys

model = json.load(open(sys.argv[1], encoding="utf-8"))
options = [name for name in sys.argv[2].split(",") if name]
top = sys.argv[3].rstrip("/") + "/"
fixture = dict(line.rstrip("\n").split("=", 1)
               for line in open(sys.argv[4], encoding="utf-8") if "=" in line)
services = model.get("services") or {}
networks = model.get("networks") or {}
problems = []

core = ["litellm", "litellm-db", "ok-auth", "open-knowledge", "openviking", "pi-durable"]
edge = ["gerbil", "pangolin", "traefik"] if "pangolin" in options else ["caddy"]
want = sorted(core + edge + (["copilot"] if "copilot" in options else []))
if sorted(services) != want:
    problems.append("the services are %s, not %s" % (" ".join(sorted(services)), " ".join(want)))

# Only the edge publishes ports.
ports = {"caddy": {("80", 80, "tcp"), ("443", 443, "tcp")},
         "gerbil": {("80", 80, "tcp"), ("443", 443, "tcp"), ("51820", 51820, "udp"), ("21820", 21820, "udp")}}
for name, service in services.items():
    have = {(str(p.get("published")), p.get("target"), p.get("protocol", "tcp")) for p in service.get("ports") or []}
    if have != ports.get(name, set()):
        problems.append("the service %s publishes the ports %s" % (name, sorted(have) or "none"))

# Each service drops each capability and sets no-new-privileges.
back = {"caddy": ["NET_BIND_SERVICE"], "gerbil": ["NET_ADMIN"]}
for name, service in services.items():
    if service.get("cap_drop") != ["ALL"]:
        problems.append("the service %s does not drop each capability" % name)
    if "no-new-privileges:true" not in (service.get("security_opt") or []):
        problems.append("the service %s does not set no-new-privileges" % name)
    if (service.get("cap_add") or []) != back.get(name, []):
        problems.append("the service %s gets a capability back that the check does not know" % name)
    if service.get("privileged") or service.get("network_mode") == "host" or service.get("pid") == "host":
        problems.append("the service %s is privileged, or it uses the network or the processes of the host" % name)
    for mount in service.get("volumes") or []:
        source = mount.get("source") or ""
        if "docker.sock" in source:
            problems.append("the service %s mounts the Docker socket" % name)
        if mount.get("type") == "bind" and not source.startswith(top):
            if not source.startswith(("/srv/data/", "/srv/efs/")):
                problems.append("the service %s binds a path outside /srv/data and /srv/efs" % name)
            if (mount.get("bind") or {}).get("create_host_path") is not False:
                problems.append("the service %s binds a host path with no create_host_path: false" % name)

# A network that needs no internet is internal.
members = {}
for name, service in services.items():
    for network in service.get("networks") or {}:
        members.setdefault(network, set()).add(name)
# The gate can have one more: its route to the identity provider, with no other member.
open_networks = sorted(name for name, network in networks.items() if not (network or {}).get("internal"))
if [name for name in open_networks if name != "gate-egress"] != ["edge-core", "egress"]:
    problems.append("the networks with a route to the internet are %s, not edge-core, egress and the network of the gate"
                    % " ".join(open_networks))
# The gate has a route to the identity provider: its network is in the model, it is not
# internal, and the gate is its one member.
if "gate-egress" not in open_networks:
    problems.append("the network gate-egress is internal or is not in the model: the gate has no route to the identity provider")
if members.get("gate-egress", set()) != {"ok-auth"}:
    problems.append("the members of gate-egress are %s, not ok-auth only" % (" ".join(sorted(members.get("gate-egress", set()))) or "none"))
# OpenKnowledge has two networks, and each one has one other member: the gate on
# knowledge, the agent on knowledge-agent. The agent does not pass the gate.
for network, wanted in (("knowledge", {"ok-auth", "open-knowledge"}), ("knowledge-agent", {"open-knowledge", "pi-durable"})):
    if members.get(network, set()) != wanted:
        problems.append("the members of %s are %s, not %s" % (
            network, " ".join(sorted(members.get(network, set()))) or "none", " ".join(sorted(wanted))))
edge_core = {"pangolin", "gerbil"} if "pangolin" in options else {"caddy"}
if members.get("edge-core", set()) != edge_core:
    problems.append("the members of edge-core are %s" % " ".join(sorted(members.get("edge-core", set()))))
egress = {"litellm", "pi-durable"} | ({"copilot"} if "copilot" in options else set())
if members.get("egress", set()) != egress:
    problems.append("the members of egress are %s" % " ".join(sorted(members.get("egress", set()))))
for network, service in (("edge-llm", "litellm"), ("edge-ok", "ok-auth"), ("edge-ov", "openviking"), ("edge-agent", "pi-durable")):
    target = {"gerbil"} if "pangolin" in options else {"caddy"}
    if members.get(network, set()) != target | {service}:
        problems.append("the members of %s are %s" % (network, " ".join(sorted(members.get(network, set())))))

# The fixed range of edge-ok: the gate trusts a forwarded header only from this range.
# The settings file of the gate names the same range, when the tree has the file.
gate_range = "198.18.5.0/29"
try:
    text = open(top + "ok-auth/oauth2-proxy.cfg", encoding="utf-8").read()
    found = re.search(r"^trusted_proxy_ips\s*=\s*\[\s*\"([^\"]+)\"\s*\]", text, re.M)
    gate_range = found.group(1) if found else ""
except OSError:
    pass
try:
    subnet = networks["edge-ok"]["ipam"]["config"][0]["subnet"]
except (KeyError, IndexError, TypeError):
    subnet = ""
if not subnet or subnet != gate_range:
    problems.append("the range of edge-ok is not the one range that the gate trusts (ok-auth/oauth2-proxy.cfg)")

# The fixed address of the gateway on egress: inside the subnet, outside the range
# from which Docker gives an address to another container.
try:
    address = ipaddress.ip_address(services["litellm"]["networks"]["egress"]["ipv4_address"])
    config = networks["egress"]["ipam"]["config"][0]
    if address not in ipaddress.ip_network(config["subnet"]) or address in ipaddress.ip_network(config["ip_range"]):
        problems.append("the address of litellm on egress is not inside subnet and outside ip_range")
    fixed = [name for name, service in services.items()
             for network in (service.get("networks") or {}).values() if (network or {}).get("ipv4_address")]
    if fixed != ["litellm"]:
        problems.append("the services with a fixed address are %s, not litellm only" % " ".join(fixed))
except (KeyError, IndexError, TypeError, ValueError):
    problems.append("litellm has no fixed IPv4 address on egress, or egress has no subnet and ip_range")

# Without the option copilot, the gateway has no path to the Copilot service.
gateway = services.get("litellm") or {}
command = " ".join(gateway.get("command") or [])
if "copilot" in options:
    if "/config/config.copilot.yaml" not in command or "copilot" not in members:
        problems.append("the gateway does not read the route file of the option copilot")
else:
    if "copilot" in networks or "COPILOT_MASTER_KEY" in (gateway.get("environment") or {}) or "copilot" in command:
        problems.append("the gateway has a network, a key or a route file of the option copilot")


# The release reads these agent names. Compare values too: a correct name with the
# wrong source setting is not a working bridge. No value reaches a failure message.
agent = services.get("pi-durable", {}).get("environment") or {}
expected_agent = {
    "RESEARCHER_DATA_DIR": "/data",
    "RESEARCHER_API_KEY": fixture.get("EC2_PI_DURABLE_API_KEY"),
    "LITELLM_BASE_URL": "http://litellm:4000/v1",
    "LITELLM_API_KEY": fixture.get("EC2_PI_DURABLE_LITELLM_API_KEY"),
    "RESEARCHER_MODEL": fixture.get("EC2_PI_DURABLE_MODEL"),
    "RESEARCHER_THINKING": fixture.get("EC2_PI_DURABLE_THINKING"),
    "OPENVIKING_URL": "http://openviking:1933",
    "OPENVIKING_API_KEY": fixture.get("EC2_PI_DURABLE_OPENVIKING_API_KEY"),
    "RESEARCHER_WEB_SEARCH": fixture.get("EC2_PI_DURABLE_WEB_SEARCH"),
    "WEB_SEARCH_MCP_URL": fixture.get("EC2_PI_DURABLE_WEB_SEARCH_MCP_URL"),
    "WEB_SEARCH_MCP_TOKEN": fixture.get("EC2_PI_DURABLE_WEB_SEARCH_MCP_TOKEN"),
    "OPENKNOWLEDGE_MCP_URL": "http://ok.%s/mcp" % fixture.get("EC2_DOMAIN"),
    # The runtime of the image reads the time zone, as in each other service.
    "TZ": fixture.get("EC2_TZ"),
}
if set(agent) != set(expected_agent):
    problems.append("the environment names of pi-durable do not match the release contract")
for name, value in expected_agent.items():
    if agent.get(name) != value:
        problems.append("pi-durable does not give %s its release value" % name)
required_agent = ("RESEARCHER_API_KEY LITELLM_API_KEY RESEARCHER_MODEL "
                  "LITELLM_BASE_URL OPENVIKING_URL OPENKNOWLEDGE_MCP_URL").split()
for name in required_agent:
    if not agent.get(name):
        problems.append("pi-durable has no value for the required name %s" % name)

# The images: each service has an image of kits.lock. A kit service has the image name of
# its kit. A service of a support image has the tag of the lock, and the digest when the
# lock gives one.
def lock_blocks(path):
    """The blocks server_kits and support_images of kits.lock: block -> entry -> fields."""
    found, block, entry = {}, "", None
    for raw in open(path, encoding="utf-8"):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            block, entry = text.rstrip(":"), None
        elif indent == 2 and text.endswith(":"):
            entry = found.setdefault(block, {}).setdefault(text[:-1], {})
        elif indent == 4 and entry is not None:
            key, _, value = text.partition(":")
            value = value.strip()
            if value[:1] in ("\"", "\x27"):
                value = value[1:].split(value[0], 1)[0]
            else:
                value = re.sub(r"\s+#.*", "", value)
            entry[key] = value
    return found

try:
    lock = lock_blocks(top + "kits.lock")
except OSError:
    lock = {}
    problems.append("kits.lock is not in the tree: the images of the file set have no pin")
pinned = {}
for name, item in sorted((lock.get("support_images") or {}).items()):
    image, digest = item.get("image", ""), item.get("image_digest", "")
    if "@" in image:
        if image.split("@", 1)[1] != digest:
            problems.append("kits.lock: image and image_digest of the support image %s do not name one digest" % name)
    elif digest:
        image = image + "@" + digest
    pinned[item.get("service", "")] = (image, "support_images." + name, " and the digest" if digest else "")
kit_images = set()
for name, item in sorted((lock.get("server_kits") or {}).items()):
    kit_images.add(item.get("image", ""))
    pinned[item.get("service", "")] = (item.get("image", ""), "server_kits." + name, "")
for name, service in sorted(services.items()):
    image = service.get("image") or ""
    if name in pinned:
        if image != pinned[name][0]:
            problems.append("the image of the service %s is not the image of kits.lock (%s): the tag%s"
                            % (name, pinned[name][1], pinned[name][2]))
    elif image not in kit_images:
        problems.append("the image of the service %s is not an image of kits.lock" % name)

for text in problems:
    print("check: " + text)
sys.exit(1 if problems else 0)
'

# compose_set_check <options> <Compose file>...: the one line of the Compose
# check of one file set.
compose_set_check() {
    set_options=$1
    shift
    label="compose $(echo "$*" | tr ' ' ':')"
    : >"$work/real"
    for f in "$@"; do
        if [ ! -f "$f" ]; then
            echo "check: the Compose file $f of the file set is not in the tree" >&2
            result "$label" FAIL
            return 0
        fi
        env_file_names "$f" >"$work/names" || die "awk cannot read $f"
        grep -vxF -e .env.example -e ./.env.example "$work/names" >>"$work/real" || :
    done
    # Before Compose runs: Compose reads each file of `env_file`, and an error
    # of that read can print a line of the file.
    if [ -s "$work/real" ]; then
        echo "check: a file of the set names a file in env_file that is not .env.example; Compose does not run on it" >&2
        result "$label" FAIL "env_file: $(tr '\n' ' ' <"$work/real" | sed 's/ $//')"
    elif ! docker_ok || ! docker compose version >/dev/null 2>&1; then
        no_tool "$label" "docker compose"
    elif ! command -v python3 >/dev/null 2>&1; then
        no_tool "$label" python3
    elif [ ! -f .env.example ]; then
        echo "check: the file set needs .env.example for the stub environment" >&2
        result "$label" FAIL
    else
        stub_env "$set_options" "$work/stub.env" || die "awk cannot read .env.example"
        stub_env "" "$work/core.env" || die "awk cannot read .env.example"
        if ! compose_model "$work/stub.env" "$@" >"$work/model.json" 2>"$work/out" </dev/null; then
            cat "$work/out" >&2
            result "$label" FAIL
        elif grep -F 'variable is not set' "$work/out" >"$work/unset"; then
            # Compose exits with 0 here. The lines hold names, not values.
            cat "$work/unset" >&2
            result "$label" FAIL "a variable is not set: .env.example lacks the name"
        elif ! python3 -I -c "$compose_model_py" "$work/model.json" "$set_options" "$top" "$work/stub.env" >"$work/out" 2>&1; then
            cat "$work/out" >&2
            result "$label" FAIL
        elif [ -n "$set_options" ] && compose_model "$work/core.env" "$@" >/dev/null 2>&1 </dev/null; then
            # The names of an option have their test in the file of the option.
            echo "check: the file set renders with no value for the names of its options" >&2
            result "$label" FAIL
        else
            sed 's/^EC2_PI_DURABLE_WEB_SEARCH=.*$/EC2_PI_DURABLE_WEB_SEARCH=mcp/;
                s|^EC2_PI_DURABLE_WEB_SEARCH_MCP_URL=.*$|EC2_PI_DURABLE_WEB_SEARCH_MCP_URL=https://search.check-fixture.test/mcp|;
                s/^EC2_PI_DURABLE_WEB_SEARCH_MCP_TOKEN=.*$/EC2_PI_DURABLE_WEB_SEARCH_MCP_TOKEN=fixture-web/' \
                "$work/stub.env" >"$work/search.env"
            if ! compose_model "$work/search.env" "$@" >"$work/model.json" 2>"$work/out" </dev/null; then
                cat "$work/out" >&2
                result "$label" FAIL
            elif ! python3 -I -c "$compose_model_py" "$work/model.json" "$set_options" "$top" "$work/search.env" >"$work/out" 2>&1; then
                cat "$work/out" >&2
                result "$label" FAIL
            else
                result "$label" ok
            fi
        fi
    fi
}

# routes_check: the one line of the check of the gateway routes. It reads the
# text of the route files; a comment line is not a part of the test.
routes_check() {
    label="gateway routes litellm/"
    : >"$work/problems"
    for f in litellm/config.yaml litellm/config.copilot.yaml litellm/copilot.yaml; do
        [ -f "$f" ] || echo "check: the route file $f is not in the tree" >>"$work/problems"
    done
    # No file sets drop_params.
    grep -n 'drop_params' litellm/*.yaml openviking/* compose*.yaml 2>/dev/null |
        grep -Ev '^[^:]*:[0-9]+:[ \t]*#' |
        sed 's/^\([^:]*:[0-9]*\):.*/check: \1 sets drop_params/' >>"$work/problems" || :
    # Each route of GPT-6 Luna sets the effort xhigh, and no route sets another effort.
    for f in litellm/*.yaml; do
        [ -f "$f" ] || continue
        awk -v file="$f" '
        function end_route() {
            if (route == "") return
            if (luna && effort != "xhigh") print "check: " file ": the route " route " of GPT-6 Luna does not set reasoning_effort: xhigh"
            route = ""
        }
        /^[ \t]*#/ { next }
        /^[^ \t-]/ { end_route() }
        $1 == "-" && $2 == "model_name:" { end_route(); route = $3; luna = 0; effort = "" }
        /luna/ { luna = 1 }
        $1 == "reasoning_effort:" {
            effort = $2
            if ($2 != "xhigh") print "check: " file ": line " NR " sets a reasoning effort that is not xhigh"
        }
        END { end_route() }' "$f" >>"$work/problems"
    done
    # The route file of the core has no route to the service copilot.
    if [ -f litellm/config.yaml ] && grep -v '^[ \t]*#' litellm/config.yaml | grep -q 'copilot'; then
        echo "check: litellm/config.yaml names the service copilot: the core has no route to it" >>"$work/problems"
    fi
    if [ -s "$work/problems" ]; then
        cat "$work/problems" >&2
        result "$label" FAIL
    else
        result "$label" ok
    fi
}

# blueprint_render <id> <EC2_EDGE_NAMES>: render the Pangolin files of the tree
# with a list of public names into $work/blueprint/<id>. The settings are no
# secret.
blueprint_render() {
    mkdir -p "$work/blueprint"
    cat >"$work/blueprint/$1.env" <<EOT
EC2_DOMAIN=ec2.check-fixture.test
EC2_ACME_EMAIL=owner@check-fixture.test
EC2_ADMIN_CIDR=198.51.100.7/32
EC2_PANGOLIN_SITE=ec2-local
EC2_EDGE_NAMES=$2
EOT
    env -i PATH="$PATH" sh scripts/render-pangolin.sh --env-file "$work/blueprint/$1.env" \
        --out "$work/blueprint/$1" >/dev/null 2>&1
}

# blueprint_facts <id>: print one line for each resource of a rendered
# blueprint: its name, `enabled`, `sso-enabled`, its target, and the number of
# its allow rules and of its deny rules. The text is read by its indentation.
blueprint_facts() {
    awk '
    function flush() {
        if (name != "") print name, "enabled=" enabled, "sso=" sso, "target=" host ":" port, "allow=" allow, "deny=" deny
    }
    /^[ \t]*#/ { next }
    /^[^ ]/ { flush(); name = ""; inside = ($1 == "public-resources:") }
    inside && /^  [^ ]/ { flush(); name = $1; sub(/:$/, "", name); enabled = "true"; sso = ""; host = ""; port = ""; allow = 0; deny = 0 }
    name != "" && $1 == "enabled:" { enabled = $2 }
    name != "" && $1 == "sso-enabled:" { sso = $2 }
    name != "" && $1 == "hostname:" { host = $2 }
    name != "" && $1 == "port:" { port = $2 }
    name != "" && $2 == "action:" && $3 == "allow" { allow++ }
    name != "" && $2 == "action:" && $3 == "deny" { deny++ }
    END { flush() }' "$work/blueprint/$1/blueprint.yaml" | LC_ALL=C sort
}

# blueprint_check: the one line of the check of the Pangolin blueprint. With
# the option pangolin, EC2_EDGE_NAMES says which resources are on.
blueprint_check() {
    label="pangolin blueprint pangolin/"
    if ! command -v envsubst >/dev/null 2>&1; then
        no_tool "$label" envsubst
        return 0
    fi
    : >"$work/problems"
    # The default list: only `ok` is on. It has no login of Pangolin, and its
    # one target is the gate. Each path rule of the other resources stays.
    if blueprint_render default ok; then
        blueprint_facts default >"$work/blueprint/default.facts"
        cat >"$work/blueprint/default.want" <<'EOT'
agent enabled=false sso=true target=pi-durable:5470 allow=0 deny=2
llm enabled=false sso=true target=litellm:4000 allow=5 deny=0
ok enabled=true sso=false target=ok-auth:4180 allow=0 deny=0
ov enabled=false sso=true target=openviking:1933 allow=0 deny=0
EOT
        cmp -s "$work/blueprint/default.facts" "$work/blueprint/default.want" ||
            echo "check: with EC2_EDGE_NAMES=ok, the resources of the rendered blueprint are not: ok on with no login of Pangolin and the target ok-auth:4180, each other resource off" >>"$work/problems"
    else
        echo "check: scripts/render-pangolin.sh fails with EC2_EDGE_NAMES=ok" >>"$work/problems"
    fi
    if blueprint_render each ok,agent,llm,ov; then
        blueprint_facts each >"$work/blueprint/each.facts"
        sed 's/enabled=false/enabled=true/' "$work/blueprint/default.want" >"$work/blueprint/each.want"
        cmp -s "$work/blueprint/each.facts" "$work/blueprint/each.want" ||
            echo "check: with each public name in EC2_EDGE_NAMES, the rendered blueprint does not turn each resource on" >>"$work/problems"
    else
        echo "check: scripts/render-pangolin.sh fails with each public name in EC2_EDGE_NAMES" >>"$work/problems"
    fi
    ! blueprint_render unknown ok,web ||
        echo "check: scripts/render-pangolin.sh accepts a public name that it does not know" >>"$work/problems"
    if [ -s "$work/problems" ]; then
        cat "$work/problems" >&2
        result "$label" FAIL
    else
        result "$label" ok
    fi
}

# lock_image <name>: the image of a support image of kits.lock with its digest,
# in the form <image>@<digest>. Nothing when the lock does not give the two.
lock_image() {
    awk -v name="$1:" '
        /^[^ #]/ { section = $1; block = "" }
        section != "support_images:" { next }
        /^  [^ #]/ { block = $1 }
        block != name { next }
        $1 == "image:" { image = $2 }
        $1 == "image_digest:" { digest = $2; gsub(/"/, "", digest) }
        END { if (image != "" && digest ~ /^sha256:[0-9a-f]+$/) print image "@" digest }
    ' kits.lock
}

# The directory of the check of the core edge: the stub and the test.
edge=$work/edge

# caddy_check: the one line of the check of the core edge. The test runs in one
# container of the Caddy image, on no network, with the user and the limits of
# the service `caddy` (compose.caddy.yaml). It reads no value of the machine.
caddy_check() {
    label="core edge caddy/"
    image=$(lock_image caddy)
    if [ -z "$image" ]; then
        echo "check: kits.lock gives the support image caddy no image with a digest" >&2
        result "$label" FAIL
        return 0
    fi
    if ! docker_ok; then
        no_tool "$label" docker
        return 0
    fi
    if ! docker image inspect "$image" >/dev/null 2>&1 &&
        { [ "$offline" -eq 1 ] || ! docker pull -q "$image" >/dev/null 2>&1; }; then
        no_tool "$label" "the image $image"
        return 0
    fi
    # The user of the container reads the two files, so the directory is open.
    mkdir -p "$edge"
    chmod 755 "$edge"
    # The stub: one listener for each service of the stack, on the port of the
    # service. It answers with the name of the service and the request target
    # that it gets.
    cat >"$edge/stub.caddy" <<'EOT'
{
	admin off
	persist_config off
	auto_https off
}
http://:4180 {
	bind 127.0.0.1
	respond "ok-auth {http.request.orig_uri}"
}
http://:4000 {
	bind 127.0.0.1
	respond "litellm {http.request.orig_uri}"
}
http://:1933 {
	bind 127.0.0.1
	respond "openviking {http.request.orig_uri}"
}
http://:5470 {
	bind 127.0.0.1
	respond "pi-durable {http.request.orig_uri}"
}
EOT
    cat >"$edge/test.sh" <<'EOT'
#!/bin/sh
# The route test of the core edge. It runs in the Caddy image. Each line that it
# prints is a problem.
set -u
D=ec2.check-fixture.test
INSIDE=127.0.0.1/32
OUTSIDE=198.51.100.7/32
edge_pid=

bad() {
    echo "check: core edge: $*"
}

# start_edge <case> <names> <range> <address>: start Caddy with the configuration
# that caddy/start.sh writes. The one difference from the host: a local authority
# makes the certificates, because the test has no network.
start_edge() {
    case_id=$1
    if ! EC2_DOMAIN=$D EC2_EDGE_NAMES=$2 EC2_ADMIN_CIDR=$3 EC2_ACME_EMAIL=$4 \
        sh /etc/caddy/start.sh --print >"/tmp/$case_id.host" 2>/dev/null; then
        bad "$case_id: caddy/start.sh refuses the settings of the test"
        return 1
    fi
    if ! EC2_DOMAIN=$D EC2_ADMIN_CIDR=$3 caddy validate --config "/tmp/$case_id.host" \
        --adapter caddyfile >/dev/null 2>&1; then
        bad "$case_id: caddy validate refuses the configuration"
        return 1
    fi
    sed '1a\	local_certs\n	skip_install_trust' "/tmp/$case_id.host" >"/tmp/$case_id.test"
    EC2_DOMAIN=$D EC2_ADMIN_CIDR=$3 XDG_DATA_HOME=/tmp/$case_id.data \
        caddy run --config "/tmp/$case_id.test" --adapter caddyfile >"/tmp/$case_id.log" 2>&1 &
    edge_pid=$!
    SSL_CERT_FILE=/tmp/$case_id.data/caddy/pki/authorities/local/root.crt
    export SSL_CERT_FILE
    # Wait for the health check, then for one certificate for each name.
    want=$(printf '%s\n' "$2" | tr ',' '\n' | grep -c .)
    n=0
    until wget -q -T 2 -O /dev/null http://127.0.0.1:8082/ 2>/dev/null &&
        [ "$(grep -c 'certificate obtained successfully' "/tmp/$case_id.log")" -ge "$want" ]; do
        n=$((n + 1))
        if [ "$n" -gt 100 ]; then
            bad "$case_id: Caddy does not start"
            stop_edge
            return 1
        fi
        sleep 0.1
    done
}

stop_edge() {
    [ -z "$edge_pid" ] || { kill "$edge_pid" 2>/dev/null; wait "$edge_pid" 2>/dev/null; }
    edge_pid=
}

# expect <URL> <status of each answer, with commas> [<body>]: one request. The
# status is `none` when no answer comes, for example with no certificate for
# the name.
expect() {
    : >/tmp/body
    out=$(wget -T 5 -q -S -O /tmp/body "$1" 2>&1)
    got=$(printf '%s\n' "$out" | sed -n 's/^ *HTTP\/[0-9.]* \([0-9][0-9]*\).*/\1/p' | tr '\n' ',' | sed 's/,$//')
    [ -n "$got" ] || got=none
    body=$(head -c 200 /tmp/body)
    [ "$got" = "$2" ] || bad "$case_id: $1 gives the status $got, not $2"
    [ $# -lt 3 ] || [ "$body" = "$3" ] || bad "$case_id: $1 does not reach its target with the correct path"
    [ $# -ge 3 ] || [ -z "$body" ] || bad "$case_id: $1 reaches a service"
}

# refuse <text> <NAME=value>...: caddy/start.sh must stop with these settings.
refuse() {
    text=$1
    shift
    if env EC2_DOMAIN=$D EC2_EDGE_NAMES=ok EC2_ADMIN_CIDR=$INSIDE EC2_ACME_EMAIL= "$@" \
        sh /etc/caddy/start.sh --print >/dev/null 2>&1; then
        bad "caddy/start.sh accepts $text"
    fi
}

caddy run --config /check/stub.caddy --adapter caddyfile >/tmp/stub.log 2>&1 &

# Case 1: each name, and the test is in the range of the administrator.
if start_edge all ok,agent,llm,ov "$INSIDE" owner@check-fixture.test; then
    # `ok` goes to the gate with each path.
    expect "https://ok.$D/" 200 "ok-auth /"
    expect "https://ok.$D/oauth2/callback?code=1" 200 "ok-auth /oauth2/callback?code=1"
    # Port 80 sends a name of the list to HTTPS.
    expect "http://ok.$D/x" 308,200 "ok-auth /x"
    # `llm` serves the five model paths only. The gateway gets the path of the rule.
    for path in /v1/chat/completions /v1/responses /v1/embeddings /v1/models /v1/messages; do
        expect "https://llm.$D$path" 200 "litellm $path"
    done
    expect "https://llm.$D/v1/models?limit=1" 200 "litellm /v1/models?limit=1"
    expect "https://llm.$D/V1/Models" 200 "litellm /v1/models"
    expect "https://llm.$D//v1/models" 200 "litellm /v1/models"
    expect "https://llm.$D/ui/../v1/models" 200 "litellm /v1/models"
    for path in / /ui /ui/ /key/generate /user/new /model/info /sso/key/generate /health /v1 \
        /v1/models/x /v1/models/ /v1/models/../../ui /v1/chat /v2/models; do
        expect "https://llm.$D$path" 404
    done
    # `ov` goes to OpenViking with each path.
    expect "https://ov.$D/api/v1/search?uri=viking%3A%2F%2Fa" 200 "openviking /api/v1/search?uri=viking%3A%2F%2Fa"
    # `agent` refuses `/` and `/healthz`, also for a text that the agent reads as one of the two.
    expect "https://agent.$D/v1/jobs" 200 "pi-durable /v1/jobs"
    expect "https://agent.$D/v1/jobs/a%20b/files/c.txt?q=//x/../y" 200 "pi-durable /v1/jobs/a%20b/files/c.txt?q=//x/../y"
    for path in / /healthz '/healthz?x=1' /HEALTHZ '/?x=1'; do
        expect "https://agent.$D$path" 403
    done
    for path in //x/healthz //x/ /x/../healthz /./healthz /v1/../healthz /v1/.. /v1/..%2fhealthz \
        /v1/%2e%2e/healthz /v1/%2E./healthz '/x\..\healthz' '/\x/healthz' /v1/..%5chealthz '/v1/..?x=1'; do
        expect "https://agent.$D$path" 400
    done
    # A name outside the list: no certificate, and the answer 404 on port 80.
    expect "https://other.$D/" none
    expect "http://other.$D/v1/models" 404
    expect "http://127.0.0.1/" 404
    stop_edge
fi

# Case 2: each name, and the test is outside the range of the administrator.
if start_edge outside ok,agent,llm,ov "$OUTSIDE" owner@check-fixture.test; then
    expect "https://ok.$D/" 200 "ok-auth /"
    for url in "https://llm.$D/v1/models" "https://llm.$D/" "https://ov.$D/api/v1/search" \
        "https://agent.$D/v1/jobs" "https://agent.$D/healthz" "https://agent.$D//x/healthz"; do
        expect "$url" 403
    done
    stop_edge
fi

# Case 3: the default list, with no address for the certificate account.
if start_edge default ok "$INSIDE" ""; then
    [ "$(grep -c '/sites/' /tmp/default.host)" -eq 1 ] ||
        bad "default: the configuration imports a site that the list does not name"
    expect "https://ok.$D/" 200 "ok-auth /"
    for name in llm ov agent; do
        expect "https://$name.$D/v1/models" none
        expect "http://$name.$D/v1/models" 404
    done
    stop_edge
fi

# caddy/start.sh stops on a value that does not have the form of its kind.
refuse "a name of the list that it does not know" EC2_EDGE_NAMES=ok,web
refuse "a blank in the list" "EC2_EDGE_NAMES=ok, llm"
refuse "an empty list" EC2_EDGE_NAMES=
refuse "a name two times" EC2_EDGE_NAMES=ok,ok
refuse "a list that ends with a comma" EC2_EDGE_NAMES=ok,
refuse "the open range" EC2_ADMIN_CIDR=0.0.0.0/0
refuse "no range" EC2_ADMIN_CIDR=
refuse "two ranges" "EC2_ADMIN_CIDR=198.51.100.7/32 0.0.0.0/0"
refuse "no domain" EC2_DOMAIN=
refuse "a domain with a blank" "EC2_DOMAIN=a.test b.test"
refuse "a domain with a brace" "EC2_DOMAIN=a.test {"
refuse "an address with a blank" "EC2_ACME_EMAIL=a@b.test c"
EOT
    set -- --rm --init --network none --user 1000:1000 --cap-drop ALL --cap-add NET_BIND_SERVICE \
        --security-opt no-new-privileges:true \
        --mount "type=bind,\"source=$(printf '%s' "$top/caddy" | sed 's/"/""/g')\",target=/etc/caddy,readonly" \
        --mount "type=bind,\"source=$(printf '%s' "$edge" | sed 's/"/""/g')\",target=/check,readonly"
    for host in ok-auth litellm openviking pi-durable ok.ec2.check-fixture.test llm.ec2.check-fixture.test \
        ov.ec2.check-fixture.test agent.ec2.check-fixture.test other.ec2.check-fixture.test; do
        set -- "$@" --add-host "$host:127.0.0.1"
    done
    if ! docker run "$@" "$image" sh /check/test.sh >"$work/out" 2>&1 </dev/null; then
        cat "$work/out" >&2
        echo "check: the test of the core edge does not run" >&2
        result "$label" FAIL
    elif [ -s "$work/out" ]; then
        cat "$work/out" >&2
        result "$label" FAIL
    else
        result "$label" ok
    fi
}

# The directory of the checks of the deploy script: the stubs, each environment
# file and each output. The scripts and the sample environment file under test
# are the files of the tree.
dry=$work/dry
# The directory of the fake zone of a stub: its state and its logs.
zone=$dry/zone

# block_names <kind> <name> [empty]: print each name of one block of
# .env.example. The kind is Option or DNS provider. With `empty`, print only a
# name with no value.
block_names() {
    awk -v mark="# ----- $1 $2 -----" -v empty="${3:-}" '
    /^# (=====|-----) / { on = ($0 == mark); next }
    on && /^[A-Za-z_][A-Za-z0-9_]*=/ {
        if (empty != "" && $0 !~ /=[ \t]*$/) next
        sub(/=.*$/, "")
        print
    }' .env.example
}

# deploy_env <file> <options> <provider> <edge names>: write an environment
# file from .env.example, with the three option settings of the arguments. An
# empty name gets a value by its place:
#   Part 1                       a fake value of its own
#   the block of an active option  a fake value of its own
#   the block of an option that is off  no value: `secret` must not need it
#   a DNS provider block         a fake value of its own: the development
#                                machine can hold the credential of each provider
#   Part 2                       a fake value of its own for a name that ends
#                                with a secret word; the text check-fixture for
#                                each other name: a dry run prints a setting
# Some names need a form. $dry/fakes lists each fake value, and $dry/dns-fakes
# lists the fake values of the DNS provider blocks.
deploy_env() {
    CHECK_DRY_KEY=$dry/key.pub CHECK_DRY_FAKES=$dry/fakes CHECK_DRY_DNS=$dry/dns-fakes awk \
        -v options="$2" -v provider="$3" -v edge="$4" '
    function fake() {
        value = sprintf("check-fixture-%03d-q7Zk9w", n)
        print value >>ENVIRON["CHECK_DRY_FAKES"]
    }
    /^# ===== Part [0-9]/ { part = $4; sub(/:.*$/, "", part); kind = block = "" }
    /^# ----- Option [a-z0-9]+ -----/ { kind = "option"; block = $4 }
    /^# ----- DNS provider [a-z0-9]+ -----/ { kind = "dns"; block = $5 }
    /^[A-Za-z_][A-Za-z0-9_]*=/ {
        i = index($0, "=")
        name = substr($0, 1, i - 1)
        value = substr($0, i + 1)
        seen[name] = 1
        if (name == "EC2_OPTIONS") {
            value = options
        } else if (name == "EC2_DNS_PROVIDER") {
            value = provider
        } else if (name == "EC2_EDGE_NAMES") {
            value = edge
        } else if (value ~ /^[ \t]*$/) {
            n++
            if (name == "EC2_DOMAIN") {
                value = "ec2.check-fixture.test"
            } else if (name == "EC2_ADMIN_CIDR") {
                value = "198.51.100.7/32"
            } else if (name == "EC2_ACME_EMAIL") {
                value = "owner@check-fixture.test"
            } else if (name == "EC2_SSH_PUBLIC_KEY_FILE") {
                value = ENVIRON["CHECK_DRY_KEY"]
            } else if (name == "EC2_SNAPSHOT_REPOSITORY") {
                value = "https://git.check-fixture.test/owner/snapshot.git"
            } else if (name == "EC2_ROUTE53_PROFILE") {
                value = "check-fixture"
            } else if (name == "EC2_ROUTE53_HOSTED_ZONE_ID") {
                value = "Z0CHECKFIXTURE"
            } else if (name == "EC2_CLOUDFLARE_ZONE_ID") {
                value = sprintf("%032d", 0)
            } else if (kind == "dns") {
                fake()
                print value >>ENVIRON["CHECK_DRY_DNS"]
            } else if (kind == "option") {
                if (index("," options ",", "," block ",")) fake()
                else value = ""
            } else if (part == 1 || name ~ /_(TOKEN|SECRET|PASSWORD|KEY)$/) {
                fake()
            } else {
                value = "check-fixture"
            }
        }
        print name "=" value
    }
    END {
        if (!("EC2_OPTIONS" in seen)) print "EC2_OPTIONS=" options
        if (!("EC2_DNS_PROVIDER" in seen)) print "EC2_DNS_PROVIDER=" provider
        if (!("EC2_EDGE_NAMES" in seen)) print "EC2_EDGE_NAMES=" edge
    }' .env.example >"$1" || die "awk cannot read .env.example"
}

# deploy_case <id> <script> <stub directory> <stdin file> <argument>...: one
# run of a script of the deploy part. Its output goes to $dry/<id>.out and
# $dry/<id>.err, and `case_rc` gets its exit status. The environment of the
# shell does not reach the script: the script takes a name that the environment
# file lacks from the shell, and a dry run prints it. The stubs of the
# directory are first on PATH.
deploy_case() {
    id=$1
    script=$2
    stubs=$3
    input=$4
    shift 4
    case_rc=0
    env -i PATH="$stubs:$PATH" HOME="$dry/home" TMPDIR="$dry/tmp" CHECK_DRY_CALLS="$dry/calls" \
        CHECK_STUB_PY="$dry/stub.py" CHECK_STUB_DIR="$zone" \
        sh "$script" "$@" >"$dry/$id.out" 2>"$dry/$id.err" <"$input" || case_rc=$?
}

# dry_case <id> <stdin file> <argument>...: one dry run of the deploy script. A
# stub of `aws` and of `curl` that refuses each call is first on PATH. The dry
# run must pass.
dry_case() {
    id=$1
    input=$2
    shift 2
    deploy_case "$id" scripts/aws-host.sh "$dry/bin" "$input" --dry-run "$@"
    [ "$case_rc" -eq 0 ] || {
        echo "check: the dry run $id exits with $case_rc"
        # A message of the script names a line number or a name, never a value.
        sed -n 's/^aws-host: /  /p; s/^dns-records: /  /p' "$dry/$id.err"
    } >>"$dry/problems"
}

# stop_case <id> <stdin file> <text> <argument>...: one dry run that must stop
# with an error. Its message must hold the text.
stop_case() {
    id=$1
    input=$2
    text=$3
    shift 3
    deploy_case "$id" scripts/aws-host.sh "$dry/bin" "$input" --dry-run "$@"
    if [ "$case_rc" -eq 0 ]; then
        echo "check: the dry run $id does not stop" >>"$dry/problems"
    elif ! grep -qF -e "$text" "$dry/$id.err"; then
        echo "check: the dry run $id stops with no message that holds: $text" >>"$dry/problems"
    fi
}

# dry_expect <id> <text>...: the output of the run holds each text.
dry_expect() {
    id=$1
    shift
    for text in "$@"; do
        grep -qF -e "$text" "$dry/$id.out" ||
            echo "check: the run $id prints no line with: $text" >>"$dry/problems"
    done
}

# dry_refuse <id> <text>...: the output of the run holds none of the texts.
dry_refuse() {
    id=$1
    shift
    for text in "$@"; do
        ! grep -qF -e "$text" "$dry/$id.out" ||
            echo "check: the run $id prints a line with: $text" >>"$dry/problems"
    done
}

# deploy_setup: make the directory of the checks of the deploy script.
deploy_setup() {
    mkdir -p "$dry/bin" "$dry/serve" "$dry/home" "$dry/tmp"
    : >"$dry/calls"
    : >"$dry/problems"
    : >"$dry/fakes"
    : >"$dry/dns-fakes"
    for tool in aws curl; do
        # The stub of a dry run records its name and refuses the call. A dry run
        # must not start it.
        # shellcheck disable=SC2016 # the stub expands the two names
        printf '#!/bin/sh\necho "${0##*/}" >>"$CHECK_DRY_CALLS"\nexit 99\n' >"$dry/bin/$tool"
        # The stub of a provider test answers a call from a fake zone.
        # shellcheck disable=SC2016 # the stub expands the names
        printf '#!/bin/sh\nexec python3 -I "$CHECK_STUB_PY" %s "$@"\n' "$tool" >"$dry/serve/$tool"
        chmod 755 "$dry/bin/$tool" "$dry/serve/$tool"
    done
    echo 'ssh-ed25519 fixture-key check' >"$dry/key.pub"
    # The parsers find a part and a block of .env.example by its marker line.
    for mark in '# ===== Part 1' '# ===== Part 2' '# ===== Part 3' '# ----- Option pangolin -----' \
        '# ----- Option copilot -----' '# ----- DNS provider porkbun -----' \
        '# ----- DNS provider cloudflare -----' '# ----- DNS provider route53 -----'; do
        grep -q -e "^$mark" .env.example ||
            echo "check: .env.example has no marker line that starts with: $mark" >>"$dry/problems"
    done
    stub_text >"$dry/stub.py"
}

# deploy_end <check>: the tests of each run of a check, then its one line.
deploy_end() {
    [ ! -s "$dry/calls" ] ||
        echo "check: a dry run calls: $(LC_ALL=C sort -u "$dry/calls" | tr '\n' ' ')" >>"$dry/problems"
    [ -z "$(ls -A "$dry/tmp")" ] ||
        echo "check: a run leaves a file in its temporary directory" >>"$dry/problems"
    # No output and no log of a stub holds a fake value. The state file of the
    # fake zone and the copy of the secret hold them: they are not an output.
    if [ -s "$dry/fakes" ] && find "$dry" \( -name '*.out' -o -name '*.err' -o -name calls -o -name ops \
        -o -name problems \) -type f -exec cat {} + | grep -qF -f "$dry/fakes"; then
        echo "check: a run prints a fake value of an environment file" >>"$dry/problems"
    fi
    if [ -s "$dry/problems" ]; then
        cat "$dry/problems" >&2
        result "$1" FAIL
    else
        result "$1" ok
    fi
    : >"$dry/problems"
    : >"$dry/calls"
}

# python_text <script> <name>...: the code of a Python function or the line of
# a Python name in the text of a script. A function ends at an empty line.
python_text() {
    f=$1
    shift
    for name in "$@"; do
        awk -v name="$name" '
        index($0, "def " name "(") == 1 { on = 1 }
        index($0, name " = ") == 1 { print; exit }
        on && /^$/ { exit }
        on { print }' "$f"
    done
}

# set_files <EC2_OPTIONS>: print the Compose files of the file set of the
# contract for this value, with a blank between two files (FILE_SETS).
set_files() {
    printf '%s\n' "$FILE_SETS" | awk -F '|' -v options="$1" '$1 == options { print $2 }'
}

# compose_services <Compose file>...: print each service that the text of the
# files names below `services:`, one name in each line, in sorted order. An
# overlay names a service of compose.yaml again, so the list is the list of the
# services of the file set.
compose_services() {
    for f in "$@"; do
        [ -f "$f" ] || continue
        awk '
        /^services:/ { on = 1; next }
        /^[A-Za-z]/ { on = 0 }
        on && /^  [A-Za-z0-9_-]+:[ \t\r]*$/ { sub(/:.*$/, ""); sub(/^  /, ""); print }' "$f"
    done | LC_ALL=C sort -u
}

# dry_run_check: the dry runs of the deploy script, for the core and for each
# option set. The check prints no output of a run: a line can hold a value.
dry_run_check() {
    label="dry run scripts/aws-host.sh"
    # The sample environment file of the tree itself, with the scripts of the
    # tree. With .env.example, `secret` gets --allow-empty for each empty name of
    # Part 1.
    awk '
    /^# ===== Part [0-9]/ { part = $4; sub(/:.*$/, "", part); block = 0 }
    /^# ----- / { block = 1 }
    part == 1 && !block && /^[A-Za-z_][A-Za-z0-9_]*=[ \t]*$/ { sub(/=.*$/, ""); print }' \
        .env.example >"$dry/empty" || die "awk cannot read .env.example"
    [ -s "$dry/empty" ] ||
        echo "check: .env.example has no empty name in Part 1" >>"$dry/problems"
    set --
    while IFS= read -r name <&3; do
        set -- "$@" --allow-empty "$name"
    done 3<"$dry/empty"
    for command in deploy secret dns; do
        case $command in
            secret) deploy_case "sample-$command" scripts/aws-host.sh "$dry/bin" .env.example --dry-run secret "$@" ;;
            *) deploy_case "sample-$command" scripts/aws-host.sh "$dry/bin" /dev/null --dry-run --env-file .env.example "$command" ;;
        esac
        [ "$case_rc" -eq 0 ] || {
            echo "check: the dry run sample-$command exits with $case_rc"
            sed -n 's/^aws-host: /  /p; s/^dns-records: /  /p' "$dry/sample-$command.err"
        } >>"$dry/problems"
    done
    dry_expect sample-deploy 'cloudformation deploy' '--no-execute-changeset' \
        'cloudformation execute-change-set' 'EnablePangolin=' 'BedrockEmbedModel='
    dry_expect sample-secret 'secretsmanager create-secret' 'secretsmanager put-secret-value'
    # --allow-empty accepts each empty name of Part 1. The run then stops only on
    # a setting with no value in the sample that a Compose file of the core
    # refuses with no value: the bootstrap stops on the same names.
    : >"$dry/expected"
    sed -n '/^# ===== Part 2/,/^# ===== Part 3/s/^\([A-Za-z_][A-Za-z0-9_]*\)=[ \t]*$/\1/p' .env.example >"$dry/settings"
    while IFS= read -r name <&3; do
        ! grep -v '^[ \t]*#' compose.yaml compose.caddy.yaml 2>/dev/null | grep -qF -e "\${$name:?" ||
            printf '%s\n' "$name" >>"$dry/expected"
    done 3<"$dry/settings"
    sed -n 's/^dry run: a real run stops here: no value for \([A-Za-z0-9_ ]*\)$/\1/p' "$dry/sample-secret.out" |
        tr ' ' '\n' | sed '/^$/d' | LC_ALL=C sort >"$dry/found"
    LC_ALL=C sort "$dry/expected" | cmp -s - "$dry/found" ||
        echo "check: the dry run sample-secret with --allow-empty for each name of Part 1 does not stop on the empty settings of the sample that a Compose file of the core needs, and on no other name" >>"$dry/problems"
    # With no --allow-empty, `secret` needs a value for each name of Part 1.
    deploy_case sample-stop scripts/aws-host.sh "$dry/bin" .env.example --dry-run secret
    dry_expect sample-stop "a real run stops here: no value for $(tr '\n' ' ' <"$dry/empty" | sed 's/ $//')"
    dry_expect sample-dns 'cloudformation describe-stacks' 'dns: the provider is'

    # The option sets. Each set runs deploy, secret, dns and verify. The provider
    # of a set with the option pangolin is porkbun.
    embed=$(sed -n 's/^EC2_BEDROCK_EMBED_MODEL=//p' .env.example)
    [ -n "$embed" ] ||
        echo "check: .env.example gives no value for EC2_BEDROCK_EMBED_MODEL" >>"$dry/problems"
    for set_id in core copilot pangolin pangolin-copilot; do
        case $set_id in
            core) options= ;;
            pangolin-copilot) options=pangolin,copilot ;;
            *) options=$set_id ;;
        esac
        case $set_id in
            pangolin*) provider=porkbun; pangolin=true ;;
            *) provider=manual; pangolin=false ;;
        esac
        deploy_env "$dry/$set_id.env" "$options" "$provider" ok,llm
        dry_case "$set_id-deploy" /dev/null --env-file "$dry/$set_id.env" deploy
        dry_expect "$set_id-deploy" 'cloudformation deploy' '--no-execute-changeset' \
            'cloudformation execute-change-set' "EnablePangolin=$pangolin" \
            "BedrockEmbedModel=$embed"
        # No --allow-empty: a name of an option that is off needs no value.
        dry_case "$set_id-secret" "$dry/$set_id.env" secret
        dry_expect "$set_id-secret" 'secretsmanager create-secret' 'secretsmanager put-secret-value' \
            'not in the secret, a name of a DNS provider'
        dry_case "$set_id-dns" /dev/null --env-file "$dry/$set_id.env" dns
        dry_expect "$set_id-dns" 'cloudformation describe-stacks'
        dry_case "$set_id-plan" /dev/null --env-file "$dry/$set_id.env" dns --plan
        dry_expect "$set_id-plan" 'ok.ec2.check-fixture.test. 600 IN A' 'llm.ec2.check-fixture.test. 600 IN A'
        dry_refuse "$set_id-plan" 'ov.ec2.check-fixture.test' 'agent.ec2.check-fixture.test'
        dry_case "$set_id-verify" /dev/null --env-file "$dry/$set_id.env" verify
        # No error text of Compose reaches the output of the host: such a text can
        # hold a line of the environment file. No command of a kit clone and no
        # public address is in the commands.
        dry_expect "$set_id-verify" 'ssm send-command' 'service litellm: running' \
            'service ok-auth: running' 'service pi-durable: running' \
            'docker compose ps 2>/dev/null' 'docker compose ps --status running --services 2>/dev/null'
        dry_refuse "$set_id-verify" 'kits/' 'https://' 'scripts/verify.py' 'smoke.sh'
        for id in "$set_id-deploy" "$set_id-secret" "$set_id-dns" "$set_id-plan" "$set_id-verify"; do
            dry_refuse "$id" 'a real run stops here'
        done
        # The file set of `verify` is the file set of the contract for this value of
        # EC2_OPTIONS. The dry run check of the bootstrap compares the same list
        # with the value that the bootstrap writes into COMPOSE_FILE.
        files=$(set_files "$options")
        printf '%s\n' "$files" | tr ' ' ':' >"$dry/expected"
        sed -n 's/^dry run:   echo "== the services of the file set \(.*\)"$/\1/p' "$dry/$set_id-verify.out" >"$dry/found"
        [ -n "$files" ] && cmp -s "$dry/expected" "$dry/found" ||
            echo "check: the file set of verify with EC2_OPTIONS=$options is not the file set of the contract" >>"$dry/problems"
        # `verify` gives that file set and the public names of the environment file
        # to the verifier of the host, as its inputs. The verifier reads the services
        # from the Compose files of the set: it holds no list of its own.
        dry_expect "$set_id-verify" \
            "python3 -I scripts/verify-deployed.py --repo . --file-set $(cat "$dry/expected") --edge-names ok,llm || rc=1"
        # `verify` reads COMPOSE_FILE from the environment file of the host and
        # compares it with that file set.
        dry_expect "$set_id-verify" 'sed -n "s/^COMPOSE_FILE=//p" .env' \
            "if [ \"\$host_files\" = $(cat "$dry/expected") ]; then"
        # The services that `verify` tests are the services of the Compose files of
        # the file set, and no other service.
        # shellcheck disable=SC2086 # the words of the list
        compose_services $files >"$dry/expected"
        sed -n 's/^dry run:   if .* then echo "service \([A-Za-z0-9_-]*\): running".*$/\1/p' "$dry/$set_id-verify.out" |
            LC_ALL=C sort >"$dry/found"
        [ -s "$dry/expected" ] && cmp -s "$dry/expected" "$dry/found" ||
            echo "check: the services that verify tests with EC2_OPTIONS=$options are not the services of the Compose files of its file set" >>"$dry/problems"
        # The services and the records of the file set of the options, and no other.
        case $set_id in
            pangolin*)
                dry_expect "$set_id-verify" 'compose.yaml:compose.pangolin.yaml' 'service pangolin: running' \
                    'service gerbil: running' 'service traefik: running'
                dry_refuse "$set_id-verify" 'service caddy' compose.caddy.yaml
                dry_expect "$set_id-dns" dns/retrieveByNameType/ dns/create/ dns/editByNameType/ /A/pangolin.ec2
                dry_expect "$set_id-plan" 'pangolin.ec2.check-fixture.test. 600 IN A'
                ;;
            *)
                dry_expect "$set_id-verify" 'compose.yaml:compose.caddy.yaml' 'service caddy: running'
                dry_refuse "$set_id-verify" 'service pangolin' 'service gerbil' 'service traefik' \
                    compose.pangolin.yaml
                dry_expect "$set_id-dns" 'dns: the provider is manual'
                dry_refuse "$set_id-dns" 'dry run: curl' pangolin.ec2
                dry_refuse "$set_id-plan" pangolin.ec2
                ;;
        esac
        case $set_id in
            *copilot)
                dry_expect "$set_id-verify" compose.copilot.yaml 'service copilot: running' ;;
            *)
                dry_refuse "$set_id-verify" compose.copilot.yaml 'service copilot' ;;
        esac
    done

    # A dry run of dns for each other provider, with the core.
    for provider in route53 cloudflare porkbun; do
        deploy_env "$dry/dry-$provider.env" '' "$provider" ok
        dry_case "dry-$provider" /dev/null --env-file "$dry/dry-$provider.env" dns
        dry_refuse "dry-$provider" 'a real run stops here'
    done
    dry_expect dry-route53 'route53 list-resource-record-sets' 'route53 change-resource-record-sets' \
        '--profile check-fixture' '--hosted-zone-id Z0CHECKFIXTURE'
    dry_expect dry-cloudflare 'dns_records?type=A&name.exact=ok.ec2.check-fixture.test' '-X POST' '-X PATCH'
    dry_expect dry-porkbun dns/retrieveByNameType/ dns/create/ dns/editByNameType/

    # `deploy` gives the embedding model of the environment file to the template.
    sed 's/^EC2_BEDROCK_EMBED_MODEL=.*$/EC2_BEDROCK_EMBED_MODEL=check.fixture-embed-v9:0/' "$dry/core.env" >"$dry/embed.env"
    dry_case embed-deploy /dev/null --env-file "$dry/embed.env" deploy
    dry_expect embed-deploy 'BedrockEmbedModel=check.fixture-embed-v9:0'
    dry_refuse embed-deploy "BedrockEmbedModel=$embed" 'a real run stops here'

    # The bootstrap names commands of the deploy script in its next steps: `dns`
    # for the records of the core edge, and `secret`. The usage text of the deploy
    # script must list each command that the bootstrap names.
    if [ -f scripts/host-bootstrap.sh ]; then
        sed -n '/^# Usage:/,/^#   --dry-run/s/^#   \([a-z][a-z-]*\).*$/\1/p' scripts/aws-host.sh >"$dry/commands"
        grep -o 'scripts/aws-host\.sh [a-z][a-z-]*' scripts/host-bootstrap.sh | LC_ALL=C sort -u >"$dry/named" || :
        grep -qxF 'scripts/aws-host.sh dns' "$dry/named" ||
            echo "check: scripts/host-bootstrap.sh does not name the command scripts/aws-host.sh dns for the records of the core edge" >>"$dry/problems"
        while read -r _ word <&3; do
            grep -qxF -e "$word" "$dry/commands" ||
                echo "check: scripts/host-bootstrap.sh names the command $word of scripts/aws-host.sh, and the usage text of that script does not list it" >>"$dry/problems"
        done 3<"$dry/named"
    fi

    # A setting that the scripts must refuse.
    deploy_env "$dry/unknown.env" pangolin,unknown porkbun ok
    for command in deploy dns verify; do
        stop_case "unknown-$command" /dev/null 'EC2_OPTIONS is not a list of these names' \
            --env-file "$dry/unknown.env" "$command"
    done
    stop_case unknown-secret "$dry/unknown.env" 'EC2_OPTIONS is not a list of these names' secret
    deploy_env "$dry/blank.env" 'pangolin, copilot' porkbun ok
    stop_case blank-deploy /dev/null 'EC2_OPTIONS is not a list of these names' --env-file "$dry/blank.env" deploy
    deploy_env "$dry/edge.env" '' manual ok,unknown
    stop_case edge-dns /dev/null 'EC2_EDGE_NAMES is not a list of these names' --env-file "$dry/edge.env" dns
    deploy_env "$dry/provider.env" '' unknown ok
    stop_case provider-dns /dev/null 'EC2_DNS_PROVIDER is not one of these names' --env-file "$dry/provider.env" dns
    deploy_env "$dry/mismatch.env" pangolin manual ok
    stop_case mismatch-deploy /dev/null 'the option pangolin needs EC2_DNS_PROVIDER=porkbun' \
        --env-file "$dry/mismatch.env" deploy
    stop_case mismatch-secret "$dry/mismatch.env" 'the option pangolin needs EC2_DNS_PROVIDER=porkbun' secret

    # With an active option, `secret` needs each empty name of its block and the
    # Porkbun pair. Each line of the file with the name loses its value.
    option_name=$(block_names Option pangolin empty | sed -n 1p)
    [ -n "$option_name" ] ||
        echo "check: the block Option pangolin of the sample has no name with an empty value" >>"$dry/problems"
    for name in $option_name EC2_PORKBUN_API_KEY; do
        sed "s/^$name=.*\$/$name=/" "$dry/pangolin.env" >"$dry/no-$name.env"
        dry_case "no-$name" "$dry/no-$name.env" secret
        dry_expect "no-$name" "a real run stops here: no value for $name"
        dry_case "allow-$name" "$dry/no-$name.env" secret --allow-empty "$name"
        dry_refuse "allow-$name" 'a real run stops here'
    done
    # The same name with the option off: an empty value passes, and --allow-empty
    # for it is not an error.
    dry_case off-allow "$dry/core.env" secret --allow-empty "${option_name:-EC2_PORKBUN_API_KEY}"
    dry_refuse off-allow 'a real run stops here'
    # The first `secret` run with the option pangolin. The installer gets the two
    # names below from the first setup of Pangolin, so they are empty: the run
    # stops without --allow-empty for each of them, and it passes with it.
    # $dry/first.env and $dry/first-allow hold this case for the secret check.
    first_names='EC2_PANGOLIN_API_KEY EC2_PANGOLIN_ORG'
    cp "$dry/pangolin.env" "$dry/first.env"
    : >"$dry/first-allow"
    for name in $first_names; do
        block_names Option pangolin empty | grep -qxF -e "$name" ||
            echo "check: the block Option pangolin of .env.example has no name $name with an empty value" >>"$dry/problems"
        sed "s/^$name=.*\$/$name=/" "$dry/first.env" >"$dry/first.new"
        mv "$dry/first.new" "$dry/first.env"
        printf '%s\n' --allow-empty "$name" >>"$dry/first-allow"
    done
    set --
    while IFS= read -r word <&3; do
        set -- "$@" "$word"
    done 3<"$dry/first-allow"
    dry_case first-stop "$dry/first.env" secret
    dry_expect first-stop "a real run stops here: no value for $first_names"
    dry_case first-allow "$dry/first.env" secret "$@"
    dry_refuse first-allow 'a real run stops here'
    dry_expect first-allow "secret: empty and accepted by --allow-empty: $first_names"
    # One of the two names only: the other name still stops the run.
    dry_case first-one "$dry/first.env" secret --allow-empty "${first_names%% *}"
    dry_expect first-one "a real run stops here: no value for ${first_names##* }"

    # The environment file of the host cannot hold a value with a single quote or with
    # a backslash at its end (scripts/host-bootstrap.sh). So `secret` must refuse each.
    fake=$(sed -n 1p "$dry/fakes")
    [ -n "$fake" ] || echo "check: the sample has no empty name in Part 1, so the fixture has no fake value" >>"$dry/problems"
    awk -v fake="$fake" '{
        i = index($0, "=")
        if (i && substr($0, i + 1) == fake) print substr($0, 1, i) "\"" fake "\047x\""
        else print
    }' "$dry/core.env" >"$dry/quote.env"
    awk -v fake="$fake" '{
        i = index($0, "=")
        if (i && substr($0, i + 1) == fake) print $0 "\\"
        else print
    }' "$dry/core.env" >"$dry/backslash.env"
    dry_case quote-secret "$dry/quote.env" secret
    dry_case backslash-secret "$dry/backslash.env" secret
    for id in quote-secret backslash-secret; do
        grep -qF 'a real run stops here: a single quote in the value' "$dry/$id.out" ||
            echo "check: the dry run $id does not refuse a value that the environment file of the host cannot hold" >>"$dry/problems"
    done

    # The two scripts read the environment file and the option settings with the
    # same code.
    if [ -f scripts/dns-records.sh ]; then
        for name in parse listed options OPTIONS EDGE_NAMES PROVIDERS; do
            python_text scripts/aws-host.sh "$name" >"$dry/code.deploy"
            python_text scripts/dns-records.sh "$name" >"$dry/code.dns"
            if [ ! -s "$dry/code.deploy" ] || ! cmp -s "$dry/code.deploy" "$dry/code.dns"; then
                echo "check: the Python text $name of scripts/aws-host.sh and of scripts/dns-records.sh are not equal" >>"$dry/problems"
            fi
        done
    fi
    deploy_end "$label"
}

# secret_check: a real run of `secret` for each option set, and for the first
# run with the option pangolin. A stub of the AWS CLI keeps the file of the
# secret, and the check reads the names of that file. Then the Python text of
# the bootstrap renders the environment file of the host from that secret, and
# the file set test of `verify` runs on that file.
secret_check() {
    label="secret scripts/aws-host.sh"
    # The Python text of the bootstrap, as scripts/host-bootstrap.sh holds it.
    : >"$dry/boot.py"
    if [ -f scripts/host-bootstrap.sh ]; then
        awk '/^helper_py=\047$/ { on = 1; next } on && /^\047$/ { exit } on { print }' \
            scripts/host-bootstrap.sh >"$dry/boot.py"
        [ -s "$dry/boot.py" ] ||
            echo "check: scripts/host-bootstrap.sh has no Python text with the name helper_py" >>"$dry/problems"
    fi
    for set_id in core copilot pangolin pangolin-copilot first; do
        case $set_id in
            core) options= ;;
            pangolin-copilot) options=pangolin,copilot ;;
            first) options=pangolin ;;
            *) options=$set_id ;;
        esac
        # The case `first` is the first run with the option pangolin
        # (dry_run_check): two names are empty, and --allow-empty names each.
        set --
        if [ "$set_id" = first ]; then
            while IFS= read -r word <&3; do
                set -- "$@" "$word"
            done 3<"$dry/first-allow"
        fi
        zone=$dry/secret-$set_id
        mkdir -p "$zone/host"
        python3 -I "$dry/stub.py" init "$zone" "$dry/$set_id.env" "$dry/fakes" 198.51.100.7 ||
            die "the stub cannot make its state"
        deploy_case "write-$set_id" scripts/aws-host.sh "$dry/serve" "$dry/$set_id.env" secret "$@"
        if [ "$case_rc" -ne 0 ]; then
            echo "check: the run write-$set_id of secret exits with $case_rc" >>"$dry/problems"
            sed -n 's/^aws-host: /  /p' "$dry/write-$set_id.err" >>"$dry/problems"
            continue
        fi
        # The names of the secret: each name of Part 1; each other name of the
        # environment file with its value; no name of a DNS provider block and no
        # value of such a name, but the Porkbun pair with the option pangolin.
        python3 -I "$dry/stub.py" secret "$zone" .env.example "$dry/$set_id.env" >>"$dry/problems" ||
            echo "check: the stub cannot read the secret of the run write-$set_id" >>"$dry/problems"
        [ -s "$dry/boot.py" ] || continue
        # The bootstrap reads this secret on the host. It must accept it, and it
        # must write the file set of the contract into COMPOSE_FILE of the
        # environment file of the host. A message of the bootstrap holds a name,
        # never a value.
        files=$(set_files "$options" | tr ' ' ':')
        if ! python3 -I "$dry/boot.py" render .env.example "$top" "$zone/host/.env" <"$zone/secret.json" \
            >"$dry/render-$set_id.out" 2>"$dry/render-$set_id.err"; then
            echo "check: the bootstrap refuses the secret of the run write-$set_id" >>"$dry/problems"
            sed -n 's/^host-bootstrap: error: /  /p' "$dry/render-$set_id.err" >>"$dry/problems"
            continue
        fi
        grep -qxF -e "COMPOSE_FILE=$files" "$zone/host/.env" ||
            echo "check: with the secret of the run write-$set_id, the bootstrap does not write the file set of the contract into COMPOSE_FILE" >>"$dry/problems"
        # The file set test of `verify`, as the host runs it, on that file.
        [ "$set_id" != first ] || continue
        # shellcheck disable=SC2016 # the shell of the test expands the names
        {
            echo 'rc=0'
            sed -n 's/^dry run:   \(host_files=.*\)$/\1/p; s/^dry run:   \(if \[ "\$host_files" = .*\)$/\1/p' \
                "$dry/$set_id-verify.out"
            echo 'exit $rc'
        } >"$dry/host-$set_id.sh"
        [ "$(wc -l <"$dry/host-$set_id.sh")" -eq 4 ] ||
            echo "check: the dry run $set_id-verify does not print the two lines of the file set test" >>"$dry/problems"
        (cd "$zone/host" && env -i PATH="$PATH" sh "$dry/host-$set_id.sh") >"$dry/host-$set_id.out" 2>&1 ||
            echo "check: the file set test of verify fails on the environment file that the bootstrap writes for EC2_OPTIONS=$options" >>"$dry/problems"
        grep -qxF -e "file set $files: the host has it" "$dry/host-$set_id.out" ||
            echo "check: the file set test of verify does not name the file set of EC2_OPTIONS=$options" >>"$dry/problems"
    done
    # The file set test of `verify` must fail when the host has another file set:
    # the test of the option pangolin on the environment file of a core host.
    if [ -f "$dry/secret-core/host/.env" ] && [ -f "$dry/host-pangolin.sh" ]; then
        if (cd "$dry/secret-core/host" && env -i PATH="$PATH" sh "$dry/host-pangolin.sh") >"$dry/host-other.out" 2>&1; then
            echo "check: the file set test of verify passes on a host with another file set" >>"$dry/problems"
        fi
    fi
    # A second run writes a new version of the secret that is there.
    zone=$dry/secret-core
    deploy_case write-again scripts/aws-host.sh "$dry/serve" "$dry/core.env" secret
    [ "$case_rc" -eq 0 ] || echo "check: the second run of secret exits with $case_rc" >>"$dry/problems"
    printf 'create-secret\nput-secret-value\n' >"$dry/expected"
    cmp -s "$dry/expected" "$zone/ops" ||
        echo "check: two runs of secret do not make the secret and then write a new version" >>"$dry/problems"
    python3 -I scripts/test-generated-keys.py "$dry" "$top" >"$dry/generated.out" 2>"$dry/generated.err" ||
        echo "check: generated-key regression fails; inspect the private check output" >>"$dry/problems"
    for zone in "$dry"/secret-*; do
        [ ! -s "$zone/problems" ] || cat "$zone/problems" >>"$dry/problems"
    done
    zone=$dry/zone
    deploy_end "$label"
}

# expected_ops <provider> <run>: print the calls that the provider must make
# in the fake zone of provider_check, in the order of the records.
expected_ops() {
    for name in ok llm ov agent; do
        echo "read $name.$zone_domain"
        [ "$2" = 1 ] || continue
        case $1:$name in
            route53:llm | route53:ov) echo "upsert $name.$zone_domain" ;;
            *:llm) echo "edit $name.$zone_domain" ;;
            *:ov) echo "create $name.$zone_domain" ;;
            cloudflare:agent) echo "edit $name.$zone_domain" ;;
        esac
    done
}

# provider_check: each DNS provider with a stub of its tool. The stub keeps the
# records of a fake zone and writes each call into a log. Four records: one is
# correct, one has another address, one is not there, and one is correct but
# proxied (only the provider cloudflare knows this state).
provider_check() {
    label="providers scripts/dns-records.sh"
    zone_domain=ec2.check-fixture.test
    zone_address=198.51.100.7
    for provider in manual porkbun route53 cloudflare; do
        zone=$dry/zone-$provider
        mkdir -p "$zone"
        deploy_env "$dry/$provider.env" '' "$provider" agent,ov,llm,ok
        python3 -I "$dry/stub.py" init "$zone" "$dry/$provider.env" "$dry/fakes" "$zone_address" \
            "ok.$zone_domain=$zone_address" "llm.$zone_domain=198.51.100.99" \
            "agent.$zone_domain=$zone_address" "proxied=agent.$zone_domain" ||
            die "the stub cannot make its state"
        set -- scripts/dns-records.sh "$dry/serve" /dev/null --env-file "$dry/$provider.env" \
            --address "$zone_address"
        # plan: the record set, and no call.
        deploy_case "$provider-plan" "$@" plan
        [ "$case_rc" -eq 0 ] || echo "check: plan with the provider $provider exits with $case_rc" >>"$dry/problems"
        for name in ok llm ov agent; do
            dry_expect "$provider-plan" "$name.$zone_domain. 600 IN A $zone_address"
        done
        [ ! -s "$zone/calls" ] || echo "check: plan with the provider $provider makes a call" >>"$dry/problems"
        # apply, two times: the first run changes only a record that differs, and
        # the second run changes nothing.
        for run in 1 2; do
            : >"$zone/ops"
            deploy_case "$provider-apply-$run" "$@" apply
            [ "$case_rc" -eq 0 ] || {
                echo "check: apply $run with the provider $provider exits with $case_rc"
                sed -n 's/^dns-records: /  /p' "$dry/$provider-apply-$run.err"
            } >>"$dry/problems"
            if [ "$provider" = manual ]; then
                [ ! -s "$zone/calls" ] || echo "check: apply with the provider manual makes a call" >>"$dry/problems"
                for name in ok llm ov agent; do
                    dry_expect "manual-apply-$run" "$name.$zone_domain. 600 IN A $zone_address"
                done
                continue
            fi
            expected_ops "$provider" "$run" >"$dry/expected"
            cmp -s "$dry/expected" "$zone/ops" ||
                echo "check: apply $run with the provider $provider does not make the calls that the fake zone needs" >>"$dry/problems"
        done
        [ "$provider" != manual ] || continue
        dry_expect "$provider-apply-1" "dns: ok.$zone_domain A $zone_address: no change" \
            "dns: llm.$zone_domain A $zone_address: done (edit)" \
            "dns: ov.$zone_domain A $zone_address: done (create)"
        [ "$(grep -c ': no change$' "$dry/$provider-apply-2.out")" -eq 4 ] ||
            echo "check: the second apply with the provider $provider does not print no change for each record" >>"$dry/problems"
    done
    # A credential that the provider refuses: the script stops, makes no change
    # and prints no credential.
    for provider in porkbun cloudflare; do
        zone=$dry/zone-$provider-refused
        mkdir -p "$zone"
        python3 -I "$dry/stub.py" init "$zone" "$dry/$provider.env" "$dry/fakes" "$zone_address" refuse ||
            die "the stub cannot make its state"
        deploy_case "$provider-refused" scripts/dns-records.sh "$dry/serve" /dev/null \
            --env-file "$dry/$provider.env" --address "$zone_address" apply
        [ "$case_rc" -ne 0 ] && grep -qF 'refuses the request' "$dry/$provider-refused.err" ||
            echo "check: apply with the provider $provider does not stop when the provider refuses the credential" >>"$dry/problems"
        [ ! -s "$zone/ops" ] ||
            echo "check: apply with the provider $provider changes a record after the provider refuses the credential" >>"$dry/problems"
    done
    # The provider porkbun through the `dns` command of the deploy script, with
    # the option pangolin and the five names. The calls to curl must be the calls
    # of the `dns` command from before the provider files. This text is the log
    # of that command with this stub and this zone: pangolin and ov are correct,
    # ok has another address, llm and agent are not there.
    zone=$dry/zone-earlier
    mkdir -p "$zone"
    deploy_env "$dry/earlier.env" pangolin porkbun agent,ov,llm,ok
    python3 -I "$dry/stub.py" init "$zone" "$dry/earlier.env" "$dry/fakes" "$zone_address" \
        "pangolin.$zone_domain=$zone_address" "ok.$zone_domain=198.51.100.99" "ov.$zone_domain=$zone_address" ||
        die "the stub cannot make its state"
    deploy_case earlier scripts/aws-host.sh "$dry/serve" /dev/null --env-file "$dry/earlier.env" dns
    [ "$case_rc" -eq 0 ] || echo "check: dns with the provider porkbun and the option pangolin exits with $case_rc" >>"$dry/problems"
    cat >"$dry/expected" <<'EOT'
curl -sS --max-time 30 -o <work>/answer.json -H Content-Type: application/json --data @<work>/auth.json https://api.porkbun.com/api/json/v3/dns/retrieveByNameType/check-fixture.test/A/pangolin.ec2 | body: apikey=<a fake credential> secretapikey=<a fake credential>
curl -sS --max-time 30 -o <work>/answer.json -H Content-Type: application/json --data @<work>/auth.json https://api.porkbun.com/api/json/v3/dns/retrieveByNameType/check-fixture.test/A/ok.ec2 | body: apikey=<a fake credential> secretapikey=<a fake credential>
curl -sS --max-time 30 -o <work>/answer.json -H Content-Type: application/json --data @<work>/record.json https://api.porkbun.com/api/json/v3/dns/editByNameType/check-fixture.test/A/ok.ec2 | body: apikey=<a fake credential> content=198.51.100.7 secretapikey=<a fake credential> ttl=600
curl -sS --max-time 30 -o <work>/answer.json -H Content-Type: application/json --data @<work>/auth.json https://api.porkbun.com/api/json/v3/dns/retrieveByNameType/check-fixture.test/A/llm.ec2 | body: apikey=<a fake credential> secretapikey=<a fake credential>
curl -sS --max-time 30 -o <work>/answer.json -H Content-Type: application/json --data @<work>/record.json https://api.porkbun.com/api/json/v3/dns/create/check-fixture.test | body: apikey=<a fake credential> content=198.51.100.7 name=llm.ec2 secretapikey=<a fake credential> ttl=600 type=A
curl -sS --max-time 30 -o <work>/answer.json -H Content-Type: application/json --data @<work>/auth.json https://api.porkbun.com/api/json/v3/dns/retrieveByNameType/check-fixture.test/A/ov.ec2 | body: apikey=<a fake credential> secretapikey=<a fake credential>
curl -sS --max-time 30 -o <work>/answer.json -H Content-Type: application/json --data @<work>/auth.json https://api.porkbun.com/api/json/v3/dns/retrieveByNameType/check-fixture.test/A/agent.ec2 | body: apikey=<a fake credential> secretapikey=<a fake credential>
curl -sS --max-time 30 -o <work>/answer.json -H Content-Type: application/json --data @<work>/record.json https://api.porkbun.com/api/json/v3/dns/create/check-fixture.test | body: apikey=<a fake credential> content=198.51.100.7 name=agent.ec2 secretapikey=<a fake credential> ttl=600 type=A
EOT
    grep '^curl ' "$zone/calls" >"$dry/found" || :
    cmp -s "$dry/expected" "$dry/found" ||
        echo "check: the provider porkbun does not make the calls of the earlier dns command" >>"$dry/problems"
    # The `dns` command of the deploy script for the core, as the next steps of
    # the bootstrap name it for the core edge. It gives one record to the Elastic
    # IP of the stack for each name of EC2_EDGE_NAMES below EC2_DOMAIN, and no
    # other record. The provider is manual: the command prints each record, and
    # its one call reads the outputs of the stack.
    zone=$dry/zone-core
    mkdir -p "$zone"
    python3 -I "$dry/stub.py" init "$zone" "$dry/core.env" "$dry/fakes" "$zone_address" ||
        die "the stub cannot make its state"
    deploy_case core-dns scripts/aws-host.sh "$dry/serve" /dev/null --env-file "$dry/core.env" dns
    [ "$case_rc" -eq 0 ] || echo "check: dns with the core and the provider manual exits with $case_rc" >>"$dry/problems"
    for name in $(sed -n 's/^EC2_EDGE_NAMES=//p' "$dry/core.env" | tr ',' ' '); do
        echo "$name.$zone_domain. 600 IN A $zone_address"
    done | LC_ALL=C sort >"$dry/expected"
    grep ' IN A ' "$dry/core-dns.out" | LC_ALL=C sort >"$dry/found" || :
    [ -s "$dry/expected" ] && cmp -s "$dry/expected" "$dry/found" ||
        echo "check: dns with the core does not print one record to the address of the stack for each name of EC2_EDGE_NAMES" >>"$dry/problems"
    if [ ! -s "$zone/calls" ] || grep -qv '^aws .* cloudformation describe-stacks ' "$zone/calls"; then
        echo "check: dns with the core and the provider manual does not make the one call that reads the outputs of the stack" >>"$dry/problems"
    fi
    # The names of each provider file are the names of its block of the sample.
    for provider in manual porkbun route53 cloudflare; do
        [ -f "scripts/dns/$provider.sh" ] || {
            echo "check: scripts/dns/$provider.sh is not there" >>"$dry/problems"
            continue
        }
        sed -n "s/^provider_names='\{0,1\}\([A-Z0-9_ ]*\)'\{0,1\}\$/\1/p" "scripts/dns/$provider.sh" |
            tr ' ' '\n' | sed '/^$/d' | LC_ALL=C sort >"$dry/names.file"
        block_names 'DNS provider' "$provider" | LC_ALL=C sort >"$dry/names.sample"
        cmp -s "$dry/names.file" "$dry/names.sample" ||
            echo "check: the names of scripts/dns/$provider.sh are not the names of the block DNS provider $provider of the sample" >>"$dry/problems"
    done
    for zone in "$dry"/zone-*; do
        [ ! -s "$zone/problems" ] || cat "$zone/problems" >>"$dry/problems"
    done
    zone=$dry/zone
    deploy_end "$label"
}

# render_check <template>: the security group and the instance role of the
# host template, with each value of the parameters that change them.
render_check() {
    label="render $1"
    render_text >"$dry/render.py"
    rc=0
    python3 -I "$dry/render.py" "$1" .env.example >"$dry/render.out" 2>"$dry/render.err" || rc=$?
    case $rc in
        0) result "$label" ok ;;
        3) no_tool "$label" "the Python module yaml" ;;
        *)
            cat "$dry/render.out" "$dry/render.err" >&2
            result "$label" FAIL
            ;;
    esac
}

# render_text: print the Python text of render_check. It reads the template
# with each condition of a parameter value, as CloudFormation does.
render_text() {
    cat <<'EOT'
import re
import sys

try:
    import yaml
except ImportError:
    sys.exit(3)


class Loader(yaml.SafeLoader):
    pass


def short(loader, suffix, node):
    """A short form such as !Ref or !Sub as the long form of CloudFormation."""
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {"Ref" if suffix == "Ref" else "Fn::" + suffix: value}


Loader.add_multi_constructor("!", short)
with open(sys.argv[1], encoding="utf-8") as handle:
    template = yaml.load(handle, Loader=Loader)
problems = []


def render(values):
    """The resources of the template for one set of parameter values."""
    given = {name: str(body.get("Default", "")) for name, body in template["Parameters"].items()}
    given.update(values)

    def value(node):
        if isinstance(node, dict) and set(node) == {"Ref"} and node["Ref"] in given:
            return given[node["Ref"]]
        return node

    def test(node):
        (name, body), = node.items()
        if name == "Fn::Equals":
            return value(body[0]) == value(body[1])
        if name == "Fn::Not":
            return not test(body[0])
        if name == "Fn::Or":
            return any(test(item) for item in body)
        if name == "Fn::And":
            return all(test(item) for item in body)
        if name == "Condition":
            return truth[body]
        raise ValueError("a condition of the template has a form that this check does not know")

    truth = {}
    for name, body in template.get("Conditions", {}).items():
        truth[name] = test(body)
    gone = object()

    def walk(node):
        if isinstance(node, dict):
            if set(node) == {"Fn::If"}:
                name, yes, no = node["Fn::If"]
                return walk(yes if truth[name] else no)
            if node == {"Ref": "AWS::NoValue"}:
                return gone
            return {key: item for key, item in ((key, walk(item)) for key, item in node.items())
                    if item is not gone}
        if isinstance(node, list):
            return [item for item in (walk(item) for item in node) if item is not gone]
        return node

    return {name: walk(body) for name, body in template["Resources"].items()
            if truth.get(body.get("Condition"), True)}


def many(node):
    return node if isinstance(node, list) else [node]


def udp_ports(resources):
    """The port ranges of each rule of the host that is not a TCP rule."""
    rules = list(resources["InstanceSecurityGroup"]["Properties"]["SecurityGroupIngress"])
    rules += [body["Properties"] for body in resources.values()
              if body["Type"] == "AWS::EC2::SecurityGroupIngress"]
    return sorted((rule.get("FromPort"), rule.get("ToPort")) for rule in rules
                  if str(rule["IpProtocol"]) not in ("tcp", "6"))


# The security group: no UDP rule without the option pangolin, the two tunnel ports with it.
if udp_ports(render({"EnablePangolin": "false"})):
    problems.append("with EnablePangolin false, the security group of the host has a rule that is not a TCP rule")
if udp_ports(render({"EnablePangolin": "true"})) != [(21820, 21820), (21820, 21820), (51820, 51820), (51820, 51820)]:
    problems.append("with EnablePangolin true, the UDP rules of the security group of the host are not "
                    "the ports 51820 and 21820, each for IPv4 and for IPv6")

# The instance role: no wildcard in an action, and the two Bedrock models only.
MODELS = {"inference-profile/us.openai.gpt-6-luna", "foundation-model/openai.gpt-6-luna",
          "foundation-model/${BedrockEmbedModel}"}
for bucket in ("false", "true"):
    role = render({"EnableBucket": bucket})["InstanceRole"]["Properties"]
    if role.get("ManagedPolicyArns"):
        problems.append("the instance role has a managed policy")
    found = set()
    for policy in role["Policies"]:
        for statement in many(policy["PolicyDocument"]["Statement"]):
            actions = many(statement["Action"])
            if any("*" in action for action in actions):
                problems.append("the instance role has an action with a wildcard")
            if not any(action.startswith("bedrock:") for action in actions):
                continue
            if not set(actions) <= {"bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"}:
                problems.append("a Bedrock statement of the instance role has an action that is not an invoke action")
            for resource in many(statement["Resource"]):
                text = resource.get("Fn::Sub", "") if isinstance(resource, dict) else str(resource)
                # The region is the region of the stack or each region. The account is
                # the account of the stack, or no account for a foundation model.
                model = re.sub(r"^arn:\$\{AWS::Partition\}:bedrock:(\$\{AWS::Region\}|\*):"
                               r"(\$\{AWS::AccountId\})?:", "", text)
                if model not in MODELS:
                    problems.append("a Bedrock statement of the instance role names a resource that is "
                                    "not the Luna model or the embedding model")
                found.add(model)
    if found != MODELS:
        problems.append("the instance role does not name the Luna model and the embedding model")
pattern = template["Parameters"]["BedrockEmbedModel"].get("AllowedPattern", "")
if not re.fullmatch(pattern, template["Parameters"]["BedrockEmbedModel"]["Default"]) or \
        any(re.fullmatch(pattern, text) for text in ("*", "amazon.*", "a/b", "")):
    problems.append("the pattern of the parameter BedrockEmbedModel accepts a wildcard or refuses its default")

# The two models of the gateway are the models of .env.example. The role must name them:
# the Luna inference profile as a fixed text, and the embedding model as the default of
# the parameter, which `deploy` sets from the same name.
sample = {}
with open(sys.argv[2], encoding="utf-8") as handle:
    for line in handle:
        match = re.fullmatch(r"(EC2_BEDROCK_[A-Z_]+)=(.*)", line.strip())
        if match:
            sample[match.group(1)] = match.group(2)
if "inference-profile/" + sample.get("EC2_BEDROCK_LUNA_MODEL", "") not in MODELS:
    problems.append("the instance role does not name the inference profile of EC2_BEDROCK_LUNA_MODEL of .env.example")
if template["Parameters"]["BedrockEmbedModel"]["Default"] != sample.get("EC2_BEDROCK_EMBED_MODEL"):
    problems.append("the default of the parameter BedrockEmbedModel is not EC2_BEDROCK_EMBED_MODEL of .env.example")

for text in sorted(set(problems)):
    print("check: " + text)
sys.exit(1 if problems else 0)
EOT
}

# stub_text: print the Python text of the stub of curl and of the AWS CLI for
# the tests with a fake zone.
stub_text() {
    cat <<'EOT'
# The stub of curl and of the AWS CLI for the provider tests of scripts/check.sh. It keeps
# the records of a fake zone in a state file and writes each call into a log. It makes no
# network call. It never writes a credential into a log: it writes a problem line.
import hashlib
import json
import os
import re
import stat
import sys

tool, args = sys.argv[1], sys.argv[2:]
home = args[0] if tool in ("init", "secret") else os.environ["CHECK_STUB_DIR"]
state_path = os.path.join(home, "state.json")
state = {"fakes": [], "records": {}}
if tool != "init":
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)
records = state["records"]


def names(path):
    """The NAME=value lines of an environment file as a dict."""
    with open(path, encoding="utf-8") as handle:
        return dict(line.rstrip("\n").split("=", 1) for line in handle
                    if re.match(r"[A-Za-z_][A-Za-z0-9_]*=", line))


def add(name, text):
    with open(os.path.join(home, name), "a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def problem(text):
    add("problems", "check: " + text)


def private(path, what):
    if stat.S_IMODE(os.stat(path).st_mode) != 0o600:
        problem("%s does not have the mode 0600" % what)


def clean(word):
    """An argument with no temporary directory in it, and with no fake credential."""
    word = re.sub(r"[^@ ]*/(?:aws-host|dns-records)\.[A-Za-z0-9]+/", "<work>/", word)
    for fake in state["fakes"]:
        word = word.replace(fake, "<a fake credential>")
    return word


def finish(code=0):
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    sys.exit(code)


def record_id(name):
    return hashlib.md5(name.encode()).hexdigest()


def curl():
    out = body = method = url = None
    headers, i = [], 0
    while i < len(args):
        word = args[i]
        if word in ("-o", "-H", "--data", "-X", "--max-time"):
            i += 1
            if word == "-o":
                out = args[i]
            elif word == "-H":
                headers.append(args[i])
            elif word == "--data":
                body = args[i]
            elif word == "-X":
                method = args[i]
        elif word.startswith("-") and word != "-sS":
            problem("the stub of curl does not know the option " + clean(word))
            finish(2)
        elif not word.startswith("-"):
            url = word
        i += 1
    lines, data = [], {}
    for header in headers:
        if header.startswith("@"):
            private(header[1:], "the header file of a curl call")
            with open(header[1:], encoding="utf-8") as handle:
                lines += handle.read().splitlines()
        else:
            lines.append(header)
    if body:
        if not body.startswith("@"):
            problem("the body of a curl call is not a file")
            finish(2)
        private(body[1:], "the body file of a curl call")
        with open(body[1:], encoding="utf-8") as handle:
            data = json.load(handle)
    method = method or ("POST" if body else "GET")
    shown = " ".join("%s=%s" % (key, clean(str(data[key]))) for key in sorted(data))
    add("calls", "curl " + " ".join(clean(word) for word in args) + (" | body: " + shown if data else ""))
    porkbun = re.fullmatch(r"https://api\.porkbun\.com/api/json/v3/dns/"
                           r"(retrieveByNameType|create|editByNameType)/([a-z0-9.-]+)(?:/A/([a-z0-9.-]+))?", url or "")
    cloudflare = re.fullmatch(r"https://api\.cloudflare\.com/client/v4/zones/([0-9a-f]{32})/dns_records"
                              r"(?:/([0-9a-f]{32})|\?type=A&name\.exact=([a-z0-9.-]+))?", url or "")
    if porkbun:
        operation, zone, sub = porkbun.groups()
        want = state["porkbun"]
        if data.get("apikey") != want["apikey"] or data.get("secretapikey") != want["secretapikey"]:
            answer = {"status": "ERROR", "message": "Invalid API key. (002)"}
        elif operation == "retrieveByNameType":
            name = sub + "." + zone
            add("ops", "read " + name)
            answer = {"status": "SUCCESS", "records": [
                {"id": str(number), "name": name, "type": "A", "content": value, "ttl": "600"}
                for number, value in enumerate(records.get(name, []), 1)]}
        elif operation == "create":
            name = data["name"] + "." + zone
            add("ops", "create " + name)
            records.setdefault(name, []).append(data["content"])
            answer = {"status": "SUCCESS", "id": 1}
        else:
            name = sub + "." + zone
            add("ops", "edit " + name)
            records[name] = [data["content"] for _ in records.get(name, [])]
            answer = {"status": "SUCCESS"}
    elif cloudflare:
        zone, record, name = cloudflare.groups()
        want = state["cloudflare"]
        auth = [line for line in lines if line.lower().startswith("authorization:")]
        if auth != ["Authorization: Bearer " + want["token"]] or zone != want["zone"]:
            answer = {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}],
                      "result": None}
        elif method == "GET" and name:
            add("ops", "read " + name)
            answer = {"success": True, "errors": [], "result": [
                {"id": record_id(name), "name": name, "type": "A", "content": value,
                 "proxied": name in state["proxied"], "ttl": 600}
                for value in records.get(name, [])]}
        elif method == "POST" and not record and not name:
            name = data["name"]
            add("ops", "create " + name)
            records.setdefault(name, []).append(data["content"])
            answer = {"success": True, "errors": [], "result": {"id": record_id(name)}}
        elif method == "PATCH" and record:
            name = [n for n in records if record_id(n) == record][0]
            add("ops", "edit " + name)
            records[name] = [data["content"]]
            if data.get("proxied") is False and name in state["proxied"]:
                state["proxied"].remove(name)
            answer = {"success": True, "errors": [], "result": {"id": record}}
        else:
            answer = {"success": False, "errors": [{"code": 7003, "message": "No route"}], "result": None}
    else:
        problem("the stub of curl gets a call to an address that it does not know")
        finish(2)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(answer, handle)
    finish()


def aws():
    options, words, i = {}, [], 0
    while i < len(args):
        word = args[i]
        if word.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            options[word] = args[i + 1]
            i += 1
        elif not word.startswith("--"):
            words.append(word)
        i += 1
    call = " ".join(words[:2])
    add("calls", "aws " + " ".join(clean(word) for word in args))
    if call == "cloudformation describe-stacks":
        if "Outputs" in options.get("--query", ""):
            print("ElasticIp\t%s\nInstanceId\ti-0c4ec4f1x7e000000" % state["address"])
        else:
            print("CREATE_COMPLETE")
    elif call == "secretsmanager get-secret-value":
        generated = options["--secret-id"].endswith("/generated")
        path = os.path.join(home, "generated.json" if generated else "secret.json")
        if state.get("read_fail"):
            sys.stderr.write("AccessDenied: " + " ".join(state["fakes"]))
            finish(254)
        if not os.path.exists(path):
            sys.stderr.write("ResourceNotFoundException\n")
            finish(254)
        with open(path, encoding="utf-8") as handle:
            print(handle.read())
    elif call == "secretsmanager describe-secret":
        if not state.get("secret"):
            sys.stderr.write("An error occurred (ResourceNotFoundException) when calling the "
                             "DescribeSecret operation: Secrets Manager can't find the specified secret.\n")
            finish(254)
        print(options["--secret-id"])
    elif call in ("secretsmanager create-secret", "secretsmanager put-secret-value"):
        path = options["--secret-string"]
        if not path.startswith("file://"):
            problem("the secret is not a file of the call")
            finish(2)
        private(path[7:], "the file of the secret")
        generated = options.get("--secret-id", "").endswith("/generated")
        if generated and state.get("put_fail"):
            sys.stderr.write("AccessDenied: " + " ".join(state["fakes"]))
            print(" ".join(state["fakes"]))
            finish(254)
        with open(path[7:], encoding="utf-8") as source:
            with open(os.path.join(home, "generated.json" if generated else "secret.json"), "w", encoding="utf-8") as target:
                target.write(source.read())
        state["secret"] = True
        add("ops", words[1])
        print(options.get("--name") or options.get("--secret-id"))
    elif call.startswith("route53 "):
        want = state["route53"]
        if options.get("--profile") != want["profile"] or options.get("--hosted-zone-id") != want["zone"]:
            sys.stderr.write("An error occurred (AccessDenied) when calling the operation\n")
            finish(254)
        if words[1] == "list-resource-record-sets":
            name = options["--start-record-name"]
            add("ops", "read " + name.rstrip("."))
            values = records.get(name.rstrip("."))
            if values:
                found = {"Name": name, "Type": "A", "TTL": 600,
                         "ResourceRecords": [{"Value": value} for value in values]}
            else:
                # Route 53 gives the next record set of the zone, with another name.
                found = {"Name": "zz-next." + name.split(".", 1)[1], "Type": "TXT", "TTL": 300,
                         "ResourceRecords": [{"Value": "\"a record of another name\""}]}
            print(json.dumps({"ResourceRecordSets": [found], "NextToken": "check-fixture"}))
        elif words[1] == "change-resource-record-sets":
            path = options["--change-batch"]
            with open(path[7:], encoding="utf-8") as handle:
                batch = json.load(handle)
            for change in batch["Changes"]:
                item = change["ResourceRecordSet"]
                name = item["Name"].rstrip(".")
                add("ops", "%s %s" % (change["Action"].lower(), name))
                records[name] = [value["Value"] for value in item["ResourceRecords"]]
            print("PENDING")
        else:
            problem("the stub of aws does not know the call " + call)
            finish(2)
    else:
        problem("the stub of aws does not know the call " + call)
        finish(2)
    finish()


def init():
    """init <directory> <environment file> <file of the fake values> <address> [refuse]
    [<name>=<address>]... [proxied=<name>]: write the state of a fake zone. With `refuse`,
    the fake provider refuses the credential of the environment file."""
    env = names(args[1])
    with open(args[2], encoding="utf-8") as handle:
        state["fakes"] = sorted(set(line.strip() for line in handle if line.strip()))
    state["address"] = args[3]
    state["proxied"] = []
    other = "-other" if "refuse" in args[4:] else ""
    for word in args[4:]:
        name, _, value = word.partition("=")
        if name == "proxied":
            state["proxied"].append(value)
        elif value:
            records[name] = [value]
    state["porkbun"] = {"apikey": env.get("EC2_PORKBUN_API_KEY", "") + other,
                        "secretapikey": env.get("EC2_PORKBUN_SECRET_KEY", "")}
    state["cloudflare"] = {"token": env.get("EC2_CLOUDFLARE_API_TOKEN", "") + other,
                           "zone": env.get("EC2_CLOUDFLARE_ZONE_ID", "")}
    state["route53"] = {"profile": env.get("EC2_ROUTE53_PROFILE", ""),
                        "zone": env.get("EC2_ROUTE53_HOSTED_ZONE_ID", "")}
    finish()


def secret():
    """secret <directory> <sample> <environment file>: print a problem line for each name
    that the kept secret must not hold, or must hold. A line holds a name, never a value."""
    env = names(args[2])
    with open(os.path.join(home, "secret.json"), encoding="utf-8") as handle:
        text = handle.read()
    kept = json.loads(text)
    pair = "EC2_PORKBUN_API_KEY EC2_PORKBUN_SECRET_KEY".split()
    pangolin = "pangolin" in env.get("EC2_OPTIONS", "").split(",")
    part = kind = ""
    with open(args[1], encoding="utf-8") as handle:
        for line in handle:
            mark = re.match(r"# ===== Part ([0-9]+)", line)
            if mark:
                part, kind = mark.group(1), ""
            mark = re.match(r"# ----- (Option|DNS provider) ([a-z0-9]+) -----", line)
            if mark:
                kind = mark.group(1)
            name = line.split("=", 1)[0]
            if not re.match(r"[A-Za-z_][A-Za-z0-9_]*=", line):
                continue
            if part == "1" and not kind and name not in kept:
                print("check: the secret lacks the name %s of Part 1" % name)
            if kind != "DNS provider":
                if name in env and kept.get(name) != env[name]:
                    print("check: the secret does not hold the value that the environment file "
                          "gives for the name %s" % name)
                continue
            if pangolin and name in pair:
                if kept.get(name) != env.get(name):
                    print("check: with the option pangolin, the secret lacks the name %s" % name)
            elif name in kept:
                print("check: the secret holds the name %s of a DNS provider" % name)
            elif env.get(name) in state["fakes"] and env[name] in text:
                print("check: the secret holds the value of the name %s of a DNS provider" % name)
    sys.exit(0)


if tool == "init":
    init()
if tool == "secret":
    secret()
for word in args:
    if any(fake in word for fake in state["fakes"]):
        problem("a fake credential is in an argument of a call to " + tool)
if tool == "curl":
    curl()
else:
    aws()
EOT
}

# The directory of the dry run check of the bootstrap: the stubs, one fixture
# for each case that is in place of /, and each output.
boot=$work/boot

# boot_fixture <id> <EC2_OPTIONS> <options with values>: make the fixture of one
# case in $boot/roots/<id>. It holds the stack values file, as the user data
# writes it, and the environment file: each line of .env.example, as the host
# has it. EC2_OPTIONS gets the second argument. An empty name gets a fake value
# of its own when a host with the options of the third argument needs it, and
# $boot/fakes lists these values:
#   - a name of Part 1;
#   - a name of the block `Option <name>` of Part 3, when the option is on;
#   - a name of the block `DNS provider porkbun`, with the option pangolin:
#     the secret holds no other name of a DNS provider block.
# Each other empty name stays empty. The names that the bootstrap makes stay
# empty, as at a first boot. Four names need a form: the scripts test them.
boot_fixture() {
    fixture=$boot/roots/$1
    mkdir -p "$fixture/etc/tenant-ec2" "$fixture/srv/data/tenant-ec2" "$fixture/srv/efs/open-knowledge" \
        "$fixture/srv/efs/openviking" "$fixture/srv/efs/shared"
    chmod 700 "$fixture/srv/data/tenant-ec2"
    # The stack values file holds no secret.
    cat >"$fixture/etc/tenant-ec2/stack.env" <<'EOT'
EC2_PROJECT=tenant-ec2
EC2_AWS_REGION=us-east-1
EC2_STACK_NAME=tenant-ec2-host
EC2_SECRET_ID=tenant-ec2/env
EOT
    (umask 077 && CHECK_BOOT_FAKES=$boot/fakes awk -v made=" $made " -v setting="$2" -v options="$3" '
    # needed(): the host with these options needs a value for a name of this place.
    function needed() {
        if (part == 1) return 1
        if (kind == "option") return index("," options ",", "," block ",") > 0
        if (kind == "dns") return block == "porkbun" && index("," options ",", ",pangolin,") > 0
        return 0
    }
    /^# ===== Part [0-9]/ { part = $4; sub(/:.*$/, "", part); kind = ""; block = "" }
    /^# ----- Option [a-z0-9]+ -----/ { kind = "option"; block = $4 }
    /^# ----- DNS provider [a-z0-9]+ -----/ { kind = "dns"; block = $5 }
    /^[A-Za-z_][A-Za-z0-9_]*=/ {
        i = index($0, "=")
        name = substr($0, 1, i - 1)
        value = substr($0, i + 1)
        if (name == "EC2_OPTIONS") {
            value = setting
        } else if (value ~ /^[ \t]*$/) {
            n++
            if (name == "EC2_DOMAIN") {
                value = "ec2.check-fixture.test"
            } else if (name == "EC2_ACME_EMAIL") {
                value = "owner@check-fixture.test"
            } else if (name == "EC2_ADMIN_CIDR") {
                value = "198.51.100.7/32"
            } else if (!needed() || index(made, " " name " ") > 0) {
                value = ""
            } else if (name == "EC2_PANGOLIN_ORG") {
                value = "check-fixture"
            } else {
                value = sprintf("check-fixture-%s-%03d-q7Zk9w", id, n)
                print value >>ENVIRON["CHECK_BOOT_FAKES"]
            }
        }
        print name "=" value
    }' id="$1" .env.example >"$fixture/srv/data/tenant-ec2/.env") || die "awk cannot read .env.example"
}

# boot_case <id> [<option of sh>]: one dry run of the bootstrap on the fixture
# of the case. Its output goes to $boot/<id>.out and $boot/<id>.err, or to
# $boot/<id>.trace.out and .err for a run with an option of sh. The environment
# of the shell does not reach the script. The stubs are first on PATH.
boot_case() {
    id=$1
    shift
    out=$boot/$id
    [ $# -eq 0 ] || out=$out.trace
    rc=0
    env -i PATH="$boot/bin:$PATH" HOME="$boot/home" TMPDIR="$boot/tmp" CHECK_BOOT_CALLS="$boot/calls" \
        CHECK_BOOT_GIT="$boot_git" sh "$@" scripts/host-bootstrap.sh --dry-run --root "$boot/roots/$id" \
        --stack-env "$boot/roots/$id/etc/tenant-ec2/stack.env" >"$out.out" 2>"$out.err" </dev/null || rc=$?
    [ "$rc" -eq 0 ] || {
        echo "check: the dry run $id of the bootstrap exits with $rc"
        # A message of the script names a name, never a value.
        sed -n 's/^host-bootstrap: error: /  /p' "$out.err"
    } >>"$boot/problems"
}

# boot_has <id> <text>...: the output of the dry run of the case holds each text.
boot_has() {
    id=$1
    shift
    for text in "$@"; do
        grep -qF -e "$text" "$boot/$id.out" ||
            echo "check: the dry run $id of the bootstrap prints no line with: $text" >>"$boot/problems"
    done
}

# boot_lacks <id> <text>...: the output of the dry run of the case holds no such
# text, in each letter case.
boot_lacks() {
    id=$1
    shift
    for text in "$@"; do
        ! grep -qiF -e "$text" "$boot/$id.out" ||
            echo "check: the dry run $id of the bootstrap prints a line with: $text" >>"$boot/problems"
    done
}

# boot_state: print the state that a dry run must not change: each file of each
# fixture with its checksum, kits.lock, and the entry .env of the tree.
boot_state() {
    find "$boot/roots" -exec ls -ld {} + | awk '{ print $1, $3, $4, $NF }'
    find "$boot/roots" -type f -exec cksum {} +
    cksum kits.lock
    for entry in .env kits; do
        if [ -L "$entry" ]; then
            echo "$entry is a link"
        elif [ -e "$entry" ]; then
            echo "$entry is there"
        else
            echo "$entry is not there"
        fi
    done
}

# placeholder_code <script>: the code lines of the Python function
# `placeholder` in the text of a script, with no comment line.
placeholder_code() {
    awk '/^def placeholder\(/ { on = 1 } on && /^$/ { exit } on && $1 !~ /^#/ { print }' "$1"
}

# boot_dry_check: the one line of the dry run check of the bootstrap. The check
# prints no output of a dry run: a fixture holds the fake values.
boot_dry_check() {
    label="dry run scripts/host-bootstrap.sh"
    mkdir -p "$boot/bin" "$boot/home" "$boot/tmp" "$boot/roots"
    : >"$boot/calls"
    : >"$boot/problems"
    : >"$boot/fakes"
    boot_git=$(command -v git || true)
    # The stubs. A question that changes nothing gets the answer "not there". Each
    # other call is a change of the host or a network call: the stub records the
    # first words of it and refuses it. A dry run must not start it.
    for tool in aws curl modprobe; do
        cat >"$boot/bin/$tool" <<'EOT'
#!/bin/sh
echo "${0##*/} ${1:-}" >>"$CHECK_BOOT_CALLS"
exit 99
EOT
    done
    cat >"$boot/bin/docker" <<'EOT'
#!/bin/sh
case "$1 ${2:-} $*" in
    "image inspect "* | "compose "*" ps --status running --services") exit 1 ;;
esac
echo "docker $1 ${2:-}" >>"$CHECK_BOOT_CALLS"
exit 99
EOT
    cat >"$boot/bin/systemctl" <<'EOT'
#!/bin/sh
case ${1:-} in
    is-enabled | is-active) exit 1 ;;
esac
echo "systemctl ${1:-}" >>"$CHECK_BOOT_CALLS"
exit 99
EOT
    cat >"$boot/bin/iptables" <<'EOT'
#!/bin/sh
for word in "$@"; do
    case $word in
        -S | -C | -L) exit 1 ;;
    esac
done
echo "iptables" >>"$CHECK_BOOT_CALLS"
exit 99
EOT
    cat >"$boot/bin/git" <<'EOT'
#!/bin/sh
# Only a question about a clone goes to the real command.
for word in "$@"; do
    case $word in
        rev-parse | status) [ -z "$CHECK_BOOT_GIT" ] && exit 1; exec "$CHECK_BOOT_GIT" "$@" ;;
        clone | fetch | pull | checkout | reset) break ;;
    esac
done
echo "git" >>"$CHECK_BOOT_CALLS"
exit 99
EOT
    chmod 755 "$boot/bin/"*
    made=$(sed -n 's/^MADE_NAMES="\(.*\)"$/\1/p' scripts/host-bootstrap.sh)
    [ -n "$made" ] || echo "check: scripts/host-bootstrap.sh has no line MADE_NAMES=\"...\"" >>"$boot/problems"
    # One case for each file set of the contract: the fixture has the values of
    # the options of the set, and of no other option.
    printf '%s\n' "$FILE_SETS" >"$boot/sets"
    while IFS='|' read -r set_options set_files <&3; do
        boot_fixture "set-$set_options" "$set_options" "$set_options"
    done 3<"$boot/sets"
    # Two cases that the bootstrap must refuse: the option pangolin with the
    # values of the core only, and an option name that it does not know.
    boot_fixture refused-pangolin pangolin ""
    boot_fixture refused-unknown newt ""
    # One more fixture of the core: the stack values turn the operations on.
    boot_fixture operations "" ""
    printf 'EC2_ENABLE_OPERATIONS=true\nEC2_LOG_GROUP=/tenant-ec2/host\n' >>"$boot/roots/operations/etc/tenant-ec2/stack.env"
    [ -s "$boot/fakes" ] ||
        echo "check: .env.example has no empty name in Part 1, so the fixture has no fake value" >>"$boot/problems"
    boot_state >"$boot/before"
    while IFS='|' read -r set_options set_files <&3; do
        id=set-$set_options
        boot_case "$id"
        for n in 1 2 3 4 5 6 7 8 9; do
            boot_has "$id" "step $n of 9"
        done
        # The bootstrap makes the file list of the contract from EC2_OPTIONS.
        boot_has "$id" 'passes the test of the names' 'passes the test of the settings of the edge' \
            "COMPOSE_FILE is $(echo "$set_files" | tr ' ' ':')" 'dry run: nothing changed'
        # The storage change: Docker needs the four durable mounts and the metadata
        # guard. Each file set names that step before its first start of a service.
        boot_has "$id" 'docker.service.d/tenant-ec2-storage.conf' 'systemctl enable --now docker'
        awk '/docker\.service\.d\/tenant-ec2-storage\.conf/ && !mounts { mounts = NR }
            /docker compose up/ && !start { start = NR }
            END { exit !(mounts && start && mounts < start) }' "$boot/$id.out" ||
            echo "check: the dry run $id of the bootstrap does not name the durable mounts of Docker before its first start of a service" >>"$boot/problems"
        # Each step is a step of the active options only.
        case ",$set_options," in
            *,pangolin,*)
                boot_has "$id" 'modprobe wireguard' 'scripts/render-pangolin.sh' '--wait pangolin gerbil traefik' \
                    'would apply' 'Home site'
                boot_lacks "$id" caddy ;;
            *)
                boot_has "$id" 'the kernel module wireguard is not loaded' '--wait caddy' 'no blueprint'
                boot_lacks "$id" modprobe render-pangolin gerbil traefik 'would apply' newt ;;
        esac
        case ",$set_options," in
            *,copilot,*)
                boot_has "$id" '--wait litellm-db litellm copilot' \
                    'models ["bedrock/embed","copilot/embed","bedrock/luna","copilot/luna"]' \
                    'models ["bedrock/luna","copilot/luna"]' 'Copilot login' ;;
            *)
                # A virtual key allows the routes of the core only.
                boot_has "$id" 'models ["bedrock/embed","bedrock/luna"]' 'models ["bedrock/luna"]'
                boot_lacks "$id" copilot ;;
        esac
    done 3<"$boot/sets"
    # A missing value of an option name stops the run when the option is on.
    boot_case refused-pangolin
    boot_has refused-pangolin 'does not pass the test: no value for EC2_PANGOLIN_SERVER_SECRET'
    refused_rc=0
    env -i PATH="$boot/bin:$PATH" HOME="$boot/home" TMPDIR="$boot/tmp" CHECK_BOOT_CALLS="$boot/calls" \
        CHECK_BOOT_GIT="$boot_git" sh scripts/host-bootstrap.sh --dry-run --root "$boot/roots/refused-unknown" \
        --stack-env "$boot/roots/refused-unknown/etc/tenant-ec2/stack.env" \
        >"$boot/refused-unknown.out" 2>"$boot/refused-unknown.err" </dev/null || refused_rc=$?
    [ "$refused_rc" -ne 0 ] && grep -qF 'EC2_OPTIONS holds a name that this script does not know' "$boot/refused-unknown.err" ||
        echo "check: the bootstrap dry run does not stop on an unknown option" >>"$boot/problems"
    # One more run of the core has the operations of the stack on. It must also
    # make no write and no service call in a dry run.
    boot_case operations
    boot_has operations 'before starting amazon-cloudwatch-agent' 'dry run: nothing changed'
    boot_lacks operations 'operations are disabled'
    boot_has set- 'operations are disabled by the stack values'
    # One more run of the core has a trace. The bootstrap must stop it at its
    # start: on the host, a trace prints the secret.
    boot_case set- -x
    boot_state >"$boot/after"
    [ ! -s "$boot/calls" ] ||
        echo "check: the dry run of the bootstrap calls: $(LC_ALL=C sort -u "$boot/calls" | tr '\n' ';')" >>"$boot/problems"
    cmp -s "$boot/before" "$boot/after" ||
        echo "check: the dry run of the bootstrap changes a file of a fixture, kits.lock or the entry .env of the tree" >>"$boot/problems"
    [ -z "$(ls -A "$boot/tmp")" ] ||
        echo "check: the dry run of the bootstrap leaves a file in its temporary directory" >>"$boot/problems"
    if [ -s "$boot/fakes" ] && cat "$boot"/*.out "$boot"/*.err "$boot/calls" | grep -qF -f "$boot/fakes"; then
        echo "check: the dry run of the bootstrap prints a fake value of a fixture" >>"$boot/problems"
    fi
    # A shell prints each trace line with + at its start. Two lines come before
    # the trace stops: `set -eu` and `set +x`.
    [ "$(grep -c '^+' "$boot/set-.trace.err")" -le 2 ] ||
        echo "check: the bootstrap prints a trace with sh -x: it must run set +x at its start" >>"$boot/problems"
    # The bootstrap and the deploy script must refuse the same placeholders.
    if [ -f scripts/aws-host.sh ]; then
        placeholder_code scripts/host-bootstrap.sh >"$boot/placeholder.bootstrap"
        placeholder_code scripts/aws-host.sh >"$boot/placeholder.deploy"
        if [ ! -s "$boot/placeholder.bootstrap" ] || ! cmp -s "$boot/placeholder.bootstrap" "$boot/placeholder.deploy"; then
            echo "check: the function placeholder of scripts/host-bootstrap.sh and of scripts/aws-host.sh are not equal" >>"$boot/problems"
        fi
    fi
    if [ -s "$boot/problems" ]; then
        cat "$boot/problems" >&2
        result "$label" FAIL
    else
        result "$label" ok
    fi
}

# boot_function <name>: print the code of one shell function of the bootstrap.
boot_function() {
    awk -v head="$1() {" '$0 == head { on = 1 } on { print } on && $0 == "}" { exit }' scripts/host-bootstrap.sh
}

# start_check: the one line of the test of a real start of the bootstrap. A dry
# run prints a text only. So this test runs the two functions of the bootstrap
# that start a group of services, `compose` and `up`, with a stub of docker that
# keeps its arguments and starts nothing. One start for each file set.
start_check() {
    label="start scripts/host-bootstrap.sh"
    real=$work/start
    mkdir -p "$real/bin" "$real/root"
    : >"$real/problems"
    : >"$real/functions"
    for name in in_list compose up; do
        boot_function "$name" >"$real/function"
        [ -s "$real/function" ] ||
            echo "check: scripts/host-bootstrap.sh has no function $name" >>"$real/problems"
        cat "$real/function" >>"$real/functions"
    done
    # `compose` gives its command an empty environment, so the stub finds its
    # file from its own path.
    cat >"$real/bin/docker" <<'EOT'
#!/bin/sh
printf '%s\n' "$*" >>"${0%/*}/../calls"
EOT
    chmod 755 "$real/bin/docker"
    # The frame of the two functions: a run that is not a dry run, with a
    # directory in place of /. Each name that step 7 makes has a value.
    cat >"$real/run.sh" <<'EOT'
set -eu
say() { :; }
skip() { :; }
die() { echo "$*" >&2; exit 1; }
env_has() { return 0; }
dry_run=0
root=$1
repo=$2
plan_files=$3
env_file=$root/.env
data=$root/srv/data
efs=$root/srv/efs
MADE_NAMES=
STUB=
WAIT=600
now=
. "$4"
shift 4
up "$@"
EOT
    printf '%s\n' "$FILE_SETS" >"$real/sets"
    while IFS='|' read -r set_options set_files <&3; do
        : >"$real/calls"
        if ! env -i PATH="$real/bin:$PATH" HOME="$real" sh "$real/run.sh" "$real/root" "$top" "$set_files" \
            "$real/functions" litellm >"$real/out" 2>&1 </dev/null; then
            echo "check: with EC2_OPTIONS=$set_options, the function up of the bootstrap fails with a stub of docker" >>"$real/problems"
            continue
        fi
        # The start names each Compose file of the file set, in the order of
        # COMPOSE_FILE, and no other file. Compose then removes each container
        # of a file that is off now, and no container of an active file.
        expected="compose --project-directory $top"
        for f in $set_files; do
            expected="$expected -f $top/$f"
        done
        expected="$expected --env-file $real/root/.env up -d"
        if [ "$(wc -l <"$real/calls")" -ne 1 ]; then
            echo "check: with EC2_OPTIONS=$set_options, one start of the bootstrap is not one call of docker" >>"$real/problems"
            continue
        fi
        IFS= read -r call <"$real/calls" || :
        case $call in
            "$expected "*) ;;
            *) echo "check: with EC2_OPTIONS=$set_options, the start of the bootstrap is not a Compose command with each Compose file of the file set: $(echo "$set_files" | tr ' ' ':')" >>"$real/problems" ;;
        esac
        case " $call " in
            *" --remove-orphans "*) ;;
            *) echo "check: with EC2_OPTIONS=$set_options, the start of the bootstrap has no --remove-orphans: after a change of EC2_OPTIONS, a container of a file that is off stays" >>"$real/problems" ;;
        esac
        case " $call " in
            *" --wait "*" litellm ") ;;
            *) echo "check: with EC2_OPTIONS=$set_options, the start of the bootstrap does not wait for the health of its services" >>"$real/problems" ;;
        esac
    done 3<"$real/sets"
    if [ -s "$real/problems" ]; then
        cat "$real/problems" >&2
        result "$label" FAIL
    else
        result "$label" ok
    fi
}

# The directory of the check of the option contract: a directory in place of the
# clone of the host for each case, the stub of docker and each output.
contract=$work/contract

# agent_lock none|pin: print kits.lock of the tree with no pin of the agent kit,
# or with a pin. The pin of the fixture is no real tag and no real commit. Its
# commit holds a letter: a YAML reader takes a text of digits only as a number.
agent_lock() {
    awk -v mode="$1" '
    /^[^ #]/ { block = $1; kit = "" }
    block == "server_kits:" && /^  [^ #]/ { kit = $1 }
    block == "server_kits:" && kit == "pi-durable:" && /^    source_(repo|tag|commit):/ {
        if (mode == "none" && $1 == "source_repo:") { print; next }
        if (mode == "none") value = "\"\""
        else if ($1 == "source_repo:") value = "https://git.check-fixture.test/owner/agent.git"
        else if ($1 == "source_tag:") value = "portable-v0.0.0"
        else value = sprintf("%038dab", 0)
        print "    " $1 " " value
        next
    }
    { print }' kits.lock
}

# agent_pinned: kits.lock of the tree gives the agent kit a tag and a commit.
agent_pinned() {
    awk '
    function v(t) {
        sub(/^[^:]*:[ \t]*/, "", t)
        sub(/[ \t]+#.*$/, "", t)
        gsub(/["\047]/, "", t)
        sub(/[ \t\r]+$/, "", t)
        return t
    }
    /^[^ #]/ { block = $1; kit = "" }
    block == "server_kits:" && /^  [^ #]/ { kit = $1 }
    block == "server_kits:" && kit == "pi-durable:" && /^    source_tag:/ { tag = v($0) }
    block == "server_kits:" && kit == "pi-durable:" && /^    source_commit:/ { commit = v($0) }
    END { exit !(tag != "" && commit != "") }' kits.lock
}

# boot_started <output>: print each service that a dry run of the bootstrap
# starts, one name in each line, in sorted order.
boot_started() {
    sed -n 's/^host-bootstrap: would run: docker compose up .* --wait \(.*\) (not running now:.*$/\1/p' "$1" |
        tr ' ' '\n' | sed '/^$/d' | LC_ALL=C sort -u
}

# contract_boot <tree> <fixture> <output>: one dry run of the bootstrap of a tree
# on a fixture of boot_dry_check, with the stubs of that check.
contract_boot() {
    (cd "$1" && env -i PATH="$boot/bin:$PATH" HOME="$boot/home" TMPDIR="$boot/tmp" CHECK_BOOT_CALLS="$boot/calls" \
        CHECK_BOOT_GIT="$boot_git" sh scripts/host-bootstrap.sh --dry-run --root "$boot/roots/$2" \
        --stack-env "$boot/roots/$2/etc/tenant-ec2/stack.env" >"$3" 2>"$3.err" </dev/null) ||
        echo "check: a dry run of the bootstrap on the fixture $2 exits with an error" >>"$contract/problems"
}

# contract_host <case> <lock file> <set id> <Compose file>...: make the directory
# that is in place of the clone of the host for one case. It holds the files that
# the commands of `verify` read: the Compose files of the tree, the route files,
# the blueprint, the verifier, the lock of the case, and the environment file
# that the bootstrap writes from the secret of secret_check.
contract_host() {
    host=$contract/$1
    lock_file=$2
    host_set=$3
    shift 3
    mkdir -p "$host/scripts" "$host/litellm" "$host/pangolin" "$host/state/bin"
    for f in compose*.yaml litellm/*.yaml pangolin/blueprint.yaml scripts/verify-deployed.py; do
        [ ! -f "$f" ] || cp "$f" "$host/$f"
    done
    cp "$lock_file" "$host/kits.lock"
    cp "$dry/secret-$host_set/host/.env" "$host/.env"
    compose_services "$@" >"$host/state/services"
    cp "$contract/docker" "$host/state/bin/docker"
}

# contract_verify <case> <id>: run the commands of `verify` in the directory of
# the case. The file state/running lists the services that run. `host_rc` gets
# the exit status, and the output goes to <case>/<id>.out.
contract_verify() {
    host=$contract/$1
    : >"$host/state/required"
    host_rc=0
    (cd "$host" && env -i PATH="$host/state/bin:$PATH" HOME="$contract/home" sh "$host/commands.sh") \
        >"$host/$2.out" 2>&1 </dev/null || host_rc=$?
    [ ! -s "$host/state/other" ] ||
        echo "check: the commands of verify make a call of docker that the fixture does not know" >>"$contract/problems"
}

# contract_check: the one line of the check of the option contract. Four parts
# read EC2_OPTIONS: the bootstrap, the deploy script, the verifier and this
# script (FILE_SETS). For each of the four file sets, the check proves that they
# agree on the Compose files, on the necessary names and on the services. It
# uses the outputs of dry_run_check, of secret_check and of boot_dry_check.
contract_check() {
    label="option contract EC2_OPTIONS"
    mkdir -p "$contract/home"
    : >"$contract/problems"
    # The stub of docker for a directory that is in place of the host. It answers
    # the calls of the commands of `verify` and of the verifier from the files
    # of its own directory: `services` holds each service of the file set, and
    # `running` holds each service that runs. The verifier gives its command no
    # name of the environment, so the stub finds the files from its own path.
    cat >"$contract/docker" <<'EOT'
#!/bin/sh
state=${0%/*}/..
last=
for word in "$@"; do
    last=$word
done
case " $* " in
    " compose ps ") echo "NAME STATUS" ;;
    *" ps --status running --services ") cat "$state/running" ;;
    *" ps --all --quiet "*)
        if grep -qxF -e "$last" "$state/running"; then
            echo "$last" >>"$state/required"
            printf '%012x\n' "$(grep -nxF -e "$last" "$state/services" | cut -d: -f1)"
        fi ;;
    " inspect "*) echo 'true|running|false|false|healthy' ;;
    " exec "*)
        case " $* " in
            *" -i "*) cat >/dev/null ;;
        esac
        case " $* " in
            *" curl "*) echo 200 ;;
        esac ;;
    *) echo "$1 ${2:-}" >>"$state/other"
       exit 99 ;;
esac
EOT
    chmod 755 "$contract/docker"
    # The lock of the tree has a pin of the agent kit, or it has none. The check
    # tests the two states: the lock of the tree as it is, and the other state.
    if agent_pinned; then
        tree_state=pin
        other_state=none
    else
        tree_state=none
        other_state=pin
    fi
    cp kits.lock "$contract/lock.$tree_state"
    agent_lock "$other_state" >"$contract/lock.$other_state"
    # A copy of the tree with the other state of the lock, for the bootstrap.
    other=$contract/tree
    mkdir -p "$other"
    while IFS= read -r f <&3; do
        case $f in
            docs/* | tests/* | skills/*) continue ;;
            */*) mkdir -p "$other/${f%/*}" ;;
        esac
        cp -p "$f" "$other/$f"
    done 3<"$work/files"
    cp "$contract/lock.$other_state" "$other/kits.lock"
    : >"$boot/calls"

    printf '%s\n' "$FILE_SETS" >"$contract/sets"
    while IFS='|' read -r set_options set_files <&3; do
        case $set_options in
            '') set_id=core ;;
            pangolin,copilot) set_id='pangolin-copilot' ;;
            *) set_id=$set_options ;;
        esac
        case $set_options in
            pangolin*) provider=porkbun ;;
            *) provider=manual ;;
        esac
        files=$(echo "$set_files" | tr ' ' ':')
        if [ ! -s "$dry/$set_id-verify.out" ] || [ ! -f "$dry/secret-$set_id/host/.env" ] ||
            [ ! -s "$boot/set-$set_options.out" ]; then
            echo "check: with EC2_OPTIONS=$set_options, a dry run of verify, the environment file that the bootstrap writes or a dry run of the bootstrap is missing" >>"$contract/problems"
            continue
        fi

        # Part 1, the Compose files. The deploy script: the file set of `verify`.
        # The bootstrap: COMPOSE_FILE of its dry run and of the environment file
        # that it writes. The verifier: see part 3.
        grep -qxF -e "dry run:   echo \"== the services of the file set $files\"" "$dry/$set_id-verify.out" ||
            echo "check: with EC2_OPTIONS=$set_options, the file set of verify is not $files" >>"$contract/problems"
        grep -qF -e "COMPOSE_FILE is $files" "$boot/set-$set_options.out" ||
            echo "check: with EC2_OPTIONS=$set_options, COMPOSE_FILE of the dry run of the bootstrap is not $files" >>"$contract/problems"
        grep -qxF -e "COMPOSE_FILE=$files" "$dry/secret-$set_id/host/.env" ||
            echo "check: with EC2_OPTIONS=$set_options, COMPOSE_FILE of the environment file of the host is not $files" >>"$contract/problems"

        # Part 2, the necessary names. An environment file with no value at all,
        # but for the two settings that select the file set: `secret` names each
        # name that it needs, and the test of the bootstrap names each name that
        # it needs. `secret` must need each name of the bootstrap.
        sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*$/\1=/p' .env.example |
            sed "s/^EC2_OPTIONS=\$/EC2_OPTIONS=$set_options/; s/^EC2_DNS_PROVIDER=\$/EC2_DNS_PROVIDER=$provider/" \
                >"$contract/empty-$set_id.env"
        deploy_case "need-$set_id" scripts/aws-host.sh "$dry/bin" "$contract/empty-$set_id.env" --dry-run secret
        sed -n 's/^dry run: a real run stops here: no value for \([A-Za-z0-9_ ]*\).*$/\1/p' "$dry/need-$set_id.out" |
            tr ' ' '\n' | sed '/^$/d' | LC_ALL=C sort -u >"$contract/need-$set_id.secret"
        python3 -I "$dry/boot.py" check .env.example "$top" "$contract/empty-$set_id.env" \
            >"$contract/need-$set_id.out" 2>/dev/null || :
        sed -n 's/^no value for \([A-Za-z0-9_ ]*\)$/\1/p' "$contract/need-$set_id.out" |
            tr ' ' '\n' | sed '/^$/d' >"$contract/need-$set_id.names"
        # The settings of the edge: the bootstrap tests them with the script of
        # the active edge. A name of the text of that script is necessary when a
        # dry run of the bootstrap refuses the fixture with no value for it.
        case $set_options in
            pangolin*) edge_id=pangolin; edge_script=scripts/render-pangolin.sh ;;
            *) edge_id=; edge_script=caddy/start.sh ;;
        esac
        if [ ! -f "$contract/edge-$edge_id.names" ]; then
            : >"$contract/edge-$edge_id.names"
            grep -o 'EC2_[A-Z0-9_]*' "$edge_script" | LC_ALL=C sort -u >"$contract/edge-$edge_id.candidates" || :
            [ -s "$contract/edge-$edge_id.candidates" ] ||
                echo "check: $edge_script names no setting of the environment file" >>"$contract/problems"
            while IFS= read -r name <&4; do
                boot_fixture "edge-$edge_id-$name" "$edge_id" "$edge_id"
                fixture_env=$boot/roots/edge-$edge_id-$name/srv/data/tenant-ec2/.env
                (umask 077 && sed "s/^$name=.*\$/$name=/" "$fixture_env" >"$fixture_env.new")
                mv "$fixture_env.new" "$fixture_env"
                contract_boot "$top" "edge-$edge_id-$name" "$contract/edge-$edge_id-$name.out"
                grep -qF 'passes the test of the settings of the edge' "$contract/edge-$edge_id-$name.out" ||
                    printf '%s\n' "$name" >>"$contract/edge-$edge_id.names"
            done 4<"$contract/edge-$edge_id.candidates"
        fi
        cat "$contract/need-$set_id.names" "$contract/edge-$edge_id.names" | LC_ALL=C sort -u >"$contract/need-$set_id.boot"
        grep -qxF EC2_DOMAIN "$contract/need-$set_id.boot" ||
            echo "check: with EC2_OPTIONS=$set_options, the test of the bootstrap does not name the names that it needs" >>"$contract/problems"
        grep -vxF -f "$contract/need-$set_id.secret" "$contract/need-$set_id.boot" >"$contract/need-$set_id.lack" || :
        [ ! -s "$contract/need-$set_id.lack" ] ||
            echo "check: with EC2_OPTIONS=$set_options, the bootstrap stops with no value for a name, and secret accepts the environment file: $(tr '\n' ' ' <"$contract/need-$set_id.lack" | sed 's/ $//')" >>"$contract/problems"

        # Part 3, the services. The commands of `verify` run in a directory that
        # is in place of the clone of the host, for each state of the lock. The
        # services that run there are the services that the bootstrap starts
        # with that lock. The commands must pass, and the three lists must be
        # equal: the services that the bootstrap starts, the services that
        # `verify` reports as running, and the services that the verifier checks.
        for state in "$tree_state" "$other_state"; do
            case_id=$set_id-$state
            if [ "$state" = "$tree_state" ]; then
                cp "$boot/set-$set_options.out" "$contract/boot-$case_id.out"
            else
                contract_boot "$other" "set-$set_options" "$contract/boot-$case_id.out"
            fi
            boot_started "$contract/boot-$case_id.out" >"$contract/started-$case_id"
            # shellcheck disable=SC2086 # the words of the list
            contract_host "$case_id" "$contract/lock.$state" "$set_id" $set_files
            host=$contract/$case_id
            sed -n 's/^dry run:   //p' "$dry/$set_id-verify.out" >"$host/commands.all"
            [ "$(grep -c "^cd '" "$host/commands.all")" -eq 1 ] ||
                echo "check: the commands of verify do not change to the clone of the host one time" >>"$contract/problems"
            grep -v "^cd '" "$host/commands.all" >"$host/commands.sh" || :
            # The agent is the one service that the lock can turn off.
            grep -vxF -f "$contract/started-$case_id" "$host/state/services" >"$contract/off-$case_id" || :
            case $state in
                none) echo pi-durable >"$contract/expected" ;;
                *) : >"$contract/expected" ;;
            esac
            cmp -s "$contract/expected" "$contract/off-$case_id" ||
                echo "check: with EC2_OPTIONS=$set_options and the agent kit with the pin state $state, the bootstrap does not start each service of the file set but the agent with no pin" >>"$contract/problems"
            cp "$contract/started-$case_id" "$host/state/running"
            contract_verify "$case_id" pass
            [ "$host_rc" -eq 0 ] ||
                echo "check: with EC2_OPTIONS=$set_options and the agent kit with the pin state $state, the commands of verify fail on the services that the bootstrap starts" >>"$contract/problems"
            for text in "file set $files: the host has it" "ok - file set $files: the host has it" \
                'verify-deployed: pass'; do
                grep -qF -e "$text" "$host/pass.out" ||
                    echo "check: with EC2_OPTIONS=$set_options and the agent kit with the pin state $state, the commands of verify print no line with: $text" >>"$contract/problems"
            done
            sed -n 's/^service \([A-Za-z0-9_-]*\): running$/\1/p' "$host/pass.out" | LC_ALL=C sort >"$contract/listed-$case_id"
            cmp -s "$contract/started-$case_id" "$contract/listed-$case_id" ||
                echo "check: with EC2_OPTIONS=$set_options and the agent kit with the pin state $state, the services that verify reports as running are not the services that the bootstrap starts" >>"$contract/problems"
            LC_ALL=C sort -u "$host/state/required" >"$contract/required-$case_id"
            cmp -s "$contract/started-$case_id" "$contract/required-$case_id" ||
                echo "check: with EC2_OPTIONS=$set_options and the agent kit with the pin state $state, the services that the verifier checks are not the services that the bootstrap starts" >>"$contract/problems"
            if [ "$state" = none ]; then
                grep -q '^service pi-durable: skip (' "$host/pass.out" && grep -q '^skip - pi-durable: ' "$host/pass.out" ||
                    echo "check: with EC2_OPTIONS=$set_options and no pin of the agent kit, verify and the verifier do not print a skip line for the agent" >>"$contract/problems"
            fi
            # A service that the bootstrap starts and that does not run is a FAIL,
            # also the agent with a pin. One file set proves it for each state.
            [ "$set_id" = core ] || continue
            for down in litellm pi-durable; do
                grep -qxF -e "$down" "$contract/started-$case_id" || continue
                grep -vxF -e "$down" "$contract/started-$case_id" >"$host/state/running"
                contract_verify "$case_id" "down-$down"
                if [ "$host_rc" -eq 0 ] || ! grep -qxF -e "service $down: FAIL (not running)" "$host/down-$down.out"; then
                    echo "check: with the agent kit with the pin state $state, the commands of verify pass when the service $down does not run" >>"$contract/problems"
                fi
            done
        done
    done 3<"$contract/sets"

    # Each bridge name is necessary with mcp, even when the other name has a value.
    for search_missing in URL TOKEN; do
        sed "s/^EC2_PI_DURABLE_WEB_SEARCH=.*\$/EC2_PI_DURABLE_WEB_SEARCH=mcp/; s/^EC2_PI_DURABLE_WEB_SEARCH_MCP_$search_missing=.*\$/EC2_PI_DURABLE_WEB_SEARCH_MCP_$search_missing=/" \
            "$dry/core.env" >"$contract/search.env"
        name=EC2_PI_DURABLE_WEB_SEARCH_MCP_$search_missing
        if python3 -I "$dry/boot.py" check .env.example "$top" "$contract/search.env" >"$contract/search.out" 2>/dev/null; then
            echo "check: the bootstrap accepts the web search mcp with no value for $name" >>"$contract/problems"
        fi
        grep -qxF "EC2_PI_DURABLE_WEB_SEARCH is mcp, and there is no value for $name" "$contract/search.out" ||
            echo "check: the bootstrap does not require $name with mcp" >>"$contract/problems"
        deploy_case need-search scripts/aws-host.sh "$dry/bin" "$contract/search.env" --dry-run secret
        grep -qxF "dry run: a real run stops here: no value for $name" "$dry/need-search.out" ||
            echo "check: secret does not require $name with mcp" >>"$contract/problems"
    done
    sed 's/^EC2_PI_DURABLE_WEB_SEARCH=.*$/EC2_PI_DURABLE_WEB_SEARCH=mcp/' \
        "$dry/core.env" >"$contract/search.env"
    if ! python3 -I "$dry/boot.py" check .env.example "$top" "$contract/search.env" >"$contract/search.out" 2>/dev/null; then
        echo "check: the bootstrap refuses mcp with both bridge values" >>"$contract/problems"
    fi
    deploy_case search-ready scripts/aws-host.sh "$dry/bin" "$contract/search.env" --dry-run secret
    if [ "$case_rc" -ne 0 ] || grep -q 'a real run stops here:' "$dry/search-ready.out"; then
        echo "check: secret refuses mcp with both bridge values" >>"$contract/problems"
    fi

    # Test unknown values and each literal alias of the validator. This catches an
    # added alias without publishing a site-specific name in a fixture.
    python3 -I - "$dry/boot.py" >"$contract/search-selectors" <<'PY'
import ast
import re
import sys
code = ast.parse(open(sys.argv[1], encoding="utf-8").read())
validator = next(node for node in code.body
                 if isinstance(node, ast.FunctionDef) and node.name == "web_search")
selectors = {node.value for node in ast.walk(validator)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and re.fullmatch(r"[a-z][a-z0-9-]*", node.value)}
print("\n".join(sorted((selectors | {"retired-search"}) - {"off", "mcp"})))
PY
    while IFS= read -r search_selector <&3; do
        boot_fixture search-retired "" ""
        fixture_env=$boot/roots/search-retired/srv/data/tenant-ec2/.env
        sed "s/^EC2_PI_DURABLE_WEB_SEARCH=.*\$/EC2_PI_DURABLE_WEB_SEARCH=$search_selector/" \
            "$fixture_env" >"$fixture_env.new"
        mv "$fixture_env.new" "$fixture_env"
        deploy_case search-retired scripts/aws-host.sh "$dry/bin" "$fixture_env" secret
        if [ "$case_rc" -eq 0 ] || ! grep -qxF 'aws-host: EC2_PI_DURABLE_WEB_SEARCH is not off or mcp' "$dry/search-retired.err"; then
            echo "check: secret accepts an unsupported web search value or gives no selector message" >>"$contract/problems"
        fi
        retired_rc=0
        env -i PATH="$boot/bin:$PATH" HOME="$boot/home" TMPDIR="$boot/tmp" CHECK_BOOT_CALLS="$boot/calls" \
            CHECK_BOOT_GIT="$boot_git" sh scripts/host-bootstrap.sh --dry-run --root "$boot/roots/search-retired" \
            --stack-env "$boot/roots/search-retired/etc/tenant-ec2/stack.env" >"$contract/retired.out" 2>"$contract/retired.err" </dev/null || retired_rc=$?
        if [ "$retired_rc" -eq 0 ] || ! grep -qxF 'host-bootstrap: error: EC2_PI_DURABLE_WEB_SEARCH is not off or mcp' "$contract/retired.err"; then
            echo "check: the bootstrap dry run accepts an unsupported web search value or gives no selector message" >>"$contract/problems"
        fi
    done 3<"$contract/search-selectors"

    [ ! -s "$boot/calls" ] ||
        echo "check: a dry run of the bootstrap calls: $(LC_ALL=C sort -u "$boot/calls" | tr '\n' ';')" >>"$contract/problems"
    [ ! -s "$dry/calls" ] ||
        echo "check: a dry run of secret calls: $(LC_ALL=C sort -u "$dry/calls" | tr '\n' ' ')" >>"$contract/problems"
    # No output of a run holds a fake value of a fixture.
    cat "$dry/fakes" "$boot/fakes" >"$contract/fakes"
    if [ -s "$contract/fakes" ] && find "$contract" "$dry" -name '*.out' -type f -exec cat {} + | grep -qF -f "$contract/fakes"; then
        echo "check: a run of the option contract check prints a fake value of a fixture" >>"$contract/problems"
    fi
    if [ -s "$contract/problems" ]; then
        cat "$contract/problems" >&2
        result "$label" FAIL
    else
        result "$label" ok
    fi
}

# self_test <check> <command>...: one self-test of a file of the tree. The
# environment of the shell does not reach it: in a hook, a variable of Git must
# not turn the copy of the index into the source checkout.
self_test() {
    label=$1
    shift
    run "$label" env -i PATH="$PATH" HOME="${HOME:-/}" "$@"
}

# script_selftest <check> <file> <command>...: the one line of the self-test of
# a script of the tree. Each such self-test needs python3.
script_selftest() {
    label=$1
    f=$2
    shift 2
    if [ ! -f "$f" ]; then
        result "$label" skip "no file yet"
    elif ! command -v python3 >/dev/null 2>&1; then
        no_tool "$label" python3
    else
        self_test "$label" "$@"
    fi
}

# review_selftest: the self-test of the helper of the security review. The
# script of the review always exits with 0, because its result is advice. So
# the test is the line that the self-test prints. It uses fixtures of its own.
# The review itself is never a part of this script or of a hook: it gives
# suggestions only.
review_selftest() {
    env -i PATH="$PATH" HOME="${HOME:-/}" sh scripts/security-review.sh --self-test >"$work/review.out" 2>&1 || :
    if grep -qxF 'security-review self-test: pass' "$work/review.out"; then
        return 0
    fi
    cat "$work/review.out"
    return 1
}

# pi_package: print the directory of the Pi package of this machine, or
# nothing. It is the package in the global directory of npm.
pi_package() {
    command -v npm >/dev/null 2>&1 || return 0
    dir=$(npm root -g 2>/dev/null || :)
    dir=${dir:+$dir/@earendil-works/pi-coding-agent}
    if [ -n "$dir" ] && [ -f "$dir/dist/core/skills.js" ]; then
        printf '%s\n' "$dir"
    fi
}

# loaders_check: the one line of the loader tests of the security review skill:
# Claude Code and Pi load the skill of a new clone. The tests use a stub of the
# model API on this machine and make no model call. Claude Code, Node.js and the
# Pi package are tools of an optional harness: with none of them, the line is a
# skip and not a missing tool.
loaders_check() {
    label="loaders skills/security-review/"
    if [ ! -f skills/security-review/test-loaders.py ]; then
        result "$label" skip "no skill yet"
        return 0
    fi
    for tool in python3 git; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            no_tool "$label" "$tool"
            return 0
        fi
    done
    for tool in node claude; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            result "$label" skip "no command $tool on this machine"
            return 0
        fi
    done
    # A directory that the caller names must be a Pi package: a wrong name is a
    # finding, not a skip.
    package=${CHECK_PI_PACKAGE:-}
    if [ -n "$package" ] && [ ! -f "$package/dist/core/skills.js" ]; then
        echo "check: CHECK_PI_PACKAGE does not name the directory of a Pi package" >&2
        result "$label" FAIL
        return 0
    fi
    [ -n "$package" ] || package=$(pi_package)
    if [ -z "$package" ]; then
        result "$label" skip "no Pi package on this machine; CHECK_PI_PACKAGE names its directory"
        return 0
    fi
    self_test "$label" python3 -I skills/security-review/test-loaders.py "$package"
}

# scenes_check: the one line of the check of the diagrams: Excalidraw loads each
# scene of docs/diagrams/, and each image is the export of its scene. The check
# needs the Python module playwright with its Chromium, and npm for the pinned
# Excalidraw packages. npm reads its registry, so CHECK_OFFLINE=1 gives a skip.
# CHECK_DIAGRAM_NPM_DIR names a directory that holds the pinned packages: then
# the check makes no network call, and it runs also with CHECK_OFFLINE=1.
# Playwright and npm are tools of the diagram build only: with none of them,
# the line is a skip and not a missing tool.
scenes_check() {
    label="scenes docs/diagrams/"
    npm_dir=${CHECK_DIAGRAM_NPM_DIR:-}
    if [ ! -f scripts/build-diagrams.py ]; then
        result "$label" skip "no diagram script yet"
    elif ! command -v python3 >/dev/null 2>&1; then
        no_tool "$label" python3
    elif [ -z "$npm_dir" ] && [ "$offline" -eq 1 ]; then
        result "$label" skip "CHECK_OFFLINE=1"
    elif ! env -i PATH="$PATH" HOME="${HOME:-/}" python3 -I -c '
import os
import sys
from playwright.sync_api import sync_playwright
with sync_playwright() as tool:
    sys.exit(0 if os.path.exists(tool.chromium.executable_path) else 1)' >/dev/null 2>&1; then
        result "$label" skip "no Python module playwright with Chromium on this machine"
    elif [ -z "$npm_dir" ] && ! command -v npm >/dev/null 2>&1; then
        result "$label" skip "no command npm on this machine"
    else
        set -- python3 -I scripts/build-diagrams.py --check
        [ -z "$npm_dir" ] || set -- "$@" --npm-dir "$npm_dir"
        self_test "$label" "$@"
    fi
}

# local_run_check: the one line of the local run of the gate: the services
# `open-knowledge` and `ok-auth` of compose.yaml start with a stub in place of
# Entra ID and a stub upstream (ok-auth/local-run.sh). The run makes a Docker
# network with the fixed range of `edge-ok`, so two runs at the same time on one
# machine do not work. Thus a hook sets CHECK_NO_LOCAL_RUN=1.
local_run_check() {
    label="local run ok-auth/local-run.sh"
    if [ ! -f ok-auth/local-run.sh ]; then
        result "$label" skip "no gate yet"
        return 0
    fi
    if [ "${CHECK_NO_LOCAL_RUN:-0}" = 1 ]; then
        result "$label" skip "CHECK_NO_LOCAL_RUN=1"
        return 0
    fi
    if ! docker_ok || ! docker compose version >/dev/null 2>&1; then
        no_tool "$label" "docker compose"
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        no_tool "$label" python3
        return 0
    fi
    # The run pulls an image that is not there. With CHECK_OFFLINE=1, a missing
    # image is a missing tool.
    if [ "$offline" -eq 1 ]; then
        {
            awk '
            /^[^ #]/ { block = $1; service = "" }
            block == "services:" && /^  [^ #]/ { service = $1 }
            block == "services:" && service == "ok-auth:" && $1 == "image:" { print $2 }' compose.yaml
            sed -n 's/^[ \t]*image:[ \t]*//p' ok-auth/local-run/*.yaml 2>/dev/null
        } | LC_ALL=C sort -u >"$work/images"
        while IFS= read -r image <&3; do
            if ! docker image inspect "$image" >/dev/null 2>&1; then
                no_tool "$label" "the image $image"
                return 0
            fi
        done 3<"$work/images"
    fi
    set -- sh ok-auth/local-run.sh --upstream stub
    set -- PATH="$PATH" HOME="${HOME:-/}" "$@"
    [ -z "${DOCKER_CONFIG:-}" ] || set -- DOCKER_CONFIG="$DOCKER_CONFIG" "$@"
    [ -z "${DOCKER_HOST:-}" ] || set -- DOCKER_HOST="$DOCKER_HOST" "$@"
    run "$label" env -i "$@"
}

# --- the files --------------------------------------------------------------

# The files of the tree, one path in each line: a file name with a line break
# is not supported. The directories below hold no source of this repository.
find . \( -path ./kits -o -path ./.git -o -path ./.local -o -path ./data -o -path ./state \
    -o -path ./secrets \) -prune -o -type f -print >"$work/found" ||
    die "find cannot list the files of the tree"
sed 's|^\./||' "$work/found" | LC_ALL=C sort >"$work/files"

# pick <list> <grep argument>...: write the paths of the tree that match into
# the list. No match is not an error.
pick() {
    list=$1
    shift
    grep "$@" "$work/files" >"$work/$list" || [ $? -eq 1 ] || die "grep cannot read the file list"
}

pick templates -E '\.cfn\.ya?ml$'
pick memory -E '^config/memory/[^/]+\.yaml$'
pick sh -E '\.sh$'
pick hooks -E '^scripts/git-hooks/[^/]+$'
pick compose -E '(^|/)compose[^/]*\.ya?ml$'
pick in_scripts -E '^scripts/'

# Under scripts/, a shell script can have a name without .sh. The first line
# shows it. A file of scripts/git-hooks/ is in the list of the hooks.
while IFS= read -r f <&3; do
    case $f in
        *.sh) continue ;;
    esac
    ! grep -qxF -e "$f" "$work/hooks" || continue
    first=
    IFS= read -r first <"$f" || :
    case $first in
        '#!/bin/sh'|'#!/bin/sh '*|'#!/bin/bash'|'#!/bin/bash '*) printf '%s\n' "$f" >>"$work/sh" ;;
    esac
done 3<"$work/in_scripts"

# --- the templates ----------------------------------------------------------

if [ ! -s "$work/templates" ]; then
    result "cfn-lint *.cfn.yaml" skip "no template yet"
    result "validate-template *.cfn.yaml" skip "no template yet"
else
    select_cfn_lint
    while IFS= read -r f <&3; do
        if [ "$cfn_lint_from" = missing ]; then
            no_tool "cfn-lint $f" cfn-lint
        else
            # --regions takes each word after it, so the template is first.
            run "cfn-lint $f" cfn_lint_cmd "$f" --regions "$region"
        fi
    done 3<"$work/templates"
    while IFS= read -r f <&3; do
        # The size is first: it is a finding with no call to AWS.
        if [ "$(wc -c <"$f")" -gt "$TEMPLATE_BODY_MAX" ]; then
            result "validate-template $f" FAIL \
                "larger than $TEMPLATE_BODY_MAX bytes: the provisioner has no template upload object right, so \`aws cloudformation deploy\` cannot upload it"
        elif [ "$offline" -eq 1 ]; then
            result "validate-template $f" skip "CHECK_OFFLINE=1"
        elif ! has_profile "$profile"; then
            result "validate-template $f" skip "no AWS profile $profile on this machine"
        elif ! command -v aws >/dev/null 2>&1; then
            no_tool "validate-template $f" aws
        else
            run "validate-template $f" validate_template "$f"
        fi
    done 3<"$work/templates"
fi

# --- the memory render ------------------------------------------------------

if [ ! -f scripts/aws-memory.py ]; then
    result "memory render" skip "no scripts/aws-memory.py yet"
elif [ ! -s "$work/memory" ]; then
    result "memory render" skip "no stage files yet"
elif ! command -v python3 >/dev/null 2>&1; then
    no_tool "memory render" python3
else
    while IFS= read -r f <&3; do
        stage=${f##*/}
        stage=${stage%.yaml}
        run "memory render $stage" python3 scripts/aws-memory.py check --stage "$stage"
    done 3<"$work/memory"
fi

# --- the memory regression checks -------------------------------------------

if [ ! -f scripts/test-memory-stack.py ]; then
    result "memory stack" skip "no memory stack tests in this copy"
elif ! command -v python3 >/dev/null 2>&1; then
    no_tool "memory stack" python3
else
    run "memory stack" python3 scripts/test-memory-stack.py
fi

if [ ! -f scripts/test-memory-deploy.py ]; then
    result "memory deploy" skip "no memory deploy tests in this copy"
elif ! command -v python3 >/dev/null 2>&1; then
    no_tool "memory deploy" python3
else
    run "memory deploy" python3 scripts/test-memory-deploy.py
fi

# --- the shell scripts ------------------------------------------------------

# shell_group <check> <dialect or empty> <file>...
shell_group() {
    label=$1
    dialect=$2
    shift 2
    if [ $# -eq 0 ]; then
        result "$label" skip "no file yet"
        return 0
    fi
    label="$label ($# files)"
    if [ "$shellcheck_from" = missing ]; then
        no_tool "$label" shellcheck
        return 0
    fi
    [ -z "$dialect" ] || set -- -s "$dialect" "$@"
    run "$label" shellcheck_cmd "$@"
}

select_shellcheck
LC_ALL=C sort -u "$work/sh" >"$work/scripts"
set --
while IFS= read -r f <&3; do
    if [ "$f" = scripts/host-bootstrap-macos.sh ]; then
        if command -v zsh >/dev/null 2>&1; then
            run "zsh syntax $f" zsh -n "$f"
        else
            no_tool "zsh syntax $f" zsh
        fi
        continue
    fi
    set -- "$@" "$f"
done 3<"$work/scripts"
shell_group "shellcheck shell scripts" "" "$@"

set --
while IFS= read -r f <&3; do
    case $f in
        *.sh) continue ;;
    esac
    set -- "$@" "$f"
done 3<"$work/hooks"
shell_group "shellcheck scripts/git-hooks/*" sh "$@"

# --- the Compose files ------------------------------------------------------

if [ ! -s "$work/compose" ]; then
    result "compose compose*.yaml" skip "no file yet"
else
    # Each file set of the contract is one model. A Compose file that no file
    # set holds is not checked, so it is a finding.
    printf '%s\n' "$FILE_SETS" >"$work/sets"
    while IFS='|' read -r set_options set_files <&3; do
        # shellcheck disable=SC2086 # the words of the list
        compose_set_check "$set_options" $set_files
    done 3<"$work/sets"
    while IFS= read -r f <&3; do
        if [ "$f" = compose.macos.yaml ]; then
            if ! command -v docker >/dev/null 2>&1; then
                no_tool "compose macos standalone" docker
            elif ! command -v python3 >/dev/null 2>&1; then
                no_tool "compose macos standalone" python3
            else
                run "compose macos standalone" python3 scripts/test-macos-compose.py
            fi
            continue
        fi
        grep -qE "(^|[| ])$(printf '%s' "$f" | sed 's/[.]/[.]/g')( |\$)" "$work/sets" ||
            result "compose $f" FAIL "no file set of the contract holds this file"
    done 3<"$work/compose"
    routes_check
    blueprint_check
fi

# --- the core edge ----------------------------------------------------------

if [ ! -f caddy/start.sh ]; then
    result "core edge caddy/" skip "no core edge yet"
elif [ ! -f kits.lock ]; then
    echo "check: the check of the core edge needs kits.lock" >&2
    result "core edge caddy/" FAIL
else
    caddy_check
fi

# --- the deploy script, the template and the DNS providers -------------------

if [ ! -f scripts/aws-host.sh ]; then
    result "dry run scripts/aws-host.sh" skip "no deploy script yet"
elif ! command -v python3 >/dev/null 2>&1; then
    no_tool "dry run scripts/aws-host.sh" python3
elif [ ! -f .env.example ]; then
    echo "check: the dry run of scripts/aws-host.sh needs .env.example" >&2
    result "dry run scripts/aws-host.sh" FAIL
else
    deploy_setup
    if [ -f scripts/aws-host.cfn.yaml ]; then
        render_check scripts/aws-host.cfn.yaml
    else
        result "render scripts/aws-host.cfn.yaml" skip "no template yet"
    fi
    dry_run_check
    secret_check
    if [ -f scripts/dns-records.sh ]; then
        provider_check
    else
        result "providers scripts/dns-records.sh" skip "no script yet"
    fi
fi

# --- the dry run of the bootstrap -------------------------------------------

if [ ! -f scripts/host-bootstrap.sh ]; then
    result "dry run scripts/host-bootstrap.sh" skip "no bootstrap script yet"
elif ! command -v python3 >/dev/null 2>&1; then
    no_tool "dry run scripts/host-bootstrap.sh" python3
elif ! command -v envsubst >/dev/null 2>&1; then
    no_tool "dry run scripts/host-bootstrap.sh" envsubst
elif [ ! -f .env.example ] || [ ! -f kits.lock ] || [ ! -f compose.yaml ]; then
    echo "check: the dry run of scripts/host-bootstrap.sh needs .env.example, kits.lock and compose.yaml" >&2
    result "dry run scripts/host-bootstrap.sh" FAIL
else
    boot_dry_check
fi

# --- the environment forms before a secret write -----------------------------

printf '%s\n' "$FILE_SETS" >"$work/form-sets"
while IFS='|' read -r set_options set_files <&3; do
    label="environment forms $(echo "$set_files" | tr ' ' ':')"
    if ! command -v python3 >/dev/null 2>&1; then
        no_tool "$label" python3
    elif ! command -v envsubst >/dev/null 2>&1; then
        no_tool "$label" envsubst
    elif python3 -I scripts/test-env-forms.py --options "$set_options" >"$work/forms.out" 2>&1; then
        result "$label" ok
    else
        cat "$work/forms.out" >&2
        result "$label" FAIL
    fi
done 3<"$work/form-sets"

# --- the real start of the bootstrap -----------------------------------------

if [ ! -f scripts/host-bootstrap.sh ]; then
    result "start scripts/host-bootstrap.sh" skip "no bootstrap script yet"
else
    start_check
fi

# --- the option contract -----------------------------------------------------

if [ ! -f scripts/aws-host.sh ] || [ ! -f scripts/host-bootstrap.sh ] || [ ! -f scripts/verify-deployed.py ]; then
    result "option contract EC2_OPTIONS" skip "no deploy script, no bootstrap script or no verifier yet"
elif ! command -v python3 >/dev/null 2>&1; then
    no_tool "option contract EC2_OPTIONS" python3
elif ! command -v envsubst >/dev/null 2>&1; then
    no_tool "option contract EC2_OPTIONS" envsubst
elif [ ! -s "$dry/boot.py" ] || [ ! -f "$boot/sets" ]; then
    echo "check: the check of the option contract needs the dry runs of the deploy script and of the bootstrap" >&2
    result "option contract EC2_OPTIONS" FAIL
else
    contract_check
fi

# --- the self-tests ----------------------------------------------------------

script_selftest "selftest scripts/aws-provisioner.sh" scripts/aws-provisioner.sh \
    sh scripts/aws-provisioner.sh selftest
script_selftest "selftest scripts/entra-app.sh" scripts/entra-app.sh \
    sh scripts/entra-app.sh selftest
if [ ! -f scripts/security-review.sh ]; then
    result "selftest scripts/security-review.sh" skip "no file yet"
elif ! command -v python3 >/dev/null 2>&1; then
    no_tool "selftest scripts/security-review.sh" python3
else
    run "selftest scripts/security-review.sh" review_selftest
fi
loaders_check
script_selftest "selftest scripts/build-diagrams.py" scripts/build-diagrams.py \
    python3 -I scripts/build-diagrams.py --self-test
script_selftest "selftest scripts/dns-migration.py" scripts/dns-migration.py \
    python3 -I scripts/dns-migration.py --self-test
script_selftest "selftest scripts/build-architecture-html.py" scripts/build-architecture-html.py \
    python3 -I scripts/build-architecture-html.py --selftest
scenes_check
script_selftest "architecture docs/architecture.html" scripts/build-architecture-html.py \
    python3 -I scripts/build-architecture-html.py --check
local_run_check

# --- operations regression checks -------------------------------------------

if [ -f scripts/test-operations.py ]; then
    run "operations regression" python3 -I scripts/test-operations.py
else
    result "operations regression" skip "no operations tests in this copy"
fi

# --- storage regression checks ----------------------------------------------

if [ -f scripts/test-storage.py ]; then
    run "storage regression" python3 -I scripts/test-storage.py
else
    result "storage regression" skip "no storage tests in this copy"
fi

# --- Mac source checks; no host, VM, image build or cloud request -------------

for test in host infra bootstrap credentials; do
    if [ -f "scripts/test-macos-$test.py" ]; then
        run "macos $test regression" python3 "scripts/test-macos-$test.py"
    else
        result "macos $test regression" skip "no Mac tests in this copy"
    fi
done
if [ -f config/macos/machine.json ]; then
    run "macos candidate schema" python3 -I scripts/check-macos-machine.py
else
    result "macos candidate schema" skip "no Mac candidate in this copy"
fi

# --- verifier regression checks ---------------------------------------------

if [ -f scripts/test-verify-deployed.py ]; then
    run "verifier regression" python3 -I scripts/test-verify-deployed.py
else
    result "verifier regression" skip "no verifier tests in this copy"
fi

# --- development publish regression checks -----------------------------------

if [ -d tests ]; then
    # Hook Git variables must not turn a staged copy into the source checkout.
    run "publish regression" env -i PATH="$PATH" HOME="${HOME:-/}" \
        python3 -I -m unittest discover -s tests -q
else
    result "publish regression" skip "no development publish tests in this copy"
fi

# --- the scan ---------------------------------------------------------------

if [ "${CHECK_NO_SCAN:-0}" = 1 ]; then
    result "scan tree" skip "CHECK_NO_SCAN=1, the caller runs the scan"
elif [ ! -f scripts/scan.sh ]; then
    echo "check: scripts/scan.sh is not in the tree" >&2
    result "scan tree" FAIL
elif sh scripts/scan.sh tree >"$work/out" 2>"$work/err" </dev/null; then
    # The last line of the scan: the counts, the level and the state of the local deny list.
    result "scan tree" ok "$(sed -n 's/^scan: \([0-9].*\)$/\1/p' "$work/out" | tail -n 1)"
else
    cat "$work/out" "$work/err" >&2
    result "scan tree" FAIL
fi

# --- the result -------------------------------------------------------------

status=0
if [ -n "$missing" ]; then
    echo "check: missing tool: ${missing#, } (install it, or see scripts/check.sh --help)"
    status=1
fi
if [ "$n_fail" -gt 0 ] || [ "$status" -ne 0 ]; then
    echo "check: FAIL (${missing:+a tool is missing; }$n_fail FAIL, $n_ok ok, $n_skip skip)"
    exit 1
fi
echo "check: pass ($n_ok ok, $n_skip skip)"
