#!/bin/sh
# The AWS provisioner credential of tenant-ec2: one IAM user with a scoped
# policy (scripts/aws-provisioner.cfn.yaml). The stack of the host is deployed
# with this credential, not with an administrator.
# See docs/credentials.md, section "The AWS provisioner".
#
# Usage: scripts/aws-provisioner.sh <command>
#   deploy              create or update the IAM stack; needs the administrator profile
#   key                 make a new key, save it, write the profile, delete each older key;
#                       needs the administrator profile
#   vault               copy the saved key to Vault (home-lab mode only)
#   verify              check the identity and the limits of the provisioner profile
#   credential-process  read the saved home key for the CLI; needs no storage setting
#   selftest            test storage with isolated stubs; no deploy or key command
#
# The administrator profile is a console session: aws login --remote --profile <name>.
# EC2_CREDENTIAL_STORES=aws is the default for a profile without a credential helper.
# It stores the key in the AWS CLI profile only. It never writes a helper line.
# EC2_CREDENTIAL_STORES=home-lab enables the shell save command and the Vault copy.
# That mode saves TENANT_EC2_AWS_ACCESS_KEY_ID and TENANT_EC2_AWS_SECRET_ACCESS_KEY.
# If the selected profile has a helper, `key` needs an explicit choice of either mode.
# Existing helper lines work unchanged: credential-process needs no storage setting.
# It returns the key to the CLI on stdout. Never run it in a transcript.
#
# The optional Vault copy is one KV v2 secret with the two names as fields.
# Use an exact reader policy that excludes this secret from each workload role.
# The copy needs VAULT_PROVISIONER_TOKEN in a login shell; without the token,
# `key` makes no copy and says so.
#
# Optional env: EC2_PROJECT=tenant-ec2 EC2_AWS_REGION=us-east-1
#               EC2_AWS_PROFILE=$EC2_PROJECT            (the provisioner profile)
#               EC2_AWS_ADMIN_PROFILE=$EC2_PROJECT-admin
#               EC2_SECRET_SAVE=bw-session-update.sh    (gets the name as its argument
#                                                        and the value on stdin)
#               EC2_VAULT_SECRET=kv/stacks/$EC2_PROJECT-provisioner
# EC2_PROJECT is the name prefix of the provisioner policy. The script does not read
# EC2_STACK_NAME: that name is the stack of the host (scripts/aws-host.sh).

set +x
set -eu
umask 077

here=$(cd "$(dirname "$0")" && pwd)
project=${EC2_PROJECT:-tenant-ec2}
region=${EC2_AWS_REGION:-us-east-1}
profile=${EC2_AWS_PROFILE:-$project}
admin=${EC2_AWS_ADMIN_PROFILE:-$project-admin}
stores=${EC2_CREDENTIAL_STORES-aws}
save=${EC2_SECRET_SAVE:-bw-session-update.sh}
user=$project-provisioner
# The IAM stack is not a `$project-*` stack, so the provisioner cannot change it.
stack=bootstrap-$project
id_name=TENANT_EC2_AWS_ACCESS_KEY_ID
secret_name=TENANT_EC2_AWS_SECRET_ACCESS_KEY
vault_path=${EC2_VAULT_SECRET:-kv/stacks/$project-provisioner}

die() {
    echo "aws-provisioner: $*" >&2
    exit 1
}

case $stores in
    aws|home-lab) ;;
    *) die "EC2_CREDENTIAL_STORES must be aws or home-lab" ;;
esac

require_home_stores() {
    [ "$stores" = home-lab ] || die "this command needs EC2_CREDENTIAL_STORES=home-lab"
}

# Inspect only the selected profile before any key operation. Do not invoke its helper.
check_key_stores() {
    [ "${EC2_CREDENTIAL_STORES+x}" != x ] || return 0
    python3 - "$profile" <<'PY'
import configparser
import os
from pathlib import Path
import sys

profile = sys.argv[1]
files = (
    ("AWS_CONFIG_FILE", "config", "default" if profile == "default" else "profile " + profile),
    ("AWS_SHARED_CREDENTIALS_FILE", "credentials", profile),
)
try:
    for name, filename, section in files:
        path = Path(os.environ.get(name, str(Path.home() / ".aws" / filename)))
        cfg = configparser.RawConfigParser()
        try:
            with path.open() as stream:
                cfg.read_file(stream)
        except FileNotFoundError:
            continue
        if cfg.has_option(section, "credential_process"):
            raise SystemExit("aws-provisioner: this profile has a credential helper; "
                             "set EC2_CREDENTIAL_STORES=aws or EC2_CREDENTIAL_STORES=home-lab before key")
except (OSError, UnicodeError, configparser.Error):
    raise SystemExit("aws-provisioner: cannot check the selected profile; no key operation runs")
PY
}

# Update only this profile. Secrets arrive on stdin, not in process arguments.
# Replace each file atomically and keep unrelated profiles and settings.
write_profile() {
    printf '%s\n%s\n' "$id" "$secret" | python3 -c '
import configparser, os, shlex, sys, tempfile
from pathlib import Path
# Never expose a malformed credential file through a Python traceback.
def quiet_error(*_):
    print("aws-provisioner: cannot write the selected profile", file=sys.stderr)
sys.excepthook = quiet_error
profile, region, mode, helper = sys.argv[1:]
key_id, secret = sys.stdin.read().splitlines()
paths = [Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", str(Path.home()/".aws/credentials"))),
         Path(os.environ.get("AWS_CONFIG_FILE", str(Path.home()/".aws/config")))]
if paths[0].resolve() == paths[1].resolve():
    raise SystemExit("aws-provisioner: config and credentials must be separate files")
sections = [profile, "default" if profile == "default" else "profile " + profile]
files = []
for path, section in zip(paths, sections):
    cfg = configparser.RawConfigParser()
    if path.exists():
        with path.open() as stream:
            cfg.read_file(stream)
    if not cfg.has_section(section):
        cfg.add_section(section)
    for name in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token",
                 "aws_security_token", "credential_process", "role_arn", "source_profile",
                 "credential_source", "web_identity_token_file", "sso_session",
                 "sso_start_url", "sso_region", "sso_account_id", "sso_role_name", "login_session"):
        cfg.remove_option(section, name)
    files.append(cfg)
if mode == "aws":
    files[0].set(sections[0], "aws_access_key_id", key_id)
    files[0].set(sections[0], "aws_secret_access_key", secret)
else:
    files[1].set(sections[1], "credential_process",
                 "env EC2_CREDENTIAL_STORES=home-lab " + shlex.quote(helper) + " credential-process")
files[1].set(sections[1], "region", region)
files[1].set(sections[1], "output", "json")
for path, cfg in zip(paths, files):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".provisioner-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            cfg.write(stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
' "$profile" "$region" "$stores" "$here/aws-provisioner.sh"
}

persist_key() {
    if [ "$stores" = home-lab ]; then
        save_value "$id_name" "$id" && save_value "$secret_name" "$secret" || return 1
    fi
    write_profile
}

copy_optional_vault() {
    [ "$stores" = home-lab ] || return 0
    echo "==> writing the optional Vault copy"
    vault_copy || {
        [ $? -eq 10 ] || die "no Vault copy; run: scripts/aws-provisioner.sh vault"
        echo "no VAULT_PROVISIONER_TOKEN in a login shell: no Vault copy"
    }
}

as_admin() {
    aws --profile "$admin" --region "$region" "$@"
}

as_provisioner() {
    aws --profile "$profile" --region "$region" "$@"
}

save_value() {
    printf '%s' "$2" | "$save" "$1"
}

# Set `id` and `secret` to the saved key.
# A shell from before the last `key` command holds no key or the deleted key. Thus read
# the values from a new login shell first, and from this environment only without one.
read_key() {
    pair=$(bash -lc "printf '%s\t%s' \"\${$id_name:-}\" \"\${$secret_name:-}\"" 2>/dev/null | tail -n 1) ||
        pair=
    id=${pair%%	*}
    secret=${pair#*	}
    if [ -z "$id" ] || [ -z "$secret" ] || [ "$id" = "$pair" ]; then
        eval "id=\${$id_name:-} secret=\${$secret_name:-}"
    fi
    [ -n "$id" ] && [ -n "$secret" ] ||
        die "$id_name or $secret_name is not set (scripts/aws-provisioner.sh key)"
}

# Write `id` and `secret` to the Vault secret and compare the copy. Each value goes on stdin.
# Return 10 when no login shell has the Vault provisioner token.
vault_copy() {
    require_home_stores
    token=$(bash -lc 'printf %s "${VAULT_PROVISIONER_TOKEN:-}"' 2>/dev/null) || token=
    [ -n "$token" ] || return 10
    [ -n "${VAULT_ADDR:-}" ] || VAULT_ADDR=$(bash -lc 'printf %s "${VAULT_ADDR:-}"')
    export VAULT_ADDR
    # `put` replaces the secret, so it holds the two fields only.
    printf '%s' "$id" | VAULT_TOKEN=$token vault kv put -field=version "$vault_path" "$id_name=-" >/dev/null ||
        return 1
    printf '%s' "$secret" |
        VAULT_TOKEN=$token vault kv patch -method=rw -field=version "$vault_path" "$secret_name=-" >/dev/null ||
        return 1
    [ "$(VAULT_TOKEN=$token vault kv get -field="$id_name" "$vault_path")" = "$id" ] &&
        [ "$(VAULT_TOKEN=$token vault kv get -field="$secret_name" "$vault_path")" = "$secret" ] ||
        die "the Vault copy at $vault_path differs from the saved key"
}

case ${1:-} in
selftest)
    python3 - "$here/aws-provisioner.sh" <<'PY'
import configparser
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix="provisioner-test-") as directory:
    root = Path(directory)
    tools = root / "bin"
    tools.mkdir()
    library = root / "functions.sh"
    library.write_text(source.read_text().split('\ncase ${1:-} in\n', 1)[0])
    stub = '''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FIXTURE"])
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root/"calls").open("a") as log:
    log.write(tool + " " + ("login" if tool == "bash" else " ".join(args)) + "\\n")
if tool == "aws":
    raise SystemExit("test refuses every AWS call")
if tool == "save":
    if os.environ.get("FAIL_SAVE"):
        raise SystemExit(1)
    (root/args[0]).write_text(sys.stdin.read())
elif tool == "bash":
    command = args[-1]
    if "VAULT_PROVISIONER_TOKEN" in command:
        print("" if os.environ.get("NO_VAULT_TOKEN") else "fixture-vault", end="")
    elif "VAULT_ADDR" in command:
        print("https://vault.example.test", end="")
    else:
        print("fixture-id\\tfixture-secret", end="")
elif tool == "vault":
    assert os.environ["VAULT_TOKEN"] == "fixture-vault"
    state = root/"vault-state.json"
    data = json.loads(state.read_text()) if state.exists() else {}
    if args[1] in ("put", "patch"):
        if args[1] == "put":
            data = {}
        data[args[-1].removesuffix("=-")] = sys.stdin.read()
        state.write_text(json.dumps(data))
    elif args[1] == "get":
        print(data[args[2].removeprefix("-field=")], end="")
    else:
        raise SystemExit("unexpected Vault command")
'''
    for name in ("aws", "vault", "save", "bash"):
        path = tools / name
        path.write_text(stub)
        path.chmod(0o755)
    env = {"PATH": str(tools) + ":/usr/bin:/bin", "HOME": str(root),
           "FIXTURE": str(root), "EC2_SECRET_SAVE": "save"}
    config = root / ".aws/config"
    credentials = root / ".aws/credentials"
    config.parent.mkdir(mode=0o700)
    config.write_text("[profile tenant-ec2]\ncredential_process = old-helper\n"
                      "role_arn = old-role\nsso_session = old-session\n"
                      "[profile unrelated]\nregion = keep-region\n")
    credentials.write_text("[tenant-ec2]\naws_session_token = old-session\n"
                           "[unrelated]\naws_access_key_id = keep-id\n")
    def run(code, *, mode=None, fail=False, extra=None):
        current = dict(env)
        if mode is not None:
            current["EC2_CREDENTIAL_STORES"] = mode
        current.update(extra or {})
        setup = ". " + shlex.quote(str(library)) + "\nhere=" + shlex.quote(str(source.parent)) + "\n"
        result = subprocess.run(["/bin/sh", "-c", setup + code],
                                env=current, text=True, capture_output=True)
        assert (result.returncode != 0) == fail, "unexpected fixture result"
        assert "fixture-secret" not in result.stdout + result.stderr, "credential printed"
        return result
    def read(path):
        cfg = configparser.RawConfigParser()
        cfg.read(path)
        return cfg
    def persist(mode=None, **kwargs):
        run("id=fixture-id; secret=fixture-secret; persist_key && copy_optional_vault", mode=mode, **kwargs)
    # Test the exact guard used by `key`, without invoking either IAM command.
    key_code = source.read_text().split("\nkey)\n", 1)[1].split("\n    ;;", 1)[0]
    assert key_code.index("check_key_stores") < key_code.index("as_admin")
    before = (config.read_bytes(), credentials.read_bytes())
    refused = run("check_key_stores", fail=True)
    assert "EC2_CREDENTIAL_STORES=aws" in refused.stderr
    assert "EC2_CREDENTIAL_STORES=home-lab" in refused.stderr
    for mode in ("aws", "home-lab"):
        run("check_key_stores", mode=mode)
    run("check_key_stores", mode="", fail=True)
    assert before == (config.read_bytes(), credentials.read_bytes()), "guard changes a profile"
    run("check_key_stores", extra={"EC2_AWS_PROFILE": "unrelated"})
    alternate = root / "alternate-config"
    alternate.write_text("[default]\ncredential_process = old-helper\n")
    run("check_key_stores", fail=True,
        extra={"AWS_CONFIG_FILE": str(alternate), "EC2_AWS_PROFILE": "default"})
    alternate.write_text("[profile tenant-ec2]\nregion = us-east-1\n")
    other_credentials = root / "alternate-credentials"
    other_credentials.write_text("[tenant-ec2]\ncredential_process = old-helper\n")
    run("check_key_stores", fail=True,
        extra={"AWS_CONFIG_FILE": str(alternate), "AWS_SHARED_CREDENTIALS_FILE": str(other_credentials)})
    other_credentials.write_text("[unrelated]\ncredential_process = old-helper\n")
    run("check_key_stores",
        extra={"AWS_CONFIG_FILE": str(alternate), "AWS_SHARED_CREDENTIALS_FILE": str(other_credentials)})
    alternate.write_text("fixture-secret\n")
    run("check_key_stores", fail=True, extra={"AWS_CONFIG_FILE": str(alternate)})
    run("check_key_stores", extra={"AWS_CONFIG_FILE": str(root / "absent"),
                                   "AWS_SHARED_CREDENTIALS_FILE": str(root / "absent-credentials")})
    assert not (root/"calls").exists(), "guard calls AWS or a store"
    persist()
    assert not (root/"calls").exists(), "default mode calls an external store or AWS"
    assert read(credentials)["tenant-ec2"]["aws_access_key_id"] == "fixture-id"
    assert read(credentials)["tenant-ec2"]["aws_secret_access_key"] == "fixture-secret"
    assert set(read(credentials)["tenant-ec2"]) == {"aws_access_key_id", "aws_secret_access_key"}
    assert set(read(config)["profile tenant-ec2"]) == {"region", "output"}
    assert read(config)["profile unrelated"]["region"] == "keep-region"
    assert read(credentials)["unrelated"]["aws_access_key_id"] == "keep-id"
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (config, credentials))
    persist("home-lab")
    assert "EC2_CREDENTIAL_STORES=home-lab" in read(config)["profile tenant-ec2"]["credential_process"]
    assert not read(credentials)["tenant-ec2"]
    helper = read(config)["profile tenant-ec2"]["credential_process"]
    returned = subprocess.run(shlex.split(helper), env=env, text=True, capture_output=True, check=True)
    assert json.loads(returned.stdout) == {"Version": 1, "AccessKeyId": "fixture-id", "SecretAccessKey": "fixture-secret"}
    calls = (root/"calls").read_text().splitlines()
    assert [line.split()[0] for line in calls] == ["save", "save", "bash", "bash", "vault", "vault", "vault", "vault", "bash"]
    assert [line.split()[2] for line in calls if line.startswith("vault ")] == ["put", "patch", "get", "get"]
    saved = json.loads((root/"vault-state.json").read_text())
    assert saved == {"TENANT_EC2_AWS_ACCESS_KEY_ID": "fixture-id", "TENANT_EC2_AWS_SECRET_ACCESS_KEY": "fixture-secret"}
    assert (root/"TENANT_EC2_AWS_ACCESS_KEY_ID").read_text() == "fixture-id"
    assert (root/"TENANT_EC2_AWS_SECRET_ACCESS_KEY").read_text() == "fixture-secret"
    run('read_key; [ "$id" = fixture-id ] && [ "$secret" = fixture-secret ]', mode="home-lab")
    # The old helper command has no mode. It reads, but never writes, the saved key.
    before_files = (config.read_bytes(), credentials.read_bytes(), (root/"vault-state.json").read_bytes())
    before_calls = (root/"calls").read_text().splitlines()
    for mode in (None, "aws", "home-lab"):
        current = dict(env)
        if mode is not None:
            current["EC2_CREDENTIAL_STORES"] = mode
        returned = subprocess.run([str(source), "credential-process"], env=current,
                                  text=True, capture_output=True)
        assert returned.returncode == 0 and not returned.stderr, "legacy helper failed"
        assert json.loads(returned.stdout) == {"Version": 1, "AccessKeyId": "fixture-id", "SecretAccessKey": "fixture-secret"}
    added = (root/"calls").read_text().splitlines()[len(before_calls):]
    assert added == ["bash login"] * 3, "credential-process writes a store"
    assert before_files == (config.read_bytes(), credentials.read_bytes(), (root/"vault-state.json").read_bytes())
    before = (root/"calls").read_bytes()
    persist()
    run("check_key_stores")
    run("vault_copy", fail=True)
    run(":", mode="unknown", fail=True)
    assert (root/"calls").read_bytes() == before, "default or invalid mode calls a home tool"
    assert "credential_process" not in read(config)["profile tenant-ec2"]
    before = (config.read_bytes(), credentials.read_bytes())
    persist("home-lab", fail=True, extra={"FAIL_SAVE": "1"})
    assert before == (config.read_bytes(), credentials.read_bytes()), "failed save changes the profile"
    # Exercise invalid local files without invoking either IAM command.
    run("id=fixture-id; secret=fixture-secret; persist_key", fail=True,
        extra={"AWS_CONFIG_FILE": str(credentials), "AWS_SHARED_CREDENTIALS_FILE": str(credentials)})
    previous = config.read_text()
    config.write_text("fixture-secret\n")
    persist(fail=True)
    config.write_text(previous)
    before = (root/"calls").read_text().splitlines()
    persist("home-lab", extra={"NO_VAULT_TOKEN": "1"})
    added = (root/"calls").read_text().splitlines()[len(before):]
    assert [line.split()[0] for line in added] == ["save", "save", "bash"]
print("provisioner selftest: pass (two modes, legacy helper, key guard, isolated stubs, no deploy or key command)")
PY
    ;;
deploy)
    echo "==> deploying stack $stack with profile $admin"
    as_admin cloudformation deploy \
        --template-file "$here/aws-provisioner.cfn.yaml" \
        --stack-name "$stack" \
        --capabilities CAPABILITY_NAMED_IAM \
        --no-fail-on-empty-changeset \
        --tags "Project=$project" Role=provisioner \
        --parameter-overrides "Project=$project" "Regions=$region"
    ;;
key)
    command -v python3 >/dev/null || die "python3 not found"
    check_key_stores
    if [ "$stores" = home-lab ]; then
        command -v "$save" >/dev/null || die "$save not found (EC2_SECRET_SAVE)"
    fi
    old=$(as_admin iam list-access-keys --user-name "$user" \
        --query 'AccessKeyMetadata[].AccessKeyId' --output text)
    echo "==> making a new access key for $user"
    pair=$(as_admin iam create-access-key --user-name "$user" \
        --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)
    id=${pair%%	*}
    secret=${pair#*	}
    [ -n "$id" ] && [ -n "$secret" ] && [ "$id" != "$secret" ] || die "no key in the answer of AWS"
    echo "==> saving the key and writing the profile $profile ($stores)"
    if ! persist_key; then
        # A key that no store holds is of no use: remove it.
        as_admin iam delete-access-key --user-name "$user" --access-key-id "$id"
        die "the key is not saved; the new key is deleted"
    fi
    for k in $old; do
        echo "==> deleting an older key"
        as_admin iam delete-access-key --user-name "$user" --access-key-id "$k"
    done
    copy_optional_vault
    echo "Next: scripts/aws-provisioner.sh verify"
    ;;
verify)
    echo "==> identity (a new key can need some seconds)"
    if [ "$stores" = home-lab ]; then
        EC2_CREDENTIAL_STORES=home-lab "$here/aws-provisioner.sh" credential-process >/dev/null
    fi
    n=0
    until arn=$(as_provisioner sts get-caller-identity --query Arn --output text 2>/dev/null); do
        n=$((n + 1))
        [ "$n" -lt 15 ] || die "the profile $profile gets no identity"
        sleep 2
    done
    echo "$arn"
    case $arn in
    *":user/$user") ;;
    *) die "the profile $profile is not the user $user" ;;
    esac
    account=${arn#arn:*:iam::}
    account=${account%%:*}
    partition=${arn#arn:}
    partition=${partition%%:*}
    boundary=arn:$partition:iam::$account:policy/$project-boundary
    role=$project-verify-$$
    trust='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    echo "==> a role without the boundary is refused"
    if as_provisioner iam create-role --role-name "$role" \
        --assume-role-policy-document "$trust" >/dev/null 2>&1; then
        as_provisioner iam delete-role --role-name "$role"
        die "a role without the boundary was made"
    fi
    echo "==> a role with the boundary is made and deleted"
    as_provisioner iam create-role --role-name "$role" --assume-role-policy-document "$trust" \
        --permissions-boundary "$boundary" >/dev/null
    as_provisioner iam delete-role --role-name "$role"
    echo "==> a role outside the prefix is refused"
    if as_provisioner iam create-role --role-name "verify-$role" \
        --assume-role-policy-document "$trust" --permissions-boundary "$boundary" >/dev/null 2>&1; then
        as_provisioner iam delete-role --role-name "verify-$role"
        die "a role outside the prefix was made"
    fi
    echo "==> the list of the IAM users is refused"
    if as_provisioner iam list-users >/dev/null 2>&1; then
        die "the provisioner can list the IAM users"
    fi
    echo "==> the services of the stack answer in $region"
    as_provisioner cloudformation validate-template \
        --template-body "file://$here/aws-provisioner.cfn.yaml" >/dev/null
    as_provisioner ec2 describe-vpcs --query 'length(Vpcs)' --output text >/dev/null
    as_provisioner efs describe-file-systems --query 'length(FileSystems)' --output text >/dev/null
    as_provisioner secretsmanager list-secrets --max-results 1 >/dev/null
    as_provisioner bedrock list-inference-profiles --max-results 1 >/dev/null
    echo "verify: pass"
    ;;
vault)
    require_home_stores
    read_key
    echo "==> writing the Vault copy $vault_path"
    vault_copy || {
        [ $? -eq 10 ] || die "no Vault copy at $vault_path"
        die "no VAULT_PROVISIONER_TOKEN in a login shell"
    }
    echo "vault: the copy has the fields $id_name and $secret_name"
    ;;
credential-process)
    read_key
    printf '{"Version":1,"AccessKeyId":"%s","SecretAccessKey":"%s"}\n' "$id" "$secret"
    ;;
*)
    sed -n '2,/^$/s/^# \{0,1\}//p' "$0" >&2
    exit 2
    ;;
esac
