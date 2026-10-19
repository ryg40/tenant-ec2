#!/usr/bin/env python3
"""Exercise input contracts and live-operation ordering with isolated fake commands."""
import base64
import copy
import json
import importlib.util
import sys
import time
from unittest.mock import patch
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

# The actual M2 renderer is merged separately. Keep its boundary stub in this test.
MEMORY_STUB = '''import json
from pathlib import Path

def load_config(stage):
    if stage not in ('dev', 'test', 'prod'):
        raise ValueError('stage')
    return json.loads((Path(__file__).parent / 'stage.json').read_text())

def host_entry(config, suffix):
    rows = [h for h in config['hostRoles'] if h['roleSuffix'] == suffix]
    if len(rows) != 1:
        raise ValueError('admission')
    return rows[0]
'''


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='macos-bootstrap-test-'))
        self.repo = self.work / 'repo'
        for directory in ('scripts', 'config/macos'):
            (self.repo / directory).mkdir(parents=True)
        for file in ('scripts/host-bootstrap-macos.sh', 'config/macos/bootstrap-forms.py',
                     'config/macos/bootstrap-runtime.py', 'config/macos/runtime.lock.json'):
            shutil.copyfile(ROOT / file, self.repo / file)
        (self.repo / 'scripts/aws-memory.py').write_text(MEMORY_STUB)
        self.stage = {'region': 'us-east-1', 'people': [{'actorId': 'alice', 'pin': 'XYZ1000', 'status': 'active'}],
                      'hostRoles': [{'roleSuffix': 'macos-role', 'actorId': 'macos-agent', 'proposalProject': 'example-project'}]}
        self.env = {'EC2_VARIANT': 'macos', 'EC2_AWS_REGION': 'us-east-1', 'EC2_TZ': 'UTC', 'COMPOSE_FILE': 'compose.macos.yaml'}
        for name in ('EC2_LITELLM_MASTER_KEY', 'EC2_LITELLM_SALT_KEY', 'EC2_LITELLM_POSTGRES_PASSWORD',
                     'EC2_LITELLM_UI_USERNAME', 'EC2_LITELLM_UI_PASSWORD', 'EC2_BEDROCK_LUNA_MODEL',
                     'EC2_BEDROCK_EMBED_MODEL', 'EC2_OK_ENTRA_TENANT_ID', 'EC2_OK_OIDC_CLIENT_ID',
                     'EC2_OK_OIDC_CLIENT_SECRET', 'EC2_OK_COOKIE_SECRET', 'EC2_OK_ENTRA_GROUP_ID'):
            self.env[name] = 'private-fixture-do-not-print'
        for name in ('EC2_OK_ENTRA_TENANT_ID', 'EC2_OK_OIDC_CLIENT_ID', 'EC2_OK_ENTRA_GROUP_ID'):
            self.env[name] = '00000000-0000-0000-0000-000000000000'
        self.env['EC2_OK_COOKIE_SECRET'] = base64.b64encode(b'c' * 32).decode()
        kind = b'ssh-ed25519'
        blob = struct.pack('>I', len(kind)) + kind + struct.pack('>I', 32) + b'k' * 32
        self.key = 'ssh-ed25519 ' + base64.b64encode(blob).decode() + ' fixture'
        self.machine = {
            'schemaVersion': 1, 'project': 'tenant-ec2', 'variant': 'macos', 'region': 'us-east-1',
            'engine': 'podman', 'fileSet': ['compose.macos.yaml'],
            'services': ['litellm', 'litellm-db', 'open-knowledge', 'ok-auth'],
            'account': {'pin': 'XYZ1000', 'name': 'xyz1000', 'home': '/Users/xyz1000',
                        'shell': '/bin/zsh', 'sshPublicKey': self.key},
            'disk': {'volumeId': 'vol-' + 'a' * 17, 'mountPoint': '/Volumes/data'},
            'memory': {'stage': 'dev', 'agentActorId': 'macos-agent', 'proposalProject': 'example-project',
                       'configPath': '/run/tenant-ec2/memory.json'},
            'podman': {'provider': 'applehv', 'cpus': 4, 'memoryMiB': 8192, 'diskGiB': 80}}
        self.envfile, self.machinefile = self.work / 'service.env', self.work / 'machine.json'
        self.save()

    def save(self):
        self.envfile.write_text(''.join(f'{k}="{v}"\n' for k, v in self.env.items()))
        self.envfile.chmod(0o600)
        self.machinefile.write_text(json.dumps(self.machine))
        self.machinefile.chmod(0o644)
        (self.repo / 'scripts/stage.json').write_text(json.dumps(self.stage))

    def run_boot(self, *flags, code=0, trace=False, stdin=None, repo=None):
        repo = repo or self.repo
        args = ['zsh', *(['-x'] if trace else []), str(repo / 'scripts/host-bootstrap-macos.sh'),
                '--machine', str(self.machinefile)]
        if stdin is None:
            args += ['--env-file', str(self.envfile)]
        run = subprocess.run([*args, *flags], input=stdin, capture_output=True, text=True, timeout=20,
                             env={'PATH': os.environ['PATH'], 'HOME': str(self.work)})
        self.assertEqual(run.returncode, code, run.stderr + run.stdout)
        self.assertNotIn('private-fixture-do-not-print', run.stdout + run.stderr)
        self.assertNotIn(self.key, run.stdout + run.stderr)
        return run

    def forms(self, **kwargs):
        return self.run_boot('--check-forms', '--root', str(self.work), **kwargs)

    def test_dry_run_repeated_without_mutation(self):
        def tree():
            return {str(p): (p.read_bytes(), p.stat().st_mode) for p in self.work.rglob('*') if p.is_file()}
        before = tree()
        a = self.run_boot('--dry-run', '--root', str(self.work))
        b = self.run_boot('--dry-run', '--root', str(self.work))
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(before, tree())
        self.assertIn('agentcore-pending disabled', a.stdout)
        self.assertLess(a.stdout.index('verify account then restrict'), a.stdout.index('applehv'))
        self.assertFalse((self.work / 'Users').exists())

    def test_stdin_and_rendered_environment_are_equivalent(self):
        file_result = self.forms()
        self.assertEqual(file_result.stdout, self.forms(stdin=json.dumps(self.env)).stdout)
        self.assertEqual(file_result.stdout, self.forms(stdin=self.envfile.read_text()).stdout)
        self.forms(stdin=json.dumps(self.env)[:-1] + ',"EC2_VARIANT":"macos"}', code=1)
        self.forms(stdin=self.envfile.read_text() + 'EC2_VARIANT=macos\n', code=1)
        self.machinefile.write_text(self.machinefile.read_text()[:-1] + ',"engine":"podman"}')
        self.forms(code=1)
        self.save()
        self.forms(stdin=self.envfile.read_text() + 'EC2_KIT_TOKEN_LITELLM=$(touch unsafe)\n', code=1)
        self.assertFalse((self.work / 'unsafe').exists())
        self.envfile.chmod(0o644)
        self.forms(code=1)
        self.envfile.chmod(0o600)
        self.machinefile.chmod(0o666)
        self.forms(code=1)

    def test_compose_file_is_required_and_exact(self):
        self.forms()
        for value in (None, '', 'compose.yaml', 'compose.macos.yaml:compose.copilot.yaml',
                      './compose.macos.yaml', 'compose.macos.yaml '):
            with self.subTest(value=value):
                self.env.pop('COMPOSE_FILE', None)
                if value is not None:
                    self.env['COMPOSE_FILE'] = value
                self.save()
                self.forms(code=1)
                self.forms(stdin=json.dumps(self.env), code=1)
        self.assertFalse((self.work / 'Volumes').exists())
        self.assertFalse((self.work / 'var/db').exists())

    def test_machine_fields_and_nested_forms(self):
        original = copy.deepcopy(self.machine)
        cases = [([], 'unknown', 'bad'), ([], 'schemaVersion', True), ([], 'variant', 'homelab'),
                 ([], 'region', 'wrong'), ([], 'engine', 'docker'), ([], 'fileSet', ['../compose.yaml']),
                 ([], 'services', ['litellm']), ([], 'services', self.machine['services'] + ['litellm']),
                 (['account'], 'name', 'ec2-user'), (['account'], 'home', '/Users/another'),
                 (['account'], 'shell', '/bin/bash'), (['account'], 'pin', 'XYZ9999'),
                 (['disk'], 'volumeId', 'vol-12345678'), (['disk'], 'mountPoint', '/'),
                 (['memory'], 'agentActorId', 'alice'), (['memory'], 'proposalProject', 'different'),
                 (['memory'], 'configPath', '/tmp/memory.json'), (['memory'], 'stage', 'unknown'),
                 (['podman'], 'cpus', True), (['podman'], 'memoryMiB', 50000), (['podman'], 'diskGiB', 0),
                 (['podman'], 'provider', 'libkrun')]
        for path, key, value in cases:
            with self.subTest(path=path, key=key):
                self.machine = copy.deepcopy(original)
                obj = self.machine
                for part in path:
                    obj = obj[part]
                obj[key] = value
                self.save()
                self.forms(code=1)
        for section in ('account', 'disk', 'memory', 'podman'):
            self.machine = copy.deepcopy(original)
            self.machine[section]['unknown'] = 'bad'
            self.save()
            self.forms(code=1)

    def test_machine_is_the_only_host_source(self):
        self.forms()
        for name, value in (('EC2_MACOS_PERSON', 'alice'), ('EC2_MEMORY_STAGE', 'dev'),
                            ('EC2_MACOS_DATA_VOLUME_ID', 'vol-' + 'a' * 17), ('EC2_MACOS_PERSON_PIN', 'XYZ1000'),
                            ('EC2_MACOS_CPUS', '6'), ('AWS_PROFILE', 'personal'), ('EC2_UNKNOWN', 'bad'),
                            ('EC2_DOMAIN', 'unused.example'), ('EC2_MACOS_PERSONAL_KEY', 'handover')):
            self.env[name] = value
            self.save()
            self.forms(code=1)
            self.env.pop(name)
        self.save()
        self.machine['podman'].update(cpus=6, memoryMiB=16384)
        self.save()
        self.forms()
        self.assertFalse((self.work / '.aws').exists())

    def test_stage_person_and_host_admission(self):
        self.stage['people'][0]['status'] = 'offboarded'
        self.save()
        self.forms(code=1)
        self.stage['people'][0]['status'] = 'active'
        self.stage['people'].append(copy.deepcopy(self.stage['people'][0]))
        self.save()
        self.forms(code=1)
        self.stage['people'].pop()
        self.stage['hostRoles'] = []
        self.save()
        self.forms(code=1)
        # The unmerged base lacks the M2 admission API. Do not silently skip it.
        if 'def host_entry(' not in (ROOT / 'scripts/aws-memory.py').read_text():
            result = self.forms(repo=ROOT, code=1)
            self.assertIn('host-admission-unavailable', result.stderr)

    def test_agent_settings_require_selection_and_release(self):
        self.forms()
        self.env['EC2_PI_DURABLE_API_KEY'] = 'private-fixture-do-not-print'
        self.save()
        self.forms(code=1)
        self.machine['services'].append('pi-durable')
        self.save()
        self.forms(code=1)
        self.env.update(EC2_PI_DURABLE_LITELLM_API_KEY='fixture-agent-key',
                        EC2_PI_DURABLE_MODEL='bedrock/luna', EC2_PI_DURABLE_THINKING='xhigh', EC2_PI_DURABLE_WEB_SEARCH='off')
        self.save()
        result = self.forms(code=1)
        self.assertIn('agentcore-release-required', result.stderr)

    def test_owner_key_only_and_symlinks(self):
        for key in (self.key + '\n' + self.key, 'command="false" ' + self.key,
                    '-----BEGIN ' + 'PRIVATE KEY-----', 'ssh-ed25519 abc', 'ssh-rsa ' + self.key.split()[1]):
            self.machine['account']['sshPublicKey'] = key
            self.save()
            self.forms(code=1)
        self.machine['account']['sshPublicKey'] = self.key
        self.save()
        home = self.work / 'Users/xyz1000'
        home.mkdir(parents=True)
        (home / '.ssh').symlink_to(self.work, target_is_directory=True)
        self.forms(code=1)
        self.machinefile.unlink()
        self.machinefile.symlink_to(self.envfile)
        self.forms(code=1)

    def test_tracing_and_live_mutation_refused(self):
        result = self.run_boot('--check-forms', trace=True, code=1)
        self.assertIn('tracing refused', result.stderr)
        self.assertNotIn('service.env', result.stderr)
        self.run_boot('--root', str(self.work), code=1)
        result = self.run_boot(code=1)
        # This fixture runs as root on the build host; no native command is executed.
        if os.geteuid() == 0:
            self.assertIn('native-root-required', result.stderr)

    def test_mount_and_disabled_lifecycle_contract(self):
        settings = json.loads((ROOT / 'config/macos/machine.json').read_text())
        self.assertEqual((settings['cpus'], settings['memoryMiB'], settings['diskGiB']), (4, 8192, 80))
        for mount in settings['mounts']:
            self.assertTrue(mount['readOnly'])
            self.assertTrue(mount['source'].startswith('/Volumes/data/'))
            self.assertNotIn('Users', mount['source'])
            self.assertNotIn('.aws', mount['source'])
            self.assertNotIn('sock', mount['source'])
        for file in (ROOT / 'config/macos/launchd').glob('*.plist.in'):
            data = plistlib.loads(file.read_bytes())
            self.assertTrue(data['Disabled'])
            self.assertFalse(data['RunAtLoad'])
            self.assertFalse(data['KeepAlive'])
            self.assertNotIn('EnvironmentVariables', data)


class RuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('runtime', ROOT / 'config/macos/bootstrap-runtime.py')
        cls.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runtime)

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='macos-runtime-test-'))
        (self.work / 'var/db').mkdir(parents=True)
        self.machine = {'project': 'tenant-ec2', 'region': 'us-east-1',
                        'account': {'name': 'xyz1000', 'pin': 'XYZ1000', 'home': '/Users/xyz1000',
                                    'shell': '/bin/zsh', 'sshPublicKey': 'fixture-owner-key'},
                        'disk': {'volumeId': 'vol-' + 'a' * 17, 'mountPoint': '/Volumes/data'},
                        'podman': {'cpus': 4, 'memoryMiB': 8192, 'diskGiB': 80}}
        self.calls = []
        def runner(args, **kwargs):
            self.calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, b'', b'')
        self.boot = self.runtime.Bootstrap(self.machine, {}, runner=runner, root=self.work)

    def test_password_pty_prompt_no_argument_and_timeout(self):
        password = 'p' * 24
        script = self.work / 'prompt.py'
        script.write_text("import sys, termios\nassert termios.tcgetattr(0)[3] & termios.ECHO == 0\n"
                          "print('New password:', end='', flush=True)\n"
                          "value=input()\nassert len(value)==24\n"
                          "print('Confirm password:', end='', flush=True)\nassert input()==value\n")
        args = [sys.executable, str(script)]
        self.runtime.password_pty(args, password, timeout=5)
        self.assertNotIn(password, repr(args))
        script.write_text("import time\ntime.sleep(30)\n")
        start = time.monotonic()
        with self.assertRaisesRegex(self.runtime.Invalid, 'password-timeout'):
            self.runtime.password_pty(args, password, timeout=0.2)
        self.assertLess(time.monotonic() - start, 3)
        script.write_text("print('Administrator password:', end='', flush=True)\ninput()\n")
        with self.assertRaisesRegex(self.runtime.Invalid, 'password-prompt'):
            self.runtime.password_pty(args, password, timeout=2)

    def test_command_timeout_redacts(self):
        with self.assertRaisesRegex(self.runtime.Invalid, 'command-timeout'):
            self.runtime.run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.1)
        with self.assertRaisesRegex(self.runtime.Invalid, '^command-failed$'):
            self.runtime.run([sys.executable, '-c', "import sys; print('private-output'); sys.exit(2)"])

    def test_identity_refuses_account_switch(self):
        self.boot.identity()
        original = (self.boot.state / 'state.json').read_bytes()
        self.boot.identity()
        self.assertEqual(original, (self.boot.state / 'state.json').read_bytes())
        self.machine['account']['name'] = 'xyz2000'
        with self.assertRaisesRegex(self.runtime.Invalid, 'account-or-volume-switch'):
            self.boot.identity()

    def test_iokit_exact_serial_and_ambiguity(self):
        tree = [{'Serial Number': 'vol' + 'a' * 17, 'IORegistryEntryChildren': [{'BSD Name': 'disk2'}]},
                {'Serial Number': 'vol' + 'b' * 17, 'IORegistryEntryChildren': [{'BSD Name': 'disk3'}]}]
        self.assertEqual(self.runtime.iokit_disks(tree, self.machine['disk']['volumeId']), {'disk2'})
        tree[1]['Serial Number'] = 'vol' + 'a' * 17
        with patch.object(self.boot, 'plist', return_value=tree):
            with self.assertRaisesRegex(self.runtime.Invalid, 'disk-ambiguous'):
                self.boot.disk()
        self.assertFalse(self.calls)

    def disk_fixture(self, formatted=False, unrelated=False, missing_mount=False):
        state = {'formatted': formatted}
        (self.work / 'Volumes').mkdir(exist_ok=True)
        self.boot.identity()
        if formatted:
            self.boot.record['volumeUUID'] = 'fixture-volume'
        def plist(args):
            if 'ioreg' in args[0]:
                return [{'Serial Number': self.machine['disk']['volumeId'], 'IORegistryEntryChildren': [{'BSD Name': 'disk2'}]}]
            if args[1:3] == ['apfs', 'list']:
                return {'Containers': [{'PhysicalStores': [{'DeviceIdentifier': 'disk2s2'}],
                         'Volumes': [{'DeviceIdentifier': 'disk3s1'}]}] if state['formatted'] else []}
            if args[1] == 'list':
                return {'AllDisksAndPartitions': [{'DeviceIdentifier': 'disk2', 'Partitions': [{'Content': 'foreign'}] if unrelated else []}]}
            if args[-1] == '/':
                return {'DeviceIdentifier': 'disk1s1', 'ParentWholeDisk': 'disk1'}
            if args[-1] == '/dev/disk2':
                return {'DeviceIdentifier': 'disk2', 'WholeDisk': True, 'TotalSize': 200 * 1024 ** 3}
            return {'FilesystemType': 'apfs', 'VolumeUUID': 'fixture-volume', 'VolumeName': 'tenant-ec2-data',
                    'MountPoint': '' if missing_mount else str(self.boot.data)}
        def command(args, **kwargs):
            self.calls.append((args, kwargs))
            if 'eraseDisk' in args:
                state['formatted'] = True
            return subprocess.CompletedProcess(args, 0, b'', b'')
        return patch.object(self.boot, 'plist', side_effect=plist), patch.object(self.boot, 'command', side_effect=command)

    def test_disk_format_once_and_repeat_readback(self):
        a, b = self.disk_fixture()
        with a, b:
            self.boot.disk()
            self.boot.disk()
        self.assertEqual(sum('eraseDisk' in args for args, _ in self.calls), 1)
        self.assertEqual(self.boot.record['volumeUUID'], 'fixture-volume')

    def test_disk_foreign_layout_and_missing_mount_refused(self):
        a, b = self.disk_fixture(unrelated=True)
        with a, b, self.assertRaisesRegex(self.runtime.Invalid, 'disk-not-blank'):
            self.boot.disk()
        self.assertFalse(self.calls)
        a, b = self.disk_fixture(formatted=True, missing_mount=True)
        with a, b, self.assertRaisesRegex(self.runtime.Invalid, 'mount-not-verified'):
            self.boot.disk(format_allowed=False)
        self.assertFalse(any('eraseDisk' in args for args, _ in self.calls))

    def test_account_create_once_password_and_owner_key(self):
        self.boot.identity()
        home = self.work / 'Users/xyz1000'
        home.mkdir(parents=True)
        self.boot.uid, self.boot.gid = os.geteuid(), os.getegid()
        exists = False
        generated = {}
        def command(args, **kwargs):
            self.calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, int(args[0].endswith('dscl') and not exists), b'', b'')
        def keys(action, data=None):
            if action == 'put': generated.update(data)
            return generated.copy()
        def create(args, password):
            nonlocal exists
            self.assertEqual(len(password), 24)
            self.assertEqual(generated['EC2_MACOS_DEVELOPER_PASSWORD'], password)
            self.assertNotIn(password, args)
            exists = True
        def readback():
            self.boot.record['account'] = {'fixture': True}
            return home
        with patch.object(self.boot, 'command', side_effect=command), patch.object(self.boot, 'generated', side_effect=keys), \
             patch.object(self.boot, 'account_readback', side_effect=readback), patch.object(self.runtime, 'password_pty', side_effect=create) as prompt:
            self.boot.account_setup()
            (home / '.zshrc').write_text('owner changes\n')
            self.boot.account_setup()
            self.assertEqual(prompt.call_count, 1)
        self.assertEqual((home / '.ssh/authorized_keys').read_text(), 'fixture-owner-key\n')
        self.assertEqual((home / '.ssh').stat().st_mode & 0o777, 0o700)
        self.assertEqual((home / '.ssh/authorized_keys').stat().st_mode & 0o777, 0o600)
        self.assertEqual((home / '.zshrc').read_text(), 'owner changes\n')
        self.assertEqual(sum('newPasswordRequired=1' in args for args, _ in self.calls), 1)

    def test_unmanaged_account_and_readback_mismatch(self):
        with self.assertRaisesRegex(self.runtime.Invalid, 'unmanaged-account'):
            self.boot.account_setup()
        record = {'dsAttrTypeStandard:' + k: [v] for k, v in
                  {'RecordName': 'different', 'NFSHomeDirectory': '/Users/xyz1000', 'UserShell': '/bin/zsh'}.items()}
        with patch.object(self.boot, 'plist', return_value=record):
            with self.assertRaisesRegex(self.runtime.Invalid, 'account-mismatch'):
                self.boot.account_readback()

    def test_machine_existing_sizes_mounts_and_no_reset(self):
        self.boot.identity()
        self.boot.uid, self.boot.gid = os.geteuid(), os.getegid()
        self.boot.data.mkdir(parents=True)
        config = self.boot.data / 'podman/config/containers/podman/machine/applehv/tenant-ec2-macos.json'
        config.parent.mkdir(parents=True)
        # Make the two user-owned parents match the runtime directory policy.
        for path in ('podman', 'podman/config'):
            (self.boot.data / path).chmod(0o700)
        mounts = [{'Source': str(self.boot.data / 'runtime'), 'Target': '/run/tenant-ec2', 'ReadOnly': True},
                  {'Source': str(self.boot.data / 'sources'), 'Target': '/srv/sources', 'ReadOnly': True}]
        config.write_text(json.dumps({'Mounts': mounts}))
        def user(args, **kwargs):
            self.calls.append((args, kwargs))
            if args[1:3] == ['machine', 'list']:
                value = [{'Name': 'tenant-ec2-macos'}]
            elif args[1:3] == ['machine', 'inspect']:
                value = [{'State': 'running', 'Resources': {'CPUs': 4, 'Memory': 8192, 'DiskSize': 80}}]
            else:
                return subprocess.CompletedProcess(args, 0, b'aarch64\n' if 'uname' in args else b'1000\n', b'')
            return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b'')
        with patch.object(self.boot, 'user', side_effect=user):
            self.boot.machine_start()
            self.boot.machine_start(initialize=False)
            mounts.append({'Source': '/Users/xyz1000', 'Target': '/Users/xyz1000', 'ReadOnly': True})
            config.write_text(json.dumps({'Mounts': mounts}))
            with self.assertRaisesRegex(self.runtime.Invalid, 'machine-mounts'):
                self.boot.machine_start()
        self.assertFalse(any('init' in args or 'start' in args or 'rm' in args for args, _ in self.calls))

    def test_stage_runtime_renders_exact_compose_file_privately(self):
        self.boot.identity()
        (self.boot.data / 'runtime').mkdir(parents=True)
        self.boot.uid, self.boot.gid = os.geteuid(), os.getegid()
        self.boot.env = {'COMPOSE_FILE': 'compose.macos.yaml', 'EC2_VARIANT': 'macos',
                         'EC2_AWS_REGION': 'us-east-1', 'EC2_SNAPSHOT_CLONE_TOKEN': 'private-clone-fixture'}
        with patch.object(self.runtime, 'load_module') as bridge, \
                patch.object(self.boot, 'deliver_credentials') as deliver:
            self.boot.stage_runtime()
            path = self.boot.data / 'service.env'
            text = path.read_text()
            self.assertEqual(text.splitlines().count("COMPOSE_FILE='compose.macos.yaml'"), 1)
            self.assertEqual(self.runtime.forms.service_settings(text)['COMPOSE_FILE'], 'compose.macos.yaml')
            self.assertNotIn('EC2_SNAPSHOT_CLONE_TOKEN', text)
            self.assertNotIn('private-clone-fixture', text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual((path.stat().st_uid, path.stat().st_gid), (self.boot.uid, self.boot.gid))
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            self.boot.stage_runtime()
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertEqual(bridge.return_value.refresh.call_count, 2)
            self.assertEqual(deliver.call_count, 2)
        self.assertFalse(self.calls)

    def test_stage_runtime_refuses_missing_or_wrong_compose_file_before_writes(self):
        for value in (None, '', 'compose.yaml', 'compose.macos.yaml:compose.copilot.yaml'):
            with self.subTest(value=value):
                self.boot.env = {} if value is None else {'COMPOSE_FILE': value}
                with patch.object(self.runtime, 'directory') as directory, \
                        patch.object(self.runtime, 'atomic') as atomic, \
                        patch.object(self.runtime, 'load_module') as bridge, \
                        patch.object(self.boot, 'deliver_credentials') as deliver:
                    with self.assertRaisesRegex(self.runtime.Invalid, '^service-selector$'):
                        self.boot.stage_runtime()
                    directory.assert_not_called()
                    atomic.assert_not_called()
                    bridge.assert_not_called()
                    deliver.assert_not_called()
        self.assertFalse(self.calls)

    def snapshot_fixture(self):
        self.boot.repo = self.work / 'opt/tenant-ec2/macos/snapshot'
        for name in ('scripts', 'config/macos', '.git/objects'):
            (self.boot.repo / name).mkdir(parents=True, mode=0o700)
        (self.work / 'opt').chmod(0o755)
        for path in (self.boot.repo.parent.parent, self.boot.repo.parent, self.boot.repo):
            path.chmod(0o700)
        for name in ('scripts/verify-deployed.py', 'config/macos/bootstrap-forms.py', '.git/config'):
            (self.boot.repo / name).write_text('fixture\n')
            (self.boot.repo / name).chmod(0o600)
        (self.boot.repo / 'scripts/verify-deployed.py').chmod(0o700)

    def test_snapshot_read_traverse_modes_and_repeat(self):
        self.snapshot_fixture()
        self.boot.snapshot_access()
        before = {p: (p.stat().st_mode, p.read_bytes() if p.is_file() else None)
                  for p in self.boot.repo.rglob('*')}
        self.boot.snapshot_access()
        self.assertEqual(before, {p: (p.stat().st_mode, p.read_bytes() if p.is_file() else None)
                                  for p in self.boot.repo.rglob('*')})
        for path in (self.boot.repo.parent.parent, self.boot.repo.parent, self.boot.repo,
                     self.boot.repo / 'scripts', self.boot.repo / 'config/macos'):
            self.assertEqual(path.stat().st_mode & 0o7777, 0o755)
            self.assertEqual(path.stat().st_uid, 0)
        self.assertEqual((self.boot.repo / 'scripts/verify-deployed.py').stat().st_mode & 0o7777, 0o755)
        self.assertEqual((self.boot.repo / 'config/macos/bootstrap-forms.py').stat().st_mode & 0o7777, 0o644)
        self.assertEqual((self.boot.repo / '.git').stat().st_mode & 0o7777, 0o700)
        self.assertEqual((self.boot.repo / '.git/config').stat().st_mode & 0o7777, 0o600)
        self.assertEqual([args for args, _ in self.calls[:2]], [
            ['/bin/chmod', '-N', str(self.boot.repo.parent.parent), str(self.boot.repo.parent)],
            ['/bin/chmod', '-RN', str(self.boot.repo)]])
        self.assertFalse(any(p.is_symlink() or p.stat().st_mode & 0o022 for p in self.boot.repo.rglob('*')))
        # Prove ordinary POSIX access without the root test process bypassing modes.
        self.work.chmod(0o755)
        probe = '''import os, pathlib, sys
repo = pathlib.Path(sys.argv[1])
assert (repo / 'config/macos/bootstrap-forms.py').read_text() == 'fixture\\n'
for path in (repo.parent.parent, repo.parent, repo, repo / 'scripts', repo / 'config/macos'):
    assert os.access(path, os.R_OK | os.X_OK) and not os.access(path, os.W_OK)
for path in (repo / 'scripts/verify-deployed.py', repo / 'config/macos/bootstrap-forms.py'):
    assert os.access(path, os.R_OK) and not os.access(path, os.W_OK)
assert not os.access(repo / '.git', os.R_OK | os.X_OK)
'''
        result = subprocess.run([sys.executable, '-I', '-c', probe, str(self.boot.repo)], user=65534, group=65534,
                                extra_groups=[], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_snapshot_acl_failure_keeps_private_modes(self):
        self.snapshot_fixture()
        modes = {p: p.stat().st_mode for p in (self.boot.repo.parent.parent, self.boot.repo.parent, self.boot.repo)}
        with patch.object(self.boot, 'command', side_effect=self.runtime.Invalid('command-failed')):
            with self.assertRaisesRegex(self.runtime.Invalid, '^command-failed$'):
                self.boot.snapshot_access()
        self.assertEqual(modes, {p: p.stat().st_mode for p in modes})
        for mode in (0o700, 0o777):
            (self.work / 'opt').chmod(mode)
            with self.assertRaisesRegex(self.runtime.Invalid, '^snapshot-parent-mode$'):
                self.boot.snapshot_access()
        self.assertFalse(self.calls)

    def test_snapshot_refuses_links_types_and_wrong_owner_before_modes(self):
        self.snapshot_fixture()
        original = {p: p.stat().st_mode for p in (self.boot.repo.parent.parent, self.boot.repo.parent, self.boot.repo)}
        target = self.work / 'outside'
        target.write_text('unchanged\n')
        target.chmod(0o600)
        link = self.boot.repo / 'config/macos/unsafe'
        for kind in ('symlink-file', 'symlink-dir', 'hardlink', 'fifo', 'owner'):
            with self.subTest(kind=kind):
                if kind == 'symlink-file':
                    link.symlink_to(target)
                elif kind == 'symlink-dir':
                    link.symlink_to(self.work, target_is_directory=True)
                elif kind == 'hardlink':
                    os.link(target, link)
                elif kind == 'fifo':
                    os.mkfifo(link)
                else:
                    link.write_text('fixture\n')
                    os.chown(link, 65534, 65534)
                with self.assertRaisesRegex(self.runtime.Invalid, 'snapshot-owner-type'):
                    self.boot.snapshot_access()
                self.assertFalse(self.calls)
                self.assertEqual(original, {p: p.stat().st_mode for p in original})
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                link.unlink()
        self.boot.repo = self.work / 'alias'
        self.boot.repo.symlink_to(self.work / 'opt/tenant-ec2/macos/snapshot', target_is_directory=True)
        with self.assertRaisesRegex(self.runtime.Invalid, 'destination-symlink'):
            self.boot.snapshot_access()
        self.boot.repo = self.work / 'wrong-location'
        with self.assertRaisesRegex(self.runtime.Invalid, 'snapshot-location'):
            self.boot.snapshot_access()
        self.assertFalse(self.calls)

    def test_apply_order(self):
        events = []
        names = ('identity', 'snapshot_access', 'disk', 'account_setup', 'account_lockdown', 'tools', 'machine_start',
                 'stage_runtime', 'build_kits', 'services', 'launchd')
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in names:
                stack.enter_context(patch.object(self.boot, name, side_effect=lambda n=name: events.append(n)))
            self.boot.apply()
        self.assertEqual(events, list(names))
        events.clear()
        with ExitStack() as stack:
            for name in ('identity', 'snapshot_access', 'disk', 'account_readback', 'machine_start', 'stage_runtime', 'services'):
                stack.enter_context(patch.object(self.boot, name, side_effect=lambda n=name, **kw: events.append((n, kw))))
            self.boot.resume()
        self.assertEqual(events[1], ('snapshot_access', {}))
        self.assertEqual(events[2], ('disk', {'format_allowed': False}))
        self.assertEqual(events[4], ('machine_start', {'initialize': False}))

    def test_guest_writer_and_isolated_user_environment(self):
        compile(self.runtime.GUEST_CREDENTIAL_WRITER, '<guest-writer>', 'exec')
        env = self.boot.user_env()
        self.assertNotIn('AWS_PROFILE', env)
        self.assertNotIn('AWS_ACCESS_KEY_ID', env)
        self.assertEqual(env['CONTAINERS_MACHINE_PROVIDER'], 'applehv')
        self.assertTrue(env['XDG_DATA_HOME'].endswith('/Volumes/data/podman/data'))
        self.boot.uid, self.boot.gid = os.geteuid(), os.getegid()
        with patch.object(self.boot, 'user', return_value=subprocess.CompletedProcess([], 0, b'[]', b'')):
            self.boot.data.mkdir(parents=True)
            with self.assertRaisesRegex(self.runtime.Invalid, 'machine-missing'):
                self.boot.machine_start(initialize=False)


if __name__ == '__main__':
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (BootstrapTest, RuntimeTest))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print('macos bootstrap: pass (forms, PTY, disk/account fixtures and lifecycle ordering)')
        print('Not verified: live account setup, APFS, recovery, applehv, GUI lock or reboot')
    raise SystemExit(not result.wasSuccessful())
