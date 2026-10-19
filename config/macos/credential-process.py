#!/usr/bin/env python3
"""AWS process provider. Stdout belongs to the SDK, never to a terminal log."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import stat
import sys

FIELDS = {"Version", "AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration"}
MIN_TTL = 120


def validate(data, now=None):
    if not isinstance(data, dict) or set(data) != FIELDS or type(data["Version"]) is not int or data["Version"] != 1:
        raise ValueError("credential-invalid")
    for key, pattern in (("AccessKeyId", r"ASIA[A-Z0-9]{12,124}"),
                         ("SecretAccessKey", r"[A-Za-z0-9/+=]{40}"),
                         ("SessionToken", r"[A-Za-z0-9/+=_-]{16,16384}")):
        if not isinstance(data[key], str) or not re.fullmatch(pattern, data[key]):
            raise ValueError("credential-invalid")
    if not isinstance(data["Expiration"], str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)", data["Expiration"]):
        raise ValueError("credential-invalid")
    expiry = dt.datetime.fromisoformat(data["Expiration"].replace("Z", "+00:00"))
    now = now or dt.datetime.now(dt.timezone.utc)
    ttl = (expiry - now).total_seconds()
    if not MIN_TTL < ttl <= 86400:
        raise ValueError("credential-expired")
    return data


def read_credentials(path, expected_uid=None):
    path = Path(path)
    # The mounted directory must remain the same inode while its file is replaced.
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise ValueError("credential-permissions")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        owner = os.geteuid() if expected_uid is None else expected_uid
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != owner or info.st_nlink != 1:
            raise ValueError("credential-permissions")
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
            raw = stream.read(32769)
        if len(raw) > 32768:
            raise ValueError("credential-invalid")
        return validate(json.loads(raw))
    finally:
        os.close(fd)


def main():
    try:
        if len(sys.argv) != 2 or sys.stdout.isatty():
            raise ValueError("credential-private-pipe-required")
        data = read_credentials(sys.argv[1])
        sys.stdout.write(json.dumps(data, separators=(",", ":")))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print("credential-process: unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
