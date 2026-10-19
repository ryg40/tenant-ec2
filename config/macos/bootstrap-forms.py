#!/usr/bin/env python3
"""Validate stack-written Mac machine data separately from private service settings."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys

CORE = ['litellm', 'litellm-db', 'open-knowledge', 'ok-auth']
SERVICE_REQUIRED = (
    'COMPOSE_FILE',
    'EC2_AWS_REGION', 'EC2_TZ', 'EC2_LITELLM_MASTER_KEY', 'EC2_LITELLM_SALT_KEY', 'EC2_LITELLM_POSTGRES_PASSWORD',
    'EC2_LITELLM_UI_USERNAME', 'EC2_LITELLM_UI_PASSWORD', 'EC2_BEDROCK_LUNA_MODEL',
    'EC2_BEDROCK_EMBED_MODEL', 'EC2_OK_ENTRA_TENANT_ID', 'EC2_OK_OIDC_CLIENT_ID',
    'EC2_OK_OIDC_CLIENT_SECRET', 'EC2_OK_COOKIE_SECRET', 'EC2_OK_ENTRA_GROUP_ID',
)
AGENT_REQUIRED = (
    'EC2_PI_DURABLE_API_KEY', 'EC2_PI_DURABLE_LITELLM_API_KEY', 'EC2_PI_DURABLE_MODEL',
    'EC2_PI_DURABLE_THINKING', 'EC2_PI_DURABLE_WEB_SEARCH',
)
SERVICE_OPTIONAL = (
    'EC2_VARIANT', 'EC2_PROJECT',
    'COMPOSE_PROJECT_NAME', 'EC2_SNAPSHOT_CLONE_TOKEN',
)


class Invalid(ValueError):
    pass


def need(test, code):
    if not test:
        raise Invalid(code)


def fields(value, names):
    need(isinstance(value, dict) and set(value) == set(names), 'machine-fields')


def match(value, pattern):
    need(isinstance(value, str) and re.fullmatch(pattern, value) is not None, 'machine-value')


def unique(pairs):
    obj = {}
    for key, value in pairs:
        need(key not in obj, 'duplicate-field')
        obj[key] = value
    return obj


def text_file(path, public=False, fixture=False):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        # macOS system aliases are not user-controlled destination symlinks.
        alias = {'/etc': '/private/etc', '/var': '/private/var', '/tmp': '/private/tmp'}
        trusted = sys.platform == 'darwin' and str(parent) in alias and str(parent.resolve()) == alias[str(parent)]
        need(not parent.is_symlink() or trusted, 'input-symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        owner = os.geteuid() if fixture or not public else 0
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == owner, 'input-owner')
        need(stat.S_IMODE(info.st_mode) == (0o644 if public else 0o600), 'input-mode')
        if public and not fixture and sys.platform == 'darwin':
            import grp
            need(info.st_gid == grp.getgrnam('admin').gr_gid, 'input-group')
        with os.fdopen(fd, 'r', closefd=False, encoding='utf-8') as stream:
            raw = stream.read(131073)
        need(len(raw) <= 131072, 'input-size')
        return raw
    finally:
        os.close(fd)


def service_settings(raw):
    need(len(raw) <= 131072, 'input-size')
    if raw.lstrip().startswith('{'):
        value = json.loads(raw, object_pairs_hook=unique)
    else:
        # This is data parsing, never shell evaluation or environment expansion.
        pairs = []
        for line in raw.splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            name, separator, item = line.partition('=')
            need(separator and re.fullmatch(r'[A-Z][A-Z0-9_]*', name), 'service-syntax')
            if item.startswith(('"', "'")):
                need(len(item) >= 2 and item[-1] == item[0] and item[0] not in item[1:-1], 'service-syntax')
                item = item[1:-1]
            else:
                need(not any(c in item for c in "\"' \t"), 'service-syntax')
            need(not any(c in item for c in '$`\\'), 'service-syntax')
            pairs.append((name, item))
        value = unique(pairs)
    need(isinstance(value, dict) and all(isinstance(v, str) for v in value.values()), 'service-shape')
    need(all(not any(c in v for c in '\r\n\0') for v in value.values()), 'service-value')
    return value


def owner_key(key):
    need(isinstance(key, str) and '\n' not in key and '\r' not in key, 'owner-key')
    words = key.split()
    need(len(words) in (2, 3) and words[0] in ('ssh-ed25519', 'ssh-rsa'), 'owner-key')
    blob = base64.b64decode(words[1], validate=True)
    size = struct.unpack('>I', blob[:4])[0]
    need(blob[4:4 + size].decode('ascii') == words[0] and len(blob) >= size + 12, 'owner-key')
    offset, parts = size + 4, []
    while offset < len(blob):
        size = struct.unpack('>I', blob[offset:offset + 4])[0]
        offset += 4
        need(size <= len(blob) - offset, 'owner-key')
        parts.append(blob[offset:offset + size])
        offset += size
    if words[0] == 'ssh-ed25519':
        need(len(parts) == 1 and len(parts[0]) == 32, 'owner-key')
    else:
        need(len(parts) == 2 and int.from_bytes(parts[1], 'big').bit_length() >= 2048, 'owner-key')


def machine_forms(machine, memory):
    fields(machine, ('schemaVersion', 'project', 'variant', 'region', 'engine', 'fileSet',
                     'services', 'account', 'disk', 'memory', 'podman'))
    need(type(machine['schemaVersion']) is int and machine['schemaVersion'] == 1, 'machine-version')
    need(machine['variant'] == 'macos' and machine['engine'] == 'podman', 'machine-engine')
    need(machine['fileSet'] == ['compose.macos.yaml'], 'machine-files')
    match(machine['project'], r'[a-z][a-z0-9-]{0,30}')
    match(machine['region'], r'[a-z]{2}(?:-[a-z]+)+-[0-9]+')
    services = machine['services']
    need(isinstance(services, list) and all(isinstance(s, str) for s in services), 'machine-services')
    need(len(services) == len(set(services)) and set(services) in (set(CORE), set(CORE + ['pi-durable'])), 'machine-services')
    account, disk, scope, podman = (machine[k] for k in ('account', 'disk', 'memory', 'podman'))
    fields(account, ('pin', 'name', 'home', 'shell', 'sshPublicKey'))
    match(account['pin'], r'XYZ[0-9]{4}')
    need(account['name'] == account['pin'].lower() and account['home'] == '/Users/' + account['name'] and account['shell'] == '/bin/zsh', 'machine-account')
    owner_key(account['sshPublicKey'])
    fields(disk, ('volumeId', 'mountPoint'))
    match(disk['volumeId'], r'vol-[0-9a-f]{17}')
    need(disk['mountPoint'] == '/Volumes/data', 'machine-disk')
    fields(scope, ('stage', 'agentActorId', 'proposalProject', 'configPath'))
    need(scope['stage'] in ('dev', 'test', 'prod') and scope['configPath'] == '/run/tenant-ec2/memory.json', 'machine-memory')
    for key in ('agentActorId', 'proposalProject'):
        match(scope[key], r'[a-z0-9][a-z0-9-]{0,63}')
    fields(podman, ('provider', 'cpus', 'memoryMiB', 'diskGiB'))
    need(podman['provider'] == 'applehv', 'machine-provider')
    for key, low, high in (('cpus', 1, 8), ('memoryMiB', 1024, 16384), ('diskGiB', 20, 1024)):
        need(type(podman[key]) is int and low <= podman[key] <= high, 'machine-size')
    config = memory.load_config(scope['stage'])
    need(config['region'] == machine['region'], 'machine-region')
    people = [p for p in config['people'] if p['pin'] == account['pin'] and p['status'] == 'active']
    need(len(people) == 1, 'machine-person')
    need(hasattr(memory, 'host_entry'), 'host-admission-unavailable')
    host = memory.host_entry(config, 'macos-role')
    need(host['actorId'] == scope['agentActorId'] and host['proposalProject'] == scope['proposalProject'], 'machine-admission')
    need(host['actorId'] not in {p['actorId'] for p in config['people']}, 'machine-agent-person')
    return people[0]['actorId']


def service_forms(env, machine):
    agent = 'pi-durable' in machine['services']
    allowed = set(SERVICE_REQUIRED + SERVICE_OPTIONAL + (AGENT_REQUIRED if agent else ()))
    kits = ('LITELLM', 'OPEN_KNOWLEDGE', 'PI_DURABLE') if agent else ('LITELLM', 'OPEN_KNOWLEDGE')
    allowed.update('EC2_KIT_' + kind + '_' + kit for kind in ('SOURCE', 'TOKEN') for kit in kits)
    need(not set(env) - allowed, 'service-fields')
    for name in SERVICE_REQUIRED + (AGENT_REQUIRED if agent else ()):
        need(bool(env.get(name)), 'service-required')
    for name, value in (('EC2_VARIANT', 'macos'), ('EC2_PROJECT', machine['project']),
                        ('EC2_AWS_REGION', machine['region']), ('COMPOSE_FILE', 'compose.macos.yaml')):
        need(env.get(name, value) == value, 'service-selector')
    match(env['EC2_TZ'], r'[A-Za-z][A-Za-z0-9_+./-]{0,63}')
    for name in ('EC2_OK_ENTRA_TENANT_ID', 'EC2_OK_OIDC_CLIENT_ID', 'EC2_OK_ENTRA_GROUP_ID'):
        match(env[name], r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
    need(len(base64.b64decode(env['EC2_OK_COOKIE_SECRET'], validate=True)) in (16, 24, 32), 'cookie-secret')
    match(env['EC2_LITELLM_POSTGRES_PASSWORD'], r'[A-Za-z0-9_-]{16,128}')
    if agent:
        need(env['EC2_PI_DURABLE_THINKING'] == 'xhigh' and env['EC2_PI_DURABLE_MODEL'] == 'bedrock/luna'
             and env['EC2_PI_DURABLE_WEB_SEARCH'] == 'off', 'agent-setting')
        # A selected service is necessary but not sufficient. The current pin has no adapter.
        raise Invalid('agentcore-release-required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--root', default='/', type=Path)
    parser.add_argument('--machine', required=True, type=Path)
    parser.add_argument('--env-file', default='-')
    args = parser.parse_args()
    try:
        sys.dont_write_bytecode = True
        machine = json.loads(text_file(args.machine, public=True, fixture=args.root != Path('/')), object_pairs_hook=unique)
        need(args.env_file != '-' or not sys.stdin.isatty(), 'service-pipe-required')
        raw = sys.stdin.read(131073) if args.env_file == '-' else text_file(args.env_file, fixture=args.root != Path('/'))
        env = service_settings(raw)
        spec = importlib.util.spec_from_file_location('memory', args.repo / 'scripts/aws-memory.py')
        memory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(memory)
        machine_forms(machine, memory)
        service_forms(env, machine)
        home = args.root / machine['account']['home'].lstrip('/')
        for path in (*home.parents, home, home / '.ssh', home / '.ssh/authorized_keys', home / '.zshrc'):
            need(not path.is_symlink(), 'account-symlink')
        print('macos bootstrap: forms pass')
        return 0
    except Invalid as error:
        print('macos bootstrap: ' + str(error), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, ImportError, UnicodeError, struct.error):
        print('macos bootstrap: input refused', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
