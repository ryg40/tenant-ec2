#!/usr/bin/env python3
"""Offline regression checks for operations resources, IAM limits and journal export."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class CfnLoader(yaml.SafeLoader):
    pass


def intrinsic(loader, tag, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {tag: value}


CfnLoader.add_multi_constructor("!", intrinsic)


def template(name):
    return yaml.load((ROOT / "scripts" / name).read_text(), Loader=CfnLoader)


def statements(document):
    return {item["Sid"]: item for item in document["Statement"]}


def actions(statement):
    value = statement["Action"]
    return set(value if isinstance(value, list) else [value])


sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("exporter", ROOT / "scripts/host-journal-export.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.host = template("aws-host.cfn.yaml")
        self.provisioner = template("aws-provisioner.cfn.yaml")

    def test_optional_resources_and_retention(self):
        resources = self.host["Resources"]
        for name in ("SnapshotPolicy", "LogGroup", "BillingAlarm", "AlarmTopic", "AlarmTopicPolicy", "AlarmSubscription"):
            self.assertEqual(resources[name]["Condition"], "HasOperations")
        self.assertEqual(resources["LogGroup"]["Properties"]["RetentionInDays"], 14)
        publish = resources["AlarmTopicPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]
        self.assertEqual(publish["Principal"], {"Service": "cloudwatch.amazonaws.com"})
        self.assertEqual(publish["Action"], "sns:Publish")
        self.assertEqual(publish["Resource"], {"Ref": "AlarmTopic"})
        self.assertEqual(publish["Condition"]["ArnEquals"]["aws:SourceArn"], {"Sub": "arn:${AWS::Partition}:cloudwatch:${AWS::Region}:${AWS::AccountId}:alarm:${Project}-billing"})
        policy = resources["SnapshotPolicy"]["Properties"]
        self.assertEqual(policy["ExecutionRoleArn"], {"Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/bootstrap-${Project}-dlm"})
        detail = policy["PolicyDetails"]
        self.assertEqual(detail["ResourceTypes"], ["VOLUME"])
        self.assertEqual(detail["TargetTags"], [{"Key": "Backup", "Value": {"Ref": "Project"}}])
        schedule = detail["Schedules"][0]
        self.assertEqual(schedule["CreateRule"], {"Interval": 24, "IntervalUnit": "HOURS", "Times": ["03:00"]})
        self.assertEqual(schedule["RetainRule"], {"Count": 7})
        self.assertEqual(schedule["TagsToAdd"], [{"Key": name, "Value": {"Ref": "Project"}} for name in ("Project", "Backup")])
        self.assertIn("EC2_ENABLE_OPERATIONS=${EnableOperations}", json.dumps(resources["Instance"]))
        self.assertIn("OperationsLogGroup", json.dumps(resources["Instance"]["Properties"]["Tags"]))

    def test_host_boundary_changes_only_add_s3(self):
        current = statements(self.provisioner["Resources"]["Boundary"]["Properties"]["PolicyDocument"])
        expected = {"ProjectBucketList", "ProjectBucketObjects"}
        original = self.provisioner["Resources"]["Boundary"]["Properties"]["PolicyDocument"].copy()
        original["Statement"] = [item for item in original["Statement"] if item["Sid"] not in expected]
        digest = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, "634474cc869a294d1bdfe99c729051e8c4348c68f13a71c20a845b60e96c9052")
        self.assertEqual(set(current) - expected, {"SsmAgent", "EfsClient", "StackSecretsRead", "Bedrock", "HostRead", "Logs"})
        self.assertEqual(actions(current["ProjectBucketList"]), {"s3:ListBucket"})
        self.assertEqual(current["ProjectBucketList"]["Resource"], {"Sub": "arn:${AWS::Partition}:s3:::${Project}-*"})
        self.assertEqual(actions(current["ProjectBucketObjects"]), {"s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"})
        self.assertEqual(current["ProjectBucketObjects"]["Resource"], {"Sub": "arn:${AWS::Partition}:s3:::${Project}-*/*"})
        for name in expected:
            self.assertEqual(current[name]["Condition"]["StringEquals"]["s3:ResourceAccount"], {"Ref": "AWS::AccountId"})
        self.assertFalse(any(action in {"ec2:CreateSnapshot", "ec2:DeleteSnapshot", "ec2:CreateTags"} or action.startswith("events:") for item in current.values() for action in actions(item)))

    def test_snapshot_role_limits(self):
        resources = self.provisioner["Resources"]
        role = resources["SnapshotRole"]["Properties"]
        self.assertEqual(role["RoleName"], {"Sub": "bootstrap-${Project}-dlm"})
        self.assertEqual(role["PermissionsBoundary"], {"Ref": "SnapshotBoundary"})
        self.assertEqual(role["ManagedPolicyArns"], [{"Ref": "SnapshotBoundary"}])
        trust = role["AssumeRolePolicyDocument"]["Statement"][0]
        self.assertEqual(trust["Principal"], {"Service": "dlm.amazonaws.com"})
        self.assertEqual(trust["Condition"]["StringEquals"]["aws:SourceAccount"], {"Ref": "AWS::AccountId"})
        grants = statements(resources["SnapshotBoundary"]["Properties"]["PolicyDocument"])
        for name in ("SnapshotSource", "SnapshotCreate", "SnapshotTags", "SnapshotDelete"):
            grant = grants[name]
            self.assertNotEqual(grant["Resource"], "*")
            self.assertEqual(grant["Condition"]["StringEquals"]["aws:RequestedRegion"], {"Ref": "Regions"})
        source = grants["SnapshotSource"]["Condition"]["StringEquals"]
        self.assertEqual(source["ec2:ResourceTag/Project"], {"Ref": "Project"})
        self.assertEqual(source["ec2:ResourceTag/Backup"], {"Ref": "Project"})
        self.assertEqual(grants["SnapshotTags"]["Condition"]["StringEquals"]["ec2:CreateAction"], "CreateSnapshot")
        self.assertEqual(grants["SnapshotDelete"]["Condition"]["Null"], {"ec2:ResourceTag/aws:dlm:lifecycle-policy-id": "false"})
        self.assertEqual(grants["SnapshotDelete"]["Condition"]["StringEquals"]["ec2:Owner"], {"Ref": "AWS::AccountId"})
        self.assertEqual(actions(grants["SnapshotDiscovery"]), {"ec2:DescribeVolumes", "ec2:DescribeSnapshots"})
        self.assertIn("rule/AwsDataLifecycleRule.managed-cwe.", grants["SnapshotSchedule"]["Resource"]["Sub"])

    def test_provisioner_additions_are_scoped(self):
        resources = self.provisioner["Resources"]
        original = resources["ProvisionerPolicy"]["Properties"]["PolicyDocument"]
        digest = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, "a2bb9a7986a16a578749e7777ca1d4d9303152bf3e7768a3fdbf186895c70b79")
        grants = statements(resources["OperationsPolicy"]["Properties"]["PolicyDocument"])
        for name, grant in grants.items():
            self.assertFalse(any("*" in action for action in actions(grant)), name)
            if grant["Resource"] == "*":
                self.assertIn(name, ("DlmCreate", "LogGroupDiscovery"))
        for name in ("BucketCreate", "BucketConfiguration"):
            self.assertEqual(grants[name]["Resource"], {"Sub": "arn:${AWS::Partition}:s3:::${Project}-*"})
            self.assertFalse(any("Object" in action for action in actions(grants[name])))
        self.assertIn("s3:PutLifecycleConfiguration", actions(grants["BucketConfiguration"]))
        self.assertEqual(grants["PassSnapshotRole"]["Resource"], {"GetAtt": "SnapshotRole.Arn"})
        self.assertEqual(grants["PassSnapshotRole"]["Condition"], {"StringEquals": {"iam:PassedToService": "dlm.amazonaws.com"}})
        self.assertEqual(grants["DlmCreate"]["Condition"]["StringEquals"]["aws:RequestTag/Project"], {"Ref": "Project"})
        self.assertEqual(grants["DlmPolicy"]["Condition"]["StringEquals"]["aws:ResourceTag/Project"], {"Ref": "Project"})
        self.assertEqual(grants["DlmUntag"]["Condition"]["ForAllValues:StringNotEquals"], {"aws:TagKeys": ["Project"]})
        self.assertTrue(grants["BillingAlarm"]["Resource"]["Sub"].endswith(":alarm:${Project}-billing"))
        self.assertTrue(grants["BillingTopic"]["Resource"]["Sub"].endswith(":${Project}-alarm"))
        self.assertNotIn("sns:Publish", actions(grants["BillingTopic"]))
        # IAM counts policy bytes without whitespace. Check the five managed-policy limit too.
        for resource in resources.values():
            if resource["Type"] == "AWS::IAM::ManagedPolicy":
                size = len(json.dumps(resource["Properties"]["PolicyDocument"], separators=(",", ":")))
                self.assertLess(size, 6144)
        self.assertEqual(len(resources["Provisioner"]["Properties"]["ManagedPolicyArns"]), 2)

    def test_export_restart_cursor_routing_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state, logs = base / "state", base / "logs"
            stub = base / "journalctl"
            stub.write_text("#!/usr/bin/env python3\n" + """import json, pathlib, sys
cursor = pathlib.Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--cursor-file=')))
if cursor.exists():
    assert cursor.read_text() == 'first'
    assert '--boot=0' not in sys.argv
    print(json.dumps({'MESSAGE': 'next system record'}))
    cursor.write_text('second')
else:
    assert '--boot=0' in sys.argv
    print(json.dumps({'MESSAGE': 'system record'}))
    print(json.dumps({'MESSAGE': 'container record', 'CONTAINER_ID_FULL': 'fixture'}))
    cursor.write_text('first')
""")
            stub.chmod(0o755)
            exporter.export(state, logs, str(stub))
            self.assertEqual((state / "cursor").read_text(), "first")
            exporter.export(state, logs, str(stub))
            self.assertEqual((state / "cursor").read_text(), "second")
            self.assertEqual(len((logs / "journal.log").read_text().splitlines()), 2)
            self.assertEqual(len((logs / "docker.log").read_text().splitlines()), 1)
            before = (logs / "journal.log").read_bytes()
            stub.write_text("#!/bin/sh\nexit 1\n")
            with self.assertRaises(subprocess.CalledProcessError):
                exporter.export(state, logs, str(stub))
            self.assertEqual((state / "cursor").read_text(), "second")
            self.assertEqual((logs / "journal.log").read_bytes(), before)
            # A disk failure after journalctl succeeds must leave the saved cursor intact.
            stub.write_text("#!/usr/bin/env python3\n" + """import json, pathlib, sys
cursor = pathlib.Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--cursor-file=')))
print(json.dumps({'MESSAGE': 'disk failure record'}))
cursor.write_text('third')
""")
            rotate = exporter.rotate
            def fail_rotate(path):
                raise OSError("fixture disk failure")
            exporter.rotate = fail_rotate
            try:
                with self.assertRaises(OSError):
                    exporter.export(state, logs, str(stub))
            finally:
                exporter.rotate = rotate
            self.assertEqual((state / "cursor").read_text(), "second")

    def test_bootstrap_operations_enable_disable_and_repeat(self):
        source = (ROOT / "scripts/host-bootstrap.sh").read_text()
        start = source.index("operations_logs() {")
        end = source.index("\n# work_dir:", start)
        function = source[start:end]
        self.assertIn("\noperations_logs\nkits\n", source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            calls = root / "calls"
            state = root / "unit-state"
            state.mkdir()
            stub = bin_dir / "systemctl"
            stub.write_text("""#!/bin/sh
set -eu
printf '%s\\n' "systemctl $*" >>"$CALLS"
for unit do :; done
case $1 in
    show)
        if [ "${MISSING_AGENT:-0}" = 1 ]; then echo not-found; else echo loaded; fi ;;
    is-active) test -f "$STATE/$unit.active" ;;
    enable)
        touch "$STATE/$unit.enabled"
        [ "${2:-}" != --now ] || touch "$STATE/$unit.active" ;;
    disable)
        if [ "$unit" = amazon-cloudwatch-agent ]; then
            [ "${MISSING_AGENT:-0}" != 1 ] || exit 5
            [ "${FAIL_DISABLE_AGENT:-0}" != 1 ] || exit 1
        fi
        rm -f "$STATE/$unit.enabled" "$STATE/$unit.active" ;;
    start) touch "$STATE/$unit.active" ;;
    stop) rm -f "$STATE/$unit.active" ;;
esac
""")
            stub.chmod(0o755)
            stub = bin_dir / "dnf"
            stub.write_text("#!/bin/sh\nprintf '%s\\n' \"dnf $*\" >>\"$CALLS\"\n")
            stub.chmod(0o755)
            agent = root / "opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl"
            agent.parent.mkdir(parents=True)
            agent.write_text("#!/bin/sh\nprintf '%s\\n' \"agent $*\" >>\"$CALLS\"\ntouch \"$STATE/amazon-cloudwatch-agent.active\"\n")
            agent.chmod(0o755)
            script = root / "check.sh"
            script.write_text("""set -eu
say() { :; }
skip() { :; }
die() { echo "$*" >&2; exit 1; }
run() { "$@"; }
same_text() { [ -f "$2" ] && [ "$(cat "$2")" = "$1" ]; }
root=$1
repo=$2
project=fixture-ops
region=us-east-1
log_group=/fixture-ops/host
dry_run=0
operations=true
""" + function + """
operations_logs
operations_logs
operations=false
operations_logs
operations_logs
""")
            environment = {"PATH": str(bin_dir) + ":" + os.environ["PATH"], "CALLS": str(calls), "STATE": str(state)}
            subprocess.run(["sh", str(script), str(root), str(ROOT)], env=environment, check=True, capture_output=True)
            lines = calls.read_text().splitlines()
            self.assertEqual(sum(line.startswith("agent ") for line in lines), 1)
            self.assertLess(lines.index("systemctl start fixture-ops-journal-export.service"), next(i for i, line in enumerate(lines) if line.startswith("agent ")))
            self.assertIn("systemctl disable --now fixture-ops-journal-export.timer", lines)
            self.assertLess(lines.index("systemctl enable amazon-cloudwatch-agent"), lines.index("systemctl disable --now amazon-cloudwatch-agent"))
            self.assertEqual(lines.count("systemctl disable --now amazon-cloudwatch-agent"), 2)
            for unit in ("amazon-cloudwatch-agent", "fixture-ops-journal-export.timer"):
                self.assertFalse((state / (unit + ".enabled")).exists())
                self.assertFalse((state / (unit + ".active")).exists())
            self.assertFalse(any(line.startswith("dnf ") for line in lines))

            # A removed agent package must not make operations-off fail.
            script.write_text(script.read_text().replace("operations=true", "operations=false"))
            calls.write_text("")
            subprocess.run(["sh", str(script), str(root), str(ROOT)], env=dict(environment, MISSING_AGENT="1"), check=True, capture_output=True)
            lines = calls.read_text().splitlines()
            self.assertIn("systemctl disable --now fixture-ops-journal-export.timer", lines)
            self.assertFalse(any(line.startswith("systemctl disable --now amazon-cloudwatch-agent") for line in lines))
            self.assertFalse(any(line.startswith("agent ") for line in lines))

            # Only a missing unit is skipped; an actual disable failure stays fatal.
            result = subprocess.run(["sh", str(script), str(root), str(ROOT)], env=dict(environment, FAIL_DISABLE_AGENT="1"), capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_rotation_and_agent_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter.configure(root, "fixture-ops", "us-east-1", "/fixture-ops/host")
            config = json.loads((root / "etc/fixture-ops/cloudwatch-agent.json").read_text())
            files = config["logs"]["logs_collected"]["files"]["collect_list"]
            self.assertEqual([item["file_path"] for item in files], ["/var/log/fixture-ops/docker.log", "/var/log/fixture-ops/journal.log"])
            self.assertTrue(all(item["log_group_name"] == "/fixture-ops/host" for item in files))
            self.assertTrue(all("retention_in_days" not in item for item in files))
            path = root / "var/log/fixture-ops/journal.log"
            for n in range(10):
                path.write_text(str(n) * 4)
                exporter.rotate(path, limit=4, keep=7)
            self.assertEqual(len(list(path.parent.glob("journal.log.*"))), 7)
            self.assertEqual(Path(str(path) + ".1").read_text(), "9999")
            self.assertEqual(Path(str(path) + ".7").read_text(), "3333")
            timer = (root / "etc/systemd/system/fixture-ops-journal-export.timer").read_text()
            self.assertIn("OnUnitInactiveSec=60", timer)
            exporter.configure(root, "fixture-ops", "us-east-1", "/fixture-ops/host")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(exporter.MAX_BYTES, 10 * 1024 * 1024)
            self.assertEqual(exporter.KEEP_FILES, 7)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OperationsTests))
    if result.wasSuccessful():
        print("operations tests: pass (%d tests)" % result.testsRun)
    sys.exit(0 if result.wasSuccessful() else 1)
