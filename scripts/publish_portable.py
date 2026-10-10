#!/usr/bin/env python3
"""Snapshot the reviewed publish set from HEAD onto the `portable` branch.

You can keep private notes in your source copy and publish only reviewed files.
This command takes the publish set from committed HEAD (never the working tree),
chains it onto the previous snapshot (or starts a new history with `--new-root`),
and tags it. It writes no file inside the checkout.

A run without `--push` changes no remote: it builds the snapshot and the tag in this
repository and prints the push plan. With `--push` the command sends the snapshot branch
and the tags of that snapshot by explicit refspec, in one atomic push, after
`git push --dry-run`.

Before a ref moves, `scripts/scan.sh --level fail` reads the tree of the snapshot commit and
the snapshot commits that the remote does not have. A push also needs a local deny list, the
installed hooks, and a GitHub repository that is private.

The snapshot commit and the tag carry a neutral identity and no signature, so that the
Git identity and the signing key of the publishing host do not leave with a snapshot.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "scripts"))

from publish_check import collect, local_deny_state, publish_files, tracked_files  # noqa: E402

DEFAULT_NAME = "tenant-ec2 portable"
DEFAULT_EMAIL = "portable@tenant-ec2.invalid"
NAME_VARIABLE = "TENANT_EC2_PUBLISH_NAME"
EMAIL_VARIABLE = "TENANT_EC2_PUBLISH_EMAIL"
# A signature would carry the key identity of the host.
NO_SIGNATURE = ("-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-c", "tag.forceSignAnnotated=false")
# Options of `git push` that send refs that the command does not name. The command has none
# of them and refuses each with a reason.
REFUSED_OPTIONS = ("--follow-tags", "--tags", "--all", "--mirror", "--force", "--force-with-lease")
# The value of an empty variable of the environment example in the stub file of the Compose
# check. It is valid as a secret and as a relative bind path.
STUB_VALUE = "./stub"
SNAPSHOT_TAG = re.compile(r"portable/[0-9]{8}-[0-9a-f]{7}")
# A remote name goes into a revision range of the scan, so it holds no blank and no glob character.
REMOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# The owner and the name of a GitHub repository in an HTTP URL, an SSH URL or the short SSH form.
GITHUB_URL = re.compile(r"(?i)(?:https?://(?:[^/@]*@)?(?:www\.)?github\.com/|ssh://(?:[^/@]*@)?github\.com(?::[0-9]+)?/"
                        r"|(?:[^/@:]+@)?github\.com:)([^/]+)/([^/]+?)(?:\.git)?/?")


def git(*args, root=ROOT, env=None, check_call=True, input=None):
    result = subprocess.run(["git", *args], cwd=root, env=env, text=True, capture_output=True, input=input)
    if check_call and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_identity(name=None, email=None, environ=None):
    """(name, e-mail) of the snapshot commit and the tag: option, then variable, then default."""
    environ = os.environ if environ is None else environ
    name = environ.get(NAME_VARIABLE, DEFAULT_NAME) if name is None else name
    email = environ.get(EMAIL_VARIABLE, DEFAULT_EMAIL) if email is None else email
    if not name.strip():
        raise SystemExit(f"empty identity name: set --identity-name or {NAME_VARIABLE}, or unset both")
    if not email.strip():
        raise SystemExit(f"empty identity e-mail: set --identity-email or {EMAIL_VARIABLE}, or unset both")
    return name.strip(), email.strip()


def identity_env(identity):
    """The host environment with `identity` as author and committer (the tagger is the committer).

    `TZ=UTC` keeps the time zone offset of the host out of the commit date and the tag date.
    """
    name, email = identity
    return dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
                GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email, TZ="UTC")


# --- refusals -------------------------------------------------------------------


def check_snapshot_branch(root, branch):
    """Reserve snapshot updates for the portable namespace, never a checked-out branch."""
    git("check-ref-format", f"refs/heads/{branch}", root=root)
    if not (branch == "portable" or branch.startswith(("portable-", "portable/"))):
        raise SystemExit("refused: the snapshot branch must be named portable, portable-* or portable/*")
    worktrees = git("worktree", "list", "--porcelain", "-z", root=root).split("\0")
    if f"branch refs/heads/{branch}" in worktrees:
        raise SystemExit(f"refused: the snapshot branch {branch} is checked out in a worktree")


def check_repository(root, source_branch="local-dev"):
    """Refuse a repository state in which a snapshot or a push is not safe."""
    if git("status", "--porcelain", "--untracked-files=all", root=root):
        raise SystemExit("refused: the working tree is not clean; commit first, the snapshot comes from HEAD only")
    if git("rev-parse", "--is-shallow-repository", root=root) == "true":
        raise SystemExit("refused: the repository is shallow; the snapshot needs the full previous chain")
    hooks = git("config", "--get", "core.hooksPath", root=root, check_call=False)
    if hooks:
        raise SystemExit("refused: core.hooksPath is set; the hooks of scripts/install-hooks.sh would not run "
                         "(git config --unset core.hooksPath)")
    current = git("symbolic-ref", "-q", "--short", "HEAD", root=root, check_call=False)
    if current != source_branch:
        raise SystemExit(f"refused: HEAD is {'on the branch ' + current if current else 'detached'}, not on the "
                         f"development branch {source_branch}; a snapshot comes from the reviewed development branch "
                         "(--source-branch names another one)")


def check_parent_chain(root, parent, source_ref, branch, identity):
    """Refuse a previous snapshot that is not a chain of snapshot commits only.

    The chain goes to the remote with the next push. It must share no commit with the source,
    and each of its commits has the snapshot identity as author and committer and one parent
    at most. A branch that someone created from a development commit fails both conditions.
    """
    shared = git("merge-base", parent, source_ref, root=root, check_call=False)
    if shared:
        raise SystemExit(f"refused: the snapshot branch {branch} shares history with the source (commit {shared[:7]}); "
                         "a push would send development commits. Delete the branch after review, or start a new "
                         "history with --new-root")
    expected = "{} <{}>".format(*identity)
    # The parents are not the last field: the last line of the output loses a trailing tab.
    lines = git("log", "--no-show-signature", "--format=%H%x09%P%x09%an <%ae>%x09%cn <%ce>", parent, root=root)
    for line in lines.splitlines():
        fields = line.split("\t")
        if len(fields) != 4 or fields[2] != expected or fields[3] != expected:
            raise SystemExit(f"refused: the commit {fields[0][:7]} in the chain of {branch} does not carry the snapshot "
                             f"identity {expected}; it is no snapshot of this command. Use the identity of the chain, "
                             "or start a new history with --new-root")
        if len(fields[1].split()) > 1:
            raise SystemExit(f"refused: the commit {fields[0][:7]} in the chain of {branch} has more than one parent; "
                             "a snapshot chain holds no merge. Start a new history with --new-root")


def check_hooks(root):
    """Refuse when the hooks of this repository are not installed and current."""
    result = subprocess.run(["sh", "scripts/install-hooks.sh", "--check"], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("refused: the hooks are not installed and current, so no hook would limit the refs and scan "
                         "the push (scripts/install-hooks.sh):\n" + (result.stdout + result.stderr).rstrip())
    return result.stdout.strip()


def github_repositories(urls):
    """The sorted `<owner>/<name>` of each GitHub repository in `urls`."""
    matches = (GITHUB_URL.fullmatch(url) for url in urls)
    return sorted({f"{match.group(1)}/{match.group(2)}" for match in matches if match is not None})


def check_visibility(urls, *, allow_public=False, checked=False, which=shutil.which, run=subprocess.run):
    """One line for each GitHub repository of the remote; refuse a repository that is not private.

    `gh repo view --json isPrivate` gives the visibility. A public repository needs `allow_public`.
    Without `gh`, or without an answer of it, the visibility is not verified and needs `checked`.
    A remote that is no GitHub repository is not checked.
    """
    repositories = github_repositories(urls)
    if not repositories:
        return ["visibility: the remote is no GitHub repository; not checked"]
    gh = which("gh")
    lines = []
    for repository in repositories:
        private = None
        if gh is not None:
            result = run([gh, "repo", "view", repository, "--json", "isPrivate"], text=True, capture_output=True)
            try:
                private = json.loads(result.stdout)["isPrivate"] if result.returncode == 0 else None
            except (ValueError, KeyError, TypeError):
                private = None
        if private is True:
            lines.append(f"visibility: {repository} is private (gh)")
        elif private is False:
            if not allow_public:
                raise SystemExit(f"refused: the GitHub repository {repository} is not private; --allow-public permits "
                                 "the push after a decision of the owner")
            lines.append(f"visibility: {repository} is not private; --allow-public is set")
        else:
            reason = "gh is absent" if gh is None else "gh gave no answer"
            if not checked:
                raise SystemExit(f"refused: the visibility of {repository} is not verified ({reason}); check it by "
                                 "hand, then add --visibility-checked")
            lines.append(f"visibility: {repository} is not verified ({reason}); --visibility-checked is set")
    return lines


def clean_url(url):
    """The URL without userinfo (a user name or a credential), for a message."""
    return re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@", r"\1", url)


def url_has_userinfo(url):
    """True for an HTTP URL with userinfo, and for each URL with a password in its userinfo.

    The user name of an SSH URL is no credential, so `ssh://git@host/path` passes.
    """
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://([^/@]*)@", url)
    if match is None:
        return False
    return match.group(1).lower() in ("http", "https") or ":" in match.group(2)


def remote_urls(root, remote):
    """The fetch and push URLs of `remote`, or None when the remote is not configured."""
    if remote not in git("remote", root=root).split():
        return None
    urls = git("remote", "get-url", "--all", remote, root=root).split()
    urls += git("remote", "get-url", "--push", "--all", remote, root=root).split()
    return list(dict.fromkeys(urls))


def check_remote(root, remote):
    """The URLs of `remote` without userinfo; refuse a URL that holds userinfo. None: no such remote."""
    urls = remote_urls(root, remote)
    if urls is None:
        return None
    if any(url_has_userinfo(url) for url in urls):
        raise SystemExit(f"refused: a URL of the remote {remote} holds userinfo; use the Git credential store")
    return [clean_url(url) for url in urls]


def resolve_remote_branch(root, remote, option=None):
    """The snapshot branch of `remote`: the setting `remote.<remote>.portableBranch`, or main.

    The pre-push hook reads the same setting and permits no other remote branch. So an option
    with another name is refused here, before a Git object is written.
    """
    key = f"remote.{remote}.portableBranch"
    setting = git("config", "--get", key, root=root, check_call=False) or "main"
    branch = setting if option is None else option
    if subprocess.run(["git", "check-ref-format", f"refs/heads/{branch}"], cwd=root).returncode != 0:
        raise SystemExit(f"refused: {branch!r} is not a branch name (--remote-branch or {key})")
    if branch != setting:
        raise SystemExit(f"refused: --remote-branch {branch} is not the snapshot branch of {remote} ({setting}); "
                         f"the pre-push hook permits only that branch. Set it one time: git config {key} {branch}")
    return branch


def refuse_options(argv):
    """Stop on an option that sends refs that the command does not name, or that forces a ref."""
    for argument in argv:
        if argument.split("=", 1)[0] in REFUSED_OPTIONS:
            raise SystemExit(f"refused: {argument.split('=', 1)[0]} is not permitted; the push names the snapshot "
                             "branch and the tags of the snapshot by explicit refspec, without force")


# --- offline checks ---------------------------------------------------------------


def shell_scripts(root, tracked):
    """The tracked shell scripts under `scripts/`: a name that ends in `.sh`, or a shell shebang."""
    found = []
    for name in sorted(tracked):
        if not name.startswith("scripts/") or not (Path(root) / name).is_file():
            continue
        with open(Path(root) / name, "rb") as handle:
            first = handle.readline(128)
        if name.endswith(".sh") or re.match(rb"#!\s*(?:/usr/bin/env\s+)?(?:/bin/|/usr/bin/)?(?:ba|da)?sh\b", first):
            found.append(name)
    return found


def stub_env(text):
    """The text of a stub environment file from the text of the environment example.

    A variable with a value keeps it (a portable default). An empty variable gets `STUB_VALUE`,
    so that a required variable of the Compose file is set. No value of the host is read.
    """
    lines = []
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if match is not None:
            lines.append(f"{match.group(1)}={match.group(2) if match.group(2).strip() else STUB_VALUE}")
    return "\n".join(lines) + "\n"


def check_shell_syntax(root):
    names = shell_scripts(root, tracked_files(root) or ())
    for name in names:
        result = subprocess.run(["bash", "-n", name], cwd=root, text=True, capture_output=True)
        if result.returncode != 0:
            return False, f"{name}:\n{result.stderr}"
    return True, f"{len(names)} scripts"


def check_compose(root):
    """`docker compose config -q` with a stub environment file: no network, no service start.

    The command reads the Compose file and the stub file only. `--env-file` replaces the `.env`
    of the checkout, and the environment of the shell does not reach the command.
    """
    root = Path(root)
    if not (root / "compose.yaml").is_file() or not (root / ".env.example").is_file():
        return True, "skipped: no compose.yaml or no .env.example in this copy"
    docker = shutil.which("docker")
    if docker is None:
        return True, "skipped: Docker is absent; run the check on a machine with Docker before a publication"
    keep = {name: value for name, value in os.environ.items() if name in ("PATH", "HOME", "DOCKER_CONFIG", "DOCKER_HOST")}
    if subprocess.run([docker, "compose", "version"], capture_output=True, env=keep).returncode != 0:
        return True, "skipped: the Docker Compose plugin is absent"
    with tempfile.TemporaryDirectory(prefix="tenant-ec2-compose-") as temp:
        stub = Path(temp) / "stub.env"
        stub.write_text(stub_env((root / ".env.example").read_text(encoding="utf-8")), encoding="utf-8")
        result = subprocess.run([docker, "compose", "--project-directory", str(root), "-f", str(root / "compose.yaml"),
                                 "--env-file", str(stub), "config", "-q"], cwd=temp, text=True, capture_output=True, env=keep)
    if result.returncode != 0:
        return False, result.stdout + result.stderr
    return True, "valid with a stub environment file"


def check_publish_set(root):
    names, found = collect(root)
    if found:
        return False, "\n".join(f"{code}: {detail}" for code, detail in found)
    return True, f"{len(names)} files, {local_deny_state(root)} a local deny list"


def check_unit_tests(root):
    if not (Path(root) / "tests").is_dir():
        return True, "skipped: this repository has no tests/ directory yet"
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=root, text=True,
                            capture_output=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    lines = (result.stdout + result.stderr).strip().splitlines()
    return result.returncode == 0, (result.stdout + result.stderr) if result.returncode else (lines[-1] if lines else "")


# The offline checks before a snapshot, in order. Each returns (passed, one line or the output).
CHECKS = (
    ("unit tests", check_unit_tests),
    ("shell syntax", check_shell_syntax),
    ("compose config", check_compose),
    ("publish set", check_publish_set),
)


def scan_snapshot(root, commit, remote):
    """Run `scripts/scan.sh --level fail` on a snapshot commit; stop on a finding or a tool error.

    The mode `commit` reads the tree of the commit, not the working tree. The mode `history`
    reads the snapshot commits that no remote-tracking ref of `remote` contains: each commit
    that the next push sends, as current as the last fetch or push. Without such a ref it
    reads the whole chain. Returns one line for each mode.
    """
    lines = []
    for mode, argument in (("commit", commit), ("history", f"{commit} --not --remotes={remote}")):
        result = subprocess.run(["sh", "scripts/scan.sh", "--level", "fail", mode, argument], cwd=root, text=True,
                                capture_output=True, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            raise SystemExit(f"check failed: scan {mode} of the snapshot {commit[:7]}; no ref is moved\n"
                             + (result.stdout + result.stderr).rstrip())
        last = (result.stdout.strip().splitlines() or [""])[-1]
        lines.append(f"check ok: scan {mode} ({last.removeprefix('scan: ')})")
    return lines


# --- snapshot -------------------------------------------------------------------


def snapshot(root, paths, source_ref, branch, *, message, identity=None, new_root=False, tag=None, verify=None):
    """Write a commit on `branch` whose tree is exactly `paths` as they are in `source_ref`.

    Returns (commit, tree, changed). Uses a temporary index; the working tree is untouched.
    With `tag`, the branch and annotated tag refs update in one transaction, or neither updates.
    An existing tag with that name stops the command before an object is written.
    The commit carries `identity` (default: `resolve_identity()`) and no signature.
    With `new_root` the commit has no parent, also when its tree equals the previous snapshot:
    `branch` starts a new history, and no earlier snapshot is an ancestor of it.
    Without `new_root`, a previous snapshot that is not a chain of snapshot commits stops the command.
    `verify(commit)` runs on the snapshot commit before a ref moves; it stops the command to refuse.
    """
    identity = resolve_identity() if identity is None else identity
    check_snapshot_branch(root, branch)
    if tag is not None:
        git("check-ref-format", f"refs/tags/{tag}", root=root)
    with tempfile.TemporaryDirectory(prefix="tenant-ec2-portable-") as temp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / "index"))
        git("read-tree", "--empty", root=root, env=env)
        for path in sorted(paths):
            entry = git("ls-tree", source_ref, "--", path, root=root)
            if not entry:
                raise SystemExit(f"missing in {source_ref}: {path}")
            mode, kind, blob = entry.split("\t", 1)[0].split()
            if kind != "blob" or mode not in ("100644", "100755"):
                raise SystemExit(f"not a regular file in {source_ref}: {path}")
            git("update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}", root=root, env=env)
        tree = git("write-tree", root=root, env=env)
    parent = git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}", root=root, check_call=False)
    if parent and not new_root:
        check_parent_chain(root, parent, source_ref, branch, identity)
        if git("rev-parse", f"{parent}^{{tree}}", root=root) == tree:
            if verify is not None:
                verify(parent)
            return parent, tree, False
    if tag is not None and git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", root=root, check_call=False):
        raise SystemExit(f"refused: the tag {tag} exists; a snapshot tag never moves")
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent and not new_root else [])
    commit = git(*NO_SIGNATURE, *args, root=root, env=identity_env(identity))
    if verify is not None:
        verify(commit)
    # Create objects first, then publish both refs atomically with an expected old branch value.
    tag_update = ""
    if tag is not None:
        tagger = git("var", "GIT_COMMITTER_IDENT", root=root, env=identity_env(identity))
        tag_object = git("mktag", root=root,
                         input=f"object {commit}\ntype commit\ntag {tag}\ntagger {tagger}\n\n{message}\n")
        tag_update = f"create refs/tags/{tag} {tag_object}\n"
    branch_update = (f"update refs/heads/{branch} {commit} {parent}\n" if parent
                     else f"create refs/heads/{branch} {commit}\n")
    check_snapshot_branch(root, branch)
    git("update-ref", "--stdin", root=root, input="start\n" + branch_update + tag_update + "prepare\ncommit\n")
    return commit, tree, True


def snapshot_tags(root, commit):
    """Tags of the form `portable/<yyyymmdd>-<source sha>` that point at `commit`, sorted.

    A tag with another name on the same commit is not a snapshot tag, and the push does not send it.
    """
    names = git("tag", "--points-at", commit, "--list", "portable/*", root=root).split()
    return sorted(name for name in names if SNAPSHOT_TAG.fullmatch(name))


def publish_tracked(root, ref):
    """Tracked paths of `ref`; stop when Git gives none, because a directory rule needs them."""
    tracked = tracked_files(root, ref)
    if tracked is None:
        raise SystemExit("publish_tracked: git metadata required")
    return tracked


def identities(root, commit, tags):
    """One line for the author and the committer of `commit` and for the tagger of each tag."""
    lines = [f"author     {git('log', '-1', '--format=%an <%ae>', commit, root=root)}",
             f"committer  {git('log', '-1', '--format=%cn <%ce>', commit, root=root)}"]
    for tag in tags:
        tagger = git("for-each-ref", "--format=%(taggername) %(taggeremail)", f"refs/tags/{tag}", root=root)
        lines.append(f"tagger     {tagger}  ({tag})")
    return lines


# --- push -----------------------------------------------------------------------


def push_refspecs(branch, remote_branch, tags=()):
    """The explicit refspecs of the push: the snapshot branch and each tag of this snapshot, no other ref."""
    return [f"refs/heads/{branch}:refs/heads/{remote_branch}", *(f"refs/tags/{tag}:refs/tags/{tag}" for tag in tags)]


def push_command(remote, branch, remote_branch, tags=(), *, dry_run=False):
    """Arguments of the push: explicit refspecs only, no option that follows tags, no force.

    `tags` holds the `portable/*` tags that point at the snapshot commit. After a build without
    a push the next run makes no new tag, and the tag of the earlier build is in this list.
    `push.followTags` of the configuration is switched off for this one command.
    `--atomic`: the remote updates the branch and each tag together, or no ref.
    """
    command = ["-c", "push.followTags=false", "push", "--atomic", "--no-follow-tags"]
    if dry_run:
        command.append("--dry-run")
    return command + [remote, *push_refspecs(branch, remote_branch, tags)]


def remote_refs(root, remote):
    """{ref: object} of the remote, from `git ls-remote`. This contacts the remote and changes nothing."""
    refs = {}
    for line in git("ls-remote", remote, root=root).splitlines():
        value, _, ref = line.partition("\t")
        refs[ref] = value
    return refs


def pull_refs(refs):
    """The `refs/pull/*` refs of the remote. The server writes them for a pull request; no push does."""
    return sorted(ref for ref in refs if ref.startswith("refs/pull/"))


def foreign_refs(refs, remote_branch):
    """The refs of the remote that are not the snapshot branch, a snapshot tag or a `refs/pull/*` ref."""
    return sorted(ref for ref in refs
                  if ref not in ("HEAD", f"refs/heads/{remote_branch}")
                  and not ref.startswith(("refs/tags/portable/", "refs/pull/")))


def push_plan(remote, urls, branch, remote_branch, tags, commit, old):
    """The lines of the push plan: remote, URL without userinfo, each ref, the old and the new commit."""
    lines = [f"push plan: remote {remote}" + (f" ({', '.join(urls)})" if urls else " (not configured)"),
             f"  refs/heads/{branch} -> refs/heads/{remote_branch}  {old} -> {commit}"]
    lines += [f"  refs/tags/{tag} -> refs/tags/{tag}  new" for tag in tags]
    return lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    refuse_options(argv)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remote", default="github", help="git remote that receives the snapshot (default github)")
    parser.add_argument("--remote-branch", default=None,
                        help="branch name on the remote (default: git config remote.<remote>.portableBranch, or main)")
    parser.add_argument("--branch", default="portable", help="local snapshot branch: portable, portable-* or portable/*")
    parser.add_argument("--source-branch", default="local-dev",
                        help="development branch that HEAD must be on (default local-dev)")
    parser.add_argument("--push", action="store_true",
                        help="push the snapshot branch and its tags after the plan and a push dry run")
    parser.add_argument("--no-push", action="store_true", help="build and tag only; this is the default")
    parser.add_argument("--new-root", action="store_true",
                        help="start a new history: the snapshot commit has no parent; not with --push")
    parser.add_argument("--skip-checks", action="store_true",
                        help="skip the offline checks and the scan of the snapshot; not with --push")
    parser.add_argument("--allow-public", action="store_true",
                        help="with --push: permit a GitHub repository that is not private (a decision of the owner)")
    parser.add_argument("--visibility-checked", action="store_true",
                        help="with --push: the visibility of the GitHub repository was checked by hand (gh gives no answer)")
    parser.add_argument("--identity-name", default=None,
                        help=f"name on the snapshot commit and the tag (default: {NAME_VARIABLE} or '{DEFAULT_NAME}')")
    parser.add_argument("--identity-email", default=None,
                        help=f"e-mail on the snapshot commit and the tag (default: {EMAIL_VARIABLE} or '{DEFAULT_EMAIL}')")
    args = parser.parse_args(argv)
    identity = resolve_identity(args.identity_name, args.identity_email)
    if args.push and args.no_push:
        raise SystemExit("refused: --push and --no-push together")
    if args.push and args.new_root:
        raise SystemExit("refused: --new-root with --push; the push of a new history is a separate, manual step")
    if args.push and args.skip_checks:
        raise SystemExit("refused: --skip-checks with --push; a publication needs each check")

    if REMOTE_NAME.fullmatch(args.remote) is None:
        raise SystemExit("refused: the remote name holds a character other than a letter, a digit, '.', '_' or '-'")
    check_repository(ROOT, args.source_branch)
    check_snapshot_branch(ROOT, args.branch)
    urls = check_remote(ROOT, args.remote)
    args.remote_branch = resolve_remote_branch(ROOT, args.remote, args.remote_branch)
    if args.push and urls is None:
        raise SystemExit(f"refused: the remote {args.remote} is not configured (docs/remotes.md)")
    deny = local_deny_state(ROOT)
    if args.push:
        # The conditions of a push that need no snapshot, before a Git object is written.
        if deny == "without":
            raise SystemExit("refused: --push without a local deny list (absent or empty); no rule then knows the "
                             "values of this host (docs/secret-handling.md, \"Deny lists\")")
        print(f"hooks {check_hooks(ROOT)}")
        print("\n".join(check_visibility(urls, allow_public=args.allow_public, checked=args.visibility_checked)))
    head = git("rev-parse", "HEAD")
    short = head[:7]
    if args.skip_checks:
        print("checks skipped: this snapshot is not for a publication")
    else:
        for name, run in CHECKS:
            passed, detail = run(ROOT)
            if not passed:
                raise SystemExit(f"check failed: {name}\n{detail.rstrip()}")
            print(f"check ok: {name} ({detail})")
        if deny == "without":
            print("note: without a local deny list; the checks and the scan have no rule for the values of this "
                  "host, and --push is refused")

    def verify(commit):
        print("\n".join(scan_snapshot(ROOT, commit, args.remote)))

    date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    message = (f"Portable snapshot of {short} ({date})\n\n"
               f"Exactly the reviewed publish set from scripts/publish_check.py at\n"
               f"source commit {head}. Only reviewed files are included.\n")
    previous = git("rev-parse", "--verify", "--quiet", f"refs/heads/{args.branch}^{{commit}}", check_call=False)
    tag = f"portable/{date.replace('-', '')}-{short}"
    commit, tree, changed = snapshot(ROOT, publish_files(publish_tracked(ROOT, head), root=ROOT, ref=head),
                                     head, args.branch, message=message, identity=identity,
                                     new_root=args.new_root, tag=tag, verify=None if args.skip_checks else verify)
    if args.new_root and previous:
        print(f"new root: {args.branch} was {previous}; earlier local tags and other refs stay unchanged")
    if changed:
        print(f"portable commit {commit[:7]} (tree {tree[:7]}), tag {tag}")
    else:
        print(f"no change: the branch {args.branch} already holds tree {tree[:7]} (commit {commit[:7]})")
    tags = snapshot_tags(ROOT, commit)
    print("\n".join(identities(ROOT, commit, tags)))

    if not args.push:
        tracking = git("rev-parse", "--verify", "--quiet", f"refs/remotes/{args.remote}/{args.remote_branch}", check_call=False)
        old = f"{tracking[:7]} (last fetch)" if tracking else "not read"
        print("\n".join(push_plan(args.remote, urls, args.branch, args.remote_branch, tags, commit[:7], old)))
        print("no push: the remote is not contacted and not changed; --push sends this plan")
        return 0

    before = remote_refs(ROOT, args.remote)
    foreign = foreign_refs(before, args.remote_branch)
    if pull_refs(before):
        print(f"note: {len(pull_refs(before))} refs/pull/* refs of the remote are ignored; the server writes them "
              "for a pull request, and the push does not change them")
    old = before.get(f"refs/heads/{args.remote_branch}", "")
    print("\n".join(push_plan(args.remote, urls, args.branch, args.remote_branch,
                              [name for name in tags if f"refs/tags/{name}" not in before], commit[:7],
                              old[:7] if old else "absent")))
    if foreign:
        raise SystemExit("refused: the remote holds refs that are not snapshot refs; nothing is pushed or deleted:\n"
                         + "\n".join(f"  {ref}" for ref in foreign))
    # A refused push stops the command: with named refs the remote can accept one ref and refuse another.
    git(*push_command(args.remote, args.branch, args.remote_branch, tags, dry_run=True))
    print("push dry run ok")
    git(*push_command(args.remote, args.branch, args.remote_branch, tags))
    print(f"pushed {args.branch} to {args.remote}/{args.remote_branch}" + "".join(f", tag {name}" for name in tags))
    after = remote_refs(ROOT, args.remote)
    print("remote refs after the push:")
    print("\n".join(f"  {value}  {ref}" for ref, value in sorted(after.items())))
    if after.get(f"refs/heads/{args.remote_branch}") != commit:
        raise SystemExit(f"the remote branch {args.remote_branch} does not hold {commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
