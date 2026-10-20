#!/usr/bin/env python3
"""Offline Mac template and IAM fixtures. No AWS authorization claim follows."""
import argparse
import base64
import copy
import fnmatch
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import struct
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
MEMORY_RENDERER = None
sys.dont_write_bytecode = True


class Loader(yaml.SafeLoader):
    pass


def intrinsic(loader, tag, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {tag if tag == "Ref" else "Fn::" + tag: value}


Loader.add_multi_constructor("!", intrinsic)


def load(name):
    return yaml.load((ROOT / "scripts" / name).read_text(), Loader=Loader)


def values(value):
    return value if isinstance(value, list) else [value]


ACCOUNT = "0" * 12
PARAMS = {
    "Project": "fixture-kit", "AWS::Partition": "aws", "AWS::AccountId": ACCOUNT,
    "AWS::Region": "us-east-1", "AWS::StackName": "fixture-kit-macos-host", "Regions": ["us-east-1"],
    "EnableMacos": "true", "MemoryStage": "dev", "DeveloperPin": "XYZ1000",
    "MemoryArn": f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:memory/fixture_memory_dev-Abc1234567",
    "MemoryBucketName": "fixture-kit-memory-dev", "AgentActorId": "macos-agent", "ProposalProject": "example-project",
    "DedicatedHostId": "h-" + "a" * 17, "AmiId": "ami-" + "a" * 17,
    "AvailabilityZone": "us-east-1a", "RootVolumeSize": 100, "DataVolumeSize": 150,
    "VpcCidr": "192.0.2.0/24", "SshPublicKey": "ssh-ed25519 Zml4dHVyZQ==",
    "SnapshotRepository": "https://example.test/fixture/repository.git", "SnapshotTag": "portable-v1.0.0",
    "SnapshotCommit": "a" * 40, "DataVolume": "vol-" + "b" * 17,
    "PodmanProvider": "applehv", "PodmanCpus": "4", "PodmanMemoryMiB": "8192", "PodmanDiskGiB": "80",
}
PARAMS["MemoryBucket"] = PARAMS["MemoryBucketName"]
KEY_KIND = b"ssh-ed25519"
KEY_BLOB = struct.pack(">I", len(KEY_KIND)) + KEY_KIND + struct.pack(">I", 32) + b"k" * 32
PARAMS["SshPublicKey"] = "ssh-ed25519 " + base64.b64encode(KEY_BLOB).decode() + " fixture"
for name in ("MemoryArn", "MemoryBucketName", "AgentActorId", "ProposalProject"):
    PARAMS["Macos" + name] = PARAMS[name]
PARAMS["EnvironmentSecret"] = f"arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:fixture-kit/macos/env-Ab1234"
PARAMS["GeneratedSecret"] = f"arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:fixture-kit/macos/generated-Ab1234"
PARAMS["Boundary"] = f"arn:aws:iam::{ACCOUNT}:policy/fixture-kit-boundary"
PARAMS["MacosBoundary"] = f"arn:aws:iam::{ACCOUNT}:policy/fixture-kit-macos-boundary"
NONE = object()


def resolve(value, params=PARAMS, conditions=None):
    conditions = conditions or {}
    if isinstance(value, list):
        result = [resolve(item, params, conditions) for item in value]
        return [item for item in result if item is not NONE]
    if not isinstance(value, dict):
        return value
    if "Ref" in value:
        return NONE if value["Ref"] == "AWS::NoValue" else params.get(value["Ref"], value["Ref"])
    if "Condition" in value and len(value) == 1:
        return resolve(conditions[value["Condition"]], params, conditions)
    if "Fn::Sub" in value:
        text = value["Fn::Sub"]
        if isinstance(text, list):
            text, mapping = text
            params = dict(params, **resolve(mapping, params, conditions))
        return re.sub(r"\$\{([^}]+)\}", lambda m: "${" + m[1][1:] + "}" if m[1].startswith("!")
                      else str(params.get(m[1], m[1])), text)
    if "Fn::GetAtt" in value:
        return "fixture-attribute"
    if "Fn::Equals" in value:
        a, b = resolve(value["Fn::Equals"], params, conditions)
        return a == b
    if "Fn::Not" in value:
        return not resolve(value["Fn::Not"][0], params, conditions)
    if "Fn::And" in value:
        return all(resolve(value["Fn::And"], params, conditions))
    if "Fn::If" in value:
        name, yes, no = value["Fn::If"]
        return resolve(yes if resolve(conditions[name], params, conditions) else no, params, conditions)
    if "Fn::Join" in value:
        separator, items = value["Fn::Join"]
        return separator.join(resolve(items, params, conditions))
    if "Fn::Split" in value:
        separator, text = value["Fn::Split"]
        return resolve(text, params, conditions).split(separator)
    if "Fn::Select" in value:
        index, items = value["Fn::Select"]
        return resolve(items, params, conditions)[index]
    return {key: resolve(item, params, conditions) for key, item in value.items()}


def condition_matches(conditions, context):
    for operation, entries in conditions.items():
        for key, expected in entries.items():
            actual = context.get(key)
            if operation == "Null":
                if (actual is None or actual == []) != (expected == "true"):
                    return False
                continue
            if operation.endswith("IfExists") and actual is None:
                continue
            op = operation.removesuffix("IfExists")
            negative = op in ("StringNotEquals", "ArnNotEquals", "StringNotLike", "ArnNotLike")
            if actual is None and not op.startswith("ForAllValues:"):
                if negative:
                    continue
                return False
            tests = [any(fnmatch.fnmatchcase(str(a), str(e)) if "Like" in op else str(a) == str(e)
                         for e in values(expected)) for a in values(actual) if a is not None]
            matched = all(tests) if op.startswith("ForAllValues:") else any(tests)
            if negative:
                matched = not matched
            if not matched:
                return False
    return True


def decision(statements, action, resource, context=None):
    """Evaluate only operators present in these fixtures, including explicit-deny precedence."""
    allowed = False
    for statement in statements:
        if not any(fnmatch.fnmatchcase(action.lower(), a.lower()) for a in values(statement["Action"])):
            continue
        if not any(fnmatch.fnmatchcase(resource, r) for r in values(statement.get("Resource", "*"))):
            continue
        if not condition_matches(statement.get("Condition", {}), context or {}):
            continue
        if statement["Effect"] == "Deny":
            return False
        allowed = True
    return allowed


def stat_mode(path):
    return path.stat().st_mode & 0o7777


class MacosInfraTests(unittest.TestCase):
    def setUp(self):
        self.allocation = load("aws-macos-allocation.cfn.yaml")
        self.runtime = load("aws-host-macos.cfn.yaml")
        self.admin = load("aws-provisioner.cfn.yaml")
        self.policy = resolve(self.runtime["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"])["Statement"]
        self.boundary = resolve(self.admin["Resources"]["MacosBoundary"]["Properties"]["PolicyDocument"])["Statement"]

    def operator_statements(self, enabled=True):
        params = dict(PARAMS, EnableMacos="true" if enabled else "false")
        result = []
        for name in ("ProvisionerPolicy", "OperationsPolicy", "MacosGuardPolicy", "MacosProvisionerPolicy", "MacosProvisionerDeny"):
            resource = self.admin["Resources"][name]
            if resource.get("Condition") and not resolve(self.admin["Conditions"][resource["Condition"]], params):
                continue
            result.extend(resolve(resource["Properties"]["PolicyDocument"], params, self.admin["Conditions"])["Statement"])
        return result

    def test_exact_contracts_and_size(self):
        self.assertEqual(set(self.allocation["Parameters"]), {"Project", "AvailabilityZone", "InstanceType"})
        self.assertEqual(set(self.allocation["Outputs"]), {"HostId", "AvailabilityZone"})
        self.assertEqual(set(self.runtime["Parameters"]), {
            "Project", "DedicatedHostId", "AvailabilityZone", "AmiId", "VpcCidr", "RootVolumeSize", "DataVolumeSize",
            "SshPublicKey", "SnapshotRepository", "SnapshotTag", "SnapshotCommit", "MemoryStage", "MemoryArn",
            "MemoryBucket", "AgentActorId", "ProposalProject", "DeveloperPin",
            "PodmanProvider", "PodmanCpus", "PodmanMemoryMiB", "PodmanDiskGiB"})
        self.assertEqual(set(self.runtime["Outputs"]), {"InstanceId", "HostId", "DataVolumeId", "HostRoleArn", "SessionDocumentName", "DeveloperAccount"})
        for name in ("aws-macos-allocation.cfn.yaml", "aws-host-macos.cfn.yaml", "aws-provisioner.cfn.yaml"):
            self.assertLessEqual((ROOT / "scripts" / name).stat().st_size, 51200)
        for resource in self.admin["Resources"].values():
            if resource["Type"] == "AWS::IAM::ManagedPolicy":
                document = resolve(resource["Properties"]["PolicyDocument"], conditions=self.admin["Conditions"])
                self.assertLessEqual(len(json.dumps(document, separators=(",", ":"))), 6144)

    def test_allocation_and_runtime_separation(self):
        host = self.allocation["Resources"]["Host"]
        self.assertEqual(host["Type"], "AWS::EC2::Host")
        self.assertNotIn("DeletionPolicy", host)
        self.assertNotIn("UpdateReplacePolicy", host)
        self.assertEqual(self.allocation["Parameters"]["InstanceType"]["AllowedValues"], ["mac2-m2.metal"])
        for name, value in (("AutoPlacement", "off"), ("HostRecovery", "on"), ("HostMaintenance", "on")):
            self.assertEqual(host["Properties"][name], value)
        instance = self.runtime["Resources"]["Instance"]["Properties"]
        self.assertEqual(instance["Tenancy"], "host")
        self.assertEqual(instance["Affinity"], "host")
        self.assertEqual(instance["HostId"], {"Ref": "DedicatedHostId"})
        self.assertEqual(instance["AvailabilityZone"], {"Ref": "AvailabilityZone"})
        self.assertEqual(instance["InstanceType"], "mac2-m2.metal")
        self.assertNotIn("ImportValue", json.dumps(self.runtime))
        self.assertNotIn("AWS::EC2::Host", json.dumps(self.runtime))

    def test_no_ingress_and_encrypted_retention(self):
        resources = self.runtime["Resources"]
        forbidden = ("AWS::EC2::EIP", "AWS::EC2::SecurityGroupIngress", "AWS::EFS::", "AWS::Route53::", "AWS::CertificateManager::", "AWS::ElasticLoadBalancing", "AWS::IAM::AccessKey", "AWS::EC2::KeyPair")
        self.assertFalse(any(r["Type"].startswith(forbidden) for r in resources.values()))
        self.assertEqual(resources["InstanceSecurityGroup"]["Properties"]["SecurityGroupIngress"], [])
        self.assertTrue(resources["Subnet"]["Properties"]["MapPublicIpOnLaunch"])
        for name in ("DataVolume", "EnvironmentSecret", "GeneratedSecret"):
            self.assertEqual(resources[name]["DeletionPolicy"], "RetainExceptOnCreate")
            self.assertEqual(resources[name]["UpdateReplacePolicy"], "Retain")
        data = resources["DataVolume"]["Properties"]
        self.assertTrue(data["Encrypted"])
        self.assertEqual(data["VolumeType"], "gp3")
        instance = resources["Instance"]["Properties"]
        root = instance["BlockDeviceMappings"][0]
        self.assertEqual(root["DeviceName"], "/dev/sda1")
        self.assertEqual(root["Ebs"]["VolumeType"], "gp3")
        self.assertTrue(root["Ebs"]["Encrypted"])
        self.assertFalse(root["Ebs"]["DeleteOnTermination"])
        self.assertEqual(instance["MetadataOptions"]["HttpTokens"], "required")
        self.assertEqual(instance["MetadataOptions"]["HttpPutResponseHopLimit"], 1)
        self.assertEqual(json.loads(resources["GeneratedSecret"]["Properties"]["SecretString"]), {"EC2_PI_DURABLE_LITELLM_API_KEY": ""})

    def test_pin_document_and_userdata(self):
        document = self.runtime["Resources"]["SessionDocument"]["Properties"]
        self.assertEqual(document["DocumentType"], "Session")
        self.assertEqual(document["Name"], {"Fn::Sub": "${Project}-macos-session"})
        for pin in ("XYZ0000", "XYZ1000", "XYZ9999"):
            inputs = resolve(document["Content"]["inputs"], dict(PARAMS, DeveloperPin=pin))
            self.assertEqual(inputs["runAsDefaultUser"], pin.lower())
            self.assertEqual(resolve(self.runtime["Outputs"]["DeveloperAccount"]["Value"], dict(PARAMS, DeveloperPin=pin)), pin.lower())
            self.assertTrue(inputs["runAsEnabled"])
            self.assertEqual(inputs["shellProfile"]["linux"],
                             f'test "$(id -un)" = "{pin.lower()}" && cd "/Users/{pin.lower()}" && exec /bin/zsh -l; exit 1')
        pattern = self.runtime["Parameters"]["DeveloperPin"]["AllowedPattern"]
        for invalid in ("xyz1000", "ABC1000", "XYZ10000", "root", "XYZ1000\nwhoami"):
            self.assertIsNone(re.fullmatch(pattern, invalid))
        script = resolve(self.runtime["Resources"]["Instance"]["Properties"]["UserData"]["Fn::Base64"])
        self.assertTrue(script.startswith("#!/bin/zsh\n"))
        self.assertLess(script.index("rev-parse HEAD"), script.index('/bin/zsh "$repo/scripts/host-bootstrap-macos.sh"'))
        self.assertNotIn("--stack-env", script)
        self.assertNotIn("stack.env", script)
        self.assertIn('--machine /etc/fixture-kit/machine.json --env-file "$temp/env.json"', script)
        self.assertIn("GIT_TERMINAL_PROMPT=0", script)
        self.assertNotIn("EC2_MACOS_PERSON_PIN", script)
        self.assertNotIn(PARAMS["MemoryArn"], script)
        self.assertNotIn(PARAMS["MemoryBucket"], script)
        self.assertNotIn("/bin/bash", script)
        self.assertIn("unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY", script)
        self.assertIn("AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null", script)
        result = subprocess.run(["zsh", "-n"], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_userdata_refuses_snapshot_symlink_before_writes(self):
        script = resolve(self.runtime["Resources"]["Instance"]["Properties"]["UserData"]["Fn::Base64"])
        block = script[:script.index('install -d')]
        for name in ('opt', 'opt/fixture-kit', 'opt/fixture-kit/macos',
                     'opt/fixture-kit/macos/snapshot', 'opt/fixture-kit/macos/snapshot/.git'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='macos-path-test-') as directory:
                root = Path(directory)
                target = root / 'outside'
                target.mkdir()
                link = root / name
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target, target_is_directory=True)
                run_script = block.replace('/etc/fixture-kit', str(root / 'etc/fixture-kit')).replace('/opt', str(root / 'opt'))
                result = subprocess.run(['zsh'], input=run_script, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr.strip(), 'macos user data: snapshot path needs review')
                self.assertEqual(list(target.iterdir()), [])
                self.assertFalse((root / 'etc').exists())

    def test_machine_parameter_forms(self):
        parameters = self.runtime["Parameters"]
        bounds = {"DataVolumeSize": (100, 1000), "PodmanCpus": (1, 8),
                  "PodmanMemoryMiB": (1024, 16384), "PodmanDiskGiB": (20, 1024)}
        for name, (low, high) in bounds.items():
            definition = parameters[name]
            self.assertEqual(definition["Type"], "String")
            pattern = re.compile(definition["AllowedPattern"])
            for number in range(0, high + 2):
                self.assertEqual(bool(pattern.fullmatch(str(number))), low <= number <= high, (name, number))
            self.assertIsNotNone(pattern.fullmatch(definition["Default"]))
            for bad in ("", "-1", "01", "1.5", "4e0", "4\n", " 4", "4 ", "$(id)", "4,\"x\":1"):
                self.assertIsNone(pattern.fullmatch(bad), (name, bad))
        patterns = {name: re.compile(parameters[name]["AllowedPattern"]) for name in
                    ("Project", "DeveloperPin", "SshPublicKey", "MemoryArn", "MemoryBucket",
                     "AgentActorId", "ProposalProject", "PodmanProvider")}
        for name, pattern in patterns.items():
            self.assertIsNotNone(pattern.fullmatch(PARAMS[name]), name)
            for suffix in ('"', "\\", "\n", "$(id)", "`id`"):
                self.assertIsNone(pattern.fullmatch(PARAMS[name] + suffix), (name, suffix))
        self.assertIsNone(patterns["PodmanProvider"].fullmatch("libkrun"))
        self.assertEqual(parameters["PodmanProvider"]["Default"], "applehv")
        self.assertEqual(parameters["MemoryStage"]["AllowedValues"], ["dev", "test", "prod"])
        for comment in ("", " fixture", " owner@example.test"):
            self.assertIsNotNone(patterns["SshPublicKey"].fullmatch(PARAMS["SshPublicKey"].rsplit(" ", 1)[0] + comment))
        for bad in ("PRIVATE KEY", "ssh-ed25519 invalid!", PARAMS["SshPublicKey"] + " comment",
                    PARAMS["SshPublicKey"] + "\nEOT\nid"):
            self.assertIsNone(patterns["SshPublicKey"].fullmatch(bad))
        self.assertEqual(self.runtime["Resources"]["DataVolume"]["Properties"]["Size"], {"Ref": "DataVolumeSize"})

    def test_machine_json_rendering_and_modes(self):
        for pin, sizes in (("XYZ0000", ("1", "1024", "20")), ("XYZ1000", ("4", "8192", "80")),
                           ("XYZ9999", ("8", "16384", "1024"))):
            params = dict(PARAMS, DeveloperPin=pin, PodmanCpus=sizes[0], PodmanMemoryMiB=sizes[1], PodmanDiskGiB=sizes[2])
            script = resolve(self.runtime["Resources"]["Instance"]["Properties"]["UserData"]["Fn::Base64"], params)
            self.assertIn('install -d -o root -g admin -m 0755 /etc/fixture-kit', script)
            self.assertIn('install -d -o root -g admin -m 0700 /etc/fixture-kit/macos', script)
            self.assertIn('install -o root -g admin -m 0644 "$temp/machine.json" /etc/fixture-kit/machine.json', script)
            self.assertIn('chmod 0600 "$temp/env.json"', script)
            # Execute the actual shell-rendered block, not a second implementation of its JSON.
            block = script[script.index("account=$("):script.index('plutil -lint "$temp/machine.json"')]
            with tempfile.TemporaryDirectory(prefix="macos-machine-test-") as directory:
                result = subprocess.run(["zsh", "-eu", "-c", block], env=dict(os.environ, temp=directory),
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                machine = json.loads((Path(directory) / "machine.json").read_text())
            self.assertEqual(machine, {
                "schemaVersion": 1, "project": PARAMS["Project"], "variant": "macos", "region": PARAMS["AWS::Region"],
                "engine": "podman", "fileSet": ["compose.macos.yaml"],
                "services": ["litellm", "litellm-db", "open-knowledge", "ok-auth"],
                "account": {"pin": pin, "name": pin.lower(), "home": "/Users/" + pin.lower(),
                            "shell": "/bin/zsh", "sshPublicKey": PARAMS["SshPublicKey"]},
                "disk": {"volumeId": PARAMS["DataVolume"], "mountPoint": "/Volumes/data"},
                "memory": {"stage": PARAMS["MemoryStage"], "agentActorId": PARAMS["AgentActorId"],
                           "proposalProject": PARAMS["ProposalProject"], "configPath": "/run/tenant-ec2/memory.json"},
                "podman": {"provider": "applehv", "cpus": int(sizes[0]), "memoryMiB": int(sizes[1]), "diskGiB": int(sizes[2])}})
            self.assertTrue(all(type(machine["podman"][key]) is int for key in ("cpus", "memoryMiB", "diskGiB")))
            for excluded in ("MemoryArn", "MemoryBucket", "pi-durable", "EC2_SNAPSHOT_CLONE_TOKEN"):
                self.assertNotIn(excluded, json.dumps(machine))

    STUB = """#!/usr/bin/python3
import json, os, pathlib, shutil, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
if name == 'install':
    args[args.index('-o') + 1] = str(os.getuid())
    args[args.index('-g') + 1] = str(os.getgid())
    sys.exit(subprocess.call(['/usr/bin/install', *args]))
elif name == 'plutil' and args[0] == '-lint':
    json.loads(pathlib.Path(args[1]).read_text())
elif name == 'aws':
    assert args[:4] == ['--region', 'us-east-1', 'secretsmanager', 'get-secret-value']
    print('{}' if os.environ.get('EMPTY_ENV') else json.dumps({'EC2_SNAPSHOT_CLONE_TOKEN': 'private-fixture-do-not-print'}))
elif name == 'git' and args[:3] == ['-c', 'credential.helper=', 'clone']:
    assert args[3:7] == ['--depth', '1', '--branch', 'portable-v1.0.0'] and os.environ['GIT_TERMINAL_PROMPT'] == '0'
    target = pathlib.Path(args[-1])
    assert target.name.startswith('snapshot.partial.') and target.is_dir() and not any(target.iterdir())
    (target / '.git').mkdir()
    (target / 'scripts').mkdir()
    (target / 'half-written').write_text('')
    if os.environ.get('CLONE_FAIL'):
        sys.exit(128)
    shutil.copy(os.environ['BOOT_SOURCE'], target / 'scripts/host-bootstrap-macos.sh')
    (target / 'half-written').unlink()
elif name == 'git' and args[2:] == ['rev-parse', 'HEAD']:
    print('b' * 40 if os.environ.get('BAD_COMMIT') else 'a' * 40)
elif name == 'git' and args[2:] == ['status', '--porcelain', '--untracked-files=all']:
    pass
else:
    raise SystemExit('unexpected fixture command')
"""
    BOOT = """#!/bin/zsh
set -eu
[[ "$1" = --machine && "$3" = --env-file ]]
[[ $# = 4 || ( $# = 5 && "$5" = --lockdown ) ]]
python3 - "$2" "$4" "${5:-first-phase}" <<'PY'
import json, os, pathlib, stat, sys
machine, env = map(pathlib.Path, sys.argv[1:3])
assert stat.S_IMODE(machine.stat().st_mode) == 0o644
assert stat.S_IMODE(machine.parent.stat().st_mode) == 0o755
assert stat.S_IMODE(env.stat().st_mode) == 0o600
assert stat.S_IMODE(env.parent.stat().st_mode) == 0o700
assert json.loads(machine.read_text())['account']['name'] == 'xyz1000'
assert json.loads(env.read_text())['EC2_SNAPSHOT_CLONE_TOKEN'] == 'private-fixture-do-not-print'
with open(os.environ['BOOT_CALLED'], 'a') as stream:
    stream.write(str(env) + ' ' + sys.argv[3] + '\\n')
PY
exit "$BOOT_EXIT"
"""

    def first_boot(self, root, snapshot=True):
        """Rendered user data and the tracked replay payload, pointed at a fixture root."""
        script = resolve(self.runtime["Resources"]["Instance"]["Properties"]["UserData"]["Fn::Base64"])
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name in ("install", "plutil", "aws", "git"):
            path = bin_dir / name
            path.write_text(self.STUB)
            path.chmod(0o755)
        (root / "boot-source").write_text(self.BOOT)
        if snapshot:
            repo = root / "opt/fixture-kit/macos/snapshot"
            (repo / "scripts").mkdir(parents=True)
            (repo / ".git").mkdir()
            (repo / "scripts/host-bootstrap-macos.sh").write_text(self.BOOT)
        userdata = script.replace('/etc/fixture-kit', str(root / 'etc/fixture-kit')).replace(
            '/opt/fixture-kit', str(root / 'opt/fixture-kit')).replace(
            'export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin',
            f'export PATH={bin_dir}:/usr/bin:/bin')
        payload = (ROOT / "scripts/macos-first-boot-replay.sh").read_text()
        self.assertEqual(payload.count('etc_dir=/etc/$MACOS_PROJECT'), 1)
        self.assertEqual(payload.count('-user 0'), 2)
        payload = payload.replace(
            'etc_dir=/etc/$MACOS_PROJECT', f'etc_dir={root}/etc/$MACOS_PROJECT').replace('-user 0', f'-user {os.getuid()}')
        called = root / "bootstrap-called"

        def lines(phase):
            # The two assignments of the wrapper; an empty phase leaves the second one out.
            return "MACOS_PROJECT=fixture-kit\n" + ("MACOS_PHASE=" + phase + "\n" if phase else "") + payload

        def run(text, shell, bootstrap_exit=0, **extra):
            result = subprocess.run([shell], input=text, text=True, capture_output=True, timeout=20,
                                    env=dict(os.environ, BOOT_CALLED=str(called), BOOT_EXIT=str(bootstrap_exit),
                                             BOOT_SOURCE=str(root / "boot-source"), **extra))
            self.assertNotIn("private-fixture-do-not-print", result.stdout + result.stderr)
            self.assertFalse(list((root / "etc/fixture-kit/macos").glob("bootstrap.*")))
            self.assertFalse(list((root / "etc/fixture-kit/macos").glob("userdata.*")))
            self.assertFalse(list((root / "opt/fixture-kit/macos").glob("snapshot.partial.*")))
            calls = called.read_text().splitlines() if called.exists() else []
            for env in calls:
                self.assertFalse(Path(env.split()[0]).exists())
            return result, len(calls)
        return (lambda bootstrap_exit=0, **extra: run(userdata, "zsh", bootstrap_exit, **extra),
                lambda bootstrap_exit=0, phase="first-boot", **extra: run(lines(phase), "sh", bootstrap_exit, **extra))

    def test_userdata_bootstrap_private_input_lifetime(self):
        for bootstrap_exit, extra in ((0, {}), (7, {}), (0, {"EMPTY_ENV": "1"}), (0, {"BAD_COMMIT": "1"})):
            with self.subTest(bootstrap_exit=bootstrap_exit, extra=extra), tempfile.TemporaryDirectory(prefix="macos-userdata-test-") as directory:
                root = Path(directory)
                userdata, _ = self.first_boot(root)
                result, calls = userdata(bootstrap_exit, **extra)
                self.assertEqual(result.returncode, 1 if extra else bootstrap_exit, result.stderr)
                self.assertEqual(calls, 0 if extra else 1)
                self.assertTrue((root / "etc/fixture-kit/machine.json").is_file())
                self.assertEqual((root / "etc/fixture-kit/macos/first-boot.done").exists(), not extra and not bootstrap_exit)

    def test_userdata_and_replay_run_one_stage(self):
        script = resolve(self.runtime["Resources"]["Instance"]["Properties"]["UserData"]["Fn::Base64"])
        head, rest = script.split("cat >\"$temp/first-boot.zsh\" <<'STAGE'\n", 1)
        stage, tail = rest.split("\nSTAGE\n", 1)
        self.assertTrue(stage.startswith("#!/bin/zsh\n[[ ! -o xtrace ]] || exit 1\n"))
        result = subprocess.run(["zsh", "-n"], input=stage, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The secret read, the clone, the checks and the bootstrap call exist once, inside the stage.
        for text in ("get-secret-value", "git -c credential.helper= clone", "rev-parse HEAD", "host-bootstrap-macos.sh"):
            self.assertIn(text, stage)
            self.assertNotIn(text, head + tail)
        self.assertEqual(tail.strip().splitlines(), [
            "/bin/zsh -n \"$temp/first-boot.zsh\" || die 'invalid first-boot stage'",
            'install -o root -g wheel -m 0700 "$temp/first-boot.zsh" /etc/fixture-kit/macos/first-boot.zsh',
            'rm -rf -- "$temp"', 'trap - EXIT', 'exec /bin/zsh /etc/fixture-kit/macos/first-boot.zsh'])
        self.assertIn("unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ACCESS_KEY_ID", head)
        self.assertIn("unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ACCESS_KEY_ID", stage)
        order = [stage.index(text) for text in ("zsystem flock", "first-boot.done ]]", "get-secret-value",
                                               "rev-parse HEAD", "host-bootstrap-macos.sh", ": >$stage/first-boot.done")]
        self.assertEqual(order, sorted(order))
        for text in (PARAMS["MemoryArn"], PARAMS["MemoryBucket"], "EC2_MACOS_DEVELOPER_PASSWORD", "xtrace\nset -x"):
            self.assertNotIn(text, stage)
        payload = (ROOT / "scripts/macos-first-boot-replay.sh").read_text()
        self.assertTrue(payload.startswith("#!/bin/sh\n"))
        self.assertIn('stage=$etc_dir/macos/first-boot.zsh', payload)
        self.assertEqual(payload.strip().splitlines()[-1], 'exec /bin/zsh "$stage" "$@"')
        # The wrapper prints its pending result only on the exact line of the stage.
        wrapper = (ROOT / "scripts/aws-host.sh").read_text()
        pending = re.search(r'^REPLAY_PENDING = "macos first boot: ([^"]+)"$', wrapper, re.M).group(1)
        self.assertEqual(stage.count("say '" + pending + "'"), 1)
        code = "\n".join(line for line in payload.split("set +x", 1)[1].splitlines() if not line.lstrip().startswith("#"))
        for word in ("aws", "git", "secret", "EC2_[A-Z_]+", "curl", "rm", "eval"):
            self.assertIsNone(re.search(r"\b" + word + r"\b", code), word)

    def test_replay_after_empty_environment_matches_filled_first_boot(self):
        with tempfile.TemporaryDirectory(prefix="macos-replay-test-") as directory:
            root = Path(directory)
            userdata, replay = self.first_boot(root, snapshot=False)
            result, calls = userdata(EMPTY_ENV="1")
            self.assertEqual(result.returncode, 1)
            self.assertIn("macos first boot: environment pending; replay required after the secret write", result.stderr)
            self.assertEqual(calls, 0)
            stage = root / "etc/fixture-kit/macos/first-boot.zsh"
            self.assertEqual(stat_mode(stage), 0o700)
            self.assertFalse((root / "opt/fixture-kit/macos/snapshot").exists())
            # A replay with the secret still empty stops at the same place and changes nothing.
            result, calls = replay(EMPTY_ENV="1")
            self.assertEqual((result.returncode, calls), (1, 0))
            self.assertIn("environment pending", result.stderr)
            result, calls = replay(CLONE_FAIL="1")
            self.assertEqual((result.returncode, calls), (1, 0))
            self.assertIn("macos first boot: snapshot clone failed", result.stderr)
            result, calls = replay(BAD_COMMIT="1")
            self.assertEqual((result.returncode, calls), (1, 0))
            self.assertIn("macos first boot: snapshot commit differs", result.stderr)
            self.assertFalse((root / "opt/fixture-kit/macos/snapshot").exists())
            result, calls = replay(7)
            self.assertEqual((result.returncode, calls), (7, 1))
            self.assertFalse((root / "etc/fixture-kit/macos/first-boot.done").exists())
            result, calls = replay()
            self.assertEqual((result.returncode, calls), (0, 2), result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["macos first boot: start", "macos first boot: environment read",
                                                          "macos first boot: snapshot verified", "macos first boot: complete"])
            self.assertTrue((root / "opt/fixture-kit/macos/snapshot/scripts/host-bootstrap-macos.sh").is_file())
            # The stage ran to completion once; a later replay is a no-op with a status line.
            result, calls = replay()
            self.assertEqual((result.returncode, calls, result.stdout), (0, 2, "macos first boot: already complete; no replay needed\n"))

        with tempfile.TemporaryDirectory(prefix="macos-replay-test-") as directory:
            # The same first boot with a filled secret gives the same end state in one run.
            root = Path(directory)
            userdata, _ = self.first_boot(root, snapshot=False)
            result, calls = userdata()
            self.assertEqual((result.returncode, calls), (0, 1), result.stderr)
            self.assertTrue((root / "etc/fixture-kit/macos/first-boot.done").exists())

    def test_first_phase_stops_before_lockdown_and_lockdown_runs_once(self):
        wrapper = (ROOT / "scripts/aws-host.sh").read_text()
        pending = re.search(r'^REPLAY_PENDING = "([^"]+)"$', wrapper, re.M).group(1)
        with tempfile.TemporaryDirectory(prefix="macos-lockdown-test-") as directory:
            root = Path(directory)
            userdata, replay = self.first_boot(root, snapshot=False)
            done = root / "etc/fixture-kit/macos/first-boot.done"
            called = root / "bootstrap-called"
            # Exit 3 of the bootstrap: the first phase stopped before the lockdown. The stage is not complete.
            result, calls = userdata(3)
            self.assertEqual((result.returncode, calls), (0, 1), result.stderr)
            self.assertEqual(result.stdout.splitlines()[-1], pending)
            self.assertFalse(done.exists())
            result, calls = replay(3)
            self.assertEqual((result.returncode, calls, result.stdout.splitlines()[-1]), (0, 2, pending))
            self.assertFalse(done.exists())
            # A failed lockdown phase keeps the stage open; the next lockdown phase completes it.
            result, calls = replay(1, phase="lockdown")
            self.assertEqual((result.returncode, calls), (1, 3))
            self.assertFalse(done.exists())
            result, calls = replay(0, phase="lockdown")
            self.assertEqual((result.returncode, calls), (0, 4), result.stderr)
            self.assertEqual(result.stdout.splitlines()[-1], "macos first boot: complete")
            self.assertTrue(done.exists())
            self.assertEqual([line.split()[1] for line in called.read_text().splitlines()],
                             ["first-phase", "first-phase", "--lockdown", "--lockdown"])
            # A second lockdown stops before the bootstrap; a plain replay is a no-op.
            result, calls = replay(0, phase="lockdown")
            self.assertEqual((result.returncode, calls, result.stdout), (1, 4, ""))
            self.assertEqual(result.stderr, "macos first boot: lockdown refused; the first-boot stage is complete\n")
            result, calls = replay(0)
            self.assertEqual((result.returncode, calls, result.stdout), (0, 4, "macos first boot: already complete; no replay needed\n"))
            # A missing or unknown phase stops in the payload; an unknown stage argument stops in the stage.
            for phase in ("", "lockdown-now", "Lockdown"):
                with self.subTest(phase=phase):
                    result, calls = replay(0, phase=phase)
                    self.assertEqual((result.returncode, calls, result.stderr), (1, 4, "macos first boot: phase refused\n"))
            stage = root / "etc/fixture-kit/macos/first-boot.zsh"
            for arguments in (["--other"], ["--lockdown", "--lockdown"]):
                result = subprocess.run(["zsh", str(stage), *arguments], capture_output=True, text=True, timeout=10)
                self.assertEqual((result.returncode, result.stderr), (1, "macos first boot: stage argument refused\n"))
            self.assertEqual(len(called.read_text().splitlines()), 4)

    def test_replay_payload_refuses_unsafe_or_busy_stage(self):
        import fcntl
        with tempfile.TemporaryDirectory(prefix="macos-replay-test-") as directory:
            root = Path(directory)
            userdata, replay = self.first_boot(root)
            result, _ = replay()
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, "macos first boot: stage missing; the user data stopped before it\n")
            userdata(EMPTY_ENV="1")
            stage = root / "etc/fixture-kit/macos/first-boot.zsh"
            lock = root / "etc/fixture-kit/macos/first-boot.lock"
            with lock.open("a") as stream:
                fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result, calls = replay()
            self.assertEqual((result.returncode, calls), (1, 0))
            self.assertEqual(result.stderr, "macos first boot: stage already running; wait for it to end\n")
            for path, mode, message in ((stage, 0o755, "stage owner or mode refused"), (stage.parent, 0o770, "stage owner or mode refused"),
                                        (stage.parent.parent, 0o775, "stage owner or mode refused")):
                with self.subTest(path=path.name, mode=oct(mode)):
                    original = stat_mode(path)
                    path.chmod(mode)
                    result, calls = replay()
                    path.chmod(original)
                    self.assertEqual((result.returncode, calls), (1, 0))
                    self.assertEqual(result.stderr, "macos first boot: " + message + "\n")
            moved = stage.with_name("moved.zsh")
            stage.rename(moved)
            stage.symlink_to(moved)
            result, calls = replay()
            self.assertEqual((result.returncode, calls), (1, 0))
            self.assertEqual(result.stderr, "macos first boot: stage path needs review\n")
            self.assertEqual(moved.stat().st_nlink, 1)
            stage.unlink()
            os.link(moved, stage)
            result, calls = replay()
            self.assertEqual((result.returncode, calls), (1, 0))
            self.assertEqual(result.stderr, "macos first boot: stage owner or mode refused\n")

    def test_replay_uses_existing_rights_only(self):
        documents = [name for name, resource in self.runtime["Resources"].items() if resource["Type"] == "AWS::SSM::Document"]
        self.assertEqual(documents, ["SessionDocument"])
        policy = self.operator_statements(True)
        instance = f"arn:aws:ec2:us-east-1:{ACCOUNT}:instance/i-fixture"
        context = {"aws:RequestedRegion": "us-east-1", "ssm:resourceTag/Variant": "macos"}
        self.assertTrue(decision(policy, "ssm:SendCommand", instance, context))
        self.assertTrue(decision(policy, "ssm:SendCommand", "arn:aws:ssm:us-east-1::document/AWS-RunShellScript", context))
        for document in (f"arn:aws:ssm:us-east-1:{ACCOUNT}:document/fixture-kit-macos-first-boot",
                         f"arn:aws:ssm:us-east-1:{ACCOUNT}:document/fixture-kit-macos-session"):
            self.assertFalse(decision(policy, "ssm:SendCommand", document, context))
        for action in ("ssm:GetCommandInvocation", "ssm:DescribeInstanceInformation"):
            self.assertTrue(decision(policy, action, "*", context))
        self.assertTrue(decision(policy, "secretsmanager:GetSecretValue", PARAMS["EnvironmentSecret"], context))

    def test_scoped_role_equals_boundary(self):
        by_sid = lambda statements: {s["Sid"]: s for s in statements}
        runtime, boundary = by_sid(self.policy), by_sid(self.boundary)
        self.assertEqual(set(runtime), set(boundary))
        for name, statement in runtime.items():
            expected = copy.deepcopy(boundary[name])
            if name in ("EnvSecretRead", "GeneratedSecretWrite"):
                self.assertTrue(fnmatch.fnmatchcase(statement["Resource"], expected["Resource"]))
                expected["Resource"] = statement["Resource"]
            self.assertEqual(statement, expected)
        self.assertEqual(self.runtime["Resources"]["InstanceRole"]["Properties"]["PermissionsBoundary"],
                         {"Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/${Project}-macos-boundary"})
        self.assertEqual(runtime["MemoryRead"]["Condition"], {"StringLike": {"bedrock-agentcore:namespace": ["/shared/*", "/project/*/shared/*"]}})
        self.assertEqual(runtime["MemoryWrite"]["Condition"]["Null"], {"bedrock-agentcore:namespace": "false"})
        self.assertEqual(runtime["MemoryWrite"]["Condition"]["ForAllValues:StringLike"],
                         {"bedrock-agentcore:namespace": ["/project/example-project/proposals/macos-agent/*"]})
        for statement in runtime.values():
            self.assertFalse(any(action.endswith(":*") for action in values(statement["Action"])))

    def test_memory_and_bucket_negative_fixtures(self):
        memory = PARAMS["MemoryArn"]
        bucket = "arn:aws:s3:::" + PARAMS["MemoryBucketName"]
        key = "bedrock-agentcore:namespace"
        for policy in (self.policy, self.boundary):
            for action in ("RetrieveMemoryRecords", "ListMemoryRecords", "GetMemoryRecord"):
                name = "bedrock-agentcore:" + action
                for namespace in ("/shared/entry", "/project/one/shared/entry"):
                    self.assertTrue(decision(policy, name, memory, {key: namespace}))
                for namespace in ("/actor/macos-agent/entry", "/project/one/proposals/macos-agent/entry", "/shared-other/entry"):
                    self.assertFalse(decision(policy, name, memory, {key: namespace}))
                self.assertFalse(decision(policy, name, memory, {}))
                self.assertFalse(decision(policy, name, memory.replace("_dev-", "_prod-"), {key: "/shared/entry"}))
            write = "bedrock-agentcore:BatchCreateMemoryRecords"
            allowed = "/project/example-project/proposals/macos-agent/entry"
            self.assertTrue(decision(policy, write, memory, {key: [allowed]}))
            for namespaces in ([], [allowed, "/shared/entry"], [allowed.replace("macos-agent", "alice")], [allowed.replace("example-project", "foreign")]):
                self.assertFalse(decision(policy, write, memory, {key: namespaces}))
            self.assertFalse(decision(policy, write, memory, {}))
            for action in ("BatchUpdateMemoryRecords", "BatchDeleteMemoryRecords"):
                self.assertFalse(decision(policy, "bedrock-agentcore:" + action, memory, {key: [allowed]}))
            context = {"s3:ResourceAccount": ACCOUNT}
            for prefix in ("projects/example-project/", "users/macos-agent/"):
                self.assertTrue(decision(policy, "s3:ListBucket", bucket, dict(context, **{"s3:prefix": prefix})))
            for prefix in ("", "users/alice/", "actor/", "project/"):
                self.assertFalse(decision(policy, "s3:ListBucket", bucket, dict(context, **{"s3:prefix": prefix})))
            self.assertTrue(decision(policy, "s3:GetObject", bucket + "/projects/example-project/file", context))
            self.assertFalse(decision(policy, "s3:GetObject", bucket + "/users/macos-agent/file", context))
            for action in ("s3:PutObject", "s3:AbortMultipartUpload"):
                self.assertTrue(decision(policy, action, bucket + "/users/macos-agent/file", context))
                for suffix in ("/projects/example-project/file", "/users/alice/file", "/users/macos-agent-other/file"):
                    self.assertFalse(decision(policy, action, bucket + suffix, context))
            self.assertFalse(decision(policy, "s3:DeleteObject", bucket + "/users/macos-agent/file", context))
            self.assertFalse(decision(policy, "s3:GetObject", bucket.replace("-dev", "-prod") + "/projects/p/file", context))
            self.assertFalse(decision(policy, "s3:GetObject", bucket + "/projects/p/file", {"s3:ResourceAccount": "1" * 12}))

    def test_role_boundary_guards_disabled_mode_and_pass_role(self):
        role = f"arn:aws:iam::{ACCOUNT}:role/fixture-kit-macos-role"
        for enabled in (False, True):
            policy = self.operator_statements(enabled)
            for action in ("iam:CreateRole", "iam:PutRolePermissionsBoundary", "iam:AttachRolePolicy", "iam:PutRolePolicy"):
                self.assertEqual(decision(policy, action, role, {"iam:PermissionsBoundary": PARAMS["MacosBoundary"]}), enabled)
                for boundary in (None, PARAMS["Boundary"], PARAMS["MacosBoundary"] + "-other"):
                    context = {} if boundary is None else {"iam:PermissionsBoundary": boundary}
                    self.assertFalse(decision(policy, action, role, context))
            self.assertFalse(decision(policy, "iam:DeleteRolePermissionsBoundary", role))
            self.assertEqual(decision(policy, "iam:PassRole", role, {"iam:PassedToService": "ec2.amazonaws.com"}), enabled)
            self.assertFalse(decision(policy, "iam:PassRole", role, {"iam:PassedToService": "lambda.amazonaws.com"}))
            self.assertFalse(decision(policy, "iam:CreatePolicyVersion", PARAMS["MacosBoundary"]))
            self.assertFalse(decision(policy, "iam:CreatePolicy", PARAMS["MacosBoundary"]))
            self.assertFalse(decision(policy, "cloudformation:CreateStack", f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:stack/bootstrap-fixture-kit-memory-dev/id"))
            for action in ("ec2:AllocateHosts", "ec2:ReleaseHosts", "ec2:ModifyHosts", "ec2:DescribeHosts"):
                self.assertTrue(decision(policy, action, "*", {"aws:RequestedRegion": "us-east-1"}))
                self.assertFalse(decision(policy, action, "*", {"aws:RequestedRegion": "eu-west-1"}))
            self.assertTrue(decision(policy, "iam:CreateRole", role.replace("macos-role", "host"), {"iam:PermissionsBoundary": PARAMS["Boundary"]}))

    def test_bootstrap_stack_read_contract(self):
        resource = self.admin["Resources"]["MacosProvisionerPolicy"]
        self.assertEqual(resource["Condition"], "HasMacos")
        self.assertEqual(resource["Properties"]["Users"], [{"Ref": "Provisioner"}])
        grants = resource["Properties"]["PolicyDocument"]["Statement"]
        self.assertEqual([s for s in grants if s["Sid"] == "MacosBootstrapStackRead"], [{
            "Sid": "MacosBootstrapStackRead", "Effect": "Allow",
            "Action": "cloudformation:DescribeStacks",
            "Resource": [
                {"Fn::Sub": "arn:${AWS::Partition}:cloudformation:*:${AWS::AccountId}:stack/bootstrap-${Project}/*"},
                {"Fn::Sub": "arn:${AWS::Partition}:cloudformation:*:${AWS::AccountId}:stack/bootstrap-${Project}-memory-*/*"},
            ],
        }])

    def test_bootstrap_stack_read_is_conditional_and_narrow(self):
        prefix = f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:stack/"
        targets = [prefix + "bootstrap-fixture-kit/id"] + [
            prefix + "bootstrap-fixture-kit-memory-" + stage + "/id" for stage in ("dev", "test", "prod")]
        unrelated = [prefix + name + "/id" for name in (
            "bootstrap-foreign", "bootstrap-foreign-memory-dev", "bootstrap-fixture-kit-other",
            "bootstrap-fixture-kit-memory", "bootstrap-fixture-kit-memoryless-dev", "unrelated")]
        unrelated += [target.replace(ACCOUNT, "1" * 12) for target in targets]
        unrelated += [target.replace("arn:aws:", "arn:aws-us-gov:") for target in targets]
        readonly = {"cloudformation:DescribeStacks", "cloudformation:ValidateTemplate",
                    "cloudformation:GetTemplateSummary", "cloudformation:ListStacks"}
        for enabled in (False, True):
            policy = self.operator_statements(enabled)
            for target in targets + unrelated + ["*"]:
                with self.subTest(enabled=enabled, target=target):
                    self.assertEqual(decision(policy, "cloudformation:DescribeStacks", target), enabled and target in targets)
                    for action in ("CreateStack", "UpdateStack", "DeleteStack", "CreateChangeSet", "ExecuteChangeSet",
                                   "DeleteChangeSet", "SetStackPolicy", "UpdateTerminationProtection",
                                   "ContinueUpdateRollback", "CancelUpdateStack", "RollbackStack", "TagResource",
                                   "UntagResource", "GetTemplate", "DescribeStackResources", "ListStackResources"):
                        self.assertFalse(decision(policy, "cloudformation:" + action, target), action)
                    # Also reject wildcard and future write actions, not only the named probes.
                    for statement in policy:
                        if statement["Effect"] != "Allow" or not any(
                                fnmatch.fnmatchcase(target, r) for r in values(statement.get("Resource", "*"))):
                            continue
                        for action in values(statement["Action"]):
                            service, separator, _ = action.lower().partition(":")
                            if not separator or fnmatch.fnmatchcase("cloudformation", service):
                                self.assertIn(action, readonly)
            self.assertTrue(decision(policy, "cloudformation:UpdateStack", prefix + "fixture-kit/id"))
            self.assertTrue(decision(policy, "cloudformation:UpdateStack", prefix + "fixture-kit-host/id"))

    def test_conditional_session_restriction_keeps_homelab_contract(self):
        instance = f"arn:aws:ec2:us-east-1:{ACCOUNT}:instance/i-fixture"
        custom = f"arn:aws:ssm:us-east-1:{ACCOUNT}:document/fixture-kit-macos-session"
        default = f"arn:aws:ssm:us-east-1:{ACCOUNT}:document/SSM-SessionManagerRunShell"
        recovery = "arn:aws:ssm:us-east-1::document/AWS-RunShellScript"
        context = {"aws:RequestedRegion": "us-east-1", "ssm:resourceTag/Variant": "macos"}
        for enabled in (False, True):
            policy = self.operator_statements(enabled)
            self.assertEqual(decision(policy, "ssm:StartSession", instance, context), not enabled)
            # Only enabled Mac mode changes the legacy direct-session contract.
            for document in (default, "arn:aws:ssm:us-east-1::document/AWS-StartSSHSession"):
                self.assertEqual(all(decision(policy, "ssm:StartSession", r, context) for r in (instance, document)), not enabled)
            self.assertFalse(all(decision(policy, "ssm:StartSession", r, context) for r in (instance, custom)))
            self.assertTrue(all(decision(policy, "ssm:SendCommand", r, context) for r in (instance, recovery)))
            homelab = dict(context, **{"ssm:resourceTag/Variant": "homelab"})
            self.assertTrue(all(decision(policy, "ssm:StartSession", r, homelab) for r in (instance, default)))
            for action in ("ssm:CreateDocument", "ssm:UpdateDocument", "ssm:DeleteDocument", "ssm:UpdateDocumentDefaultVersion"):
                self.assertEqual(decision(policy, action, custom, context), enabled)
                self.assertFalse(decision(policy, action, default, context))
                self.assertFalse(decision(policy, action, custom + "-foreign", context))
                self.assertFalse(decision(policy, action, custom, dict(context, **{"aws:RequestedRegion": "eu-west-1"})))

    def test_session_role_trust_ceiling_and_immutable_ownership(self):
        resources = self.admin["Resources"]
        role = resources["MacosSessionRole"]
        self.assertEqual(role["Condition"], "HasMacos")
        properties = role["Properties"]
        self.assertEqual(properties["RoleName"], {"Fn::Sub": "${Project}-macos-session"})
        self.assertEqual(properties["PermissionsBoundary"], {"Ref": "MacosSessionBoundary"})
        self.assertEqual(properties["ManagedPolicyArns"], [{"Ref": "MacosSessionBoundary"}])
        self.assertNotIn("Policies", properties)
        self.assertEqual(properties["MaxSessionDuration"], 3600)
        self.assertEqual(properties["AssumeRolePolicyDocument"]["Statement"], [
            {"Effect": "Allow", "Principal": {"AWS": {"Fn::GetAtt": "Provisioner.Arn"}}, "Action": "sts:AssumeRole"}])
        self.assertNotIn("SSMSessionRunAs", json.dumps(properties))
        self.assertEqual(self.admin["Outputs"]["MacosSessionRoleArn"], {
            "Condition": "HasMacos", "Description": "Assume this role for the approved Mac shell, SSH, and local-port session documents.",
            "Value": {"Fn::GetAtt": "MacosSessionRole.Arn"}})
        for name in ("MacosSessionRole", "MacosSessionBoundary", "MacosProvisionerDeny"):
            self.assertFalse(resolve(self.admin["Conditions"][resources[name]["Condition"]], dict(PARAMS, EnableMacos="false")))
        deny = resources["MacosProvisionerDeny"]["Properties"]
        self.assertEqual(deny["ManagedPolicyName"], {"Fn::Sub": "${Project}-macos-provisioner-deny"})
        self.assertTrue(all(s["Effect"] == "Deny" for s in deny["PolicyDocument"]["Statement"]))
        arn = f"arn:aws:iam::{ACCOUNT}:role/fixture-kit-macos-session"
        for enabled in (False, True):
            policy = self.operator_statements(enabled)
            for action in ("iam:CreateRole", "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy",
                           "iam:PutRolePermissionsBoundary", "iam:DeleteRolePermissionsBoundary", "iam:PutRolePolicy",
                           "iam:AttachRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:PassRole"):
                self.assertFalse(decision(policy, action, arn, {"iam:PermissionsBoundary": PARAMS["Boundary"],
                                                               "iam:PassedToService": "ec2.amazonaws.com"}))
            for action in ("iam:CreatePolicy", "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion", "iam:DeletePolicy"):
                self.assertFalse(decision(policy, action, f"arn:aws:iam::{ACCOUNT}:policy/fixture-kit-macos-session-boundary"))

    def test_session_role_three_documents_and_own_sessions_only(self):
        policy = resolve(self.admin["Resources"]["MacosSessionBoundary"]["Properties"]["PolicyDocument"])["Statement"]
        actual = {action for statement in policy for action in values(statement["Action"])}
        self.assertEqual(actual, {"ssm:StartSession", "ssm:ResumeSession", "ssm:TerminateSession", "ssmmessages:OpenDataChannel"})
        context = {"aws:RequestedRegion": "us-east-1", "ssm:resourceTag/Variant": "macos", "ssm:resourceTag/Project": "fixture-kit"}
        instance = f"arn:aws:ec2:us-east-1:{ACCOUNT}:instance/i-fixture"
        self.assertTrue(decision(policy, "ssm:StartSession", instance, context))
        target = next(s for s in policy if s["Sid"] == "MacosSessionTarget")
        self.assertEqual(target["Condition"]["BoolIfExists"], {"ssm:SessionDocumentAccessCheck": "true"})
        self.assertFalse(decision(policy, "ssm:StartSession", instance,
                                  dict(context, **{"ssm:SessionDocumentAccessCheck": "false"})))
        for key, bad in (("ssm:resourceTag/Variant", "homelab"), ("ssm:resourceTag/Project", "foreign"), ("aws:RequestedRegion", "eu-west-1")):
            self.assertFalse(decision(policy, "ssm:StartSession", instance, dict(context, **{key: bad})))
            missing = dict(context)
            missing.pop(key)
            self.assertFalse(decision(policy, "ssm:StartSession", instance, missing))
        self.assertFalse(decision(policy, "ssm:StartSession", instance.replace(ACCOUNT, "1" * 12), context))
        documents = [f"arn:aws:ssm:us-east-1:{ACCOUNT}:document/fixture-kit-macos-session",
                     "arn:aws:ssm:us-east-1::document/AWS-StartPortForwardingSession",
                     "arn:aws:ssm:us-east-1::document/AWS-StartSSHSession"]
        grant = next(s for s in policy if s["Sid"] == "MacosSessionDocuments")
        self.assertEqual(grant["Condition"]["Bool"], {"ssm:SessionDocumentAccessCheck": "true"})
        self.assertEqual(grant["Resource"], [d.replace("us-east-1", "*") for d in documents])
        for document in documents:
            self.assertTrue(decision(policy, "ssm:StartSession", document,
                                     dict(context, **{"ssm:SessionDocumentAccessCheck": "true"})))
            self.assertFalse(decision(policy, "ssm:StartSession", document, context))
            self.assertFalse(decision(policy, "ssm:StartSession", document,
                                      dict(context, **{"ssm:SessionDocumentAccessCheck": "false"})))
        for document in (documents[0] + "-foreign", "arn:aws:ssm:us-east-1::document/AWS-StartPortForwardingSessionToRemoteHost",
                         f"arn:aws:ssm:us-east-1:{ACCOUNT}:document/SSM-SessionManagerRunShell"):
            self.assertFalse(decision(policy, "ssm:StartSession", document, dict(context, **{"ssm:SessionDocumentAccessCheck": "true"})))
        own = next(s for s in policy if s["Sid"] == "MacosOwnSession")
        self.assertEqual(own["Condition"]["StringEquals"]["ssm:resourceTag/aws:ssmmessages:session-id"], "${aws:userid}")
        caller = "fixture-role-id:fixture-session"
        # IAM substitutes the role session's userid in policy variables, not its user name.
        policy = json.loads(json.dumps(policy).replace("${aws:userid}", caller))
        session = f"arn:aws:ssm:us-east-1:{ACCOUNT}:session/fixture-session-abc"
        context = {"aws:RequestedRegion": "us-east-1", "ssm:resourceTag/aws:ssmmessages:session-id": caller}
        for action in ("ssm:ResumeSession", "ssm:TerminateSession"):
            self.assertTrue(decision(policy, action, session, context))
            self.assertFalse(decision(policy, action, session, {"aws:RequestedRegion": "us-east-1"}))
            self.assertFalse(decision(policy, action, session, dict(context, **{"ssm:resourceTag/aws:ssmmessages:session-id": caller + "-other"})))
        self.assertTrue(decision(policy, "ssmmessages:OpenDataChannel", session.replace("fixture-session-abc", caller + "-abc"), context))
        self.assertFalse(decision(policy, "ssmmessages:OpenDataChannel", session, context))
        for action in ("ssm:SendCommand", "ec2:StopInstances", "sts:AssumeRole", "iam:PassRole", "s3:GetObject"):
            self.assertFalse(decision(policy, action, "*", context))

    def test_optional_scope_rules(self):
        rules = self.admin["Rules"]["MacosScopeRequired"]
        self.assertFalse(resolve(rules["RuleCondition"], dict(PARAMS, EnableMacos="false")))
        self.assertTrue(resolve(rules["RuleCondition"]))
        for name in ("MacosMemoryArn", "MacosMemoryBucketName", "MacosAgentActorId", "MacosProposalProject"):
            invalid = dict(PARAMS, **{name: ""})
            self.assertFalse(all(resolve(item["Assert"], invalid) for item in rules["Assertions"]))
            self.assertEqual(self.admin["Parameters"][name]["Default"], "")
            for value in ("*", "a/*", "wrong\nvalue"):
                self.assertIsNone(re.fullmatch(self.admin["Parameters"][name]["AllowedPattern"], value))

    def test_host_policy_renderer_equivalence(self):
        path = MEMORY_RENDERER
        if path is None:
            candidate = ROOT / "scripts/aws-memory.py"
            if "def host_policy(" not in candidate.read_text():
                self.skipTest("host-policy renderer is not merged; pass --memory-renderer for an external read-only comparison")
            path = candidate
        spec = importlib.util.spec_from_file_location("memory_compare", path)
        memory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(memory)
        config = memory.load_config("dev")
        config["hostRoles"] = [{"roleSuffix": "macos-role", "actorId": PARAMS["AgentActorId"],
                                "proposalProject": PARAMS["ProposalProject"]}]
        expected = resolve(memory.host_policy(config, "macos-role"))["Statement"]
        actual = [s for s in self.policy if s["Sid"] in ("MemoryRead", "MemoryWrite", "BucketList", "BucketRead", "BucketWrite")]
        def normalize(statements, strip_account=False):
            normalized = []
            for statement in copy.deepcopy(statements):
                statement.pop("Sid", None)
                statement["Action"] = sorted(values(statement["Action"]))
                if strip_account and statement["Action"][0].startswith("s3:"):
                    condition = statement.get("Condition", {})
                    self.assertEqual(condition.pop("StringEquals"), {"s3:ResourceAccount": ACCOUNT})
                    if not condition:
                        statement.pop("Condition", None)
                normalized.append(statement)
            return normalized
        # The host fragment has no account condition; our additional check only narrows S3.
        self.assertEqual(normalize(actual, strip_account=True), normalize(expected))

    def test_deploy_inputs_no_call_dry_run_and_refusals(self):
        with tempfile.TemporaryDirectory(prefix="macos-deploy-test-") as directory:
            root = Path(directory)
            stub = root / "aws"
            stub.write_text("#!/usr/bin/python3\nimport json, os, sys\nfrom pathlib import Path\n"
                            "Path(os.environ['CALLS']).write_text(json.dumps(sys.argv[1:]))\n"
                            "print('private-response-must-not-print', file=sys.stderr)\nraise SystemExit(3)\n")
            stub.chmod(0o755)
            calls = root / "calls"
            env = {"PATH": str(root) + ":/usr/bin:/bin", "HOME": str(root), "CALLS": str(calls),
                   "EC2_PROJECT": PARAMS["Project"], "EC2_AWS_ADMIN_PROFILE": "fixture-administrator"}
            script = str(ROOT / "scripts/aws-provisioner.sh")
            def run(extra=None, args=("--dry-run",)):
                result = subprocess.run([script, "deploy", *args], env=dict(env, **(extra or {})), capture_output=True, text=True)
                self.assertNotIn("private-response-must-not-print", result.stdout + result.stderr)
                for value in (PARAMS["MemoryArn"], PARAMS["MemoryBucketName"], "fixture-administrator"):
                    self.assertNotIn(value, result.stdout + result.stderr)
                return result
            self.assertEqual(run().returncode, 0)
            mac = {"EC2_ENABLE_MACOS": "true", "EC2_MACOS_MEMORY_ARN": PARAMS["MemoryArn"],
                   "EC2_MACOS_MEMORY_BUCKET": PARAMS["MemoryBucketName"], "EC2_MACOS_AGENT": PARAMS["AgentActorId"],
                   "EC2_MACOS_PROPOSAL_PROJECT": PARAMS["ProposalProject"]}
            self.assertEqual(run(mac).returncode, 0)
            self.assertFalse(calls.exists())
            self.assertEqual(set(root.iterdir()), {stub})
            for name in mac:
                self.assertNotEqual(run(dict(mac, **{name: "invalid/*"})).returncode, 0)
                if name != "EC2_ENABLE_MACOS":
                    self.assertNotEqual(run(dict(mac, **{name: ""})).returncode, 0)
            self.assertNotEqual(run(dict(mac, EC2_AWS_ADMIN_PROFILE="pi-memory-person-alice")).returncode, 0)
            self.assertNotEqual(run(dict(mac, EC2_AWS_ADMIN_PROFILE=PARAMS["Project"])).returncode, 0)
            self.assertNotEqual(run(dict(mac, EC2_MACOS_MEMORY_ARN=PARAMS["MemoryArn"].replace("us-east-1", "eu-west-1"))).returncode, 0)
            self.assertNotEqual(run(mac, ("--unknown",)).returncode, 0)
            self.assertFalse(calls.exists())
            self.assertNotEqual(run(mac, ()).returncode, 0)
            argv = json.loads(calls.read_text())
            self.assertIn("EnableMacos=true", argv)
            self.assertIn("MacosMemoryArn=" + PARAMS["MemoryArn"], argv)
            self.assertIn("MacosMemoryBucketName=" + PARAMS["MemoryBucketName"], argv)
            self.assertNotEqual(run(dict(mac, EC2_ENABLE_MACOS="false"), ()).returncode, 0)
            argv = json.loads(calls.read_text())
            self.assertIn("EnableMacos=false", argv)
            self.assertIn("MacosMemoryArn=", argv)
            self.assertIn("MacosMemoryBucketName=", argv)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-renderer", type=Path, help="Compare a reviewed host-policy renderer without changing it.")
    MEMORY_RENDERER = parser.parse_args().memory_renderer
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MacosInfraTests))
    if result.wasSuccessful():
        print("macos infra tests: pass (%d tests, %d skipped; offline fixtures only)" % (result.testsRun, len(result.skipped)))
    sys.exit(0 if result.wasSuccessful() else 1)
