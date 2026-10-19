#!/usr/bin/env python3
"""Offline credential bridge, process-provider and SDK refresh checks."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bridge", ROOT / "scripts/macos-credentials.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def credentials(suffix="A", seconds=3600):
    return {"Version": 1, "AccessKeyId": "ASIA" + suffix * 16, "SecretAccessKey": suffix * 40,
            "SessionToken": suffix * 80,
            "Expiration": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat()}


class CredentialsTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="macos-credentials-test-"))
        self.file = self.work / "credentials.json"
        self.first = credentials()
        bridge.replace(self.work, self.first)

    def run_helper(self, language="python", success=True):
        executable, helper = ((sys.executable, "credential-process.py") if language == "python" else ("node", "credential-process.mjs"))
        run = subprocess.run([executable, str(ROOT / "config/macos" / helper), str(self.file)],
                             capture_output=True, text=True, timeout=10,
                             env={"PATH": os.environ["PATH"], "AWS_PROFILE": "personal", "AWS_ACCESS_KEY_ID": "do-not-resolve"})
        self.assertEqual(run.returncode, 0 if success else 1, run.stderr)
        self.assertNotIn(self.first["SecretAccessKey"], run.stderr)
        if not success:
            self.assertEqual(run.stdout, "")
            self.assertEqual(run.stderr, "credential-process: unavailable\n")
            return None
        return json.loads(run.stdout)

    def test_atomic_replacement_helpers_and_reader_inode(self):
        with self.file.open() as old:
            for lang in ("python", "node"):
                self.assertEqual(self.run_helper(lang), self.first)
            second = credentials("B")
            bridge.replace(self.work, second)
            self.assertEqual(json.load(old), self.first)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        for lang in ("python", "node"):
            self.assertEqual(self.run_helper(lang), second)
        with patch.object(bridge.os, "replace", side_effect=OSError("private-error")):
            with self.assertRaises(OSError):
                bridge.replace(self.work, credentials("C"))
        self.assertEqual(json.loads(self.file.read_text()), second)
        self.assertEqual(list(self.work.glob(".refresh-*")), [])

    def test_invalid_expiry_values_and_permissions(self):
        bad_values = [credentials(seconds=-1), credentials(seconds=60), credentials(seconds=90000),
                      dict(self.first, Version=True), dict(self.first, SecretAccessKey=3),
                      dict(self.first, AccessKeyId="AKIA" + "A" * 16), dict(self.first, Expiration="tomorrow"),
                      dict(self.first, SessionToken="private\nline"), dict(self.first, Unexpected="private")]
        for bad in bad_values:
            self.file.write_text(json.dumps(bad))
            for lang in ("python", "node"):
                self.run_helper(lang, False)
        self.file.write_text(json.dumps(self.first))
        self.file.chmod(0o644)
        for lang in ("python", "node"):
            self.run_helper(lang, False)
        self.file.chmod(0o600)
        saved = self.work / "saved"
        self.file.rename(saved)
        self.file.symlink_to(saved)
        for lang in ("python", "node"):
            self.run_helper(lang, False)
        with self.assertRaises(ValueError):
            bridge.replace(self.work, self.first)

    def test_export_isolated_chain_and_redaction(self):
        seen = []
        def run(argv, **kwargs):
            seen.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.first), "private-error")
        with patch.dict(os.environ, {"AWS_PROFILE": "personal", "AWS_ACCESS_KEY_ID": "private", "AWS_ENDPOINT_URL": "https://invalid.example", "HTTPS_PROXY": "https://invalid.example"}):
            self.assertEqual(bridge.export_credentials("/fake/aws", run), self.first)
        argv, kwargs = seen[0]
        self.assertEqual(argv, ["/fake/aws", "configure", "export-credentials", "--format", "process", "--no-cli-pager"])
        self.assertEqual(kwargs["env"]["AWS_CONFIG_FILE"], "/dev/null")
        self.assertEqual(kwargs["env"]["AWS_SHARED_CREDENTIALS_FILE"], "/dev/null")
        self.assertEqual(kwargs["env"]["HOME"], "/var/empty")
        for name in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_ENDPOINT_URL", "HTTPS_PROXY"):
            self.assertNotIn(name, kwargs["env"])
        self.assertLessEqual(kwargs["timeout"], 20)
        aws = self.work / "aws"
        aws.write_text('#!/bin/sh\nprintf "%s" "private SDK error" >&2\nexit 1\n')
        aws.chmod(0o700)
        run = subprocess.run([sys.executable, str(ROOT / "scripts/macos-credentials.py"), "refresh", "--directory", str(self.work), "--aws", str(aws)], capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stderr, "macos credentials: unavailable\n")
        self.assertEqual(run.stdout, "")
        self.assertEqual(json.loads(self.file.read_text()), self.first)

    def test_bounded_retry_and_bad_destination(self):
        attempts, waits = [], []
        def failing(_):
            attempts.append(1)
            raise ValueError("never-print-private")
        with self.assertRaisesRegex(ValueError, "credential-refresh"):
            bridge.refresh(self.work, "/fake/aws", export=failing, pause=waits.append)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(waits, [1, 2])
        self.assertEqual(json.loads(self.file.read_text()), self.first)
        import fcntl
        with (self.work / ".refresh.lock").open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                bridge.refresh(self.work, "/fake/aws", export=lambda _: self.first)
        self.work.chmod(0o755)
        with self.assertRaises(ValueError):
            bridge.refresh(self.work, "/fake/aws", export=lambda _: self.first)
        self.work.chmod(0o700)
        linked = self.work / "link"
        linked.symlink_to(self.work, target_is_directory=True)
        with self.assertRaises(ValueError):
            bridge.check_directory(linked)

    def test_python_sdk_process_provider_refresh(self):
        from botocore.credentials import ProcessProvider
        command = f'{sys.executable} {ROOT / "config/macos/credential-process.py"} {self.file}'
        provider = ProcessProvider("ec2-instance", lambda: {"profiles": {"ec2-instance": {"credential_process": command}}})
        sdk = provider.load()
        self.assertEqual(sdk.get_frozen_credentials().access_key, self.first["AccessKeyId"])
        next_value = credentials("B")
        bridge.replace(self.work, next_value)
        sdk._expiry_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        self.assertEqual(sdk.get_frozen_credentials().access_key, next_value["AccessKeyId"])
        self.file.write_text(json.dumps(credentials(seconds=-10)))
        sdk._expiry_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        with self.assertRaises(Exception):
            sdk.get_frozen_credentials()

    def test_node_cache_fixture_refresh(self):
        # This exercises the helper and expiration contract, not the uninstalled JS SDK.
        cached = self.run_helper("node")
        bridge.replace(self.work, credentials("C"))
        cached["Expiration"] = "2000-01-01T00:00:00Z"
        if dt.datetime.fromisoformat(cached["Expiration"].replace("Z", "+00:00")) <= dt.datetime.now(dt.timezone.utc):
            cached = self.run_helper("node")
        self.assertEqual(cached["AccessKeyId"], "ASIA" + "C" * 16)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CredentialsTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("macos credentials: pass (offline helpers, Python SDK, isolated chain and atomic files)")
        print("Not verified: Mac-to-guest replacement, UID mapping or real Node SDK refresh")
    sys.exit(not result.wasSuccessful())
