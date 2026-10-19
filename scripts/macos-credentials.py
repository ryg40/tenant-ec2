#!/usr/bin/env python3
"""Refresh temporary instance-role credentials without a personal AWS chain."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("credential_process", ROOT / "config/macos/credential-process.py")
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)


def export_credentials(aws, run=subprocess.run):
    # An allowlist prevents SDK profiles, endpoint overrides, container credentials,
    # proxy variables and inherited static keys from changing the credential source.
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": "/var/empty",
           "AWS_CONFIG_FILE": "/dev/null", "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
           "AWS_EC2_METADATA_DISABLED": "false", "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off",
           "AWS_METADATA_SERVICE_TIMEOUT": "2", "AWS_METADATA_SERVICE_NUM_ATTEMPTS": "2"}
    result = run([aws, "configure", "export-credentials", "--format", "process", "--no-cli-pager"],
                 env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    if result.returncode or len(result.stdout) > 32768:
        raise ValueError("credential-export")
    return provider.validate(json.loads(result.stdout))


def check_directory(directory):
    path = Path(directory).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("credential-directory")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("credential-directory")
    return path


def replace(directory, data, uid=None, gid=None):
    provider.validate(data)
    target = directory / "credentials.json"
    if target.is_symlink():
        raise ValueError("credential-destination")
    if target.exists():
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_uid != (uid if uid is not None else os.geteuid()):
            raise ValueError("credential-destination")
    fd, name = tempfile.mkstemp(prefix=".refresh-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if uid is not None:
                os.fchown(stream.fileno(), uid, gid)
            json.dump(data, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def refresh(directory, aws, uid=None, gid=None, export=export_credentials, pause=time.sleep):
    directory = check_directory(directory)
    lock = os.open(directory / ".refresh.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("credential-lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for attempt in range(3):
            try:
                data = export(aws)
                replace(directory, data, uid, gid)
                return
            except (ValueError, OSError, subprocess.SubprocessError):
                if attempt == 2:
                    raise ValueError("credential-refresh") from None
                pause(2 ** attempt)
    finally:
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("refresh", "check", "clear"))
    parser.add_argument("--directory", required=True)
    parser.add_argument("--aws", default="/usr/local/bin/aws")
    parser.add_argument("--reader-uid", type=int)
    parser.add_argument("--reader-gid", type=int)
    args = parser.parse_args()
    try:
        if (args.reader_uid is None) != (args.reader_gid is None):
            raise ValueError("credential-owner")
        if args.reader_uid is not None and (args.reader_uid < 1 or args.reader_gid < 1):
            raise ValueError("credential-owner")
        directory = check_directory(args.directory)
        if args.command == "refresh":
            if not Path(args.aws).is_absolute():
                raise ValueError("credential-command")
            refresh(directory, args.aws, args.reader_uid, args.reader_gid)
        elif args.command == "check":
            provider.read_credentials(directory / "credentials.json", args.reader_uid)
        else:
            # Call only after the selected services stop. Do not remove the lock inode.
            lock = os.open(directory / ".refresh.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                target = directory / "credentials.json"
                if target.is_symlink():
                    raise ValueError("credential-destination")
                target.unlink(missing_ok=True)
            finally:
                os.close(lock)
        print("macos credentials: " + args.command + " pass")
        return 0
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print("macos credentials: unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
