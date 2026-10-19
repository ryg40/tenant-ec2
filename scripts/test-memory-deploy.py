#!/usr/bin/env python3
"""Test the memory administration script with isolated tools and no AWS calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().with_name("aws-memory.sh")

TOOLS = r'''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FIXTURE"])
mode = os.environ.get("MODE", "")
args = sys.argv[1:]
account = "0" * 12
prefix = "tenant-ec2-memory-dev-"
boundary = "arn:aws:iam::" + account + ":policy/" + prefix + "boundary"
old_id = "A" * 20
new_id = "B" * 20
secret = "C" * 40

def record(value):
    with (root / "calls").open("a") as stream:
        stream.write(json.dumps(value) + "\n")

def out(value):
    print(json.dumps(value))

if Path(sys.argv[0]).name == "bw-session-update.sh":
    value = sys.stdin.read()
    assert value == (new_id if args[0].endswith("_KEY_ID") else secret)
    record(["sync", args[0]])
    # Both streams must stay hidden even with a misbehaving credential helper.
    print(value)
    print(value, file=sys.stderr)
    if mode == "sync-fail" and args[0].endswith("_SECRET"):
        sys.exit(1)
    sys.exit(0)

assert "--profile" in args and args[args.index("--profile") + 1] == "fixture-admin"
assert os.environ["AWS_PAGER"] == "" and os.environ["AWS_CLI_AUTO_PROMPT"] == "off"
record(["aws", *args])
assert "AWS_PROFILE" not in os.environ
service = args[args.index("json") + 1:]
operation = service[1]
user = args[args.index("--user-name") + 1] if "--user-name" in args else ""
if mode == "aws-fail":
    print(secret, file=sys.stderr)
    sys.exit(1)
if operation == "describe-stacks":
    values = {
        "MemoryId": "FixtureMemory-id", "MemoryArn": "arn:aws:bedrock-agentcore:us-east-1:" + account + ":memory/FixtureMemory-id",
        "BoundaryArn": boundary, "BucketName": "tenant-ec2-memory-dev",
        "PersonUsers": prefix + "person-alice," + prefix + "person-alice-b",
        "SubmitUser": prefix + "submit", "WorkerUser": prefix + "worker",
    }
    if mode == "bad-users":
        values["PersonUsers"] += ",pi-memory-person-unrelated"
    if mode == "empty":
        values["PersonUsers"] = ""
        del values["WorkerUser"], values["SubmitUser"], values["BucketName"]
    if mode == "unknown-output":
        values["Unexpected"] = secret
    out({"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in values.items()]}]})
elif operation == "list-access-keys":
    count = 0 if mode in ("missing", "sync-fail") else 2 if mode == "two-keys" else 1
    out({"AccessKeyMetadata": [{"UserName": user, "AccessKeyId": old_id if n == 0 else new_id,
                               "Status": "Active"} for n in range(count)]})
elif operation == "create-access-key":
    out({"AccessKey": {"UserName": user, "AccessKeyId": new_id, "SecretAccessKey": secret, "Status": "Active"}})
elif operation == "delete-access-key":
    assert args[args.index("--access-key-id") + 1] == new_id, "an old key must never be deleted"
elif operation == "get-user":
    out({"User": {"UserName": user, "PermissionsBoundary": {
        "PermissionsBoundaryArn": "wrong" if mode == "bad-boundary" else boundary}}})
elif operation == "list-user-policies":
    out({"PolicyNames": [] if mode == "bad-policy" else ["agentcore-memory"]})
elif operation == "list-attached-user-policies":
    out({"AttachedPolicies": [{"PolicyArn": "arn:aws:iam::" + ("aws" if mode == "bad-policy" else account) + ":policy/fixture"}]})
elif operation == "deploy":
    assert "--no-execute-changeset" in args
    assert "--capabilities" in args and "CAPABILITY_NAMED_IAM" in args
    assert Path(args[args.index("--template-file") + 1]).is_file()
    if mode == "no-change":
        print("No changes to deploy. Stack is up to date")
    elif mode == "unknown-change-set":
        print("No review command returned")
    else:
        print("Changeset created successfully. Run the following command to review changes")
        print("aws cloudformation describe-change-set --change-set-name arn:aws:cloudformation:us-east-1:" + account + ":changeSet/fixture-changes/fixture-id")
elif operation == "describe-change-set":
    assert args[args.index("--change-set-name") + 1].endswith(":changeSet/fixture-changes/fixture-id")
    out({"Status": "FAILED" if mode == "bad-change-set" else "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE",
         "Changes": [{"ResourceChange": {"Action": "Modify", "LogicalResourceId": "Memory", "ResourceType": "AWS::BedrockAgentCore::Memory", "Replacement": "False"}}]})
elif operation == "execute-change-set":
    assert args[args.index("--change-set-name") + 1].endswith(":changeSet/fixture-changes/fixture-id")
elif operation == "delete-stack":
    pass
else:
    raise AssertionError("unexpected operation")
'''

RENDERER = r'''import json, os, sys
from pathlib import Path
root = Path(os.environ["FIXTURE"])
with (root / "calls").open("a") as stream:
    stream.write(json.dumps(["renderer", *sys.argv[1:]]) + "\n")
if os.environ.get("MODE") == "stale":
    sys.exit(1)
if sys.argv[1] == "client-config":
    assert sys.argv[sys.argv.index("--memory-id") + 1] == "FixtureMemory-id"
    print(json.dumps({"schemaVersion": 1, "memoryId": "FixtureMemory-id", "region": "us-east-1",
                      "profile": "tenant-ec2-memory-dev-person-alice", "actorId": "alice", "projects": []}))
else:
    assert sys.argv[1:] == ["check", "--stage", "dev"]
'''


class MemoryScriptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="memory-deploy-test-"))
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        self.script = self.scripts / SOURCE.name
        shutil.copyfile(SOURCE, self.script)
        (self.scripts / "aws-memory.py").write_text(RENDERER)
        (self.scripts / "aws-memory-dev.cfn.yaml").write_text("Resources: {}\n")
        tools = self.root / "bin"
        tools.mkdir()
        for tool in ("aws", "bw-session-update.sh"):
            path = tools / tool
            path.write_text(TOOLS)
            path.chmod(0o755)
        self.env = {"PATH": str(tools) + ":/usr/bin:/bin", "HOME": str(self.root),
                    "FIXTURE": str(self.root), "EC2_AWS_ADMIN_PROFILE": "fixture-admin"}

    def run_script(self, *args, mode="", ok=True, trace=False, env=None):
        result = subprocess.run(["/bin/bash", *(["-x"] if trace else []), str(self.script), *args],
                                cwd=self.root, env={**self.env, "MODE": mode, **(env or {})},
                                stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertNotIn("C" * 40, result.stdout + result.stderr)
        self.assertNotIn("B" * 20, result.stdout + result.stderr)
        self.assertNotIn("0" * 12, result.stdout + result.stderr)
        return result

    def calls(self):
        path = self.root / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def operations(self):
        return [row[row.index("json") + 2] for row in self.calls() if row[0] == "aws"]

    def test_deploy_dry_run_no_aws(self):
        for flags in ([], ["--dry-run"]):
            result = self.run_script("deploy", "--stage", "dev", *flags)
            self.assertIn("cloudformation deploy", result.stdout)
            self.assertIn("--no-execute-changeset", result.stdout)
            self.assertIn("bootstrap-tenant-ec2-memory-dev", result.stdout)
            self.assertIn("Stage=dev", result.stdout)
            self.assertEqual(self.operations(), [])
        self.assertEqual(self.calls(), [["renderer", "check", "--stage", "dev"]] * 2)

    def test_administrator_parameters(self):
        result = self.run_script("deploy", "--stage", "dev", "--dry-run")
        self.assertIn("AdministratorRole=Administrator", result.stdout)
        self.assertIn("AdministratorUser= ", result.stdout)
        self.assertEqual(self.operations(), [])
        for user in ("ExampleAdmin", ""):
            result = self.run_script("deploy", "--stage", "dev", "--apply", "--profile", "fixture-admin",
                                     env={"EC2_MEMORY_ADMINISTRATOR_ROLE": "ExampleRole", "EC2_MEMORY_ADMINISTRATOR_USER": user})
            deploy = [row for row in self.calls() if row[0] == "aws" and "deploy" in row][-1]
            self.assertIn("AdministratorRole=ExampleRole", deploy)
            self.assertIn("AdministratorUser=" + user, deploy)

    def test_administrator_names_refuse_paths_and_wildcards_before_calls(self):
        for setting in ("EC2_MEMORY_ADMINISTRATOR_ROLE", "EC2_MEMORY_ADMINISTRATOR_USER"):
            for value in ("*", "admin?", "team/admin", "a" * 65, "admin name"):
                with self.subTest(setting=setting, value=value):
                    self.run_script("deploy", "--stage", "dev", "--apply", "--profile", "fixture-admin",
                                    env={setting: value}, ok=False)
        self.assertEqual(self.calls(), [])

    def test_missing_or_stale_renderer_stops_deploy(self):
        self.run_script("deploy", "--stage", "dev", "--apply", "--profile", "fixture-admin", mode="stale", ok=False)
        (self.scripts / "aws-memory.py").unlink()
        self.run_script("deploy", "--stage", "dev", "--dry-run", ok=False)
        self.assertEqual(self.operations(), [])

    def test_apply_guard_and_invalid_options(self):
        for args in (("deploy", "--stage", "dev", "--apply"),
                     ("keys", "--stage", "dev", "--apply"),
                     ("rotate", "--stage", "dev", "--user", "test", "--apply"),
                     ("delete-stack", "--stage", "dev", "--apply"),
                     ("deploy", "--stage", "dev", "--apply", "--dry-run", "--profile", "fixture-admin"),
                     ("deploy", "--stage", "../dev"), ("deploy", "--stage"),
                     ("keys", "--stage", "dev", "--write"), ("outputs", "--stage", "dev", "--apply"),
                     ("rotate", "--stage", "dev"), ("client-config", "--stage", "dev", "--actor", "../alice"),
                     ("delete-stack", "--stage", "prod")):
            self.run_script(*args, ok=False)
        self.assertEqual(self.calls(), [])

    def test_change_set_execution(self):
        result = self.run_script("deploy", "--stage", "dev", "--apply", "--profile", "fixture-admin")
        self.assertEqual(self.operations(), ["deploy", "describe-change-set", "execute-change-set"])
        self.assertIn("Modify Memory AWS::BedrockAgentCore::Memory False", result.stdout)
        self.assertLess(result.stdout.index("Modify Memory"), result.stdout.index("execute-change-set"))

    def test_empty_or_unavailable_change_set_never_executes(self):
        for mode in ("no-change", "unknown-change-set", "bad-change-set"):
            self.run_script("deploy", "--stage", "dev", "--apply", "--profile", "fixture-admin",
                            mode=mode, ok=mode == "no-change")
        self.assertNotIn("execute-change-set", self.operations())

    def test_outputs_cache_and_client_config(self):
        self.run_script("client-config", "--stage", "dev", "--actor", "alice", ok=False)
        self.assertEqual(self.calls(), [])
        result = self.run_script("outputs", "--stage", "dev", "--write")
        path = self.root / ".local/memory/dev.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn("0" * 12, path.read_text())
        self.assertIn("<account>", result.stdout)
        self.assertEqual(json.loads(path.read_text())["MemoryId"], "FixtureMemory-id")
        path.chmod(0o644)
        self.run_script("outputs", "--stage", "dev", "--write")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        before = self.operations()
        result = self.run_script("client-config", "--stage", "dev", "--actor", "alice")
        self.assertEqual(json.loads(result.stdout)["actorId"], "alice")
        self.assertEqual(self.operations(), before)
        self.run_script("outputs", "--stage", "dev", mode="unknown-output", ok=False)

    def test_cache_symlink_is_refused(self):
        target = self.root / "unrelated"
        target.write_text("keep")
        (self.root / ".local/memory").mkdir(parents=True)
        (self.root / ".local/memory/dev.json").symlink_to(target)
        self.run_script("outputs", "--stage", "dev", "--write", ok=False)
        self.assertEqual(target.read_text(), "keep")

    def test_users_and_scope_refusal(self):
        result = self.run_script("users", "--stage", "dev")
        self.assertEqual(len(result.stdout.splitlines()), 4)
        self.run_script("users", "--stage", "dev", mode="bad-users", ok=False)
        result = self.run_script("users", "--stage", "dev", mode="empty")
        self.assertEqual(result.stdout, "")
        self.run_script("rotate", "--stage", "dev", "--user", "pi-memory-person-alice", "--apply", "--profile", "fixture-admin", ok=False)
        self.assertNotIn("create-access-key", self.operations())

    def test_keys_dry_run_and_idempotence(self):
        result = self.run_script("keys", "--stage", "dev", mode="missing")
        self.assertEqual(len(result.stdout.splitlines()), 4)
        self.run_script("keys", "--stage", "dev", "--apply", "--profile", "fixture-admin")
        self.assertNotIn("create-access-key", self.operations())
        self.assertFalse(any(row[0] == "sync" for row in self.calls()))

    def test_keys_sync_stdin_and_trace_suppression(self):
        result = self.run_script("keys", "--stage", "dev", "--apply", "--profile", "fixture-admin", mode="missing", trace=True)
        self.assertEqual(len(result.stdout.splitlines()), 4)
        self.assertEqual(self.operations().count("create-access-key"), 4)
        sync_names = [row[1] for row in self.calls() if row[0] == "sync"]
        self.assertEqual(sync_names, ["TENANT_EC2_MEMORY_DEV_" + suffix + part
                                     for suffix in ("PERSON_ALICE", "PERSON_ALICE_B", "SUBMIT", "WORKER")
                                     for part in ("_KEY_ID", "_SECRET")])
        self.assertNotIn("delete-access-key", self.operations())

    def test_sync_failure_deletes_only_new_key(self):
        self.run_script("keys", "--stage", "dev", "--apply", "--profile", "fixture-admin", mode="sync-fail", ok=False)
        self.assertEqual(self.operations()[-2:], ["create-access-key", "delete-access-key"])

    def test_missing_sync_stops_before_aws(self):
        (self.root / "bin/bw-session-update.sh").unlink()
        self.run_script("keys", "--stage", "dev", "--apply", "--profile", "fixture-admin", mode="missing", ok=False)
        self.assertEqual(self.calls(), [])

    def test_rotation_keeps_old_key(self):
        user = "tenant-ec2-memory-dev-person-alice"
        self.run_script("rotate", "--stage", "dev", "--user", user)
        self.assertNotIn("create-access-key", self.operations())
        result = self.run_script("rotate", "--stage", "dev", "--user", user, "--apply", "--profile", "fixture-admin")
        self.assertIn("delete-access-key", result.stdout)
        self.assertIn("A" * 20, result.stdout)
        self.assertNotIn("delete-access-key", self.operations())
        self.run_script("rotate", "--stage", "dev", "--user", user, mode="two-keys", ok=False)
        self.run_script("keys", "--stage", "dev", "--apply", "--profile", "fixture-admin", mode="two-keys", ok=False)
        self.assertEqual(self.operations().count("create-access-key"), 1)

    def test_check_reports_each_difference(self):
        result = self.run_script("check", "--stage", "dev")
        self.assertEqual(result.stdout.count("keys=1 boundary=ok policy=ok"), 4)
        for mode in ("missing", "two-keys", "bad-boundary", "bad-policy"):
            self.run_script("check", "--stage", "dev", mode=mode, ok=False)
        self.assertNotIn("create-access-key", self.operations())

    def test_delete_plan_and_explicit_apply(self):
        result = self.run_script("delete-stack", "--stage", "dev")
        self.assertIn("Retained resources:", result.stdout)
        self.assertIn("Bucket: tenant-ec2-memory-dev", result.stdout)
        self.assertNotIn("delete-stack", self.operations())
        self.run_script("delete-stack", "--stage", "prod", "--apply", "--profile", "fixture-admin", ok=False)
        self.assertNotIn("delete-stack", self.operations())
        self.run_script("delete-stack", "--stage", "dev", "--apply", "--profile", "fixture-admin")
        self.assertEqual(self.operations()[-1], "delete-stack")

    def test_aws_errors_are_redacted(self):
        self.run_script("outputs", "--stage", "dev", mode="aws-fail", ok=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
