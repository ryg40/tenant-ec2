#!/usr/bin/env python3
"""Offline tests of the pinned image and the two-step instance replacement of the host.

The tests run scripts/aws-host.sh with a stub of the AWS CLI. The stub answers from a JSON
state file and keeps each call. No test makes an AWS call. Each id below is a fixture.
"""
import datetime
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "aws-host.sh"
IMAGE = "ami-0123456789abcdef0"
NEW_IMAGE = "ami-0fedcba9876543210"
PROOF = "replace: /srv/data is unmounted"
STILL = "replace: /srv/data is still unmounted"
NOT_MOUNTED = ("replace: /srv/data is not mounted at the start: an earlier run of step 1 unmounted it; "
               "no journal export")
STACK = "tenant-ec2-host"

STUB = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["STUB_STATE"]
with open(state_path) as handle:
    state = json.load(handle)
args = sys.argv[1:]
while args and args[0] in ("--profile", "--region"):
    args = args[2:]
with open(os.environ["STUB_CALLS"], "a") as handle:
    handle.write(json.dumps(args) + "\n")

def option(name, default=""):
    return args[args.index(name) + 1] if name in args else default

def save():
    with open(state_path, "w") as handle:
        json.dump(state, handle)

service, operation = args[0], args[1]
query = option("--query")
out = None
code = 0
if (service, operation) == ("cloudformation", "describe-stacks"):
    if "StackStatus" in query:
        out = state["stack_status"]
    elif "Parameters" in query:
        out = state["attach"]
    else:
        outputs = state["outputs_after"] if state.get("executed") and state.get("outputs_after") else state["outputs"]
        out = "\n".join(key + "\t" + value for key, value in outputs.items())
elif (service, operation) == ("cloudformation", "list-change-sets"):
    out = "\t".join(state.get("sets", [])) or "None"
elif (service, operation) == ("cloudformation", "deploy"):
    queue = state["queue"]
    if not queue:
        sys.exit(98)
    state["count"] = state.get("count", 0) + 1
    name = "cs-%d" % state["count"]
    state.setdefault("sets", []).append(name)
    state.setdefault("changes", {})[name] = queue.pop(0)
    overrides = args[args.index("--parameter-overrides") + 1:]
    attach = [word.split("=", 1)[1] for word in overrides if word.startswith("AttachInstance=")]
    state.setdefault("set_attach", {})[name] = attach[0] if attach else "None"
    save()
elif (service, operation) == ("cloudformation", "describe-change-set"):
    name = option("--change-set-name")
    if name not in state.get("sets", []):
        sys.stderr.write("An error occurred (ChangeSetNotFound): ChangeSet [%s] does not exist\n" % name)
        sys.exit(254)
    if query == "Status":
        out = "CREATE_COMPLETE"
    elif query == "[StackName,Status,ExecutionStatus]":
        out = "\t".join([state.get("set_stack", {}).get(name, "tenant-ec2-host"), "CREATE_COMPLETE",
                         state.get("set_exec", {}).get(name, "AVAILABLE")])
    elif "Parameters" in query:
        out = state["set_attach"][name]
    elif "Details" in query:
        out = "\t".join(state.get("details", {}).get(name, ["UserData", "Tags"]))
    else:
        out = "\n".join("\t".join(row) for row in state["changes"][name])
elif (service, operation) in (("cloudformation", "delete-change-set"), ("cloudformation", "execute-change-set")):
    state["sets"].remove(option("--change-set-name"))
    if operation == "execute-change-set":
        state["executed"] = True
        if state.get("attach_after_execute") is not None:
            state["attach"] = state["attach_after_execute"]
    save()
elif (service, operation) == ("cloudformation", "wait"):
    code = state.get("update_rc", 0)
elif (service, operation) == ("cloudformation", "get-template"):
    out = state["template_body"]
elif (service, operation) == ("ssm", "send-command"):
    path = option("--parameters")[len("file://"):]
    with open(path) as handle:
        state["ssm_commands"] = json.load(handle)["commands"]
    save()
    out = "cmd-fixture"
elif (service, operation) == ("ssm", "get-command-invocation"):
    out = state["ssm_status"] if query == "Status" else state["ssm_output"]
elif (service, operation) == ("ssm", "get-parameter"):
    out = state["parameter_image"]
elif (service, operation) == ("ec2", "create-snapshot"):
    out = "snap-0fixture"
elif (service, operation) == ("ec2", "wait"):
    waits = state.get("snapshot_waits", [])
    code = waits.pop(0) if waits else 0
    save()
elif (service, operation) == ("ec2", "describe-snapshots"):
    if "--filters" in args:
        out = state.get("recent_snapshot", "None")
    else:
        out = state.get("snapshot_state", "pending")
elif (service, operation) == ("ec2", "describe-volumes"):
    out = state["volume_after"] if state.get("executed") else state["volume_before"]
    if "length(" in query:
        out = "0" if state.get("executed") else "1"
elif (service, operation) == ("ec2", "describe-addresses"):
    out = state["address_instance"]
elif (service, operation) == ("ec2", "describe-instances"):
    out = state["instance_image"]
else:
    sys.exit(99)
if out is not None:
    print(out)
sys.exit(code)
'''

REPLACE = ["Modify", "Instance", "AWS::EC2::Instance", "True"]
ATTACH_REPLACE = [["Modify", "DataVolumeAttachment", "AWS::EC2::VolumeAttachment", "True"],
                  ["Modify", "ElasticIpAssociation", "AWS::EC2::EIPAssociation", "True"]]
DETACH = [["Remove", "DataVolumeAttachment", "AWS::EC2::VolumeAttachment", "None"],
          ["Remove", "ElasticIpAssociation", "AWS::EC2::EIPAssociation", "None"]]
ATTACH_ADD = [["Add", "DataVolumeAttachment", "AWS::EC2::VolumeAttachment", "None"],
              ["Add", "ElasticIpAssociation", "AWS::EC2::EIPAssociation", "None"]]


class CfnLoader(yaml.SafeLoader):
    pass


CfnLoader.add_multi_constructor("!", lambda loader, tag, node: {tag: (
    loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
    else loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node))})


class HostReplaceTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="host-replace-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.dir)])
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "aws"
        stub.write_text(STUB)
        stub.chmod(0o755)
        (self.dir / "tmp").mkdir()
        key = self.dir / "key.pub"
        key.write_text("ssh-ed25519 AAAAfixture check\n")
        self.env_file = self.dir / "host.env"
        self.write_env(IMAGE)
        self.calls = self.dir / "calls"
        self.calls.write_text("")
        self.state_path = self.dir / "state.json"
        self.state({})

    def write_env(self, image):
        lines = {
            "EC2_AMI_ID": image,
            "EC2_VPC_CIDR": "203.0.113.0/24",
            "EC2_ADMIN_CIDR": "198.51.100.7/32",
            "EC2_SSH_PUBLIC_KEY_FILE": str(self.dir / "key.pub"),
            "EC2_SNAPSHOT_REPOSITORY": "https://git.check-fixture.test/owner/snapshot.git",
            "EC2_SNAPSHOT_TAG": "portable/fixture",
            "EC2_SNAPSHOT_COMMIT": "0" * 40,
            "EC2_DOMAIN": "ec2.check-fixture.test",
        }
        self.env_file.write_text("".join("%s=%s\n" % item for item in lines.items()))
        self.env_file.chmod(0o640)

    def state(self, extra):
        base = {
            "stack_status": "UPDATE_COMPLETE",
            "attach": "true",
            "outputs": {"InstanceId": "i-0old", "DataVolumeId": "vol-0data", "ElasticIp": "192.0.2.10"},
            "outputs_after": {"InstanceId": "i-0new", "DataVolumeId": "vol-0data", "ElasticIp": "192.0.2.10"},
            "queue": [],
            "template_body": "Parameters:\n  AttachInstance:\n    Type: String\n",
            "ssm_status": "Success",
            "ssm_output": "replace: Docker is stopped\n" + PROOF,
            "volume_before": "i-0old\tattached",
            "volume_after": "i-0new\tattached",
            "address_instance": "i-0new",
            "instance_image": IMAGE,
            "parameter_image": NEW_IMAGE,
        }
        base.update(extra)
        self.state_path.write_text(json.dumps(base))

    def run_script(self, *args):
        env = {"PATH": "%s:%s" % (self.dir / "bin", os.environ["PATH"]), "HOME": str(self.dir),
               "TMPDIR": str(self.dir / "tmp"), "STUB_STATE": str(self.state_path),
               "STUB_CALLS": str(self.calls)}
        result = subprocess.run(["sh", str(SCRIPT), "--env-file", str(self.env_file), *args],
                                env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(list((self.dir / "tmp").iterdir()), [], "a run leaves a temporary file")
        return result

    def call_list(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def operations(self):
        return [" ".join(call[:2]) for call in self.call_list()]

    def saved(self):
        return json.loads(self.state_path.read_text())

    # ----- the template and the parameters

    def test_template_pins_the_image(self):
        template = yaml.load((ROOT / "scripts/aws-host.cfn.yaml").read_text(), Loader=CfnLoader)
        parameters = template["Parameters"]
        self.assertNotIn("AmiParameter", parameters)
        self.assertEqual(parameters["AmiId"]["Type"], "AWS::EC2::Image::Id")
        self.assertNotIn("Default", parameters["AmiId"])
        self.assertEqual(template["Resources"]["Instance"]["Properties"]["ImageId"], {"Ref": "AmiId"})

    def test_template_conditions_the_two_attachments(self):
        template = yaml.load((ROOT / "scripts/aws-host.cfn.yaml").read_text(), Loader=CfnLoader)
        self.assertEqual(template["Parameters"]["AttachInstance"]["Default"], "true")
        self.assertEqual(template["Conditions"]["HasAttachments"],
                         {"Equals": [{"Ref": "AttachInstance"}, "true"]})
        resources = template["Resources"]
        for name in ("DataVolumeAttachment", "ElasticIpAssociation"):
            self.assertEqual(resources[name].get("Condition"), "HasAttachments", name)
        # No other resource and no output names the two attachments.
        text = json.dumps({key: value for key, value in resources.items()
                           if key not in ("DataVolumeAttachment", "ElasticIpAssociation")})
        text += json.dumps(template.get("Outputs", {}))
        self.assertNotIn("DataVolumeAttachment", text)
        self.assertNotIn("ElasticIpAssociation", text)
        for name in ("DataVolume", "ElasticIp"):
            self.assertNotIn("Condition", resources[name])

    # ----- the pinned image

    def test_empty_image_is_refused_before_any_call(self):
        self.write_env("")
        result = self.run_script("deploy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EC2_AMI_ID is empty", result.stderr)
        self.assertIn("scripts/aws-host.sh current-image --write", result.stderr)
        self.assertIn("scripts/aws-host.sh new-image --write", result.stderr)
        self.assertEqual(self.call_list(), [])

    def test_image_of_a_wrong_form_is_refused(self):
        self.write_env("ubuntu-latest")
        result = self.run_script("deploy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EC2_AMI_ID is not an image id", result.stderr)
        self.assertEqual(self.call_list(), [])

    def test_deploy_passes_the_pinned_image(self):
        self.state({"queue": [[["Modify", "Instance", "AWS::EC2::Instance", "False"]]]})
        result = self.run_script("deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        deploy = [call for call in self.call_list() if call[:2] == ["cloudformation", "deploy"]][0]
        self.assertIn("AmiId=" + IMAGE, deploy)
        self.assertIn("AttachInstance=true", deploy)
        self.assertFalse(any(word.startswith("AmiParameter=") for word in deploy))

    def test_current_image_writes_the_image_of_the_instance(self):
        self.write_env("")
        result = self.run_script("current-image", "--write")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EC2_AMI_ID=" + IMAGE, result.stdout)
        self.assertIn("EC2_AMI_ID=%s\n" % IMAGE, self.env_file.read_text())
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o640)
        self.assertEqual(self.operations(), ["cloudformation describe-stacks", "ec2 describe-instances"])

    def test_new_image_prints_and_names_the_replacement(self):
        result = self.run_script("new-image")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EC2_AMI_ID=" + NEW_IMAGE, result.stdout)
        self.assertIn("deploy --replace-instance --step 1", result.stdout)
        # No --write: the file keeps the old value.
        self.assertIn("EC2_AMI_ID=%s\n" % IMAGE, self.env_file.read_text())
        call = self.call_list()[0]
        self.assertEqual(call[:2], ["ssm", "get-parameter"])
        self.assertIn("/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64", call)

    def test_image_reply_of_a_wrong_form_is_refused(self):
        self.state({"parameter_image": "None"})
        result = self.run_script("new-image", "--write")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EC2_AMI_ID=%s\n" % IMAGE, self.env_file.read_text())

    # ----- the dry runs

    def test_dry_runs_make_no_call(self):
        step1 = ["AWS-RunShellScript", "host-journal-export.py", "systemctl stop docker.socket docker.service",
                 "umount /srv/data", "fuser -vm /srv/data", PROOF, "Key=Name,Value=tenant-ec2-data-before-replace",
                 "Key=Backup,Value=tenant-ec2", "Name=status,Values=completed", "get-template",
                 "--parameter-overrides AttachInstance=false"]
        step1_execute = ["[StackName,Status,ExecutionStatus]", "--change-set-name", STILL,
                         "execute-change-set", "length(Volumes[0].Attachments)"]
        step2_execute = ["[StackName,Status,ExecutionStatus]", "execute-change-set", "describe-volumes",
                         "describe-addresses"]
        forms = ["deploy --replace-instance --step 1 --prepare", "deploy --replace-instance --step 1 --execute",
                 "deploy --replace-instance --step 2 --prepare", "deploy --replace-instance --step 2 --execute"]
        cases = {
            ("deploy", "--replace-instance"): forms + step1 + step1_execute + step2_execute + ["AttachInstance=true"],
            ("deploy", "--replace-instance", "--step", "1"): forms[:2] + step1 + step1_execute + [
                "a real run stops here: a step needs --prepare or --execute"],
            ("deploy", "--replace-instance", "--step", "1", "--prepare"): forms[:1] + step1 + [
                "a real run stops here. It prints the name of the change set"],
            ("deploy", "--replace-instance", "--step", "1", "--execute", "cs-7"): step1_execute + [
                "--change-set-name cs-7"],
            ("deploy", "--replace-instance", "--step", "2"): forms[2:] + step2_execute + ["AttachInstance=true"],
            ("deploy", "--replace-instance", "--step", "2", "--prepare"): forms[2:3] + ["AttachInstance=true"],
            ("deploy", "--replace-instance", "--step", "2", "--execute", "cs-7"): step2_execute,
            ("current-image", "--write"): ["ec2 describe-instances", "set EC2_AMI_ID in"],
            ("new-image", "--write"): ["ssm get-parameter", "set EC2_AMI_ID in"],
        }
        for args, texts in cases.items():
            with self.subTest(args=args):
                result = self.run_script("--dry-run", *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                for text in texts:
                    self.assertIn(text, result.stdout)
                self.assertNotIn("a real run stops here: EC2_AMI_ID", result.stdout)
        prepare_only = self.run_script("--dry-run", "deploy", "--replace-instance", "--step", "1", "--prepare")
        self.assertNotIn("execute-change-set", prepare_only.stdout)
        execute_only = self.run_script("--dry-run", "deploy", "--replace-instance", "--step", "1", "--execute", "cs-7")
        self.assertNotIn("ec2 create-snapshot", execute_only.stdout)
        self.assertEqual(self.call_list(), [])
        self.assertIn("EC2_AMI_ID=%s\n" % IMAGE, self.env_file.read_text())

    def test_dry_run_with_an_empty_image_names_the_stop(self):
        self.write_env("")
        result = self.run_script("--dry-run", "deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a real run stops here: EC2_AMI_ID is empty", result.stdout)
        self.assertEqual(self.call_list(), [])

    # ----- the ordinary update and the options

    def test_plain_deploy_refuses_a_replacement(self):
        self.state({"queue": [[REPLACE] + ATTACH_REPLACE]})
        result = self.run_script("deploy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("it replaces the instance", result.stderr)
        self.assertIn("scripts/aws-host.sh deploy --replace-instance --step 1 --prepare", result.stderr)
        self.assertIn("cloudformation delete-change-set", self.operations())
        self.assertNotIn("cloudformation execute-change-set", self.operations())

    def test_plain_deploy_after_step1_names_step2(self):
        self.state({"attach": "false", "queue": [[REPLACE] + ATTACH_ADD]})
        result = self.run_script("deploy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scripts/aws-host.sh deploy --replace-instance --step 2 --prepare", result.stderr)
        self.assertNotIn("--step 1", result.stderr)
        self.assertNotIn("cloudformation execute-change-set", self.operations())

    def test_plain_deploy_runs_a_user_data_change(self):
        self.state({"queue": [[["Modify", "Instance", "AWS::EC2::Instance", "False"]]]})
        result = self.run_script("deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cloudformation execute-change-set", self.operations())

    def test_plain_deploy_runs_a_conditional_user_data_change(self):
        # CloudFormation marks a user data change as Conditional; it restarts, it does not replace.
        conditional = ["Modify", "Instance", "AWS::EC2::Instance", "Conditional"]
        attach = [["Modify", "DataVolumeAttachment", "AWS::EC2::VolumeAttachment", "Conditional"],
                  ["Modify", "ElasticIpAssociation", "AWS::EC2::EIPAssociation", "Conditional"]]
        self.state({"queue": [[conditional] + attach]})
        result = self.run_script("deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("restarts the instance", result.stdout)
        self.assertNotIn("replaces the instance", result.stdout)
        self.assertIn("cloudformation execute-change-set", self.operations())

    def test_plain_deploy_refuses_a_conditional_change_with_no_target(self):
        conditional = ["Modify", "Instance", "AWS::EC2::Instance", "Conditional"]
        self.state({"queue": [[conditional]], "details": {"cs-1": []}})
        result = self.run_script("deploy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("it replaces the instance (Replacement Conditional)", result.stderr)
        self.assertNotIn("cloudformation execute-change-set", self.operations())

    def test_plain_deploy_refuses_a_conditional_change_of_another_target(self):
        conditional = ["Modify", "Instance", "AWS::EC2::Instance", "Conditional"]
        self.state({"queue": [[conditional]], "details": {"cs-1": ["UserData", "SubnetId"]}})
        result = self.run_script("deploy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("it replaces the instance (Replacement Conditional)", result.stderr)
        self.assertNotIn("cloudformation execute-change-set", self.operations())

    def test_a_step_without_a_word_is_refused_before_any_call(self):
        for args in (("--replace-instance",), ("--replace-instance", "--step", "1"),
                     ("--replace-instance", "--step", "2")):
            with self.subTest(args=args):
                result = self.run_script("deploy", *args)
                self.assertNotEqual(result.returncode, 0)
                for text in ("--step 1 --prepare", "--step 1 --execute <name>", "--step 2"):
                    self.assertIn(text, result.stderr)
        self.assertEqual(self.call_list(), [])

    def test_the_options_of_the_words(self):
        cases = {
            ("deploy", "--step", "1"): "--step is an option of deploy --replace-instance",
            ("deploy", "--replace-instance", "--step", "3", "--prepare"): "--step needs 1 or 2",
            ("deploy", "--prepare"): "--prepare and --execute are options of deploy --replace-instance --step",
            ("deploy", "--replace-instance", "--prepare"): "are options of deploy --replace-instance --step",
            ("deploy", "--replace-instance", "--step", "1", "--prepare", "--execute", "cs-1"): "conflict",
            ("deploy", "--replace-instance", "--step", "1", "--execute",
             "arn:aws:cloudformation:us-east-1:fixture:changeSet/cs-1/x"): "not an ARN",
            ("deploy", "--replace-instance", "--step", "1", "--execute", "1cs"): "not an ARN",
            ("outputs", "--prepare"): "options of deploy --replace-instance --step",
        }
        for args, text in cases.items():
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(text, result.stderr)
        self.assertEqual(self.call_list(), [])

    # ----- step 1, prepare

    def step1_state(self, **extra):
        state = {"queue": [[REPLACE] + ATTACH_REPLACE, DETACH], "attach_after_execute": "false"}
        state.update(extra)
        self.state(state)

    def prepare1(self):
        return self.run_script("deploy", "--replace-instance", "--step", "1", "--prepare")

    def test_step1_prepare_runs_its_parts_in_order_and_stops(self):
        self.step1_state()
        result = self.prepare1()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("step 1 prepare: pass. The change set cs-2 waits", result.stdout)
        self.assertIn("deploy --replace-instance --step 1 --execute cs-2", result.stdout)
        self.assertIn("The site is down", result.stdout)
        ops = self.operations()
        self.assertNotIn("cloudformation execute-change-set", ops)
        self.assertNotIn("cloudformation wait", ops)
        order = [ops.index("ssm send-command"), ops.index("ec2 create-snapshot"), ops.index("ec2 wait"),
                 ops.index("cloudformation get-template"), len(ops) - 1 - ops[::-1].index("cloudformation deploy")]
        self.assertEqual(order, sorted(order))
        # Part 0: the change set of step 2 is made, shown and deleted before the stop.
        self.assertEqual(ops.count("cloudformation delete-change-set"), 1)
        self.assertLess(ops.index("cloudformation delete-change-set"), ops.index("ssm send-command"))
        saved = self.saved()
        self.assertEqual(saved["sets"], ["cs-2"])
        self.assertEqual(saved["set_attach"], {"cs-1": "true", "cs-2": "false"})
        deploys = [call for call in self.call_list() if call[:2] == ["cloudformation", "deploy"]]
        self.assertIn("AmiId=" + IMAGE, deploys[0])
        # Part c: the template of the stack, with one override only.
        step1 = deploys[1]
        self.assertEqual(step1[step1.index("--parameter-overrides") + 1:], ["AttachInstance=false"])
        self.assertNotEqual(step1[step1.index("--template-file") + 1], str(ROOT / "scripts/aws-host.cfn.yaml"))
        commands = "\n".join(saved["ssm_commands"])
        for text in ("host-journal-export.py", "/srv/data/journal-before-replace/",
                     "systemctl stop docker.socket docker.service", "umount /srv/data", PROOF):
            self.assertIn(text, commands)
        self.assertLess(commands.index("host-journal-export.py"), commands.index("systemctl stop"))
        self.assertLess(commands.index("systemctl stop"), commands.index("umount /srv/data"))
        # A failed umount names the processes that hold the mount.
        umount = [line for line in commands.splitlines() if "umount /srv/data ||" in line][0]
        self.assertIn("fuser -vm /srv/data 2>&1", umount)
        self.assertLess(umount.index("FAIL"), umount.index("exit 1"))
        snapshot = [call for call in self.call_list() if call[:2] == ["ec2", "create-snapshot"]][0]
        self.assertIn("vol-0data", snapshot)
        tags = snapshot[snapshot.index("--tag-specifications") + 1]
        for tag in ("{Key=Project,Value=tenant-ec2}", "{Key=Backup,Value=tenant-ec2}",
                    "{Key=Name,Value=tenant-ec2-data-before-replace}"):
            self.assertIn(tag, tags)
        # The volume was mounted at the start: no search for an earlier snapshot.
        self.assertNotIn("--filters", json.dumps(self.call_list()))

    def snapshot_age(self, minutes):
        when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)
        return "snap-0earlier\t" + when.isoformat()

    def test_step1_prepare_again_uses_a_recent_snapshot(self):
        self.step1_state(ssm_output=NOT_MOUNTED + "\n" + PROOF, recent_snapshot=self.snapshot_age(10))
        result = self.prepare1()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("the snapshot snap-0earlier of the data volume is completed and younger than one hour",
                      result.stdout)
        ops = self.operations()
        self.assertNotIn("ec2 create-snapshot", ops)
        search = [call for call in self.call_list() if call[:2] == ["ec2", "describe-snapshots"]][0]
        for word in ("Name=volume-id,Values=vol-0data", "Name=tag:Name,Values=tenant-ec2-data-before-replace",
                     "Name=status,Values=completed"):
            self.assertIn(word, search)
        self.assertIn("cloudformation get-template", ops)

    def test_step1_prepare_again_makes_a_new_snapshot_after_one_hour(self):
        for recent in (self.snapshot_age(90), "None"):
            with self.subTest(recent=recent):
                self.calls.write_text("")
                self.step1_state(ssm_output=NOT_MOUNTED + "\n" + PROOF, recent_snapshot=recent)
                result = self.prepare1()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ec2 create-snapshot", self.operations())

    def assert_stopped_before_detach(self, result, text):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(text, result.stderr)
        ops = self.operations()
        self.assertNotIn("cloudformation execute-change-set", ops)
        self.assertEqual(ops.count("cloudformation deploy"), 1)

    def test_step1_refuses_when_the_unmount_is_not_proven(self):
        self.step1_state(ssm_output="replace: Docker is stopped\nreplace: umount /srv/data failed: FAIL")
        result = self.prepare1()
        self.assert_stopped_before_detach(result, "does not prove the unmount")
        self.assertNotIn("ec2 create-snapshot", self.operations())

    def test_step1_refuses_a_failed_command(self):
        self.step1_state(ssm_status="Failed")
        result = self.prepare1()
        self.assert_stopped_before_detach(result, "the status Failed")
        self.assertNotIn("ec2 create-snapshot", self.operations())

    def test_step1_waits_for_the_snapshot_again(self):
        self.step1_state(snapshot_waits=[255, 255, 0])
        result = self.prepare1()
        self.assertEqual(result.returncode, 0, result.stderr)
        ops = self.operations()
        self.assertEqual(ops.count("ec2 wait"), 3)
        self.assertEqual(ops.count("ec2 describe-snapshots"), 2)
        self.assertLess(len(ops) - 1 - ops[::-1].index("ec2 wait"), ops.index("cloudformation get-template"))

    def test_step1_refuses_a_failed_snapshot(self):
        self.step1_state(snapshot_waits=[255], snapshot_state="error")
        result = self.prepare1()
        self.assert_stopped_before_detach(result, "the state error")
        self.assertNotIn("cloudformation get-template", self.operations())

    def test_step1_refuses_a_snapshot_that_never_completes(self):
        self.step1_state(snapshot_waits=[255] * 8)
        result = self.prepare1()
        self.assert_stopped_before_detach(result, "is not completed after 8 waits")

    def test_step1_refuses_an_update_with_no_replacement(self):
        for replacement in ("False", "Conditional"):
            with self.subTest(replacement=replacement):
                self.calls.write_text("")
                self.step1_state(queue=[[["Modify", "Instance", "AWS::EC2::Instance", replacement]]])
                result = self.prepare1()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("it does not replace the instance", result.stderr)
                self.assertIn("Nothing is changed", result.stderr)
                ops = self.operations()
                self.assertIn("cloudformation delete-change-set", ops)
                self.assertNotIn("ssm send-command", ops)

    def test_step1_prepare_refuses_a_change_set_with_another_change(self):
        self.step1_state(queue=[[REPLACE] + ATTACH_REPLACE,
                                DETACH + [["Modify", "Instance", "AWS::EC2::Instance", "False"]]])
        result = self.prepare1()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("a change other than the removal of the two attachments (Modify Instance)", result.stderr)
        self.assertIn("mount /srv/data && systemctl start", result.stderr)
        self.assertNotIn("cloudformation execute-change-set", self.operations())
        self.assertEqual(self.saved()["sets"], [])

    def test_step1_refuses_a_stack_template_with_no_attachment_parameter(self):
        self.step1_state(attach="")
        result = self.prepare1()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("current-image --write, then scripts/aws-host.sh deploy", result.stderr)
        self.assertEqual(self.operations(), ["cloudformation describe-stacks"] * 2)

    def test_step1_refuses_a_second_run_after_its_end(self):
        self.step1_state(attach="false")
        for word in (("--prepare",), ("--execute", "cs-2")):
            with self.subTest(word=word):
                result = self.run_script("deploy", "--replace-instance", "--step", "1", *word)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("step 1 is done", result.stderr)
                self.assertIn("--step 2 --prepare", result.stderr)
        self.assertNotIn("ssm send-command", self.operations())

    # ----- step 1, execute

    def prepared1(self, **extra):
        """The state after a step 1 prepare: the change set cs-2 waits."""
        self.step1_state(**extra)
        result = self.prepare1()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.saved()
        state["ssm_output"] = STILL
        state["ssm_commands"] = []
        self.state_path.write_text(json.dumps(state))
        self.calls.write_text("")

    def edit(self, **values):
        state = self.saved()
        state.update(values)
        self.state_path.write_text(json.dumps(state))

    def execute1(self, name="cs-2"):
        return self.run_script("deploy", "--replace-instance", "--step", "1", "--execute", name)

    def assert_not_executed(self, result, text, stays=True):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(text, result.stderr)
        ops = self.operations()
        self.assertNotIn("cloudformation execute-change-set", ops)
        self.assertNotIn("cloudformation delete-change-set", ops)
        self.assertNotIn("cloudformation deploy", ops)
        if stays:
            self.assertIn("cs-2", self.saved()["sets"])

    def test_step1_execute_runs_the_prepared_change_set(self):
        self.prepared1()
        result = self.execute1()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("step 1: pass. The data volume has no attachment", result.stdout)
        self.assertIn("deploy --replace-instance --step 2 --prepare", result.stdout)
        ops = self.operations()
        for absent in ("cloudformation deploy", "ec2 create-snapshot", "cloudformation get-template"):
            self.assertNotIn(absent, ops)
        order = [ops.index("cloudformation describe-change-set"), ops.index("ssm send-command"),
                 ops.index("cloudformation execute-change-set"), ops.index("cloudformation wait"),
                 ops.index("ec2 describe-volumes")]
        self.assertEqual(order, sorted(order))
        executed = [call for call in self.call_list() if call[:2] == ["cloudformation", "execute-change-set"]][0]
        self.assertEqual(executed[executed.index("--change-set-name") + 1], "cs-2")
        self.assertEqual(executed[executed.index("--stack-name") + 1], STACK)
        commands = "\n".join(self.saved()["ssm_commands"])
        self.assertIn("mountpoint -q /srv/data", commands)
        self.assertIn(STILL, commands)
        self.assertNotIn("umount", commands)

    def test_step1_execute_refuses_a_change_set_of_another_stack(self):
        self.prepared1()
        self.edit(set_stack={"cs-2": "tenant-ec2-other"})
        result = self.execute1()
        self.assert_not_executed(result, "is not a waiting change set of the stack tenant-ec2-host")
        self.assertNotIn("ssm send-command", self.operations())

    def test_step1_execute_refuses_an_unknown_change_set(self):
        self.prepared1()
        result = self.execute1("cs-9")
        self.assert_not_executed(result, "the stack tenant-ec2-host has no change set cs-9")

    def test_step1_execute_refuses_a_change_set_that_is_not_available(self):
        self.prepared1()
        self.edit(set_exec={"cs-2": "EXECUTE_COMPLETE"})
        result = self.execute1()
        self.assert_not_executed(result, "is not a waiting change set")

    def test_step1_execute_refuses_a_change_set_with_an_extra_change(self):
        self.prepared1()
        state = self.saved()
        state["changes"]["cs-2"] = DETACH + [["Modify", "Instance", "AWS::EC2::Instance", "False"]]
        self.state_path.write_text(json.dumps(state))
        result = self.execute1()
        self.assert_not_executed(result, "a change other than the removal of the two attachments (Modify Instance)")
        self.assertIn("It is not executed and it stays", result.stderr)
        self.assertIn("delete-change-set --stack-name tenant-ec2-host --change-set-name cs-2", result.stderr)
        self.assertNotIn("ssm send-command", self.operations())

    def test_step1_execute_refuses_a_change_set_of_step2(self):
        self.prepared1()
        state = self.saved()
        state["set_attach"]["cs-2"] = "true"
        self.state_path.write_text(json.dumps(state))
        result = self.execute1()
        self.assert_not_executed(result, "step1 needs AttachInstance=false")

    def test_step1_execute_refuses_when_the_mount_is_proven_again(self):
        self.prepared1()
        for status, output in (("Failed", "replace: /srv/data is mounted again: FAIL"),
                               ("Success", "replace: Docker is stopped")):
            with self.subTest(status=status):
                self.calls.write_text("")
                self.edit(ssm_status=status, ssm_output=output)
                result = self.execute1()
                self.assert_not_executed(result, "does not prove that /srv/data is still unmounted")
                self.assertIn("ssm send-command", self.operations())
                self.assertIn("Run step 1 --prepare again", result.stderr)

    # ----- step 2

    def step2_state(self, **extra):
        state = {"attach": "false", "queue": [[REPLACE] + ATTACH_ADD]}
        state.update(extra)
        self.state(state)

    def waiting2(self, rows, **extra):
        """The state after a step 2 prepare: the change set cs-5 waits."""
        state = {"attach": "false", "sets": ["cs-5"], "changes": {"cs-5": rows}, "set_attach": {"cs-5": "true"}}
        state.update(extra)
        self.state(state)

    def execute2(self, name="cs-5"):
        return self.run_script("deploy", "--replace-instance", "--step", "2", "--execute", name)

    def test_step2_prepare_shows_the_change_set_and_stops(self):
        self.write_env(NEW_IMAGE)
        self.step2_state()
        result = self.run_script("deploy", "--replace-instance", "--step", "2", "--prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("step 2 prepare: pass. The change set cs-1 waits", result.stdout)
        self.assertIn("deploy --replace-instance --step 2 --execute cs-1", result.stdout)
        self.assertIn("Instance\tAWS::EC2::Instance\tTrue", result.stdout)
        deploy = [call for call in self.call_list() if call[:2] == ["cloudformation", "deploy"]][0]
        self.assertIn("AmiId=" + NEW_IMAGE, deploy)
        self.assertIn("AttachInstance=true", deploy)
        self.assertEqual(deploy[deploy.index("--template-file") + 1], str(ROOT / "scripts/aws-host.cfn.yaml"))
        ops = self.operations()
        for absent in ("cloudformation execute-change-set", "cloudformation delete-change-set", "ssm send-command",
                       "ec2 create-snapshot"):
            self.assertNotIn(absent, ops)
        self.assertEqual(self.saved()["sets"], ["cs-1"])

    def test_step2_prepare_refuses_an_update_with_no_replacement(self):
        self.step2_state(queue=[ATTACH_ADD])
        result = self.run_script("deploy", "--replace-instance", "--step", "2", "--prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("it does not replace the instance", result.stderr)
        self.assertIn("current-image --write, then scripts/aws-host.sh deploy", result.stderr)
        self.assertIn("cloudformation delete-change-set", self.operations())
        self.assertEqual(self.saved()["sets"], [])

    def test_step2_execute_replaces_and_proves_the_attachments(self):
        self.waiting2([REPLACE] + ATTACH_ADD)
        result = self.execute2()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("step 2: the data volume is attached to the new instance i-0new", result.stdout)
        self.assertIn("step 2: the Elastic IP is on the new instance i-0new", result.stdout)
        self.assertIn("scripts/aws-host.sh verify", result.stdout)
        ops = self.operations()
        self.assertNotIn("cloudformation deploy", ops)
        self.assertLess(ops.index("cloudformation execute-change-set"), ops.index("ec2 describe-volumes"))
        self.assertLess(ops.index("ec2 describe-volumes"), ops.index("ec2 describe-addresses"))
        self.assertNotIn("ssm send-command", ops)
        self.assertNotIn("ec2 create-snapshot", ops)

    def test_step2_execute_refuses_an_update_with_no_replacement(self):
        for rows in (ATTACH_ADD, [["Modify", "Instance", "AWS::EC2::Instance", "Conditional"]] + ATTACH_ADD):
            with self.subTest(rows=rows):
                self.calls.write_text("")
                self.waiting2(rows)
                result = self.execute2()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("it does not replace the instance", result.stderr)
                self.assertNotIn("cloudformation execute-change-set", self.operations())
                self.assertNotIn("cloudformation delete-change-set", self.operations())

    def test_step2_execute_refuses_the_removal_of_the_data_or_the_address(self):
        for name in ("DataVolume", "FileSystem", "ElasticIp"):
            with self.subTest(name=name):
                self.calls.write_text("")
                self.waiting2([REPLACE] + ATTACH_ADD + [["Remove", name, "AWS::EC2::Volume", "None"]])
                result = self.execute2()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Remove " + name, result.stderr)
                self.assertNotIn("cloudformation execute-change-set", self.operations())

    def test_step2_execute_refuses_another_stack_and_step1_change_sets(self):
        cases = ({"set_stack": {"cs-5": "tenant-ec2-other"}}, {"set_attach": {"cs-5": "false"}})
        for extra in cases:
            with self.subTest(extra=extra):
                self.calls.write_text("")
                self.waiting2([REPLACE] + ATTACH_ADD, **extra)
                result = self.execute2()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("cloudformation execute-change-set", self.operations())

    def test_step2_refuses_before_step1(self):
        self.step2_state(attach="true")
        for word in (("--prepare",), ("--execute", "cs-1")):
            with self.subTest(word=word):
                result = self.run_script("deploy", "--replace-instance", "--step", "2", *word)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("step 1 is not done", result.stderr)
        self.assertNotIn("cloudformation deploy", self.operations())
        self.assertNotIn("cloudformation describe-change-set", self.operations())

    def test_step2_fails_when_the_volume_is_not_on_the_new_instance(self):
        self.waiting2([REPLACE] + ATTACH_ADD, volume_after="i-0old\tattached")
        result = self.execute2()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not attached to the new instance", result.stderr)

    def test_step2_fails_when_the_address_is_not_on_the_new_instance(self):
        self.waiting2([REPLACE] + ATTACH_ADD, address_instance="None")
        result = self.execute2()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the Elastic IP is not on the new instance", result.stderr)

    def test_step2_reports_a_failed_update_with_the_way_back(self):
        self.waiting2([REPLACE] + ATTACH_ADD, update_rc=255)
        result = self.execute2()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the update of the stack tenant-ec2-host failed", result.stderr)
        self.assertIn("current-image --write, then scripts/aws-host.sh deploy", result.stderr)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main(verbosity=1)
