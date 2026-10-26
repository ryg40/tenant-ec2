#!/usr/bin/env python3
"""Test the memory administration script with isolated tools and no AWS calls."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().with_name("aws-memory.sh")
IDENTITY_TEMPLATE = SOURCE.with_name("aws-memory-identity.cfn.yaml")
# Fixture Entra values of the dev stage. None may reach an argument or an output.
ENTRA = {"TENANT_EC2_MEMORY_DEV_ENTRA_TENANT_ID": "E" * 36,
         "TENANT_EC2_MEMORY_DEV_ENTRA_CLIENT_ID": "D" * 36,
         "TENANT_EC2_MEMORY_DEV_ENTRA_CLIENT_SECRET": "F" * 40}

TOOLS = r'''#!/usr/bin/python3
import json, os, sys, time
from pathlib import Path
root = Path(os.environ["FIXTURE"])
mode = os.environ.get("MODE", "")
args = sys.argv[1:]
account = "0" * 12
prefix = "tenant-ec2-memory-dev-"
boundary = "arn:aws:iam::" + account + ":policy/" + prefix + "boundary"
stack = "bootstrap-tenant-ec2-memory-dev"
change_arn = "arn:aws:cloudformation:us-east-1:" + account + ":changeSet/fixture-changes/fixture-id"
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
identity_stack = "bootstrap-tenant-ec2-memory-identity-dev"
named = args[args.index("--stack-name") + 1] if "--stack-name" in args else ""
if operation == "describe-stacks" and not named:
    rows = [{"StackName": stack, "StackStatus": "CREATE_COMPLETE"}]
    if mode.startswith("status-"):
        rows.append({"StackName": identity_stack, "StackStatus": mode[len("status-"):]})
    out({"Stacks": rows})
    sys.exit(0)
if named == identity_stack:
    saved = root / "identity-change-set"
    if operation == "describe-stacks":
        values = {"UserPoolId": "us-east-1_Fixture0", "ClientId": "fixtureclient0",
                  "Domain": "tenant-ec2-memory-dev.auth.us-east-1.amazoncognito.com",
                  "IdentityPoolId": "us-east-1:00000000-0000-4000-8000-000000000000",
                  "ProviderName": "cognito-idp.us-east-1.amazonaws.com/us-east-1_Fixture0"}
        if mode == "unknown-output":
            values["Unexpected"] = secret
        out({"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in values.items()]}]})
    elif operation == "create-change-set":
        assert "--change-set-type" in args and "--template-body" in args
        assert Path(args[args.index("--template-body") + 1][len("file://"):]).is_file()
        source = args[args.index("--parameters") + 1]
        assert source.startswith("file:///dev/shm/")
        path = Path(source[len("file://"):])
        rows = json.loads(path.read_text())
        record(["file", str(path), oct(path.stat().st_mode & 0o777),
                args[args.index("--change-set-type") + 1]])
        values = {row["ParameterKey"]: row["ParameterValue"] for row in rows}
        assert values == {"Project": "tenant-ec2", "Stage": "dev", "EntraTenantId": "E" * 36,
                          "EntraClientId": "D" * 36, "EntraClientSecret": "F" * 40}
        name = args[args.index("--change-set-name") + 1]
        saved.write_text(name)
        if mode == "create-fail":
            print("F" * 40, file=sys.stderr)
            sys.exit(254)
        if mode == "create-hang":
            (root / "hang").write_text("")
            time.sleep(30)
        out({"Id": "arn:aws:cloudformation:us-east-1:" + account + ":changeSet/" + name + "/fixture-id",
             "StackId": "arn:aws:cloudformation:us-east-1:" + account + ":stack/" + identity_stack + "/fixture-stack-id"})
    elif operation == "wait":
        assert service[2] == "change-set-create-complete"
        assert args[args.index("--change-set-name") + 1] == saved.read_text()
    elif operation == "describe-change-set":
        asked = args[args.index("--change-set-name") + 1]
        if mode == "missing-change-set":
            sys.exit(254)
        name = "fixture-other" if mode == "other-name" else asked
        stack_name = identity_stack.replace("-dev", "-test") if mode == "other-stack" else identity_stack
        arn = "arn:aws:cloudformation:" + ("us-west-2" if mode == "other-region" else "us-east-1") + ":" + account + ":"
        parameters = {"Project": "tenant-ec2", "Stage": "test" if mode == "other-stage" else "dev",
                      "EntraTenantId": "****", "EntraClientId": "****", "EntraClientSecret": "****"}
        if mode == "missing-entra":
            del parameters["EntraClientSecret"]
        if mode == "extra-parameter":
            parameters["Extra"] = "x"
        reply = {"StackName": stack_name, "StackId": arn + "stack/" + stack_name + "/fixture-stack-id",
                 "ChangeSetName": name, "ChangeSetId": arn + "changeSet/" + name + "/fixture-id",
                 "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k, v in parameters.items()],
                 "Status": "FAILED" if mode == "bad-change-set" else "CREATE_COMPLETE",
                 "ExecutionStatus": "EXECUTE_COMPLETE" if mode == "used-change-set" else "AVAILABLE",
                 "Changes": [{"ResourceChange": {"Action": "Add", "LogicalResourceId": "IdentityPool",
                                                 "ResourceType": "AWS::Cognito::IdentityPool", "Replacement": "-"}}]}
        out(reply)
    elif operation == "execute-change-set":
        name = args[args.index("--change-set-name") + 1]
        assert name == "arn:aws:cloudformation:us-east-1:" + account + ":changeSet/identity-fixture/fixture-id", \
            "only the checked change set executes"
    else:
        raise AssertionError("unexpected identity operation")
    sys.exit(0)
oid = "00000000-0000-4000-8000-0000000000aa"
if operation == "list-users":
    assert args[args.index("--user-pool-id") + 1] == "us-east-1_Fixture0"
    def person(name, entra, pool_sub):
        return {"Username": name, "Attributes": [{"Name": "sub", "Value": pool_sub}, {"Name": "custom:entra_oid", "Value": entra}]}
    users = [person("entra_a", oid, "00000000-0000-4000-8000-0000000000b1"),
             person("entra_b", "00000000-0000-4000-8000-0000000000cc", "00000000-0000-4000-8000-0000000000b2")]
    if mode == "two-records":
        users.append(person("entra_c", oid, "00000000-0000-4000-8000-0000000000b3"))
    out({"Users": users})
elif operation == "describe-stacks":
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
    if mode.startswith("roles"):
        values["PersonRoles"] = prefix + "person-alice-role," + prefix + "person-bruno-role"
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
elif operation == "get-role":
    out({"Role": {"RoleName": args[args.index("--role-name") + 1], "PermissionsBoundary": {
        "PermissionsBoundaryArn": "wrong" if mode == "roles-bad-boundary" else boundary}}})
elif operation == "list-role-policies":
    out({"PolicyNames": [] if mode == "roles-bad-policy" else ["agentcore-memory"]})
elif operation == "list-user-policies":
    out({"PolicyNames": [] if mode == "bad-policy" else ["agentcore-memory"]})
elif operation == "list-attached-user-policies":
    out({"AttachedPolicies": [{"PolicyArn": "arn:aws:iam::" + ("aws" if mode == "bad-policy" else account) + ":policy/fixture"}]})
elif operation == "deploy":
    assert "--no-execute-changeset" in args
    assert "--capabilities" in args and "CAPABILITY_NAMED_IAM" in args
    assert Path(args[args.index("--template-file") + 1]).is_file()
    # The created change set keeps the parameters of this call.
    overrides = args[args.index("--parameter-overrides") + 1:]
    (root / "change-set-parameters").write_text(json.dumps(dict(o.split("=", 1) for o in overrides)))
    if mode == "no-change":
        print("No changes to deploy. Stack is up to date")
    elif mode == "unknown-change-set":
        print("No review command returned")
    else:
        print("Changeset created successfully. Run the following command to review changes")
        print("aws cloudformation describe-change-set --change-set-name " + change_arn)
elif operation == "wait":
    assert service[2] == "change-set-create-complete"
    assert args[args.index("--change-set-name") + 1] == change_arn
    if mode == "bad-change-set":
        sys.exit(255)
elif operation == "describe-change-set":
    assert args[args.index("--stack-name") + 1] == stack
    assert args[args.index("--change-set-name") + 1] in ("fixture-changes", change_arn)
    if mode == "missing-change-set":
        print(secret, file=sys.stderr)
        sys.exit(254)
    saved = root / "change-set-parameters"
    parameters = json.loads(saved.read_text()) if saved.exists() else {
        "Project": "tenant-ec2", "Stage": "dev", "AdministratorRole": "Administrator", "AdministratorUser": ""}
    if mode == "other-admin":
        parameters["AdministratorUser"] = "ExampleAdmin"
    if mode == "extra-parameter":
        parameters["Extra"] = "x"
    name = "fixture-other" if mode == "other-name" else "fixture-changes"
    stack_name = stack.replace("-dev", "-test") if mode == "other-stack" else stack
    arn = "arn:aws:cloudformation:" + ("us-west-2" if mode == "other-region" else "us-east-1") + ":" + account + ":"
    reply = {"StackName": stack_name, "StackId": arn + "stack/" + stack_name + "/fixture-stack-id",
             "ChangeSetName": name, "ChangeSetId": arn + "changeSet/" + name + "/fixture-id",
             "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k, v in parameters.items()],
             "Status": "FAILED" if mode == "bad-change-set" else "CREATE_COMPLETE",
             "ExecutionStatus": "EXECUTE_COMPLETE" if mode == "used-change-set" else "AVAILABLE",
             "Changes": [{"ResourceChange": {"Action": "Modify", "LogicalResourceId": "Memory", "ResourceType": "AWS::BedrockAgentCore::Memory", "Replacement": "False"}}]}
    if mode == "bad-change-set":
        reply["StatusReason"] = "Fixture failure in account " + account
    role = {"LogicalResourceId": "PersonAliceRole", "ResourceType": "AWS::IAM::Role"}
    if mode == "role-removal":
        reply["Changes"].append({"ResourceChange": {**role, "Action": "Remove", "PhysicalResourceId": prefix + "person-alice-role"}})
    if mode == "role-add":
        reply["Changes"] = [{"ResourceChange": {**role, "Action": "Add", "Replacement": "-"}}]
    out(reply)
elif operation == "execute-change-set":
    assert args[args.index("--stack-name") + 1] == stack
    assert args[args.index("--change-set-name") + 1] == change_arn, "only the checked change set executes"
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
        shutil.copyfile(IDENTITY_TEMPLATE, self.scripts / IDENTITY_TEMPLATE.name)
        (self.root / "config/memory").mkdir(parents=True)
        (self.root / "config/memory/dev.yaml").write_text("people: [{actorId: alice, status: active}]\n")
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
        for value in ENTRA.values():
            self.assertNotIn(value, result.stdout + result.stderr)
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
            result = self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin",
                                     env={"EC2_MEMORY_ADMINISTRATOR_ROLE": "ExampleRole", "EC2_MEMORY_ADMINISTRATOR_USER": user})
            deploy = [row for row in self.calls() if row[0] == "aws" and "deploy" in row][-1]
            self.assertIn("AdministratorRole=ExampleRole", deploy)
            self.assertIn("AdministratorUser=" + user, deploy)

    def test_administrator_names_refuse_paths_and_wildcards_before_calls(self):
        for setting in ("EC2_MEMORY_ADMINISTRATOR_ROLE", "EC2_MEMORY_ADMINISTRATOR_USER"):
            for value in ("*", "admin?", "team/admin", "a" * 65, "admin name"):
                for step in (["--prepare"], ["--execute", "fixture-changes"]):
                    with self.subTest(setting=setting, value=value, step=step[0]):
                        self.run_script("deploy", "--stage", "dev", *step, "--profile", "fixture-admin",
                                        env={setting: value}, ok=False)
        self.assertEqual(self.calls(), [])

    def test_missing_or_stale_renderer_stops_deploy(self):
        self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin", mode="stale", ok=False)
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
                     ("delete-stack", "--stage", "prod"),
                     ("deploy", "--stage", "dev", "--prepare"),
                     ("deploy", "--stage", "dev", "--execute", "fixture-changes"),
                     ("deploy", "--stage", "dev", "--prepare", "--dry-run", "--profile", "fixture-admin"),
                     ("deploy", "--stage", "dev", "--execute", "fixture-changes", "--dry-run", "--profile", "fixture-admin"),
                     ("deploy", "--stage", "dev", "--prepare", "--execute", "fixture-changes", "--profile", "fixture-admin"),
                     ("deploy", "--stage", "dev", "--execute", "--profile", "fixture-admin"),
                     ("deploy", "--stage", "dev", "--execute", "1-changes", "--profile", "fixture-admin"),
                     ("deploy", "--stage", "dev", "--execute", "a" * 129, "--profile", "fixture-admin"),
                     ("deploy", "--stage", "dev", "--execute", "arn:aws:cloudformation:us-east-1:" + "0" * 12
                      + ":changeSet/fixture-changes/fixture-id", "--profile", "fixture-admin"),
                     ("keys", "--stage", "dev", "--prepare", "--profile", "fixture-admin"),
                     ("deploy", "--stage", "dev", "--allow-role-removal"),
                     ("identity", "--stage", "dev", "--prepare", "--allow-role-removal", "--profile", "fixture-admin"),
                     ("overlay", "--stage", "dev"), ("check", "--stage", "dev", "--compare"),
                     ("outputs", "--stage", "dev", "--execute", "fixture-changes")):
            with self.subTest(args=args):
                self.run_script(*args, ok=False)
        self.assertEqual(self.calls(), [])

    def test_deploy_apply_refuses_and_names_two_steps(self):
        result = self.run_script("deploy", "--stage", "dev", "--apply", "--profile", "fixture-admin", ok=False)
        self.assertIn("--prepare", result.stderr)
        self.assertIn("--execute <change-set-name>", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_prepare_shows_change_set_and_stops(self):
        result = self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin")
        self.assertEqual(self.operations(), ["deploy", "wait", "describe-change-set"])
        for text in ("Stack: bootstrap-tenant-ec2-memory-dev", "Change set: fixture-changes",
                     "Status: CREATE_COMPLETE", "Execution status: AVAILABLE", "Resource changes: 1",
                     "Modify Memory AWS::BedrockAgentCore::Memory False", "Nothing executes in this step."):
            self.assertIn(text, result.stdout)
        command = "scripts/aws-memory.sh deploy --stage dev --execute fixture-changes --profile fixture-admin"
        self.assertIn(command, result.stdout)
        self.assertLess(result.stdout.index("Modify Memory"), result.stdout.index(command))
        self.assertNotIn("execute-change-set", result.stdout)

    def test_prepare_without_a_ready_change_set(self):
        self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin", mode="no-change")
        self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin", mode="unknown-change-set", ok=False)
        self.assertEqual(self.operations(), ["deploy", "deploy"])
        result = self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin", mode="bad-change-set", ok=False)
        self.assertIn("Status: FAILED", result.stdout)
        self.assertIn("Reason: Fixture failure in account <account>", result.stdout)
        self.assertIn("not ready", result.stderr)
        self.assertNotIn("--execute", result.stdout)
        self.assertNotIn("execute-change-set", self.operations())

    def test_execute_checks_then_executes_the_named_set(self):
        result = self.run_script("deploy", "--stage", "dev", "--execute", "fixture-changes",
                                 "--profile", "fixture-admin", trace=True)
        self.assertEqual(self.operations(), ["describe-change-set", "execute-change-set"])
        self.assertFalse(any(row[0] == "renderer" for row in self.calls()))
        describe = [row for row in self.calls() if "describe-change-set" in row][0]
        self.assertEqual(describe[describe.index("--change-set-name") + 1], "fixture-changes")
        self.assertIn("Change set: fixture-changes", result.stdout)
        self.assertIn("execute-change-set --stack-name bootstrap-tenant-ec2-memory-dev --change-set-name fixture-changes",
                      result.stdout)
        self.assertIn("describe-stacks --stack-name bootstrap-tenant-ec2-memory-dev", result.stdout)
        self.assertLess(result.stdout.index("Modify Memory"), result.stdout.index("execute-change-set"))
        self.assertLess(result.stdout.index("execute-change-set"), result.stdout.index("describe-stacks"))

    def test_execute_refuses_another_or_unready_change_set(self):
        for mode in ("missing-change-set", "bad-change-set", "used-change-set", "other-stack", "other-name",
                     "other-region", "other-admin", "extra-parameter"):
            with self.subTest(mode=mode):
                self.run_script("deploy", "--stage", "dev", "--execute", "fixture-changes",
                                "--profile", "fixture-admin", mode=mode, ok=False)
        self.assertNotIn("execute-change-set", self.operations())

    def test_both_steps_refuse_a_role_removal(self):
        for step in (["--prepare"], ["--execute", "fixture-changes"]):
            with self.subTest(step=step[0]):
                result = self.run_script("deploy", "--stage", "dev", *step, "--profile", "fixture-admin",
                                         mode="role-removal", ok=False)
                self.assertIn("tenant-ec2-memory-dev-person-alice-role", result.stderr)
                self.assertIn("delete-change-set", result.stderr)
                self.assertIn("fixture-changes", result.stderr)
        self.assertNotIn("execute-change-set", self.operations())

    def test_the_option_allows_a_role_removal(self):
        result = self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin",
                                 "--allow-role-removal", mode="role-removal")
        self.assertIn("--execute fixture-changes", result.stdout)
        self.assertIn("--allow-role-removal", result.stdout)
        self.assertNotIn("execute-change-set", self.operations())
        self.run_script("deploy", "--stage", "dev", "--execute", "fixture-changes", "--profile", "fixture-admin",
                        "--allow-role-removal", mode="role-removal")
        self.assertEqual(self.operations()[-1], "execute-change-set")

    def test_an_added_role_needs_no_option(self):
        result = self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin", mode="role-add")
        self.assertNotIn("--allow-role-removal", result.stdout)
        self.run_script("deploy", "--stage", "dev", "--execute", "fixture-changes", "--profile", "fixture-admin", mode="role-add")
        self.assertEqual(self.operations()[-1], "execute-change-set")

    def test_overlay_compare_with_the_saved_copy(self):
        identity = "us-east-1:00000000-0000-4000-8000-000000000001"
        overlay = "schemaVersion: 1\nstage: dev\nidentities:\n  alice: " + identity + "\n"
        (self.root / ".local/memory").mkdir(parents=True)
        (self.root / ".local/memory/dev.identities.yaml").write_text(overlay)
        name = "TENANT_EC2_MEMORY_DEV_IDENTITIES"
        # Credential sync drops the last newline of a value.
        result = self.run_script("overlay", "--stage", "dev", "--compare", env={name: overlay.rstrip("\n")})
        self.assertIn("equal", result.stdout)
        for saved in (overlay.replace("0001", "0002"), ""):
            with self.subTest(saved=saved):
                result = self.run_script("overlay", "--stage", "dev", "--compare", env={name: saved}, ok=False)
                self.assertIn("differs" if saved else "no saved copy", result.stdout)
                self.assertNotIn("00000000-0000-4000", result.stdout + result.stderr)
        self.assertEqual(self.calls(), [])

    def test_both_steps_keep_the_administrator_exceptions(self):
        admin = {"EC2_MEMORY_ADMINISTRATOR_ROLE": "ExampleRole", "EC2_MEMORY_ADMINISTRATOR_USER": "ExampleAdmin"}
        self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin", env=admin)
        for env in ({}, {**admin, "EC2_MEMORY_ADMINISTRATOR_USER": ""}, {**admin, "EC2_MEMORY_ADMINISTRATOR_ROLE": "Administrator"}):
            with self.subTest(env=env):
                result = self.run_script("deploy", "--stage", "dev", "--execute", "fixture-changes",
                                         "--profile", "fixture-admin", env=env, ok=False)
                self.assertIn("parameters differ", result.stderr)
        self.assertNotIn("execute-change-set", self.operations())
        self.run_script("deploy", "--stage", "dev", "--execute", "fixture-changes", "--profile", "fixture-admin", env=admin)
        self.assertEqual(self.operations()[-1], "execute-change-set")

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

    def test_client_config_writes_the_sign_in_file(self):
        self.run_script("outputs", "--stage", "dev", "--write")
        (self.root / "out").mkdir()
        result = self.run_script("client-config", "--stage", "dev", "--actor", "alice", "--out", "out/config.json")
        self.assertIn("sign-in file: none", result.stdout)
        self.assertFalse((self.root / "out/tenant-ec2-memory-dev-person-alice.signin.json").exists())
        self.run_script("outputs", "--stage", "dev", "--identity", mode="unknown-output", ok=False)
        result = self.run_script("outputs", "--stage", "dev", "--identity", "--write")
        self.assertIn("us-east-1_Fixture0", result.stdout)
        saved = self.root / ".local/memory/dev-identity.json"
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        result = self.run_script("client-config", "--stage", "dev", "--actor", "alice", "--out", "out/config.json")
        config = self.root / "out/config.json"
        signin = self.root / "out/tenant-ec2-memory-dev-person-alice.signin.json"
        self.assertIn("sign-in file: out/tenant-ec2-memory-dev-person-alice.signin.json", result.stdout)
        for path in (config, signin):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(config.read_text())["profile"], "tenant-ec2-memory-dev-person-alice")
        self.assertEqual(json.loads(signin.read_text()), {
            "schemaVersion": 1, "profile": "tenant-ec2-memory-dev-person-alice", "actorId": "alice",
            "region": "us-east-1", "domain": "tenant-ec2-memory-dev.auth.us-east-1.amazoncognito.com",
            "clientId": "fixtureclient0", "identityPoolId": "us-east-1:00000000-0000-4000-8000-000000000000",
            "providerName": "cognito-idp.us-east-1.amazonaws.com/us-east-1_Fixture0",
            "roleArn": "arn:aws:iam::" + "0" * 12 + ":role/tenant-ec2-memory-dev-person-alice-role"})
        self.run_script("outputs", "--stage", "dev", "--out", "x", ok=False)
        self.run_script("client-config", "--stage", "dev", "--actor", "alice", "--identity", ok=False)

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

    def test_check_reports_roles_and_keys_ignores_them(self):
        result = self.run_script("check", "--stage", "dev", mode="roles")
        self.assertIn("tenant-ec2-memory-dev-person-alice: keys=1 boundary=ok policy=ok", result.stdout)
        for role in ("alice", "bruno"):
            self.assertIn(f"tenant-ec2-memory-dev-person-{role}-role: keys=none boundary=ok policy=ok", result.stdout)
        for mode in ("roles-bad-boundary", "roles-bad-policy"):
            result = self.run_script("check", "--stage", "dev", mode=mode, ok=False)
            self.assertIn("-role: keys=none boundary=", result.stdout)
        keyed = [row[row.index("--user-name") + 1] for row in self.calls() if "list-access-keys" in row]
        self.assertFalse(any(name.endswith("-role") for name in keyed))
        before = len(self.calls())
        result = self.run_script("keys", "--stage", "dev", "--apply", "--profile", "fixture-admin", mode="roles")
        self.assertEqual(result.stdout, "")
        self.assertFalse(any(name.endswith("-role") for row in self.calls()[before:] for name in row))
        self.assertNotIn("create-access-key", self.operations())

    def test_bind_check_reads_only_and_refuses_a_shared_entra_user_id(self):
        oid = "00000000-0000-4000-8000-0000000000aa"
        result = self.run_script("bind-check", "--stage", "dev", "--oid", oid, "--sub", "00000000-0000-4000-8000-0000000000b1")
        self.assertIn("bind-check: match", result.stdout)
        result = self.run_script("bind-check", "--stage", "dev", "--oid", oid, "--sub", "00000000-0000-4000-8000-0000000000b2", ok=False)
        self.assertIn("bind-check: no match", result.stdout)
        result = self.run_script("bind-check", "--stage", "dev", "--oid", "00000000-0000-4000-8000-0000000000dd",
                                 "--sub", "00000000-0000-4000-8000-0000000000b1", ok=False)
        self.assertIn("bind-check: no match", result.stdout)
        result = self.run_script("bind-check", "--stage", "dev", "--oid", oid, "--sub", "00000000-0000-4000-8000-0000000000b1",
                                 mode="two-records", ok=False)
        self.assertIn("2 user pool records hold this Entra user ID", result.stderr)
        self.assertNotIn("match", result.stdout)
        for output in (result.stdout + result.stderr,):
            self.assertNotIn("entra_", output)
            self.assertNotIn("0000000000b3", output)
        self.assertEqual(set(self.operations()), {"describe-stacks", "list-users"})
        stacks = {row[row.index("--stack-name") + 1] for row in self.calls() if "describe-stacks" in row}
        self.assertEqual(stacks, {"bootstrap-tenant-ec2-memory-identity-dev"})
        before = len(self.calls())
        for args in (("--oid", oid), ("--sub", oid), ("--oid", "not-a-guid", "--sub", oid), ("--oid", oid.upper(), "--sub", oid)):
            self.run_script("bind-check", "--stage", "dev", *args, ok=False)
        self.run_script("check", "--stage", "dev", "--oid", oid, ok=False)
        self.assertEqual(len(self.calls()), before)

    def test_deploy_uses_the_bound_template_with_an_overlay(self):
        (self.root / ".local/memory").mkdir(parents=True)
        (self.root / ".local/memory/dev.identities.yaml").write_text("identities: {}\n")
        self.run_script("deploy", "--stage", "dev", "--dry-run", ok=False)
        (self.root / ".local/memory/aws-memory-dev.cfn.yaml").write_text("Resources: {}\n")
        result = self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin")
        self.assertIn("Template: .local/memory/aws-memory-dev.cfn.yaml", result.stdout)
        deploy = [row for row in self.calls() if row[0] == "aws" and "deploy" in row][-1]
        self.assertEqual(deploy[deploy.index("--template-file") + 1], ".local/memory/aws-memory-dev.cfn.yaml")

    def test_deploy_refuses_iam_user_false_without_an_overlay(self):
        (self.root / "config/memory/dev.yaml").write_text(
            "people: [{actorId: alice, status: active, iamUser: false}, {actorId: dana, status: offboarded, iamUser: false}]\n")
        result = self.run_script("deploy", "--stage", "dev", "--prepare", "--profile", "fixture-admin", ok=False)
        self.assertIn("alice: iamUser: false needs the overlay .local/memory/dev.identities.yaml", result.stderr)
        self.assertNotIn("dana", result.stderr)
        self.assertEqual(self.operations(), [])

    def test_delete_plan_and_explicit_apply(self):
        result = self.run_script("delete-stack", "--stage", "dev")
        self.assertIn("Retained resources:", result.stdout)
        self.assertIn("Bucket: tenant-ec2-memory-dev", result.stdout)
        self.assertNotIn("delete-stack", self.operations())
        self.run_script("delete-stack", "--stage", "prod", "--apply", "--profile", "fixture-admin", ok=False)
        self.assertNotIn("delete-stack", self.operations())
        self.run_script("delete-stack", "--stage", "dev", "--apply", "--profile", "fixture-admin")
        self.assertEqual(self.operations()[-1], "delete-stack")

    def identity(self, *args, **kwargs):
        kwargs["env"] = {**ENTRA, **kwargs.get("env", {})}
        return self.run_script("identity", "--stage", "dev", *args, **kwargs)

    def params_files(self):
        return [row for row in self.calls() if row[0] == "file"]

    def assert_secret_never_an_argument(self):
        for row in self.calls():
            for value in ENTRA.values():
                self.assertNotIn(value, json.dumps(row))

    def test_identity_preview_calls_no_aws(self):
        for flags in ([], ["--dry-run"]):
            result = self.identity(*flags)
            self.assertIn("create-change-set --stack-name bootstrap-tenant-ec2-memory-identity-dev", result.stdout)
            self.assertIn("credsync name TENANT_EC2_MEMORY_DEV_ENTRA_CLIENT_SECRET: set", result.stdout)
        result = self.run_script("identity", "--stage", "dev")
        self.assertIn("credsync name TENANT_EC2_MEMORY_DEV_ENTRA_TENANT_ID: missing", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_identity_prepare_shows_change_set_and_stops(self):
        result = self.identity("--prepare", "--profile", "fixture-admin", trace=True)
        name = (self.root / "identity-change-set").read_text()
        [row] = self.params_files()
        self.assertEqual(row[2:], ["0o600", "CREATE"])
        self.assertFalse(Path(row[1]).exists())
        self.assertIn("Add IdentityPool AWS::Cognito::IdentityPool -", result.stdout)
        self.assertIn("scripts/aws-memory.sh identity --stage dev --execute " + name + " --profile fixture-admin",
                      result.stdout)
        self.assertNotIn("execute-change-set", self.operations())
        self.assert_secret_never_an_argument()

    def test_identity_prepare_updates_a_complete_stack(self):
        self.identity("--prepare", "--profile", "fixture-admin", mode="status-UPDATE_ROLLBACK_COMPLETE")
        self.assertEqual(self.params_files()[0][3], "UPDATE")

    def test_identity_prepare_needs_the_credsync_names(self):
        for missing in ENTRA:
            with self.subTest(missing=missing):
                result = self.identity("--prepare", "--profile", "fixture-admin", env={missing: ""}, ok=False)
                self.assertIn(missing + ": missing", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_identity_file_is_removed_on_failure(self):
        self.identity("--prepare", "--profile", "fixture-admin", mode="create-fail", ok=False)
        [row] = self.params_files()
        self.assertFalse(Path(row[1]).exists())
        self.assertEqual(self.operations(), ["describe-stacks", "create-change-set"])
        self.assert_secret_never_an_argument()

    def test_identity_file_is_removed_on_a_signal(self):
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=number):
                (self.root / "calls").unlink(missing_ok=True)
                (self.root / "hang").unlink(missing_ok=True)
                process = subprocess.Popen(
                    ["/bin/bash", str(self.script), "identity", "--stage", "dev", "--prepare", "--profile", "fixture-admin"],
                    cwd=self.root, env={**self.env, **ENTRA, "MODE": "create-hang"}, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
                deadline = time.monotonic() + 10
                while not (self.root / "hang").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue((self.root / "hang").exists())
                [row] = self.params_files()
                self.assertTrue(Path(row[1]).exists())
                os.killpg(process.pid, number)
                stdout, stderr = process.communicate(timeout=10)
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(Path(row[1]).exists())
                for value in ENTRA.values():
                    self.assertNotIn(value, stdout + stderr)

    def test_identity_failed_first_create_names_the_delete_step(self):
        for status in ("ROLLBACK_COMPLETE", "ROLLBACK_FAILED", "ROLLBACK_IN_PROGRESS", "CREATE_FAILED"):
            with self.subTest(status=status):
                result = self.identity("--prepare", "--profile", "fixture-admin", mode="status-" + status, ok=False)
                self.assertIn("failed first create", result.stderr)
                self.assertIn("delete-stack --stack-name bootstrap-tenant-ec2-memory-identity-dev", result.stderr)
        for status in ("UPDATE_IN_PROGRESS", "UPDATE_ROLLBACK_FAILED", "DELETE_FAILED"):
            with self.subTest(status=status):
                self.identity("--prepare", "--profile", "fixture-admin", mode="status-" + status, ok=False)
        self.assertEqual(set(self.operations()), {"describe-stacks"})
        self.assertEqual(self.params_files(), [])

    def test_identity_execute_checks_then_executes_the_named_set(self):
        result = self.identity("--execute", "identity-fixture", "--profile", "fixture-admin", trace=True)
        self.assertEqual(self.operations(), ["describe-change-set", "execute-change-set"])
        self.assertIn("execute-change-set --stack-name bootstrap-tenant-ec2-memory-identity-dev"
                      " --change-set-name identity-fixture", result.stdout)
        self.assertEqual(self.params_files(), [])

    def test_identity_execute_refuses_another_or_unready_change_set(self):
        for mode in ("missing-change-set", "bad-change-set", "used-change-set", "other-stack", "other-name",
                     "other-region", "other-stage", "missing-entra", "extra-parameter"):
            with self.subTest(mode=mode):
                self.identity("--execute", "identity-fixture", "--profile", "fixture-admin", mode=mode, ok=False)
        self.assertNotIn("execute-change-set", self.operations())

    def test_identity_guards_before_calls(self):
        for args in (["--apply", "--profile", "fixture-admin"], ["--prepare"], ["--execute", "identity-fixture"],
                     ["--prepare", "--dry-run", "--profile", "fixture-admin"],
                     ["--prepare", "--execute", "identity-fixture", "--profile", "fixture-admin"],
                     ["--execute", "arn:aws:cloudformation:us-east-1:" + "0" * 12 + ":changeSet/x/y",
                      "--profile", "fixture-admin"], ["--write"], ["--user", "x"]):
            with self.subTest(args=args):
                self.identity(*args, ok=False)
        (self.scripts / IDENTITY_TEMPLATE.name).unlink()
        self.identity("--prepare", "--profile", "fixture-admin", ok=False)
        self.assertEqual(self.calls(), [])

    def test_aws_errors_are_redacted(self):
        self.run_script("outputs", "--stage", "dev", mode="aws-fail", ok=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
