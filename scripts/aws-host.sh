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
#       The command reads stdin only. These names need a value that is not a placeholder:
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


def load(path, names):
    found = parse(read_file(path), path)
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
        mark = re.match(r"# ----- (Option|DNS provider) ([a-z0-9]+) -----", line)
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


def secret(sample_path, out, allow, repo):
    text = read_file(sample_path)
    sample = parse(text, sample_path)
    rows = blocks(text)
    core = [name for part, kind, _, name in rows if part == "1" and not kind]
    if not core:
        stop("%s has no name in Part 1" % sample_path)
    found = parse(sys.stdin, "stdin")
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
    if not problems and out != "-":
        write_file(out, body)
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


mode, args = sys.argv[1], sys.argv[2:]
if mode == "load":
    load(args[0], args[1])
elif mode == "secret":
    secret(args[0], args[1], args[2], args[3])
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
env_file=
command=
replace=0
allow_remove=0
write=0
plan=0
allow_empty=
while [ $# -gt 0 ]; do
    case $1 in
        -h | --help) usage; exit 0 ;;
        --dry-run) dry=1 ;;
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
    deploy | outputs | secret | dns | verify) ;;
    *) usage >&2; exit 2 ;;
esac
[ "$replace" = 0 ] || [ "$command" = deploy ] || die "--replace-instance is an option of deploy"
[ "$allow_remove" = 0 ] || [ "$command" = deploy ] || die "--allow-remove is an option of deploy"
[ "$write" = 0 ] || [ "$command" = outputs ] || die "--write is an option of outputs"
[ "$plan" = 0 ] || [ "$command" = dns ] || die "--plan is an option of dns"
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
        assign=$(helper secret "$sample" - "$allow_empty" "$top") || exit 1
    else
        make_work
        secret_file=$work/secret.json
        assign=$(helper secret "$sample" "$secret_file" "$allow_empty" "$top") || exit 1
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
    assign=$(helper load "$env_file" "$setting_names") || exit 1
fi
# The helper quotes each value, so the assignments are safe for eval.
eval "$assign"

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
    verify)
        # Validate before AWS or SSM; the project becomes a shell path on the host.
        case $project in
            '' | *[!A-Za-z0-9_-]*) die "EC2_PROJECT is not a safe host directory name" ;;
        esac
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
