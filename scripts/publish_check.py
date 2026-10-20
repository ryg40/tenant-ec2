#!/usr/bin/env python3
"""Check the reviewed publish set of this repository; never traverse private inputs.

The publish set is the explicit files of `PUBLISH` and the files of the directory rules of
`PUBLISH_DIRS`, without the optional private exclusions. The check prints one line
`<finding>: <path>` for each finding and exits with 1. See docs/publishing.md.
"""
import hashlib
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Invalid(Exception):
    """One finding of the check: `<finding>: <path or detail>`."""


def fail(code, detail):
    raise Invalid(f"{code}: {detail}")


# Exact paths, not extensions or a recursive glob. Update only after source review.
PUBLISH = (
    "README.md", "AGENTS.md", "GLOSSARY.md",
    ".env.example", ".gitignore", ".dockerignore", ".gitleaks.toml",
    "kits.lock",
    "config/memory/dev.yaml", "config/memory/test.yaml", "config/memory/prod.yaml",
    "config/macos/aws-node.config", "config/macos/aws-python.config",
    "config/macos/credential-process.mjs", "config/macos/credential-process.py",
    "config/macos/launchd/credentials.plist.in",
    "compose.macos.yaml", "ok-auth/oauth2-proxy.macos.cfg",
    "config/macos/README.md", "config/macos/bootstrap-forms.py", "config/macos/bootstrap-runtime.py",
    "config/macos/machine.json", "config/macos/runtime.lock.json", "config/macos/launchd/bootstrap.plist.in",
    "compose.yaml", "compose.caddy.yaml", "compose.pangolin.yaml", "compose.copilot.yaml",
    "caddy/start.sh", "caddy/common.caddy", "caddy/options.caddy",
    "caddy/sites/agent.caddy", "caddy/sites/llm.caddy", "caddy/sites/ok.caddy", "caddy/sites/ov.caddy",
    "litellm/config.yaml", "litellm/config.copilot.yaml", "litellm/copilot.yaml", "openviking/ov.conf",
    "ok-auth/oauth2-proxy.cfg", "ok-auth/local-run.sh",
    "ok-auth/local-run/overlay.yaml", "ok-auth/local-run/overlay-stub-upstream.yaml",
    "ok-auth/local-run/probe.py", "ok-auth/local-run/stubs.py",
    "pangolin/blueprint.yaml", "pangolin/config/config.yml",
    "pangolin/config/traefik/traefik_config.yml", "pangolin/config/traefik/dynamic_config.yml",
    "skills/security-review/SKILL.md", "skills/security-review/security-review.md",
    "skills/security-review/install.sh", "skills/security-review/review.py",
    "skills/security-review/test-loaders.py",
    "docs/agents/issue-tracker.md", "docs/branches.md", "docs/remotes.md", "docs/publishing.md",
    "docs/secret-handling.md", "docs/credentials.md",
    "docs/install.md", "docs/variants.md", "docs/reference/host-template-macos.md",
    "docs/architecture.md", "docs/memory-stack.md", "docs/entra.md", "docs/security-review.md",
    "docs/reference/checks.md", "docs/reference/compose-stack.md", "docs/reference/deploy-script.md",
    "docs/reference/host-boot.md", "docs/reference/host-template.md",
    "docs/reference/host-snapshot-move.md",
    "docs/reference/dns-migration.md",
    "docs/reference/host-domain-move.md",
    "docs/reference/memory-template.md",
    "docs/options/README.md", "docs/options/copilot.md", "docs/options/dns.md", "docs/options/home-lab.md",
    "docs/options/pangolin.md",
    "docs/diagrams/01-aws-core.excalidraw", "docs/diagrams/01-aws-core.json", "docs/diagrams/01-aws-core.svg",
    "docs/diagrams/02-services.excalidraw", "docs/diagrams/02-services.json", "docs/diagrams/02-services.svg",
    "docs/diagrams/03-entra-login.excalidraw", "docs/diagrams/03-entra-login.json",
    "docs/diagrams/03-entra-login.svg",
    "docs/diagrams/04-install-flow.excalidraw", "docs/diagrams/04-install-flow.json",
    "docs/diagrams/04-install-flow.svg",
    "docs/diagrams/05-options.excalidraw", "docs/diagrams/05-options.json", "docs/diagrams/05-options.svg",
    "docs/diagrams/architecture-graph.json", "docs/architecture.html",
    "docs/diagrams/aws-core.architecture.json", "docs/aws-core.html", "docs/diagrams/aws-core.svg",
    "docs/diagrams/aws-memory.architecture.json", "docs/aws-memory.html", "docs/diagrams/aws-memory.svg",
    "docs/diagrams/aws-mac.architecture.json", "docs/aws-mac.html", "docs/diagrams/aws-mac.svg",
    "docs/diagrams/memory-terminal.workflow.json", "docs/memory-terminal.html",
)
# Directory rules: (directory, technical excludes relative to that directory).
# A rule publishes each tracked file under the directory. Private-copy exclusions belong
# in the optional list, not in published source text.
PUBLISH_DIRS = (
    ("scripts", ()),
)
# Optional list of repository-relative files or directory prefixes ending in "/".
# The list excludes itself. A portable copy has neither the list nor its excluded files.
DEV_ONLY_REL = "scripts/dev-only-files.txt"
# Directory names that the inventory without Git metadata skips at any depth.
SKIP_DIRS = frozenset((".local", ".git", "__pycache__"))
PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    # A value that is a reference to an environment variable of the gateway is no token.
    re.compile(r"(?i)(?:api[_-]?key|bearer|token|password)\s*[:=]\s*['\"]?"
               r"(?!os\.environ/[A-Za-z_][A-Za-z0-9_]*(?![A-Za-z0-9_./+-]))[A-Za-z0-9_./+-]{20,}"),
)
# Reviewed lines that match PATTERNS but hold no secret: (path, exact line text without the
# line end). A match is dropped only when the line of that file is exactly this text.
# The exception is the exact line text, not a line number: each copy of the text in that
# file is dropped. Add an entry only after review; a changed line is a new finding.
# This file is in the publish set too, so split each text where PATTERNS would match.
# The viewer code that archify embeds in each generated view holds three lines with a JavaScript
# variable named for a motion marker. They hold no credential. Reviewed for archify 3.2.0.
ARCHIFY_VIEWS = ("docs/aws-core.html", "docs/aws-memory.html", "docs/aws-mac.html", "docs/memory-terminal.html")
PATTERN_ALLOW = tuple(
    (path, line) for path in ARCHIFY_VIEWS for line in (
        "        var tok" "en = document.createElementNS(svgNamespace, 'g');",
        "            var tok" "en = relationshipTokenGeometry(shapes[0], tokenKind, key);",
        "          tok" "en = Archify.motionGovernor.claim('route', function () {",
    )
)
# A variable of the environment example whose name holds one of these words is a secret:
# its value must be empty. Each other variable may hold a portable default.
ENV_EXAMPLE = ".env.example"
ENV_SECRET_WORDS = ("KEY", "TOKEN", "PASSWORD", "SECRET", "HASH", "SALT")
ENV_LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)")

# Public reader rules: content that a reader of the published copy must not get
# (docs/publishing.md, "Public reader rules"). One (rule name, regular expression) pair for
# each class that a pattern can find. This file is in the publish set too, so each
# expression is written in a form that does not match its own source text, and no comment
# of this file gives an example of a match.
PUBLIC_RULES = (
    # A reference to an entry of a private tracker: the word for an issue or a ticket, then a
    # number; a planning map with a number; a decision with a letter code.
    ("tracker-reference", re.compile(
        r"(?i)\b(?:(?:issue|ticket)s?[ \t]+#?[0-9]+|map[ \t]+[0-9]{1,3}\b(?![.,]?[0-9])|decision[ \t]+[A-Z][0-9]+)\b")),
    # A path of the home directory of the administrator or of a stack layout of one host.
    ("machine-path", re.compile(r"(?<![A-Za-z0-9_.~-])/(?:root|opt/" r"stacks)(?:/|\b)")),
    # A record of work on one host: the phrase for the one reference machine; a record word and a
    # date in one line; a date and a phrase for one host in one line, in each order.
    ("host-record", re.compile(
        r"(?i)\b(?:reference[ \t]+host\b"
        r"|(?:observ(?:ed|ation)|verified|seen|read|decision|decided|approved|accepted)\b[^\n]{0,60}?\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b"
        r"|20[0-9]{2}-[0-9]{2}-[0-9]{2}\b[^\n]{0,80}?\b(?:this|live|lab|test)[ \t]+(?:host|vm)\b"
        r"|(?:this|live|lab|test)[ \t]+(?:host|vm)\b[^\n]{0,80}?\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b)")),
)
# The rule for a published file that names a tracked path outside the publish set. It reads
# two forms: the target of a Markdown link, and a path between backticks.
PATH_RULE = "unpublished-path"
LINK_TARGET = re.compile(r"\]\(\s*<?([^)\s>]+)")
TICK_PATH = re.compile(r"`([^`\s]+)`")
# The lists of scripts/scan.sh (docs/secret-handling.md), relative to the repository root.
DENY_REL, LOCAL_DENY_REL = "scripts/host-values.deny", ".local/host-values.deny"
ALLOW_REL, ALLOW_LINES_REL = "scripts/host-values.allow", "scripts/host-values.allow-lines"


def _list_lines(path):
    """The entries of one list file: stripped lines, without comments and empty lines."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def local_deny_file(root=None):
    """The path of the local deny list, as scripts/scan.sh finds it, or None.

    `SCAN_LOCAL_DENY` names the file. Else it is `.local/host-values.deny` of the checkout,
    or of the main checkout for a linked worktree. A clone of the portable snapshot has none.
    """
    root = ROOT if root is None else root
    named = os.environ.get("SCAN_LOCAL_DENY")
    if named:
        return Path(named) if Path(named).is_file() else None
    own = Path(root) / LOCAL_DENY_REL
    if own.is_file():
        return own
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root, text=True, capture_output=True)
    if common.returncode == 0 and common.stdout.strip():
        git_dir = (Path(root) / common.stdout.strip()).resolve()
        if git_dir.name == ".git" and (git_dir.parent / LOCAL_DENY_REL).is_file():
            return git_dir.parent / LOCAL_DENY_REL
    return None


def local_deny_state(root=None):
    """"with" when the local deny list holds one pattern or more, else "without".

    Without the list, no rule knows the values of one host: a pass is then no host-value pass.
    """
    local = local_deny_file(root)
    return "with" if local is not None and _list_lines(local) else "without"


def deny_rules(root=None):
    """(rule id, literal pattern) for each line of the tracked and of the local deny list.

    The rule ids are the ids of scripts/scan.sh: `host-value-<n>` and `host-value-local-<n>`.
    """
    root = ROOT if root is None else root
    rules = [(f"host-value-{n}", pattern) for n, pattern in enumerate(_list_lines(Path(root) / DENY_REL), 1)]
    local = local_deny_file(root)
    if local is not None:
        rules += [(f"host-value-local-{n}", pattern) for n, pattern in enumerate(_list_lines(local), 1)]
    return rules


def accepted_lines(root=None):
    """The (path, sha256 of the exact line text) pairs of scripts/host-values.allow-lines."""
    root = ROOT if root is None else root
    pairs = set()
    for line in _list_lines(Path(root) / ALLOW_LINES_REL):
        path, _, digest = line.partition("\t")
        pairs.add((path, digest))
    return pairs


def _path_class(path, tracked, published):
    """"published", "private" or None (not a tracked path) for a file or a directory path."""
    path = path.rstrip("/")
    if path in published:
        return "published"
    if path in tracked:
        return "private"
    below = [name for name in tracked if name.startswith(path + "/")]
    if not below:
        return None
    return "published" if any(name in published for name in below) else "private"


def unpublished_paths(line, name, tracked, published):
    """The tracked paths outside the publish set that one line of the file `name` names.

    A Markdown link target is relative to the directory of the file. A backticked path is
    relative to the repository root, or to the directory of the file when the root has no
    such tracked path. A directory is a finding when no file below it is published.
    The optional exclusion list itself is no finding: a reader can create it.
    """
    found = []
    base = posixpath.dirname(name)
    for link, raw in [(True, m) for m in LINK_TARGET.findall(line)] + [(False, m) for m in TICK_PATH.findall(line)]:
        target = re.sub(r":[0-9]+(?:-[0-9]+)?$", "", raw.split("#", 1)[0].split("?", 1)[0])
        if "*" in target:
            target = target.split("*", 1)[0].rpartition("/")[0]
        if not target or "://" in target or target.startswith(("/", "~", "$", "mailto:")):
            continue
        relative = posixpath.normpath(posixpath.join(base, target))
        plain = posixpath.normpath(target)
        for path in ((relative,) if link else (plain, relative)):
            if path.startswith("../") or path in (".", ".."):
                continue
            kind = _path_class(path, tracked, published)
            if kind == "private" and path != DEV_ONLY_REL:
                found.append(path)
            if kind is not None:
                break
    return found


def public_findings(names, root=None, deny=None, allow=None, accepted=None, tracked=None, published=None):
    """Sorted (path, line number, rule id) for each public reader finding in the files `names`.

    A finding is a match of `PUBLIC_RULES`, or a deny-list pattern (a literal, any case). As in
    scripts/scan.sh: a deny-list pattern that is only inside an exact URL of the allow list is
    dropped, a deny list is not searched for its own patterns, and a line whose path and hash
    are in the accepted lines gives no finding. With `tracked` and `published`, a line that
    names a tracked path outside `published` is a finding of `PATH_RULE`.
    A finding never holds text of the file.
    """
    root = ROOT if root is None else root
    deny = deny_rules(root) if deny is None else deny
    allow = _list_lines(Path(root) / ALLOW_REL) if allow is None else allow
    accepted = accepted_lines(root) if accepted is None else accepted
    lowered = [(rule, pattern.lower()) for rule, pattern in deny]
    paths = tracked is not None and published is not None
    if paths:
        tracked, published = set(tracked), set(published)
    found = []
    for name in names:
        text = (Path(root) / name).read_bytes().decode("utf-8", "replace")
        for number, line in enumerate(text.split("\n"), 1):
            rules = [rule for rule, pattern in PUBLIC_RULES if pattern.search(line)]
            if lowered and name not in (DENY_REL, LOCAL_DENY_REL):
                outside = line
                for url in allow:
                    outside = outside.replace(url, "\x01")
                outside = outside.lower()
                rules += [rule for rule, pattern in lowered if pattern in outside]
            if paths and unpublished_paths(line, name, tracked, published):
                rules.append(PATH_RULE)
            if rules and (name, hashlib.sha256(line.encode("utf-8", "surrogatepass")).hexdigest()) not in accepted:
                found += [(name, number, rule) for rule in rules]
    return sorted(found)


def env_example_findings(text):
    """The numbers of the lines of an environment example that break its rule.

    A line is empty, a comment, or `NAME=value`. A variable whose name holds a word of
    `ENV_SECRET_WORDS` must have an empty value.
    """
    numbers = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = ENV_LINE.fullmatch(line)
        if match is None or (match.group(2).strip() and any(word in match.group(1).upper() for word in ENV_SECRET_WORDS)):
            numbers.append(number)
    return numbers


def private_excludes(root=None, ref=None):
    """Read optional exclusions from the checkout or the exact snapshot source commit."""
    root = ROOT if root is None else Path(root)
    if ref is None:
        path = root / DEV_ONLY_REL
        if not path.exists() and not path.is_symlink():
            return ()
        if path.is_symlink() or not path.is_file():
            fail("private_exclude_list", DEV_ONLY_REL)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            fail("private_exclude_unreadable", DEV_ONLY_REL)
    else:
        exists = subprocess.run(["git", "ls-tree", ref, "--", DEV_ONLY_REL], cwd=root,
                                text=True, capture_output=True, check=True).stdout
        if not exists:
            return ()
        if not exists.startswith(("100644 blob ", "100755 blob ")):
            fail("private_exclude_list", DEV_ONLY_REL)
        try:
            text = subprocess.run(["git", "show", f"{ref}:{DEV_ONLY_REL}"], cwd=root,
                                  text=True, capture_output=True, check=True).stdout
        except (OSError, UnicodeError, subprocess.CalledProcessError):
            fail("private_exclude_unreadable", DEV_ONLY_REL)
    entries = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    for entry in entries:
        if any(c.isspace() for c in entry) or "#" in entry:
            fail("private_exclude_text", DEV_ONLY_REL)
        parts = entry.removesuffix("/").split("/")
        if any(part in ("", ".", "..") for part in parts) or "\\" in entry or any(ord(c) < 32 for c in entry):
            fail("private_exclude_path", DEV_ONLY_REL)
    return tuple(entries)


def exclude_findings(entries, tracked):
    """(finding, entry) for each exclusion that covers no tracked file, or a directory without "/"."""
    found = []
    for entry in entries:
        if not entry.endswith("/") and any(name.startswith(entry + "/") for name in tracked):
            found.append(("private_exclude_directory", entry))
        elif not excluded_files(tracked, (entry,)):
            found.append(("private_exclude_unused", entry))
    return found


def validate_private_excludes(entries, tracked):
    """Require each exclusion to cover tracked files; catch stale or mistyped exclusions."""
    for code, entry in exclude_findings(entries, tracked):
        fail(code, f"{DEV_ONLY_REL}: {entry}")


def excluded_files(names, entries):
    """Match exact paths or directory prefixes, never a sibling with a similar name."""
    return {name for name in names if any(name.startswith(item) if item.endswith("/") else name == item
                                         for item in entries)}


def rule_files(tracked, rules=None):
    """Split the paths that the directory rules name into (published, excluded) sets."""
    published, excluded = set(), set()
    for directory, excludes in PUBLISH_DIRS if rules is None else rules:
        prefix = directory.rstrip("/") + "/"
        for name in tracked:
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix):]
            if any(rest.startswith(item) if item.endswith("/") else rest == item for item in excludes):
                excluded.add(name)
            else:
                published.add(name)
    return published, excluded


def publish_files(tracked, rules=None, *, root=None, ref=None):
    """Explicit files and directory rule files, minus optional private-copy exclusions."""
    entries = private_excludes(root, ref)
    if excluded_files(PUBLISH, entries):
        fail("private_excludes_explicit", DEV_ONLY_REL)
    validate_private_excludes(entries, tracked)
    published = rule_files(tracked, rules)[0]
    return PUBLISH + tuple(sorted(published - excluded_files(published, (*entries, DEV_ONLY_REL))))


def _git_names(root, *command):
    result = subprocess.run(["git", *command], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        return None
    return {name for name in result.stdout.split("\0") if name}


def tracked_files(root=None, ref=None):
    """Tracked paths of the checkout at `root` (of `ref` when given), or None without Git."""
    root = ROOT if root is None else root
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True, capture_output=True)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != Path(root).resolve():
        return None
    return _git_names(root, "ls-tree", "-r", "-z", "--name-only", ref) if ref else _git_names(root, "ls-files", "-z")


def inventory(root=None, tracked=None):
    """The files that the check must account for, as repository-relative paths.

    With Git metadata: each tracked file and each untracked file that Git does not ignore, when
    it is in the working tree. Without (an unpacked snapshot): each file outside `SKIP_DIRS`.
    """
    root = ROOT if root is None else Path(root)
    if tracked is not None:
        untracked = _git_names(root, "ls-files", "-z", "--others", "--exclude-standard") or set()
        return {name for name in set(tracked) | untracked
                if ((root / name).is_file() or (root / name).is_symlink())
                and not SKIP_DIRS & set(Path(name).parts)}
    return {p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and not SKIP_DIRS & set(p.relative_to(root).parts)}


def collect(root=None):
    """(publish set, findings) of the checkout: each finding is a (finding, path or detail) pair."""
    root = ROOT if root is None else Path(root)
    found = []
    tracked = tracked_files(root)
    actual = inventory(root, tracked)
    # Without Git metadata (an unpacked portable snapshot), every file counts as tracked.
    known = actual if tracked is None else tracked
    published, excluded = rule_files(known)
    try:
        entries = private_excludes(root)
    except Invalid as exc:
        found.append(tuple(str(exc).split(": ", 1)))
        entries = ()
    if excluded_files(PUBLISH, entries):
        found.append(("private_excludes_explicit", DEV_ONLY_REL))
    found += [(code, f"{DEV_ONLY_REL}: {entry}") for code, entry in exclude_findings(entries, known)]
    private = excluded_files(known, (*entries, DEV_ONLY_REL))
    published -= private
    names = list(PUBLISH) + sorted(published)
    if len(set(names)) != len(names):
        found += [("publish_duplicates", name) for name in sorted(set(PUBLISH) & published)]
    names = list(dict.fromkeys(names))
    readable = []
    for name in names:
        file = root / name
        if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(root.resolve()):
            found.append(("publish_path", name))
            continue
        # A file of a directory rule can be binary; an explicit file is text.
        try:
            content = file.read_bytes().decode("utf-8", "replace" if name in published else "strict")
        except UnicodeError:
            found.append(("publish_text", name))
            continue
        readable.append(name)
        if file.name == ENV_EXAMPLE and env_example_findings(content):
            found.append(("env_example_value", name))
        allowed = {line for path, line in PATTERN_ALLOW if path == name}
        if any(pattern.search("\n".join("" if line in allowed else line for line in content.split("\n")))
               for pattern in PATTERNS):
            found.append(("publish_secret_pattern", name))
    # The public reader rules. `--public` prints each finding with its line and its rule.
    reader = public_findings(readable, root, tracked=known, published=names)
    found += [("publish_public_reader", name) for name in sorted({name for name, _, _ in reader})]
    # A candidate release must account for every file: a file that no list names, then a
    # reviewed file that the checkout does not hold (with the prefix `missing: `).
    # Reviewed private-copy files are accepted when present; a snapshot holds none.
    reviewed = (set(PUBLISH) | published | excluded | private) & (known | set(PUBLISH))
    found += [("unreviewed_file", name) for name in sorted(actual - reviewed)]
    found += [("unreviewed_file", f"missing: {name}") for name in sorted(reviewed - actual - set(PUBLISH))]
    return names, found


def check(root=None):
    """The publish set of the checkout; raise `Invalid` with the first finding."""
    names, found = collect(root)
    if found:
        fail(*found[0])
    return names


def public_report(hashes=False, root=None):
    """Print each public reader finding of the whole publish set; return the exit code.

    A line is `<rule id>: <path>:<line>`. It holds no text of the file and no deny-list
    pattern. With `hashes`, a line is `<path><TAB><sha256>`, the form of an accepted line.
    """
    root = ROOT if root is None else Path(root)
    tracked = tracked_files(root)
    known = inventory(root, None) if tracked is None else tracked
    published = rule_files(known)[0]
    try:
        entries = private_excludes(root)
    except Invalid:
        entries = ()
    published -= excluded_files(known, (*entries, DEV_ONLY_REL))
    every = list(dict.fromkeys(list(PUBLISH) + sorted(published)))
    names = [name for name in every if (root / name).is_file()]
    findings = public_findings(names, root, tracked=known, published=every)
    if hashes:
        lines = {}
        for name in sorted({name for name, _, _ in findings}):
            lines[name] = (root / name).read_bytes().decode("utf-8", "replace").split("\n")
        for name, number in sorted({(name, number) for name, number, _ in findings}):
            print(f"{name}\t{hashlib.sha256(lines[name][number - 1].encode('utf-8', 'surrogatepass')).hexdigest()}")
    else:
        for name, number, rule in findings:
            print(f"{rule}: {name}:{number}")
    local = local_deny_state(root)
    if findings:
        print(f"public reader check failed: {len(findings)} findings in {len({name for name, _, _ in findings})} of "
              f"{len(names)} files, {local} a local deny list", file=sys.stderr if hashes else sys.stdout)
        return 1
    print(f"public reader check valid: {len(names)} files, {len(PUBLIC_RULES) + 1} rules, {local} a local deny list")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["--public"], ["--public", "--hashes"]):
        return public_report(hashes=len(argv) == 2)
    if argv:
        print("usage: publish_check.py [--public [--hashes]]", file=sys.stderr)
        return 2
    names, found = collect()
    local = local_deny_state()
    for code, detail in found:
        print(f"{code}: {detail}", file=sys.stderr)
    if found:
        print(f"publish set not valid: {len(found)} findings, {local} a local deny list", file=sys.stderr)
        return 1
    print(f"publish set valid: {len(names)} files, {len(PUBLISH)} explicit, {local} a local deny list "
          "(not a release approval)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
