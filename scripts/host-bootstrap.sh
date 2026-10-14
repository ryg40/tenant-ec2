#!/bin/sh
# Bring a prepared host of tenant-ec2 to a running Compose stack. scripts/host-userdata.sh
# starts this script at the first boot, as root, from the clone of the portable snapshot.
# A second run changes nothing that is done. See docs/reference/host-boot.md.
#
# Usage: scripts/host-bootstrap.sh [--dry-run] [--stack-env <file>] [--root <directory>]
#        scripts/host-bootstrap.sh --check-forms <file>
#   --dry-run           print each step and what it would do; change nothing on the host,
#                       make no AWS call, start no container. It writes only into a
#                       private directory below /tmp, and it removes that directory.
#                       A wrong form stops the dry run at step 2. Missing values still
#                       give notes, so a dry run can show an unfinished install.
#   --check-forms <file> test nonempty forms only, then exit; - reads stdin.
#                       Read no stack file, change no input and make no network call.
#                       Pangolin uses a temporary file with edge settings only.
#                       The secret command runs this test before writing its JSON file.
#   --stack-env <file>  the stack values file; the default is /etc/tenant-ec2/stack.env
#   --root <directory>  for a test: a directory in place of / for each path outside the
#                       clone. The script then does not test the mount points.
#   --help              print this text
#
# The steps, in this order:
#   1. stack values    read the region, the project and the name of the secret from the
#                      stack values file that the user data writes. It holds no secret.
#   2. environment     read the Secrets Manager secret with the instance role and write
#                      the environment file from it, with mode 0600; read EC2_OPTIONS
#                      and write COMPOSE_FILE; test each name that a later step needs
#   3. directories     make each directory of the data volume that an active Compose
#                      file binds, with the owner that its service needs
#   4. host settings   make Docker need the four durable mounts and the metadata guard;
#                      with the option pangolin, load the kernel module wireguard;
#                      close the instance metadata address for the containers; set the
#                      Docker log driver journald; with the operations of the stack,
#                      export the journal to files before the start of the CloudWatch
#                      agent
#   5. kits            clone each kit of kits.lock at its tag, compare the commit, build
#                      one kit at a time, record the image id into kits.lock of the host
#                      and, with the commit, into the build record
#   6. edge            with the option pangolin, render the Pangolin configuration
#                      (scripts/render-pangolin.sh). The core edge needs no file.
#   7. start and keys  start the edge, then the gateway; make the two virtual keys; start
#                      OpenViking; make the OpenViking key of the agent; start the rest
#   8. blueprint       with the option pangolin, apply pangolin/blueprint.yaml when
#                      EC2_PANGOLIN_API_KEY has a value
#   9. next steps      print each step that the owner does by hand, for the active
#                      options only
#
# The options. EC2_OPTIONS of the environment file is a list of option names with commas
# and no blank. The known names are pangolin and copilot. The script stops on another
# name. The list says which Compose files are on:
#   EC2_OPTIONS        COMPOSE_FILE
#   (empty)            compose.yaml:compose.caddy.yaml
#   copilot            compose.yaml:compose.caddy.yaml:compose.copilot.yaml
#   pangolin           compose.yaml:compose.pangolin.yaml
#   pangolin,copilot   compose.yaml:compose.pangolin.yaml:compose.copilot.yaml
# Step 2 writes the list into the name COMPOSE_FILE of the environment file. Compose
# reads the name there, so a Compose command by hand from the clone uses the same files.
# The secret does not hold the name: the script makes it at each run.
# EC2_EDGE_NAMES is the list of the public names, in the same form. The known names are
# ok, agent, llm and ov. Each edge makes a route only for a name of the list.
# After a change of EC2_OPTIONS, run the script again. Compose then removes each
# container of a file that is off now, for example the other edge.
#
# Paths on the host. The clone is the tree that holds this script.
#   /opt/tenant-ec2                  the clone of the portable snapshot
#   /opt/tenant-ec2/kits/<kit>       the clone of each kit, on the root volume
#   /opt/tenant-ec2/kits.lock        this script writes `image_digest` of each kit here.
#                                    The owner copies the ids into the repository.
#   /etc/tenant-ec2/stack.env        the stack values file
#   /srv/data/tenant-ec2/.env        the environment file, owner root, mode 0600. It is
#                                    on the data volume, outside each build context.
#   /srv/data/tenant-ec2/builds/<kit>  the build record of a kit: one line with the
#                                    commit and the image id of the last build
#   /opt/tenant-ec2/.env             a link to the environment file, so that a Compose
#                                    command by hand from the clone reads it
#   /srv/data/<service>              the directories of the data volume (step 3)
#   /srv/efs/<name>                  the access points. This script makes nothing there.
#
# The environment file. Its content is the secret: one JSON object with the names of
# .env.example as keys, as `scripts/aws-host.sh secret` writes it. This script stops at
# step 2, before a build, when a name that a later step needs has no value. It tests only
# the names that the active Compose files and blocks need:
#   - a name of Part 1 of .env.example with no value or a placeholder;
#   - a name with an empty value in the block `Option <name>` of Part 3, when EC2_OPTIONS
#     holds <name>: no value or a placeholder;
#   - a name that an active Compose file reads in the form ${NAME:?...} with no value.
#     With the option pangolin, the two names of the block `DNS provider porkbun` are
#     such names. The script reads no other name of a DNS provider block;
#   - a setting of the active edge that caddy/start.sh or scripts/render-pangolin.sh
#     refuses;
#   - a model setting of the agent or of OpenViking that is not a gateway route of the
#     active Compose files;
#   - with the web search `mcp` of the agent, one of the two web search names.
# These names can be empty:
#   - the three names that step 7 makes: EC2_OPENVIKING_LITELLM_API_KEY,
#     EC2_PI_DURABLE_LITELLM_API_KEY and EC2_PI_DURABLE_OPENVIKING_API_KEY;
#   - EC2_SNAPSHOT_CLONE_TOKEN (a public snapshot repository needs none);
#   - EC2_PANGOLIN_API_KEY and EC2_PANGOLIN_ORG (step 8 waits for them);
#   - each EC2_KIT_SOURCE_<KIT> and EC2_KIT_TOKEN_<KIT> (step 5 asks for the one it needs).
# A placeholder has the meaning of scripts/aws-host.sh: the function `placeholder` below
# holds the same list. EC2_PI_DURABLE_THINKING must be xhigh.
# The generated secret supplies these three keys first. The environment secret is
# second, and the old host file is the fallback. Step 7 writes changed keys to
# <project>/generated only. A failed write warns and keeps the host file.
# A value must not hold a line break or a single quote, and it must not end with a
# backslash: the environment file cannot hold such a value. `scripts/aws-host.sh secret`
# refuses the same values, so the secret holds none.
#
# The virtual keys. A virtual key allows only the gateway routes of the active Compose
# files: `bedrock/luna` and `bedrock/embed`, and with the option copilot also
# `copilot/luna` and `copilot/embed`. The key of the agent allows the routes of GPT-6
# Luna only. At each run, the script sets the model list of each key that is there. So a
# key follows a change of EC2_OPTIONS. When the gateway does not know a key of the
# environment file, the script makes a new key.
#
# Secrets. The script stops a trace at its start (`set +x`), so a run with `sh -x` prints
# no value. It prints a name, never a value of the environment file. A secret is in no
# argument of a command: a value goes through a pipe, and Git gets the clone token of a
# kit from a helper (GIT_ASKPASS). The script never runs `docker compose config`.
# Each Python text runs in isolated mode (`python3 -I`): Python then loads no module from
# the current directory and reads no PYTHON* name of the environment.
#
# Kits. A kit with no tag or no commit in kits.lock is not cloned and not built, and its
# service is not started: one line says so. A clone whose commit is not the commit of
# kits.lock stops the script. A build runs with an empty environment, so no variable of
# the shell changes a pin of the kit. The one variable is GATEWAY_IMAGE with the image
# name of kits.lock: the OpenViking kit names its image from it.
# After a build, the script writes the commit and the image id into the build record of
# the kit. A run skips a build only when these three facts are true: the clone is at the
# commit of kits.lock; the image is there with the id of kits.lock; the build record names
# that commit and that id. So a new commit in kits.lock gives a new build, also when the
# name and the old id of the image stay in kits.lock.
# The build of each public kit writes the commit of its clone into the image label
# org.opencontainers.image.revision. When an image has that label with a commit, the
# commit must be the commit of kits.lock: for a skip, and after a build. An image with no
# such label has the build record only.
#
# Exit: 0 pass, 1 an error or a failed apply of the blueprint, 2 usage.
# Needs: python3, git, Docker with the Compose and buildx plugins, the AWS CLI version 2,
# cmp (the package diffutils), iptables, systemd. The option pangolin needs envsubst,
# modprobe and, for the blueprint step, the Python module yaml.
# --check-forms needs only Python 3 and the shell tools of the edge. Pangolin needs
# envsubst from gettext, also on the development machine.

set -eu
# No trace: with `sh -x`, the shell would print the secret and the token of a kit.
set +x
# A kit clone and a build context need files that each user can read. Each file with a
# secret gets its mode from the helper, and step 2 runs with umask 077.
umask 022

# The names that step 7 makes, and the value that stands in for one of them in a Compose
# command before it exists. The value reaches no container.
MADE_NAMES="EC2_OPENVIKING_LITELLM_API_KEY EC2_PI_DURABLE_LITELLM_API_KEY EC2_PI_DURABLE_OPENVIKING_API_KEY"
STUB=not-made-yet
# The account and the two users of the agent in OpenViking.
OV_ACCOUNT=pi-durable
OV_ADMIN=owner
OV_USER=agent
# Wait this number of seconds for the health of a group of services.
WAIT=600
GUARD=tenant-ec2-metadata-guard

say() {
    echo "host-bootstrap: $*"
}

skip() {
    echo "host-bootstrap: skip: $*"
}

die() {
    echo "host-bootstrap: error: $*" >&2
    exit 1
}

usage() {
    sed -n '2,/^$/s/^# \{0,1\}//p' "$0"
}

# The one reader and writer of the environment file and of kits.lock. A value arrives on
# stdin or from a file, never as an argument. A message holds a name, never a value.
# The text is Python 3.9: the version of Amazon Linux 2023.
# shellcheck disable=SC2016 # the text is Python: ${ is the text of a placeholder rule
helper_py='
import base64
import json
import os
import re
import shlex
import sys

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)")
# A name that a Compose file refuses with no value: the form ${NAME:?text}.
NEEDED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?")
SAFE = re.compile(r"[A-Za-z0-9_./:@%+=,-]*")
# One name on each line: the secret scan reads two names on one line as a name and a value.
MADE = (
    "EC2_OPENVIKING_LITELLM_API_KEY",
    "EC2_PI_DURABLE_LITELLM_API_KEY",
    "EC2_PI_DURABLE_OPENVIKING_API_KEY",
)
OPTIONAL = (
    "EC2_SNAPSHOT_CLONE_TOKEN",
    "EC2_PANGOLIN_API_KEY",
    "EC2_PANGOLIN_ORG",
)
OPTIONAL_STARTS = ("EC2_KIT_SOURCE_", "EC2_KIT_TOKEN_")
# The names that the agent needs with the web search `mcp`.
WEB_SEARCH_NAMES = (
    "EC2_PI_DURABLE_WEB_SEARCH_MCP_URL",
    "EC2_PI_DURABLE_WEB_SEARCH_MCP_TOKEN",
)
# The option names of EC2_OPTIONS and the public names of EC2_EDGE_NAMES.
KNOWN_OPTIONS = ("pangolin", "copilot")
EDGE_NAMES = ("ok", "agent", "llm", "ov")
# The marker lines of .env.example: a part, and a block of Part 3.
PART = re.compile(r"# ===== Part ([0-9]+)")
BLOCK = re.compile(r"# ----- (Option|DNS provider) ([a-z0-9]+) -----")
KIT_FIELDS = ("source_repo", "source_tag", "source_commit", "image", "image_digest",
              "build", "service")
LOG_OPTS = {"tag": "{{.Name}}"}


def stop(text):
    sys.stderr.write("host-bootstrap: error: " + text + "\n")
    sys.exit(1)


def read_lines(path):
    try:
        if path == "-":
            return sys.stdin.read().splitlines()
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()
    except (OSError, UnicodeError) as error:
        stop("cannot read %s: %s" % (path, getattr(error, "strerror", None) or "not UTF-8 text"))


def parse(path):
    """The NAME=value lines of a file as a dict in the order of the file."""
    found = {}
    for number, raw in enumerate(read_lines(path), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE.fullmatch(line)
        if not match:
            stop("%s: line %d is not a NAME=value line" % (path, number))
        name, value = match.group(1), match.group(2).strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"\x27":
            value = value[1:-1]
        found[name] = value
    return found


def write_private(path, text, mode=0o600):
    """Write a file through a new file with the final mode, then move it into place."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "w",
                   encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_env(path, found):
    lines = []
    for name, value in found.items():
        if any(char in value for char in "\n\r\0\x27") or value.endswith("\\"):
            stop("the value of %s holds a line break or a single quote, or it ends with a "
                 "backslash: the environment file cannot hold it" % name)
        # Compose reads a value between single quotes as it is. It replaces a reference
        # such as $NAME in a value with no quotes, so only a plain value has no quotes.
        lines.append("%s=%s" % (name, value) if SAFE.fullmatch(value)
                     else "%s=\x27%s\x27" % (name, value))
    write_private(path, "\n".join(lines) + "\n")


def placeholder(value, sample):
    # The same list as the function `placeholder` of scripts/aws-host.sh. Each rule but
    # the first ignores the letter case. A value is a placeholder when it is:
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


def compose_names(path):
    """Each name that the Compose file refuses with no value, in the order of the file."""
    names = []
    for line in read_lines(path):
        if line.lstrip().startswith("#"):
            continue
        for name in NEEDED.findall(line):
            if name not in names:
                names.append(name)
    if not names:
        stop("%s reads no name in the form ${NAME:?...}" % path)
    return names


def layout(sample_path):
    """The names of .env.example by their place: the names of Part 1, and for each block
    of Part 3 its names with their sample values. A block ends at the next marker line."""
    part, block, core, blocks = "", None, [], {}
    for raw in read_lines(sample_path):
        line = raw.strip()
        mark = PART.match(line)
        if mark:
            part, block = mark.group(1), None
            continue
        mark = BLOCK.match(line)
        if mark:
            block = (mark.group(1), mark.group(2))
            blocks.setdefault(block, [])
            continue
        match = LINE.fullmatch(line)
        if not match:
            continue
        if part == "1":
            core.append(match.group(1))
        elif part == "3" and block:
            blocks[block].append((match.group(1), match.group(2).strip()))
    if not core:
        stop("%s has no name in Part 1" % sample_path)
    return core, blocks


def name_list(found, name, known):
    """The names of a list setting. The second result says why the value is refused."""
    value = found.get(name, "")
    names = value.split(",") if value else []
    if not all(re.fullmatch(r"[a-z0-9]+", item) for item in names):
        return [], "%s is not a list of names with commas and no blank" % name
    if len(set(names)) != len(names):
        return [], "%s holds a name two times" % name
    if not all(item in known for item in names):
        return [], ("%s holds a name that this script does not know; the known names are: %s"
                    % (name, " ".join(known)))
    return names, ""


def compose_files(options):
    """The Compose files of a list of options, in the order of COMPOSE_FILE."""
    files = ["compose.yaml",
             "compose.pangolin.yaml" if "pangolin" in options else "compose.caddy.yaml"]
    if "copilot" in options:
        files.append("compose.copilot.yaml")
    return files


def routes(options):
    """The gateway routes of the active Compose files: the routes of GPT-6 Luna, then the
    embedding routes (litellm/config.yaml, litellm/config.copilot.yaml)."""
    luna, embed = ["bedrock/luna"], ["bedrock/embed"]
    if "copilot" in options:
        luna.append("copilot/luna")
        embed.append("copilot/embed")
    return luna, embed


def web_search(found):
    search = found.get("EC2_PI_DURABLE_WEB_SEARCH")
    if search and search not in ("off", "mcp"):
        stop("EC2_PI_DURABLE_WEB_SEARCH is not off or mcp")
    return search


def problems(found, sample_path, repo):
    """Each reason to refuse the values, with names only."""
    sample = parse(sample_path)
    core, blocks = layout(sample_path)
    options, bad = name_list(found, "EC2_OPTIONS", KNOWN_OPTIONS)
    if bad:
        # The active Compose files are not known, so no other test runs.
        return [bad]
    # `names` gets the placeholder test. `need` must have a value.
    names = list(core)
    need = [n for n in core
            if n not in MADE and n not in OPTIONAL and not n.startswith(OPTIONAL_STARTS)]
    for option in options:
        for name, value in blocks.get(("Option", option), []):
            if value:
                # A name with a default: the default is a correct value.
                continue
            names.append(name)
            if name not in OPTIONAL:
                need.append(name)
    if "pangolin" in options:
        # The one DNS provider block that the host reads: Traefik needs the pair.
        names += [name for name, _ in blocks.get(("DNS provider", "porkbun"), [])]
    # Compose stops on an empty value of each such name, also for a service that a
    # command does not start. Step 7 makes the names of MADE.
    for file in compose_files(options):
        need += [n for n in compose_names(os.path.join(repo, file))
                 if n not in MADE and n not in need]
    empty = [n for n in need if not found.get(n)]
    fake = [n for n in names if found.get(n) and placeholder(found[n], sample.get(n, ""))]
    out = []
    if empty:
        out.append("no value for " + " ".join(empty))
    if fake:
        out.append("a placeholder as the value of " + " ".join(fake))
    out += form_problems(found, options)
    if web_search(found) == "mcp":
        lack = [n for n in WEB_SEARCH_NAMES if not found.get(n)]
        if lack:
            out.append("EC2_PI_DURABLE_WEB_SEARCH is mcp, and there is no value for "
                       + " ".join(lack))
    return out


def form_problems(found, options):
    """Test nonempty values only. The caller handles required names and exceptions."""
    out = []
    effort = found.get("EC2_PI_DURABLE_THINKING")
    if effort and effort != "xhigh":
        out.append("EC2_PI_DURABLE_THINKING is not xhigh")
    _, bad = name_list(found, "EC2_EDGE_NAMES", EDGE_NAMES)
    if bad:
        out.append(bad)
    luna, embed = routes(options)
    for name, allowed in (("EC2_PI_DURABLE_MODEL", luna), ("EC2_OPENVIKING_VLM_MODEL", luna),
                          ("EC2_OPENVIKING_EMBED_MODEL", embed)):
        if found.get(name) and found[name] not in allowed:
            out.append("%s is not a gateway route of the active Compose files; the routes are: %s"
                       % (name, " ".join(allowed)))
    size = found.get("EC2_OPENVIKING_EMBED_DIMENSION")
    if size and not re.fullmatch(r"[1-9][0-9]{1,4}", size):
        out.append("EC2_OPENVIKING_EMBED_DIMENSION must be an integer from 10 to 99999")
    web_search(found)
    return out


def forms(found, repo):
    """Use the edge scripts themselves. Skip empty values with valid stand-ins."""
    import subprocess
    import tempfile
    options, bad = name_list(found, "EC2_OPTIONS", KNOWN_OPTIONS)
    errors = [bad] if bad else form_problems(found, options)
    if errors:
        stop("; ".join(errors))
    clean = {"PATH": os.environ.get("PATH", os.defpath)}
    # These are valid stand-ins, not a second list of form rules. Presence tests
    # stay with the caller, including --allow-empty and first-boot exceptions.
    edge = {
        "EC2_DOMAIN": "forms.invalid",
        "EC2_EDGE_NAMES": "ok",
        "EC2_ADMIN_CIDR": "192.0.2.1/32",
        "EC2_ACME_EMAIL": "forms@forms.invalid",
        "EC2_GERBIL_BASE_ENDPOINT": "forms.invalid",
        "EC2_PANGOLIN_SITE": "forms",
    }
    edge = {name: found.get(name) or default for name, default in edge.items()}
    with tempfile.TemporaryDirectory(prefix="env-forms-") as work:
        if "pangolin" in options:
            path = os.path.join(work, "edge.env")
            # No credential goes into this file. Quotes preserve blanks and hashes.
            write_env(path, edge)
            command = ["sh", os.path.join(repo, "scripts/render-pangolin.sh"),
                       "--dry-run", "--env-file", path]
        else:
            clean.update(edge)
            command = ["sh", os.path.join(repo, "caddy/start.sh"), "--print"]
        result = subprocess.run(command, env=clean, capture_output=True, text=True)
    if result.returncode:
        # Edge errors hold names only. Add the rule without copying the edge tests.
        rules = {
            "EC2_DOMAIN": "must use letters, digits, dots or hyphens, with letters or digits at both ends",
            "EC2_ADMIN_CIDR": "must be an IPv4 address with a prefix length from 16 to 32",
            "EC2_ACME_EMAIL": "must be an email address with a local part, @ and a domain",
            "EC2_GERBIL_BASE_ENDPOINT": "must use letters, digits, dots or hyphens, with letters or digits at both ends",
            "EC2_PANGOLIN_SITE": "must use letters, digits, underscores or hyphens, with letters or digits at both ends",
            "EC2_EDGE_NAMES": "must list ok, agent, llm or ov with commas, no blanks and no duplicate",
        }
        for name, rule in rules.items():
            if re.search(r"\b" + name + r"\b", result.stderr):
                stop(name + " " + rule)
        stop("the edge form test failed; check its files and the required shell tools")


def render(sample_path, repo, env_path, generated_path=None):
    """stdin: the text of the secret. Write the environment file from it."""
    try:
        data = json.loads(sys.stdin.read())
    except ValueError:
        stop("the secret is not JSON text")
    if not isinstance(data, dict) or not data:
        stop("the secret is not a JSON object with the names as keys")
    found = {}
    for name, value in data.items():
        if not NAME.fullmatch(name):
            stop("the secret has a key that is not a name of the environment file")
        if value is None:
            value = ""
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            stop("the value of %s in the secret is not a text" % name)
        found[name] = str(value)
    forms(found, repo)
    old = parse(env_path) if os.path.exists(env_path) else {}
    generated = {}
    if generated_path:
        try:
            with open(generated_path, encoding="utf-8") as handle:
                generated = json.load(handle)
        except (OSError, ValueError):
            stop("cannot read the generated keys file")
    kept = []
    for name in MADE:
        if generated.get(name):
            found[name] = generated[name]
        if not found.get(name) and old.get(name):
            found[name] = old[name]
            kept.append(name)
    bad = problems(found, sample_path, repo)
    if bad:
        stop("the secret is refused, and the environment file is not written: " + "; ".join(bad))
    # The Compose files of the options. Compose reads the name from the environment
    # file. A value of the secret for the name is not used.
    options, _ = name_list(found, "EC2_OPTIONS", KNOWN_OPTIONS)
    found["COMPOSE_FILE"] = ":".join(compose_files(options))
    same = bool(old) and old == found and (os.stat(env_path).st_mode & 0o777) == 0o600
    if not same:
        write_env(env_path, found)
    print("render_count=%d" % len(found))
    print("render_same=%d" % same)
    print("render_kept=%s" % shlex.quote(" ".join(kept)))


def check(sample_path, repo, env_path):
    bad = problems(parse(env_path), sample_path, repo)
    for text in bad:
        print(text)
    sys.exit(1 if bad else 0)


def plan(env_path):
    """Print what the options of the environment file turn on, as shell assignments.
    With no file, and with a list of options that is refused, the plan is the core."""
    found = parse(env_path) if os.path.exists(env_path) else {}
    # Refuse an old search selector also in a dry run, before the later steps.
    web_search(found)
    options, bad = name_list(found, "EC2_OPTIONS", KNOWN_OPTIONS)
    luna, embed = routes(options)
    files = compose_files(options)
    pairs = (
        ("plan_bad", "1" if bad else "0"),
        ("plan_options", " ".join(options)),
        ("plan_files", " ".join(files)),
        ("plan_compose_file", ":".join(files)),
        ("opt_pangolin", "1" if "pangolin" in options else "0"),
        ("opt_copilot", "1" if "copilot" in options else "0"),
        # The routes that a virtual key allows, as JSON lists.
        ("plan_openviking_models", json.dumps(embed + luna, separators=(",", ":"))),
        ("plan_pi_durable_models", json.dumps(luna, separators=(",", ":"))),
    )
    for name, value in pairs:
        print("%s=%s" % (name, shlex.quote(value)))


def get(env_path, name):
    value = parse(env_path).get(name, "")
    if value:
        print(value)


def has(env_path, name):
    sys.exit(0 if os.path.exists(env_path) and parse(env_path).get(name) else 1)


def put(env_path, name, kept=""):
    """stdin: one new value of a name. Write it into the environment file.
    Exit 3 and no change: stdin holds the word `kept`, so the value of the file stays."""
    value = sys.stdin.read().strip()
    if kept and value == kept:
        sys.exit(3)
    if len(value) < 16 or re.search(r"\s", value):
        stop("no value for %s arrived" % name)
    found = parse(env_path)
    found[name] = value
    write_env(env_path, found)


def lock(path):
    """The kits of the block server_kits of kits.lock: names, fields, line numbers."""
    kits, lines, section, kit = {}, {}, "", ""
    for number, raw in enumerate(read_lines(path), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            section, kit = (text[:-1] if text.endswith(":") else ""), ""
        elif section == "server_kits" and indent == 2 and text.endswith(":"):
            kit = text[:-1]
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", kit):
                stop("%s: line %d: the name of a kit has small letters, digits and - only"
                     % (path, number))
            kits[kit] = {}
        elif section == "server_kits" and indent == 4 and kit:
            key, _, value = text.partition(":")
            value = value.strip()
            if value[:1] in ("\"", "\x27"):
                end = value.find(value[0], 1)
                if end < 0:
                    stop("%s: line %d has a quote with no end" % (path, number))
                value = value[1:end]
            else:
                value = re.sub(r"\s+#.*$", "", value)
            kits[kit][key] = value
            lines[(kit, key)] = number
    return kits, lines


def kit(path, name):
    kits, _ = lock(path)
    if name not in kits:
        stop("%s has no kit %s" % (path, name))
    for field in KIT_FIELDS:
        print("kit_%s=%s" % (field, shlex.quote(kits[name].get(field, ""))))


def lock_set(path, name, image_id):
    """Write image_digest of one kit. Only that line of the file changes."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        stop("the image id of the kit %s does not have the form sha256:<64 digits>" % name)
    _, lines = lock(path)
    number = lines.get((name, "image_digest"))
    if not number:
        stop("%s has no image_digest line for the kit %s" % (path, name))
    text = read_lines(path)
    old = text[number - 1]
    text[number - 1] = "%simage_digest: \"%s\"" % (old[:len(old) - len(old.lstrip(" "))], image_id)
    write_private(path, "\n".join(text) + "\n", os.stat(path).st_mode & 0o777)


def daemon(path, mode):
    """The Docker daemon file with the log driver journald. Exit 3: a change is necessary."""
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            data = json.loads(text) if text.strip() else {}
        except (OSError, ValueError):
            stop("%s is not JSON text" % path)
        if not isinstance(data, dict):
            stop("%s is not a JSON object" % path)
    want = dict(data)
    want["log-driver"] = "journald"
    # An option of another log driver stops Docker, so the options are replaced.
    want["log-opts"] = LOG_OPTS
    if want == data:
        sys.exit(0)
    if mode != "write":
        sys.exit(3)
    write_private(path, json.dumps(want, indent=2) + "\n", 0o644)


def blueprint(env_path, rendered):
    """Print a configuration of curl for the apply of the blueprint. It holds the key."""
    found = parse(env_path)
    key, org = found.get("EC2_PANGOLIN_API_KEY", ""), found.get("EC2_PANGOLIN_ORG", "")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", key):
        stop("EC2_PANGOLIN_API_KEY has a character that the request cannot hold")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", org):
        stop("EC2_PANGOLIN_ORG is not the identifier of an organization")
    try:
        import yaml
    except ImportError:
        stop("the Python module yaml is missing: install the package python3-pyyaml")
    try:
        with open(rendered, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        stop("cannot read %s as YAML" % rendered)
    if not isinstance(data, dict):
        stop("%s is not a YAML mapping" % rendered)
    text = base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")

    def quote(value):
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

    print("url = " + quote("http://localhost:3003/v1/org/%s/blueprint" % org))
    print("request = \"PUT\"")
    print("header = " + quote("Authorization: Bearer " + key))
    print("header = \"Content-Type: application/json\"")
    print("data = " + quote(json.dumps({"blueprint": text})))


command, args = sys.argv[1], sys.argv[2:]
if command == "render":
    render(*args)
elif command == "generated":
    found = parse(args[0])
    print(json.dumps({name: found.get(name, "") for name in MADE}))
elif command == "check":
    check(*args)
elif command == "forms":
    forms(parse(args[0]), args[1])
elif command == "plan":
    plan(*args)
elif command == "get":
    get(*args)
elif command == "has":
    has(*args)
elif command == "put":
    put(*args)
elif command == "kits":
    print("\n".join(lock(args[0])[0]))
elif command == "kit":
    kit(*args)
elif command == "lock-set":
    lock_set(*args)
elif command == "daemon":
    daemon(*args)
elif command == "blueprint":
    blueprint(*args)
else:
    stop("unknown helper command")
'

# The text that makes one virtual key, or sets the model list of a key that is there. It
# runs in the container of the gateway, which holds the master key in its environment.
# Arguments: the key alias, the models as a JSON list. stdin: the key that the
# environment file holds, or nothing. The key can call a model route only.
# - With a key on stdin that the gateway knows, the text sets the model list of the key
#   and prints the word `kept`.
# - With no key, or with a key that the gateway does not know, the text prints a new key
#   and nothing else. An older key with the same alias has no holder, so the text
#   removes it first.
gateway_key_py='
import json
import os
import sys
import urllib.error
import urllib.request

alias, models = sys.argv[1], json.loads(sys.argv[2])
master = os.environ.get("LITELLM_MASTER_KEY", "")
if not master:
    sys.exit("the container of the gateway has no master key")
present = sys.stdin.read().strip()


def call(path, body):
    request = urllib.request.Request(
        "http://127.0.0.1:4000" + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + master, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            return answer.status, json.loads(answer.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as error:
        return error.code, None
    except (OSError, ValueError):
        sys.exit("the gateway does not answer on %s" % path)


if present:
    # The key goes in the body of the request, so no log line of the gateway holds it.
    status, data = call("/key/update", {"key": present, "models": models})
    if status == 200:
        print("kept")
        sys.exit(0)
call("/key/delete", {"key_aliases": [alias]})
status, data = call("/key/generate", {"key_alias": alias, "models": models,
                                      "allowed_routes": ["llm_api_routes"]})
if status != 200 or not isinstance(data, dict) or not data.get("key"):
    sys.exit("the gateway answers the HTTP status %s to /key/generate" % status)
print(data["key"])
'

# The text that makes the user key of the agent. It runs in the container of OpenViking,
# which holds the root key in its environment. Arguments: the account, its first admin
# user, the user of the agent. It prints the new user key and nothing else. The agent
# gets the role `user`. The key of the admin user is not kept.
openviking_key_py='
import json
import os
import sys
import urllib.error
import urllib.request

account, admin, user = sys.argv[1:4]
root = os.environ.get("OPENVIKING_ROOT_API_KEY", "")
if not root:
    sys.exit("the container of OpenViking has no root key")
base = "http://127.0.0.1:1933/api/v1/admin/accounts"


def call(url, body):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"X-API-Key": root, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            data = json.loads(answer.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except (OSError, ValueError):
        sys.exit("OpenViking does not answer")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return 200, ""
    return 200, result.get("user_key") or ""


# The account can exist from an earlier run. Then the call fails, and the next call works.
call(base, {"account_id": account, "admin_user_id": admin})
status, key = call("%s/%s/users" % (base, account), {"user_id": user, "role": "user"})
if not key:
    # The user exists from an earlier run. A new key takes the place of the old one.
    status, key = call("%s/%s/users/%s/key" % (base, account, user), {})
if not key:
    sys.exit("OpenViking answers the HTTP status %s and gives no user key" % status)
print(key)
'

# Isolated mode: Python loads no module from the current directory.
helper() {
    python3 -I -c "$helper_py" "$@"
}

dry_run=0
check_forms=
stack_env=
root=
while [ $# -gt 0 ]; do
    case $1 in
        -h | --help) usage; exit 0 ;;
        --dry-run) dry_run=1; shift ;;
        --check-forms) [ $# -ge 2 ] && [ -n "$2" ] || { usage >&2; exit 2; }; check_forms=$2; shift 2 ;;
        --stack-env) [ $# -ge 2 ] || { usage >&2; exit 2; }; stack_env=$2; shift 2 ;;
        --root) [ $# -ge 2 ] || { usage >&2; exit 2; }; root=${2%/}; shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done

repo=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
if [ -n "$check_forms" ]; then
    helper forms "$check_forms" "$repo"
    exit 0
fi
[ -n "$stack_env" ] || stack_env=$root/etc/tenant-ec2/stack.env
data=$root/srv/data
efs=$root/srv/efs
lock=$repo/kits.lock
sample=$repo/.env.example
compose_file=$repo/compose.yaml
rc=0
made=
kept=
absent=
work=
edge_ready=0
edge_changed=0
# The plan of the options: step 2 sets these names from EC2_OPTIONS (load_plan).
plan_bad=0
plan_options=
plan_files="compose.yaml compose.caddy.yaml"
plan_compose_file=compose.yaml:compose.caddy.yaml
opt_pangolin=0
opt_copilot=0
plan_openviking_models=
plan_pi_durable_models=

# Remove the short-lived files of this run at each end of the script.
trap '[ -z "$work" ] || rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# run <command> <argument>...: run a command that changes the host. In a dry run, print
# it. No argument of such a command is a value of the environment file.
run() {
    if [ "$dry_run" = 1 ]; then
        say "would run: $*"
        return 0
    fi
    "$@"
}

# need <command>...: stop when a command is missing. A dry run prints one line.
need() {
    for tool in "$@"; do
        command -v "$tool" >/dev/null 2>&1 && continue
        [ "$dry_run" = 1 ] || die "the command $tool is missing"
        say "note: the command $tool is missing here; the host needs it"
    done
}

# stack_value <name>: the value of one name of the stack values file. The file is read
# as text and never run as a script.
stack_value() {
    awk -v name="$1" 'index($0, name "=") == 1 { value = substr($0, length(name) + 2) } END { print value }' "$stack_env"
}

# env_has <name>: the environment file gives the name a value.
env_has() {
    [ -f "$env_file" ] && helper has "$env_file" "$1"
}

# in_list <word> <list>
in_list() {
    case " $2 " in
        *" $1 "*) return 0 ;;
    esac
    return 1
}

# image_id <image>: the id of a local image, or nothing.
image_id() {
    docker image inspect --format '{{.Id}}' "$1" 2>/dev/null || true
}

# image_commit <image>: the commit in the label org.opencontainers.image.revision of a
# local image, or nothing. The build of a kit can write the commit of its clone there.
# Only a text of 40 hexadecimal digits is a commit.
image_commit() {
    docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1" 2>/dev/null |
        grep -Ex '[0-9a-f]{40}' || true
}

# compose <argument>...: one Compose command on the stack, with the Compose files of the
# plan. The command names each file, so it does not need COMPOSE_FILE of the environment
# file. Compose prefers a value of the shell over the environment file, so the command
# gets an empty environment. A name of MADE_NAMES that has no value yet gets the stand-in
# value: a Compose file refuses an empty value of each name that it reads, also for a
# service that the command does not start.
compose() {
    # The durable mounts come before each Compose command, for each file set: a service
    # must not start on a directory of the root disk.
    if [ "$dry_run" = 0 ] && [ -z "$root" ]; then
        for dir in "$data" "$efs/open-knowledge" "$efs/openviking" "$efs/shared"; do
            mountpoint -q "$dir" || die "$dir is not a mount point: no Compose command"
        done
    fi
    set -- --env-file "$env_file" "$@"
    # The files go before the arguments in the order of the plan, so the last one is first.
    reversed=
    for file in $plan_files; do
        reversed="$file $reversed"
    done
    for file in $reversed; do
        set -- -f "$repo/$file" "$@"
    done
    set -- docker compose --project-directory "$repo" "$@"
    for made_name in $MADE_NAMES; do
        env_has "$made_name" || set -- "$made_name=$STUB" "$@"
    done
    env -i PATH="$PATH" HOME="${HOME:-/root}" "$@"
}

# running: the services of the stack that run now, one line each.
running() {
    [ -f "$env_file" ] && command -v docker >/dev/null 2>&1 || return 0
    compose ps --status running --services 2>/dev/null || true
}

# up <service>...: start a group of services and wait for the health of each one. Compose
# changes nothing for a service that runs with the same configuration. It removes each
# container of a service that no active Compose file holds: after a change of
# EC2_OPTIONS, the other edge must go before the new edge takes the ports.
up() {
    if [ "$dry_run" = 1 ]; then
        todo=
        for service in "$@"; do
            in_list "$service" "$now" || todo="$todo $service"
        done
        if [ -z "$todo" ]; then
            skip "each service of this group runs: $*"
        else
            say "would run: docker compose up -d --remove-orphans --wait $* (not running now:$todo)"
        fi
        return 0
    fi
    say "start and wait for the health of: $*"
    compose up -d --remove-orphans --wait --wait-timeout "$WAIT" "$@" </dev/null
}

# ----- Step 1: the stack values -----

stack_values() {
    say "step 1 of 9: stack values"
    [ -r "$stack_env" ] || die "the stack values file $stack_env is not there"
    project=$(stack_value EC2_PROJECT)
    region=$(stack_value EC2_AWS_REGION)
    secret_id=$(stack_value EC2_SECRET_ID)
    [ -n "$project" ] || project=tenant-ec2
    printf '%s\n' "$project" | grep -Eqx '[a-z][a-z0-9-]{2,30}' ||
        die "$stack_env: EC2_PROJECT is not a project name"
    printf '%s\n' "$region" | grep -Eqx '[a-z]{2}(-[a-z]+)+-[0-9]+' ||
        die "$stack_env does not set EC2_AWS_REGION to a region"
    printf '%s\n' "$secret_id" | grep -Eqx '[A-Za-z0-9/_+=.@-]+' ||
        die "$stack_env does not set EC2_SECRET_ID to the name of a secret"
    operations=$(stack_value EC2_ENABLE_OPERATIONS)
    [ -n "$operations" ] || operations=false
    case $operations in
        true | false) ;;
        *) die "$stack_env: EC2_ENABLE_OPERATIONS must be true or false" ;;
    esac
    log_group=$(stack_value EC2_LOG_GROUP)
    [ -n "$log_group" ] || log_group=/$project/host
    [ "$log_group" = "/$project/host" ] || die "$stack_env: EC2_LOG_GROUP must be /<project>/host"
    state=$data/$project
    env_file=$state/.env
    say "the project is $project, the region is $region, the secret is $secret_id"
    # Step 2 asks for the tools of the option pangolin, after it reads the options.
    need python3 git docker aws cmp iptables systemctl
    if [ "$dry_run" = 0 ]; then
        [ "$(id -u)" -eq 0 ] || die "run this script as root"
    fi
    if [ -n "$root" ]; then
        say "note: --root is set, so the mount points are not tested"
        return 0
    fi
    # Without the data volume or an access point, a directory would be on the root disk.
    for dir in "$data" "$efs/open-knowledge" "$efs/openviking" "$efs/shared"; do
        mountpoint -q "$dir" 2>/dev/null && continue
        [ "$dry_run" = 1 ] || die "$dir is not a mount point: scripts/host-userdata.sh mounts it"
        say "note: $dir is not a mount point here; a run on the host stops at this test"
    done
}

# ----- Step 2: the environment file -----

# load_plan: read the options of the environment file and set the names of the plan. With
# no environment file, the plan is the core: only a dry run comes that far without one.
load_plan() {
    assign=$(helper plan "$env_file") || exit 1
    eval "$assign"
    if [ "$plan_bad" = 1 ]; then
        say "note: EC2_OPTIONS of the file that is there now is refused; the dry run shows the core with no option"
    elif [ ! -f "$env_file" ]; then
        say "note: no environment file is there; the dry run shows the core with no option"
    fi
    say "the options are: ${plan_options:-none (the core only)}. COMPOSE_FILE is $plan_compose_file"
    [ "$opt_pangolin" = 0 ] || need envsubst modprobe
}

# edge_settings: test the settings of the active edge. Step 2 does it, so a missing
# setting stops the run before a build. A message names a name, never a value.
# - The core edge: caddy/start.sh is the command of the service `caddy`. It tests its
#   four settings at each start. Here it runs on the host with the same four names, and
#   it starts nothing. The names are no secret, and each value goes through the
#   environment, not through an argument.
# - The option pangolin: scripts/render-pangolin.sh renders the Pangolin files into the
#   private directory of this run. The files hold no secret. Step 6 compares these files
#   with the files of the data volume.
edge_settings() {
    if [ "$opt_pangolin" = 1 ]; then
        work_dir
        rm -rf "$work/pangolin"
        sh "$repo/scripts/render-pangolin.sh" --env-file "$env_file" --out "$work/pangolin" >/dev/null
        return
    fi
    (
        EC2_DOMAIN=$(helper get "$env_file" EC2_DOMAIN)
        EC2_EDGE_NAMES=$(helper get "$env_file" EC2_EDGE_NAMES)
        EC2_ADMIN_CIDR=$(helper get "$env_file" EC2_ADMIN_CIDR)
        EC2_ACME_EMAIL=$(helper get "$env_file" EC2_ACME_EMAIL)
        export EC2_DOMAIN EC2_EDGE_NAMES EC2_ADMIN_CIDR EC2_ACME_EMAIL
        sh "$repo/caddy/start.sh" --print >/dev/null
    )
}

environment() {
    say "step 2 of 9: environment file"
    if [ "$dry_run" = 1 ]; then
        say "would read $project/generated first, then $secret_id, and write $env_file (owner root, mode 0600)"
        if [ "$(readlink "$repo/.env" 2>/dev/null || true)" = "$env_file" ]; then
            skip "the link $repo/.env to that file is there"
        else
            say "would make the link $repo/.env to that file"
        fi
        load_plan
        if [ -f "$env_file" ]; then
            if [ "$(helper get "$env_file" COMPOSE_FILE)" = "$plan_compose_file" ]; then
                skip "the file that is there now has that value of COMPOSE_FILE"
            else
                say "would write COMPOSE_FILE=$plan_compose_file into $env_file"
            fi
            helper forms "$env_file" "$repo" || exit 1
            if text=$(helper check "$sample" "$repo" "$env_file"); then
                say "the file that is there now passes the test of the names of .env.example and of the active Compose files"
            else
                say "note: the file that is there now does not pass the test: $(printf '%s' "$text" | tr '\n' ';')"
            fi
            # The private directory is made here: a variable that a command substitution
            # sets is lost.
            [ "$opt_pangolin" = 0 ] || work_dir
            if text=$(edge_settings 2>&1); then
                edge_ready=1
                say "the file that is there now passes the test of the settings of the edge"
            else
                say "note: the file that is there now does not pass the test of the settings of the edge: $(printf '%s' "$text" | tr '\n' ';')"
            fi
        fi
        return 0
    fi
    install -d -m 0700 -o 0 -g 0 "$state"
    work_dir
    generated_file=$work/generated.json
    if ! (umask 077; python3 -I "$repo/scripts/generated-keys.py" read \
        --project "$project" --region "$region" >"$generated_file"); then
        printf '{}\n' >"$generated_file"
        say "Warning: $project/generated is unavailable; the environment file remains the fallback."
    fi
    # The value of the secret stays in one variable of this shell and goes to the helper
    # through a pipe. `printf` is a part of the shell, so no process list shows it.
    secret=$(aws secretsmanager get-secret-value --region "$region" --secret-id "$secret_id" \
        --query SecretString --output text --no-cli-pager) ||
        die "cannot read the secret $secret_id with the instance role"
    assign=$(umask 077 && printf '%s' "$secret" | helper render "$sample" "$repo" "$env_file" "$generated_file") || exit 1
    secret=
    render_count=0
    render_same=0
    render_kept=
    eval "$assign"
    kept=$render_kept
    chown 0:0 "$env_file"
    if [ "$render_same" = 1 ]; then
        skip "$env_file has the names and values of the secret and COMPOSE_FILE, $render_count names (owner root, mode 0600)"
    else
        say "wrote $env_file with $render_count names: the names of the secret and COMPOSE_FILE (owner root, mode 0600)"
    fi
    [ -z "$kept" ] || say "the secret has no value for: $kept. The file of the host keeps its own value."
    load_plan
    # A Compose command by hand from the clone reads `.env` of the clone. The link holds
    # no value, and .dockerignore keeps the name out of a build context. A file that is
    # not a link is not replaced.
    if [ -e "$repo/.env" ] && [ ! -L "$repo/.env" ]; then
        say "note: $repo/.env is a file, not a link: it stays. A Compose command by hand needs --env-file $env_file"
    else
        ln -sfn "$env_file" "$repo/.env"
    fi
    # The settings of the edge are names of Part 2. Without this test, a missing one
    # stops the run at step 6 or at the start of the edge, after each build.
    edge_settings || die "the settings of the edge are refused: set the name of the message above in the secret"
    edge_ready=1
}

# ----- Step 3: the directories of the data volume -----

# owner_of <path below /srv/data>: the owner and the mode of a directory that a Compose
# file binds. The list is the contract of the head of each Compose file.
owner_of() {
    case $1 in
        pangolin/config | pangolin/gerbil | pangolin/traefik/config | pangolin/traefik/letsencrypt) echo "0 0 0700" ;;
        litellm-db) echo "999 999 0700" ;;
        caddy | copilot | pi-durable) echo "1000 1000 0700" ;;
        *) return 1 ;;
    esac
}

# ensure_dir <directory> <uid> <gid> <mode>
ensure_dir() {
    if [ -d "$1" ] && [ "$(stat -c '%u %g %a' "$1")" = "$2 $3 ${4#0}" ]; then
        skip "$1 is there with the owner $2:$3 and the mode $4"
        return 0
    fi
    run install -d -m "$4" -o "$2" -g "$3" "$1"
    [ "$dry_run" = 1 ] || say "$1 has the owner $2:$3 and the mode $4 now"
}

directories() {
    say "step 3 of 9: directories of the data volume"
    ensure_dir "$state" 0 0 0700
    if [ "$opt_pangolin" = 1 ]; then
        ensure_dir "$data/pangolin" 0 0 0700
        ensure_dir "$data/pangolin/traefik" 0 0 0700
    fi
    # Only the directories of the active Compose files: a file that is off binds nothing.
    sources=$(for file in $plan_files; do
        awk '$1 == "source:" { print $2 }' "$repo/$file"
    done | sort -u)
    for source in $sources; do
        case $source in
            /srv/data/*)
                spec=$(owner_of "${source#/srv/data/}") ||
                    die "a Compose file binds $source, and this script has no owner for it"
                # shellcheck disable=SC2086 # the three words of the list
                ensure_dir "$root$source" $spec
                ;;
            /srv/efs/*)
                # The access point owns its directory. This script makes nothing there.
                if [ -d "$root$source" ]; then
                    skip "$root$source is an access point: nothing to make"
                elif [ "$dry_run" = 1 ]; then
                    say "note: $root$source is not there; a run on the host stops at this test"
                else
                    die "$source is not there: scripts/host-userdata.sh mounts each access point"
                fi
                ;;
            *) die "a Compose file binds $source: a path outside /srv/data and /srv/efs" ;;
        esac
    done
}

# ----- Step 4: the settings of the host -----

# gateway_address: the fixed address of the service litellm on the network egress, as
# compose.yaml gives it. The text is read by its indentation: Compose is not started.
gateway_address() {
    awk '
        /^[^ #]/ { top = $1; service = ""; key = ""; network = "" }
        top != "services:" { next }
        /^  [^ #]/ { service = $1; key = ""; network = "" }
        service != "litellm:" { next }
        /^    [^ #]/ { key = $1; network = "" }
        key != "networks:" { next }
        /^      [^ #]/ { network = $1 }
        network == "egress:" && $1 == "ipv4_address:" { print $2 }
    ' "$compose_file"
}

# same_text <wanted text> <file>: the file holds the text and one line end.
same_text() {
    [ -f "$2" ] && [ "$(cat "$2")" = "$1" ]
}

# Keep this drop-in equal to the first-boot gate in scripts/host-userdata.sh.
# The mountpoint prechecks run at EVERY Docker start, including a daemon auto-restart.
storage_dependencies() {
    storage_conf=$root/etc/systemd/system/docker.service.d/tenant-ec2-storage.conf
    storage_unit='[Unit]
RequiresMountsFor=/srv/data /srv/efs/open-knowledge /srv/efs/openviking /srv/efs/shared
BindsTo=srv-data.mount srv-efs-open\x2dknowledge.mount srv-efs-openviking.mount srv-efs-shared.mount
Requires=tenant-ec2-metadata-guard.service
After=srv-data.mount srv-efs-open\x2dknowledge.mount srv-efs-openviking.mount srv-efs-shared.mount tenant-ec2-metadata-guard.service

[Service]
ExecStartPre=/usr/bin/mountpoint -q /srv/data
ExecStartPre=/usr/bin/mountpoint -q /srv/efs/open-knowledge
ExecStartPre=/usr/bin/mountpoint -q /srv/efs/openviking
ExecStartPre=/usr/bin/mountpoint -q /srv/efs/shared'
    if same_text "$storage_unit" "$storage_conf"; then
        skip "Docker requires all four durable mounts and the metadata guard"
    elif [ "$dry_run" = 1 ]; then
        say "would install $storage_conf and run: systemctl daemon-reload"
    else
        install -d -m 0755 "${storage_conf%/*}"
        printf '%s\n' "$storage_unit" >"$storage_conf"
        chmod 0644 "$storage_conf"
        systemctl daemon-reload
    fi
}

host_settings() {
    say "step 4 of 9: host settings"
    storage_dependencies

    # Gerbil has no right to load a kernel module (compose.pangolin.yaml), so the host
    # loads it. Only the option pangolin has a tunnel.
    conf=$root/etc/modules-load.d/wireguard.conf
    if [ "$opt_pangolin" != 1 ]; then
        skip "the option pangolin is off: the kernel module wireguard is not loaded"
    elif [ -f "$conf" ] && [ "$(cat "$conf")" = wireguard ] && [ -d "$root/sys/module/wireguard" ]; then
        skip "the kernel module wireguard is loaded, and $conf is there"
    elif [ "$dry_run" = 1 ]; then
        say "would write $conf and run: modprobe wireguard"
    else
        install -d -m 0755 "$root/etc/modules-load.d"
        printf 'wireguard\n' >"$conf"
        modprobe wireguard
        say "the kernel module wireguard is loaded"
    fi

    # The metadata guard: scripts/host-metadata-guard.sh says what it does and why.
    # The exception is the fixed address that compose.yaml gives the gateway.
    gateway=$(gateway_address)
    octet='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
    printf '%s\n' "$gateway" | grep -Eqx "$octet\\.$octet\\.$octet\\.$octet" ||
        die "compose.yaml does not give the service litellm one fixed IPv4 address on the network egress"
    bin=$root/usr/local/sbin/$GUARD
    unit_dir=$root/etc/systemd/system
    # Docker needs the unit (RequiredBy): when the rules cannot be made, Docker does not
    # start, so no container runs with an open metadata address.
    unit="[Unit]
Description=tenant-ec2: close the instance metadata address for the containers
Before=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/$GUARD apply $gateway

[Install]
RequiredBy=docker.service"
    # Each test of systemctl names one unit: with two names, one enabled unit is a pass.
    if [ -f "$bin" ] && cmp -s "$repo/scripts/host-metadata-guard.sh" "$bin" &&
        same_text "$unit" "$unit_dir/$GUARD.service" &&
        systemctl is-enabled --quiet "$GUARD.service" 2>/dev/null &&
        systemctl is-active --quiet "$GUARD.service" 2>/dev/null &&
        sh "$repo/scripts/host-metadata-guard.sh" check "$gateway" 2>/dev/null; then
        skip "the metadata guard is installed, and its rules are there: only the gateway reaches the metadata address"
    elif [ "$dry_run" = 1 ]; then
        say "would install $bin and the unit $GUARD.service (the exception is the address $gateway of the gateway on the network egress)"
        say "would run: systemctl daemon-reload; systemctl enable $GUARD.service; $bin apply $gateway; systemctl start $GUARD.service"
    else
        install -d -m 0755 "$root/usr/local/sbin" "$unit_dir"
        install -m 0755 "$repo/scripts/host-metadata-guard.sh" "$bin"
        printf '%s\n' "$unit" >"$unit_dir/$GUARD.service"
        chmod 0644 "$unit_dir/$GUARD.service"
        systemctl daemon-reload
        systemctl enable "$GUARD.service"
        # The rules change here, at once. The unit is not started again when it is
        # active: Docker needs the unit, so systemd would start Docker again with it.
        "$bin" apply "$gateway"
        systemctl start "$GUARD.service"
        say "the metadata guard is installed: only the gateway reaches the metadata address"
    fi

    # Never restart Docker for a log-driver change before both guards are installed.
    json=$root/etc/docker/daemon.json
    code=0
    helper daemon "$json" check || code=$?
    case $code in
        0) skip "$json sets the log driver journald" ;;
        3) if [ "$dry_run" = 1 ]; then
               say "would set the log driver journald in $json and run: systemctl restart docker"
           else
               install -d -m 0755 "$root/etc/docker"
               helper daemon "$json" write
               systemctl restart docker
               say "the log driver is journald; Docker is started again"
           fi ;;
        *) exit 1 ;;
    esac
    run systemctl enable --now docker
}

# operations_logs: the agent reads rotating JSON files, never the binary journal.
operations_logs() {
    export_unit=$project-journal-export
    agent_config=$root/etc/$project/cloudwatch-agent.json
    if [ "$operations" = false ]; then
        if [ -f "$agent_config" ]; then
            run systemctl disable --now "$export_unit.timer"
            # The configuration can remain after the agent package is removed.
            if [ "$dry_run" = 1 ] ||
                [ "$(systemctl show --property=LoadState --value amazon-cloudwatch-agent 2>/dev/null)" != not-found ]; then
                run systemctl disable --now amazon-cloudwatch-agent
            fi
        fi
        skip "operations are disabled by the stack values"
        return 0
    fi
    if [ "$dry_run" = 1 ]; then
        say "would install amazon-cloudwatch-agent and the journal exporter $export_unit"
        say "would export Docker and system journal records to rotating files before starting amazon-cloudwatch-agent ($log_group)"
        return 0
    fi
    command -v journalctl >/dev/null 2>&1 || die "journalctl is missing"
    agent_ctl=$root/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl
    [ -x "$agent_ctl" ] || dnf install -y amazon-cloudwatch-agent
    install -d -m 0755 "$root/usr/local/sbin"
    install -m 0755 "$repo/scripts/host-journal-export.py" "$root/usr/local/sbin/$export_unit"
    python3 -I "$repo/scripts/host-journal-export.py" --configure --root "${root:-/}" \
        --project "$project" --region "$region" --log-group "$log_group"
    systemctl daemon-reload
    # Export existing records first. A failed exporter prevents the agent start.
    systemctl start "$export_unit.service"
    systemctl enable --now "$export_unit.timer"
    config_hash=$(sha256sum "$agent_config" | cut -d' ' -f1)
    applied=$root/var/lib/$project/cloudwatch-config.sha256
    if same_text "$config_hash" "$applied" && systemctl is-active --quiet amazon-cloudwatch-agent; then
        skip "the CloudWatch agent runs with this file configuration"
    else
        "$agent_ctl" -a fetch-config -m ec2 -s -c "file:$agent_config"
        printf '%s\n' "$config_hash" >"$applied"
    fi
    systemctl enable amazon-cloudwatch-agent
}

# work_dir: make the private directory for the short-lived files of this run.
work_dir() {
    [ -n "$work" ] || work=$(mktemp -d "${TMPDIR:-/tmp}/host-bootstrap.XXXXXX")
}

# ----- Step 5: the kits -----

# clone_kit: clone the kit of the variables kit, dir, kit_source_*, source_name and
# token_name into a new directory, compare the commit, then put it in place.
clone_kit() {
    if [ -n "$source_name" ]; then
        source=$(helper get "$env_file" "$source_name")
    else
        source=$kit_source_repo
    fi
    case $source in
        https://* | /*) ;;
        *) die "the source of the kit $kit is not an https URL" ;;
    esac
    if printf '%s\n' "$source" | grep -Eq '^https://[^/]*@'; then
        die "the source of the kit $kit holds a user name or a credential: use a URL with none"
    fi
    token=
    ! env_has "$token_name" || token=$(helper get "$env_file" "$token_name")
    # Git asks this helper for a credential only when the source is private. The helper
    # holds no token: the token is in the environment of the one Git command.
    work_dir
    (umask 077 && cat >"$work/askpass") <<'EOT'
#!/bin/sh
case $1 in
    Username*) echo x-access-token ;;
    *) printf '%s\n' "${HOST_BOOTSTRAP_GIT_TOKEN:-}" ;;
esac
EOT
    chmod 0700 "$work/askpass"
    new=$dir.new.$$
    rm -rf "$new"
    mkdir -p "$repo/kits"
    code=0
    HOST_BOOTSTRAP_GIT_TOKEN=$token GIT_ASKPASS=$work/askpass GIT_TERMINAL_PROMPT=0 \
        git -c credential.helper= -c advice.detachedHead=false clone --quiet --depth 1 \
        --branch "$kit_source_tag" -- "$source" "$new" </dev/null || code=$?
    rm -f "$work/askpass"
    token=
    [ "$code" -eq 0 ] || { rm -rf "$new"; die "the clone of the kit $kit at the tag $kit_source_tag failed"; }
    # A tag can move. No build runs before this test.
    head=$(git -C "$new" rev-parse HEAD)
    if [ "$head" != "$kit_source_commit" ]; then
        rm -rf "$new"
        die "the tag $kit_source_tag of the kit $kit is at the commit $head, and kits.lock gives $kit_source_commit: stop"
    fi
    rm -rf "$dir"
    mv "$new" "$dir"
    say "the kit $kit is cloned at the tag $kit_source_tag, commit $head"
}

one_kit() {
    kit=$1
    assign=$(helper kit "$lock" "$kit") || exit 1
    kit_source_repo=
    kit_source_tag=
    kit_source_commit=
    kit_image=
    kit_image_digest=
    kit_build=
    kit_service=
    eval "$assign"
    dir=$repo/kits/$kit
    upper=$(printf '%s' "$kit" | tr 'a-z-' 'A-Z_')
    token_name=EC2_KIT_TOKEN_$upper
    source_name=
    ! env_has "EC2_KIT_SOURCE_$upper" || source_name=EC2_KIT_SOURCE_$upper

    if [ -z "$kit_source_tag" ] || [ -z "$kit_source_commit" ]; then
        say "the kit $kit has no tag or no commit in kits.lock: no clone and no build. The service $kit_service is not started."
        absent="$absent $kit_service"
        return 0
    fi
    printf '%s\n' "$kit_source_commit" | grep -Eqx '[0-9a-f]{40}' ||
        die "kits.lock: source_commit of the kit $kit is not 40 hexadecimal digits"
    printf '%s\n' "$kit_source_tag" | grep -Eqx '[A-Za-z0-9][A-Za-z0-9._/-]*' ||
        die "kits.lock: source_tag of the kit $kit is not a tag name"
    [ -n "$kit_image" ] || die "kits.lock: the kit $kit has no image"
    if [ -z "$kit_source_repo" ] && [ -z "$source_name" ]; then
        say "the kit $kit has no source: kits.lock has no source_repo, and EC2_KIT_SOURCE_$upper has no value. No clone and no build. The service $kit_service is not started."
        absent="$absent $kit_service"
        return 0
    fi

    head=
    [ ! -d "$dir/.git" ] || head=$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)
    if [ "$head" = "$kit_source_commit" ]; then
        skip "the clone of the kit $kit is at the commit of kits.lock"
    elif [ "$dry_run" = 1 ]; then
        if [ -n "$source_name" ]; then
            say "would clone the kit $kit from the source of $source_name at the tag $kit_source_tag and compare the commit with kits.lock"
        else
            say "would clone the kit $kit from $kit_source_repo at the tag $kit_source_tag and compare the commit with kits.lock"
        fi
    else
        clone_kit
    fi
    # From here the clone is at the commit of kits.lock: a run on the host stops when it
    # is not. The image, the build record and the label decide about the build.

    # The build record: the commit and the image id of the last build of this kit. The
    # image id alone does not say from which commit the image is: the name of an image
    # can stay the same for a new commit.
    record=$state/builds/$kit
    built_commit=
    built_id=
    [ ! -f "$record" ] || read -r built_commit built_id <"$record" || true
    have=$(image_id "$kit_image")
    labelled=
    [ -z "$have" ] || labelled=$(image_commit "$kit_image")
    if [ -z "$have" ]; then
        why="no image $kit_image is there"
    elif [ "$have" != "$kit_image_digest" ]; then
        why="the id of the image $kit_image is not the id of kits.lock"
    elif [ "$built_commit" != "$kit_source_commit" ] || [ "$built_id" != "$have" ]; then
        why="no build record says that the image $kit_image is a build of the commit of kits.lock"
    elif [ -n "$labelled" ] && [ "$labelled" != "$kit_source_commit" ]; then
        why="the label of the image $kit_image names another commit"
    else
        skip "the image $kit_image of the kit $kit is there with the id of kits.lock, and the build record names the commit of kits.lock"
        return 0
    fi
    if [ "$dry_run" = 1 ]; then
        say "would build the kit $kit in kits/$kit with: $kit_build ($why)"
        say "would write the id of the image $kit_image into image_digest of the kit $kit in $lock, and the commit and the id into $record"
        return 0
    fi
    [ -n "$kit_build" ] || die "kits.lock: the kit $kit has no build command"
    [ -z "$(git -C "$dir" status --porcelain)" ] || die "the clone of the kit $kit has a local change: no build"
    docker buildx version >/dev/null 2>&1 || die "the buildx plugin of Docker is missing: the build of a kit needs it"
    say "build the kit $kit ($why): $kit_build"
    # An empty environment: no variable of this shell changes a pin of the kit.
    (cd "$dir" && env -i PATH="$PATH" HOME="${HOME:-/root}" GATEWAY_IMAGE="$kit_image" sh -c "$kit_build" </dev/null) ||
        die "the build of the kit $kit failed"
    new_id=$(image_id "$kit_image")
    [ -n "$new_id" ] || die "the build of the kit $kit made no image with the name $kit_image"
    printf '%s\n' "$new_id" | grep -Eqx 'sha256:[0-9a-f]{64}' ||
        die "the image id of the kit $kit does not have the form sha256:<64 digits>"
    # An older image can have the name of kits.lock while the build gives its image
    # another name. Then the label of the older image names another commit.
    labelled=$(image_commit "$kit_image")
    [ -z "$labelled" ] || [ "$labelled" = "$kit_source_commit" ] ||
        die "after the build of the kit $kit, the label of the image $kit_image names another commit: the build made no image with that name. Correct the image name of the kit in kits.lock"
    install -d -m 0700 "$state/builds"
    printf '%s %s\n' "$kit_source_commit" "$new_id" >"$record.tmp.$$"
    mv -f "$record.tmp.$$" "$record"
    helper lock-set "$lock" "$kit" "$new_id"
    [ -z "$kit_image_digest" ] || say "the image id of the kit $kit is different from the id that kits.lock had"
    say "the image $kit_image has the id $new_id; $lock and $record record it"
}

kits() {
    say "step 5 of 9: kits"
    names=$(helper kits "$lock") || exit 1
    [ -n "$names" ] || die "$lock has no kit in the block server_kits"
    # One kit at a time: the host has no memory for two builds.
    for kit_name in $names; do
        one_kit "$kit_name"
    done
    [ -z "$absent" ] || say "not built, so not started:$absent"
}

# ----- Step 6: the configuration of the edge -----

edge_configuration() {
    say "step 6 of 9: edge configuration"
    edge_changed=0
    if [ "$opt_pangolin" != 1 ]; then
        skip "the option pangolin is off: no Pangolin configuration. The core edge needs no file: caddy/start.sh makes its routes at each start"
        return 0
    fi
    out=$data/pangolin
    if [ "$edge_ready" != 1 ]; then
        # Only a dry run comes here, with no environment file or with a file that the
        # test of step 2 refuses. A run on the host has the files from step 2.
        say "would run: sh scripts/render-pangolin.sh --env-file $env_file --out $out"
        return 0
    fi
    # Step 2 rendered the files into the private directory of this run. Compare them:
    # a run that changes nothing writes nothing, and the edge services start again only
    # when one of their files is different.
    same=1
    for file in config/config.yml traefik/config/traefik_config.yml traefik/config/dynamic_config.yml; do
        cmp -s "$work/pangolin/$file" "$out/$file" 2>/dev/null || edge_changed=1
    done
    cmp -s "$work/pangolin/blueprint.yaml" "$out/blueprint.yaml" 2>/dev/null || same=0
    if [ "$edge_changed" = 0 ] && [ "$same" = 1 ]; then
        skip "the four Pangolin files below $out are there with the same content"
    elif [ "$dry_run" = 1 ]; then
        say "would run: sh scripts/render-pangolin.sh --env-file $env_file --out $out (a file there is missing or different)"
    else
        sh "$repo/scripts/render-pangolin.sh" --env-file "$env_file" --out "$out"
    fi
}

# ----- Step 7: the start of the stack and the keys -----

# gateway_key <name> <service> <models as a JSON list>: the virtual key of a service, with
# the routes of the active Compose files as its model list. A key that the environment
# file holds goes to the gateway through a pipe; the gateway then sets its model list.
gateway_key() {
    if [ "$dry_run" = 1 ]; then
        if env_has "$1"; then
            say "would set the model list of the virtual key of $2 on the gateway to $3 ($1 has a value)"
        else
            say "would make the virtual key of $2 on the gateway (alias $2, models $3, model routes only) and write it into $1"
        fi
        return 0
    fi
    code=0
    { ! env_has "$1" || helper get "$env_file" "$1"; } |
        compose exec -T litellm python -I -S -c "$gateway_key_py" "$2" "$3" |
        helper put "$env_file" "$1" kept || code=$?
    case $code in
        0) made="$made $1"
           say "made the virtual key of $2 (models $3) and wrote it into $1" ;;
        3) skip "$1 has a value: the virtual key of $2 is there, and its model list is $3" ;;
        *) die "the gateway gave no virtual key for $2" ;;
    esac
}

openviking_key() {
    name=EC2_PI_DURABLE_OPENVIKING_API_KEY
    if env_has "$name"; then
        skip "$name has a value: the OpenViking key of the agent is there"
        return 0
    fi
    if [ "$dry_run" = 1 ]; then
        say "would make the account $OV_ACCOUNT and the user $OV_USER (role user) in OpenViking and write the user key into $name"
        return 0
    fi
    compose exec -T openviking python -I -c "$openviking_key_py" "$OV_ACCOUNT" "$OV_ADMIN" "$OV_USER" </dev/null |
        helper put "$env_file" "$name" || die "OpenViking gave no user key for the agent"
    made="$made $name"
    say "made the OpenViking user key of the agent and wrote it into $name"
}

generated_writeback() {
    if [ "$dry_run" = 1 ]; then
        say "would save changed generated keys in $project/generated; a failed put keeps the environment file"
        return 0
    fi
    helper generated "$env_file" |
        python3 -I "$repo/scripts/generated-keys.py" put --project "$project" --region "$region" \
            --previous "$generated_file" ||
        say "Warning: $project/generated is not saved; keep the environment file and repeat the bootstrap."
}

start() {
    say "step 7 of 9: start of the stack and the keys"
    for service in litellm openviking; do
        ! in_list "$service" "$absent" || die "the service $service has no image, so the stack cannot start"
    done
    # The services of the active Compose files.
    if [ "$opt_pangolin" = 1 ]; then
        edge_services="pangolin gerbil traefik"
    else
        edge_services=caddy
    fi
    gateway_services="litellm-db litellm"
    [ "$opt_copilot" = 0 ] || gateway_services="$gateway_services copilot"
    now=$(running | tr '\n' ' ')
    # The edge first: its services own the public ports. The core edge asks for its
    # certificates at its start, so each public name needs its DNS record before.
    # shellcheck disable=SC2086 # the words of the list
    up $edge_services
    if [ "$edge_changed" = 1 ] && in_list pangolin "$now"; then
        # The services of the Pangolin edge read their files at the start only.
        if [ "$dry_run" = 1 ]; then
            say "the Pangolin configuration would change: would run docker compose restart pangolin gerbil traefik"
        else
            say "the Pangolin configuration changed: start the edge services again"
            compose restart pangolin gerbil traefik </dev/null
        fi
    fi
    # shellcheck disable=SC2086 # the words of the list
    up $gateway_services
    gateway_key EC2_OPENVIKING_LITELLM_API_KEY openviking "$plan_openviking_models"
    gateway_key EC2_PI_DURABLE_LITELLM_API_KEY pi-durable "$plan_pi_durable_models"
    # OpenViking before the agent: the key of the agent must exist before the agent starts.
    up openviking
    openviking_key
    generated_writeback
    # The rest. A service whose key is new starts with it: Compose makes a container
    # again when its environment changed.
    rest=
    for service in $edge_services $gateway_services openviking open-knowledge ok-auth pi-durable; do
        in_list "$service" "$absent" || rest="$rest $service"
    done
    # shellcheck disable=SC2086 # the words of the list
    up $rest
}

# ----- Step 8: the blueprint -----

apply_blueprint() {
    say "step 8 of 9: blueprint"
    if [ "$opt_pangolin" != 1 ]; then
        skip "the option pangolin is off: no blueprint. The core edge has its routes from EC2_EDGE_NAMES"
        return 0
    fi
    if ! env_has EC2_PANGOLIN_API_KEY; then
        skip "EC2_PANGOLIN_API_KEY has no value: set up Pangolin first (step 9), then run this script again"
        return 0
    fi
    if ! env_has EC2_PANGOLIN_ORG; then
        skip "EC2_PANGOLIN_ORG has no value: the apply needs the identifier of the organization"
        return 0
    fi
    if [ "$dry_run" = 1 ]; then
        say "would apply $data/pangolin/blueprint.yaml through the integration API of Pangolin (a second apply gives the same resources)"
        return 0
    fi
    # The request goes to curl in the container of Pangolin through a pipe: the key is in
    # no argument. The answer is the HTTP status only.
    status=$(helper blueprint "$env_file" "$data/pangolin/blueprint.yaml" |
        compose exec -T pangolin curl -sS -o /dev/null -w '%{http_code}' -K -) || status=${status:-none}
    case $status in
        200 | 201) say "the blueprint is applied" ;;
        *) say "error: the apply of the blueprint failed with the HTTP status $status"
           rc=1 ;;
    esac
}

# ----- Step 9: the next steps -----

next_steps() {
    say "step 9 of 9: next steps for the owner"
    litellm_image=
    if assign=$(helper kit "$lock" litellm 2>/dev/null); then
        kit_image=
        eval "$assign"
        litellm_image=$kit_image
    fi
    # The names that the host has and the secret lacks: made in this run, or kept from
    # an earlier run.
    # shellcheck disable=SC2086 # the words of the two lists
    new_names=$(printf '%s\n' $made $kept | sort -u | tr '\n' ' ')
    if [ -n "${new_names% }" ]; then
        say "- Generated names: ${new_names% }. Check write-back with scripts/aws-host.sh generated-status."
        say "- Preserve approved external copies through a private Secrets Manager read, never through SSM output."
        say "- A failed write-back keeps $env_file. Repeat the bootstrap after the secret and its rights are ready."
    fi
    # Only the steps of the active options follow.
    if [ "$opt_pangolin" = 0 ]; then
        say "- DNS: the core edge gets a certificate for a public name only when the name has an A record to the address of the host. The names are the names of EC2_EDGE_NAMES below EC2_DOMAIN. On the development machine: scripts/aws-host.sh dns."
    fi
    if [ "$opt_copilot" = 1 ]; then
        if [ -f "$data/copilot/access-token" ]; then
            skip "the Copilot token files are in $data/copilot"
        else
            say "- Copilot login, one time, from a shell on the host: cd $repo/kits/litellm && COPILOT_TOKEN_DIR=/srv/data/copilot LITELLM_IMAGE=$litellm_image sh scripts/login-copilot.sh login. Approve the code in a browser. Then: docker compose up -d --force-recreate copilot (from $repo)."
        fi
    fi
    if [ "$opt_pangolin" = 1 ]; then
        if env_has EC2_PANGOLIN_API_KEY; then
            skip "EC2_PANGOLIN_API_KEY has a value: Pangolin is set up"
        else
            say "- Pangolin, one time, from the administrator range: open https://pangolin.<EC2_DOMAIN>/auth/initial-setup and make the administrator and the organization. Make one site of the type local with the identifier of EC2_PANGOLIN_SITE. Make one API key with the right to apply a blueprint. Put the key into EC2_PANGOLIN_API_KEY and the organization into EC2_PANGOLIN_ORG of .env on the development machine, write the secret, and run this script again."
        fi
        say "- Home site, when you have one: make one site of the type Newt in the dashboard of Pangolin, and run one Newt container at home with the id and the secret that the dashboard gives."
    fi
    say "- kits.lock: copy each image_digest of $lock into kits.lock of the repository."
    [ -z "$absent" ] || say "- Not started, because the kit has no pin or no source:$absent."
}

stack_values
environment
directories
host_settings
operations_logs
kits
edge_configuration
start
apply_blueprint
next_steps
if [ "$dry_run" = 1 ]; then
    say "dry run: nothing changed"
elif [ "$rc" -eq 0 ]; then
    say "done"
fi
exit "$rc"
