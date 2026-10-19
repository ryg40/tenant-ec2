#!/usr/bin/env python3
"""Read and preserve one variant's generated keys without printing their values."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


NAMES = (
    "EC2_OPENVIKING_LITELLM_API_KEY",
    "EC2_PI_DURABLE_LITELLM_API_KEY",
    "EC2_PI_DURABLE_OPENVIKING_API_KEY",
)


def names_for(variant):
    return ("EC2_MACOS_DEVELOPER_PASSWORD", "EC2_PI_DURABLE_LITELLM_API_KEY") if variant == "macos" else NAMES


def complete(data, variant):
    # The gateway key is optional while the Mac agent profile is disabled.
    return bool(data["EC2_MACOS_DEVELOPER_PASSWORD"]) if variant == "macos" else all(data.values())


def checked(text, variant="homelab"):
    names = names_for(variant)
    data = json.loads(text or "{}")
    if not isinstance(data, dict) or set(data) - set(names):
        raise ValueError("unexpected names")
    for value in data.values():
        if not isinstance(value, str) or any(c in value for c in "\n\r\0\x27") or value.endswith("\\"):
            raise ValueError("unsupported value")
    return {name: data.get(name, "") for name in names}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("read", "put", "status"))
    parser.add_argument("--variant", choices=("homelab", "macos"), default="homelab")
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--previous")
    parser.add_argument("--require", action="store_true",
                        help="accepted for compatibility; status checks the required variant names")
    args = parser.parse_args()
    # Reject path separators before constructing either secret namespace.
    import re
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", args.project):
        print("generated: invalid project", file=sys.stderr)
        return 1
    names = names_for(args.variant)
    secret = args.project + ("/macos/generated" if args.variant == "macos" else "/generated")
    command = ["aws", "--region", args.region]
    if args.profile:
        command += ["--profile", args.profile]
    command += ["secretsmanager"]
    try:
        if args.command == "put":
            data = checked(sys.stdin.read(), args.variant)
            if args.variant == "macos":
                # Merge against the remote object, never erase a sibling key or rotate
                # the first-login password during an unrelated generated-key update.
                current = subprocess.run(command + ["get-secret-value", "--secret-id", secret,
                    "--query", "SecretString", "--output", "text", "--no-cli-pager"],
                    capture_output=True, text=True, timeout=60)
                if current.returncode and "ResourceNotFoundException" not in current.stderr:
                    raise OSError("read failed")
                previous = checked("{}" if current.returncode else current.stdout, args.variant)
                password_field = "EC2_MACOS_DEVELOPER_PASSWORD"
                if previous[password_field] and data[password_field] and previous[password_field] != data[password_field]:
                    raise ValueError("password replacement refused")
                data = {name: data[name] or previous[name] for name in names}
                if complete(data, args.variant) and data == previous:
                    print("generated: unchanged")
                    return 0
            if not complete(data, args.variant):
                raise ValueError("incomplete generated keys")
            if args.variant != "macos" and args.previous and Path(args.previous).exists():
                if data == checked(Path(args.previous).read_text(), args.variant):
                    print("generated: unchanged")
                    return 0
            # Keep the file private even when the bootstrap uses umask 022.
            with tempfile.TemporaryDirectory(prefix="generated-keys-") as work:
                path = Path(work) / "payload.json"
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
                    json.dump(data, handle)
                result = subprocess.run(command + ["put-secret-value", "--secret-id", secret,
                    "--secret-string", "file://" + str(path), "--no-cli-pager"],
                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise OSError("write failed")
            print("generated: saved " + " ".join(names))
            return 0
        result = subprocess.run(command + ["get-secret-value", "--secret-id", secret,
            "--query", "SecretString", "--output", "text", "--no-cli-pager"],
            capture_output=True, text=True, timeout=60)
        missing = False
        if result.returncode:
            if "ResourceNotFoundException" not in result.stderr:
                raise OSError("read failed")
            missing = True
            data = checked("{}", args.variant)
        else:
            data = checked(result.stdout, args.variant)
        if args.command == "read":
            # This stream goes only to a private file, never to a terminal or SSM output.
            json.dump(data, sys.stdout)
        else:
            for name in names:
                print(name + ": " + ("present" if data[name] else "absent"))
            if missing:
                print("generated: secret absent")
                return 3
            if not complete(data, args.variant):
                return 1
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        # AWS errors can echo request values. Do not copy them into a log.
        print("Warning: cannot %s %s; no values are printed." % (args.command, secret), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
