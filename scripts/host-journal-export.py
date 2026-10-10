#!/usr/bin/env python3
"""Export journald JSON records to rotating files. CloudWatch reads files, not journals."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile

MAX_BYTES = 10 * 1024 * 1024
KEEP_FILES = 7


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def rotate(path, limit=MAX_BYTES, keep=KEEP_FILES):
    if not path.exists() or path.stat().st_size < limit:
        return
    for number in range(keep, 0, -1):
        source = path if number == 1 else Path(str(path) + ".%d" % (number - 1))
        target = Path(str(path) + ".%d" % number)
        if source.exists():
            os.replace(source, target)


def export(state, logs, journal="journalctl"):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # journalctl updates its cursor at exit. Commit that cursor only after both
        # output files are on disk. A crash can replay records, but cannot skip them.
        with tempfile.TemporaryDirectory(dir=state) as work:
            cursor = Path(work) / "cursor"
            saved = state / "cursor"
            if saved.exists():
                cursor.write_bytes(saved.read_bytes())
            command = [journal, "--system", "--no-pager", "--quiet", "--all",
                       "--output=json", "--cursor-file=" + str(cursor)]
            if not saved.exists():
                command.append("--boot=0")
            with (Path(work) / "records").open("w+b") as records:
                subprocess.run(command, stdout=records, check=True, timeout=45)
                records.seek(0)
                for line in records:
                    item = json.loads(line)
                    name = "docker" if item.get("CONTAINER_ID_FULL") or item.get("CONTAINER_NAME") else "journal"
                    path = logs / (name + ".log")
                    rotate(path)
                    with path.open("ab") as output:
                        output.write(line.rstrip(b"\n") + b"\n")
                        output.flush()
                        os.fsync(output.fileno())
            sync_directory(logs)
            if cursor.exists():
                with cursor.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(cursor, saved)
                sync_directory(state)


def configure(root, project, region, group):
    config = root / "etc" / project
    config.mkdir(parents=True, exist_ok=True)
    logs = "/var/log/" + project
    state = "/var/lib/" + project + "/journal-export"
    unit = project + "-journal-export"
    units = root / "etc/systemd/system"
    units.mkdir(parents=True, exist_ok=True)
    (units / (unit + ".service")).write_text("""[Unit]
Description=Export the system and Docker journal to files
After=systemd-journald.service

[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/local/sbin/{unit} --state {state} --logs {logs}
TimeoutStartSec=60
""".format(unit=unit, state=state, logs=logs), encoding="utf-8")
    (units / (unit + ".timer")).write_text("""[Unit]
Description=Export the journal every minute

[Timer]
OnBootSec=30
OnUnitInactiveSec=60
Unit={unit}.service

[Install]
WantedBy=timers.target
""".format(unit=unit), encoding="utf-8")
    collected = [{"file_path": logs + "/" + name + ".log",
                  "log_group_name": group, "log_stream_name": "{instance_id}/" + name}
                 for name in ("docker", "journal")]
    agent = {"agent": {"region": region},
             "logs": {"logs_collected": {"files": {"collect_list": collected}}}}
    (config / "cloudwatch-agent.json").write_text(json.dumps(agent, indent=2) + "\n", encoding="utf-8")
    for path in (root / logs.lstrip("/"), root / state.lstrip("/")):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    for name in ("docker", "journal"):
        path = root / logs.lstrip("/") / (name + ".log")
        path.touch(mode=0o600, exist_ok=True)
        os.chmod(path, 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--project")
    parser.add_argument("--region")
    parser.add_argument("--log-group")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--logs", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.configure:
        if not all((args.project, args.region, args.log_group)):
            parser.error("configure needs project, region and log-group")
        configure(args.root, args.project, args.region, args.log_group)
    else:
        if not all((args.state, args.logs)):
            parser.error("export needs state and logs")
        export(args.state, args.logs)


if __name__ == "__main__":
    main()
