#!/usr/bin/env python3
"""Exercise generated-key delivery with the AWS stub of scripts/check.sh."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    dry, repo = map(Path, sys.argv[1:])
    zone = dry / "secret-generated"
    zone.mkdir()
    state = json.loads((dry / "secret-core/state.json").read_text())
    (zone / "state.json").write_text(json.dumps(state))
    env = {"PATH": str(dry / "serve") + os.pathsep + os.environ["PATH"],
           "HOME": str(dry / "home"), "TMPDIR": str(dry / "tmp"),
           "CHECK_STUB_PY": str(dry / "stub.py"), "CHECK_STUB_DIR": str(zone)}
    boot = (repo / "scripts/host-bootstrap.sh").read_text()
    helper = boot.split("helper_py='\n", 1)[1].split("\n'\n", 1)[0]
    scope = {"__name__": "fixture"}
    exec(helper.split("command, args = sys.argv", 1)[0], scope)
    names = scope["MADE"]
    values = {name: "generated-fixture-%d-q9t2" % i for i, name in enumerate(names)}
    state["fakes"] += list(values.values())
    (zone / "state.json").write_text(json.dumps(state))
    with (dry / "fakes").open("a") as handle:
        handle.write("\n".join(values.values()) + "\n")
    args = ["python3", "-I", str(repo / "scripts/generated-keys.py")]
    opts = ["--project", "tenant-ec2", "--region", "us-east-1"]

    def run(command, data="", code=0, stream=False):
        result = subprocess.run(command, input=data, text=True, capture_output=True,
                                env=env, cwd=repo, timeout=30)
        require(result.returncode == code, "unexpected exit from " + command[0])
        require(not any(v in result.stderr for v in values.values()), "stderr leaks a value")
        if not stream:
            require(not any(v in result.stdout for v in values.values()), "stdout leaks a value")
        return result.stdout

    def flag(name, value):
        current = json.loads((zone / "state.json").read_text())
        current[name] = value
        (zone / "state.json").write_text(json.dumps(current))

    missing = run(args + ["read", *opts], stream=True)
    require(not any(json.loads(missing).values()), "missing secret fails first boot")
    for flags in ([], ["--require"]):
        output = run(args + ["status", *opts, *flags], code=3)
        require(output.splitlines() == [name + ": absent" for name in names]
                + ["generated: secret absent"], "missing secret status differs")
    (zone / "generated.json").write_text("{}")
    require(not any(json.loads(run(args + ["read", *opts], stream=True)).values()), "empty secret fails")
    for flags in ([], ["--require"]):
        output = run(args + ["status", *opts, *flags], code=1)
        require(output.splitlines() == [name + ": absent" for name in names], "empty secret status differs")
        for missing_name in names:
            (zone / "generated.json").write_text(json.dumps({n: v for n, v in values.items() if n != missing_name}))
            output = run(args + ["status", *opts, *flags], code=1)
            require(output.splitlines() == [n + (": absent" if n == missing_name else ": present")
                                            for n in names], "partial secret status differs")
        (zone / "generated.json").write_text("{}")
    run(args + ["put", *opts], json.dumps(values))
    require(json.loads((zone / "generated.json").read_text()) == values, "write changes values or names")
    original_ops = (zone / "ops").read_text()
    run(args + ["put", *opts, "--previous", str(zone / "generated.json")], json.dumps(values))
    require((zone / "ops").read_text() == original_ops, "unchanged repeat writes a version")
    run(args + ["put", *opts], "{}", code=1)
    for flags in ([], ["--require"]):
        output = run(args + ["status", *opts, *flags])
        require(output.splitlines() == [name + ": present" for name in names], "status prints more than presence")

    # Exercise the production environment and write-back functions, not a copy.
    prefix = boot.split("dry_run=0\n", 1)[0]
    functions = boot.split("\nstack_values\n", 1)[0].split("# run <command>", 1)[1]
    driver = zone / "bootstrap.sh"
    driver.write_text(prefix + "\n" + "# run <command>" + functions + "\n" + r'''
dry_run=0
root=$FIXTURE
repo=$REPO
state=$FIXTURE/host
env_file=$state/.env
region=us-east-1
project=tenant-ec2
secret_id=tenant-ec2/env
sample=$repo/.env.example
work=
trap '[ -z "$work" ] || rm -rf "$work"' EXIT
made=
kept=
# No host tools or services run in this fixture.
need() { :; }
edge_settings() { :; }
environment
if [ "${FIRST_BOOT:-0}" = 1 ]; then
    absent=
    edge_changed=0
    OV_ACCOUNT=pi-durable
    OV_ADMIN=owner
    OV_USER=agent
    up() { :; }
    running() { :; }
    compose() {
        case "$2" in
            -T) ;;
            *) exit 98 ;;
        esac
        case "$3" in
            openviking) helper get "$FIXTURE/values.env" EC2_PI_DURABLE_OPENVIKING_API_KEY ;;
            litellm)
                case "$9" in
                    openviking) helper get "$FIXTURE/values.env" EC2_OPENVIKING_LITELLM_API_KEY ;;
                    pi-durable) helper get "$FIXTURE/values.env" EC2_PI_DURABLE_LITELLM_API_KEY ;;
                    *) exit 98 ;;
                esac ;;
            *) exit 98 ;;
        esac
    }
    start
else
    generated_writeback
fi
printf 'bootstrap fixture: continues\n'
''')
    # environment() must not make a link in the actual checkout.
    fixture_repo = zone / "repo"
    fixture_repo.mkdir()
    for path in (repo / "scripts", repo / "caddy"):
        (fixture_repo / path.name).symlink_to(path)
    for path in [repo / ".env.example", *repo.glob("compose*.yaml")]:
        (fixture_repo / path.name).symlink_to(path)
    (fixture_repo / ".env").write_text("")
    env.update(FIXTURE=str(zone), REPO=str(fixture_repo))
    body = json.loads((dry / "secret-core/secret.json").read_text())
    body.update({name: "" for name in names})
    (zone / "secret.json").write_text(json.dumps(body))
    (zone / "host").mkdir()
    (zone / "host/.env").write_text("")
    run(["sh", "-x", str(driver)])
    require(all(scope["parse"](zone / "host/.env")[n] == values[n] for n in names), "repeat loses generated keys")
    require((zone / "host/.env").stat().st_mode & 0o777 == 0o600, "host file mode differs")
    # Missing and empty remote storage permit the real start/key functions.
    (zone / "values.env").write_text("".join(n + "=" + v + "\n" for n, v in values.items()))
    (zone / "values.env").chmod(0o600)
    for initial in (None, "{}"):
        (zone / "host/.env").write_text("")
        if initial is None:
            (zone / "generated.json").unlink()
        else:
            (zone / "generated.json").write_text(initial)
        env["FIRST_BOOT"] = "1"
        run(["sh", "-x", str(driver)])
        env.pop("FIRST_BOOT")
        require(json.loads((zone / "generated.json").read_text()) == values, "first boot does not save generated keys")
    # A missing generated secret uses the old host file, then writes it back.
    (zone / "generated.json").unlink()
    run(["sh", str(driver)])
    require(json.loads((zone / "generated.json").read_text()) == values, "fallback write-back loses keys")
    flag("put_fail", True)
    (zone / "generated.json").write_text("{}")
    output = run(["sh", str(driver)])
    require("Warning:" in output and "bootstrap fixture: continues" in output, "failed put stops bootstrap")
    require(all(scope["parse"](zone / "host/.env")[n] == values[n] for n in names), "failed put loses host keys")
    flag("put_fail", False)
    (zone / "generated.json").write_text(json.dumps(values))
    input_env = "".join(n + "=" + v + "\n" for n, v in body.items())
    run(["sh", "-x", "scripts/aws-host.sh", "secret"], input_env)
    require(all(json.loads((zone / "secret.json").read_text())[n] == values[n] for n in names), "secret does not fill empty names")
    # Explicit private-file values remain authoritative for the secret command.
    explicit = dict(body, **values)
    explicit[names[0]] = "explicit-fixture-key-q9t2"
    run(["sh", "scripts/aws-host.sh", "secret"], "".join(n + "=" + v + "\n" for n, v in explicit.items()))
    require(json.loads((zone / "secret.json").read_text())[names[0]] == explicit[names[0]], "secret overwrites explicit input")
    run(["sh", "scripts/aws-host.sh", "--env-file", str(dry / "core.env"), "generated-status"])
    flag("read_fail", True)
    for flags in ([], ["--require"]):
        output = run(args + ["status", *opts, *flags], code=1)
        require(output == "", "read failure prints presence or absence")
    run(["sh", "scripts/aws-host.sh", "secret"], input_env, code=1)
    flag("read_fail", False)
    (zone / "generated.json").write_text("{}")
    output = run(["sh", "scripts/aws-host.sh", "--env-file", str(dry / "core.env"), "verify"], code=1)
    require(all(n + ": absent" in output for n in names), "verify misses absent names")
    for bad in ('[]', '{"unexpected":"value"}', '{"' + names[0] + '":42}'):
        (zone / "generated.json").write_text(bad)
        run(args + ["status", *opts], code=1)
    require(not (zone / "problems").exists(), "AWS stub detects unsafe arguments or files")

    class Loader(yaml.SafeLoader):
        pass
    Loader.add_multi_constructor("!", lambda loader, tag, node: {tag: loader.construct_scalar(node)}
                                 if isinstance(node, yaml.ScalarNode) else {tag: loader.construct_sequence(node)})
    host = yaml.load((repo / "scripts/aws-host.cfn.yaml").read_text(), Loader=Loader)
    secret = host["Resources"]["GeneratedSecret"]
    require(secret["Properties"]["Name"] == {"Sub": "${Project}/generated"}, "wrong secret name")
    require(set(json.loads(secret["Properties"]["SecretString"])) == set(names), "wrong secret schema")
    policies = host["Resources"]["InstanceRole"]["Properties"]["Policies"]
    grants = {s["Sid"]: s for p in policies for s in p["PolicyDocument"]["Statement"] if "Sid" in s}
    require(set(grants["EnvSecretRead"]["Action"]) == {"secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"}, "env gains a right")
    grant = grants["GeneratedSecretWrite"]
    require(grant["Resource"] == {"Ref": "GeneratedSecret"}, "write grant does not name one secret")
    require(set(grant["Action"]) == {"secretsmanager:PutSecretValue", "secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"}, "unexpected generated rights")
    print("generated keys: pass (recovery, write-back, failure, presence, private files and arguments)")


class VariantTest(unittest.TestCase):
    def test_variant_namespaces_and_preservation(self):
        repo = Path(__file__).resolve().parents[1]
        work = Path(tempfile.mkdtemp(prefix="generated-variants-test-"))
        aws = work / "aws"
        aws.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
secret = args[args.index("--secret-id") + 1]
root = pathlib.Path(os.environ["FIXTURE"])
assert secret in ("tenant-ec2/generated", "tenant-ec2/macos/generated")
path = root / ("macos.json" if "/macos/" in secret else "homelab.json")
with (root / "calls").open("a") as stream: stream.write(secret + "\\n")
if "get-secret-value" in args:
    if not path.exists():
        print("ResourceNotFoundException", file=sys.stderr)
        sys.exit(1)
    print(path.read_text())
else:
    payload = args[args.index("--secret-string") + 1]
    assert payload.startswith("file://")
    source = pathlib.Path(payload[7:])
    assert source.stat().st_mode & 0o777 == 0o600
    path.write_text(source.read_text())
''')
        aws.chmod(0o700)
        names = ("EC2_OPENVIKING_LITELLM_API_KEY", "EC2_PI_DURABLE_LITELLM_API_KEY", "EC2_PI_DURABLE_OPENVIKING_API_KEY")
        homelab = {name: "test-generated-" + str(i) for i, name in enumerate(names)}
        password_name = "EC2_MACOS_DEVELOPER_PASSWORD"
        macos = {password_name: "fake-first-login-value-24", names[1]: "test-macos-generated"}
        opts = ["--project", "tenant-ec2", "--region", "us-east-1"]
        def run(command, variant=None, data=None, code=0, extra=()):
            args = [sys.executable, str(repo / "scripts/generated-keys.py"), command, *opts, *extra]
            if variant:
                args += ["--variant", variant]
            result = subprocess.run(args, input=json.dumps(data) if data is not None else "", capture_output=True,
                                    text=True, timeout=20, env={"PATH": str(work) + os.pathsep + os.environ["PATH"],
                                                              "HOME": str(work), "FIXTURE": str(work)})
            self.assertEqual(result.returncode, code, result.stderr)
            for value in (*homelab.values(), *macos.values()):
                self.assertNotIn(value, result.stderr)
                if command != "read":
                    self.assertNotIn(value, result.stdout)
            return result.stdout
        run("status", "macos", code=3)
        run("put", data=homelab)
        run("put", "macos", macos)
        self.assertEqual(json.loads(run("read")), homelab)
        self.assertEqual(json.loads(run("read", "macos")), macos)
        self.assertEqual(run("status", "macos"), password_name + ": present\n" + names[1] + ": present\n")
        self.assertEqual(run("status").splitlines(), [name + ": present" for name in names])
        run("put", "macos", homelab, code=1)
        run("put", data=macos, code=1)
        run("put", "macos", {password_name: "different-password-value"}, code=1)
        run("put", "macos", {names[1]: "replacement-gateway-key"})
        macos[names[1]] = "replacement-gateway-key"
        self.assertEqual(json.loads(run("read", "macos")), macos)
        (work / "macos.json").unlink()
        run("put", "macos", {password_name: macos[password_name]})
        self.assertEqual(run("status", "macos"), password_name + ": present\n" + names[1] + ": absent\n")
        run("put", "macos", macos)
        before = (work / "calls").read_text()
        self.assertEqual(run("put", "macos", macos, extra=("--previous", str(work / "macos.json"))), "generated: unchanged\n")
        # Repeat reads the current remote value, but does not issue another write.
        self.assertEqual(before + "tenant-ec2/macos/generated\n", (work / "calls").read_text())
        self.assertEqual(json.loads((work / "macos.json").read_text()), macos)
        before = (work / "calls").read_text()
        run("status", "unknown", code=2)
        self.assertEqual(before, (work / "calls").read_text())
        self.assertEqual(json.loads((work / "homelab.json").read_text()), homelab)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(VariantTest))
    if not result.wasSuccessful():
        sys.exit(1)
    print("generated variants: pass (homelab default, Mac subset, namespace separation and repeat preservation)")
    if len(sys.argv) > 1:
        main()
