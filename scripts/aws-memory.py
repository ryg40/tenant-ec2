#!/usr/bin/env python3
"""Render the stage memory stack and its review documents without an AWS call."""
import argparse
import copy
import json
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("dev", "test", "prod")
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
REGION = re.compile(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+")
IDENTITY = re.compile(r"([a-z]{2}(?:-[a-z]+)+-[0-9]+):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
COGNITO = "cognito-identity.amazonaws.com"
KEY = "bedrock-agentcore:namespace"
READ = ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"]
MUTATIONS = {"write": "BatchCreateMemoryRecords", "move": "BatchUpdateMemoryRecords", "delete": "BatchDeleteMemoryRecords"}
TEMPLATE_LIMIT = 51200
POLICY_LIMIT = 2048
HEADER_NOTE = """# Not verified: a resource policy on a memory.
# ResourcePolicy supports Runtime and Gateway only. Use the identity policies and boundary.
# https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-resourcepolicy.html
# https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_PutResourcePolicy.html
# Not verified: namespace authorization for BatchDeleteMemoryRecords. The Null condition stays mandatory.
"""


class Invalid(ValueError):
    """An input or a generated document breaks a named rule."""


def refuse(field, rule):
    raise Invalid(f"{field}: {rule}")


class ConfigLoader(yaml.SafeLoader):
    """Do not silently accept the last of two YAML fields."""


def mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            refuse("config", "field names must be strings")
        if key in result:
            refuse(key, "duplicate field")
        result[key] = loader.construct_object(value_node)
    return result


ConfigLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def fields(value, required, field, optional=()):
    if not isinstance(value, dict):
        refuse(field, "must be a mapping")
    for key in value:
        if key not in required and key not in optional:
            refuse(f"{field}.{key}", "unknown field")
    for key in required:
        if key not in value:
            refuse(f"{field}.{key}", "required field")


def identifier(value, field):
    if not isinstance(value, str) or not ID.fullmatch(value):
        refuse(field, "must match [a-z0-9][a-z0-9-]{0,63}")
    return value


def sequence(value, field):
    if not isinstance(value, list):
        refuse(field, "must be a list")
    return value


def ids(value, field):
    result = [identifier(item, field) for item in sequence(value, field)]
    if len(set(result)) != len(result):
        refuse(field, "ids must be unique")
    return result


def validate_config(value, stage):
    fields(value, ("schemaVersion", "stage", "region", "memory", "bucket", "service", "people", "projects"), "config", ("hostRoles",))
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        refuse("schemaVersion", "must be the integer 1")
    if stage not in STAGES or value["stage"] != stage:
        refuse("stage", "must match the selected stage: dev, test or prod")
    if not isinstance(value["region"], str) or not REGION.fullmatch(value["region"]) or len(value["region"]) > 32:
        refuse("region", "must be an AWS region name of at most 32 characters")
    fields(value["memory"], ("eventExpiryDays",), "memory")
    days = value["memory"]["eventExpiryDays"]
    if type(days) is not int or not 3 <= days <= 365:
        refuse("memory.eventExpiryDays", "must be an integer from 3 to 365")
    fields(value["bucket"], ("enabled",), "bucket")
    fields(value["service"], ("submit", "workerRead"), "service")
    for section, key in (("bucket", "enabled"), ("service", "submit"), ("service", "workerRead")):
        if type(value[section][key]) is not bool:
            refuse(f"{section}.{key}", "must be a boolean")
    people, pins, active = set(), set(), set()
    for index, person in enumerate(sequence(value["people"], "people")):
        field = f"people[{index}]"
        fields(person, ("actorId", "pin", "status"), field, ("iamUser",))
        actor = identifier(person["actorId"], field + ".actorId")
        if actor in people:
            refuse(field + ".actorId", "must be unique")
        people.add(actor)
        pin = person["pin"]
        if not isinstance(pin, str) or not re.fullmatch(r"XYZ[0-9]{4}", pin):
            refuse(field + ".pin", "must be XYZ followed by four digits")
        if pin in pins:
            refuse(field + ".pin", "must be unique, including offboarded people")
        pins.add(pin)
        if person["status"] not in ("active", "offboarded"):
            refuse(field + ".status", "must be active or offboarded")
        if type(person.get("iamUser", True)) is not bool:
            refuse(field + ".iamUser", "must be a boolean")
        if person["status"] == "active":
            active.add(actor)
    projects = set()
    for index, project in enumerate(sequence(value["projects"], "projects")):
        field = f"projects[{index}]"
        fields(project, ("projectId", "maintainers", "members"), field, ("deleters",))
        project_id = identifier(project["projectId"], field + ".projectId")
        if project_id in projects:
            refuse(field + ".projectId", "must be unique")
        projects.add(project_id)
        members = ids(project["members"], field + ".members")
        maintainers = ids(project["maintainers"], field + ".maintainers")
        deleters = ids(project.get("deleters", []), field + ".deleters")
        if not set(members) <= active:
            refuse(field + ".members", "each member must be an active person")
        if not maintainers:
            refuse(field + ".maintainers", "at least one maintainer is required")
        for name, group in (("maintainers", maintainers), ("deleters", deleters)):
            if not set(group) <= set(members):
                refuse(field + "." + name, "each person must be an active member of this project")
    roles, agents = set(), set()
    for index, host in enumerate(sequence(value.get("hostRoles", []), "hostRoles")):
        field = f"hostRoles[{index}]"
        fields(host, ("roleSuffix", "actorId", "proposalProject"), field)
        if host["roleSuffix"] != "macos-role":
            refuse(field + ".roleSuffix", "must be macos-role")
        if host["roleSuffix"] in roles:
            refuse(field + ".roleSuffix", "must be unique")
        roles.add(host["roleSuffix"])
        actor = identifier(host["actorId"], field + ".actorId")
        if actor in people:
            refuse(field + ".actorId", "must not name an active or offboarded person")
        if actor in agents:
            refuse(field + ".actorId", "must be unique")
        agents.add(actor)
        project = identifier(host["proposalProject"], field + ".proposalProject")
        if project not in projects:
            refuse(field + ".proposalProject", "must name a configured project")
    config = copy.deepcopy(value)
    config.setdefault("hostRoles", [])
    return config


def bind_identities(config, overlay):
    """Merge the ignored identity overlay of a stage: one Cognito identity ID per bound person.

    Only the bound render calls this. The tracked render has no identity, so there a person
    with iamUser: false gets no user and no role. Refusals name people, never an identity ID.
    """
    config = copy.deepcopy(config)
    people = {person["actorId"]: person for person in config["people"]}
    fields(overlay, ("schemaVersion", "stage", "identities"), "identities")
    if type(overlay["schemaVersion"]) is not int or overlay["schemaVersion"] != 1:
        refuse("identities.schemaVersion", "must be the integer 1")
    if overlay["stage"] != config["stage"]:
        refuse("identities.stage", "must match the selected stage")
    if not isinstance(overlay["identities"], dict):
        refuse("identities.identities", "must be a mapping from actorId to identity ID")
    owners = {}
    for actor, value in sorted(overlay["identities"].items()):
        field = "identities." + identifier(actor, "identities.actorId")
        if actor not in people:
            refuse(field, "must name a person of the stage file")
        if people[actor]["status"] != "active":
            refuse(field, "must name an active person, not an offboarded one")
        match = IDENTITY.fullmatch(value) if isinstance(value, str) else None
        if not match:
            refuse(field, "must have the form <region>:<uuid> with a lowercase uuid")
        if match.group(1) != config["region"]:
            refuse(field, "must be in the region of the stage")
        if value in owners:
            refuse(field, f"shares one identity with {owners[value]}")
        owners[value] = actor
        people[actor]["identityId"] = value
    for person in config["people"]:
        if person["status"] == "active" and not person.get("iamUser", True) and "identityId" not in person:
            refuse(f"people.{person['actorId']}.iamUser", "false needs an identity in the overlay, or the person has no access")
    return config


def identities_path(stage, root=None):
    return (root or ROOT) / ".local" / "memory" / f"{stage}.identities.yaml"


def bound_template_path(stage, root=None):
    return (root or ROOT) / ".local" / "memory" / f"aws-memory-{stage}.cfn.yaml"


def read_yaml(path, field):
    try:
        if path.is_symlink():
            raise OSError
        return yaml.load(path.read_text(encoding="utf-8"), Loader=ConfigLoader)
    except (OSError, UnicodeError, yaml.YAMLError):
        refuse(field, "must be readable, valid YAML and no symbolic link")


def load_config(stage, root=None, identities=None):
    """Load the tracked stage file; with an overlay path, merge its identities too."""
    value = read_yaml((root or ROOT) / "config" / "memory" / f"{stage}.yaml", f"config/memory/{stage}.yaml")
    config = validate_config(value, stage)
    if identities is None:
        return config
    # An empty overlay file gives None, which fields() refuses like any other invalid overlay.
    return bind_identities(config, read_yaml(identities, f".local/memory/{stage}.identities.yaml"))


def active_person(config, actor):
    identifier(actor, "actor")
    for person in config["people"]:
        if person["actorId"] == actor and person["status"] == "active":
            return person
    refuse("actor", "must name an active person")


def unique(values):
    return list(dict.fromkeys(values))


def person_scope(config, actor):
    active_person(config, actor)
    scope = {"read": ["/shared/*", f"/actor/{actor}/*"], "write": [], "move": [], "delete": []}
    for project in config["projects"]:
        if actor not in project["members"]:
            continue
        prefix = f"/project/{project['projectId']}"
        scope["read"] += [prefix + "/shared/*", prefix + f"/proposals/{actor}/*"]
        scope["write"].append(prefix + f"/proposals/{actor}/*")
        if actor in project["maintainers"]:
            scope["read"] += [prefix + "/proposals/*", prefix + "/rejected/*"]
            scope["move"] += [prefix + "/proposals/*", prefix + "/shared/*", prefix + "/rejected/*"]
        if actor in project.get("deleters", []):
            scope["delete"].append(prefix + "/*")
    return {key: unique(patterns) for key, patterns in scope.items()}


def service_scope(kind):
    if kind == "submit":
        return {"read": ["/shared/*", "/actor/*"], "write": ["/shared/*", "/actor/*"],
                "move": ["/shared/*", "/actor/*"], "delete": []}
    return {"read": ["/shared/*", "/project/*/shared/*"], "write": [], "move": [], "delete": []}


def memory_policy(scope, resource):
    statements = []
    if scope["read"]:
        statements.append({"Sid": "Read", "Effect": "Allow", "Action": READ.copy(), "Resource": resource,
                           "Condition": {"StringLike": {KEY: scope["read"]}}})
    for kind, action in MUTATIONS.items():
        if scope[kind]:
            statements.append({"Sid": kind.capitalize(), "Effect": "Allow", "Action": ["bedrock-agentcore:" + action],
                               "Resource": resource, "Condition": {"ForAllValues:StringLike": {KEY: scope[kind]},
                                                                    "Null": {KEY: "false"}}})
    return {"Version": "2012-10-17", "Statement": statements}


def s3_scope(config, actor=None, service=None):
    if actor is None:
        if service == "submit":
            return {"read": ["projects/*", "users/*"], "write": ["projects/*", "users/*"], "delete": []}
        if service == "worker":
            return {"read": ["projects/*"], "write": [], "delete": []}
        refuse("service", "must be submit or worker for a service bucket policy")
    active_person(config, actor)
    projects = [p for p in config["projects"] if actor in p["members"]]
    prefixes = [f"projects/{p['projectId']}/*" for p in projects] + [f"users/{actor}/*"]
    deletes = [f"users/{actor}/*"] + [f"projects/{p['projectId']}/*" for p in projects if actor in p.get("deleters", [])]
    return {"read": prefixes, "write": prefixes.copy(), "delete": deletes}


def sub(text):
    return {"Fn::Sub": text}


def ref(name):
    return {"Ref": name}


def att(name, attribute):
    return {"Fn::GetAtt": [name, attribute]}


def policy_document(config, scope, actor=None, service=None):
    policy = memory_policy(scope, att("Memory", "MemoryArn"))
    if not config["bucket"]["enabled"]:
        return policy
    scope = s3_scope(config, actor=actor, service=service)
    # Shared read/write resources use one statement; this keeps small inline policies usable.
    read_write = [p for p in scope["write"] if p in scope["read"]]
    groups = [("s3:GetObject", [p for p in scope["read"] if p not in read_write]),
              (["s3:GetObject", "s3:PutObject"], read_write),
              ("s3:PutObject", [p for p in scope["write"] if p not in read_write]),
              ("s3:DeleteObject", scope["delete"])]
    prefixes = scope["read"]
    if prefixes:
        policy["Statement"].append({"Effect": "Allow", "Action": "s3:ListBucket", "Resource": att("Bucket", "Arn"),
                                    "Condition": {"StringLike": {"s3:prefix": prefixes}}})
    for actions, patterns in groups:
        if patterns:
            resources = [sub("${Bucket.Arn}/" + p) for p in patterns]
            policy["Statement"].append({"Effect": "Allow", "Action": actions,
                                        "Resource": resources[0] if len(resources) == 1 else resources})
    return policy


def project_limit(config):
    limit = 31
    for person in config["people"]:
        if person["status"] != "active":
            continue
        # The role name adds the suffix -role; only a bound person has a role.
        suffix = "-role" if "identityId" in person else ""
        available = 64 - len(f"-memory-{config['stage']}-person-{person['actorId']}{suffix}")
        if available < len("tenant-ec2"):
            kind = "role" if suffix else "user"
            refuse(f"people.{person['actorId']}.actorId", f"the IAM {kind} name exceeds 64 characters with the default Project")
        limit = min(limit, available)
    return limit


def policy_size(policy, config):
    """Count resolved JSON, not the shorter CloudFormation references.

    Bound Project, partition and region lengths, plus the generated memory id suffix.
    No real account or memory id is needed for this offline upper bound.
    """
    project = "p" * project_limit(config)
    partition = "p" * 10
    region = "r" * 32
    account = "0" * 12
    memory_id = project + "_memory_" + config["stage"] + "-" + "x" * 10
    memory_arn = f"arn:{partition}:bedrock-agentcore:{region}:{account}:memory/{memory_id}"
    bucket_arn = f"arn:{partition}:s3:::{project}-memory-{config['stage']}"

    def resolve(value):
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            if value == att("Memory", "MemoryArn"):
                return memory_arn
            if value == att("Bucket", "Arn"):
                return bucket_arn
            if set(value) == {"Fn::Sub"}:
                return value["Fn::Sub"].replace("${Bucket.Arn}", bucket_arn)
            return {key: resolve(item) for key, item in value.items()}
        return value

    return len(json.dumps(resolve(policy), separators=(",", ":"), ensure_ascii=True))


def person_policy(config, actor):
    document = policy_document(config, person_scope(config, actor), actor)
    size = policy_size(document, config)
    if size > POLICY_LIMIT:
        refuse(f"people.{actor}.policy", f"at most {POLICY_LIMIT} characters without white space; resolved upper bound is {size}")
    return document


def client_config(config, actor, memory_id):
    active_person(config, actor)
    if not isinstance(memory_id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", memory_id):
        refuse("memory-id", "must be a memory identifier of at most 128 characters")
    return {"schemaVersion": 1, "memoryId": memory_id, "region": config["region"],
            "profile": f"tenant-ec2-memory-{config['stage']}-person-{actor}", "actorId": actor,
            "projects": [{"projectId": p["projectId"], "role": "maintainer" if actor in p["maintainers"] else "member"}
                         for p in config["projects"] if actor in p["members"]]}


def host_entry(config, role_suffix):
    config = validate_config(config, config["stage"])
    if role_suffix != "macos-role":
        refuse("role-suffix", "must be macos-role")
    for host in config["hostRoles"]:
        if host["roleSuffix"] == role_suffix:
            return host
    refuse("role-suffix", "must name an admitted host in the selected stage")


def host_policy(config, role_suffix):
    """Render a policy fragment for the separate host stack, not a stage user.

    MemoryArn and MemoryBucketName come from the selected stage's private outputs.
    The caller must compare these inputs with the administrator-approved boundary.
    """
    host = host_entry(config, role_suffix)
    actor, project = host["actorId"], host["proposalProject"]
    scope = {"read": ["/shared/*", "/project/*/shared/*"],
             "write": [f"/project/{project}/proposals/{actor}/*"], "move": [], "delete": []}
    policy = memory_policy(scope, ref("MemoryArn"))
    # Keep the host read contract separate from the unchanged person/service actions.
    # Not verified: GetMemoryRecord supplies the required namespace condition key.
    policy["Statement"][0]["Action"].append("bedrock-agentcore:GetMemoryRecord")
    if config["bucket"]["enabled"]:
        bucket = "arn:${AWS::Partition}:s3:::${MemoryBucketName}"
        policy["Statement"] += [
            {"Sid": "BucketList", "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": sub(bucket),
             "Condition": {"StringLike": {"s3:prefix": ["projects/*", f"users/{actor}/*"]}}},
            {"Sid": "BucketRead", "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": sub(bucket + "/projects/*")},
            {"Sid": "BucketWrite", "Effect": "Allow", "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
             "Resource": sub(bucket + f"/users/{actor}/*")},
        ]
    return policy


def host_client_config(config, role_suffix, memory_id):
    """The service adapter must omit the ordinary person core's actor recall.

    This config does not change the core's recall behavior or supply credentials.
    """
    host = host_entry(config, role_suffix)
    if not isinstance(memory_id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", memory_id):
        refuse("memory-id", "must be a memory identifier of at most 128 characters")
    return {"schemaVersion": 1, "memoryId": memory_id, "region": config["region"],
            "profile": "ec2-instance", "actorId": host["actorId"],
            "projects": [{"projectId": host["proposalProject"], "role": "member"}]}


def boundary(config):
    statements = [{"Sid": "Memory", "Effect": "Allow", "Action": READ + ["bedrock-agentcore:GetMemoryRecord"] +
                   ["bedrock-agentcore:" + action for action in MUTATIONS.values()], "Resource": att("Memory", "MemoryArn")}]
    if config["bucket"]["enabled"]:
        for name, actions, resource in (("BucketList", ["s3:ListBucket"], att("Bucket", "Arn")),
                                        ("BucketObjects", ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"], sub("${Bucket.Arn}/*"))):
            statements.append({"Sid": name, "Effect": "Allow", "Action": actions, "Resource": resource,
                               "Condition": {"StringEquals": {"s3:ResourceAccount": ref("AWS::AccountId")}}})
    statements.append({"Sid": "Identity", "Effect": "Allow", "Action": "sts:GetCallerIdentity", "Resource": "*"})
    return {"Version": "2012-10-17", "Statement": statements}


def person_logical_id(actor):
    # Hex is injective: unlike title case, it cannot collapse a-b and ab into one user.
    return "Person" + actor.encode("ascii").hex()


def user_name(suffix):
    return sub("${Project}-memory-${Stage}-" + suffix)


def role_trust(identity):
    """One statement: the basic-flow web identity of exactly one bound person (spike 127 shape)."""
    pool = {"Fn::ImportValue": sub("${Project}-memory-identity-${Stage}:IdentityPoolId")}
    return {"Version": "2012-10-17", "Statement": [{
        "Sid": "CognitoIdentity", "Effect": "Allow", "Principal": {"Federated": COGNITO},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {"StringEquals": {COGNITO + ":aud": pool, COGNITO + ":sub": identity},
                      "ForAnyValue:StringEquals": {COGNITO + ":amr": "authenticated"}}}]}


def template(config, check_policies=True):
    stage = config["stage"]
    resources = {}
    tags = [{"Key": "Project", "Value": ref("Project")}, {"Key": "Stage", "Value": ref("Stage")}]
    retention = {"DeletionPolicy": "RetainExceptOnCreate", "UpdateReplacePolicy": "Retain"}
    resources["Memory"] = {"Type": "AWS::BedrockAgentCore::Memory", **retention, "Properties": {
        "Name": {"Fn::Join": ["_", [{"Fn::Join": ["_", {"Fn::Split": ["-", ref("Project")]}]}, "memory", ref("Stage")]]},
        "Description": "The shared memory of this stage.", "EventExpiryDuration": config["memory"]["eventExpiryDays"],
        "Tags": {"Project": ref("Project"), "Stage": ref("Stage")}}}
    if config["bucket"]["enabled"]:
        resources["Bucket"] = {"Type": "AWS::S3::Bucket", **retention, "Properties": {
            "BucketName": sub("${Project}-memory-${Stage}"),
            "PublicAccessBlockConfiguration": {name: True for name in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")},
            "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}, "BucketKeyEnabled": True}]},
            "VersioningConfiguration": {"Status": "Enabled"},
            "LifecycleConfiguration": {"Rules": [{"Id": "expire-old-versions-and-uploads", "Status": "Enabled",
                                                  "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                                                  "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]}, "Tags": tags}}
    resources["Boundary"] = {"Type": "AWS::IAM::ManagedPolicy", "Properties": {
        "ManagedPolicyName": sub("${Project}-memory-${Stage}-boundary"),
        "Description": "The permission ceiling for the users of this memory stage.", "PolicyDocument": boundary(config)}}
    people, roles = [], []
    principals = [sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:root"),
                  sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:role/${AdministratorRole}"),
                  {"Fn::If": ["HasAdministratorUser", sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:user/${AdministratorUser}"),
                              ref("AWS::NoValue")]}]
    for person in sorted(config["people"], key=lambda p: p["actorId"]):
        if person["status"] != "active":
            continue
        actor = person["actorId"]
        logical_id = person_logical_id(actor)
        policy = person_policy(config, actor) if check_policies else policy_document(config, person_scope(config, actor), actor)
        person_tags = [{"Key": "purpose", "Value": "agentcore-memory"}, {"Key": "actorId", "Value": actor},
                       {"Key": "pin", "Value": person["pin"]}, {"Key": "Stage", "Value": ref("Stage")}]
        if person.get("iamUser", True):
            resources[logical_id] = {"Type": "AWS::IAM::User", "Properties": {
                "UserName": user_name("person-" + actor), "PermissionsBoundary": ref("Boundary"),
                "Policies": [{"PolicyName": "agentcore-memory", "PolicyDocument": policy}], "Tags": person_tags}}
            people.append(ref(logical_id))
            principals.append(att(logical_id, "Arn"))
        if "identityId" in person:
            resources[logical_id + "Role"] = {"Type": "AWS::IAM::Role", "Properties": {
                "RoleName": user_name("person-" + actor + "-role"), "PermissionsBoundary": ref("Boundary"),
                "Description": "The person role of one bound Cognito identity.", "MaxSessionDuration": 3600,
                "AssumeRolePolicyDocument": role_trust(person["identityId"]),
                "Policies": [{"PolicyName": "agentcore-memory", "PolicyDocument": copy.deepcopy(policy)}],
                "Tags": copy.deepcopy(person_tags)}}
            roles.append(ref(logical_id + "Role"))
            principals.append(att(logical_id + "Role", "Arn"))
    outputs = {"MemoryId": {"Description": "The identifier of the memory.", "Value": att("Memory", "MemoryId")},
               "MemoryArn": {"Description": "The ARN of the memory.", "Value": att("Memory", "MemoryArn")},
               "BoundaryArn": {"Description": "The ARN of the permission ceiling.", "Value": ref("Boundary")},
               "PersonUsers": {"Description": "The active person user names, separated by commas.",
                               "Value": {"Fn::Join": [",", people]} if people else ""}}
    if roles:
        outputs["PersonRoles"] = {"Description": "The bound person role names, separated by commas.",
                                  "Value": {"Fn::Join": [",", roles]}}
    for kind, switch in (("submit", "submit"), ("worker", "workerRead")):
        label = kind.capitalize()
        output = {"Description": f"The {kind} user name, or empty when disabled.", "Value": ""}
        if config["service"][switch]:
            resources[label + "Policy"] = {"Type": "AWS::IAM::ManagedPolicy", "Properties": {
                "ManagedPolicyName": user_name(kind), "Description": f"The scoped {kind} permissions of this memory stage.",
                "PolicyDocument": policy_document(config, service_scope(kind), service=kind)}}
            resources[label + "User"] = {"Type": "AWS::IAM::User", "Properties": {
                "UserName": user_name(kind), "PermissionsBoundary": ref("Boundary"),
                "ManagedPolicyArns": [ref(label + "Policy")], "Tags": tags + [{"Key": "purpose", "Value": "agentcore-memory"}]}}
            principals.append(att(label + "User", "Arn"))
            output["Value"] = ref(label + "User")
        outputs[label + "User"] = output
    for host in sorted(config.get("hostRoles", []), key=lambda h: h["roleSuffix"]):
        principals.append(sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:role/${Project}-" + host["roleSuffix"]))
    if config["bucket"]["enabled"]:
        bucket_resources = [att("Bucket", "Arn"), sub("${Bucket.Arn}/*")]
        resources["BucketPolicy"] = {"Type": "AWS::S3::BucketPolicy", **retention, "Properties": {
            "Bucket": ref("Bucket"), "PolicyDocument": {"Version": "2012-10-17", "Statement": [
                {"Sid": "DenyNoTls", "Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": bucket_resources,
                 "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
                {"Sid": "DenyOutsiders", "Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": bucket_resources,
                 "Condition": {"ArnNotLike": {"aws:PrincipalArn": principals}}}]}}}
        outputs["BucketName"] = {"Description": "The name of the memory files bucket.", "Value": ref("Bucket")}
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": f"The shared memory, files and identities of the {stage} stage.",
            "Parameters": {
                "Project": {"Type": "String", "Default": "tenant-ec2", "AllowedPattern": "[a-z][a-z0-9-]{2,30}",
                            "MinLength": 3, "MaxLength": project_limit(config),
                            "ConstraintDescription": "Use lower case letters, digits and hyphens, starting with a letter, within the name length limit.",
                            "Description": "The name prefix of the memory, bucket, users and policies."},
                "Stage": {"Type": "String", "Default": stage, "AllowedValues": list(STAGES),
                          "ConstraintDescription": "Use dev, test or prod, matching the generated stage file.",
                          "Description": "The stage of the source configuration. Use its matching template."},
                "AdministratorRole": {"Type": "String", "Default": "Administrator", "AllowedPattern": "[A-Za-z0-9+=,.@_-]{1,64}",
                                      "ConstraintDescription": "Use one IAM role name without a path or wildcard.",
                                      "Description": "The administrator role that can access the bucket."},
                "AdministratorUser": {"Type": "String", "Default": "", "AllowedPattern": "[A-Za-z0-9+=,.@_-]{0,64}",
                                      "ConstraintDescription": "Use an empty value or one IAM user name without a path or wildcard.",
                                      "Description": "The optional administrator user exempt from the bucket deny rule. Require MFA separately."}},
            "Conditions": {"HasAdministratorUser": {"Fn::Not": [{"Fn::Equals": [ref("AdministratorUser"), ""]}]}},
            "Rules": {"StageMatchesConfig": {"Assertions": [{"Assert": {"Fn::Equals": [ref("Stage"), stage]},
                                                             "AssertDescription": "Stage must match the generated configuration."}]}},
            "Resources": resources, "Outputs": outputs}


class TemplateDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def bound(config):
    return any("identityId" in person for person in config["people"])


def serialize_template(config, document):
    source = f"config/memory/{config['stage']}.yaml"
    if bound(config):
        source += f" and .local/memory/{config['stage']}.identities.yaml"
    header = f"# Generated by scripts/aws-memory.py from {source}. Do not edit.\n"
    return header + HEADER_NOTE + yaml.dump(document, Dumper=TemplateDumper, sort_keys=True, allow_unicode=False, width=120)


def render(config):
    text = serialize_template(config, template(config))
    if len(text.encode("utf-8")) > TEMPLATE_LIMIT:
        # Count the sorted active prefix that fits this exact policy layout, not an average user.
        candidate = copy.deepcopy(config)
        active = sorted((p for p in config["people"] if p["status"] == "active"), key=lambda p: p["actorId"])
        low, high = 0, len(active)
        while low < high:
            count = (low + high + 1) // 2
            candidate["people"] = active[:count]
            size = len(serialize_template(candidate, template(candidate, check_policies=False)).encode("utf-8"))
            if size <= TEMPLATE_LIMIT:
                low = count
            else:
                high = count - 1
        users = sum(1 for p in active[:low] if p.get("iamUser", True))
        roles = sum(1 for p in active[:low] if "identityId" in p)
        refuse("template", f"must be at most {TEMPLATE_LIMIT} bytes; {len(text.encode('utf-8'))} bytes; {low} active people fit "
               f"in sorted actor order ({users} users and {roles} roles)")
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("render", "check", "policy", "client-config", "host-policy", "host-client-config"):
        help_text = None
        if command == "host-client-config":
            help_text = "The service adapter must omit actor recall from the ordinary person core. No credentials are read or written."
        subparser = commands.add_parser(command, description=help_text)
        subparser.add_argument("--stage", required=True, choices=STAGES)
        if command in ("policy", "client-config"):
            subparser.add_argument("--actor", required=True)
        if command in ("host-policy", "host-client-config"):
            subparser.add_argument("--role-suffix", required=True)
        if command in ("client-config", "host-client-config"):
            subparser.add_argument("--memory-id", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.stage)
        if args.command in ("render", "check"):
            # The tracked template never holds an identity. A bound copy goes to the ignored .local/memory/.
            targets = [(render(config), ROOT / "scripts" / f"aws-memory-{args.stage}.cfn.yaml",
                        f"scripts/aws-memory-{args.stage}.cfn.yaml")]
            overlay = identities_path(args.stage)
            if overlay.exists() or overlay.is_symlink():
                bound_config = load_config(args.stage, identities=overlay)
                targets.append((render(bound_config), bound_template_path(args.stage),
                                f".local/memory/aws-memory-{args.stage}.cfn.yaml"))
            for text, path, label in targets:
                if args.command == "render":
                    if path.parent != ROOT / "scripts":
                        for directory in (path.parent.parent, path.parent):
                            if directory.is_symlink():
                                refuse(label, "the directory must not be a symbolic link")
                            directory.mkdir(mode=0o700, exist_ok=True)
                        if path.is_symlink():
                            refuse(label, "must not be a symbolic link")
                        path.touch(mode=0o600)
                        path.chmod(0o600)
                    path.write_text(text, encoding="utf-8")
                    print(f"memory render {args.stage}: pass ({len(text.encode('utf-8'))} bytes, {label})")
                elif path.is_symlink() or not path.is_file() or path.read_bytes() != text.encode("utf-8"):
                    refuse(label, "differs from the configuration; run render")
            if args.command == "check":
                print(f"memory check {args.stage}: pass" + (" (with the identity overlay)" if len(targets) > 1 else ""))
        else:
            if args.command == "policy":
                document = person_policy(config, args.actor)
            elif args.command == "host-policy":
                document = host_policy(config, args.role_suffix)
            elif args.command == "host-client-config":
                document = host_client_config(config, args.role_suffix, args.memory_id)
            else:
                document = client_config(config, args.actor, args.memory_id)
            print(json.dumps(document, indent=2, sort_keys=True))
        return 0
    except (Invalid, OSError) as error:
        print(f"memory: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
