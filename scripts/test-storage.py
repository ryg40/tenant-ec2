#!/usr/bin/env python3
"""Offline durable-start and EFS identity fixtures. No service or mount runs."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
MOUNTS = ("/srv/data", "/srv/efs/open-knowledge", "/srv/efs/openviking", "/srv/efs/shared")
UNITS = ("srv-data.mount", r"srv-efs-open\x2dknowledge.mount", "srv-efs-openviking.mount", "srv-efs-shared.mount")
BOOT = (ROOT / "scripts/host-bootstrap.sh").read_text()
USERDATA = (ROOT / "scripts/host-userdata.sh").read_text()
DROPIN = re.search(r"storage_unit='(.*?)'\n", BOOT, re.S).group(1) + "\n"
# The file sets of the contract, one for each value of EC2_OPTIONS: the table FILE_SETS
# of scripts/check.sh. The check script compares it with the bootstrap.
FILE_SETS = dict(line.split("|") for line in re.search(
    r"^FILE_SETS='(.*?)'$", (ROOT / "scripts/check.sh").read_text(), re.S | re.M).group(1).splitlines())
GUARD = re.search(r'    unit="(.*?)"\n', BOOT, re.S).group(1).replace("$GUARD", "tenant-ec2-metadata-guard").replace("$gateway", "198.51.100.9") + "\n"


class CfnLoader(yaml.SafeLoader):
    pass


def intrinsic(loader, tag, node):
    if isinstance(node, yaml.ScalarNode):
        return {tag: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag: loader.construct_sequence(node)}
    return {tag: loader.construct_mapping(node)}


CfnLoader.add_multi_constructor("!", intrinsic)


def function(source, name):
    return source[source.index(name + "() {"):source.index("\n}\n", source.index(name + "() {")) + 3]


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, timeout=30, **kwargs)


class StorageTests(unittest.TestCase):
    def test_first_boot_gate_matches_and_precedes_package_install(self):
        first = re.search(r"tenant-ec2-storage.conf <<'EOT'\n(.*?)\nEOT", USERDATA, re.S).group(1) + "\n"
        self.assertEqual(first, DROPIN)
        main = USERDATA[USERDATA.index("\ndocker_engine\n"):]
        self.assertLess(main.index("docker_engine"), main.index("packages"))
        self.assertNotIn("systemctl enable --now docker", USERDATA)
        self.assertNotIn("systemctl start docker", USERDATA)
        self.assertNotIn("systemctl restart docker", USERDATA)
        for name in MOUNTS:
            self.assertIn(name, DROPIN.split("RequiresMountsFor=", 1)[1].splitlines()[0].split())
        self.assertEqual(DROPIN.count("ExecStartPre="), 4)
        self.assertIn("Requires=tenant-ec2-metadata-guard.service", DROPIN)
        self.assertIn("After=" + " ".join(UNITS) + " tenant-ec2-metadata-guard.service", DROPIN)
        self.assertIn("BindsTo=" + " ".join(UNITS), DROPIN)

    def test_bootstrap_guards_precede_docker_restart_and_stack_start(self):
        settings = function(BOOT, "host_settings")
        self.assertLess(settings.index("storage_dependencies"), settings.index("systemctl restart docker"))
        self.assertLess(settings.index('systemctl start "$GUARD.service"'), settings.index("systemctl restart docker"))
        self.assertLess(settings.index('"$bin" apply "$gateway"'), settings.index("systemctl restart docker"))
        self.assertIn("run systemctl enable --now docker", settings)
        self.assertIn("Before=docker.service", GUARD)
        self.assertNotIn("After=docker", GUARD)
        main = BOOT[BOOT.index("\nstack_values\n"):]
        self.assertLess(main.index("host_settings"), main.index("\nstart\n"))
        self.assertIn('mountpoint -q "$dir"', function(BOOT, "stack_values"))
        self.assertIn('mountpoint -q "$dir"', function(BOOT, "compose"))

    def test_bootstrap_installs_both_guards_before_daemon_change(self):
        # The same order with each edge: the option pangolin adds only the kernel module.
        self.assertEqual(len(FILE_SETS), 4)
        for options in FILE_SETS:
            with self.subTest(options=options):
                self.installs_both_guards("pangolin" in options.split(","))

    def installs_both_guards(self, pangolin):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bin_dir = base / "bin"
            bin_dir.mkdir()
            calls = base / "calls"
            guard_source = base / "scripts/host-metadata-guard.sh"
            guard_source.parent.mkdir()
            guard_source.write_text('#!/bin/sh\necho "guard $1" >>"$CALLS"\n')
            guard_source.chmod(0o755)
            control = bin_dir / "systemctl"
            control.write_text('''#!/bin/sh
case "$1" in
    is-enabled|is-active) exit 1 ;;
    restart|enable)
        case "$*" in
            *docker*)
                [ -f "$FIXTURE_ROOT/etc/systemd/system/docker.service.d/tenant-ec2-storage.conf" ] || exit 98
                [ -f "$FIXTURE_ROOT/etc/systemd/system/tenant-ec2-metadata-guard.service" ] || exit 98
                grep -qxF 'systemctl start tenant-ec2-metadata-guard.service' "$CALLS" || exit 98 ;;
        esac ;;
esac
echo "systemctl $*" >>"$CALLS"
''')
            control.chmod(0o755)
            module = bin_dir / "modprobe"
            module.write_text('#!/bin/sh\necho modprobe >>"$CALLS"\n')
            module.chmod(0o755)
            script = base / "fixture.sh"
            script.write_text('''set -eu
root=$1
repo=$1
dry_run=0
opt_pangolin=$2
GUARD=tenant-ec2-metadata-guard
say() { :; }
skip() { :; }
die() { echo "$*" >&2; exit 1; }
run() { "$@"; }
gateway_address() { echo 198.51.100.9; }
helper() {
    [ "$3" != check ] || return 3
    printf '{}\\n' >"$2"
    echo daemon-write >>"$CALLS"
}
''' + "\n".join(function(BOOT, name) for name in ("same_text", "storage_dependencies", "host_settings")) + '\nhost_settings\n')
            environment = {"PATH": str(bin_dir) + ":" + os.environ["PATH"], "CALLS": str(calls), "FIXTURE_ROOT": str(base)}
            answer = run(["sh", str(script), str(base), "1" if pangolin else "0"], env=environment)
            self.assertEqual(answer.returncode, 0, answer.stderr)
            self.assertEqual((base / "etc/systemd/system/docker.service.d/tenant-ec2-storage.conf").read_text(), DROPIN)
            lines = calls.read_text().splitlines()
            self.assertLess(lines.index("guard apply"), lines.index("systemctl start tenant-ec2-metadata-guard.service"))
            self.assertLess(lines.index("systemctl start tenant-ec2-metadata-guard.service"), lines.index("daemon-write"))
            self.assertLess(lines.index("daemon-write"), lines.index("systemctl restart docker"))
            self.assertEqual(lines[-1], "systemctl enable --now docker")
            # The core has no tunnel: it loads no kernel module.
            self.assertEqual("modprobe" in lines, pangolin)

    def test_missing_delayed_and_lost_mounts_block_every_docker_start(self):
        prechecks = [line.partition("=")[2].split() for line in DROPIN.splitlines() if line.startswith("ExecStartPre=")]
        self.assertEqual([item[-1] for item in prechecks], list(MOUNTS))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            paths, marker = base / "mounted", base / "container-starts"
            checker = base / "mountpoint"
            checker.write_text('#!/bin/sh\ngrep -qxF -- "$2" "$MOUNT_FIXTURE"\n')
            checker.chmod(0o755)
            for name in MOUNTS:
                underlying = base / name.lstrip("/")
                underlying.mkdir(parents=True)
                (underlying / "sentinel").write_text("underlying path stays unused\n")
            environment = {"PATH": os.environ["PATH"], "MOUNT_FIXTURE": str(paths)}

            def start_fixture():
                # Run the exact production precheck arguments; only the executable is a stub.
                for argv in prechecks:
                    if run([str(checker), *argv[1:]], env=environment).returncode:
                        return False
                with marker.open("a") as handle:
                    handle.write("started\n")
                return True

            for missing in MOUNTS:
                paths.write_text("\n".join(name for name in MOUNTS if name != missing) + "\n")
                self.assertFalse(start_fixture(), missing)
                self.assertFalse(marker.exists())
            # A delayed mount does not release startup until the final check succeeds.
            paths.write_text("\n".join(MOUNTS[:-1]) + "\n")
            self.assertFalse(start_fixture())
            paths.write_text("\n".join(MOUNTS) + "\n")
            self.assertTrue(start_fixture())
            # Later daemon restarts recheck mounts, even after a successful first start.
            for missing in MOUNTS:
                paths.write_text("\n".join(name for name in MOUNTS if name != missing) + "\n")
                self.assertFalse(start_fixture(), missing)
                self.assertEqual(marker.read_text(), "started\n")
            self.assertTrue(all(path.read_text() == "underlying path stays unused\n" for path in base.glob("srv/**/sentinel")))

    def test_compose_refuses_existing_underlying_paths(self):
        # The durable mounts come before a Compose command, for each of the four file sets.
        self.assertEqual(len(FILE_SETS), 4)
        for options, files in FILE_SETS.items():
            with self.subTest(options=options):
                self.compose_refuses_underlying_paths(files)

    def compose_refuses_underlying_paths(self, files):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bin_dir = base / "bin"
            bin_dir.mkdir()
            mounted, calls = base / "mounted", base / "calls"
            for name, text in {
                "mountpoint": 'grep -qxF -- "$2" "$MOUNT_FIXTURE"',
                "docker": 'echo "docker $*" >>"$CALLS"',
            }.items():
                path = bin_dir / name
                path.write_text("#!/bin/sh\n" + text + "\n")
                path.chmod(0o755)
            script = base / "fixture.sh"
            script.write_text('set -eu\ndry_run=0\nroot=\ndata=/srv/data\nefs=/srv/efs\nrepo=/fixture\nplan_files="' + files + '"\nenv_file=/fixture/env\nMADE_NAMES=\ndie() { echo "$*" >&2; exit 1; }\n' + function(BOOT, "compose") + '\ncompose restart open-knowledge\n')
            environment = {"PATH": str(bin_dir) + ":" + os.environ["PATH"], "HOME": str(base), "MOUNT_FIXTURE": str(mounted), "CALLS": str(calls)}
            # Compose clears the environment. Preserve the fixture path in its Docker stub.
            (bin_dir / "docker").write_text('#!/bin/sh\necho "docker $*" >>"' + str(calls) + '"\n')
            for missing in MOUNTS:
                mounted.write_text("\n".join(name for name in MOUNTS if name != missing) + "\n")
                answer = run(["sh", str(script)], env=environment)
                self.assertNotEqual(answer.returncode, 0)
                self.assertIn(missing + " is not a mount point", answer.stderr)
                self.assertFalse(calls.exists())
            mounted.write_text("\n".join(MOUNTS) + "\n")
            self.assertEqual(run(["sh", str(script)], env=environment).returncode, 0)
            self.assertIn("restart open-knowledge", calls.read_text())
            # The command names each Compose file of the file set, in its order.
            words = calls.read_text().split()
            self.assertEqual([words[i + 1] for i, word in enumerate(words) if word == "-f"],
                             ["/fixture/" + name for name in files.split()])

    def test_systemd_mount_paths_and_ordering_have_no_cycle(self):
        analyze = shutil.which("systemd-analyze")
        escape = shutil.which("systemd-escape")
        generator = Path("/usr/lib/systemd/system-generators/systemd-fstab-generator")
        self.assertIsNotNone(analyze, "systemd-analyze is required for the isolated syntax check")
        self.assertIsNotNone(escape)
        self.assertTrue(generator.is_file())
        for path, unit in zip(MOUNTS, UNITS):
            self.assertEqual(run([escape, "--path", "--suffix=mount", path]).stdout.strip(), unit)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            units = base / "etc/systemd/system"
            units.mkdir(parents=True)
            (units / "docker.service.d").mkdir()
            (units / "docker.service.d/tenant-ec2-storage.conf").write_text(DROPIN)
            # Preserve the AL2023 dependency shape. Executables and the socket are fixtures.
            (units / "docker.service").write_text('[Unit]\nAfter=network-online.target docker.socket firewalld.service containerd.service time-set.target\nWants=network-online.target containerd.service\nRequires=docker.socket\n[Service]\nExecStart=/usr/bin/true\nRestart=always\n[Install]\nWantedBy=multi-user.target\n')
            (units / "docker.socket").write_text('[Socket]\nListenStream=/run/fixture-docker.sock\n')
            (units / "tenant-ec2-metadata-guard.service").write_text(GUARD)
            for name in ("containerd.service", "firewalld.service"):
                (units / name).write_text('[Service]\nExecStart=/usr/bin/true\n')
            for name in ("sysinit", "basic", "shutdown", "network", "network-online", "local-fs", "local-fs-pre", "remote-fs", "remote-fs-pre", "sockets", "multi-user", "time-set", "umount"):
                (units / (name + ".target")).write_text('[Unit]\nDescription=fixture target\n')
            for name in ("true", "mountpoint"):
                executable = base / "usr/bin" / name
                executable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(shutil.which(name), executable)
            guard_bin = base / "usr/local/sbin/tenant-ec2-metadata-guard"
            guard_bin.parent.mkdir(parents=True)
            shutil.copy2(shutil.which("true"), guard_bin)
            # Render the production fstab lines without calling mount or changing /etc/fstab.
            fstab = base / "fstab"
            script = base / "fstab-fixture.sh"
            script.write_text('set -eu\nDATA_DIR=/srv/data\nEFS_DIR=/srv/efs\nEC2_EFS_ID=fs-fixture\nuuid=12345678-1234-1234-1234-123456789012\nfstab_line() { printf "%s\\n" "$2"; }\n' + re.search(r'^    fstab_line "\$DATA_DIR".*$', USERDATA, re.M).group(0) + '\n' + re.search(r'^    fstab_line "\$dir".*$', USERDATA, re.M).group(0).replace('$dir', '$EFS_DIR/$1') + '\n')
            rendered = [run(["sh", str(script), name, "fsap-fixture"]).stdout.splitlines() for name in ("open-knowledge", "openviking", "shared")]
            data_line = rendered[0][0]
            self.assertEqual(data_line, "UUID=12345678-1234-1234-1234-123456789012 /srv/data ext4 defaults,nofail 0 2")
            lines = [item[-1] for item in rendered]
            self.assertTrue(all("_netdev,nofail,tls,iam,accesspoint=" in line for line in lines))
            fstab.write_text("\n".join([data_line, *lines]) + "\n")
            generated = base / "generated"
            generated.mkdir()
            early, late = base / "early", base / "late"
            early.mkdir()
            late.mkdir()
            answer = run([str(generator), str(generated), str(early), str(late)], env=dict(os.environ, SYSTEMD_FSTAB=str(fstab)))
            self.assertEqual(answer.returncode, 0, answer.stderr)
            for path, unit in zip(MOUNTS[1:], UNITS[1:]):
                mount = generated / unit
                self.assertIn("Where=" + path, mount.read_text())
                shutil.copy2(mount, units / unit)
            # The generator ignores UUID devices inside containers. Check EBS syntax separately.
            (units / UNITS[0]).write_text('[Mount]\nWhat=/dev/disk/by-uuid/12345678-1234-1234-1234-123456789012\nWhere=/srv/data\nType=ext4\nOptions=defaults,nofail\n')
            device = base / "dev/disk/by-uuid/12345678-1234-1234-1234-123456789012"
            device.parent.mkdir(parents=True)
            device.touch()
            answer = run([analyze, "verify", "--root=" + str(base), "--generators=no", "--man=no", "docker.service", "tenant-ec2-metadata-guard.service", *UNITS], env=dict(os.environ, SYSTEMD_LOG_LEVEL="debug"))
            self.assertEqual(answer.returncode, 0, answer.stderr)
            self.assertNotIn("cycle", answer.stderr.lower())
            docker_state = answer.stdout.split("-> Unit docker.service:", 1)[1].split("-> Unit ", 1)[0]
            for unit in UNITS:
                for kind in ("Requires", "BindsTo", "After"):
                    self.assertIn(kind + ": " + unit + " ", docker_state)
            self.assertIn("After: tenant-ec2-metadata-guard.service ", docker_state)

    def test_efs_identity_matches_pinned_default_without_runtime_override(self):
        template = yaml.load((ROOT / "scripts/aws-host.cfn.yaml").read_text(), Loader=CfnLoader)
        resources = template["Resources"]
        for name, uid in (("AccessPointOpenKnowledge", "10001"), ("AccessPointOpenViking", "1101"), ("AccessPointShared", "1102")):
            access = resources[name]["Properties"]
            self.assertEqual(access["PosixUser"], {"Uid": uid, "Gid": uid})
            self.assertEqual(access["RootDirectory"]["CreationInfo"], {"OwnerUid": uid, "OwnerGid": uid, "Permissions": "0750"})
        compose = (ROOT / "compose.yaml").read_text().split("  open-knowledge:\n", 1)[1].split("\n  ok-auth:", 1)[0]
        self.assertNotRegex(compose, r"(?m)^    user:")
        self.assertNotIn("safe.directory", compose)

    def test_git_owner_alignment_without_safe_directory(self):
        self.assertEqual(os.geteuid(), 0, "the owner/process fixture needs root to set isolated numeric IDs")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            base.chmod(0o755)
            home, repo = base / "home", base / "repo"
            home.mkdir(mode=0o700)
            repo.mkdir(mode=0o750)
            os.chown(home, 10001, 10001)
            os.chown(repo, 10001, 10001)
            environment = {"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}

            def identity():
                os.setgroups([])
                os.setgid(10001)
                os.setuid(10001)

            def git(*args):
                return run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-c", "credential.interactive=false", *args], env=environment, preexec_fn=identity)

            self.assertEqual(git("init", "-q").returncode, 0)
            (repo / "page.md").write_text("fixture\n")
            os.chown(repo / "page.md", 10001, 10001)
            # Pinned git-paths.ts PORCELAIN_STATUS_ARGS plus rawZ's NUL output flag.
            args = ("status", "--porcelain", "-uall", "-z")
            os.chown(repo, 1100, 1100)
            # EFS grants access through its enforced identity despite the visible owner.
            # A local read permission reproduces that view without making an EFS mount.
            repo.chmod(0o755)
            rejected = git(*args)
            self.assertEqual(rejected.returncode, 128)
            self.assertIn("dubious ownership", rejected.stderr)
            os.chown(repo, 10001, 10001)
            accepted = git(*args)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertIn("page.md", accepted.stdout)
            self.assertEqual(git("rev-parse", "--show-toplevel").returncode, 0)
            probe = run([sys.executable, "-I", "-c", "from pathlib import Path; Path.home().joinpath('writable').write_text('fixture')"], env=environment, preexec_fn=identity)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertFalse((home / ".gitconfig").exists())


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StorageTests))
    if result.wasSuccessful():
        print("storage tests: pass (%d tests)" % result.testsRun)
    sys.exit(0 if result.wasSuccessful() else 1)
