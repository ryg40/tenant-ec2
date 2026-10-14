#!/usr/bin/env python3
"""Check environment forms with no network, host tools or credential-store write."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


REPO = Path(__file__).resolve().parent.parent


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def fixture(options):
    values = {}
    for line in (REPO / ".env.example").read_text().splitlines():
        if re.match(r"[A-Za-z_][A-Za-z0-9_]*=", line):
            name, value = line.split("=", 1)
            values[name] = value or "forms-fixture-value-7q9z"
    values.update({
        "EC2_OPTIONS": options,
        "EC2_DNS_PROVIDER": "porkbun" if "pangolin" in options else "manual",
        "EC2_DOMAIN": "forms-fixture.test",
        "EC2_ADMIN_CIDR": "192.0.2.7/32",
        "EC2_ACME_EMAIL": "forms@forms-fixture.test",
        "EC2_GERBIL_BASE_ENDPOINT": "edge.forms-fixture.test",
        "EC2_PI_DURABLE_WEB_SEARCH": "off",
    })
    return values


def main(options):
    # Every invocation has its own directory. Keep it for a failed-check inspection.
    root = Path(tempfile.mkdtemp(prefix="check-env-forms-"))
    home = root / "home"
    home.mkdir()
    temp = root / "tmp"
    temp.mkdir()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    calls = root / "calls"
    saved = root / "saved.json"
    staged = root / "staged-secret"
    # Only portable development tools exist on this PATH. Host commands refuse calls.
    import shutil
    for name in ("sh", "dirname", "awk", "sed", "grep", "envsubst", "mktemp", "rm"):
        source = shutil.which(name)
        require(source is not None, "missing development tool: " + name)
        (bin_dir / name).symlink_to(source)
    # An audit hook observes even a JSON file that the shell removes on failure.
    python = bin_dir / "python3"
    python.write_text("#!" + shutil.which("python3") + " -I\n" + """
import os
import runpy
import sys

def audit(event, args):
    if event == "open" and isinstance(args[0], str) and args[0].endswith("/secret.json"):
        if args[2] & (os.O_WRONLY | os.O_RDWR):
            with open(MARKER, "a") as handle:
                handle.write("write\\n")
sys.addaudithook(audit)
if sys.argv[1:3] == ["-I", "-c"]:
    code = sys.argv[3]
    sys.argv = ["-c", *sys.argv[4:]]
    exec(compile(code, "<string>", "exec"), {"__name__": "__main__"})
else:
    sys.argv = sys.argv[2:]
    runpy.run_path(sys.argv[0], run_name="__main__")
""".replace("MARKER", repr(str(staged))))
    python.chmod(0o755)
    stub = "#!/bin/sh\nprintf '%s\\n' \"${0##*/}\" >>" + str(calls) + "\nexit 97\n"
    for name in ("aws", "curl", "docker", "systemctl", "modprobe", "iptables", "git"):
        path = bin_dir / name
        path.write_text(stub)
        path.chmod(0o755)
    environment = {"PATH": str(bin_dir), "HOME": str(home), "TMPDIR": str(temp),
                   "FORM_CALLS": str(calls), "FORM_SAVED": str(saved)}
    values = fixture(options)
    source = root / "input.env"

    def write(data):
        source.write_text("".join(name + "=" + value + "\n" for name, value in data.items()))
        source.chmod(0o600)

    def run(args, data, stdin=False):
        write(data)
        before = (source.read_bytes(), source.stat().st_mode)
        result = subprocess.run(["sh", *args], cwd=REPO, env=environment,
                                input=source.read_text() if stdin else "",
                                capture_output=True, text=True, timeout=20)
        require(before == (source.read_bytes(), source.stat().st_mode), "form test changes its input")
        require(not list(temp.iterdir()), "form test leaves a temporary file")
        return result

    direct = ["scripts/host-bootstrap.sh", "--check-forms", str(source)]
    require(run(direct, values).returncode == 0, "valid forms fail")
    trace = run(["-x", *direct], values)
    require(trace.returncode == 0, "form test with sh -x fails")
    require("forms-fixture-value-7q9z" not in trace.stdout + trace.stderr, "form test prints a credential")
    require(len([line for line in trace.stderr.splitlines() if line.startswith("+")]) <= 2,
            "form test leaves shell tracing on")
    require(not calls.exists(), "read-only form test calls an external tool")

    # These are negative fixtures, not copies of the validators.
    cases = [
        ("EC2_ADMIN_CIDR", "invalid-admin-form", "prefix length"),
        ("EC2_ADMIN_CIDR", "192.0.2.7/15", "prefix length"),
        ("EC2_ADMIN_CIDR", "999.0.2.7/32", "IPv4"),
        ("EC2_ACME_EMAIL", "invalid-email-form", "email address"),
        ("EC2_DOMAIN", "invalid/domain-form", "letters"),
        ("EC2_PI_DURABLE_THINKING", "invalid-thinking-form", "xhigh"),
        ("EC2_PI_DURABLE_MODEL", "invalid-agent-route", "gateway route"),
        ("EC2_OPENVIKING_VLM_MODEL", "invalid-vision-route", "gateway route"),
        ("EC2_OPENVIKING_EMBED_MODEL", "invalid-embed-route", "gateway route"),
        ("EC2_OPENVIKING_EMBED_DIMENSION", "invalid-vector-form", "10 to 99999"),
        ("EC2_OPENVIKING_EMBED_DIMENSION", "9", "10 to 99999"),
        ("EC2_OPENVIKING_EMBED_DIMENSION", "100000", "10 to 99999"),
        ("EC2_PI_DURABLE_WEB_SEARCH", "invalid-search-form", "off or mcp"),
        ("EC2_EDGE_NAMES", "ok,unknownedgeform", "known names"),
        ("EC2_EDGE_NAMES", "ok,ok", "two times"),
        ("EC2_OPTIONS", "unknownoptionform", "known names"),
    ]
    if "pangolin" in options:
        cases += [
            ("EC2_GERBIL_BASE_ENDPOINT", "invalid/endpoint-form", "letters"),
            ("EC2_PANGOLIN_SITE", "invalid/site-form", "letters"),
        ]
    for name, value, rule in cases:
        bad = dict(values, **{name: value})
        for command, stdin in ((direct, False), (["scripts/aws-host.sh", "secret"], True)):
            result = run(command, bad, stdin)
            output = result.stdout + result.stderr
            require(result.returncode != 0, name + ": wrong form passes " + command[0])
            require(name in output, name + ": message lacks the setting")
            if command == direct:
                require(rule in output, name + ": message lacks its rule")
            # Short numeric fixtures can also occur in a rule; distinctive values cannot.
            if len(value) > 6:
                require(value not in output, name + ": message holds the refused value")
            require(not calls.exists(), name + ": wrong form reaches an external tool")
            require(not staged.exists(), name + ": secret writes its JSON file before the form test")

    # A stub permits a secret write. Invalid input must never get as far as this stub.
    (bin_dir / "aws").write_text("""#!/bin/sh
printf '%s\\n' "$*" >>"$FORM_CALLS"
case "$*" in
    *get-secret-value*) printf 'ResourceNotFoundException\\n' >&2; exit 254 ;;
    *describe-secret*) printf 'present\\n' ;;
    *put-secret-value* | *create-secret*)
        for arg in "$@"; do
            case "$arg" in file://*) /bin/cp "${arg#file://}" "$FORM_SAVED" ;; esac
        done ;;
    *) exit 97 ;;
esac
""")
    bad = dict(values, EC2_ADMIN_CIDR="invalid-admin-form")
    result = run(["scripts/aws-host.sh", "secret"], bad, True)
    require(result.returncode != 0, "secret accepts a wrong form")
    require(not saved.exists() and not calls.exists() and not staged.exists(), "secret writes a wrong form")
    result = run(["scripts/aws-host.sh", "secret", "--allow-empty", "EC2_DOMAIN"],
                 dict(values, EC2_DOMAIN="invalid/domain-form"), True)
    require(result.returncode != 0 and not calls.exists(), "allow-empty accepts a nonempty wrong form")

    # Preserve the generated-key and Pangolin setup exceptions. Empty domain has no form.
    empty = "EC2_DOMAIN EC2_SNAPSHOT_CLONE_TOKEN EC2_OPENVIKING_LITELLM_API_KEY "
    empty += "EC2_PI_DURABLE_LITELLM_API_KEY EC2_PI_DURABLE_OPENVIKING_API_KEY"
    if "pangolin" in options:
        empty += " EC2_PANGOLIN_API_KEY EC2_PANGOLIN_ORG"
    first = dict(values, **{name: "" for name in empty.split()})
    args = ["scripts/aws-host.sh", "secret"]
    for name in empty.split():
        args += ["--allow-empty", name]
    require(run(args, first, True).returncode == 0, "first-boot exceptions fail")
    require(saved.exists() and staged.exists(), "valid secret never reaches the file and AWS write stubs")
    body = json.loads(saved.read_text())
    require(all(body[name] == "" for name in empty.split()), "empty values change in the secret")

    # The same helper runs before bootstrap render writes the host file.
    text = (REPO / "scripts/host-bootstrap.sh").read_text()
    helper = text.split("helper_py='\n", 1)[1].split("\n'\n", 1)[0]
    code = root / "helper.py"
    code.write_text(helper)
    host_file = root / "host.env"
    result = subprocess.run([str(bin_dir / "python3"), "-I", str(code), "render",
                             str(REPO / ".env.example"), str(REPO), str(host_file)],
                            input=json.dumps(bad), env=environment, capture_output=True, text=True)
    require(result.returncode != 0 and not host_file.exists(), "bootstrap render writes a wrong form")
    # Check a real dry run at step 2, before any host command is needed.
    stack = root / "stack.env"
    stack.write_text("EC2_PROJECT=tenant-ec2\nEC2_AWS_REGION=us-east-1\nEC2_SECRET_ID=tenant-ec2/env\n")
    target = root / "srv/data/tenant-ec2"
    target.mkdir(parents=True)
    write(bad)
    (target / ".env").write_bytes(source.read_bytes())
    result = run(["scripts/host-bootstrap.sh", "--dry-run", "--root", str(root),
                  "--stack-env", str(stack)], bad)
    output = result.stdout + result.stderr
    require(result.returncode != 0 and "EC2_ADMIN_CIDR" in output, "bootstrap dry run accepts a wrong form")
    require("step 2 of 9" in output, "bootstrap dry run stops before the form test")
    require("invalid-admin-form" not in output, "bootstrap dry run prints the wrong value")
    require("step 3 of 9" not in output, "bootstrap dry run continues after a wrong form")

    for name in ("EC2_PI_DURABLE_MODEL", "EC2_OPENVIKING_VLM_MODEL", "EC2_OPENVIKING_EMBED_MODEL"):
        value = "copilot/embed" if name.endswith("EMBED_MODEL") else "copilot/luna"
        result = run(direct, dict(values, **{name: value}))
        require((result.returncode == 0) == ("copilot" in options), name + ": option route test differs")
    for value in ("10", "99999"):
        require(run(direct, dict(values, EC2_OPENVIKING_EMBED_DIMENSION=value)).returncode == 0,
                "valid vector boundary fails")
    optional = dict(values, EC2_GERBIL_BASE_ENDPOINT="", EC2_ACME_EMAIL="")
    require(run(direct, optional).returncode == 0, "empty values get form tests")
    # A newly accepted search alias must not silently enter the shared validator.
    tree = ast.parse(helper)
    validator = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "web_search")
    aliases = {node.value for node in ast.walk(validator) if isinstance(node, ast.Constant)
               and isinstance(node.value, str) and re.fullmatch(r"[a-z][a-z0-9-]*", node.value)}
    for alias in aliases - {"off", "mcp"}:
        require(run(direct, dict(values, EC2_PI_DURABLE_WEB_SEARCH=alias)).returncode != 0,
                "unsupported search alias passes")
    print("environment forms: pass (" + (options or "core") + ")")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", default="")
    args = parser.parse_args()
    try:
        main(args.options)
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit("environment forms: FAIL: " + str(error))
