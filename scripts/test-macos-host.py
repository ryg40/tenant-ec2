#!/usr/bin/env python3
"""Exercise Mac dispatch with isolated AWS replies; no cloud or Mac runtime is used."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PARAMETERS = ("Project DedicatedHostId AvailabilityZone AmiId VpcCidr RootVolumeSize "
                      "DataVolumeSize SshPublicKey SnapshotRepository SnapshotTag SnapshotCommit "
                      "MemoryStage MemoryArn MemoryBucket AgentActorId ProposalProject DeveloperPin "
                      "PodmanProvider PodmanCpus PodmanMemoryMiB PodmanDiskGiB").split()
ALLOCATION_PARAMETERS = "Project AvailabilityZone InstanceType".split()

# These fixtures implement only the sibling interfaces. They never patch production files.
MEMORY_STUB = '''import json
from pathlib import Path

def load_config(stage, root):
    config = json.loads((root / "config/memory" / (stage + ".json")).read_text())
    people = config["people"]
    if len({p["pin"] for p in people}) != len(people) or len({p["actorId"] for p in people}) != len(people):
        raise ValueError("ambiguous people")
    return config

def active_person(config, actor):
    people = [p for p in config["people"] if p["actorId"] == actor and p["status"] == "active"]
    if len(people) != 1:
        raise ValueError("missing or offboarded person")
    return people[0]

def host_client_config(config, suffix, memory):
    role = next(r for r in config["hostRoles"] if r["roleSuffix"] == suffix)
    return dict(profile="ec2-instance", actorId=role["actorId"])
'''
AWS_STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ["FAKE_DATA"])
data = json.loads(path.read_text())
args = sys.argv[1:]
for flag in ("--profile", "--region", "--output"):
    if flag in args:
        offset = args.index(flag)
        del args[offset:offset+2]
args.remove("--no-cli-pager")
service, op, *rest = args
with open(os.environ["FAKE_CALLS"], "a") as stream:
    stream.write(json.dumps(args) + "\\n")

def option(name):
    return rest[rest.index(name)+1]

def emit(value):
    print(json.dumps(value))

if data.get("fail") == service + " " + op:
    print("credential-private-response", file=sys.stderr)
    sys.exit(97)
if data.get("refuse"):
    sys.exit(98)
if op in ("create-stack", "delete-stack", "start-instances", "stop-instances", "create-change-set", "execute-change-set", "put-secret-value", "send-command"):
    if "Cost/state:" not in Path(os.environ["FAKE_PUBLIC"]).read_text():
        print("cost-order-failure", file=sys.stderr)
        sys.exit(95)
if service == "cloudformation":
    if op == "describe-stacks":
        name = option("--stack-name")
        item = data["stacks"].get(name)
        if item is None:
            print("An error occurred (ValidationError): Stack with id " + name + " does not exist", file=sys.stderr)
            sys.exit(254)
        emit(dict(Stacks=[item]))
    elif op == "list-stack-resources":
        emit(dict(StackResourceSummaries=[dict(ResourceType="AWS::EC2::Host", PhysicalResourceId=data["host"]["HostId"])]))
    elif op == "create-stack":
        data["stacks"]["tenant-ec2-macos-allocation"] = data["allocation"]
        data["inventory"] = [data["host"]]
        emit({})
    elif op == "delete-stack":
        data["stacks"].pop("tenant-ec2-macos-allocation", None)
        data["host"]["State"] = "released"
        emit({})
    elif op in ("create-change-set", "delete-change-set", "execute-change-set", "wait"):
        emit({})
    elif op == "describe-change-set":
        emit(data.get("review", dict(Status="CREATE_COMPLETE", ExecutionStatus="AVAILABLE", Changes=data.get("changes", []))))
    else:
        sys.exit(96)
elif service == "ec2":
    if op == "describe-hosts":
        emit(dict(Hosts=[data["host"]] if "--host-ids" in rest else data["inventory"]))
    elif op == "describe-instances":
        emit(dict(Reservations=[dict(Instances=data["instances"])]))
    elif op in ("stop-instances", "start-instances"):
        data["instances"][0]["State"]["Name"] = "stopping" if op == "stop-instances" else "pending"
        emit({})
    else:
        sys.exit(96)
elif service == "ssm":
    if op == "start-session":
        assert "--profile" not in sys.argv
        assert os.environ.get("AWS_SESSION_TOKEN") == "temporary-fixture-token"
        assert "AWS_PROFILE" not in os.environ
        emit({})
    elif op == "describe-document":
        emit(dict(Document=dict(DocumentType=data.get("documentType", "Command"))))
    elif op == "describe-instance-information":
        emit(dict(InstanceInformationList=[dict(InstanceId=data["instances"][0]["InstanceId"], PingStatus=data.get("ping", "Online"))]))
    elif op == "send-command":
        emit(dict(Command=dict(CommandId="a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12)))
    elif op == "wait":
        emit({})
    elif op == "get-command-invocation":
        if data.get("invocations"):
            emit(data["invocations"].pop(0))
        else:
            emit(dict(Status="Success", StandardOutputContent="credential-private-response", StandardErrorContent="credential-private-response"))
    else:
        sys.exit(96)
elif service == "sts" and op == "assume-role":
    import datetime as dt
    emit(dict(Credentials=dict(AccessKeyId="temporary-fixture-id", SecretAccessKey="temporary-fixture-secret",
                               SessionToken="temporary-" + "fixture-token", Expiration=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15)).isoformat())))
elif service == "secretsmanager":
    if op in ("create-secret", "put-secret-value"):
        data["secret"] = json.loads(Path(option("--secret-string").removeprefix("file://")).read_text())
    if op == "get-secret-value":
        emit(dict(SecretString=data.get("environment", "{}")))
    else:
        emit({})
else:
    sys.exit(96)
path.write_text(json.dumps(data))
'''


class MacHostTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="macos-host-test-"))
        self.repo = self.root / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/aws-host.sh", self.repo / "scripts/aws-host.sh")
        shutil.copy2(ROOT / ".env.example", self.repo / ".env.example")
        # The replay reads its tracked payload and the shared service forms of the bootstrap.
        shutil.copy2(ROOT / "scripts/macos-first-boot-replay.sh", self.repo / "scripts/macos-first-boot-replay.sh")
        (self.repo / "config/macos").mkdir(parents=True)
        for name in ("bootstrap-forms.py", "machine.json"):
            shutil.copy2(ROOT / "config/macos" / name, self.repo / "config/macos" / name)
        (self.repo / "scripts/aws-memory.py").write_text(MEMORY_STUB)
        for name, parameters in (("aws-macos-allocation.cfn.yaml", ALLOCATION_PARAMETERS),
                                 ("aws-host-macos.cfn.yaml", RUNTIME_PARAMETERS)):
            text = "AWSTemplateFormatVersion: '2010-09-09'\nParameters:\n"
            text += "".join("  " + p + ":\n    Type: String\n" for p in parameters)
            resources = "Resources:\n  Instance:\n    Type: AWS::EC2::Instance\n  DataVolume:\n    Type: AWS::EC2::Volume\n"
            (self.repo / "scripts" / name).write_text(text + resources)
        # The validator and generated-key adapter belong to the runtime worker.
        (self.repo / "scripts/host-bootstrap-macos.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.repo / "scripts/generated-keys.py").write_text('''import sys
assert "--variant" in sys.argv and sys.argv[sys.argv.index("--variant")+1] == "macos"
print("EC2_PI_DURABLE_LITELLM_API_KEY: present")
''')
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("sh", "dirname", "basename", "sed", "python3"):
            (self.bin / name).symlink_to(shutil.which(name))
        (self.bin / "zsh").symlink_to(shutil.which("sh"))
        (self.bin / "aws").write_text(AWS_STUB)
        (self.bin / "aws").chmod(0o755)
        for name in ("mktemp", "curl", "docker", "podman", "git"):
            path = self.bin / name
            path.write_text("#!/bin/sh\nprintf 'forbidden\\n' >>\"$FAKE_CALLS\"\nexit 99\n")
            path.chmod(0o755)
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.calls = self.root / "calls"
        self.data = self.root / "aws.json"
        self.env = {"PATH": str(self.bin), "HOME": str(self.root), "TMPDIR": str(self.tmp),
                    "FAKE_DATA": str(self.data), "FAKE_CALLS": str(self.calls), "FAKE_PUBLIC": str(self.root / "public")}
        self.source = self.root / "input.env"
        self.state_path = self.repo / ".local/macos/tenant-ec2-macos-host.json"
        self.stage_path = self.repo / "config/memory/dev.json"
        self.account = "0" * 12
        self.host_id = "h-" + "a" * 17
        self.instance = "i-" + "b" * 17
        self.memory_id = "tenant_ec2_memory_dev-" + "c" * 10
        self.memory_arn = "arn:aws:bedrock-agentcore:us-east-1:" + self.account + ":memory/" + self.memory_id
        self.now = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25)).isoformat()
        self.values = dict(EC2_VARIANT="macos", EC2_AWS_PROFILE="fixture", EC2_AWS_REGION="us-east-1",
                           EC2_MACOS_AVAILABILITY_ZONE="us-east-1a", EC2_MACOS_MEMORY_STAGE="dev", EC2_MACOS_PERSON="alice",
                           EC2_MACOS_DEVELOPER_PIN="XYZ1000", EC2_TZ="Etc/UTC",
                           EC2_MACOS_AGENT="macos-agent", EC2_MACOS_PROPOSAL_PROJECT="example-project",
                           EC2_MACOS_HOST_HOURLY_USD="1.25", EC2_MACOS_PRICE_CURRENCY="USD", EC2_MACOS_PRICE_REGION="us-east-1",
                           EC2_MACOS_PRICE_SOURCE="https://aws.amazon.com/ec2/dedicated-hosts/pricing/",
                           EC2_MACOS_PRICE_CHECKED_AT=self.now, EC2_MACOS_AMI_SSM_PARAMETER="/aws/service/ec2-macos/sequoia/arm64_mac/latest/image_id",
                           EC2_VPC_CIDR="192.0.2.0/24", EC2_SNAPSHOT_REPOSITORY="https://git.example.com/example/runtime.git",
                           EC2_SNAPSHOT_TAG="portable-fixture", EC2_SNAPSHOT_COMMIT="f" * 40,
                           EC2_MACOS_RUNTIME_LOCK="config/macos/runtime.lock.json")
        key = self.root / "owner.pub"
        import base64
        import struct
        blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + b"a" * 32
        key.write_text("ssh-ed25519 " + base64.b64encode(blob).decode() + "\n")
        self.values["EC2_SSH_PUBLIC_KEY_FILE"] = str(key)
        (self.repo / "scripts/verify-deployed.py").write_text("# Contract fixture: --variant --engine --file-set --project\n")
        self.store(self.repo / "config/macos/runtime.lock.json", {})
        self.values.update(EC2_LITELLM_UI_USERNAME="admin", EC2_BEDROCK_LUNA_MODEL="us.openai.gpt-6-luna",
                           EC2_BEDROCK_EMBED_MODEL="amazon.titan-embed-text-v2:0")
        self.stage = dict(region="us-east-1", people=[dict(actorId="alice", pin="XYZ1000", status="active")],
                          hostRoles=[dict(roleSuffix="macos-role", actorId="macos-agent", proposalProject="example-project")])
        self.store(self.stage_path, self.stage)
        self.saved_memory = dict(MemoryId=self.memory_id, MemoryArn=self.memory_arn, BucketName="tenant-ec2-memory-dev")
        self.store(self.repo / ".local/memory/dev.json", self.saved_memory)
        allocation = self.stack("tenant-ec2-macos-allocation", dict(Project="tenant-ec2", AvailabilityZone="us-east-1a", InstanceType="mac2-m2.metal"),
                                dict(HostId=self.host_id, AvailabilityZone="us-east-1a"))
        runtime = self.stack("tenant-ec2-macos-host", dict(DedicatedHostId=self.host_id, AvailabilityZone="us-east-1a", DeveloperPin="XYZ1000"),
                             dict(HostId=self.host_id, InstanceId=self.instance, SessionDocumentName="tenant-ec2-macos-session",
                                  DeveloperAccount="xyz1000"))
        boundary = self.stack("bootstrap-tenant-ec2", dict(EnableMacos="true", MacosMemoryArn=self.memory_arn,
                              MacosMemoryBucketName="tenant-ec2-memory-dev", MacosAgentActorId="macos-agent", MacosProposalProject="example-project"),
                              dict(MacosSessionRoleArn="arn:aws:iam::" + self.account + ":role/tenant-ec2-macos-session"))
        memory = self.stack("bootstrap-tenant-ec2-memory-dev", {}, self.saved_memory)
        host = dict(HostId=self.host_id, State="available", AvailabilityZone="us-east-1a", AllocationTime=self.now,
                    HostProperties=dict(InstanceType="mac2-m2.metal"), Instances=[dict(InstanceId=self.instance)],
                    Tags=self.tags(Project="tenant-ec2", Variant="macos", Purpose="macos-allocation", **{"aws:cloudformation:stack-id": allocation["StackId"]}))
        instance = dict(InstanceId=self.instance, State=dict(Name="stopped"),
                        Placement=dict(HostId=self.host_id, AvailabilityZone="us-east-1a", Tenancy="host"),
                        Tags=self.tags(**{"aws:cloudformation:stack-name": "tenant-ec2-macos-host"}))
        self.fixture = dict(stacks={s["StackName"]: s for s in (allocation, runtime, memory, boundary)},
                            allocation=allocation, host=host, inventory=[host], instances=[instance])
        self.store(self.data, self.fixture)
        self.state = dict(schemaVersion=1, project="tenant-ec2", region="us-east-1", stack="tenant-ec2-macos-host",
                          allocationStack="tenant-ec2-macos-allocation", allocationStackId=allocation["StackId"],
                          hostId=self.host_id, availabilityZone="us-east-1a", instanceType="mac2-m2.metal", allocatedAt=self.now,
                          instanceId=self.instance, status="allocated", stage="dev", personActor="alice", personPin="XYZ1000",
                          localAccount="xyz1000", releaseHostId=self.host_id,
                          ami=dict(imageId="ami-" + "d" * 17, parameter=self.values["EC2_MACOS_AMI_SSM_PARAMETER"], region="us-east-1",
                                   architecture="arm64_mac", rootVolumeMinGiB=100, reviewedAt=self.now))
        self.store(self.state_path, self.state)

    def tags(self, **values):
        return [dict(Key=k, Value=v) for k, v in values.items()]

    def stack(self, name, parameters, outputs):
        return dict(StackName=name, StackId="arn:aws:cloudformation:us-east-1:" + self.account + ":stack/" + name + "/fixture-id",
                    StackStatus="CREATE_COMPLETE", Parameters=[dict(ParameterKey=k, ParameterValue=v) for k, v in parameters.items()],
                    Outputs=[dict(OutputKey=k, OutputValue=v) for k, v in outputs.items()], Tags=self.tags(Project="tenant-ec2", Variant="macos"))

    def store(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def command(self, command, *flags, values=None, ok=True, env=None):
        values = self.values if values is None else values
        self.source.write_text("".join(k + "=" + v + "\n" for k, v in values.items()))
        args = ["sh", str(self.repo / "scripts/aws-host.sh"), command, *flags]
        if command != "secret":
            args += ["--env-file", str(self.source)]
        with (self.root / "public").open("w") as public_file:
            result = subprocess.run(args, input=self.source.read_text() if command == "secret" else "",
                                    env=env or self.env, text=True, stdout=public_file, stderr=subprocess.PIPE, timeout=20)
        result.stdout = (self.root / "public").read_text()
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        public = result.stdout + result.stderr
        for private in (self.account, self.host_id, self.instance, self.memory_id, "credential-private-response", "fixture-credential-7q9z", "temporary-fixture-token", "temporary-fixture-secret"):
            self.assertNotIn(private, public)
        self.assertEqual(list(self.tmp.iterdir()), [])
        return result

    def recorded(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def no_writes(self):
        writes = {"create-stack", "delete-stack", "start-instances", "stop-instances", "create-change-set", "execute-change-set",
                  "put-secret-value", "create-secret", "send-command"}
        self.assertFalse(any(row[1] in writes for row in self.recorded()))

    def test_empty_dry_runs_and_default_preview_make_no_calls_or_files(self):
        self.fixture["refuse"] = True
        self.store(self.data, self.fixture)
        before = sorted(str(p) for p in self.repo.rglob("*"))
        for command in ("host-allocate", "host-release", "stop", "start", "deploy", "outputs", "generated-status", "verify", "session", "tunnel", "ssh",
                        "host-bootstrap", "recovery-check"):
            result = self.command(command, "--dry-run", values={}, env=dict(self.env, EC2_VARIANT="macos"))
            self.assertIn("dry run: macos " + command, result.stdout)
        for command in ("host-allocate", "host-release", "stop", "start", "host-bootstrap"):
            self.command(command, values={"EC2_VARIANT": "macos"})
        self.assertEqual(self.recorded(), [])
        self.assertEqual(before, sorted(str(p) for p in self.repo.rglob("*")))

    def test_unknown_variant_and_dns_forms_refuse_before_calls(self):
        self.command("outputs", "--dry-run", values={"EC2_VARIANT": "wrong"}, ok=False)
        for flags in ((), ("--plan",), ("--dry-run",), ("--plan", "--dry-run")):
            self.command("dns", *flags, ok=False)
        self.command("start", "--apply", "--dry-run", ok=False)
        self.command("start", "--dry-run", values={"EC2_VARIANT": "homelab"}, ok=False)
        result = self.command("host-bootstrap", "--dry-run", values={"EC2_VARIANT": "homelab"}, ok=False)
        self.assertIn("needs EC2_VARIANT=macos", result.stderr)
        self.assertEqual(self.recorded(), [])

    def test_failed_allocation_records_owned_host_for_explicit_release(self):
        self.fixture["stacks"].pop("tenant-ec2-macos-allocation")
        self.fixture["stacks"].pop("tenant-ec2-macos-host")
        self.fixture["inventory"] = []
        self.fixture["host"]["Instances"] = []
        self.fixture["instances"] = []
        self.fixture["allocation"]["StackStatus"] = "CREATE_FAILED"
        self.fixture["allocation"]["Outputs"] = []
        self.fixture["fail"] = "cloudformation wait"
        self.store(self.data, self.fixture)
        self.state_path.unlink()
        self.command("host-allocate", "--apply", ok=False)
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["hostId"], self.host_id)
        self.assertEqual(state["status"], "allocation-incomplete")
        fixture = json.loads(self.data.read_text())
        fixture.pop("fail")
        self.store(self.data, fixture)
        state["releaseHostId"] = self.host_id
        self.store(self.state_path, state)
        self.command("host-release", "--apply")
        self.assertEqual(json.loads(self.state_path.read_text())["status"], "released")

    def test_allocation_retry_does_not_create_another_host(self):
        result = self.command("host-allocate", "--apply")
        self.assertIn("no second host", result.stdout)
        self.assertFalse(any(row[1] == "create-stack" for row in self.recorded()))
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["price"]["currency"], "USD")

    def test_allocation_create_uses_cloudformation_and_cost_precedes_write(self):
        self.fixture["stacks"].pop("tenant-ec2-macos-allocation")
        self.fixture["inventory"] = []
        self.fixture["stacks"].pop("tenant-ec2-macos-host")
        self.fixture["host"]["Instances"] = []
        self.fixture["instances"] = []
        self.store(self.data, self.fixture)
        self.state_path.unlink()
        result = self.command("host-allocate", "--apply")
        self.assertIn("minimum 30.000 USD", result.stdout)
        created = [r for r in self.recorded() if r[1] == "create-stack"]
        self.assertEqual(len(created), 1)
        self.assertIn("tenant-ec2-macos-allocation", created[0])
        self.assertFalse(any(r[1] in ("allocate-hosts", "release-hosts") for r in self.recorded()))
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)

    def test_duplicate_allocation_and_wrong_template_refuse(self):
        self.fixture["inventory"].append(copy.deepcopy(self.fixture["host"]))
        self.store(self.data, self.fixture)
        self.command("host-allocate", "--apply", ok=False)
        self.no_writes()
        template = self.repo / "scripts/aws-macos-allocation.cfn.yaml"
        template.write_text(template.read_text().replace("  InstanceType:", "  WrongParameter:"))
        self.command("host-allocate", "--apply", ok=False)
        self.no_writes()

    def test_missing_offboarded_ambiguous_and_changed_people_refuse_before_calls(self):
        original = copy.deepcopy(self.stage)
        for people in ([], [dict(actorId="alice", pin="XYZ1000", status="offboarded")], original["people"] * 2,
                       [dict(actorId="alice", pin="XYZ9999", status="active")]):
            self.stage["people"] = people
            self.store(self.stage_path, self.stage)
            self.command("host-allocate", "--apply", ok=False)
            self.assertEqual(self.recorded(), [])

    def test_stage_region_agent_private_outputs_and_boundary_mismatch(self):
        for name, value in (("EC2_AWS_REGION", "us-west-2"), ("EC2_MACOS_AGENT", "wrong"),
                            ("EC2_MACOS_MEMORY_ID", "wrong"), ("EC2_MACOS_PROPOSAL_PROJECT", "wrong")):
            self.command("host-allocate", "--apply", values=dict(self.values, **{name: value}), ok=False)
            self.assertEqual(self.recorded(), [])
        self.fixture["stacks"]["bootstrap-tenant-ec2"]["Parameters"][0]["ParameterValue"] = "false"
        self.store(self.data, self.fixture)
        self.command("host-allocate", "--apply", ok=False)
        self.no_writes()

    def test_release_young_pending_unknown_time_wrong_owner_and_confirmation(self):
        original = copy.deepcopy(self.fixture)
        for field, value in (("AllocationTime", dt.datetime.now(dt.timezone.utc).isoformat()),
                             ("AllocationTime", None), ("State", "pending"), ("State", "under-assessment"),
                             ("AvailabilityZone", "us-east-1b"), ("Tags", [])):
            self.fixture = copy.deepcopy(original)
            self.fixture["host"][field] = value
            self.store(self.data, self.fixture)
            self.command("host-release", "--apply", ok=False)
            self.no_writes()
        self.store(self.data, original)
        self.state.pop("releaseHostId")
        self.store(self.state_path, self.state)
        self.command("host-release", "--apply", ok=False)
        self.no_writes()

    def test_release_stopped_host_deletes_only_allocation_and_retry_is_safe(self):
        self.command("host-release", "--apply")
        delete = [r for r in self.recorded() if r[1] == "delete-stack"]
        self.assertEqual(len(delete), 1)
        self.assertIn(self.state["allocationStackId"], delete[0])
        self.assertEqual(json.loads(self.state_path.read_text())["status"], "released")
        self.command("host-release", "--apply")
        self.assertEqual(len([r for r in self.recorded() if r[1] == "delete-stack"]), 1)
        self.command("start", "--apply", ok=False)
        self.assertFalse(any(r[1] == "start-instances" for r in self.recorded()))

    def test_release_running_or_foreign_attachment_refuses(self):
        self.fixture["instances"][0]["State"]["Name"] = "running"
        self.store(self.data, self.fixture)
        self.command("host-release", "--apply", ok=False)
        self.no_writes()
        self.fixture["instances"][0]["InstanceId"] = "i-" + "e" * 17
        self.store(self.data, self.fixture)
        self.command("host-release", "--apply", ok=False)
        self.no_writes()

    def test_failed_release_retains_private_state_and_redacts_error(self):
        self.fixture["fail"] = "cloudformation delete-stack"
        self.store(self.data, self.fixture)
        result = self.command("host-release", "--apply", ok=False)
        self.assertIn("charges may remain", result.stderr)
        self.assertEqual(json.loads(self.state_path.read_text())["status"], "allocated")

    def test_start_stop_exact_instance_idempotency_and_pending_scrub(self):
        self.command("start", "--apply")
        self.command("start", "--apply")
        writes = [r for r in self.recorded() if r[1] == "start-instances"]
        self.assertEqual(writes, [["ec2", "start-instances", "--instance-ids", self.instance]])
        self.fixture["instances"][0]["State"]["Name"] = "running"
        self.store(self.data, self.fixture)
        self.command("stop", "--apply")
        self.command("stop", "--apply")
        self.assertEqual(len([r for r in self.recorded() if r[1] == "stop-instances"]), 1)
        self.fixture["host"]["State"] = "pending"
        self.fixture["instances"][0]["State"]["Name"] = "stopped"
        self.store(self.data, self.fixture)
        self.command("stop", "--apply")
        self.command("start", "--apply", ok=False)

    def test_missing_state_and_wrong_runtime_host_refuse(self):
        self.state_path.unlink()
        self.command("start", "--apply", ok=False)
        self.assertEqual(self.recorded(), [])
        self.store(self.state_path, self.state)
        self.fixture["stacks"]["tenant-ec2-macos-host"]["Outputs"][0]["OutputValue"] = "h-" + "c" * 17
        self.store(self.data, self.fixture)
        self.command("start", "--apply", ok=False)
        self.no_writes()

    def test_runtime_parameters_follow_machine_contract_and_private_inputs(self):
        for overrides, expected in (({}, ("4", "8192", "80")),
                                    ({"EC2_MACOS_PODMAN_CPUS": "6", "EC2_MACOS_PODMAN_MEMORY_MIB": "12288",
                                      "EC2_MACOS_PODMAN_DISK_GIB": "120", "EC2_MACOS_DATA_VOLUME_SIZE": "200"},
                                     ("6", "12288", "120"))):
            self.command("deploy", "--apply", values=dict(self.values, **overrides))
            request = [r for r in self.recorded() if r[1] == "create-change-set"][-1]
            rows = json.loads(request[request.index("--parameters")+1])
            values = {r["ParameterKey"]: r["ParameterValue"] for r in rows}
            self.assertEqual(set(values), set(RUNTIME_PARAMETERS))
            self.assertEqual(len(rows), len(values))
            self.assertNotIn("PersonPin", values)
            self.assertNotIn("MemoryBucketName", values)
            self.assertEqual(values["DeveloperPin"], "XYZ1000")
            self.assertEqual(values["SshPublicKey"], Path(self.values["EC2_SSH_PUBLIC_KEY_FILE"]).read_text().strip())
            self.assertEqual(values["MemoryArn"], self.saved_memory["MemoryArn"])
            self.assertEqual(values["MemoryBucket"], self.saved_memory["BucketName"])
            self.assertEqual(values["PodmanProvider"], "applehv")
            self.assertEqual(tuple(values[k] for k in ("PodmanCpus", "PodmanMemoryMiB", "PodmanDiskGiB")), expected)
            self.assertEqual(values["DataVolumeSize"], overrides.get("EC2_MACOS_DATA_VOLUME_SIZE", "150"))

    def test_runtime_old_missing_and_extra_parameters_fail_closed(self):
        path = self.repo / "scripts/aws-host-macos.cfn.yaml"
        text = path.read_text()
        for changed in (text.replace("  DeveloperPin:", "  PersonPin:"),
                        text.replace("  MemoryBucket:", "  MemoryBucketName:"),
                        text.replace("  PodmanCpus:\n    Type: String\n", ""),
                        text.replace("Resources:", "  Unexpected:\n    Type: String\nResources:")):
            path.write_text(changed)
            result = self.command("deploy", "--apply", ok=False)
            self.assertIn("template parameter list mismatch", result.stderr)
            self.no_writes()

    def test_exact_names_reject_wrong_provider_pin_and_legacy_stage(self):
        for name, value in (("EC2_MACOS_PODMAN_PROVIDER", "libkrun"), ("EC2_MACOS_PODMAN_PROVIDER", "APPLEHV"),
                            ("EC2_MACOS_DEVELOPER_PIN", ""), ("EC2_MACOS_DEVELOPER_PIN", "XYZ1001"),
                            ("EC2_MACOS_DEVELOPER_PIN", "xyz1000"), ("EC2_MACOS_MEMORY_STAGE", "")):
            self.command("deploy", "--apply", values=dict(self.values, **{name: value}), ok=False)
            self.assertEqual(self.recorded(), [])
        legacy = dict(self.values, EC2_MEMORY_STAGE="dev")
        del legacy["EC2_MACOS_MEMORY_STAGE"]
        self.command("deploy", "--apply", values=legacy, ok=False)
        self.assertEqual(self.recorded(), [])

    def test_explicit_provider_and_stage_supply_deployment_parameters(self):
        values = dict(self.values, EC2_MACOS_PODMAN_PROVIDER="applehv", EC2_MACOS_MEMORY_STAGE="dev")
        self.command("deploy", "--apply", values=values)
        request = [r for r in self.recorded() if r[1] == "create-change-set"][-1]
        parameters = {r["ParameterKey"]: r["ParameterValue"] for r in json.loads(request[request.index("--parameters")+1])}
        self.assertEqual(parameters["PodmanProvider"], "applehv")
        self.assertEqual(parameters["MemoryStage"], "dev")
        self.assertEqual(parameters["DeveloperPin"], values["EC2_MACOS_DEVELOPER_PIN"])
        self.assertEqual(parameters["MemoryBucket"], self.saved_memory["BucketName"])
        self.assertEqual(parameters["MemoryArn"], self.saved_memory["MemoryArn"])
        path = self.repo / "scripts/aws-host-macos.cfn.yaml"
        path.write_text(path.read_text().replace("  PodmanProvider:\n    Type: String\n", ""))
        result = self.command("deploy", "--apply", values=values, ok=False)
        self.assertIn("template parameter list mismatch", result.stderr)
        self.assertEqual(len([r for r in self.recorded() if r[1] == "create-change-set"]), 1)

    def test_podman_invalid_sizes_refuse_before_aws(self):
        for name, value in (("EC2_MACOS_PODMAN_CPUS", "0"), ("EC2_MACOS_PODMAN_CPUS", "9"),
                            ("EC2_MACOS_PODMAN_MEMORY_MIB", "1023"), ("EC2_MACOS_PODMAN_MEMORY_MIB", "16385"),
                            ("EC2_MACOS_PODMAN_DISK_GIB", "19"), ("EC2_MACOS_PODMAN_DISK_GIB", "1025"),
                            ("EC2_MACOS_PODMAN_CPUS", "$(forbidden)")):
            result = self.command("deploy", "--apply", values=dict(self.values, **{name: value}), ok=False)
            self.assertIn(name, result.stderr)
            self.assertEqual(self.recorded(), [])

    def test_verify_wrapper_reads_machine_account_before_sudo(self):
        import contextlib
        import io
        import shlex
        import stat
        from types import SimpleNamespace
        from unittest import mock
        self.command("verify")
        sent = [r for r in self.recorded() if r[1] == "send-command"][-1]
        params = json.loads(sent[-1])
        args = shlex.split(params["commands"][-1])
        program = args[3]
        machine_path = self.root / "machine.json"
        machine = dict(schemaVersion=1, project="tenant-ec2", variant="macos", engine="podman",
                       fileSet=["compose.macos.yaml"], account=dict(pin="XYZ1000", name="xyz1000",
                       home="/Users/xyz1000", shell="/bin/zsh"))
        real_open = os.open
        def local_open(path, flags):
            self.assertEqual(path, "/etc/tenant-ec2/machine.json")
            self.assertTrue(flags & os.O_NOFOLLOW)
            return real_open(machine_path, flags)
        class ExecReached(BaseException):
            pass
        def run(text, ok, uid=0, mode=0o644):
            machine_path.write_text(text)
            stderr, stdout = io.StringIO(), io.StringIO()
            with mock.patch("sys.argv", ["-c", *args[4:]]), mock.patch("os.open", side_effect=local_open), \
                    mock.patch("os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=uid)), \
                    mock.patch("os.execvp", side_effect=ExecReached) as execute, \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(ExecReached if ok else SystemExit):
                    exec(compile(program, "<remote-verify>", "exec"), {"__name__": "__main__"})
            self.assertEqual(stdout.getvalue(), "")
            if ok:
                command = execute.call_args.args
                self.assertEqual(command[0], "sudo")
                self.assertEqual(command[1], ["sudo", "-u", "xyz1000", "-H", "python3", "-I",
                                             "/opt/tenant-ec2/macos/snapshot/scripts/verify-deployed.py",
                                             "--repo", "/opt/tenant-ec2/macos/snapshot", "--project", "tenant-ec2",
                                             "--variant", "macos", "--engine", "podman", "--file-set", "compose.macos.yaml",
                                             "--edge-names", ""])
            else:
                execute.assert_not_called()
                self.assertEqual(stderr.getvalue(), "macos verify: machine configuration refused\n")
        run(json.dumps(machine), True)
        for key, value in (("name", "root"), ("name", "xyz1001"), ("pin", "XYZ1001"),
                           ("home", "/Users/wrong"), ("shell", "/bin/bash")):
            wrong = copy.deepcopy(machine)
            wrong["account"][key] = value
            run(json.dumps(wrong), False)
        for key, value in (("project", "wrong"), ("engine", "docker"), ("fileSet", ["compose.yaml"])):
            run(json.dumps(dict(machine, **{key: value})), False)
        run(json.dumps(machine), False, uid=501)
        run(json.dumps(machine), False, mode=0o664)
        run(json.dumps(machine)[:-1] + ', "account": {}}', False)
        run("not-json-private-fixture", False)

    def test_conditional_replacement_and_removal_are_independent(self):
        self.fixture["changes"] = [dict(ResourceChange=dict(Action="Modify", LogicalResourceId=logical,
                                  ResourceType=kind, Replacement="Conditional")) for logical, kind in
                                  (("Instance", "AWS::EC2::Instance"), ("DataVolume", "AWS::EC2::Volume"))]
        self.store(self.data, self.fixture)
        for flags in ((), ("--replace-instance",), ("--allow-remove",)):
            self.command("deploy", "--apply", *flags, ok=False)
        self.assertFalse(any(r[1] == "execute-change-set" for r in self.recorded()))
        self.command("deploy", "--apply", "--replace-instance", "--allow-remove")
        self.assertEqual(len([r for r in self.recorded() if r[1] == "execute-change-set"]), 1)

    def test_host_resource_change_always_refused_and_ami_never_resolved(self):
        self.fixture["changes"] = [dict(ResourceChange=dict(Action="Modify", LogicalResourceId="Host", ResourceType="AWS::EC2::Host", Replacement="Conditional"))]
        self.store(self.data, self.fixture)
        self.command("deploy", "--apply", "--replace-instance", "--allow-remove", ok=False)
        self.assertFalse(any(r[1] in ("execute-change-set", "get-parameter", "describe-images") for r in self.recorded()))

    def test_outputs_and_verify_hide_private_response_and_select_pin_account(self):
        self.command("outputs", "--write")
        self.command("verify")
        sent = [r for r in self.recorded() if r[1] == "send-command"]
        self.assertEqual(len(sent), 1)
        self.assertIn("AWS-RunShellScript", sent[0])
        self.assertIn(self.instance, sent[0])
        arguments = json.loads(sent[0][-1])
        self.assertEqual(arguments["commands"][:3], ["set +x", "set -eu", "cd /opt/tenant-ec2/macos/snapshot"])
        import shlex
        wrapper = shlex.split(arguments["commands"][-1])
        self.assertEqual(wrapper[:3], ["python3", "-I", "-c"])
        self.assertEqual(wrapper[4:], ["tenant-ec2", "xyz1000"])
        self.assertIn("machine[\"account\"]", wrapper[3])
        self.assertIn("/machine.json", wrapper[3])
        self.assertIn("os.execvp(\"sudo\"", wrapper[3])
        self.assertIn("--engine", wrapper[3])
        dry = self.command("verify", "--dry-run")
        self.assertIn("--repo /opt/<project>/macos/snapshot --project <project>", dry.stdout)
        (self.repo / "scripts/verify-deployed.py").write_text("# no Mac adapter yet\n")
        result = self.command("verify", ok=False)
        self.assertIn("verify-deployed.py has no macos variant yet", result.stderr)
        self.assertEqual(len([r for r in self.recorded() if r[1] == "send-command"]), 1)
        for missing in ("--variant", "--engine", "--file-set", "--project"):
            flags = [flag for flag in ("--variant", "--engine", "--file-set", "--project") if flag != missing]
            (self.repo / "scripts/verify-deployed.py").write_text("# " + " ".join(flags) + "\n")
            self.command("verify", ok=False)
            self.assertEqual(len([r for r in self.recorded() if r[1] == "send-command"]), 1)
        self.command("generated-status")

    def test_interactive_paths_assume_scoped_role_and_use_three_documents(self):
        self.fixture["instances"][0]["State"]["Name"] = "running"
        self.store(self.data, self.fixture)
        for command in ("session", "tunnel", "ssh"):
            self.command(command, "--apply", env=dict(self.env, AWS_PROFILE="wrong-profile"))
        sessions = [r for r in self.recorded() if r[1] == "start-session"]
        self.assertEqual(len(sessions), 3)
        self.assertEqual([r[r.index("--document-name")+1] for r in sessions],
                         ["tenant-ec2-macos-session", "AWS-StartPortForwardingSession", "AWS-StartSSHSession"])
        self.assertTrue(all(self.instance in r for r in sessions))
        self.assertEqual(len([r for r in self.recorded() if r[1] == "assume-role"]), 3)
        # As an SSH ProxyCommand, `ssh` keeps stdout for the SSH stream; its cost lines go to stderr.
        result = self.command("ssh", "--apply")
        self.assertNotIn("Cost/state:", result.stdout)
        self.assertIn("Cost/state:", result.stderr)
        self.assertIn("Cost/state:", self.command("session", "--apply").stdout)

    def test_empty_changeset_is_idempotent(self):
        self.fixture["review"] = dict(Status="FAILED", StatusReason="No updates are to be performed")
        self.store(self.data, self.fixture)
        self.command("deploy", "--apply")
        self.assertFalse(any(r[1] == "execute-change-set" for r in self.recorded()))
        self.assertEqual(len([r for r in self.recorded() if r[1] == "delete-change-set"]), 1)

    def test_failed_deletion_retry_and_missing_price_region(self):
        self.fixture["stacks"]["tenant-ec2-macos-allocation"]["StackStatus"] = "DELETE_FAILED"
        self.store(self.data, self.fixture)
        self.command("host-release", "--apply")
        self.assertEqual(len([r for r in self.recorded() if r[1] == "delete-stack"]), 1)
        self.command("host-allocate", "--apply", values=dict(self.values, EC2_MACOS_PRICE_REGION=""), ok=False)

    def test_symlink_and_duplicate_private_state_refuse_before_calls(self):
        original = self.state_path.read_text()
        self.state_path.write_text(original[:-1] + ', "hostId": "another"}')
        self.command("start", "--apply", ok=False)
        self.state_path.unlink()
        other = self.root / "state.json"
        other.write_text(original)
        other.chmod(0o600)
        self.state_path.symlink_to(other)
        self.command("start", "--apply", ok=False)
        self.assertEqual(self.recorded(), [])

    def test_deployed_person_change_refuses_without_local_selection_record(self):
        self.state.pop("personActor")
        self.store(self.state_path, self.state)
        self.fixture["stacks"]["tenant-ec2-macos-host"]["Parameters"][2]["ParameterValue"] = "XYZ9999"
        self.store(self.data, self.fixture)
        self.command("host-allocate", "--apply", ok=False)
        self.no_writes()

    def test_secret_subset_and_credential_refusals(self):
        values = dict(self.values, EC2_MACOS_PODMAN_PROVIDER="applehv", EC2_MACOS_PODMAN_CPUS="4", EC2_MACOS_PODMAN_MEMORY_MIB="8192",
                      EC2_MACOS_PODMAN_DISK_GIB="80", EC2_MACOS_DATA_VOLUME_SIZE="150")
        for name in ("EC2_LITELLM_MASTER_KEY", "EC2_LITELLM_SALT_KEY", "EC2_LITELLM_POSTGRES_PASSWORD", "EC2_LITELLM_UI_PASSWORD",
                     "EC2_PI_DURABLE_API_KEY", "EC2_PI_DURABLE_LITELLM_API_KEY", "EC2_SNAPSHOT_CLONE_TOKEN",
                     "EC2_OK_ENTRA_TENANT_ID", "EC2_OK_ENTRA_GROUP_ID", "EC2_OK_OIDC_CLIENT_ID", "EC2_OK_OIDC_CLIENT_SECRET", "EC2_OK_COOKIE_SECRET"):
            values[name] = "fixture-credential-7q9z"
        for name in ("EC2_OK_ENTRA_TENANT_ID", "EC2_OK_ENTRA_GROUP_ID", "EC2_OK_OIDC_CLIENT_ID"):
            values[name] = "abcdefab-1234-1234-1234-abcdefabcdef"
        self.command("secret", "--dry-run", values=values)
        self.assertEqual(self.recorded(), [])
        for name, value in (("EC2_MACOS_DEVELOPER_PIN", "XYZ9999"), ("EC2_MACOS_PODMAN_PROVIDER", "libkrun"),
                            ("EC2_MACOS_MEMORY_STAGE", "invalid")):
            self.command("secret", "--apply", values=dict(values, **{name: value}), ok=False)
            self.assertEqual(self.recorded(), [])
        for name in ("AWS_ACCESS_KEY_ID", "EC2_PERSONAL_SECRET_ACCESS_KEY", "EC2_SSH_PRIVATE_KEY"):
            self.command("secret", "--apply", values=dict(values, **{name: "fixture-credential-7q9z"}), ok=False)
            self.assertEqual(self.recorded(), [])
        values["EC2_PORKBUN_API_KEY"] = "fixture-credential-7q9z"
        self.command("secret", "--apply", values=values)
        body = json.loads(self.data.read_text())["secret"]
        self.assertNotIn("COMPOSE_FILE", values)
        self.assertEqual(body["COMPOSE_FILE"], "compose.macos.yaml")
        self.assertEqual(body["EC2_VARIANT"], "macos")
        self.assertNotIn("EC2_PORKBUN_API_KEY", body)
        self.assertNotIn("EC2_MACOS_HOST_HOURLY_USD", body)
        self.assertNotIn("EC2_SSH_PUBLIC_KEY_FILE", body)
        self.assertFalse(any(n.startswith("EC2_MACOS_") for n in body))
        self.assertNotIn("EC2_MEMORY_STAGE", body)
        self.assertTrue(all("OPENVIKING" not in n for n in body))
        target = [r for r in self.recorded() if r[1] == "put-secret-value"][0]
        self.assertIn("tenant-ec2/macos/env", target)

    def replay_fixture(self, environment=None, **fixture):
        """A running instance and a filled Mac environment secret with private fixture values."""
        import base64
        if environment is None:
            environment = dict(COMPOSE_FILE="compose.macos.yaml", EC2_VARIANT="macos", EC2_AWS_REGION="us-east-1", EC2_TZ="Etc/UTC",
                               EC2_LITELLM_UI_USERNAME="admin", EC2_BEDROCK_LUNA_MODEL="us.openai.gpt-6-luna",
                               EC2_BEDROCK_EMBED_MODEL="amazon.titan-embed-text-v2:0",
                               EC2_OK_COOKIE_SECRET=base64.b64encode(b"c" * 32).decode())
            for name in ("EC2_LITELLM_MASTER_KEY", "EC2_LITELLM_SALT_KEY", "EC2_LITELLM_POSTGRES_PASSWORD", "EC2_LITELLM_UI_PASSWORD",
                         "EC2_OK_OIDC_CLIENT_SECRET", "EC2_SNAPSHOT_CLONE_TOKEN"):
                environment[name] = "fixture-credential-7q9z"
            for name in ("EC2_OK_ENTRA_TENANT_ID", "EC2_OK_ENTRA_GROUP_ID", "EC2_OK_OIDC_CLIENT_ID"):
                environment[name] = "abcdefab-1234-1234-1234-abcdefabcdef"
        self.fixture["instances"][0]["State"]["Name"] = "running"
        self.fixture["environment"] = environment if isinstance(environment, str) else json.dumps(environment)
        self.fixture.update(fixture)
        self.store(self.data, self.fixture)
        return environment

    def test_host_bootstrap_sends_fixed_payload_to_stack_instance_only(self):
        self.replay_fixture(invocations=[
            dict(Status="InProgress"),
            dict(Status="Success", StandardErrorContent="",
                 StandardOutputContent="macos first boot: start\nmacos first boot: environment read\n"
                                       "fixture-credential-7q9z\nmacos bootstrap: durable mount pass\n"
                                       "macos bootstrap: fixture-credential-7q9z leaked?\nmacos first boot: complete\n")])
        result = self.command("host-bootstrap", "--apply")
        rows = self.recorded()
        operations = [r[1] for r in rows]
        sent = [r for r in rows if r[1] == "send-command"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][sent[0].index("--instance-ids") + 1], self.instance)
        self.assertEqual(sent[0][sent[0].index("--document-name") + 1], "AWS-RunShellScript")
        parameters = json.loads(sent[0][sent[0].index("--parameters") + 1])
        payload = (ROOT / "scripts/macos-first-boot-replay.sh").read_text().splitlines()
        self.assertEqual(parameters["commands"], ["MACOS_PROJECT=tenant-ec2", "MACOS_PHASE=first-boot"] + payload)
        self.assertEqual(sent[0][sent[0].index("--comment") + 1], "tenant-ec2 macos first-boot replay")
        self.assertEqual(parameters["executionTimeout"], ["10800"])
        self.assertNotIn("fixture-credential-7q9z", json.dumps(sent))
        # Scope, placement, SSM registration and the secret form come before the one write.
        for before in ("describe-stacks", "describe-hosts", "describe-instances", "describe-instance-information", "get-secret-value"):
            self.assertLess(operations.index(before), operations.index("send-command"), before)
        self.assertIn(["cloudformation", "describe-stacks", "--stack-name", "bootstrap-tenant-ec2"], rows)
        self.assertIn(["cloudformation", "describe-stacks", "--stack-name", "bootstrap-tenant-ec2-memory-dev"], rows)
        self.assertEqual(operations.count("get-command-invocation"), 2)
        self.assertLess(result.stdout.index("Cost/state:"), result.stdout.index("replay sent"))
        for line in ("macos first boot: start", "macos first boot: environment read", "macos bootstrap: durable mount pass",
                     "macos first boot: complete", "macos host-bootstrap: 2 other remote lines withheld",
                     "macos host-bootstrap: pass; the first-boot stage is complete"):
            self.assertIn(line + "\n", result.stdout)

    def test_host_bootstrap_refuses_empty_or_invalid_environment_before_send(self):
        valid = self.replay_fixture()
        broken = dict(valid, EC2_OK_COOKIE_SECRET="not-base64-fixture-credential-7q9z")
        cases = (("{}", "environment secret is empty"), ("", "environment secret is empty"),
                 ("{not json fixture-credential-7q9z", "environment secret refused: service-syntax"),
                 (dict(valid, COMPOSE_FILE="compose.yaml"), "environment secret refused: service-selector"),
                 ({k: v for k, v in valid.items() if k != "EC2_LITELLM_MASTER_KEY"}, "environment secret refused: service-required"),
                 (dict(valid, **{"EC2_OPENVIKING_API_KEY": "fixture-credential-7q9z"}), "environment secret refused: service-fields"),
                 (dict(valid, **{"EC2_PI_DURABLE_API_KEY": "fixture-credential-7q9z"}), "environment secret refused: service-fields"),
                 (broken, "values withheld"))
        for environment, message in cases:
            with self.subTest(message=message):
                self.replay_fixture(environment)
                result = self.command("host-bootstrap", "--apply", ok=False)
                self.assertIn(message, result.stderr)
                self.assertFalse(any(r[1] == "send-command" for r in self.recorded()))

    def test_host_bootstrap_refuses_wrong_state_and_reports_remote_stop(self):
        self.replay_fixture()
        self.fixture["instances"][0]["State"]["Name"] = "stopped"
        self.store(self.data, self.fixture)
        result = self.command("host-bootstrap", "--apply", ok=False)
        self.assertIn("needs the running runtime instance", result.stderr)
        self.replay_fixture(ping="ConnectionLost")
        result = self.command("host-bootstrap", "--apply", ok=False)
        self.assertIn("not online in Systems Manager", result.stderr)
        self.replay_fixture(ping="Online")
        self.fixture["stacks"]["bootstrap-tenant-ec2"]["Parameters"][0]["ParameterValue"] = "false"
        self.store(self.data, self.fixture)
        result = self.command("host-bootstrap", "--apply", ok=False)
        self.assertIn("approved Mac boundary scope mismatch", result.stderr)
        self.assertFalse(any(r[1] in ("send-command", "get-secret-value") for r in self.recorded()))
        self.fixture["stacks"]["bootstrap-tenant-ec2"]["Parameters"][0]["ParameterValue"] = "true"
        self.replay_fixture(invocations=[dict(Status="Failed", StandardOutputContent="macos first boot: start\n",
                                              StandardErrorContent="macos first boot: environment pending; replay required after the secret write\n")])
        result = self.command("host-bootstrap", "--apply", ok=False)
        self.assertIn("macos first boot: environment pending; replay required after the secret write\n", result.stdout)
        self.assertIn("replay ended with status Failed", result.stderr)
        self.assertEqual(len([r for r in self.recorded() if r[1] == "send-command"]), 1)

    def test_host_bootstrap_first_phase_pending_then_lockdown_phase(self):
        template = (ROOT / "scripts/aws-host-macos.cfn.yaml").read_text()
        pending = "macos first boot: " + re.search(r"say '(lockdown pending; [^']+)'", template).group(1)
        payload = (ROOT / "scripts/macos-first-boot-replay.sh").read_text().splitlines()
        # The first phase stops before the lockdown with a named state, not a pass.
        self.replay_fixture(invocations=[dict(Status="Success", StandardErrorContent="",
                                              StandardOutputContent="macos first boot: start\nmacos bootstrap: PIN account readback pass\n"
                                                                    "macos bootstrap: lockdown pending; default accounts unchanged\n" + pending + "\n")])
        result = self.command("host-bootstrap", "--apply")
        self.assertIn(pending + "\n", result.stdout)
        self.assertIn("macos host-bootstrap: first phase complete; lockdown pending; the default accounts are unchanged\n", result.stdout)
        self.assertIn("macos host-bootstrap: next: recovery-check, then host-bootstrap --apply --lockdown\n", result.stdout)
        self.assertNotIn("pass;", result.stdout)
        # The lockdown phase sends the same payload with the lockdown phase line, after the same checks.
        self.calls.unlink()
        self.replay_fixture(invocations=[dict(Status="Success", StandardErrorContent="",
                                              StandardOutputContent="macos bootstrap: default-account restrictions pass\nmacos first boot: complete\n")])
        result = self.command("host-bootstrap", "--apply", "--lockdown")
        rows = self.recorded()
        operations = [r[1] for r in rows]
        sent = [r for r in rows if r[1] == "send-command"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(json.loads(sent[0][sent[0].index("--parameters") + 1])["commands"],
                         ["MACOS_PROJECT=tenant-ec2", "MACOS_PHASE=lockdown"] + payload)
        self.assertEqual(sent[0][sent[0].index("--comment") + 1], "tenant-ec2 macos lockdown")
        for before in ("describe-stacks", "describe-hosts", "describe-instances", "describe-instance-information", "get-secret-value"):
            self.assertLess(operations.index(before), operations.index("send-command"), before)
        self.assertIn("macos host-bootstrap: pass; lockdown applied; the first-boot stage is complete\n", result.stdout)
        # The host refusals arrive as status lines; a pending line in the lockdown phase is a failure.
        for output, status, message in (
                ("macos bootstrap: lockdown-not-pending\n", "Failed", "replay ended with status Failed"),
                ("macos first boot: lockdown refused; the first-boot stage is complete\n", "Failed", "replay ended with status Failed"),
                (pending + "\n", "Success", "the lockdown phase reported lockdown pending")):
            with self.subTest(output=output):
                self.replay_fixture(invocations=[dict(Status=status, StandardOutputContent="", StandardErrorContent=output)
                                                 if status == "Failed" else dict(Status=status, StandardOutputContent=output, StandardErrorContent="")])
                result = self.command("host-bootstrap", "--apply", "--lockdown", ok=False)
                self.assertIn(output, result.stdout)
                self.assertIn(message, result.stderr)

    def test_lockdown_flag_belongs_to_host_bootstrap_and_previews_without_calls(self):
        self.replay_fixture()
        for command in ("session", "ssh", "verify", "recovery-check", "deploy"):
            result = self.command(command, "--lockdown", ok=False)
            self.assertIn("--lockdown is an option of host-bootstrap", result.stderr)
        result = self.command("host-bootstrap", "--lockdown")
        self.assertIn("dry run: macos host-bootstrap", result.stdout)
        self.assertIn("dry run: phase lockdown; the host refuses without lockdown pending and refuses a second lockdown", result.stdout)
        result = self.command("host-bootstrap", "--dry-run")
        self.assertIn("dry run: phase first-boot; the stage stops with lockdown pending", result.stdout)
        self.assertEqual(self.recorded(), [])

    def test_recovery_check_prints_steps_without_aws_calls_or_writes(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file() and p.name not in ("public", "input.env")}
        result = self.command("recovery-check")
        self.assertEqual(self.recorded(), [])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file() and p.name not in ("public", "input.env")})
        wrapper = "sh " + str(self.repo / "scripts/aws-host.sh") + " --env-file " + str(self.source)
        lines = result.stdout.splitlines()
        self.assertIn("step 1, the Run As check: " + wrapper + " session --apply", lines)
        self.assertIn("  pass: the two lines are xyz1000 and zsh; any other user or an ended session is a failure", lines)
        ssh = lines[lines.index("step 2, the SSH login of xyz1000 with the owner key through SSM:") + 1]
        for part in ("ssh -F none -i <the private key of EC2_SSH_PUBLIC_KEY_FILE> -o IdentitiesOnly=yes -o PreferredAuthentications=publickey",
                     " -o HostKeyAlias=tenant-ec2-macos-host -o UserKnownHostsFile=" + str(self.repo / ".local/macos/tenant-ec2-macos-host.known_hosts"),
                     " -o StrictHostKeyChecking=accept-new -o 'ProxyCommand=" + wrapper + " ssh --apply' xyz1000@tenant-ec2-macos-host id -un"):
            self.assertIn(part, ssh)
        self.assertIn("step 3, only after both steps pass: " + wrapper + " host-bootstrap --apply --lockdown", lines)
        self.assertEqual(lines[-1], "on a failure: do not run --lockdown; follow docs/variants.md#recovery-before-the-lockdown")
        # The private key beside the public key file is named by its path, never read.
        (self.root / "owner").write_text("private-fixture-key-do-not-print\n")
        result = self.command("recovery-check")
        self.assertIn("ssh -F none -i " + str(self.root / "owner") + " -o", result.stdout)
        self.assertNotIn("private-fixture-key-do-not-print", result.stdout + result.stderr)
        # It refuses --apply, a missing runtime record and the homelab variant, all without AWS calls.
        self.assertIn("--apply is not supported for this command", self.command("recovery-check", "--apply", ok=False).stderr)
        self.store(self.state_path, {k: v for k, v in self.state.items() if k != "instanceId"})
        self.assertIn("recovery-check requires private runtime state", self.command("recovery-check", ok=False).stderr)
        self.assertIn("needs EC2_VARIANT=macos", self.command("recovery-check", values=dict(self.values, EC2_VARIANT="homelab"), ok=False).stderr)
        self.assertEqual(self.recorded(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
