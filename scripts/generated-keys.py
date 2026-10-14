#!/usr/bin/env python3
"""Read and preserve the three generated keys without printing their values."""
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


def checked(text):
    data = json.loads(text or "{}")
    if not isinstance(data, dict) or set(data) - set(NAMES):
        raise ValueError("unexpected names")
    for value in data.values():
        if not isinstance(value, str) or any(c in value for c in "\n\r\0\x27") or value.endswith("\\"):
            raise ValueError("unsupported value")
    return {name: data.get(name, "") for name in NAMES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("read", "put", "status"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--previous")
    parser.add_argument("--require", action="store_true",
                        help="accepted for compatibility; status always requires all names")
    args = parser.parse_args()
    secret = args.project + "/generated"
    command = ["aws", "--region", args.region]
    if args.profile:
        command += ["--profile", args.profile]
    command += ["secretsmanager"]
    try:
        if args.command == "put":
            data = checked(sys.stdin.read())
            if not all(data.values()):
                raise ValueError("incomplete generated keys")
            if args.previous and Path(args.previous).exists():
                if data == checked(Path(args.previous).read_text()):
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
            print("generated: saved " + " ".join(NAMES))
            return 0
        result = subprocess.run(command + ["get-secret-value", "--secret-id", secret,
            "--query", "SecretString", "--output", "text", "--no-cli-pager"],
            capture_output=True, text=True, timeout=60)
        missing = False
        if result.returncode:
            if "ResourceNotFoundException" not in result.stderr:
                raise OSError("read failed")
            missing = True
            data = checked("{}")
        else:
            data = checked(result.stdout)
        if args.command == "read":
            # This stream goes only to a private file, never to a terminal or SSM output.
            json.dump(data, sys.stdout)
        else:
            for name in NAMES:
                print(name + ": " + ("present" if data[name] else "absent"))
            if missing:
                print("generated: secret absent")
                return 3
            if not all(data.values()):
                return 1
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        # AWS errors can echo request values. Do not copy them into a log.
        print("Warning: cannot %s %s; no values are printed." % (args.command, secret), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
