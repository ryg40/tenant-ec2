#!/bin/sh
# The stack of the host of tenant-ec2: deploy it, read its outputs, write the Secrets
# Manager secret, set the DNS records and run the verify commands on the host.
# The template is scripts/aws-host.cfn.yaml. Each AWS call uses the provisioner profile
# (scripts/aws-provisioner.sh), not an administrator.
# See docs/reference/deploy-script.md.
#
# Usage: scripts/aws-host.sh [--dry-run] [--env-file <path>] <command> [<option>]
#   deploy [--replace-instance] [--allow-remove]
#       Create or update the stack with `aws cloudformation deploy`. Each parameter of the
#       template comes from the environment file. EC2_OPTIONS sets the parameter
#       EnablePangolin: true with the option pangolin, false without it. Before an update,
#       the script makes a change set and shows it. It stops and deletes the change set in
#       two cases. A replacement is the value True or Conditional.
#       - The change set replaces the template resource Instance. --replace-instance
#         accepts this.
#       - The change set removes Instance, DataVolume, FileSystem or ElasticIp, or it
#         replaces DataVolume or FileSystem. --allow-remove accepts this. Each option
#         accepts its own case only.
#   outputs [--write]
#       Print each output of the stack as a NAME=value line. An output is not a secret.
#       With --write, set these names in the environment file: EC2_GERBIL_BASE_ENDPOINT
#       (the Elastic IP), only with the option pangolin; EC2_BUCKET (the bucket name),
#       when the stack has a bucket. The file keeps its mode.
#   secret [--allow-empty <NAME>]...
#       Read the environment file on stdin and write it to the Secrets Manager secret
#       <project>/env as one JSON object with the names as keys:
#         scripts/aws-host.sh secret < .env
#       Empty generated names take their values from <project>/generated when it exists.
#       The command reads the private file on stdin. These names need a non-placeholder value:
#       - each name of Part 1 of .env.example;
#       - each name with an empty value in the block `Option <name>` of .env.example, when
#         EC2_OPTIONS of the input holds <name>;
#       - the Porkbun pair, when EC2_OPTIONS holds pangolin.
#       These names need a value too. The value of .env.example is a correct value:
#       - each name that a Compose file of the file set reads in the form ${NAME:?...};
#       - with the option pangolin, each setting that scripts/render-pangolin.sh needs;
#       - with EC2_PI_DURABLE_WEB_SEARCH=mcp, the two web search names.
#       The bootstrap of the host stops at its step 2 on each of these names with no
#       value. This command also calls host-bootstrap.sh --check-forms before it writes
#       its JSON file. Both commands use the same form tests, without a network call.
#       A wrong nonempty form stops even --dry-run. A message names the setting and rule.
#       A name of an option that is off needs no value. --allow-empty accepts one named
#       empty value of Part 1, of an option block or of the Porkbun pair. The command
#       stops also when a value holds a single quote or ends with a backslash.
#       A name of a block `DNS provider <name>` of .env.example does not go into the
#       secret: a DNS credential stays on the development machine. The one exception is
#       the Porkbun pair with the option pangolin: the Pangolin edge needs it on the host
#       for its certificate. The command prints each name that it leaves out.
#   generated-status
#       Read <project>/generated and print the three names with present or absent.
#       Print no value. An unavailable secret read fails with a name-only warning.
#   dns [--plan]
#       Set the DNS records of the host with scripts/dns-records.sh: one A record to the
#       Elastic IP for each name of EC2_EDGE_NAMES below EC2_DOMAIN, and for the name
#       pangolin with the option pangolin. No wildcard record. EC2_DNS_PROVIDER names the
#       provider: manual (the default), porkbun, route53 or cloudflare. A record that is
#       correct gets no change. With --plan, the command prints the records and changes
#       nothing. See docs/options/dns.md.
#   verify
#       Show the status of the stack. Then run these checks on the host, through one SSM
#       command, and print the output: `docker compose ps`; COMPOSE_FILE of the
#       environment file of the host is the file set of EC2_OPTIONS; each service of that
#       file set is running; scripts/verify-deployed.py with that file set and with
#       EC2_EDGE_NAMES as its inputs. A service of a kit that has no tag or no commit in
#       kits.lock of the host gets a skip line: the bootstrap starts no service of such a
#       kit, and the verifier prints a skip line for it. The verifier accepts this state
#       only for the agent kit. The verifier checks the deployed containers and the
#       internal endpoints of the services of the file set, and the path from the edge to
#       the service of each public name. It uses no kit-local .env, no public route, no
#       image smoke and no paid model call. It reads a bearer value inside an existing
#       container, never in the SSM text.
#   host-allocate | host-release | stop | start
#       Mac only. Default to a no-call preview; --apply permits the reviewed write.
#       Allocation uses its own CloudFormation stack. Stop does not release the host.
#       Release needs releaseHostId in .local/macos/<runtime-stack>.json.
#       Private state also holds reviewed price and pinned AMI evidence.
#   session | tunnel | ssh
#       Mac only. --apply assumes the scoped session role for 900 seconds, then starts
#       the PIN shell, localhost port 4180 forwarding, or SSH transport on port 22.
#       The SSH transport still needs the owner public-key authentication.
#   host-bootstrap [--lockdown]
#       Mac only. --apply replays the first-boot stage of the user data after `secret`.
#       It runs the scope checks of `session`, needs the running instance online in SSM,
#       and refuses an empty or invalid <project>/macos/env (values withheld). It sends
#       scripts/macos-first-boot-replay.sh with AWS-RunShellScript to the one stack
#       instance, waits a bounded time and prints static status lines only.
#       Without --lockdown, the stage stops after the PIN account readback with
#       "lockdown pending"; the default accounts stay unchanged. --lockdown approves the
#       account lockdown and runs the rest of the bootstrap. The host refuses it without
#       the pending marker, and refuses a second lockdown.
#       Not verified: a real replay on a Mac host; the tests use a stub of the AWS CLI.
#   recovery-check
#       Mac only. Print the operator check before host-bootstrap --lockdown: the Run As
#       session and an SSH login of the PIN account with the owner key through SSM. It
#       makes no AWS call and writes nothing.
#   --apply            permit a Mac write; homelab retains its existing behavior
#   --dry-run          print each command and make no AWS, DNS provider or SSM call
#   --env-file <path>  the environment file; the default is .env of this repository
#   --help             print this text
#
# Settings. Each one is a name of the environment file. A value in the file wins. A name
# that the file does not set comes from the shell environment, then from its default.
#   EC2_PROJECT=tenant-ec2         the name prefix of the provisioner policy
#   EC2_AWS_PROFILE=$EC2_PROJECT   the provisioner profile
#   EC2_AWS_REGION=us-east-1
#   EC2_STACK_NAME=$EC2_PROJECT-host
#   EC2_DOMAIN                     the domain of the host: each public name is one label
#                                  below it
#   The parameters of the template: EC2_INSTANCE_TYPE, EC2_AMI_SSM_PARAMETER, EC2_VPC_CIDR,
#   EC2_ADMIN_CIDR, EC2_SSH_PUBLIC_KEY_FILE (a path; the script reads the public key from
#   the file), EC2_DATA_VOLUME_SIZE, EC2_SNAPSHOT_REPOSITORY, EC2_SNAPSHOT_TAG,
#   EC2_SNAPSHOT_COMMIT, EC2_ENABLE_BUCKET, EC2_ENABLE_OPERATIONS, EC2_ALARM_EMAIL,
#   EC2_ALARM_THRESHOLD, EC2_BEDROCK_EMBED_MODEL (the parameter BedrockEmbedModel; the
#   default is amazon.titan-embed-text-v2:0).
#   EC2_SNAPSHOT_REPOSITORY is a URL with no user name and no credential in it: `deploy`
#   prints it and passes it as an argument. The token of a private clone is the key
#   EC2_SNAPSHOT_CLONE_TOKEN of the secret.
#   The option settings. They come from the environment file only, never from the shell
#   environment: the same file is the source of the secret, and the host reads the same
#   names from the secret.
#   EC2_OPTIONS        a list of option names with commas and no blank; the known names are
#                      pangolin and copilot; the default is empty. A name that the script
#                      does not know stops it.
#   EC2_EDGE_NAMES     a list of public names with commas and no blank; the known names are
#                      ok, llm, ov and agent; the default is ok
#   EC2_DNS_PROVIDER   manual (the default), porkbun, route53 or cloudflare. The option
#                      pangolin needs porkbun: its certificate uses the DNS-01 challenge
#                      through Porkbun.
#   This script reads no credential of a DNS provider. scripts/dns-records.sh reads it
#   from the environment file.
#
# The file set. EC2_OPTIONS decides the Compose files of the host and their services.
# `verify` checks the services of this set only. The bootstrap of the host makes the same
# list from EC2_OPTIONS of the secret, and writes it into the name COMPOSE_FILE of the
# environment file of the host (scripts/host-bootstrap.sh). Compose reads the name there.
# `verify` reads the name from that file and compares it: after a change of EC2_OPTIONS,
# the host has the new file set only after `secret` and a new run of the bootstrap.
#   compose.yaml            always: litellm, litellm-db, openviking, open-knowledge,
#                           ok-auth, pi-durable
#   compose.caddy.yaml      without the option pangolin: caddy
#   compose.pangolin.yaml   with the option pangolin: pangolin, gerbil, traefik
#   compose.copilot.yaml    with the option copilot: copilot
#
# The environment file. A line is NAME=value, an empty line or a line that starts with #.
# One pair of quotes around a value is removed. A value with no quotes must not hold a
# blank and then #. A name that is set two times stops the script. A message names a
# line number or a name, never a value.
# A value of the secret must not hold a single quote, and it must not end with a
# backslash: the bootstrap of the host (scripts/host-bootstrap.sh) writes each value
# between single quotes into the environment file of the host, and it refuses such a
# value. So `secret` refuses it here, before the deploy.
#
# A placeholder is a value that stands in for a real one: the value of the same name in
# .env.example, a word such as changeme or test, a value of x or of * only, a value
# between < and >, a value of the form ${NAME}, a value with example.com in it. The
# function `placeholder` of this script holds the complete list, in one place.
#
# Secrets. The script uses no trace (`set -x`) and makes each file with the mode 0600.
# The secret goes to AWS as a file (file://). No argument of a command holds a secret
# value. In a dry run, such a file is shown as a text between < and >.
#
# Needs: a POSIX shell with awk and sed, python3, the AWS CLI version 2. `dns` needs the
# tool of its provider (scripts/dns-records.sh).

# Not verified: no stack exists, so no run proved these facts. The tests of this script
# used a stub of the AWS CLI.
#   - Not verified: a real deploy, a real secret write and a real SSM command.
#   - Not verified: the provisioner policy allows each change set call of an update
#     (list-change-sets, describe-change-set, execute-change-set, delete-change-set).
#   - Not verified: after `aws cloudformation deploy --no-execute-changeset`, the list of
#     the change sets holds one new id. The script finds the change set by this rule.
#   - Not verified: describe-change-set, delete-change-set and execute-change-set accept
#     --stack-name together with the id of the change set. The script sends the two.
#   - Not verified: a run on a host. `verify` expects the snapshot at /opt/<project> on
#     the host (the user data), with the link .env to the environment file. It reads no
#     kit clone and no kit-local .env.
#   - Not verified: the internal checks pass against live containers. A health pass
#     proves no model answer, provider access, public login gate or durable storage write.
#     The host snapshot must include scripts/verify-deployed.py before this command runs.
#   - Not verified: on the host, `docker compose ps --status running --services` prints
#     each running service of the file set of COMPOSE_FILE, one name in each line.
#   - Not verified: on the host, the file .env of the clone is the link to the environment
#     file that the bootstrap writes, and the user of the SSM command reads it.

set -eu
# No trace, also when a caller starts the script with `sh -x`: a trace prints values.
set +x
umask 077

here=$(cd "$(dirname "$0")" && pwd)
top=$(dirname "$here")
template=$here/aws-host.cfn.yaml
sample=$top/.env.example
dns_records=$here/dns-records.sh
# The parameters of the template that `deploy` sets.
parameters='Project InstanceType AmiParameter VpcCidr AdminCidr SshPublicKey DataVolumeSize
SnapshotRepository SnapshotTag SnapshotCommit Domain EnablePangolin BedrockEmbedModel
EnableBucket EnableOperations AlarmEmail AlarmThreshold'
# The names that the script reads from the environment file. Each other name stays in the
# file. The helper reads the three option settings with each of them (the function
# `options` of the helper).
setting_names='EC2_PROJECT EC2_AWS_PROFILE EC2_AWS_REGION EC2_STACK_NAME EC2_DOMAIN
EC2_INSTANCE_TYPE EC2_AMI_SSM_PARAMETER EC2_VPC_CIDR EC2_ADMIN_CIDR EC2_SSH_PUBLIC_KEY_FILE
EC2_DATA_VOLUME_SIZE EC2_SNAPSHOT_REPOSITORY EC2_SNAPSHOT_TAG EC2_SNAPSHOT_COMMIT
EC2_ENABLE_BUCKET EC2_ENABLE_OPERATIONS EC2_ALARM_EMAIL EC2_ALARM_THRESHOLD
EC2_BEDROCK_EMBED_MODEL'
# The services of each Compose file of the file set (the head of this file).
services_core='litellm litellm-db openviking open-knowledge ok-auth pi-durable'
services_caddy='caddy'
services_pangolin='pangolin gerbil traefik'
services_copilot='copilot'
# The template resources of the stop rule of `deploy` (deploy_update): the instance, and
# the resources that hold the data or the address of the host.
guarded='Instance DataVolume FileSystem ElasticIp'
# The seconds that `verify` gives the command on the host.
verify_timeout=900

# A line of a dry run goes to the first stdout, also from inside a command substitution.
exec 3>&1

die() {
    echo "aws-host: $*" >&2
    exit 1
}

# say <text>: one line for a step of a real run. A dry run prints the commands in its place.
say() {
    [ "$dry" = 1 ] || echo "==> $*"
}

usage() {
    sed -n '2,/^$/s/^# \{0,1\}//p' "$0"
}

# The one reader of the environment file, and the writer of each JSON file. It gets a
# secret value on stdin or from a file, never as an argument, and it prints no value of a
# name that the caller does not ask for.
# shellcheck disable=SC2016 # the text is Python: ${ is the text of a placeholder rule
helper='
import json
import os
import re
import shlex
import stat
import sys

LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)")
SECRET_MAX = 65536
MADE = (
    "EC2_OPENVIKING_LITELLM_API_KEY",
    "EC2_PI_DURABLE_LITELLM_API_KEY",
    "EC2_PI_DURABLE_OPENVIKING_API_KEY",
)
# The known names of the three option settings. The order of a tuple is the order of each
# result: the DNS records and the checks of `verify` use it.
OPTIONS = ("pangolin", "copilot")
EDGE_NAMES = ("ok", "llm", "ov", "agent")
PROVIDERS = ("manual", "porkbun", "route53", "cloudflare")
# The option pangolin needs this pair on the host: Traefik makes the certificate with the
# DNS-01 challenge through Porkbun. The two names are one text: the secret scan reads a
# name after a comma as the value of the name before it.
PORKBUN_PAIR = "EC2_PORKBUN_API_KEY EC2_PORKBUN_SECRET_KEY".split()
# A name that a Compose file refuses with no value: the form ${NAME:?text}.
NEEDED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?")
# The settings that scripts/render-pangolin.sh needs. The bootstrap of the host runs that
# script at its step 2, with the option pangolin. The core edge needs no name that
# compose.caddy.yaml does not read in the form ${NAME:?text}.
PANGOLIN_SETTINGS = ("EC2_EDGE_NAMES", "EC2_ACME_EMAIL", "EC2_ADMIN_CIDR", "EC2_PANGOLIN_SITE")
# The names that the agent needs with the web search `mcp`.
WEB_SEARCH_NAMES = (
    "EC2_PI_DURABLE_WEB_SEARCH_MCP_URL",
    "EC2_PI_DURABLE_WEB_SEARCH_MCP_TOKEN",
)


def stop(text):
    sys.stderr.write("aws-host: " + text + "\n")
    sys.exit(1)


def parse(lines, label):
    """The NAME=value lines as a dict in the order of the file. No message holds a value."""
    found = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        match = LINE.fullmatch(line)
        if not match:
            stop("%s: line %d is not a NAME=value line" % (label, number))
        name, value = match.group(1), match.group(2).strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"\x27":
            value = value[1:-1]
        elif re.search(r"\s#", value):
            stop("%s: line %d has a blank and then # in a value with no quotes; "
                 "put the value in quotes" % (label, number))
        if name in found:
            stop("%s: line %d sets %s a second time" % (label, number, name))
        found[name] = value
    return found


def read_file(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()
    except (OSError, UnicodeError) as error:
        stop("cannot read %s: %s" % (path, getattr(error, "strerror", None) or "not UTF-8 text"))


def write_file(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        handle.write(text)


def placeholder(value, sample):
    # The complete list of the placeholders. It is here only: add a new rule to this
    # function, with one line in this comment. Each rule but the first ignores the
    # letter case. A value is a placeholder when it is:
    #   1. the value of the same name in .env.example;
    #   2. one of the words of `words`;
    #   3. a text that starts with one of the texts of `starts`;
    #   4. the letter x only (x, xx, xxx) or the character * only (*, ***);
    #   5. a text between < and >;
    #   6. a text of the form ${NAME}: a reference that no shell replaced;
    #   7. a text that holds one of the names of `parts`.
    words = ("changeme", "change-me", "change_me", "placeholder", "example", "todo", "tbd",
             "fixme", "dummy", "secret", "password", "none", "null", "test")
    starts = ("changeme", "change-me", "change_me", "replaceme", "replace-me", "replace_me",
              "your-", "your_")
    parts = ("example.com", "example.org", "example.net")
    low = value.lower()
    return bool((sample and value == sample)
                or low in words
                or low.startswith(starts)
                or set(low) in ({"x"}, {"*"})
                or (low.startswith("<") and low.endswith(">"))
                or (low.startswith("${") and low.endswith("}"))
                or any(part in low for part in parts))


def unwritable(value):
    # The same test as the function write_env of scripts/host-bootstrap.sh: the
    # environment file of the host cannot hold such a value. A line of the input holds
    # no line break, so the test of the bootstrap for a line break is not here.
    return "\x27" in value or value.endswith("\\")


def listed(found, name, known, default):
    """A list setting as a list in the order of `known`. No message holds a value."""
    text = found.get(name, "") or default
    items = text.split(",") if text else []
    if any(item not in known for item in items) or len(set(items)) != len(items):
        stop("%s is not a list of these names with commas and no blank, each name one time: %s"
             % (name, " ".join(known)))
    return [item for item in known if item in items]


def options(found):
    """The three option settings. Print them as assignments for the shell."""
    active = listed(found, "EC2_OPTIONS", OPTIONS, "")
    edge = listed(found, "EC2_EDGE_NAMES", EDGE_NAMES, "ok")
    provider = found.get("EC2_DNS_PROVIDER", "") or "manual"
    if provider not in PROVIDERS:
        stop("EC2_DNS_PROVIDER is not one of these names: " + " ".join(PROVIDERS))
    if "pangolin" in active and provider != "porkbun":
        stop("the option pangolin needs EC2_DNS_PROVIDER=porkbun: its certificate uses the "
             "DNS-01 challenge through Porkbun")
    print("options=%s" % shlex.quote(" ".join(active)))
    print("edge_names=%s" % shlex.quote(" ".join(edge)))
    print("dns_provider=%s" % shlex.quote(provider))
    return active


def load(path, names, flags):
    found = parse(read_file(path), path)
    if variant(found) == "macos":
        macos_dispatch(flags[0], flags[1], found, *(v == "1" for v in flags[2:7]),
                       lockdown=flags[7] == "1", env_file=path)
        print("macos_done=1")
        return
    if flags[1] in ("host-allocate", "host-release", "stop", "start", "session", "tunnel", "ssh",
                    "host-bootstrap", "recovery-check") or flags[3] == "1":
        stop("this command or --apply needs EC2_VARIANT=macos")
    for name in names.split():
        if name in found:
            print("cfg_%s=%s" % (name, shlex.quote(found[name])))
    options(found)


def blocks(text):
    """Each name of the sample with its place: the part, the kind and the name of its block.

    A marker line starts a part or a block. A block ends at the next marker line or at the
    end of the file. A name outside a block has an empty kind.
    """
    part, kind, block, rows = "", "", "", []
    for line in text:
        mark = re.match(r"# ===== Part ([0-9]+)", line)
        if mark:
            part, kind, block = mark.group(1), "", ""
            continue
        mark = re.match(r"# ----- (Option|DNS provider|Variant) ([a-z0-9]+) -----", line)
        if mark:
            kind, block = mark.group(1), mark.group(2)
            continue
        match = LINE.fullmatch(line.strip())
        if match:
            rows.append((part, kind, block, match.group(1)))
    return rows


def compose_files(options):
    """The Compose files of a list of options, in the order of COMPOSE_FILE."""
    files = ["compose.yaml",
             "compose.pangolin.yaml" if "pangolin" in options else "compose.caddy.yaml"]
    if "copilot" in options:
        files.append("compose.copilot.yaml")
    return files


def compose_names(path):
    """Each name that the Compose file refuses with no value, in the order of the file."""
    names = []
    for line in read_file(path):
        if line.lstrip().startswith("#"):
            continue
        for name in NEEDED.findall(line):
            if name not in names:
                names.append(name)
    if not names:
        stop("%s reads no name in the form ${NAME:?...}" % path)
    return names


def secret(sample_path, out, allow, repo, apply):
    text = read_file(sample_path)
    sample = parse(text, sample_path)
    rows = blocks(text)
    core = [name for part, kind, _, name in rows if part == "1" and not kind]
    if not core:
        stop("%s has no name in Part 1" % sample_path)
    found = parse(sys.stdin, "stdin")
    if variant(found) == "macos":
        macos_dispatch(repo, "secret", found, out == "-", apply=apply == "1", allow=allow)
        print("macos_done=1")
        return
    if apply == "1":
        stop("--apply needs EC2_VARIANT=macos")
    active = options(found)
    pair = PORKBUN_PAIR if "pangolin" in active else []
    # The names that need a value: Part 1, each empty name of the block of an active
    # option, and the Porkbun pair with the option pangolin.
    names = core + [name for _, kind, block, name in rows
                    if kind == "Option" and block in active and not sample.get(name)]
    names += [name for name in pair if name not in names]
    # The settings: the names that the bootstrap of the host needs and that are not in
    # `names`. It stops at its step 2 on each of them with no value. A setting gets no
    # placeholder test: the value of the sample is a correct value.
    settings = []
    for file in compose_files(active):
        settings += compose_names(os.path.join(repo, file))
    if "pangolin" in active:
        settings += PANGOLIN_SETTINGS
    if found.get("EC2_PI_DURABLE_WEB_SEARCH") == "mcp":
        settings += WEB_SEARCH_NAMES
    need = names + [name for name in dict.fromkeys(settings) if name not in names]
    # The names that do not go into the secret: each name of a DNS provider block. The
    # Porkbun pair with the option pangolin is the one exception.
    provider_names = [name for _, kind, _, name in rows if kind == "DNS provider"]
    left_out = [name for name in found if name in provider_names and name not in pair]
    written = {name: value for name, value in found.items() if name not in left_out}
    known = core + [name for _, kind, _, name in rows if kind == "Option"] + PORKBUN_PAIR
    allowed = allow.split()
    for name in allowed:
        if name not in known:
            stop("--allow-empty %s: Part 1 and the option blocks of %s have no such name"
                 % (name, sample_path))
    empty = [n for n in need if not found.get(n) and n not in allowed]
    fake = [n for n in names if found.get(n) and placeholder(found[n], sample.get(n, ""))]
    refused = [n for n in written if unwritable(written[n])]
    body = json.dumps(written, separators=(",", ":"))
    problems = []
    if empty:
        problems.append("no value for " + " ".join(empty))
    if fake:
        problems.append("a placeholder as the value of " + " ".join(fake))
    if refused:
        problems.append("a single quote in the value, or a backslash at its end, of "
                        + " ".join(refused) + " (the environment file of the host cannot hold it)")
    if len(body.encode("utf-8")) > SECRET_MAX:
        problems.append("the secret is larger than %d bytes" % SECRET_MAX)
    if not refused:
        # Validate before making the JSON file or calling AWS. Values travel on stdin.
        import subprocess
        text = "\n".join("%s=\"%s\"" % (name, value) for name, value in written.items()) + "\n"
        result = subprocess.run(
            ["sh", os.path.join(repo, "scripts/host-bootstrap.sh"), "--check-forms", "-"],
            input=text, text=True, capture_output=True,
            env={"PATH": os.environ.get("PATH", os.defpath),
                 "TMPDIR": os.environ.get("TMPDIR", "/tmp")})
        if result.returncode:
            message = result.stderr.strip().removeprefix("host-bootstrap: error: ")
            stop(message or "the environment form test failed")
    # Forms must pass before a read. A dry run makes no AWS call.
    if (out != "-" and not fake and not refused and not any(n not in MADE for n in empty)
            and any(not found.get(n) for n in MADE)):
        import subprocess
        project = found.get("EC2_PROJECT") or os.environ.get("EC2_PROJECT") or "tenant-ec2"
        profile = found.get("EC2_AWS_PROFILE") or os.environ.get("EC2_AWS_PROFILE") or project
        region = found.get("EC2_AWS_REGION") or os.environ.get("EC2_AWS_REGION") or "us-east-1"
        result = subprocess.run(["python3", "-I", os.path.join(repo, "scripts/generated-keys.py"),
            "read", "--project", project, "--region", region, "--profile", profile],
            capture_output=True, text=True)
        if result.returncode:
            stop("cannot read the generated secret; no environment secret is written")
        generated = json.loads(result.stdout)
        for name in MADE:
            if not found.get(name) and generated.get(name):
                found[name] = written[name] = generated[name]
        empty = [n for n in need if not found.get(n) and n not in allowed]
        problems = [p for p in problems if not p.startswith("no value for ")]
        if empty:
            problems.append("no value for " + " ".join(empty))
        body = json.dumps(written, separators=(",", ":"))
        if len(body.encode("utf-8")) > SECRET_MAX:
            problems.append("the secret is larger than %d bytes" % SECRET_MAX)
    if not problems and out != "-":
        import tempfile
        work = tempfile.mkdtemp(prefix="aws-host.")
        out = os.path.join(work, "secret.json")
        write_file(out, body)
        print("work=%s" % shlex.quote(work))
        print("secret_file=%s" % shlex.quote(out))
    for name in ("EC2_PROJECT", "EC2_AWS_PROFILE", "EC2_AWS_REGION"):
        if name in found:
            print("cfg_%s=%s" % (name, shlex.quote(found[name])))
    print("secret_count=%d" % len(written))
    print("secret_empty=%s" % shlex.quote(" ".join(
        n for n in allowed if n in names and not found.get(n))))
    print("secret_left_out=%s" % shlex.quote(" ".join(left_out)))
    print("secret_problems=%s" % shlex.quote("; ".join(problems)))


def ssm(out, timeout):
    commands = sys.stdin.read().splitlines()
    write_file(out, json.dumps({"commands": commands, "executionTimeout": [timeout]}))


def copy_mode(source, target):
    # Give the target the permission bits of the source.
    os.chmod(target, stat.S_IMODE(os.stat(source).st_mode))



def macos_dispatch(*args, **kwargs):
    import contextlib
    # FD 3 is the original stdout, outside the shell assignment capture.
    with os.fdopen(os.dup(3), "w") as public, contextlib.redirect_stdout(public):
        macos(*args, **kwargs)


def variant(found):
    selected = found.get("EC2_VARIANT") or os.environ.get("EC2_VARIANT") or "homelab"
    if selected not in ("homelab", "macos"):
        stop("EC2_VARIANT must be homelab or macos")
    return selected


def macos_verify_commands(project, account):
    # Run Command starts as an administrator. Read a protected machine file before
    # selecting its unprivileged Podman owner; never interpolate its text into a shell.
    program = """import json, os, re, stat, sys

def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate machine field")
        result[key] = value
    return result

try:
    project, expected = sys.argv[1:]
    path = "/etc/" + project + "/machine.json"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("machine ownership or mode")
        text = stream.read(65537)
        if len(text) > 65536:
            raise ValueError("machine too large")
        machine = json.loads(text, object_pairs_hook=unique)
    if (machine.get("schemaVersion") != 1 or machine.get("project") != project or
            machine.get("variant") != "macos" or machine.get("engine") != "podman" or
            machine.get("fileSet") != ["compose.macos.yaml"]):
        raise ValueError("machine selection mismatch")
    account = machine["account"]
    name, pin = account["name"], account["pin"]
    if (not isinstance(name, str) or not re.fullmatch(r"xyz[0-9]{4}", name) or name != expected or
            pin != name.upper() or account["home"] != "/Users/" + name or account["shell"] != "/bin/zsh"):
        raise ValueError("machine account mismatch")
except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
    print("macos verify: machine configuration refused", file=sys.stderr)
    sys.exit(1)
repo = "/opt/" + project + "/macos/snapshot"
os.execvp("sudo", ["sudo", "-u", name, "-H", "python3", "-I", repo + "/scripts/verify-deployed.py",
                    "--repo", repo, "--project", project, "--variant", "macos", "--engine", "podman",
                    "--file-set", "compose.macos.yaml", "--edge-names", ""])
"""
    return ["set +x", "set -eu", "cd /opt/" + project + "/macos/snapshot",
            "python3 -I -c " + shlex.quote(program) + " " + shlex.quote(project) + " " + shlex.quote(account)]


# The first-boot replay: the SSM time limit, and the only remote lines that the wrapper prints.
REPLAY_SECONDS = 10800
REPLAY_STATUS = re.compile(r"macos (?:first boot|bootstrap|user data): [A-Za-z0-9 ;,\x27-]{1,120}")
# The line of the first-boot stage after a first phase that stopped before the account lockdown.
REPLAY_PENDING = "macos first boot: lockdown pending; run recovery-check, then host-bootstrap --apply --lockdown"


def macos_replay_commands(repo, project, phase="first-boot"):
    """The fixed payload: one tracked file after the validated project and phase assignments."""
    with open(os.path.join(repo, "scripts/macos-first-boot-replay.sh"), encoding="ascii") as handle:
        text = handle.read(16385)
    lines = text.splitlines()
    if len(text) > 16384 or not lines or lines[0] != "#!/bin/sh":
        stop("scripts/macos-first-boot-replay.sh is not the reviewed payload form")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,30}", project):
        stop("EC2_PROJECT: invalid form")
    if phase not in ("first-boot", "lockdown"):
        stop("host-bootstrap: invalid phase")
    return ["MACOS_PROJECT=" + project, "MACOS_PHASE=" + phase] + lines


def macos_podman(found):
    """Match the machine schema bounds without an engine or provider call."""
    provider = found.get("EC2_MACOS_PODMAN_PROVIDER") or os.environ.get("EC2_MACOS_PODMAN_PROVIDER") or "applehv"
    if provider != "applehv":
        stop("EC2_MACOS_PODMAN_PROVIDER: only applehv is supported")
    sizes = {"PodmanProvider": provider}
    for field, name, default, low, high in (
            ("PodmanCpus", "EC2_MACOS_PODMAN_CPUS", "4", 1, 8),
            ("PodmanMemoryMiB", "EC2_MACOS_PODMAN_MEMORY_MIB", "8192", 1024, 16384),
            ("PodmanDiskGiB", "EC2_MACOS_PODMAN_DISK_GIB", "80", 20, 1024)):
        value = found.get(name) or os.environ.get(name) or default
        if not re.fullmatch(r"[0-9]+", value) or not low <= int(value) <= high:
            stop(name + ": expected an integer from " + str(low) + " to " + str(high))
        sizes[field] = str(int(value))
    return sizes


def macos(repo, command, found, dry, apply=False, replace=False, remove=False, write=False, allow="",
          lockdown=False, env_file=""):
    """Keep Mac state, private responses and lifecycle guards separate from homelab."""
    import datetime as dt
    import importlib.util
    import ipaddress
    from pathlib import Path
    import subprocess
    import tempfile
    import urllib.parse
    import uuid

    root = Path(repo)
    allocation_parameters = "Project AvailabilityZone InstanceType".split()
    runtime_parameters = ("Project DedicatedHostId AvailabilityZone AmiId VpcCidr RootVolumeSize "
                          "DataVolumeSize SshPublicKey SnapshotRepository SnapshotTag SnapshotCommit "
                          "MemoryStage MemoryArn MemoryBucket AgentActorId ProposalProject DeveloperPin "
                          "PodmanProvider PodmanCpus PodmanMemoryMiB PodmanDiskGiB").split()
    podman_sizes = macos_podman(found)

    def need(ok, message):
        if not ok:
            stop(message)

    def setting(name, default=""):
        return found.get(name) or os.environ.get(name) or default

    def matches(value, pattern, name):
        need(isinstance(value, str) and re.fullmatch(pattern, value), name + ": invalid form")
        return value

    def mapping(rows, key, value):
        need(isinstance(rows, list) and all(isinstance(row, dict) and isinstance(row.get(key), str) and
             isinstance(row.get(value), str) for row in rows), "invalid AWS rows")
        result = {row[key]: row[value] for row in rows}
        need(len(result) == len(rows), "ambiguous AWS rows")
        return result

    def timestamp(value):
        need(isinstance(value, str), "missing reviewed timestamp or allocation time")
        instant = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        need(instant.tzinfo is not None, "timestamp needs a time zone")
        need(instant <= dt.datetime.now(dt.timezone.utc), "timestamp is in the future")
        return instant

    def private(path, optional=False):
        for parent in [path, *path.parents]:
            need(not parent.is_symlink(), "private state cannot use symbolic links")
        if optional and not path.exists():
            return {}
        info = path.stat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077,
             "private state needs an owned regular file with mode 0600")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                need(key not in result, "duplicate private state field")
                result[key] = value
            return result
        data = json.loads(path.read_text(), object_pairs_hook=unique)
        need(isinstance(data, dict), "private state must be an object")
        return data

    def save(path, data):
        for parent in [path, *path.parents]:
            need(not parent.is_symlink(), "private state cannot use symbolic links")
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if path.exists():
            need(path.stat().st_uid == os.geteuid() and stat.S_ISREG(path.stat().st_mode) and
                 not path.stat().st_mode & 0o077, "private state destination permissions are unsafe")
        fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    # Unknown names cannot silently become service settings or credentials.
    sample_values = parse(read_file(str(root / ".env.example")), "sample")
    extra = {"EC2_PROJECT", "EC2_AWS_PROFILE", "COMPOSE_PROJECT_NAME"}
    extra.update("EC2_KIT_" + kind + "_" + kit for kind in ("SOURCE", "TOKEN")
                 for kit in ("LITELLM", "OPEN_KNOWLEDGE", "PI_DURABLE"))
    need(not set(found) - set(sample_values) - extra, "macos: unknown environment setting")
    need(not found.get("EC2_OPTIONS"), "EC2_OPTIONS must be empty for macos")
    need(not found.get("EC2_EDGE_NAMES"), "EC2_EDGE_NAMES must be empty for macos")
    need(found.get("EC2_DNS_PROVIDER", "manual") in ("", "manual"),
         "EC2_DNS_PROVIDER is unsupported for macos")
    excluded = ("EC2_DOMAIN", "EC2_ACME_EMAIL", "EC2_ADMIN_CIDR", "EC2_GERBIL_BASE_ENDPOINT",
                "EC2_PI_DURABLE_OPENVIKING_API_KEY", "EC2_BUCKET")
    for name, value in found.items():
        if name in excluded or name.startswith(("EC2_OPENVIKING_", "EC2_PANGOLIN_", "EC2_COPILOT_", "EC2_NEWT_")):
            need(not value, name + " is unsupported for macos")
    need(command != "dns", "dns is unavailable for macos, including plan and dry-run")
    writes = ("host-allocate", "host-release", "stop", "start", "deploy", "secret", "session", "tunnel", "ssh",
              "host-bootstrap")
    if apply:
        need(command in writes, "--apply is not supported for this command")
    if command in writes and not apply:
        dry = True
    if dry:
        print("dry run: macos " + command + "; no AWS call, provider call, temporary file or mutation")
        plans = {
            "host-allocate": "review <rate> <currency> in <region> at <timestamp>; minimum 24 billable hours; cloudformation create-stack --template-file scripts/aws-macos-allocation.cfn.yaml --stack-name <allocation-stack>; never allocate-hosts",
            "host-release": "confirm <host> in private state; check ownership, age, availability and stopped attachments; cloudformation delete-stack --stack-name <allocation-stack>; verify released",
            "stop": "ec2 stop-instances --instance-ids <runtime-instance>; stopping does not release the host",
            "start": "ec2 start-instances --instance-ids <runtime-instance>; require its allocated available host",
            "deploy": "check stage, boundary, pinned AMI and exact parameters of scripts/aws-host-macos.cfn.yaml; preview change set; separate --replace-instance and --allow-remove gates",
            "outputs": "read runtime outputs; values stay private; --write saves ignored deployment state",
            "secret": "validate selected services; write <project>/macos/env; no DNS, OpenViking, personal IAM or SSH private key",
            "generated-status": "read <project>/macos/generated with --variant macos; print names only",
            "verify": "ssm send-command --document-name AWS-RunShellScript; validate /etc/<project>/machine.json; cd /opt/<project>/macos/snapshot; sudo -u <machine.account.name> -H python3 -I /opt/<project>/macos/snapshot/scripts/verify-deployed.py --repo /opt/<project>/macos/snapshot --project <project> --variant macos --engine podman --file-set compose.macos.yaml --edge-names <empty>",
            "host-bootstrap": "check stage, boundary, allocation, host and the running runtime instance; ssm describe-instance-information; secretsmanager get-secret-value --secret-id <project>/macos/env and refuse an empty or invalid secret, values withheld; ssm send-command --document-name AWS-RunShellScript --instance-ids <InstanceId> with MACOS_PROJECT=<project>, MACOS_PHASE=<phase> and scripts/macos-first-boot-replay.sh; executionTimeout " + str(REPLAY_SECONDS) + " seconds; poll get-command-invocation; print static status lines only",
            "recovery-check": "read the local stage file and private state; print the Run As session check and the owner-key SSH login through SSM; no AWS call; nothing written",
        }
        for kind, document in (("session", "<SessionDocumentName>"), ("tunnel", "AWS-StartPortForwardingSession"),
                               ("ssh", "AWS-StartSSHSession")):
            plans[kind] = "sts assume-role --role-arn <MacosSessionRoleArn> --duration-seconds 900; ssm start-session --target <InstanceId> --document-name " + document
        print("dry run: " + plans[command])
        if command == "secret":
            secret_values(sample_values, found, allow, root, dry=True)
        if command == "host-bootstrap":
            phase = "lockdown" if lockdown else "first-boot"
            payload = macos_replay_commands(repo, "tenant-ec2", phase)
            print("dry run: payload of " + str(len(payload) - 2) + " lines from scripts/macos-first-boot-replay.sh; no secret value in it")
            if lockdown:
                print("dry run: phase lockdown; the host refuses without lockdown pending and refuses a second lockdown; then the rest of the bootstrap")
            else:
                print("dry run: phase first-boot; the stage stops with lockdown pending after the PIN account readback; default accounts unchanged")
        return

    project = matches(setting("EC2_PROJECT", "tenant-ec2"), r"[a-z][a-z0-9-]{2,30}", "EC2_PROJECT")
    profile = matches(setting("EC2_AWS_PROFILE", project), r"[A-Za-z0-9_.-]+", "EC2_AWS_PROFILE")
    need(not profile.endswith("-admin"), "use the provisioner profile, not an administrator")
    region = matches(setting("EC2_AWS_REGION", "us-east-1"), r"[a-z]{2}(?:-[a-z]+)+-\d+", "EC2_AWS_REGION")
    stack = matches(setting("EC2_MACOS_STACK_NAME", project + "-macos-host"),
                    re.escape(project) + r"-macos-[A-Za-z0-9-]+", "EC2_MACOS_STACK_NAME")
    allocation = matches(setting("EC2_MACOS_ALLOCATION_STACK_NAME", project + "-macos-allocation"),
                         re.escape(project) + r"-macos-[A-Za-z0-9-]+", "EC2_MACOS_ALLOCATION_STACK_NAME")
    need(stack != allocation and setting("EC2_STACK_NAME", project + "-host") not in (stack, allocation),
         "Mac and homelab stack names must be distinct")
    path = root / ".local" / "macos" / (stack + ".json")
    state = private(path, optional=True)
    identity = dict(schemaVersion=1, project=project, region=region, stack=stack, allocationStack=allocation)
    if state:
        need(all(state.get(k) == v for k, v in identity.items()), "private deployment state identity mismatch")

    def cost(stream=None):
        print("Cost/state: allocated and stopped hosts keep charging outside pending scrub; the minimum is 24 billable hours.", file=stream, flush=True)
        print("Cost/state: pending scrub pauses host billing; release ends future host charges, not retained storage charges.", file=stream, flush=True)

    def aws(service, operation, *args, absent=False, failed_wait=False):
        result = subprocess.run(["aws", "--profile", profile, "--region", region, "--no-cli-pager",
                                 "--output", "json", service, operation, *args],
                                capture_output=True, text=True, timeout=1800,
                                env=dict(os.environ, AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off"))
        if result.returncode:
            if failed_wait:
                return None
            if absent and "(ValidationError)" in result.stderr and ("Stack with id " + args[1] + " does not exist") in result.stderr:
                return None
            stop("macos AWS operation failed: " + service + " " + operation + "; inspect private state; charges may remain")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def get_stack(name, optional=False):
        data = aws("cloudformation", "describe-stacks", "--stack-name", name, absent=optional)
        if data is None:
            return None
        rows = data["Stacks"]
        need(len(rows) == 1 and rows[0]["StackName"] == name, "ambiguous or wrong stack identity")
        item = rows[0]
        matches(item["StackId"], r"arn:[a-z-]+:cloudformation:" + re.escape(region) +
                r":\d{12}:stack/" + re.escape(name) + r"/[A-Za-z0-9-]+", "stack identity")
        return item

    def params(item):
        return mapping(item.get("Parameters", []), "ParameterKey", "ParameterValue")

    def outputs(item):
        return mapping(item.get("Outputs", []), "OutputKey", "OutputValue")

    def stable(item):
        allowed = ("CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE")
        if command == "host-release" and item["StackName"] == allocation:
            allowed += ("DELETE_FAILED", "CREATE_FAILED", "ROLLBACK_FAILED", "DELETE_IN_PROGRESS")
        need(item["StackStatus"] in allowed, "stack is not stable; reconcile it without another allocation")

    def template(name, names):
        text = (root / "scripts" / name).read_text()
        section = text.split("\nParameters:\n", 1)[1].split("\nResources:", 1)[0]
        section = re.split(r"\n[A-Za-z][A-Za-z0-9]*:", section)[0]
        actual = re.findall(r"^  ([A-Za-z][A-Za-z0-9]*):", section, flags=re.M)
        need(sorted(actual) == sorted(names), "template parameter list mismatch")
        return str(root / "scripts" / name)

    def selected():
        stage = matches(setting("EC2_MACOS_MEMORY_STAGE"), r"dev|test|prod", "EC2_MACOS_MEMORY_STAGE")
        spec = importlib.util.spec_from_file_location("memory_renderer", root / "scripts/aws-memory.py")
        memory = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(memory)
        config = memory.load_config(stage, root)
        person = memory.active_person(config, setting("EC2_MACOS_PERSON"))
        pin = matches(setting("EC2_MACOS_DEVELOPER_PIN"), r"XYZ[0-9]{4}", "EC2_MACOS_DEVELOPER_PIN")
        need(pin == person["pin"], "EC2_MACOS_DEVELOPER_PIN must match the selected active person")
        need(config["region"] == region, "stage region mismatch")
        roles = [r for r in config.get("hostRoles", []) if r["roleSuffix"] == "macos-role"]
        need(len(roles) == 1, "stage needs exactly one macos-role admission")
        role = roles[0]
        need(setting("EC2_MACOS_AGENT") == role["actorId"] and
             setting("EC2_MACOS_PROPOSAL_PROJECT") == role["proposalProject"], "stage agent or proposal project mismatch")
        need(person["actorId"] != role["actorId"], "person and agent must be distinct")
        selection = dict(personActor=person["actorId"], personPin=person["pin"], localAccount=person["pin"].lower(), stage=stage)
        if state.get("personActor"):
            need(all(state.get(k) == v for k, v in selection.items()),
                 "person selection changed; an existing interactive account cannot be renamed or added")
        saved = private(root / ".local" / "memory" / (stage + ".json"))
        arn = saved["MemoryArn"]
        matches(arn, r"arn:[a-z-]+:bedrock-agentcore:" + re.escape(region) + r":\d{12}:memory/" +
                re.escape(project.replace("-", "_") + "_memory_" + stage) + r"-[A-Za-z0-9]+", "stage MemoryArn")
        need(saved["MemoryId"] == arn.rsplit("/", 1)[1], "stage memory identifier mismatch")
        need(saved.get("BucketName", "") == project + "-memory-" + stage, "stage bucket mismatch")
        for name, key in (("EC2_MACOS_MEMORY_ID", "MemoryId"), ("EC2_MACOS_MEMORY_ARN", "MemoryArn"),
                          ("EC2_MACOS_MEMORY_BUCKET", "BucketName")):
            need(not setting(name) or setting(name) == saved[key], name + ": private output mismatch")
        # Exercise the M2 service configuration contract, not the person profile renderer.
        client = memory.host_client_config(config, "macos-role", saved["MemoryId"])
        need(client["profile"] == "ec2-instance" and client["actorId"] == role["actorId"], "host client configuration mismatch")
        state.update(selection)
        return stage, role, saved

    def scope_check(stage, role, saved):
        live = get_stack("bootstrap-" + project + "-memory-" + stage)
        stable(live)
        current = outputs(live)
        need(all(current.get(k) == saved[k] for k in ("MemoryId", "MemoryArn", "BucketName")),
             "saved stage outputs differ from deployed stage")
        boundary = get_stack("bootstrap-" + project)
        stable(boundary)
        expected = {"EnableMacos": "true", "MacosMemoryArn": saved["MemoryArn"],
                    "MacosMemoryBucketName": saved["BucketName"], "MacosAgentActorId": role["actorId"],
                    "MacosProposalProject": role["proposalProject"]}
        need(all(params(boundary).get(k) == v for k, v in expected.items()), "approved Mac boundary scope mismatch")
        need(boundary["StackId"].split(":")[4] == saved["MemoryArn"].split(":")[4], "boundary account mismatch")

    az = setting("EC2_MACOS_AVAILABILITY_ZONE")

    def location():
        matches(az, re.escape(region) + r"[a-z]", "EC2_MACOS_AVAILABILITY_ZONE")
        need(not state.get("availabilityZone") or state["availabilityZone"] == az, "private host AZ mismatch")

    def allocation_stack(optional=False):
        item = get_stack(allocation, optional)
        if item is None:
            return None
        stable(item)
        need(params(item) == {"Project": project, "AvailabilityZone": az, "InstanceType": "mac2-m2.metal"},
             "allocation parameter mismatch; host replacement is forbidden")
        tags = mapping(item.get("Tags", []), "Key", "Value")
        need(tags.get("Project") == project and tags.get("Variant") == "macos", "allocation ownership mismatch")
        out = outputs(item)
        resources = aws("cloudformation", "list-stack-resources", "--stack-name", item["StackId"])["StackResourceSummaries"]
        hosts = [r for r in resources if r["ResourceType"] == "AWS::EC2::Host"]
        if not out and command == "host-release":
            need(len(hosts) == 1, "failed allocation has no unique owned host")
            out = {"HostId": hosts[0]["PhysicalResourceId"], "AvailabilityZone": az}
        need(out["AvailabilityZone"] == az, "allocation output AZ mismatch")
        matches(out["HostId"], r"h-[a-f0-9]{8,17}", "HostId")
        need(not state.get("hostId") or state["hostId"] == out["HostId"], "wrong host; explicit replacement recovery required")
        need(not state.get("allocationStackId") or state["allocationStackId"] == item["StackId"], "allocation stack identity changed")
        need(len(hosts) == 1 and hosts[0]["PhysicalResourceId"] == out["HostId"], "allocation owns no unique matching host")
        return item

    def host(host_id, allocation_id=None, states=("available",)):
        rows = aws("ec2", "describe-hosts", "--host-ids", host_id)["Hosts"]
        need(len(rows) == 1 and rows[0]["HostId"] == host_id, "missing or ambiguous host")
        row = rows[0]
        need(row["AvailabilityZone"] == az and row["HostProperties"]["InstanceType"] == "mac2-m2.metal", "wrong host type or AZ")
        tags = mapping(row.get("Tags", []), "Key", "Value")
        need(tags.get("Project") == project and tags.get("Variant") == "macos" and
             tags.get("Purpose") == "macos-allocation", "host ownership mismatch")
        if allocation_id:
            need(tags.get("aws:cloudformation:stack-id") == allocation_id, "host CloudFormation ownership mismatch")
        need(row["State"] in states, "host unavailable, released or pending scrub")
        timestamp(row.get("AllocationTime"))
        return row

    def runtime():
        item = get_stack(stack)
        stable(item)
        out = outputs(item)
        matches(out["InstanceId"], r"i-[a-f0-9]{8,17}", "InstanceId")
        need(out["HostId"] == state["hostId"], "runtime host mismatch")
        need(params(item).get("DedicatedHostId") == state["hostId"] and
             params(item).get("AvailabilityZone") == az and params(item).get("DeveloperPin") == state["personPin"],
             "runtime host, AZ or interactive account mismatch")
        need(not state.get("instanceId") or state["instanceId"] == out["InstanceId"], "runtime instance identity changed")
        return item

    def attachments(host_row, instance_id=None):
        rows = aws("ec2", "describe-instances", "--filters", "Name=host-id,Values=" + host_row["HostId"])["Reservations"]
        instances = [i for r in rows for i in r["Instances"]]
        need(len({i["InstanceId"] for i in instances}) == len(instances), "ambiguous attached instances")
        for i in instances:
            need(i["InstanceId"] == instance_id, "foreign or unrecorded host attachment")
            need(i["Placement"]["HostId"] == host_row["HostId"] and i["Placement"]["AvailabilityZone"] == az and
                 i["Placement"]["Tenancy"] == "host", "instance placement mismatch")
            tags = mapping(i.get("Tags", []), "Key", "Value")
            need(tags.get("aws:cloudformation:stack-name") == stack, "instance ownership mismatch")
        listed = {i["InstanceId"] for i in host_row.get("Instances", [])}
        need(listed <= {i["InstanceId"] for i in instances}, "host attachments cannot be reconciled")
        return instances

    if command == "generated-status":
        result = subprocess.run(["python3", "-I", str(root / "scripts/generated-keys.py"), "status", "--variant", "macos",
                                 "--project", project, "--region", region, "--profile", profile],
                                capture_output=True, text=True)
        for line in result.stdout.splitlines():
            need(re.fullmatch(r"EC2_PI_DURABLE_LITELLM_API_KEY: (present|absent)|generated: secret absent", line),
                 "unexpected generated status output")
            print(line)
        need(result.returncode == 0, "Mac generated secret status unavailable or incomplete")
        return

    if command == "outputs":
        item = get_stack(stack)
        stable(item)
        out = outputs(item)
        print("macos outputs: " + " ".join(sorted(k for k in out if k in
              ("InstanceId", "HostId", "DataVolumeId", "HostRoleArn", "SessionDocumentName", "DeveloperAccount"))) + "; values withheld")
        if write:
            need(state.get("hostId") == out["HostId"], "save allocation state before runtime outputs")
            need(params(item).get("DeveloperPin") == state.get("personPin"), "runtime person mismatch")
            state["outputs"] = out
            state["instanceId"] = out["InstanceId"]
            save(path, state)
        return

    if command in ("session", "tunnel", "ssh"):
        stage, role, saved = selected()
        scope_check(stage, role, saved)
        need(state.get("hostId"), "session requires private allocation state")
        location()
        allocation_item = allocation_stack()
        row = host(state["hostId"], allocation_item["StackId"])
        item = runtime()
        out = outputs(item)
        instances = attachments(row, out["InstanceId"])
        need(len(instances) == 1 and instances[0]["State"]["Name"] == "running", "session needs the running runtime instance")
        account = matches(out.get("DeveloperAccount", ""), r"xyz[0-9]{4}", "DeveloperAccount")
        need(account == state.get("localAccount"), "developer account output mismatch")
        boundary = get_stack("bootstrap-" + project)
        stable(boundary)
        arn = outputs(boundary).get("MacosSessionRoleArn", "")
        matches(arn, r"arn:[a-z-]+:iam::" + re.escape(item["StackId"].split(":")[4]) +
                r":role/" + re.escape(project) + r"-macos-session", "MacosSessionRoleArn")
        if command == "session":
            document = matches(out.get("SessionDocumentName", ""), re.escape(project) + r"-macos-session", "SessionDocumentName")
            parameters = {}
        elif command == "tunnel":
            document = "AWS-StartPortForwardingSession"
            parameters = dict(portNumber=["4180"], localPortNumber=["4180"])
        else:
            document = "AWS-StartSSHSession"
            parameters = dict(portNumber=["22"])
        # An SSH client can use `ssh --apply` as its ProxyCommand: stdout carries the SSH stream only.
        cost(sys.stderr if command == "ssh" else None)
        response = aws("sts", "assume-role", "--role-arn", arn, "--role-session-name", "macos-operator", "--duration-seconds", "900")
        credentials = response["Credentials"]
        need(all(isinstance(credentials.get(k), str) and credentials[k] for k in
                 ("AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration")), "invalid temporary session credentials")
        expiry = dt.datetime.fromisoformat(credentials["Expiration"].replace("Z", "+00:00"))
        need(expiry.tzinfo is not None and expiry > dt.datetime.now(dt.timezone.utc), "expired session credentials")
        environment = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
        environment.update(AWS_ACCESS_KEY_ID=credentials["AccessKeyId"], AWS_SECRET_ACCESS_KEY=credentials["SecretAccessKey"],
                           AWS_SESSION_TOKEN=credentials["SessionToken"], AWS_CONFIG_FILE=os.devnull,
                           AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_EC2_METADATA_DISABLED="true",
                           AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off")
        args = ["aws", "--region", region, "--no-cli-pager", "ssm", "start-session", "--target", out["InstanceId"],
                "--document-name", document]
        if parameters:
            args += ["--parameters", json.dumps(parameters)]
        result = subprocess.run(args, env=environment, stdout=sys.stdout, stderr=subprocess.DEVNULL)
        need(result.returncode == 0, "Mac session failed; provider error withheld")
        return

    if command == "recovery-check":
        # Print the operator check of the lockdown gate. It uses the scoped session commands of
        # this script. Local reads only: no AWS call, no file write, no secret value.
        selected()
        need(state.get("hostId") and state.get("instanceId"),
             "recovery-check requires private runtime state; run outputs --write first")
        account = matches(state.get("localAccount", ""), r"xyz[0-9]{4}", "localAccount")
        wrapper = "sh " + shlex.quote(str(root / "scripts/aws-host.sh")) + " --env-file " + shlex.quote(env_file)
        public_key = Path(setting("EC2_SSH_PUBLIC_KEY_FILE")).expanduser().absolute()
        private_key = Path(str(public_key)[:-4]) if str(public_key).endswith(".pub") else None
        identity = shlex.quote(str(private_key)) if private_key and private_key.is_file() else "<the private key of EC2_SSH_PUBLIC_KEY_FILE>"
        known = shlex.quote(str(root / ".local" / "macos" / (stack + ".known_hosts")))
        ssh = ("ssh -F none -i " + identity + " -o IdentitiesOnly=yes -o PreferredAuthentications=publickey"
               " -o HostKeyAlias=" + stack + " -o UserKnownHostsFile=" + known + " -o StrictHostKeyChecking=accept-new"
               " -o " + shlex.quote("ProxyCommand=" + wrapper + " ssh --apply") + " " + account + "@" + stack + " id -un")
        for line in (
                "macos recovery-check: no AWS call and no file write; run each step yourself before the lockdown",
                "macos recovery-check: the first phase ran through Run Command; that is the administrative recovery path",
                "step 1, the Run As check: " + wrapper + " session --apply",
                "  in the session, run: id -un; print -r -- $ZSH_NAME; then exit",
                "  pass: the two lines are " + account + " and zsh; any other user or an ended session is a failure",
                "step 2, the SSH login of " + account + " with the owner key through SSM:",
                "  " + ssh,
                "  pass: the output is " + account + "; a password prompt or another line is a failure",
                "  the client stores the host key in " + known + "; it is no secret",
                "step 3, only after both steps pass: " + wrapper + " host-bootstrap --apply --lockdown",
                "on a failure: do not run --lockdown; follow docs/variants.md#recovery-before-the-lockdown"):
            print(line)
        return

    if command == "host-bootstrap":
        import time
        stage, role, saved = selected()
        scope_check(stage, role, saved)
        need(state.get("hostId"), "host-bootstrap requires private allocation state")
        location()
        allocation_item = allocation_stack()
        row = host(state["hostId"], allocation_item["StackId"])
        item = runtime()
        out = outputs(item)
        instances = attachments(row, out["InstanceId"])
        need(len(instances) == 1 and instances[0]["State"]["Name"] == "running", "host-bootstrap needs the running runtime instance")
        account = matches(out.get("DeveloperAccount", ""), r"xyz[0-9]{4}", "DeveloperAccount")
        need(account == state.get("localAccount"), "developer account output mismatch")
        managed = aws("ssm", "describe-instance-information", "--filters", "Key=InstanceIds,Values=" + out["InstanceId"])
        rows = managed.get("InstanceInformationList", [])
        need(len(rows) == 1 and rows[0].get("InstanceId") == out["InstanceId"] and rows[0].get("PingStatus") == "Online",
             "the runtime instance is not online in Systems Manager; wait, then repeat host-bootstrap")
        # Refuse before the replay with the same service forms as the bootstrap. Values stay in memory.
        spec = importlib.util.spec_from_file_location("macos_forms", root / "config/macos/bootstrap-forms.py")
        forms = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(forms)
        reply = aws("secretsmanager", "get-secret-value", "--secret-id", project + "/macos/env")
        try:
            body = forms.service_settings(reply.get("SecretString") or "")
            need(body, "environment secret is empty; write it with secret --apply, then repeat host-bootstrap")
            services = json.loads((root / "config/macos/machine.json").read_text())["services"]
            forms.service_forms(body, dict(services=services, project=project, region=region))
        except ValueError as error:
            code = str(error) if isinstance(error, forms.Invalid) else "service-syntax"
            stop("environment secret refused: " + code + "; values withheld; write it with secret --apply")
        del reply, body
        commands = macos_replay_commands(repo, project, "lockdown" if lockdown else "first-boot")
        cost()
        sent = aws("ssm", "send-command", "--instance-ids", out["InstanceId"], "--document-name", "AWS-RunShellScript",
                   "--comment", project + (" macos lockdown" if lockdown else " macos first-boot replay"),
                   "--parameters", json.dumps({"commands": commands, "executionTimeout": [str(REPLAY_SECONDS)]}))
        command_id = matches(sent["Command"]["CommandId"], r"[a-f0-9-]{36}", "command identity")
        print("macos host-bootstrap: replay sent; the wait ends after at most " + str(REPLAY_SECONDS // 60 + 10) + " minutes", flush=True)
        deadline, polls, unread = time.monotonic() + REPLAY_SECONDS + 600, 0, 0
        while True:
            invocation = aws("ssm", "get-command-invocation", "--command-id", command_id, "--instance-id", out["InstanceId"],
                             failed_wait=True)
            polls += 1
            unread = unread + 1 if invocation is None else 0
            need(unread <= 30, "replay status unreadable; inspect the command before a new replay")
            if invocation is not None and invocation.get("Status") not in ("Pending", "InProgress", "Delayed", "Cancelling"):
                break
            need(time.monotonic() < deadline, "replay wait ended; the command can still run; inspect it before a new replay")
            time.sleep(2 if polls < 4 else 20)
        shown = withheld = 0
        for key in ("StandardOutputContent", "StandardErrorContent"):
            text = invocation.get(key)
            for line in (text if isinstance(text, str) else "").splitlines():
                if shown < 60 and REPLAY_STATUS.fullmatch(line):
                    print(line)
                    shown += 1
                elif line.strip():
                    withheld += 1
        if withheld:
            print("macos host-bootstrap: " + str(withheld) + " other remote lines withheld")
        status = invocation.get("Status")
        status = status if isinstance(status, str) and re.fullmatch(r"[A-Za-z]{1,32}", status) else "unknown"
        need(status == "Success", "replay ended with status " + status + "; read the status lines above and the stop table of docs/variants.md")
        text = invocation.get("StandardOutputContent")
        if REPLAY_PENDING in (text if isinstance(text, str) else "").splitlines():
            need(not lockdown, "the lockdown phase reported lockdown pending; inspect the host before a new replay")
            print("macos host-bootstrap: first phase complete; lockdown pending; the default accounts are unchanged")
            print("macos host-bootstrap: next: recovery-check, then host-bootstrap --apply --lockdown")
            return
        if lockdown:
            print("macos host-bootstrap: pass; lockdown applied; the first-boot stage is complete")
        else:
            print("macos host-bootstrap: pass; the first-boot stage is complete")
        return

    if command == "verify":
        verifier = root / "scripts/verify-deployed.py"
        need(verifier.is_file() and all(flag in verifier.read_text() for flag in ("--variant", "--engine", "--file-set", "--project")),
             "verify-deployed.py has no macos variant yet")
        need(state.get("hostId"), "verify requires private allocation state")
        item = runtime()
        out = outputs(item)
        account = matches(out.get("DeveloperAccount", ""), r"xyz[0-9]{4}", "DeveloperAccount")
        need(account == state.get("localAccount"), "developer account output mismatch")
        # Run Command is administrative recovery, not an interactive Run As session.
        # The stack output is a cross-check; machine.json supplies the actual account.
        commands = macos_verify_commands(project, account)
        cost()
        sent = aws("ssm", "send-command", "--instance-ids", out["InstanceId"], "--document-name", "AWS-RunShellScript",
                   "--parameters", json.dumps({"commands": commands, "executionTimeout": ["900"]}))
        command_id = matches(sent["Command"]["CommandId"], r"[a-f0-9-]{36}", "command identity")
        aws("ssm", "wait", "command-executed", "--command-id", command_id, "--instance-id", out["InstanceId"])
        reply = aws("ssm", "get-command-invocation", "--command-id", command_id, "--instance-id", out["InstanceId"])
        need(reply["Status"] == "Success", "Mac verification failed; remote output withheld")
        print("macos verify: pass; remote output withheld")
        return

    stage, role, saved = selected()
    def existing_person():
        item = get_stack(stack, optional=True)
        if item:
            stable(item)
            need(params(item).get("DeveloperPin") == state["personPin"],
                 "person selection changed on deployed runtime")
        return item

    if command == "secret":
        generated_name = "EC2_PI_DURABLE_LITELLM_API_KEY"
        # Validate all non-generated values before a read. The generated value comes
        # through a private pipe only when the selected runtime needs the agent.
        machine = root / "config/macos/machine.json"
        agent = machine.is_file() and "pi-durable" in json.loads(machine.read_text()).get("services", [])
        if agent and not found.get(generated_name) and generated_name not in allow.split():
            secret_values(sample_values, found, allow + " " + generated_name, root)
            result = subprocess.run(["python3", "-I", str(root / "scripts/generated-keys.py"), "read",
                                     "--variant", "macos", "--project", project, "--region", region, "--profile", profile],
                                    capture_output=True, text=True, timeout=90)
            need(result.returncode == 0, "Mac generated secret read failed")
            generated = json.loads(result.stdout)
            need(set(generated) == {generated_name} and isinstance(generated[generated_name], str), "invalid generated key response")
            found[generated_name] = generated[generated_name]
        body = secret_values(sample_values, found, allow, root)
        scope_check(stage, role, saved)
        existing_person()
        cost()
        secret_id = project + "/macos/env"
        result = subprocess.run(["aws", "--profile", profile, "--region", region, "--no-cli-pager", "--output", "json", "secretsmanager",
                                 "describe-secret", "--secret-id", secret_id], capture_output=True, text=True,
                                env=dict(os.environ, AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off"))
        need(result.returncode == 0 or "(ResourceNotFoundException)" in result.stderr, "Mac secret lookup failed")
        with tempfile.TemporaryDirectory(prefix="macos-secret-") as temp:
            target = Path(temp) / "secret.json"
            target.write_text(json.dumps(body))
            target.chmod(0o600)
            if result.returncode:
                aws("secretsmanager", "create-secret", "--name", secret_id, "--secret-string", "file://" + str(target))
            else:
                aws("secretsmanager", "put-secret-value", "--secret-id", secret_id, "--secret-string", "file://" + str(target))
        print("macos secret: written; excluded settings and values withheld")
        return

    location()
    if command == "host-allocate":
        source = template("aws-macos-allocation.cfn.yaml", allocation_parameters)
        price = {"hourlyRate": setting("EC2_MACOS_HOST_HOURLY_USD"), "currency": setting("EC2_MACOS_PRICE_CURRENCY", "USD"),
                 "region": setting("EC2_MACOS_PRICE_REGION"), "source": setting("EC2_MACOS_PRICE_SOURCE"), "checkedAt": setting("EC2_MACOS_PRICE_CHECKED_AT")}
        need(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", price["hourlyRate"]) and 0 < float(price["hourlyRate"]) < 1000,
             "reviewed hourly rate required")
        need(price["currency"] == "USD" and price["region"] == region and
             price["source"] == "https://aws.amazon.com/ec2/dedicated-hosts/pricing/",
             "reviewed USD official price source required")
        timestamp(price["checkedAt"])
        scope_check(stage, role, saved)
        item = allocation_stack(optional=True)
        inventory = aws("ec2", "describe-hosts", "--filter", "Name=tag:Project,Values=" + project,
                        "Name=tag:Variant,Values=macos")["Hosts"]
        allocated = [h for h in inventory if h["State"] not in ("released", "released-permanent-failure")]
        if item:
            existing_person()
            need(len(allocated) == 1 and allocated[0]["HostId"] == outputs(item)["HostId"], "duplicate allocation")
        else:
            need(not allocated and not state.get("hostId"), "duplicate or historical allocation; explicit recovery required")
            need(get_stack(stack, optional=True) is None, "runtime already exists; explicit replacement recovery required")
            cost()
            print("Reviewed price: " + price["hourlyRate"] + " USD per hour; minimum " +
                  format(float(price["hourlyRate"]) * 24, ".3f") + " USD before other resources.", flush=True)
            aws("cloudformation", "create-stack", "--stack-name", allocation, "--template-body", "file://" + source,
                "--disable-rollback", "--tags", "Key=Project,Value=" + project, "Key=Variant,Value=macos",
                "--parameters", "ParameterKey=Project,ParameterValue=" + project,
                "ParameterKey=AvailabilityZone,ParameterValue=" + az, "ParameterKey=InstanceType,ParameterValue=mac2-m2.metal")
            waited = aws("cloudformation", "wait", "stack-create-complete", "--stack-name", allocation, failed_wait=True)
            if waited is None:
                failed = get_stack(allocation)
                resources = aws("cloudformation", "list-stack-resources", "--stack-name", failed["StackId"])["StackResourceSummaries"]
                hosts = [r for r in resources if r["ResourceType"] == "AWS::EC2::Host" and r.get("PhysicalResourceId")]
                need(len(hosts) <= 1, "failed allocation has ambiguous hosts; administrator reconciliation required")
                state.update(identity, availabilityZone=az, instanceType="mac2-m2.metal", price=price,
                             allocationStackId=failed["StackId"], status="allocation-incomplete")
                if hosts:
                    state["hostId"] = matches(hosts[0]["PhysicalResourceId"], r"h-[a-f0-9]{8,17}", "failed allocation HostId")
                save(path, state)
                stop("allocation did not complete; owned host reference saved privately when present; review host-release after eligibility; charges may remain")
            item = allocation_stack()
        row = host(outputs(item)["HostId"], item["StackId"])
        attachments(row, state.get("instanceId"))
        state.update(identity, availabilityZone=az, instanceType="mac2-m2.metal", hostId=row["HostId"],
                     allocationStackId=item["StackId"], allocatedAt=row["AllocationTime"], status="allocated", price=price)
        save(path, state)
        print("macos allocation: owned host recorded privately; no second host is allocated")
        return

    need(state.get("hostId") and state.get("allocationStackId"), "missing private allocation state")
    if command == "host-release":
        need(state.get("releaseHostId") == state["hostId"], "confirm the exact host with releaseHostId in private state")
        item = allocation_stack(optional=True)
        if item is None:
            row = host(state["hostId"], state["allocationStackId"], states=("released", "released-permanent-failure"))
            state["status"] = "released"
            save(path, state)
            print("macos release: already released; retained storage can still charge")
            return
        row = host(state["hostId"], item["StackId"])
        need((dt.datetime.now(dt.timezone.utc) - timestamp(row.get("AllocationTime"))).total_seconds() >= 86400,
             "allocation is younger than the 24-hour minimum")
        if state.get("instanceId"):
            runtime()
        for i in attachments(row, state.get("instanceId")):
            need(i["State"]["Name"] in ("stopped", "terminated"), "release requires stopped or terminated attachments")
        scope_check(stage, role, saved)
        cost()
        if item["StackStatus"] != "DELETE_IN_PROGRESS":
            aws("cloudformation", "delete-stack", "--stack-name", item["StackId"])
        state["status"] = "release-requested"
        save(path, state)
        aws("cloudformation", "wait", "stack-delete-complete", "--stack-name", item["StackId"])
        host(state["hostId"], item["StackId"], states=("released", "released-permanent-failure"))
        state["status"] = "released"
        save(path, state)
        print("macos release: released; retained storage can still charge")
        return

    need(state.get("status") not in ("released", "release-requested"), "released or releasing host cannot be reused")
    item = allocation_stack()
    row = host(state["hostId"], item["StackId"], states=("available", "pending") if command == "stop" else ("available",))
    if command in ("stop", "start"):
        current = runtime()
        instance_id = outputs(current)["InstanceId"]
        instances = attachments(row, instance_id)
        need(len(instances) == 1, "runtime instance is missing")
        status = instances[0]["State"]["Name"]
        cost()
        if command == "stop" and status in ("stopped", "stopping"):
            print("macos stop: already stopped or stopping; the host is not released")
            return
        if command == "start" and status in ("running", "pending"):
            print("macos start: already running or starting")
            return
        need(status == ("running" if command == "stop" else "stopped"), "instance state does not permit this transition")
        scope_check(stage, role, saved)
        aws("ec2", command + "-instances", "--instance-ids", instance_id)
        print("macos " + command + ": request accepted; completion is not verified")
        return

    need(command == "deploy", "unknown macos command")
    source = template("aws-host-macos.cfn.yaml", runtime_parameters)
    template_text = Path(source).read_text()
    for logical in ("Instance", "DataVolume"):
        need(re.search(r"^  " + logical + r":\s*$", template_text, re.M), "runtime template lacks a guarded resource")
    scope_check(stage, role, saved)
    ami = state.get("ami", {})
    image = matches(ami.get("imageId", ""), r"ami-[a-f0-9]{8,17}", "private pinned AMI")
    parameter = setting("EC2_MACOS_AMI_SSM_PARAMETER")
    matches(parameter, r"/aws/service/ec2-macos/[a-z0-9-]+/arm64_mac/latest/image_id", "EC2_MACOS_AMI_SSM_PARAMETER")
    need(ami.get("parameter") == parameter and ami.get("region") == region and ami.get("architecture") == "arm64_mac",
         "reviewed AMI metadata mismatch; no automatic AMI lookup")
    timestamp(ami.get("reviewedAt"))
    root_size = setting("EC2_MACOS_ROOT_VOLUME_SIZE", "100")
    data_size = setting("EC2_MACOS_DATA_VOLUME_SIZE", "150")
    need(root_size.isdigit() and data_size.isdigit() and 1 <= int(data_size) <= 16384 and
         isinstance(ami.get("rootVolumeMinGiB"), int) and 1 <= ami["rootVolumeMinGiB"] <= int(root_size) <= 16384,
         "volume size or reviewed AMI minimum mismatch")
    cidr = ipaddress.ip_network(setting("EC2_VPC_CIDR"), strict=True)
    need(cidr.version == 4 and 16 <= cidr.prefixlen <= 26, "EC2_VPC_CIDR: expected an IPv4 range /16 to /26")
    url = urllib.parse.urlsplit(setting("EC2_SNAPSHOT_REPOSITORY"))
    need(url.scheme == "https" and url.hostname and not url.username and not url.password and not url.query and not url.fragment,
         "snapshot needs a credential-free HTTPS URL")
    tag = matches(setting("EC2_SNAPSHOT_TAG"), r"[A-Za-z0-9][A-Za-z0-9._-]*", "EC2_SNAPSHOT_TAG")
    commit = matches(setting("EC2_SNAPSHOT_COMMIT"), r"[a-f0-9]{40}", "EC2_SNAPSHOT_COMMIT")
    key = Path(setting("EC2_SSH_PUBLIC_KEY_FILE")).expanduser().read_text().strip()
    matches(key, r"ssh-(?:ed25519|rsa) [A-Za-z0-9+/]+={0,3}(?: [^\r\n]+)?", "SSH public key")
    import base64
    import struct
    words = key.split()
    blob = base64.b64decode(words[1], validate=True)
    size = struct.unpack(">I", blob[:4])[0]
    need(blob[4:4 + size].decode("ascii") == words[0] and len(blob) >= size + 12, "invalid SSH public key encoding")
    values = dict(Project=project, DedicatedHostId=state["hostId"], AvailabilityZone=az, AmiId=image,
                  VpcCidr=str(cidr), RootVolumeSize=root_size, DataVolumeSize=data_size, SshPublicKey=key,
                  SnapshotRepository=url.geturl(), SnapshotTag=tag, SnapshotCommit=commit, MemoryStage=stage,
                  MemoryArn=saved["MemoryArn"], MemoryBucket=saved["BucketName"], AgentActorId=role["actorId"],
                  ProposalProject=role["proposalProject"], DeveloperPin=state["personPin"], **podman_sizes)
    current = get_stack(stack, optional=True)
    if current:
        stable(current)
        need(params(current).get("DeveloperPin") == state["personPin"], "person selection changed on deployed runtime")
        attachments(row, outputs(current)["InstanceId"])
    else:
        attachments(row)
    cost()
    change = "macos-" + uuid.uuid4().hex
    aws("cloudformation", "create-change-set", "--stack-name", stack, "--change-set-name", change,
        "--change-set-type", "UPDATE" if current else "CREATE", "--template-body", "file://" + source,
        "--capabilities", "CAPABILITY_NAMED_IAM", "--tags", "Key=Project,Value=" + project, "Key=Variant,Value=macos",
        "--parameters", json.dumps([dict(ParameterKey=k, ParameterValue=v) for k, v in values.items()]))
    aws("cloudformation", "wait", "change-set-create-complete", "--stack-name", stack, "--change-set-name", change, failed_wait=True)
    review = aws("cloudformation", "describe-change-set", "--stack-name", stack, "--change-set-name", change)
    if review["Status"] == "FAILED" and any(text in review.get("StatusReason", "") for text in
            ("didn\x27t contain changes", "No updates are to be performed")):
        aws("cloudformation", "delete-change-set", "--stack-name", stack, "--change-set-name", change)
        print("macos deploy: no changes")
        return
    need(review["Status"] == "CREATE_COMPLETE" and review["ExecutionStatus"] == "AVAILABLE", "change set is not executable")
    refused = []
    for entry in review["Changes"]:
        r = entry["ResourceChange"]
        action, logical, kind = r["Action"], r["LogicalResourceId"], r["ResourceType"]
        replacement = r.get("Replacement") in ("True", "Conditional")
        if kind == "AWS::EC2::Host":
            refused.append("runtime cannot allocate or replace a Dedicated Host")
        if logical == "Instance" and replacement and not replace:
            refused.append("instance replacement needs --replace-instance")
        if ((action == "Remove" and (logical == "Instance" or kind in ("AWS::EC2::Volume", "AWS::SecretsManager::Secret"))) or
                (replacement and (kind in ("AWS::EC2::Volume", "AWS::SecretsManager::Secret")))) and not remove:
            refused.append("data removal or replacement needs --allow-remove")
        need(action in ("Add", "Modify", "Remove", "Import", "Dynamic") and
             re.fullmatch(r"AWS::[A-Za-z0-9]+::[A-Za-z0-9]+", kind), "invalid change summary")
        print("change: " + action + " " + kind + "; replacement=" + str(bool(replacement)))
    if refused:
        aws("cloudformation", "delete-change-set", "--stack-name", stack, "--change-set-name", change)
        stop("; ".join(sorted(set(refused))))
    aws("cloudformation", "execute-change-set", "--stack-name", stack, "--change-set-name", change)
    aws("cloudformation", "wait", "stack-update-complete" if current else "stack-create-complete", "--stack-name", stack)
    out = outputs(get_stack(stack))
    need(out["HostId"] == state["hostId"], "deployed host output mismatch")
    state.update(instanceId=out["InstanceId"], outputs=out)
    save(path, state)
    print("macos deploy: complete; outputs recorded privately; runtime qualification remains separate")


def secret_values(sample, found, allow, root, dry=False):
    """Allow only the selected service configuration into the Mac environment secret."""
    service_names = ("COMPOSE_PROJECT_NAME COMPOSE_FILE EC2_VARIANT EC2_PROJECT EC2_AWS_REGION EC2_TZ "
                     "EC2_LITELLM_MASTER_KEY EC2_LITELLM_SALT_KEY EC2_LITELLM_POSTGRES_PASSWORD "
                     "EC2_LITELLM_UI_PASSWORD EC2_LITELLM_UI_USERNAME EC2_BEDROCK_LUNA_MODEL EC2_BEDROCK_EMBED_MODEL "
                     "EC2_OK_ENTRA_TENANT_ID EC2_OK_ENTRA_GROUP_ID EC2_OK_OIDC_CLIENT_ID EC2_OK_OIDC_CLIENT_SECRET "
                     "EC2_OK_COOKIE_SECRET EC2_SNAPSHOT_CLONE_TOKEN "
                     "EC2_PI_DURABLE_API_KEY EC2_PI_DURABLE_LITELLM_API_KEY EC2_PI_DURABLE_MODEL "
                     "EC2_PI_DURABLE_THINKING EC2_PI_DURABLE_WEB_SEARCH").split()
    service_names += ["EC2_KIT_" + kind + "_" + kit for kind in ("SOURCE", "TOKEN")
                      for kit in ("LITELLM", "OPEN_KNOWLEDGE", "PI_DURABLE")]
    # The current upstream kit has no AgentCore adapter. Only an explicit runtime
    # machine service selection can require agent credentials after that pin moves.
    machine = root / "config/macos/machine.json"
    agent = machine.is_file() and "pi-durable" in json.loads(machine.read_text()).get("services", [])
    if not agent:
        service_names = [n for n in service_names if not n.startswith("EC2_PI_DURABLE_") and
                         n not in ("EC2_KIT_SOURCE_PI_DURABLE", "EC2_KIT_TOKEN_PI_DURABLE")]
    body = {name: value for name, value in found.items() if name in service_names}
    body["EC2_VARIANT"] = "macos"
    body["COMPOSE_FILE"] = "compose.macos.yaml"
    for name, value in body.items():
        if unwritable(value):
            stop(name + ": a single quote or final backslash is not allowed")
    for name, valid in (("EC2_PI_DURABLE_THINKING", "xhigh"), ("EC2_PI_DURABLE_MODEL", "bedrock/luna"),
                        ("EC2_PI_DURABLE_WEB_SEARCH", "off")):
        if found.get(name, valid) != valid:
            stop(name + ": unsupported Mac setting")
    core = [n for part, kind, _, n in blocks(read_file(str(root / ".env.example"))) if part == "1" and not kind and n in service_names]
    allowed = allow.split()
    if any(n not in core for n in allowed):
        stop("--allow-empty names an unselected service setting")
    missing = [n for n in core if not body.get(n) and n not in allowed]
    if missing:
        stop("no value for " + " ".join(missing))
    if any(placeholder(body[n], sample.get(n, "")) for n in core if body.get(n)):
        stop("macos secret: placeholder in selected service setting")
    # The bootstrap validates host account and disk inputs later. They do not exist
    # while preparing this service-only secret, so validate its forms here.
    required = ("EC2_AWS_REGION", "EC2_TZ", "EC2_LITELLM_UI_USERNAME", "EC2_BEDROCK_LUNA_MODEL", "EC2_BEDROCK_EMBED_MODEL")
    for name in required:
        if not body.get(name):
            stop(name + ": selected service needs a value")
    for name in ("EC2_OK_ENTRA_TENANT_ID", "EC2_OK_ENTRA_GROUP_ID", "EC2_OK_OIDC_CLIENT_ID"):
        if body.get(name) and not re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", body[name]):
            stop(name + ": expected a UUID")
    for name in ("EC2_MACOS_PERSON", "EC2_MACOS_AGENT", "EC2_MACOS_PROPOSAL_PROJECT"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", found.get(name, "")):
            stop(name + ": expected a stage identity")
    if found.get("EC2_MACOS_MEMORY_STAGE") not in ("dev", "test", "prod"):
        stop("EC2_MACOS_MEMORY_STAGE: expected dev, test or prod")
    if not re.fullmatch(r"XYZ[0-9]{4}", found.get("EC2_MACOS_DEVELOPER_PIN", "")):
        stop("EC2_MACOS_DEVELOPER_PIN: expected an uppercase stage PIN")
    lock = found.get("EC2_MACOS_RUNTIME_LOCK", "")
    if not re.fullmatch(r"config/macos/[A-Za-z0-9._-]+", lock):
        stop("EC2_MACOS_RUNTIME_LOCK: expected a tracked Mac runtime lock")
    if not dry and not (root / lock).is_file():
        stop("EC2_MACOS_RUNTIME_LOCK: selected lock is missing")
    if len(json.dumps(body).encode()) > SECRET_MAX:
        stop("macos secret exceeds size limit")
    return body

mode, args = sys.argv[1], sys.argv[2:]
if mode == "load":
    try:
        load(args[0], args[1], args[2:])
    except Exception:
        stop("Mac input or response is invalid; values withheld")
elif mode == "secret":
    try:
        secret(args[0], args[1], args[2], args[3], args[4])
    except Exception:
        stop("Mac input or response is invalid; values withheld")
elif mode == "ssm":
    ssm(args[0], args[1])
elif mode == "mode":
    copy_mode(args[0], args[1])
else:
    stop("unknown helper mode")
'

helper() {
    python3 -I -c "$helper" "$@"
}

# show <command> [<argument>...]: print one command of a dry run.
show() {
    shown='dry run:'
    for word in "$@"; do
        case $word in
            '' | *[!A-Za-z0-9_@%+=:,./-]*)
                word=\'$(printf '%s' "$word" | sed "s/'/'\\\\''/g")\'
                ;;
        esac
        shown="$shown $word"
    done
    printf '%s\n' "$shown" >&3
}

# note <text>: one line of a dry run that is not a command.
note() {
    printf 'dry run: %s\n' "$*" >&3
}

# run <command> [<argument>...]: start the command. In a dry run, print it and start nothing.
run() {
    if [ "$dry" = 1 ]; then
        show "$@"
    else
        "$@"
    fi
}

as_provisioner() {
    run aws --profile "$profile" --region "$region" "$@"
}

# capture <dry-run value> <command> [<argument>...]: set `captured` to the output of the
# command. In a dry run, the command is printed and `captured` gets the dry-run value.
capture() {
    captured=$1
    shift
    if [ "$dry" = 1 ]; then
        "$@"
    else
        captured=$("$@") || return 1
    fi
}

# setting <NAME> <default>: set `value`. The environment file is first, then the shell
# environment, then the default.
setting() {
    eval "value=\${cfg_$1:-}"
    [ -n "$value" ] || eval "value=\${$1:-}"
    [ -n "$value" ] || value=$2
}

# required <NAME>: set `value` to a setting with no default. A real run stops later when
# the setting has no value (stop_on_missing). A dry run shows <NAME> in its place.
required() {
    setting "$1" ''
    if [ -z "$value" ]; then
        missing="$missing $1"
        value="<$1>"
    fi
}

stop_on_missing() {
    [ -n "$missing" ] || return 0
    if [ "$dry" = 1 ]; then
        note "a real run stops here: $env_file gives no value for$missing"
        missing=
    else
        die "$env_file gives no value for$missing"
    fi
}

# stack_status: set `status` to the status of the stack, or to nothing when no stack has the name.
stack_status() {
    if [ "$dry" = 1 ]; then
        as_provisioner cloudformation describe-stacks --stack-name "$stack" \
            --query 'Stacks[0].StackStatus' --output text
        status='<the status of the stack>'
    elif status=$(as_provisioner cloudformation describe-stacks --stack-name "$stack" \
        --query 'Stacks[0].StackStatus' --output text 2>&1); then
        :
    else
        case $status in
            *"does not exist"*) status= ;;
            *) die "describe-stacks failed: $status" ;;
        esac
    fi
}

# stack_outputs: set `outputs` to one line for each output of the stack: the key, a tab, the value.
stack_outputs() {
    capture '' as_provisioner cloudformation describe-stacks --stack-name "$stack" \
        --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output text ||
        die "the stack $stack gives no outputs"
    outputs=$captured
}

# output <key> <dry-run value>: set `value` to one output of the stack, or to nothing.
output() {
    if [ "$dry" = 1 ]; then
        value=$2
    else
        value=$(printf '%s\n' "$outputs" | awk -F '\t' -v key="$1" '$1 == key { print $2 }')
    fi
}

print_outputs() {
    printf '%s\n' "$outputs" | awk -F '\t' 'NF == 2 { print $1 "=" $2 }'
}

# template_parameters: print the parameter names of the template, one in each line.
template_parameters() {
    awk '
    /^Parameters:/ { on = 1; next }
    /^[A-Za-z]/ { on = 0 }
    on && /^  [A-Za-z][A-Za-z0-9]*:/ { sub(/:.*$/, ""); sub(/^  /, ""); print }' "$template"
}

# deploy_command [<option>]: the one `aws cloudformation deploy` call of this script.
deploy_command() {
    as_provisioner cloudformation deploy \
        --template-file "$template" \
        --stack-name "$stack" \
        --capabilities CAPABILITY_NAMED_IAM \
        --no-fail-on-empty-changeset \
        --tags "Project=$project" \
        "$@" \
        --parameter-overrides \
        "Project=$project" \
        "InstanceType=$p_instance_type" \
        "AmiParameter=$p_ami" \
        "VpcCidr=$p_vpc_cidr" \
        "AdminCidr=$p_admin_cidr" \
        "SshPublicKey=$p_ssh_key" \
        "DataVolumeSize=$p_volume_size" \
        "SnapshotRepository=$p_repository" \
        "SnapshotTag=$p_tag" \
        "SnapshotCommit=$p_commit" \
        "Domain=$p_domain" \
        "EnablePangolin=$p_pangolin" \
        "BedrockEmbedModel=$p_embed" \
        "EnableBucket=$p_bucket" \
        "EnableOperations=$p_operations" \
        "AlarmEmail=$p_alarm_email" \
        "AlarmThreshold=$p_alarm_threshold"
}

# change_sets: print the id of each change set of the stack.
change_sets() {
    as_provisioner cloudformation list-change-sets --stack-name "$stack" \
        --query 'Summaries[].ChangeSetId' --output text
}

# deploy_update: update a stack that exists. The change set is made first and shown.
deploy_update() {
    capture '' change_sets
    before=$captured
    say "making a change set for the stack $stack"
    # The CLI prints the ARN of the change set. The ARN holds the account id: no print.
    deploy_command --no-execute-changeset >/dev/null
    capture '<the new change set>' change_sets
    change=$captured
    if [ "$dry" != 1 ]; then
        # The new change set is the one id that the list did not hold before.
        change=
        count=0
        for id in $captured; do
            new=1
            for old in $before; do
                [ "$id" != "$old" ] || new=0
            done
            if [ "$new" = 1 ] && [ "$id" != None ]; then
                change=$id
                count=$((count + 1))
            fi
        done
        case $count in
            0) echo "deploy: the stack $stack has no change"; return 0 ;;
            1) ;;
            *) die "the stack $stack has $count new change sets; delete them and run deploy again" ;;
        esac
    fi
    capture CREATE_COMPLETE as_provisioner cloudformation describe-change-set \
        --change-set-name "$change" --stack-name "$stack" --query Status --output text
    change_status=$captured
    if [ "$change_status" != CREATE_COMPLETE ]; then
        reason=$(as_provisioner cloudformation describe-change-set --change-set-name "$change" \
            --stack-name "$stack" --query StatusReason --output text)
        as_provisioner cloudformation delete-change-set --change-set-name "$change" \
            --stack-name "$stack"
        case $reason in
            *"didn't contain changes"* | *"No updates are to be performed"*)
                echo "deploy: the stack $stack has no change"
                return 0
                ;;
        esac
        die "the change set has the status $change_status: $reason"
    fi
    capture '' as_provisioner cloudformation describe-change-set \
        --change-set-name "$change" --stack-name "$stack" \
        --query 'Changes[].ResourceChange.[Action,LogicalResourceId,ResourceType,Replacement]' \
        --output text
    changes=$captured
    if [ "$dry" = 1 ]; then
        note "show the change set; stop and delete it when it replaces the template resource Instance and --replace-instance is not given, or when it removes Instance, DataVolume, FileSystem or ElasticIp or replaces DataVolume or FileSystem and --allow-remove is not given"
    else
        say "the change set of the stack $stack"
        printf 'Action\tTemplate resource\tType\tReplacement\n%s\n' "$changes"
        # The stop rule. A line of `changes` is: the action, the template resource, the
        # type, the replacement. A replacement is the value True or Conditional. The names
        # are the names of `guarded`; `deploy` checks that the template has each of them.
        #   replaced  the change set replaces Instance: the root volume is lost.
        #   removed   the change set removes Instance, DataVolume, FileSystem or ElasticIp,
        #             or it replaces DataVolume or FileSystem: the stack loses the data or
        #             the address, and a new volume or file system is empty.
        replaced=$(printf '%s\n' "$changes" | awk -F '\t' '
            $1 != "Remove" && $2 == "Instance" && ($4 == "True" || $4 == "Conditional") { print $4 }')
        removed=$(printf '%s\n' "$changes" | awk -F '\t' '
            $1 == "Remove" && ($2 == "Instance" || $2 == "DataVolume" || $2 == "FileSystem" || $2 == "ElasticIp") {
                printf "%sRemove %s", sep, $2; sep = ", "; next
            }
            ($2 == "DataVolume" || $2 == "FileSystem") && ($4 == "True" || $4 == "Conditional") {
                printf "%sReplacement %s of %s", sep, $4, $2; sep = ", "
            }')
        refused=
        options=
        if [ -n "$replaced" ] && [ "$replace" != 1 ]; then
            refused="it replaces the instance (Replacement $replaced)"
            options=' --replace-instance'
        fi
        if [ -n "$removed" ] && [ "$allow_remove" != 1 ]; then
            refused="${refused:+$refused; }it removes or replaces a resource that holds the data or the address ($removed)"
            options="$options --allow-remove"
        fi
        if [ -n "$refused" ]; then
            as_provisioner cloudformation delete-change-set --change-set-name "$change" \
                --stack-name "$stack"
            die "the change set is refused: $refused. Nothing is changed and the change set is deleted. To accept it: scripts/aws-host.sh deploy$options"
        fi
        [ -z "$replaced" ] || echo "Warning: this update replaces the instance (Replacement $replaced)."
        [ -z "$removed" ] || echo "Warning: this update removes or replaces a resource that holds the data or the address: $removed."
    fi
    say "running the change set"
    as_provisioner cloudformation execute-change-set --change-set-name "$change" \
        --stack-name "$stack"
    as_provisioner cloudformation wait stack-update-complete --stack-name "$stack" ||
        die "the update of the stack $stack failed; read the events: aws cloudformation describe-stack-events --stack-name $stack"
}

# make_work: one private directory for the files of this run. The exit removes it.
make_work() {
    work=$(mktemp -d "${TMPDIR:-/tmp}/aws-host.XXXXXX")
    trap 'rm -rf "$work"' EXIT
    # In sh, a signal handler does not end the script; exit runs the EXIT trap. PIPE is in
    # the list: without it, a reader that closes the pipe of stdout ends the script with
    # no EXIT trap, and the files stay. QUIT is in the list for the same reason.
    trap 'exit 130' INT TERM HUP PIPE QUIT
}

secret_create() {
    as_provisioner secretsmanager create-secret --name "$secret_id" \
        --description "The environment of the host of $project. scripts/aws-host.sh writes it." \
        --secret-string "file://$secret_file" \
        --tags "Key=Project,Value=$project" \
        --query Name --output text
}

secret_put() {
    as_provisioner secretsmanager put-secret-value --secret-id "$secret_id" \
        --secret-string "file://$secret_file" \
        --query Name --output text
}

# has_option <name>: EC2_OPTIONS holds the name.
has_option() {
    case " $options " in
        *" $1 "*) return 0 ;;
    esac
    return 1
}

# file_set: set `compose_files` to the Compose files of EC2_OPTIONS, in the form of
# COMPOSE_FILE, and `services` to the services of these files.
file_set() {
    compose_files=compose.yaml
    services=$services_core
    if has_option pangolin; then
        compose_files=$compose_files:compose.pangolin.yaml
        services="$services $services_pangolin"
    else
        compose_files=$compose_files:compose.caddy.yaml
        services="$services $services_caddy"
    fi
    if has_option copilot; then
        compose_files=$compose_files:compose.copilot.yaml
        services="$services $services_copilot"
    fi
}

# host_commands: print the shell commands that `verify` runs on the host. They come from
# the option settings of this checkout, and they hold no secret: only a safe path, the
# names of the Compose files and the names of the services of this script enter the SSM
# text. Do not expand a bearer into these strings. The commands print no error text of
# Compose: such a text can hold a line of the environment file.
host_commands() {
    case $project in
        '' | *[!A-Za-z0-9_-]*) die "EC2_PROJECT is not a safe host directory name" ;;
    esac
    file_set
    printf "set +x\nrc=0\ncd '%s' || exit 1\n" "$host_repo"
    printf 'echo "== docker compose ps"\ndocker compose ps 2>/dev/null || { echo "docker compose ps: FAIL"; rc=1; }\n'
    # The file set of the host: the bootstrap writes COMPOSE_FILE into the environment file,
    # and .env of the clone is a link to that file. Compose reads the name there. It must
    # be the file set of EC2_OPTIONS of this checkout. The name is not a secret, and the
    # command prints no other line of the file.
    printf 'echo "== the file set of the host"\n'
    # shellcheck disable=SC2016 # the shell of the host runs the command
    printf 'host_files=$(sed -n "s/^COMPOSE_FILE=//p" .env | tr -d "\\047") || rc=1\n'
    # shellcheck disable=SC2016 # the shell of the host expands $host_files
    printf 'if [ "$host_files" = %s ]; then echo "file set %s: the host has it"; else echo "file set %s: FAIL (COMPOSE_FILE of the host is: $host_files. Write the secret, then run the bootstrap again)"; rc=1; fi\n' \
        "$compose_files" "$compose_files" "$compose_files"
    # Only the services of the file set of EC2_OPTIONS: a service of an option that is off
    # is not checked.
    printf 'echo "== the services of the file set %s"\n' "$compose_files"
    # shellcheck disable=SC2016 # the shell of the host runs the command
    printf 'running=$(docker compose ps --status running --services 2>/dev/null) || rc=1\n'
    # A kit with no tag or no commit in kits.lock of the host has no service: the bootstrap
    # clones and builds no such kit and does not start its service, and the verifier prints
    # a skip line for it. So such a service gets a skip line here too. The lock of the host
    # decides, not the lock of this checkout: the bootstrap reads that file. The command
    # prints the value `service` of each such kit. It is one line: the check script reads
    # each command of a dry run as one line.
    cat <<'EOT'
unpinned=$(awk 'function v(t, q, i) { sub(/^[^:]*:[ \t]*/, "", t); q = substr(t, 1, 1); if (q == "\042" || q == "\047") { t = substr(t, 2); i = index(t, q); if (i) t = substr(t, 1, i - 1) } else sub(/[ \t]+#.*$/, "", t); sub(/[ \t\r]+$/, "", t); return t } function flush() { if (kit != "" && service != "" && (tag == "" || commit == "")) print service; kit = "" } /^[^ #]/ { flush(); on = ($1 == "server_kits:"); next } !on { next } /^  [^ #]/ { flush(); kit = $1; tag = commit = service = ""; next } /^    source_tag:/ { tag = v($0) } /^    source_commit:/ { commit = v($0) } /^    service:/ { service = v($0) } END { flush() }' kits.lock 2>/dev/null) || rc=1
EOT
    for service in $services; do
        # shellcheck disable=SC2016 # the shell of the host expands $running and $unpinned
        printf 'if printf "%%s\\n" "$running" | grep -qxF -e %s; then echo "service %s: running"; elif printf "%%s\\n" "$unpinned" | grep -qxF -e %s; then echo "service %s: skip (its kit has no pin in kits.lock of the host, so the bootstrap starts no service of it)"; else echo "service %s: FAIL (not running)"; rc=1; fi\n' \
            "$service" "$service" "$service" "$service" "$service"
    done
    # The verifier of the host snapshot gets the file set and the public names as its
    # inputs. It reads the services from the Compose files of that set, so it holds no
    # list of its own. It compares the two inputs with the environment file of the host.
    # It reads the deployed lock and the keys of the containers itself.
    printf 'echo "== the deployed services of the file set %s"\n' "$compose_files"
    printf 'python3 -I scripts/verify-deployed.py --repo . --file-set %s --edge-names %s || rc=1\n' \
        "$compose_files" "$(printf '%s' "$edge_names" | tr ' ' ',')"
    echo "exit \$rc"
}

dry=0
apply=0
env_file=
command=
replace=0
allow_remove=0
write=0
plan=0
lockdown=0
allow_empty=
while [ $# -gt 0 ]; do
    case $1 in
        -h | --help) usage; exit 0 ;;
        --dry-run) dry=1 ;;
        --apply) apply=1 ;;
        --env-file)
            [ $# -ge 2 ] || die "--env-file needs a path"
            env_file=$2
            shift
            ;;
        --env-file=*) env_file=${1#--env-file=} ;;
        --replace-instance) replace=1 ;;
        --allow-remove) allow_remove=1 ;;
        --write) write=1 ;;
        --plan) plan=1 ;;
        --lockdown) lockdown=1 ;;
        --allow-empty | --allow-empty=*)
            case $1 in
                --allow-empty)
                    [ $# -ge 2 ] || die "--allow-empty needs a name"
                    word=$2
                    shift
                    ;;
                *) word=${1#--allow-empty=} ;;
            esac
            case $word in
                '' | *[!A-Za-z0-9_]*) die "--allow-empty needs one name of the environment file" ;;
            esac
            allow_empty="$allow_empty $word"
            ;;
        -*) usage >&2; exit 2 ;;
        *)
            [ -z "$command" ] || { usage >&2; exit 2; }
            command=$1
            ;;
    esac
    shift
done

case $command in
    deploy | outputs | secret | generated-status | dns | verify | host-allocate | host-release | stop | start | session | tunnel | ssh | host-bootstrap | recovery-check) ;;
    *) usage >&2; exit 2 ;;
esac
[ "$apply$dry" != 11 ] || die "--apply and --dry-run conflict"
[ "$replace" = 0 ] || [ "$command" = deploy ] || die "--replace-instance is an option of deploy"
[ "$allow_remove" = 0 ] || [ "$command" = deploy ] || die "--allow-remove is an option of deploy"
[ "$write" = 0 ] || [ "$command" = outputs ] || die "--write is an option of outputs"
[ "$plan" = 0 ] || [ "$command" = dns ] || die "--plan is an option of dns"
[ "$lockdown" = 0 ] || [ "$command" = host-bootstrap ] || die "--lockdown is an option of host-bootstrap"
[ -z "$allow_empty" ] || [ "$command" = secret ] || die "--allow-empty is an option of secret"
command -v python3 >/dev/null || die "python3 not found"
[ "$dry" = 1 ] || command -v aws >/dev/null || die "the AWS CLI not found"

missing=
secret_count=0
secret_empty=
secret_left_out=
secret_problems=
# The helper sets the option settings from the environment file (the function `options`
# of the helper). The shell environment does not set them. This script uses two of them.
options=
edge_names=
if [ "$command" = secret ]; then
    [ -z "$env_file" ] ||
        die "secret reads the environment file on stdin only: scripts/aws-host.sh secret < .env"
    [ ! -t 0 ] ||
        die "secret reads the environment file on stdin: scripts/aws-host.sh secret < .env"
    if [ "$dry" = 1 ]; then
        secret_file='<temporary file, mode 0600: the JSON object>'
        assign=$(helper secret "$sample" - "$allow_empty" "$top" "$apply") || exit 1
    else
        assign=$(helper secret "$sample" AUTO "$allow_empty" "$top" "$apply") || exit 1
    fi
    env_file=stdin
else
    [ -n "$env_file" ] || env_file=$top/.env
    [ -r "$env_file" ] ||
        die "the environment file $env_file is not there; a dry run can use: --env-file .env.example"
    # The absolute path, for each command that takes the file. A relative path of the form
    # <name>=<text> is an assignment for awk, and one that starts with - is an option.
    env_dir=$(CDPATH='' cd -- "$(dirname -- "$env_file")" && pwd) ||
        die "cannot find the directory of the environment file $env_file"
    env_file=${env_dir%/}/$(basename -- "$env_file")
    assign=$(helper load "$env_file" "$setting_names" "$top" "$command" "$dry" "$apply" "$replace" "$allow_remove" "$write" "$lockdown") || exit 1
fi
# Mac output is a static status, never shell input. Keep its private data in Python.
case $assign in
    *macos_done=1) printf '%s\n' "$assign" | sed '/^macos_done=1$/d'; exit 0 ;;
esac
# The helper quotes each homelab value, so the assignments are safe for eval.
eval "$assign"
if [ -n "${work:-}" ]; then
    trap 'rm -rf "$work"' EXIT
    trap 'exit 130' INT TERM HUP PIPE QUIT
fi

setting EC2_PROJECT tenant-ec2
project=$value
setting EC2_AWS_PROFILE "$project"
profile=$value
setting EC2_AWS_REGION us-east-1
region=$value
setting EC2_STACK_NAME "$project-host"
stack=$value
secret_id=$project/env
host_repo=/opt/$project
# The provisioner has rights only on a stack with the name of the project or with the
# project and a hyphen at the start of its name.
case $stack in
    "$project" | "$project"-*) ;;
    *) die "the stack name $stack does not start with the project $project" ;;
esac

case $command in
    deploy)
        [ -r "$template" ] || die "the template $template is not there"
        [ "$(template_parameters | LC_ALL=C sort | tr '\n' ' ')" = \
            "$(echo "$parameters" | tr -s ' \n' '\n' | LC_ALL=C sort | tr '\n' ' ')" ] ||
            die "the parameters of $template are not the parameters that this script sets"
        for name in $guarded; do
            grep -q "^  $name:\$" "$template" ||
                die "$template has no resource $name; the stop rule of deploy names it"
        done
        setting EC2_INSTANCE_TYPE t3a.large
        p_instance_type=$value
        setting EC2_AMI_SSM_PARAMETER /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
        p_ami=$value
        required EC2_VPC_CIDR
        p_vpc_cidr=$value
        required EC2_ADMIN_CIDR
        p_admin_cidr=$value
        setting EC2_DATA_VOLUME_SIZE 50
        p_volume_size=$value
        required EC2_SNAPSHOT_REPOSITORY
        p_repository=$value
        # The text before the first / of the address, after the scheme, must hold no @. A
        # user name or a credential in the URL goes to a dry run, to an argument and to a
        # parameter of the stack. The message does not show the value.
        authority=${p_repository#*://}
        case ${authority%%/*} in
            *@*) die "EC2_SNAPSHOT_REPOSITORY holds a user name or a credential before @. Use the URL with no such part. The token of a private clone is the key EC2_SNAPSHOT_CLONE_TOKEN of the secret." ;;
        esac
        required EC2_SNAPSHOT_TAG
        p_tag=$value
        required EC2_SNAPSHOT_COMMIT
        p_commit=$value
        required EC2_DOMAIN
        p_domain=$value
        # The two tunnel ports of the security group are open only with the option pangolin.
        p_pangolin=false
        ! has_option pangolin || p_pangolin=true
        setting EC2_BEDROCK_EMBED_MODEL amazon.titan-embed-text-v2:0
        p_embed=$value
        setting EC2_ENABLE_BUCKET false
        p_bucket=$value
        setting EC2_ENABLE_OPERATIONS false
        p_operations=$value
        setting EC2_ALARM_EMAIL ''
        p_alarm_email=$value
        setting EC2_ALARM_THRESHOLD 100
        p_alarm_threshold=$value
        [ "$p_operations" != true ] || [ -n "$p_alarm_email" ] ||
            die "EC2_ENABLE_OPERATIONS=true needs EC2_ALARM_EMAIL"
        required EC2_SSH_PUBLIC_KEY_FILE
        key_file=$value
        case $key_file in
            \~/*) key_file=$HOME/${key_file#??} ;;
        esac
        # The dry run shows no key text. The key is not a secret, but it is a value of one host.
        p_ssh_key='<the public key of EC2_SSH_PUBLIC_KEY_FILE>'
        case $key_file in
            '<'*) ;;
            *)
                [ -r "$key_file" ] || die "EC2_SSH_PUBLIC_KEY_FILE: the file $key_file is not there"
                IFS= read -r key_text <"$key_file" || [ -n "$key_text" ] ||
                    die "EC2_SSH_PUBLIC_KEY_FILE: the file $key_file is empty"
                case $key_text in
                    'ssh-ed25519 '* | 'ssh-rsa '*) ;;
                    *) die "EC2_SSH_PUBLIC_KEY_FILE: $key_file does not hold an OpenSSH public key of the type ed25519 or rsa" ;;
                esac
                [ "$dry" = 1 ] || p_ssh_key=$key_text
                ;;
        esac
        stop_on_missing
        stack_status
        if [ "$dry" = 1 ]; then
            note "the options of EC2_OPTIONS: ${options:-none}"
            note "when no stack has the name $stack, the first deploy:"
            deploy_command
            note "when the stack $stack exists, an update:"
            deploy_update
        else
            case $status in
                '' | REVIEW_IN_PROGRESS)
                    say "creating the stack $stack with the profile $profile"
                    deploy_command
                    ;;
                ROLLBACK_COMPLETE)
                    die "the stack $stack has the status ROLLBACK_COMPLETE: its create failed. Delete the stack, then run deploy again."
                    ;;
                *_IN_PROGRESS | *_FAILED)
                    die "the stack $stack has the status $status: no deploy now"
                    ;;
                *)
                    say "updating the stack $stack ($status) with the profile $profile"
                    deploy_update
                    ;;
            esac
            stack_outputs
            print_outputs
            echo "Next: scripts/aws-host.sh dns, then scripts/aws-host.sh verify after the bootstrap"
        fi
        ;;
    outputs)
        stack_outputs
        print_outputs
        if [ "$write" = 1 ]; then
            output ElasticIp '<ElasticIp>'
            pairs=
            # The address of each tunnel is a name of the option pangolin only.
            if has_option pangolin; then
                [ -n "$value" ] || die "the stack $stack has no output ElasticIp"
                pairs="EC2_GERBIL_BASE_ENDPOINT=$value"
            fi
            output BucketName '<BucketName, when the stack has a bucket>'
            if [ -n "$value" ]; then
                pairs="${pairs:+$pairs
}EC2_BUCKET=$value"
            else
                echo "outputs: the stack has no output BucketName; EC2_BUCKET is not changed"
            fi
            if [ -z "$pairs" ]; then
                echo "outputs: no name of $env_file takes an output of this stack; the file is not changed"
            elif [ "$dry" = 1 ]; then
                note "set these names in $env_file: $(printf '%s' "$pairs" | tr '\n' ' ')"
            else
                [ "$env_file" != "$sample" ] ||
                    die "--write does not change the sample environment file"
                [ ! -L "$env_file" ] || die "--write does not change a symbolic link: $env_file"
                new=$(mktemp "$env_file.XXXXXX")
                # The copy holds each value of the file: an exit before the move removes it.
                trap 'rm -f "$new"' EXIT
                trap 'exit 130' INT TERM HUP PIPE QUIT
                # The file is read two times: first for the names that it holds, then for
                # the copy. A name that the file lacks goes to the end of the outputs part.
                AWS_HOST_PAIRS=$pairs awk '
                # The reader of the file accepts blanks before a name. Without the same
                # rule here, such a name gets a second line, and the reader then refuses
                # the file.
                function key_of(line) {
                    sub(/^[ \t]+/, "", line)
                    if (line !~ /^[A-Za-z_][A-Za-z0-9_]*=/) return ""
                    sub(/=.*$/, "", line)
                    return line
                }
                function add_missing(    i) {
                    for (i = 1; i <= n; i++) {
                        if (name[i] == "" || name[i] in present) continue
                        print name[i] "=" val[name[i]]
                        present[name[i]] = 1
                    }
                }
                BEGIN {
                    marker = "# The outputs of the stack."
                    n = split(ENVIRON["AWS_HOST_PAIRS"], pair, "\n")
                    for (i = 1; i <= n; i++) {
                        name[i] = key_of(pair[i])
                        val[name[i]] = substr(pair[i], length(name[i]) + 2)
                    }
                }
                NR == FNR {
                    key = key_of($0)
                    if (key != "") present[key] = 1
                    next
                }
                {
                    if (in_part && $0 ~ /^[ \t]*$/) { add_missing(); in_part = 0 }
                    key = key_of($0)
                    if (key != "" && key in val) { print key "=" val[key]; next }
                    if (index($0, marker) == 1) { in_part = 1; has_marker = 1 }
                    print
                }
                END {
                    if (in_part) add_missing()
                    if (has_marker) exit
                    for (i = 1; i <= n; i++) {
                        if (name[i] == "" || name[i] in present) continue
                        print ""
                        print marker " `scripts/aws-host.sh outputs --write` sets each value."
                        add_missing()
                        break
                    }
                }' "$env_file" "$env_file" >"$new" || { rm -f "$new"; die "cannot write $env_file"; }
                # mktemp gives the copy the mode 0600. The file keeps the mode that it had.
                helper mode "$env_file" "$new"
                mv -f "$new" "$env_file"
                echo "outputs: set in $env_file: $(printf '%s' "$pairs" | sed 's/=.*$//' | tr '\n' ' ')"
                echo "Next: scripts/aws-host.sh secret < $env_file, so that the secret has the new values"
            fi
        fi
        ;;
    secret)
        if [ -n "$secret_problems" ]; then
            [ "$dry" = 1 ] || die "the input is refused: $secret_problems. No secret is written."
            note "a real run stops here: $secret_problems"
        fi
        [ -z "$secret_empty" ] || echo "secret: empty and accepted by --allow-empty: $secret_empty"
        [ -z "$secret_left_out" ] ||
            echo "secret: not in the secret, a name of a DNS provider: $secret_left_out"
        if [ "$dry" = 1 ]; then
            note "the options of EC2_OPTIONS: ${options:-none}"
            note "the secret gets $secret_count names; the JSON object goes to a file with the mode 0600"
            as_provisioner secretsmanager describe-secret --secret-id "$secret_id" \
                --query Name --output text
            note "when no secret has the name $secret_id:"
            secret_create
            note "when the secret $secret_id exists:"
            secret_put
        elif found=$(as_provisioner secretsmanager describe-secret --secret-id "$secret_id" \
            --query Name --output text 2>&1); then
            say "writing a new version of the secret $secret_id with the profile $profile"
            secret_put >/dev/null
            echo "secret: $secret_id has a new version with $secret_count names"
        else
            case $found in
                *ResourceNotFoundException*) ;;
                *) die "describe-secret failed: $found" ;;
            esac
            say "creating the secret $secret_id with the profile $profile"
            secret_create >/dev/null
            echo "secret: $secret_id is created with $secret_count names"
        fi
        ;;
    dns)
        [ -r "$dns_records" ] || die "the script $dns_records is not there"
        stack_outputs
        output ElasticIp ''
        address=$value
        # scripts/dns-records.sh reads the environment file itself: the domain, the option
        # settings and the credential of the provider. This script gives it the address.
        if [ "$plan" = 1 ]; then
            set -- plan
        else
            set -- apply
        fi
        if [ "$dry" = 1 ]; then
            # A dry run knows no address: the other script shows a stand-in.
            set -- --dry-run "$@"
        else
            [ -n "$address" ] || die "the stack $stack has no output ElasticIp"
            set -- --address "$address" "$@"
        fi
        sh "$dns_records" --env-file "$env_file" "$@" >&3
        ;;
    generated-status)
        if [ "$dry" = 1 ]; then
            note "read $project/generated; print each generated name with present or absent, never a value"
        else
            python3 -I "$here/generated-keys.py" status --project "$project" --region "$region" --profile "$profile"
        fi
        ;;
    verify)
        # Validate before AWS or SSM; the project becomes a shell path on the host.
        case $project in
            '' | *[!A-Za-z0-9_-]*) die "EC2_PROJECT is not a safe host directory name" ;;
        esac
        if [ "$dry" = 1 ]; then
            note "compare the three required generated names with $project/generated; never compare or print values"
        else
            generated_status=0
            python3 -I "$here/generated-keys.py" status --require \
                --project "$project" --region "$region" --profile "$profile" || generated_status=$?
            case $generated_status in
                0) echo "ok - generated secret: all required names are present" ;;
                3) echo "skip - generated secret: not in this deployment; apply the host stack update first" ;;
                *) die "verify: the generated secret lacks a required name or cannot be read" ;;
            esac
        fi
        stack_status
        [ -n "$status" ] || die "no stack has the name $stack"
        echo "stack $stack: $status"
        stack_outputs
        output InstanceId '<InstanceId>'
        instance=$value
        [ -n "$instance" ] || die "the stack $stack has no output InstanceId"
        make_work
        host_commands >"$work/commands"
        if [ "$dry" = 1 ]; then
            note "the commands for the host:"
            sed 's/^/dry run:   /' "$work/commands" >&3
            ssm_file='<temporary file: the commands above as JSON>'
        else
            ssm_file=$work/ssm.json
            helper ssm "$ssm_file" "$verify_timeout" <"$work/commands"
            say "running the verify commands on $instance"
        fi
        capture '<command id>' as_provisioner ssm send-command \
            --instance-ids "$instance" --document-name AWS-RunShellScript \
            --comment "$project: verify" --parameters "file://$ssm_file" \
            --query Command.CommandId --output text
        command_id=$captured
        if [ "$dry" = 1 ]; then
            as_provisioner ssm get-command-invocation --command-id "$command_id" \
                --instance-id "$instance"
            note "wait for the end of the command, then print its output and its status"
            exit 0
        fi
        n=0
        errors=0
        while :; do
            if state=$(as_provisioner ssm get-command-invocation --command-id "$command_id" \
                --instance-id "$instance" --query Status --output text 2>&1); then
                errors=0
            else
                # The invocation can need some seconds before SSM knows it.
                errors=$((errors + 1))
                [ "$errors" -lt 6 ] || die "get-command-invocation failed: $state"
                state=Pending
            fi
            case $state in
                Pending | InProgress | Delayed) ;;
                *) break ;;
            esac
            n=$((n + 5))
            [ "$n" -le $((verify_timeout + 60)) ] ||
                die "the command $command_id has no end after $n s"
            sleep 5
        done
        # SSM keeps the first 24000 characters of each stream.
        as_provisioner ssm get-command-invocation --command-id "$command_id" \
            --instance-id "$instance" --query StandardOutputContent --output text
        errors=$(as_provisioner ssm get-command-invocation --command-id "$command_id" \
            --instance-id "$instance" --query StandardErrorContent --output text)
        case $errors in
            '' | None) ;;
            *) printf '%s\n' "$errors" >&2 ;;
        esac
        [ "$state" = Success ] || die "verify: the command on the host has the status $state"
        echo "verify: pass"
        ;;
esac
