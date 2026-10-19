#!/usr/bin/env python3
"""Offline tests for the memory stage schema, policy contracts and rendered templates."""
import copy
from fnmatch import fnmatchcase
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("memory", ROOT / "scripts/aws-memory.py")
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


def statements(document):
    return {statement["Sid"]: statement for statement in document["Statement"] if "Sid" in statement}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.config = memory.load_config("dev")

    def invalid(self, config, message):
        with self.assertRaisesRegex(memory.Invalid, message):
            memory.validate_config(config, "dev")

    def test_examples_and_publication(self):
        self.assertEqual([p["actorId"] for p in self.config["people"]], ["alice", "bruno", "chen", "dana"])
        self.assertEqual([p["pin"] for p in self.config["people"]], ["XYZ1000", "XYZ1001", "XYZ1002", "XYZ1003"])
        self.assertEqual([p["projectId"] for p in self.config["projects"]], ["example-project", "example-site"])
        for stage, people, projects in (("dev", 4, 2), ("test", 2, 1), ("prod", 1, 1)):
            config = memory.load_config(stage)
            self.assertEqual(len(config["people"]), people)
            self.assertEqual(len(config["projects"]), projects)
            self.assertIn(f'"config/memory/{stage}.yaml"', (ROOT / "scripts/publish_check.py").read_text())
        self.assertIn("# replace the example people", (ROOT / "config/memory/prod.yaml").read_text())

    def test_unknown_fields_at_every_level(self):
        for path in ((), ("memory",), ("bucket",), ("service",), ("people", 0), ("projects", 0)):
            with self.subTest(path=path):
                value = copy.deepcopy(self.config)
                target = value
                for key in path:
                    target = target[key]
                target["unknown"] = "example"
                self.invalid(value, "unknown: unknown field")
        for name in ("name", "email", "accountId", "key"):
            value = copy.deepcopy(self.config)
            value["people"][0][name] = "example"
            self.invalid(value, re.escape(name) + ": unknown field")

    def test_required_fields_types_and_expiry(self):
        for field in set(self.config) - {"hostRoles"}:
            value = copy.deepcopy(self.config)
            del value[field]
            self.invalid(value, f"config.{field}: required field")
        for value in (True, "1", 2):
            self.invalid(dict(self.config, schemaVersion=value), "schemaVersion: must be the integer 1")
        for days in (True, "7", 2, 366, 7.5):
            self.invalid(dict(self.config, memory={"eventExpiryDays": days}), "memory.eventExpiryDays: must be an integer")
        for days in (3, 365):
            memory.validate_config(dict(self.config, memory={"eventExpiryDays": days}), "dev")
        for section, key in (("bucket", "enabled"), ("service", "submit"), ("service", "workerRead")):
            value = copy.deepcopy(self.config)
            value[section][key] = "true"
            self.invalid(value, section + "." + key + ": must be a boolean")
        self.invalid(dict(self.config, stage="prod"), "stage: must match")
        self.invalid(dict(self.config, region="not a region"), "region: must be an AWS region")
        for field in ("people", "projects"):
            self.invalid(dict(self.config, **{field: {}}), field + ": must be a list")

    def test_duplicate_actor_project_and_pin(self):
        for field, key in (("people", "actorId"), ("people", "pin"), ("projects", "projectId")):
            value = copy.deepcopy(self.config)
            value[field][1][key] = value[field][0][key]
            self.invalid(value, key + ": must be unique")
        value = copy.deepcopy(self.config)
        value["people"].append({"actorId": "former", "pin": "XYZ1000", "status": "offboarded"})
        self.invalid(value, "pin: must be unique")

    def test_bad_ids_pin_and_status(self):
        for actor in ("", "UPPER", "a*", "a?", "a/b", "a:b", "a_b", "a" * 65, 3):
            value = copy.deepcopy(self.config)
            value["people"][0]["actorId"] = actor
            self.invalid(value, "actorId: must match")
            value = copy.deepcopy(self.config)
            value["projects"][0]["projectId"] = actor
            self.invalid(value, "projectId: must match")
        for pin in ("XYZ100", "xyz1000", "XYZ10000", 1000, "XYZ\u0661\u0660\u0660\u0660"):
            value = copy.deepcopy(self.config)
            value["people"][0]["pin"] = pin
            self.invalid(value, "pin: must be XYZ")
        value = copy.deepcopy(self.config)
        value["people"][0]["status"] = "disabled"
        self.invalid(value, "status: must be active or offboarded")

    def test_membership_constraints(self):
        for key, values, message in (("maintainers", [], "at least one"), ("maintainers", ["bruno"], "active member"),
                                     ("deleters", ["bruno"], "active member"), ("members", ["unknown"], "active person"),
                                     ("members", ["alice", "alice"], "unique")):
            value = copy.deepcopy(self.config)
            value["projects"][0][key] = values
            self.invalid(value, key + ": .*" + message)
        value = copy.deepcopy(self.config)
        value["people"][3]["status"] = "offboarded"
        self.invalid(value, "members: each member must be an active person")
        del value["projects"][0]["deleters"]
        value["projects"][0]["members"].remove("dana")
        memory.validate_config(value, "dev")

    def test_duplicate_yaml_fields(self):
        for text in ("stage: dev\nstage: test\n", "memory:\n  eventExpiryDays: 7\n  eventExpiryDays: 365\n"):
            with self.assertRaisesRegex(memory.Invalid, "duplicate field"):
                yaml.load(text, Loader=memory.ConfigLoader)
        with self.assertRaisesRegex(memory.Invalid, "field names must be strings"):
            yaml.load("1: value\n", Loader=memory.ConfigLoader)

    def test_tenant_pi_scope_and_statement_order(self):
        config = copy.deepcopy(self.config)
        config["projects"][0]["members"].insert(1, "bruno")
        scope = memory.person_scope(config, "bruno")
        self.assertEqual(scope, {
            "read": ["/shared/*", "/actor/bruno/*", "/project/example-project/shared/*", "/project/example-project/proposals/bruno/*",
                     "/project/example-site/shared/*", "/project/example-site/proposals/bruno/*", "/project/example-site/proposals/*",
                     "/project/example-site/rejected/*"],
            "write": ["/project/example-project/proposals/bruno/*", "/project/example-site/proposals/bruno/*"],
            "move": ["/project/example-site/proposals/*", "/project/example-site/shared/*", "/project/example-site/rejected/*"], "delete": []})
        document = memory.memory_policy(scope, "memory-arn")
        self.assertEqual([item["Sid"] for item in document["Statement"]], ["Read", "Write", "Move"])
        expected_read = {"Sid": "Read", "Effect": "Allow", "Action": ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"],
                         "Resource": "memory-arn", "Condition": {"StringLike": {memory.KEY: scope["read"]}}}
        self.assertEqual(document["Statement"][0], expected_read)
        for item, kind, action in zip(document["Statement"][1:], ("write", "move"), ("BatchCreateMemoryRecords", "BatchUpdateMemoryRecords")):
            self.assertEqual(item, {"Sid": kind.capitalize(), "Effect": "Allow", "Action": ["bedrock-agentcore:" + action], "Resource": "memory-arn",
                                    "Condition": {"ForAllValues:StringLike": {memory.KEY: scope[kind]}, "Null": {memory.KEY: "false"}}})
        with self.assertRaisesRegex(memory.Invalid, "actor: must name an active person"):
            memory.person_scope(config, "missing")

    def test_deleter_is_only_project_scoped(self):
        self.config["projects"][0]["deleters"] = ["dana"]
        scope = memory.person_scope(self.config, "dana")
        self.assertEqual(scope["delete"], ["/project/example-project/*"])
        policy = memory.policy_document(self.config, scope, "dana")
        delete = statements(policy)["Delete"]
        self.assertEqual(delete["Action"], ["bedrock-agentcore:BatchDeleteMemoryRecords"])
        self.assertEqual(delete["Condition"], {"ForAllValues:StringLike": {memory.KEY: scope["delete"]}, "Null": {memory.KEY: "false"}})
        self.assertEqual([s["Sid"] for s in policy["Statement"] if "Sid" in s], ["Read", "Write", "Delete"])
        s3_delete = [s for s in policy["Statement"] if s["Action"] == "s3:DeleteObject"]
        self.assertEqual(s3_delete[0]["Resource"], [memory.sub("${Bucket.Arn}/users/dana/*"),
                                                    memory.sub("${Bucket.Arn}/projects/example-project/*")])
        for actor in ("alice", "bruno", "chen"):
            self.assertNotIn("Delete", statements(memory.person_policy(self.config, actor)))
        compact = copy.deepcopy(self.config)
        compact["people"] = [{"actorId": "a", "pin": "XYZ1000", "status": "active"},
                             {"actorId": "b", "pin": "XYZ1001", "status": "active"}]
        compact["projects"] = [{"projectId": "p", "maintainers": ["a"], "members": ["a", "b"], "deleters": ["b"]}]
        memory.validate_config(compact, "dev")
        self.assertLessEqual(memory.policy_size(memory.person_policy(compact, "b"), compact), 2048)
        self.assertIn("Delete", statements(memory.person_policy(compact, "b")))
        memory.render(compact)

    def assert_s3_policy(self, document, expected):
        actual = {"s3:GetObject": [], "s3:PutObject": [], "s3:DeleteObject": []}
        lists = []
        for item in document["Statement"]:
            actions = item["Action"] if isinstance(item["Action"], list) else [item["Action"]]
            if actions == ["s3:ListBucket"]:
                lists.append(item)
                self.assertEqual(item["Resource"], memory.att("Bucket", "Arn"))
                self.assertEqual(item["Condition"], {"StringLike": {"s3:prefix": expected["read"]}})
            for action in actions:
                if action.startswith("s3:"):
                    self.assertIn(action, ["s3:ListBucket", *actual])
                if action in actual:
                    resources = item["Resource"] if isinstance(item["Resource"], list) else [item["Resource"]]
                    actual[action] += [r["Fn::Sub"].removeprefix("${Bucket.Arn}/") for r in resources]
        self.assertEqual(len(lists), 1)
        for action, kind in (("s3:GetObject", "read"), ("s3:PutObject", "write"), ("s3:DeleteObject", "delete")):
            self.assertEqual(actual[action], expected[kind])

    def test_s3_scopes(self):
        for actor, projects in (("alice", ["example-project"]), ("bruno", ["example-site"]),
                                ("chen", ["example-project", "example-site"]), ("dana", ["example-project"])):
            with self.subTest(actor=actor):
                prefixes = [f"projects/{project}/*" for project in projects] + [f"users/{actor}/*"]
                expected = {"read": prefixes, "write": prefixes, "delete": [f"users/{actor}/*"]}
                self.assertEqual(memory.s3_scope(self.config, actor=actor), expected)
                self.assert_s3_policy(memory.person_policy(self.config, actor), expected)
        self.config["projects"][0]["deleters"] = ["dana"]
        prefixes = ["projects/example-project/*", "users/dana/*"]
        self.assert_s3_policy(memory.person_policy(self.config, "dana"),
                              {"read": prefixes, "write": prefixes, "delete": ["users/dana/*", "projects/example-project/*"]})

    def test_person_without_projects_has_only_own_files(self):
        self.config["projects"] = []
        for person in self.config["people"]:
            actor = person["actorId"]
            own = [f"users/{actor}/*"]
            self.assert_s3_policy(memory.person_policy(self.config, actor), {"read": own, "write": own, "delete": own})

    def test_old_bucket_prefixes_are_absent_and_memory_statements_stay_first(self):
        for stage in memory.STAGES:
            config = memory.load_config(stage)
            resources = memory.template(config)["Resources"]
            for resource in resources.values():
                policies = resource["Properties"].get("Policies", [])
                documents = [p["PolicyDocument"] for p in policies]
                if "PolicyDocument" in resource["Properties"]:
                    documents.append(resource["Properties"]["PolicyDocument"])
                for document in documents:
                    for statement in document["Statement"]:
                        actions = statement["Action"]
                        actions = actions if isinstance(actions, list) else [actions]
                        if any(a.startswith("s3:") for a in actions):
                            self.assertNotRegex(json.dumps(statement), r"(?:/|\")(?:(?:shared|actor|project)/)")
            for person in config["people"]:
                actor = person["actorId"]
                expected = memory.memory_policy(memory.person_scope(config, actor), memory.att("Memory", "MemoryArn"))["Statement"]
                self.assertEqual(memory.person_policy(config, actor)["Statement"][:len(expected)], expected)
            for kind in ("submit", "worker"):
                scope = memory.service_scope(kind)
                expected = memory.memory_policy(scope, memory.att("Memory", "MemoryArn"))["Statement"]
                self.assertEqual(memory.policy_document(config, scope, service=kind)["Statement"][:len(expected)], expected)

    def test_service_scopes(self):
        submit = memory.service_scope("submit")
        self.assertEqual(submit, {"read": ["/shared/*", "/actor/*"], "write": ["/shared/*", "/actor/*"],
                                  "move": ["/shared/*", "/actor/*"], "delete": []})
        worker = memory.service_scope("worker")
        self.assertEqual(worker, {"read": ["/shared/*", "/project/*/shared/*"], "write": [], "move": [], "delete": []})
        self.assertEqual([s["Sid"] for s in memory.memory_policy(submit, "memory")["Statement"]], ["Read", "Write", "Move"])
        self.assertEqual([s["Sid"] for s in memory.memory_policy(worker, "memory")["Statement"]], ["Read"])
        for kind, scope in (("submit", submit), ("worker", worker)):
            resources = memory.template(self.config)["Resources"]
            document = resources[kind.capitalize() + "Policy"]["Properties"]["PolicyDocument"]
            self.assertEqual(document, memory.policy_document(self.config, scope, service=kind))
            expected = ({"read": ["projects/*", "users/*"], "write": ["projects/*", "users/*"], "delete": []}
                        if kind == "submit" else {"read": ["projects/*"], "write": [], "delete": []})
            self.assertEqual(memory.s3_scope(self.config, service=kind), expected)
            self.assert_s3_policy(document, expected)
        with self.assertRaisesRegex(memory.Invalid, "service: must be submit or worker"):
            memory.s3_scope(self.config)

    def test_boundary_ceiling(self):
        policy = statements(memory.boundary(self.config))
        self.assertEqual(set(policy), {"Memory", "BucketList", "BucketObjects", "Identity"})
        self.assertEqual(policy["Memory"]["Action"], ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords",
                         "bedrock-agentcore:GetMemoryRecord", "bedrock-agentcore:BatchCreateMemoryRecords",
                         "bedrock-agentcore:BatchUpdateMemoryRecords", "bedrock-agentcore:BatchDeleteMemoryRecords"])
        self.assertEqual(policy["Memory"]["Resource"], memory.att("Memory", "MemoryArn"))
        self.assertEqual(policy["BucketList"]["Action"], ["s3:ListBucket"])
        self.assertEqual(policy["BucketObjects"]["Action"], ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"])
        for name in ("BucketList", "BucketObjects"):
            self.assertEqual(policy[name]["Condition"], {"StringEquals": {"s3:ResourceAccount": memory.ref("AWS::AccountId")}})
        self.assertEqual(policy["Identity"], {"Sid": "Identity", "Effect": "Allow", "Action": "sts:GetCallerIdentity", "Resource": "*"})

    def test_bucket_protection_and_deny_allowlist(self):
        resources = memory.template(self.config)["Resources"]
        bucket = resources["Bucket"]["Properties"]
        self.assertTrue(all(bucket["PublicAccessBlockConfiguration"].values()))
        self.assertEqual(bucket["BucketEncryption"]["ServerSideEncryptionConfiguration"],
                         [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}, "BucketKeyEnabled": True}])
        self.assertEqual(bucket["VersioningConfiguration"], {"Status": "Enabled"})
        rule = bucket["LifecycleConfiguration"]["Rules"][0]
        self.assertEqual(rule["NoncurrentVersionExpiration"], {"NoncurrentDays": 30})
        self.assertEqual(rule["AbortIncompleteMultipartUpload"], {"DaysAfterInitiation": 7})
        policy = statements(resources["BucketPolicy"]["Properties"]["PolicyDocument"])
        for statement in policy.values():
            self.assertEqual(statement["Effect"], "Deny")
            self.assertEqual(statement["Principal"], "*")
            self.assertEqual(statement["Action"], "s3:*")
            self.assertEqual(statement["Resource"], [memory.att("Bucket", "Arn"), memory.sub("${Bucket.Arn}/*")])
        self.assertEqual(policy["DenyNoTls"]["Condition"], {"Bool": {"aws:SecureTransport": "false"}})
        expected = [memory.sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:root"),
                    memory.sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:role/${AdministratorRole}"),
                    {"Fn::If": ["HasAdministratorUser", memory.sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:user/${AdministratorUser}"),
                                memory.ref("AWS::NoValue")]}]
        expected += [memory.att(memory.person_logical_id(a), "Arn") for a in ("alice", "bruno", "chen", "dana")]
        expected += [memory.att("SubmitUser", "Arn"), memory.att("WorkerUser", "Arn")]
        self.assertEqual(policy["DenyOutsiders"]["Condition"], {"ArnNotLike": {"aws:PrincipalArn": expected}})

    def test_optional_administrator_user(self):
        for stage in memory.STAGES:
            document = memory.template(memory.load_config(stage))
            parameter = document["Parameters"]["AdministratorUser"]
            self.assertEqual(parameter["Default"], "")
            for name in ("", "ExampleAdmin", "a" * 64, "admin+example@example.invalid"):
                self.assertIsNotNone(re.fullmatch(parameter["AllowedPattern"], name))
            for name in ("*", "admin?", "team/admin", "a" * 65, "admin name"):
                self.assertIsNone(re.fullmatch(parameter["AllowedPattern"], name))
            self.assertEqual(document["Conditions"]["HasAdministratorUser"],
                             {"Fn::Not": [{"Fn::Equals": [memory.ref("AdministratorUser"), ""]}]})
            policy = statements(document["Resources"]["BucketPolicy"]["Properties"]["PolicyDocument"])
            principals = policy["DenyOutsiders"]["Condition"]["ArnNotLike"]["aws:PrincipalArn"]
            for name in ("", "ExampleAdmin"):
                resolved = []
                for principal in principals:
                    if "Fn::If" in principal:
                        condition, present, absent = principal["Fn::If"]
                        self.assertEqual(condition, "HasAdministratorUser")
                        principal = present if name else absent
                    if principal != memory.ref("AWS::NoValue"):
                        resolved.append(principal)
                users = [p for p in resolved if ":user/" in p.get("Fn::Sub", "")]
                self.assertEqual(users, [memory.sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:user/${AdministratorUser}")] if name else [])
                self.assertEqual(len(resolved), len(principals) if name else len(principals) - 1)

    def test_users_no_credentials_and_offboarding(self):
        self.config["people"][3]["status"] = "offboarded"
        self.config["projects"][0]["members"].remove("dana")
        document = memory.template(memory.validate_config(self.config, "dev"))
        self.assertNotIn(memory.person_logical_id("dana"), document["Resources"])
        self.assertNotIn("dana", json.dumps(document))
        for resource in document["Resources"].values():
            self.assertNotEqual(resource["Type"], "AWS::IAM::AccessKey")
            if resource["Type"] == "AWS::IAM::User":
                properties = resource["Properties"]
                self.assertEqual(properties["PermissionsBoundary"], memory.ref("Boundary"))
                self.assertNotIn("LoginProfile", properties)
                self.assertNotIn("Groups", properties)
                if "Policies" in properties:
                    self.assertEqual(len(properties["Policies"]), 1)
                    self.assertEqual(properties["Policies"][0]["PolicyName"], "agentcore-memory")
        self.assertNotEqual(memory.person_logical_id("a-b"), memory.person_logical_id("ab"))

    def test_parameters_outputs_and_retention(self):
        document = memory.template(self.config)
        self.assertEqual(set(document["Parameters"]), {"Project", "Stage", "AdministratorRole", "AdministratorUser"})
        for parameter in document["Parameters"].values():
            self.assertIn("ConstraintDescription", parameter)
            self.assertTrue("AllowedPattern" in parameter or "AllowedValues" in parameter)
        self.assertEqual(document["Parameters"]["Stage"]["AllowedValues"], ["dev", "test", "prod"])
        self.assertEqual(document["Rules"]["StageMatchesConfig"]["Assertions"][0]["Assert"], {"Fn::Equals": [memory.ref("Stage"), "dev"]})
        self.assertEqual(set(document["Outputs"]), {"MemoryId", "MemoryArn", "BucketName", "BoundaryArn", "PersonUsers", "SubmitUser", "WorkerUser"})
        self.assertEqual(document["Outputs"]["MemoryId"]["Value"], memory.att("Memory", "MemoryId"))
        resource = document["Resources"]["Memory"]
        self.assertEqual(resource["Properties"]["Name"], {"Fn::Join": ["_", [{"Fn::Join": ["_", {"Fn::Split": ["-", memory.ref("Project")]}]}, "memory", memory.ref("Stage")]]})
        self.assertNotIn("MemoryStrategies", resource["Properties"])
        self.assertNotIn("MemoryExecutionRoleArn", resource["Properties"])
        self.assertNotIn("EncryptionKeyArn", resource["Properties"])
        self.assertEqual(resource["Properties"]["Tags"], {"Project": memory.ref("Project"), "Stage": memory.ref("Stage")})
        for name in ("Memory", "Bucket", "BucketPolicy"):
            self.assertEqual(document["Resources"][name]["DeletionPolicy"], "RetainExceptOnCreate")
            self.assertEqual(document["Resources"][name]["UpdateReplacePolicy"], "Retain")
        self.assertNotIn("MemoryResourcePolicy", document["Resources"])
        self.assertIn("# Not verified: a resource policy on a memory.", memory.render(self.config))

    def test_optional_bucket_services_and_empty_people(self):
        self.config["bucket"]["enabled"] = False
        self.config["service"] = {"submit": False, "workerRead": False}
        self.config["people"] = []
        self.config["projects"] = []
        document = memory.template(memory.validate_config(self.config, "dev"))
        self.assertEqual(set(document["Resources"]), {"Memory", "Boundary"})
        self.assertNotIn("BucketName", document["Outputs"])
        for name in ("PersonUsers", "SubmitUser", "WorkerUser"):
            self.assertEqual(document["Outputs"][name]["Value"], "")
        self.assertNotIn("s3:", json.dumps(document))

    def test_policy_size_counts_resolved_values(self):
        policy = memory.person_policy(self.config, "alice")
        self.assertGreater(memory.policy_size(policy, self.config), len(json.dumps(policy, separators=(",", ":"))))
        for stage in memory.STAGES:
            config = memory.load_config(stage)
            for person in config["people"]:
                self.assertLessEqual(memory.policy_size(memory.person_policy(config, person["actorId"]), config), memory.POLICY_LIMIT)
        self.config["projects"][0]["members"].insert(1, "bruno")
        policy = memory.policy_document(self.config, memory.person_scope(self.config, "bruno"), "bruno")
        self.assertEqual(memory.policy_size(policy, self.config), 2250)
        with self.assertRaisesRegex(memory.Invalid, "people.bruno.policy: at most 2048 characters.*resolved upper bound is 2250"):
            memory.render(self.config)
        policy = memory.policy_document(self.config, memory.person_scope(self.config, "dana"), "dana")
        exact = memory.policy_size(policy, self.config)
        with patch.object(memory, "POLICY_LIMIT", exact):
            memory.person_policy(self.config, "dana")
        with patch.object(memory, "POLICY_LIMIT", exact - 1):
            with self.assertRaisesRegex(memory.Invalid, "people.dana.policy"):
                memory.person_policy(self.config, "dana")

    def test_template_limit_and_fit_count(self):
        self.config["people"] = [{"actorId": f"person-{i:03}", "pin": f"XYZ{i:04}", "status": "active"} for i in range(80)]
        self.config["projects"] = []
        with self.assertRaisesRegex(memory.Invalid, "template: must be at most 51200 bytes") as error:
            memory.render(self.config)
        count = int(re.search(r"; ([0-9]+) active people fit", str(error.exception)).group(1))
        self.assertGreater(count, 0)
        self.assertLess(count, 80)
        candidate = copy.deepcopy(self.config)
        candidate["people"] = self.config["people"][:count]
        self.assertLessEqual(len(memory.render(candidate).encode()), 51200)
        candidate["people"] = self.config["people"][:count + 1]
        with self.assertRaisesRegex(memory.Invalid, "template: must be at most 51200 bytes"):
            memory.render(candidate)

    def test_iam_name_length_limits(self):
        self.config["people"] = [{"actorId": "a" * 35, "pin": "XYZ1000", "status": "active"}]
        self.config["projects"] = []
        memory.validate_config(self.config, "dev")
        self.assertEqual(memory.project_limit(self.config), 10)
        self.config["people"][0]["actorId"] += "a"
        with self.assertRaisesRegex(memory.Invalid, "actorId: the IAM user name exceeds 64"):
            memory.render(self.config)

    def test_determinism_and_tracked_templates(self):
        for stage in memory.STAGES:
            config = memory.load_config(stage)
            before = copy.deepcopy(config)
            first = memory.render(config)
            self.assertEqual(first, memory.render(config))
            self.assertEqual(config, before)
            self.assertEqual(first.encode(), (ROOT / "scripts" / f"aws-memory-{stage}.cfn.yaml").read_bytes())
            self.assertTrue(first.startswith(f"# Generated by scripts/aws-memory.py from config/memory/{stage}.yaml. Do not edit.\n"))
            self.assertLessEqual(len(first.encode()), 51200)
            self.assertEqual(yaml.safe_load(first), memory.template(config))
        reordered = {key: self.config[key] for key in reversed(self.config)}
        self.assertEqual(memory.render(reordered), memory.render(self.config))
        reordered["people"] = list(reversed(self.config["people"]))
        self.assertEqual(memory.render(reordered), memory.render(self.config))

    def test_client_config(self):
        config = memory.client_config(self.config, "chen", "ExampleMemory-abcdefghij")
        self.assertEqual(config, {"schemaVersion": 1, "memoryId": "ExampleMemory-abcdefghij", "region": "us-east-1",
                                  "profile": "tenant-ec2-memory-dev-person-chen", "actorId": "chen",
                                  "projects": [{"projectId": "example-project", "role": "member"}, {"projectId": "example-site", "role": "member"}]})
        self.assertEqual(memory.client_config(self.config, "alice", "ExampleMemory-abcdefghij")["projects"][0]["role"], "maintainer")
        for invalid in ("", "a/b", "1memory", "a" * 129):
            with self.assertRaisesRegex(memory.Invalid, "memory-id:"):
                memory.client_config(self.config, "alice", invalid)

    def host_fixture(self):
        config = copy.deepcopy(self.config)
        config["hostRoles"] = [{"roleSuffix": "macos-role", "actorId": "macos-agent", "proposalProject": "example-project"}]
        return memory.validate_config(config, "dev")

    def test_host_schema_defaults_and_refusals(self):
        for stage in memory.STAGES:
            self.assertEqual(memory.load_config(stage)["hostRoles"], [])
        missing = copy.deepcopy(self.config)
        del missing["hostRoles"]
        self.assertEqual(memory.validate_config(missing, "dev"), self.config)
        self.assertNotIn("hostRoles", missing)
        for value in (None, {}, "macos-role", True):
            self.invalid(dict(self.config, hostRoles=value), "hostRoles: must be a list")
        for value in (None, "macos-role", []):
            self.invalid(dict(self.config, hostRoles=[value]), r"hostRoles\[0\]: must be a mapping")
        for field in ("roleSuffix", "actorId", "proposalProject"):
            config = self.host_fixture()
            del config["hostRoles"][0][field]
            self.invalid(config, field + ": required field")
        for field in ("roleArn", "accountId", "profile", "pin", "unknown"):
            config = self.host_fixture()
            config["hostRoles"][0][field] = "example"
            self.invalid(config, field + ": unknown field")
        for value in ("", "other-role", "macos-role*", "macos-role?", "path/macos-role", "arn:example", None, [], {}):
            config = self.host_fixture()
            config["hostRoles"][0]["roleSuffix"] = value
            self.invalid(config, "roleSuffix: must be macos-role")
        for field in ("actorId", "proposalProject"):
            for value in ("", "UPPER", "a*", "a?", "a/b", "a:b", "a_b", "${Project}", "a" * 65, 3, None, []):
                config = self.host_fixture()
                config["hostRoles"][0][field] = value
                self.invalid(config, field + ": must match")
        config = self.host_fixture()
        config["hostRoles"].append(dict(config["hostRoles"][0], actorId="another-agent"))
        self.invalid(config, "roleSuffix: must be unique")
        config = self.host_fixture()
        config["hostRoles"].append(copy.deepcopy(config["hostRoles"][0]))
        self.invalid(config, "roleSuffix: must be unique")
        config = self.host_fixture()
        config["hostRoles"][0]["proposalProject"] = "missing-project"
        self.invalid(config, "proposalProject: must name a configured project")
        for status in ("active", "offboarded"):
            config = self.host_fixture()
            config["people"].append({"actorId": "macos-agent", "pin": "XYZ1004", "status": status})
            self.invalid(config, "actorId: must not name an active or offboarded person")

    def test_host_admission_adds_only_exact_exception_and_removal_restores_output(self):
        baseline = memory.template(self.config)
        config = self.host_fixture()
        document = memory.template(config)
        policy = statements(document["Resources"]["BucketPolicy"]["Properties"]["PolicyDocument"])
        arn = memory.sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:role/${Project}-macos-role")
        principals = policy["DenyOutsiders"]["Condition"]["ArnNotLike"]["aws:PrincipalArn"]
        self.assertEqual(principals[-1], arn)
        self.assertEqual(principals.count(arn), 1)
        self.assertNotIn("assumed-role", json.dumps(document))
        self.assertNotIn("*", arn["Fn::Sub"])
        self.assertTrue(all(s["Effect"] == "Deny" for s in policy.values()))
        principals.pop()
        self.assertEqual(document, baseline)
        # Removing admission removes the exception; it does not change any existing grant.
        config["hostRoles"] = []
        self.assertEqual(memory.render(config), memory.render(self.config))
        del config["hostRoles"]
        self.assertEqual(memory.render(config), memory.render(self.config))

    def test_host_policy_exact_scope_and_no_person_policy_change(self):
        config = self.host_fixture()
        policy = memory.host_policy(config, "macos-role")
        bucket = "arn:${AWS::Partition}:s3:::${MemoryBucketName}"
        self.assertEqual(policy, {"Version": "2012-10-17", "Statement": [
            {"Sid": "Read", "Effect": "Allow", "Action": memory.READ + ["bedrock-agentcore:GetMemoryRecord"],
             "Resource": memory.ref("MemoryArn"),
             "Condition": {"StringLike": {memory.KEY: ["/shared/*", "/project/*/shared/*"]}}},
            {"Sid": "Write", "Effect": "Allow", "Action": ["bedrock-agentcore:BatchCreateMemoryRecords"],
             "Resource": memory.ref("MemoryArn"),
             "Condition": {"ForAllValues:StringLike": {memory.KEY: ["/project/example-project/proposals/macos-agent/*"]},
                           "Null": {memory.KEY: "false"}}},
            {"Sid": "BucketList", "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": memory.sub(bucket),
             "Condition": {"StringLike": {"s3:prefix": ["projects/*", "users/macos-agent/*"]}}},
            {"Sid": "BucketRead", "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": memory.sub(bucket + "/projects/*")},
            {"Sid": "BucketWrite", "Effect": "Allow", "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
             "Resource": memory.sub(bucket + "/users/macos-agent/*")},
        ]})
        self.assertEqual(memory.READ, ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"])
        for person in config["people"]:
            actor = person["actorId"]
            self.assertEqual(memory.person_policy(config, actor), memory.person_policy(self.config, actor))
            self.assertEqual(memory.client_config(config, actor, "ExampleMemory-abcdefghij"),
                             memory.client_config(self.config, actor, "ExampleMemory-abcdefghij"))
        self.assertEqual(memory.boundary(config), memory.boundary(self.config))
        for kind in ("submit", "worker"):
            scope = memory.service_scope(kind)
            self.assertEqual(memory.policy_document(config, scope, service=kind),
                             memory.policy_document(self.config, scope, service=kind))

    def test_host_namespace_and_prefix_matching(self):
        policy = statements(memory.host_policy(self.host_fixture(), "macos-role"))
        read = policy["Read"]["Condition"]["StringLike"][memory.KEY]
        write = policy["Write"]["Condition"]["ForAllValues:StringLike"][memory.KEY]
        matches = lambda value, patterns: any(fnmatchcase(value, p) for p in patterns)
        for value in ("/shared/", "/project/example-project/shared/", "/project/example-site/shared/"):
            self.assertTrue(matches(value, read))
        for value in ("/actor/macos-agent/", "/actor/alice/", "/project/example-project/proposals/macos-agent/",
                      "/project/example-project/rejected/macos-agent/"):
            self.assertFalse(matches(value, read))
        allowed = "/project/example-project/proposals/macos-agent/"
        self.assertTrue(matches(allowed, write))
        for value in ("/project/example-site/proposals/macos-agent/", "/project/example-project/proposals/alice/",
                      "/project/example-project/proposals/macos-agent-extra/", "/project/example-project/shared/", "/shared/"):
            self.assertFalse(matches(value, write))
            self.assertFalse(all(matches(n, write) for n in (allowed, value)))
        self.assertEqual(policy["Write"]["Condition"]["Null"], {memory.KEY: "false"})
        prefixes = policy["BucketList"]["Condition"]["StringLike"]["s3:prefix"]
        for value in ("projects/example-project/", "projects/example-site/", "users/macos-agent/"):
            self.assertTrue(matches(value, prefixes))
        for value in ("", "users/", "users/alice/", "users/macos-agent-extra/", "actor/macos-agent/", "project/example-project/"):
            self.assertFalse(matches(value, prefixes))
        actions = [a for statement in policy.values() for a in statement["Action"]]
        for denied in ("bedrock-agentcore:BatchUpdateMemoryRecords", "bedrock-agentcore:BatchDeleteMemoryRecords", "s3:DeleteObject"):
            self.assertNotIn(denied, actions)

    def test_host_bucket_disabled_and_stage_resource_binding(self):
        config = self.host_fixture()
        config["bucket"]["enabled"] = False
        policy = memory.host_policy(config, "macos-role")
        self.assertEqual(list(statements(policy)), ["Read", "Write"])
        self.assertNotIn("s3:", json.dumps(policy))
        self.assertEqual(memory.template(config), memory.template(dict(config, hostRoles=[])))
        for stage in memory.STAGES:
            config = memory.load_config(stage)
            config["hostRoles"] = copy.deepcopy(self.host_fixture()["hostRoles"])
            config = memory.validate_config(config, stage)
            policy = memory.host_policy(config, "macos-role")
            # A consumer binds these references to one stage's outputs, not a stage wildcard.
            for statement in policy["Statement"][:2]:
                self.assertEqual(statement["Resource"], memory.ref("MemoryArn"))
            for statement in policy["Statement"][2:]:
                self.assertTrue(statement["Resource"]["Fn::Sub"].startswith("arn:${AWS::Partition}:s3:::${MemoryBucketName}"))
            self.assertNotIn("Fn::GetAtt", json.dumps(policy))
            self.assertNotIn("AWS::AccountId", json.dumps(policy))
            self.assertNotIn("*", json.dumps(policy["Statement"][0]["Resource"]))
            self.assertEqual(memory.template(config)["Rules"]["StageMatchesConfig"]["Assertions"][0]["Assert"],
                             {"Fn::Equals": [memory.ref("Stage"), stage]})
            config["stage"] = "another-stage"
            with self.assertRaisesRegex(memory.Invalid, "stage: must match"):
                memory.host_policy(config, "macos-role")

    def test_host_selection_is_validated_before_policy_or_config(self):
        for config in (self.config, memory.load_config("test"), memory.load_config("prod")):
            with self.assertRaisesRegex(memory.Invalid, "must name an admitted host in the selected stage"):
                memory.host_policy(config, "macos-role")
            with self.assertRaisesRegex(memory.Invalid, "must name an admitted host in the selected stage"):
                memory.host_client_config(config, "macos-role", "ExampleMemory-abcdefghij")
        for suffix in ("*", "other-role", "path/macos-role", "", None):
            with self.assertRaisesRegex(memory.Invalid, "role-suffix: must be macos-role"):
                memory.host_policy(self.host_fixture(), suffix)
            with self.assertRaisesRegex(memory.Invalid, "role-suffix: must be macos-role"):
                memory.host_client_config(self.host_fixture(), suffix, "ExampleMemory-abcdefghij")
        config = self.host_fixture()
        config["hostRoles"][0]["actorId"] = "alice"
        for render in (lambda: memory.host_policy(config, "macos-role"),
                       lambda: memory.host_client_config(config, "macos-role", "ExampleMemory-abcdefghij")):
            with self.assertRaisesRegex(memory.Invalid, "actorId: must not name"):
                render()

    def test_host_client_config_and_determinism(self):
        config = self.host_fixture()
        before = copy.deepcopy(config)
        client = memory.host_client_config(config, "macos-role", "ExampleMemory-abcdefghij")
        self.assertEqual(client, {"schemaVersion": 1, "memoryId": "ExampleMemory-abcdefghij", "region": "us-east-1",
                                  "profile": "ec2-instance", "actorId": "macos-agent",
                                  "projects": [{"projectId": "example-project", "role": "member"}]})
        self.assertNotIn("pin", client)
        for value in ("", "a/b", "1memory", "a" * 129, None, 1):
            with self.assertRaisesRegex(memory.Invalid, "memory-id:"):
                memory.host_client_config(config, "macos-role", value)
        for render in (memory.render, lambda c: memory.host_policy(c, "macos-role"),
                       lambda c: memory.host_client_config(c, "macos-role", "ExampleMemory-abcdefghij")):
            first = render(config)
            reordered = {key: config[key] for key in reversed(config)}
            reordered["people"] = list(reversed(config["people"]))
            self.assertEqual(render(reordered), first)
            self.assertEqual(render(config), first)
        self.assertEqual(config, before)
        config["region"] = "us-west-2"
        self.assertEqual(memory.host_client_config(config, "macos-role", "ExampleMemory-abcdefghij")["region"], "us-west-2")
        config["hostRoles"][0]["proposalProject"] = "example-site"
        client = memory.host_client_config(config, "macos-role", "ExampleMemory-abcdefghij")
        self.assertEqual(client["projects"], [{"projectId": "example-site", "role": "member"}])
        self.assertEqual(statements(memory.host_policy(config, "macos-role"))["Write"]["Condition"]["ForAllValues:StringLike"],
                         {memory.KEY: ["/project/example-site/proposals/macos-agent/*"]})
        size = len(memory.render(config).encode())
        self.assertLessEqual(size, memory.TEMPLATE_LIMIT)
        with patch.object(memory, "TEMPLATE_LIMIT", size - 1):
            with self.assertRaisesRegex(memory.Invalid, "template: must be at most"):
                memory.render(config)

    def test_host_commands_from_temporary_fixture_do_not_read_credentials_or_write_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "config/memory").mkdir(parents=True)
            script = root / "scripts/aws-memory.py"
            script.write_bytes((ROOT / "scripts/aws-memory.py").read_bytes())
            config = self.host_fixture()
            for stage in memory.STAGES:
                value = config if stage == "dev" else memory.load_config(stage)
                (root / f"config/memory/{stage}.yaml").write_text(yaml.safe_dump(value))
            env = {**os.environ, "HOME": str(root / "no-home"), "AWS_PROFILE": "person-example",
                   "AWS_SHARED_CREDENTIALS_FILE": str(root / "no-credentials"), "AWS_EC2_METADATA_DISABLED": "true"}
            inventory = sorted(root.rglob("*"))
            call = lambda *args: subprocess.run([sys.executable, str(script), *args], env=env, capture_output=True, text=True, timeout=20)
            policy = call("host-policy", "--stage", "dev", "--role-suffix", "macos-role")
            self.assertEqual(policy.returncode, 0, policy.stderr)
            self.assertEqual(json.loads(policy.stdout), memory.host_policy(config, "macos-role"))
            args = ["host-client-config", "--stage", "dev", "--role-suffix", "macos-role", "--memory-id", "ExampleMemory-abcdefghij"]
            client = call(*args)
            self.assertEqual(client.returncode, 0, client.stderr)
            self.assertEqual(json.loads(client.stdout), memory.host_client_config(config, "macos-role", "ExampleMemory-abcdefghij"))
            self.assertEqual(call(*args).stdout, client.stdout)
            for command in ("host-policy", "host-client-config"):
                for stage in ("test", "prod"):
                    options = ["--memory-id", "ExampleMemory-abcdefghij"] if command == "host-client-config" else []
                    result = call(command, "--stage", stage, "--role-suffix", "macos-role", *options)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("must name an admitted host", result.stderr)
            for field, value in (("actorId", "alice"), ("roleSuffix", "macos-role*"), ("proposalProject", "missing-project")):
                malformed = copy.deepcopy(config)
                malformed["hostRoles"][0][field] = value
                (root / "config/memory/dev.yaml").write_text(yaml.safe_dump(malformed))
                for command, options in (("host-policy", []), ("host-client-config", ["--memory-id", "ExampleMemory-abcdefghij"])):
                    result = call(command, "--stage", "dev", "--role-suffix", "macos-role", *options)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(field + ":", result.stderr)
            (root / "config/memory/dev.yaml").write_text(yaml.safe_dump(config))
            for options in (["--profile", "person-example"], ["--actor", "alice"]):
                self.assertEqual(call(*args, *options).returncode, 2)
            self.assertEqual(call("client-config", "--stage", "dev", "--actor", "alice", "--memory-id",
                                  "ExampleMemory-abcdefghij", "--profile", "person-example").returncode, 2)
            self.assertEqual(sorted(root.rglob("*")), inventory)
            self.assertIn("omit actor recall", call("host-client-config", "--help").stdout)

    def test_commands_and_stale_refusal(self):
        def call(args):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = memory.main(args)
            return result, stdout.getvalue(), stderr.getvalue()
        for stage in memory.STAGES:
            self.assertEqual(call(["check", "--stage", stage])[0], 0)
        result, stdout, stderr = call(["policy", "--stage", "dev", "--actor", "alice"])
        self.assertEqual(result, 0, stderr)
        self.assertEqual(json.loads(stdout), memory.person_policy(self.config, "alice"))
        result, stdout, stderr = call(["client-config", "--stage", "dev", "--actor", "alice", "--memory-id", "ExampleMemory-abcdefghij"])
        self.assertEqual(result, 0, stderr)
        self.assertEqual(json.loads(stdout)["profile"], "tenant-ec2-memory-dev-person-alice")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            with patch.object(memory, "ROOT", root), patch.object(memory, "load_config", return_value=self.config):
                self.assertEqual(call(["check", "--stage", "dev"])[0], 1)
                self.assertEqual(call(["render", "--stage", "dev"])[0], 0)
                self.assertEqual(call(["check", "--stage", "dev"])[0], 0)
                path = root / "scripts/aws-memory-dev.cfn.yaml"
                path.write_text(path.read_text() + "# stale\n")
                result, stdout, stderr = call(["check", "--stage", "dev"])
                self.assertEqual(result, 1)
                self.assertIn("differs from the configuration", stderr)
                self.assertTrue(path.read_text().endswith("# stale\n"))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MemoryTests))
    if result.wasSuccessful():
        print("memory stack tests: pass (%d tests)" % result.testsRun)
    sys.exit(0 if result.wasSuccessful() else 1)
