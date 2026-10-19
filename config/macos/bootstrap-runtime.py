#!/usr/bin/env python3
"""Privileged Mac setup and bounded PIN-owned Podman lifecycle."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pty
import re
import secrets
import select
import signal
import stat
import string
import subprocess
import sys
import tempfile
import termios
import time
import urllib.request

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


forms = load_module('macos_forms', ROOT / 'config/macos/bootstrap-forms.py')
need = forms.need
Invalid = forms.Invalid


def clean_env():
    return {'PATH': '/opt/homebrew/bin:/usr/local/bin:/opt/podman/bin:/usr/bin:/bin:/usr/sbin:/sbin',
            'HOME': '/var/root', 'LC_ALL': 'C', 'AWS_CONFIG_FILE': '/dev/null',
            'AWS_SHARED_CREDENTIALS_FILE': '/dev/null', 'AWS_EC2_METADATA_DISABLED': 'false',
            'AWS_PAGER': '', 'AWS_CLI_AUTO_PROMPT': 'off', 'AWS_IGNORE_CONFIGURED_ENDPOINT_URLS': 'true'}


def run(argv, data=None, env=None, timeout=60, allow_failure=False):
    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env or clean_env(), start_new_session=True)
    except OSError:
        if allow_failure:
            return subprocess.CompletedProcess(argv, 127, b'', b'')
        raise Invalid('command-unavailable') from None
    try:
        stdout, stderr = child.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate()
        raise Invalid('command-timeout') from None
    need(allow_failure or child.returncode == 0, 'command-failed')
    return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)


def safe_path(path):
    path = Path(path).absolute()
    aliases = {'/etc': '/private/etc', '/var': '/private/var', '/tmp': '/private/tmp'}
    for parent in (path, *path.parents):
        allowed = sys.platform == 'darwin' and str(parent) in aliases and str(parent.resolve()) == aliases[str(parent)]
        need(not parent.is_symlink() or allowed, 'destination-symlink')
    return path


def directory(path, uid=0, gid=0, mode=0o700):
    path = safe_path(path)
    if path.exists():
        info = path.stat()
        need(path.is_dir() and info.st_uid == uid and stat.S_IMODE(info.st_mode) == mode, 'directory-owner-mode')
    else:
        path.mkdir(mode=mode)
        os.chown(path, uid, gid)
        os.chmod(path, mode)
    return path


def atomic(path, data, uid=0, gid=0, mode=0o600):
    path = safe_path(path)
    if path.exists():
        info = path.stat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == uid, 'file-owner')
        if path.read_bytes() == data and stat.S_IMODE(info.st_mode) == mode and info.st_gid == gid:
            return
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), uid, gid)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def password_pty(argv, password, timeout=45, env=None):
    """Send one password only after a prompt; discard every byte of tool output."""
    master, slave = pty.openpty()
    settings = termios.tcgetattr(slave)
    settings[3] &= ~(termios.ECHO | termios.ECHONL)
    termios.tcsetattr(slave, termios.TCSANOW, settings)
    child = None
    try:
        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        child = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                 env=env or clean_env(), preexec_fn=controlling_terminal)
        os.close(slave)
        slave = None
        deadline, pending, sent = time.monotonic() + timeout, b'', 0
        while child.poll() is None:
            need(time.monotonic() < deadline, 'password-timeout')
            ready, _, _ = select.select([master], [], [], min(0.1, max(0, deadline - time.monotonic())))
            if not ready:
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            pending = (pending + chunk)[-8192:]
            # Never answer an administrator-password prompt with the new password.
            need(b'administrator password' not in pending.lower() and b'admin password' not in pending.lower(), 'password-prompt')
            if re.search(rb'(?i)(?:password|retype|verify|confirm)[^\r\n:]{0,80}[:?]\s*$', pending):
                need(sent < 2, 'password-prompt')
                os.write(master, password.encode('ascii') + b'\n')
                sent += 1
                pending = b''
        try:
            status = child.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise Invalid('password-timeout') from None
        need(status == 0 and sent >= 1, 'password-command')
    finally:
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        os.close(master)
        if slave is not None:
            os.close(slave)


GUEST_CREDENTIAL_WRITER = r'''
import json, os, pathlib, stat, sys, tempfile
root = pathlib.Path('/var/lib/tenant-ec2/aws-litellm')
# The guest parent is created for the SSH user before entering the user namespace.
for p in (root, *root.parents):
    if p.is_symlink(): raise ValueError()
root.mkdir(mode=0o700, exist_ok=True)
info = root.stat()
if info.st_uid not in (0, 1000) or stat.S_IMODE(info.st_mode) != 0o700: raise ValueError()
values = json.load(sys.stdin)
if set(values) != {'credentials.json', 'config', 'empty', 'credential-process.py'}: raise ValueError()
for name, value in values.items():
    path = root / name
    if path.is_symlink(): raise ValueError()
    fd, temporary = tempfile.mkstemp(prefix='.refresh-', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), 1000, 1000)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
os.chown(root, 1000, 1000)
'''


def normalized_volume(value):
    return str(value).strip().lower().replace('-', '')


def iokit_disks(tree, volume_id):
    """Associate a storage serial with BSD media only inside its IOKit subtree."""
    found = set()
    def visit(node, serial=False):
        if isinstance(node, list):
            for item in node:
                visit(item, serial)
        elif isinstance(node, dict):
            def serials(properties):
                values = []
                for key, value in properties.items():
                    if key in ('IORegistryEntryChildren', 'children'):
                        continue
                    if 'serial' in key.lower() and isinstance(value, str):
                        values.append(value)
                    elif isinstance(value, dict):
                        values.extend(serials(value))
                return values
            own = serials(node)
            if own:
                serial = any(normalized_volume(v) == normalized_volume(volume_id) for v in own)
            bsd = node.get('BSD Name')
            if serial and isinstance(bsd, str) and re.fullmatch(r'disk[0-9]+', bsd):
                found.add(bsd)
            for key in ('IORegistryEntryChildren', 'children'):
                visit(node.get(key, []), serial)
    visit(tree)
    return found


class Bootstrap:
    def __init__(self, machine, env, runner=run, root=Path('/')):
        self.machine, self.env, self.call = machine, env, runner
        self.root = root
        self.account = machine['account']
        self.state = root / 'var/db/tenant-ec2-macos'
        self.data = root / machine['disk']['mountPoint'].lstrip('/')
        self.uid, self.gid = None, None
        self.record = {}
        self.repo = ROOT
        self.lock = json.loads((ROOT / 'config/macos/runtime.lock.json').read_text())

    def command(self, args, **kwargs):
        return self.call(args, **kwargs)

    def plist(self, args):
        try:
            return plistlib.loads(self.command(args).stdout)
        except (ValueError, TypeError, plistlib.InvalidFileException):
            raise Invalid('plist-response') from None

    def save(self):
        atomic(self.state / 'state.json', json.dumps(self.record, sort_keys=True).encode())

    def identity(self):
        directory(self.state)
        path = self.state / 'state.json'
        if path.exists():
            self.record = json.loads(forms.text_file(path))
        expected = {'project': self.machine['project'], 'pin': self.account['pin'],
                    'name': self.account['name'], 'volumeId': self.machine['disk']['volumeId']}
        if 'identity' in self.record:
            need(self.record['identity'] == expected, 'account-or-volume-switch')
        else:
            self.record['identity'] = expected
            self.save()

    def disk(self, format_allowed=True):
        disks = iokit_disks(self.plist(['/usr/sbin/ioreg', '-a', '-r', '-c', 'IOService']), self.machine['disk']['volumeId'])
        need(len(disks) == 1, 'disk-ambiguous')
        disk = next(iter(disks))
        info = self.plist(['/usr/sbin/diskutil', 'info', '-plist', '/dev/' + disk])
        root_info = self.plist(['/usr/sbin/diskutil', 'info', '-plist', '/'])
        need(info.get('DeviceIdentifier') == disk and info.get('WholeDisk') is True, 'disk-identity')
        need(disk not in (root_info.get('DeviceIdentifier'), root_info.get('ParentWholeDisk')) and not info.get('MountPoint'), 'disk-is-system')
        need(info.get('TotalSize', 0) >= (self.machine['podman']['diskGiB'] + 20) * 1024 ** 3, 'disk-too-small')
        layout = self.plist(['/usr/sbin/diskutil', 'list', '-plist', '/dev/' + disk])
        rows = [r for r in layout.get('AllDisksAndPartitions', []) if r.get('DeviceIdentifier') == disk]
        need(len(rows) == 1, 'disk-layout')
        parts = rows[0].get('Partitions', [])
        apfs = self.plist(['/usr/sbin/diskutil', 'apfs', 'list', '-plist'])
        containers = [c for c in apfs.get('Containers', []) if any(
            re.fullmatch(re.escape(disk) + r'(?:s[0-9]+)?', p.get('DeviceIdentifier', ''))
            for p in c.get('PhysicalStores', []))]
        if not containers:
            need(format_allowed and not self.record.get('volumeUUID'), 'disk-not-mounted')
            need(not parts and not info.get('FilesystemType') and info.get('Content', '') in ('', 'None', 'Unknown'), 'disk-not-blank')
            self.record['formatIntent'] = self.machine['disk']['volumeId']
            self.save()
            self.command(['/usr/sbin/diskutil', 'eraseDisk', 'APFS', 'tenant-ec2-data', '/dev/' + disk], timeout=180)
            apfs = self.plist(['/usr/sbin/diskutil', 'apfs', 'list', '-plist'])
            containers = [c for c in apfs.get('Containers', []) if any(
                re.fullmatch(re.escape(disk) + r's[0-9]+', p.get('DeviceIdentifier', ''))
                for p in c.get('PhysicalStores', []))]
        need(self.record.get('volumeUUID') or self.record.get('formatIntent') == self.machine['disk']['volumeId'], 'foreign-apfs')
        need(len(containers) == 1 and len(containers[0].get('PhysicalStores', [])) == 1, 'apfs-ambiguous')
        volumes = containers[0].get('Volumes', [])
        need(len(volumes) == 1, 'apfs-volumes')
        volume = volumes[0]['DeviceIdentifier']
        need(re.fullmatch(r'disk[0-9]+s[0-9]+', volume) is not None, 'apfs-device')
        volume_info = self.plist(['/usr/sbin/diskutil', 'info', '-plist', '/dev/' + volume])
        need(volume_info.get('FilesystemType', '').lower() == 'apfs' and volume_info.get('VolumeUUID'), 'apfs-filesystem')
        need(volume_info.get('VolumeName') == 'tenant-ec2-data', 'foreign-apfs')
        uuid = volume_info['VolumeUUID']
        if self.record.get('volumeUUID'):
            need(self.record['volumeUUID'] == uuid, 'apfs-volume-changed')
        safe_path(self.data)
        if volume_info.get('MountPoint') != str(self.data):
            need(not self.data.is_mount() and (not self.data.exists() or not any(self.data.iterdir())), 'mount-conflict')
            if volume_info.get('MountPoint'):
                self.command(['/usr/sbin/diskutil', 'unmount', '/dev/' + volume])
            self.data.mkdir(mode=0o755, exist_ok=True)
            self.command(['/usr/sbin/diskutil', 'mount', '-mountPoint', str(self.data), '/dev/' + volume])
        verified = self.plist(['/usr/sbin/diskutil', 'info', '-plist', str(self.data)])
        need(verified.get('VolumeUUID') == uuid and verified.get('MountPoint') == str(self.data), 'mount-not-verified')
        self.command(['/usr/sbin/diskutil', 'enableOwnership', str(self.data)])
        self.record['volumeUUID'] = uuid
        self.save()
        print('macos bootstrap: durable mount pass')

    def generated(self, command, data=None):
        args = [sys.executable, '-B', str(self.repo / 'scripts/generated-keys.py'), command,
                '--variant', 'macos', '--project', self.machine['project'], '--region', self.machine['region']]
        result = self.command(args, data=json.dumps(data).encode() if data is not None else None, timeout=90)
        return json.loads(result.stdout) if command == 'read' else None

    def account_readback(self):
        data = self.plist(['/usr/bin/dscl', '-plist', '.', '-read', '/Users/' + self.account['name']])
        def value(key):
            values = data.get('dsAttrTypeStandard:' + key, data.get(key, []))
            need(isinstance(values, list) and len(values) == 1, 'account-record')
            return str(values[0])
        need(value('RecordName') == self.account['name'] and value('NFSHomeDirectory') == self.account['home']
             and value('UserShell') == '/bin/zsh', 'account-mismatch')
        self.uid, self.gid = int(value('UniqueID')), int(value('PrimaryGroupID'))
        need(self.uid >= 501 and self.gid > 0, 'account-identifier')
        expected = {'uid': self.uid, 'gid': self.gid, 'guid': value('GeneratedUID')}
        if self.record.get('account'):
            need(self.record['account'] == expected, 'account-identifier-changed')
        membership = self.command(['/usr/sbin/dseditgroup', '-o', 'checkmember', '-m', self.account['name'], 'admin']).stdout
        need(membership.lower().startswith(b'yes '), 'account-not-admin')
        home = safe_path(self.root / self.account['home'].lstrip('/'))
        need(home.is_dir() and home.stat().st_uid == self.uid, 'account-home-owner')
        self.record['account'] = expected
        self.save()
        return home

    def account_setup(self):
        name = self.account['name']
        exists = self.command(['/usr/bin/dscl', '.', '-read', '/Users/' + name], allow_failure=True).returncode == 0
        if not exists:
            need(not self.record.get('account'), 'account-removed')
            # Persist the generated value before creation. A crash can retry without
            # silently resetting an already-created account or losing its first password.
            values = self.generated('read')
            key = 'EC2_MACOS_DEVELOPER_PASSWORD'
            password = values.get(key)
            if not password:
                alphabet = string.ascii_letters + string.digits
                password = ''.join(secrets.choice(alphabet) for _ in range(24))
                values[key] = password
                self.generated('put', values)
                need(self.generated('read').get(key) == password, 'password-storage-readback')
            need(len(password) == 24 and password.isascii() and password.isalnum(), 'password-format')
            self.record['accountIntent'] = True
            self.save()
            password_pty(['/usr/sbin/sysadminctl', '-addUser', name, '-fullName', name, '-admin',
                          '-shell', '/bin/zsh', '-home', self.account['home'], '-password', '-'], password)
            del password
        else:
            need(self.record.get('account') or self.record.get('accountIntent'), 'unmanaged-account')
        home = self.account_readback()
        ssh = directory(home / '.ssh', self.uid, self.gid)
        atomic(ssh / 'authorized_keys', (self.account['sshPublicKey'] + '\n').encode(), self.uid, self.gid)
        zshrc = safe_path(home / '.zshrc')
        if not zshrc.exists():
            atomic(zshrc, b'eval "$(/opt/homebrew/bin/brew shellenv)"\n', self.uid, self.gid)
        else:
            need(zshrc.is_file() and zshrc.stat().st_uid == self.uid and zshrc.stat().st_nlink == 1, 'zshrc-owner')
        # Preserve the user's changed password on reruns; only first boot expires it.
        if not self.record.get('passwordPolicy'):
            self.command(['/usr/bin/pwpolicy', '-u', name, '-setpolicy', 'minChars=14 usingHistory=5'])
            self.command(['/usr/bin/pwpolicy', '-u', name, '-setpolicy', 'newPasswordRequired=1'])
            self.record['passwordPolicy'] = True
            self.save()
        print('macos bootstrap: PIN account readback pass')

    def account_lockdown(self):
        name = self.account['name']
        sudoers = self.root / 'etc/sudoers.d'
        directory(sudoers, mode=0o755)
        rule = ('Defaults:' + name + ' authenticate\n' + name + ' ALL=(ALL) PASSWD: ALL\n').encode()
        atomic(sudoers / 'zz-tenant-ec2-macos', rule, mode=0o440)
        self.command(['/usr/sbin/visudo', '-c'])
        listing = self.command(['/usr/bin/sudo', '-l', '-U', name]).stdout
        need(b'NOPASSWD:' not in listing, 'sudo-password-policy')
        # Put the managed SSH block first so later includes cannot restore defaults.
        config = self.root / 'etc/ssh/sshd_config'
        safe_path(config)
        block = ('# tenant-ec2 macos begin\nPermitRootLogin no\nPasswordAuthentication no\n'
                 'KbdInteractiveAuthentication no\nAllowUsers ' + name + '\n# tenant-ec2 macos end\n')
        text = config.read_text()
        text = re.sub(r'^# tenant-ec2 macos begin\n.*?^# tenant-ec2 macos end\n', '', text, flags=re.M | re.S)
        atomic(config, (block + text).encode(), mode=0o600)
        self.command(['/usr/sbin/sshd', '-t'])
        effective = self.command(['/usr/sbin/sshd', '-T', '-C', 'user=' + name + ',host=localhost,addr=127.0.0.1']).stdout.decode()
        for line in ('permitrootlogin no', 'passwordauthentication no', 'kbdinteractiveauthentication no'):
            need(line in effective.splitlines(), 'ssh-policy')
        allowed = [word for line in effective.splitlines() if line.startswith('allowusers ') for word in line.split()[1:]]
        need(allowed == [name], 'ssh-policy')
        self.command(['/usr/bin/defaults', 'write', '/Library/Preferences/com.apple.loginwindow', 'GuestEnabled', '-bool', 'false'])
        self.command(['/usr/bin/defaults', 'delete', '/Library/Preferences/com.apple.loginwindow', 'autoLoginUser'], allow_failure=True)
        for domain, key, kind, value in (('com.apple.screensaver', 'idleTime', '-int', '300'),
                                         ('com.apple.screensaver', 'askForPassword', '-int', '1'),
                                         ('com.apple.screensaver', 'askForPasswordDelay', '-int', '0')):
            self.user(['/usr/bin/defaults', 'write', domain, key, kind, value])
        # SSH AllowUsers is effective even if an init module recreates ec2-user keys.
        for old in ('ec2-user', 'ssm-user'):
            present = self.command(['/usr/bin/dscl', '.', '-read', '/Users/' + old], allow_failure=True).returncode == 0
            if present:
                self.command(['/usr/bin/dscl', '.', '-create', '/Users/' + old, 'UserShell', '/usr/bin/false'])
                self.command(['/usr/bin/pwpolicy', '-u', old, '-disableuser'])
                auth = self.root / 'Users' / old / '.ssh/authorized_keys'
                safe_path(auth)
                if auth.exists():
                    need(auth.is_file() and auth.stat().st_nlink == 1, 'default-key-file')
                    auth.unlink()
        print('macos bootstrap: default-account restrictions pass')

    def user_env(self):
        env = {'PATH': clean_env()['PATH'], 'HOME': self.account['home'], 'USER': self.account['name'], 'LOGNAME': self.account['name'],
               'LC_ALL': 'C', 'CONTAINERS_MACHINE_PROVIDER': 'applehv',
               'XDG_DATA_HOME': str(self.data / 'podman/data'), 'XDG_CONFIG_HOME': str(self.data / 'podman/config'),
               'PODMAN_COMPOSE_PROVIDER': str(self.data / 'bin/docker-compose'), 'COMPOSE_PROFILES': '',
               'CONTAINER_CONNECTION': 'tenant-ec2-macos', 'GIT_TERMINAL_PROMPT': '0',
               'HOMEBREW_NO_AUTO_UPDATE': '1', 'HOMEBREW_NO_ENV_HINTS': '1'}
        return env

    def user(self, argv, data=None, timeout=60, allow_failure=False):
        return self.command(['/usr/bin/sudo', '-n', '-u', self.account['name'], '-H', '/usr/bin/env', '-i',
                             *[k + '=' + v for k, v in self.user_env().items()], *argv],
                            data=data, timeout=timeout, allow_failure=allow_failure)

    def download(self, entry, destination):
        safe_path(destination)
        if destination.exists():
            digest = hashlib.sha256()
            with destination.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            need(digest.hexdigest() == entry['sha256'], 'asset-hash')
            return
        need(entry['url'].startswith('https://github.com/'), 'asset-source')
        # Only public pinned assets use this downloader. No authorization is sent.
        fd, name = tempfile.mkstemp(prefix='.asset-', dir=destination.parent)
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, 'wb') as stream, urllib.request.urlopen(entry['url'], timeout=60) as response:
                deadline = time.monotonic() + 900
                while True:
                    need(time.monotonic() < deadline, 'asset-timeout')
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    stream.write(chunk)
            need(digest.hexdigest() == entry['sha256'], 'asset-hash')
            os.replace(name, destination)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def tools(self):
        assets = directory(self.data / 'assets', mode=0o755)
        package = assets / 'podman.pkg'
        self.download(self.lock['podman'], package)
        self.command(['/usr/sbin/pkgutil', '--check-signature', str(package)])
        self.command(['/usr/sbin/spctl', '--assess', '--type', 'install', str(package)])
        podman = self.command(['/opt/podman/bin/podman', '--version'], allow_failure=True).stdout.decode()
        if self.lock['podman']['version'] not in podman:
            self.command(['/usr/sbin/installer', '-pkg', str(package), '-target', '/'], timeout=300)
        version = self.command(['/opt/podman/bin/podman', '--version']).stdout.decode()
        need(version.strip() == 'podman version ' + self.lock['podman']['version'], 'podman-version')
        binary = directory(self.data / 'bin', mode=0o755)
        self.download(self.lock['compose'], binary / 'docker-compose')
        os.chmod(binary / 'docker-compose', 0o755)
        self.download(self.lock['machine'], assets / 'machine.raw.zst')
        os.chmod(assets, 0o755)
        os.chmod(assets / 'machine.raw.zst', 0o644)
        brew = Path('/opt/homebrew')
        need(brew.is_dir() and not brew.is_symlink(), 'homebrew-missing')
        # Migrate only the Homebrew prefix, not system directories or a developer home.
        for parent, directories, files in os.walk(brew, followlinks=False):
            for path in [Path(parent), *[Path(parent) / p for p in directories + files]]:
                os.chown(path, self.uid, self.gid, follow_symlinks=False)
        self.user(['/usr/bin/xcode-select', '-p'])
        self.user(['/opt/homebrew/bin/brew', 'install', 'git', 'python', 'node'], timeout=900)
        print('macos bootstrap: pinned runtime tools pass')

    def machine_start(self, initialize=True):
        for path in ('podman', 'podman/data', 'podman/config', 'sources'):
            directory(self.data / path, self.uid, self.gid)
        runtime = directory(self.data / 'runtime', mode=0o755)
        name = 'tenant-ec2-macos'
        podman = '/opt/podman/bin/podman'
        known = json.loads(self.user([podman, 'machine', 'list', '--format', 'json']).stdout)
        need(all(m.get('Name') == name for m in known), 'unexpected-machine')
        if not known:
            need(initialize and not self.record.get('machineCreated'), 'machine-missing')
            size = self.machine['podman']
            args = [podman, 'machine', 'init', '--cpus', str(size['cpus']), '--memory', str(size['memoryMiB']),
                    '--disk-size', str(size['diskGiB']), '--rootful=false', '--image', str(self.data / 'assets/machine.raw.zst'),
                    '--volume', str(runtime) + ':/run/tenant-ec2:ro',
                    '--volume', str(self.data / 'sources') + ':/srv/sources:ro', name]
            self.user(args, timeout=300)
        inspected = json.loads(self.user([podman, 'machine', 'inspect', name]).stdout)
        need(len(inspected) == 1, 'machine-inspection')
        info = inspected[0]
        resources = info.get('Resources', {})
        size = self.machine['podman']
        need(resources.get('CPUs') == size['cpus'] and resources.get('Memory') == size['memoryMiB']
             and resources.get('DiskSize') == size['diskGiB'], 'machine-size-mismatch')
        # Podman machine inspect does not expose mounts on every version. Its saved
        # machine config must expose the exact mounts; absence is a refusal, not proof.
        configs = list((self.data / 'podman/config/containers/podman/machine/applehv').glob(name + '.json'))
        need(len(configs) == 1, 'machine-config-missing')
        config = json.loads(configs[0].read_text())
        actual = {(m.get('Source'), m.get('Target'), m.get('ReadOnly')) for m in config.get('Mounts', [])}
        expected = {(str(runtime), '/run/tenant-ec2', True), (str(self.data / 'sources'), '/srv/sources', True)}
        need(actual == expected, 'machine-mounts')
        if info.get('State') != 'running':
            self.user([podman, 'machine', 'start', name], timeout=180)
        need(self.user([podman, 'machine', 'ssh', name, 'uname', '-m']).stdout.strip() == b'aarch64', 'machine-architecture')
        need(self.user([podman, 'machine', 'ssh', name, 'id', '-u']).stdout.strip() == b'1000', 'machine-user')
        self.record['machineCreated'] = True
        self.save()
        print('macos bootstrap: isolated applehv machine pass')

    def stage_runtime(self):
        need(self.env.get('COMPOSE_FILE') == 'compose.macos.yaml', 'service-selector')
        runtime = self.data / 'runtime'
        directory(runtime / 'litellm', mode=0o755)
        atomic(runtime / 'litellm/config.yaml', (self.repo / 'litellm/config.yaml').read_bytes(), mode=0o644)
        atomic(runtime / 'oauth2-proxy.cfg', (self.repo / 'ok-auth/oauth2-proxy.macos.cfg').read_bytes(), mode=0o644)
        # A root-owned source is copied into a container-user-owned guest directory.
        # No temporary AWS credential is frozen in a container environment.
        directory(self.state / 'aws-litellm')
        bridge = load_module('macos_credentials', self.repo / 'scripts/macos-credentials.py')
        bridge.refresh((self.state / 'aws-litellm').resolve(), '/usr/local/bin/aws')
        self.deliver_credentials()
        render = ''.join(k + "='" + v + "'\n" for k, v in sorted(self.env.items())
                         if k in set(forms.SERVICE_REQUIRED + forms.SERVICE_OPTIONAL) and k != 'EC2_SNAPSHOT_CLONE_TOKEN')
        need(all("'" not in v and '\\' not in v for v in self.env.values()), 'environment-quoting')
        atomic(self.data / 'service.env', render.encode(), self.uid, self.gid)
        atomic(runtime / 'compose.macos.yaml', (self.repo / 'compose.macos.yaml').read_bytes(), mode=0o644)
        print('macos bootstrap: private runtime configuration pass')

    def deliver_credentials(self):
        # Use a private SSH stdin stream into the guest. This avoids assuming that
        # macOS uid 0 maps to the application's Linux uid through virtiofs.
        podman = '/opt/podman/bin/podman'
        source = (self.state / 'aws-litellm/credentials.json').resolve()
        provider = load_module('credential_process', self.repo / 'config/macos/credential-process.py')
        data = provider.read_credentials(source)
        payload = {'credentials.json': json.dumps(data), 'empty': '',
                   'config': (self.repo / 'config/macos/aws-python.config').read_text(),
                   'credential-process.py': (self.repo / 'config/macos/credential-process.py').read_text()}
        # unshare maps uid 1000 to the same subordinate uid used by the container.
        # The guest copy is refreshed atomically; no virtiofs UID assumption is made.
        self.user([podman, 'machine', 'ssh', 'tenant-ec2-macos', 'sudo', 'install', '-d', '-o', '1000', '-g', '1000', '-m', '0700', '/var/lib/tenant-ec2'])
        import shlex
        guest_command = shlex.join(['podman', 'unshare', 'python3', '-c', GUEST_CREDENTIAL_WRITER])
        self.user([podman, 'machine', 'ssh', 'tenant-ec2-macos', guest_command],
                  data=json.dumps(payload).encode())
        # Atomic replacement creates new inodes. Restore the shared container label
        # on each refresh, not just when Compose first mounts the directory.
        self.user([podman, 'machine', 'ssh', 'tenant-ec2-macos',
                   'podman unshare chcon -R -t container_file_t -l s0 /var/lib/tenant-ec2/aws-litellm'])

    def build_kits(self):
        import yaml
        lock = yaml.safe_load((self.repo / 'kits.lock').read_text())
        for name in ('litellm', 'open-knowledge'):
            pin = lock['server_kits'][name]
            source = self.data / 'sources' / name
            key = name.upper().replace('-', '_')
            url = self.env.get('EC2_KIT_SOURCE_' + key) or pin['source_repo']
            need(re.fullmatch(r'https://[A-Za-z0-9.-]+/[A-Za-z0-9_./-]+\.git', url) is not None, 'kit-url')
            need(re.fullmatch(r'[0-9a-f]{40}', pin['source_commit']) is not None, 'kit-commit')
            # Private source authentication is not needed by these public kit pins.
            need(not self.env.get('EC2_KIT_TOKEN_' + key), 'private-kit-clone-not-supported')
            if not source.exists():
                self.user(['/usr/bin/git', '-c', 'credential.helper=', 'clone', '--depth', '1', '--branch', pin['source_tag'], '--', url, str(source)], timeout=300)
            need(self.user(['/usr/bin/git', '-C', str(source), 'rev-parse', 'HEAD']).stdout.decode().strip() == pin['source_commit'], 'kit-pin')
            need(not self.user(['/usr/bin/git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all']).stdout.strip(), 'kit-dirty')
            podman = '/opt/podman/bin/podman'
            dockerfile = 'Dockerfile' if name == 'litellm' else 'deploy/Dockerfile'
            build = [podman, 'build', '--platform', 'linux/arm64', '-f', str(source / dockerfile), '-t', pin['image']]
            if name == 'open-knowledge':
                build += ['--build-arg', 'OK_REVISION=' + pin['source_commit'], '--build-arg', 'OK_RELEASE_VERSION=0.83.2']
            else:
                build += ['--target', 'gateway', '--build-arg', 'PORTABLE_VERSION=0.4.1', '--build-arg', 'REVISION=' + pin['source_commit']]
                smoke = build.copy()
                smoke[smoke.index('gateway')] = 'smoke'
                self.user([*smoke, str(source)], timeout=1200)
            self.user([*build, str(source)], timeout=1200)
            image = json.loads(self.user([podman, 'image', 'inspect', pin['image']]).stdout)[0]
            need(image.get('Architecture') == 'arm64', 'kit-architecture')
        print('macos bootstrap: locked ARM64 kit builds pass')

    def volumes(self):
        podman = '/opt/podman/bin/podman'
        for suffix, uid in (('litellm-db', 999), ('open-knowledge', 10001)):
            name = 'tenant-ec2-macos_' + suffix
            exists = self.user([podman, 'volume', 'exists', name], allow_failure=True)
            if exists.returncode:
                self.user([podman, 'volume', 'create', '--uid', str(uid), '--gid', str(uid), name])
            # Validate mapped ownership inside the guest, not the unrelated Mac UID.
            command = ('p=$(podman volume inspect --format "{{.Mountpoint}}" ' + name +
                       ') && podman unshare stat -c "%u:%g" "$p"')
            owner = self.user([podman, 'machine', 'ssh', 'tenant-ec2-macos', command]).stdout.strip()
            need(owner == (str(uid) + ':' + str(uid)).encode(), 'volume-owner')

    def services(self):
        podman = '/opt/podman/bin/podman'
        self.volumes()
        # Verify the credential path as the actual application uid before startup.
        self.user([podman, 'run', '--rm', '--network', 'none', '--user', '1000:1000', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '-v', '/var/lib/tenant-ec2/aws-litellm:/run/tenant-ec2/aws:ro,z',
                   '--entrypoint', 'python', 'litellm-gateway:1.103.0-p0.4.1', '-c',
                   "import subprocess; r=subprocess.run(['python','/run/tenant-ec2/aws/credential-process.py','/run/tenant-ec2/aws/credentials.json'],capture_output=True); raise SystemExit(r.returncode)"])
        self.user([podman, 'compose', '-p', 'tenant-ec2-macos', '-f', str(self.data / 'runtime/compose.macos.yaml'), '--env-file', str(self.data / 'service.env'),
                   'up', '-d', '--remove-orphans', '--wait', '--wait-timeout', '180', *self.machine['services']], timeout=240)
        print('macos bootstrap: selected services pass')

    def launchd(self):
        directory(self.root / 'Library/LaunchDaemons', mode=0o755)
        machine_file = self.state / 'machine.json'
        import grp
        atomic(machine_file, json.dumps(self.machine).encode(), gid=grp.getgrnam('admin').gr_gid, mode=0o644)
        # The service-only restart input has no developer password or clone token.
        service_env = {k: v for k, v in self.env.items() if k in set(forms.SERVICE_REQUIRED + forms.SERVICE_OPTIONAL)
                       and k != 'EC2_SNAPSHOT_CLONE_TOKEN'}
        atomic(self.state / 'services.json', json.dumps(service_env).encode())
        template = plistlib.loads((self.repo / 'config/macos/launchd/bootstrap.plist.in').read_bytes())
        template.update(Disabled=False, RunAtLoad=True, StartInterval=60,
                        ProgramArguments=[sys.executable, '-B', str(self.repo / 'config/macos/bootstrap-runtime.py'),
                                          '--resume', '--machine', str(machine_file), '--env-file', str(self.state / 'services.json')])
        path = self.root / 'Library/LaunchDaemons/com.tenant-ec2.macos.bootstrap.plist'
        atomic(path, plistlib.dumps(template), mode=0o644)
        label = 'system/' + template['Label']
        loaded = self.command(['/bin/launchctl', 'print', label], allow_failure=True)
        if loaded.returncode:
            self.command(['/bin/launchctl', 'bootstrap', 'system', str(path)])
        print('macos bootstrap: bounded launchd lifecycle installed')

    def snapshot_access(self):
        repo = safe_path(self.repo)
        project = self.root / 'opt' / self.machine['project']
        need(repo == project / 'macos/snapshot', 'snapshot-location')
        parents = [project, project / 'macos']
        opt = self.root / 'opt'
        info = opt.lstat()
        need(info.st_mode & 0o005 == 0o005 and not info.st_mode & 0o022, 'snapshot-parent-mode')
        for path in [opt, *parents, repo]:
            info = path.lstat()
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0, 'snapshot-owner-type')
        paths = [(path, False) for path in [*parents, repo]]
        # Validate the entire clone before changing modes. Git metadata stays private.
        def walk_error(error):
            raise Invalid('snapshot-unreadable') from None
        for parent, directories, files in os.walk(repo, followlinks=False, onerror=walk_error):
            for name in directories + files:
                path = Path(parent) / name
                paths.append((path, path.relative_to(repo).parts[0] == '.git'))
        modes = []
        for path, private in paths:
            info = path.lstat()
            directory = stat.S_ISDIR(info.st_mode)
            need(info.st_uid == 0 and (directory or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)),
                 'snapshot-owner-type')
            executable = directory or bool(info.st_mode & 0o111)
            modes.append((path, (0o700 if executable else 0o600) if private else (0o755 if executable else 0o644)))
        # macOS ACLs can grant writes beyond the mode bits; remove them only here.
        self.command(['/bin/chmod', '-N', *map(str, parents)])
        self.command(['/bin/chmod', '-RN', str(repo)])
        for path, mode in reversed(modes):
            os.chmod(path, mode, follow_symlinks=False)
        print('macos bootstrap: root-owned snapshot read and traverse pass')

    def apply(self):
        self.identity()
        self.snapshot_access()
        self.disk()
        self.account_setup()
        self.account_lockdown()
        self.tools()
        self.machine_start()
        self.stage_runtime()
        self.build_kits()
        self.services()
        self.launchd()

    def resume(self):
        self.identity()
        self.snapshot_access()
        self.disk(format_allowed=False)
        self.account_readback()
        self.machine_start(initialize=False)
        self.stage_runtime()
        self.services()


def python_dependency(args):
    try:
        import yaml  # noqa: F401
        return
    except ImportError:
        need(not args.check_forms and not args.dry_run and args.root == Path('/')
             and sys.platform == 'darwin' and os.geteuid() == 0, 'python-yaml-required')
    state = directory('/var/db/tenant-ec2-macos')
    environment = safe_path(state / 'python')
    if not environment.exists():
        run([sys.executable, '-I', '-m', 'venv', str(environment)], timeout=120)
    need(environment.is_dir() and environment.stat().st_uid == 0, 'python-environment-owner')
    python = environment / 'bin/python3'
    run([str(python), '-I', '-m', 'pip', '--isolated', 'install', '--only-binary=:all:',
         '--disable-pip-version-check', 'PyYAML==6.0.3'], timeout=180)
    run([str(python), '-I', '-c', 'import yaml'])
    os.execve(str(python), [str(python), '-I', '-B', str(Path(__file__).resolve()), *sys.argv[1:]], clean_env())


def inputs(args):
    machine = json.loads(forms.text_file(args.machine, public=True, fixture=args.root != Path('/')), object_pairs_hook=forms.unique)
    need(args.env_file != '-' or not sys.stdin.isatty(), 'service-pipe-required')
    raw = sys.stdin.read(131073) if args.env_file == '-' else forms.text_file(args.env_file, fixture=args.root != Path('/'))
    env = forms.service_settings(raw)
    memory = load_module('memory', ROOT / 'scripts/aws-memory.py')
    forms.machine_forms(machine, memory)
    forms.service_forms(env, machine)
    need(all("'" not in v and '\\' not in v for v in env.values()), 'environment-quoting')
    need(not any(v for k, v in env.items() if k.startswith('EC2_KIT_TOKEN_')), 'private-kit-clone-not-supported')
    home = args.root / machine['account']['home'].lstrip('/')
    for path in (*home.parents, home, home / '.ssh', home / '.ssh/authorized_keys', home / '.zshrc'):
        need(not path.is_symlink(), 'account-symlink')
    return machine, env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine', required=True, type=Path)
    parser.add_argument('--env-file', default='-')
    parser.add_argument('--root', default='/', type=Path)
    parser.add_argument('--check-forms', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.umask(0o077)
        python_dependency(args)
        machine, env = inputs(args)
        if args.check_forms:
            print('macos bootstrap: forms pass')
            return 0
        if args.dry_run:
            for step in ('verify durable disk identity', 'store first password and create the PIN account',
                         'verify account then restrict default logins', 'install pinned tools', 'verify isolated applehv mounts',
                         'refresh scoped credentials', 'build locked kits', 'start four selected services; agentcore-pending disabled'):
                print('plan: ' + step)
            return 0
        need(args.root == Path('/') and sys.platform == 'darwin' and os.geteuid() == 0, 'native-root-required')
        lock = os.open('/var/run/tenant-ec2-macos.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(lock)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, 'bootstrap-lock')
            bootstrap = Bootstrap(machine, env)
            bootstrap.resume() if args.resume else bootstrap.apply()
        finally:
            os.close(lock)
        return 0
    except Invalid as error:
        print('macos bootstrap: ' + str(error), file=sys.stderr)
    except Exception:
        print('macos bootstrap: operation failed; private detail withheld', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
