#!/usr/bin/env python3
"""Test the sign-in helper against a stub token endpoint, a stub Cognito and a stub STS. No AWS call."""
import base64
import datetime as dt
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

HELPER = Path(__file__).resolve().with_name("memory-signin.py")
CALLBACK = "http://localhost:8400/callback"
PROFILE = "tenant-ec2-memory-dev-person-alice"
IDENTITY = "us-east-1:00000000-0000-4000-8000-0000000000b0"
SUB = "00000000-0000-4000-8000-00000000000a"
SIGNIN = {"schemaVersion": 1, "profile": PROFILE, "actorId": "alice", "region": "us-east-1",
          "domain": "tenant-ec2-memory-dev.auth.us-east-1.amazoncognito.com", "clientId": "fixtureclient0",
          "identityPoolId": "us-east-1:00000000-0000-4000-8000-000000000000",
          "providerName": "cognito-idp.us-east-1.amazonaws.com/us-east-1_Fixture0",
          "roleArn": "arn:aws:iam::" + "0" * 12 + ":role/tenant-ec2-memory-dev-person-alice-role"}
FIELDS = ["Version", "AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration"]
SECRET = "S" * 40
REFRESH = "fixture-refresh-token"


def jwt(payload):
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return part({"alg": "none"}) + "." + part(payload) + ".fixture"


class Stub(http.server.ThreadingHTTPServer):
    """The token endpoint, Cognito identity and STS of one test."""
    request_queue_size = 64

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.calls, self.challenge, self.valid, self.sts_ttl, self.issued = [], None, set(), 3600, 0
        self.revoke = (200, "")


class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, status, body, kind="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        stub = self.server
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
        if self.path.startswith("/cognito-identity/"):
            body = json.loads(raw)
            target = self.headers["X-Amz-Target"].split(".")[-1]
            stub.calls.append((target, body))
            if list(body["Logins"]) != [SIGNIN["providerName"]]:
                return self.reply(400, {"__type": "NotAuthorizedException"})
            if target == "GetId":
                assert body["IdentityPoolId"] == SIGNIN["identityPoolId"]
                return self.reply(200, {"IdentityId": IDENTITY})
            assert body["IdentityId"] == IDENTITY
            return self.reply(200, {"IdentityId": IDENTITY, "Token": "fixture-openid-token"})
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        stub.calls.append((self.path, form))
        if self.path == "/oauth2/token":
            assert form["client_id"] == SIGNIN["clientId"]
            if form["grant_type"] == "authorization_code":
                verifier = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
                assert verifier.rstrip(b"=").decode() == stub.challenge and form["code"] == "fixture-code"
                assert form["redirect_uri"] == CALLBACK
                stub.valid.add(REFRESH)
                return self.reply(200, {"id_token": jwt({"sub": SUB}), "access_token": "fixture-access",
                                        "refresh_token": REFRESH, "expires_in": 3600})
            if form["refresh_token"] not in stub.valid:
                return self.reply(400, {"error": "invalid_grant"})
            return self.reply(200, {"id_token": jwt({"sub": SUB}), "access_token": "fixture-access"})
        if self.path == "/oauth2/revoke":
            if stub.revoke[0] == 200:
                stub.valid.discard(form["token"])
            return self.reply(*stub.revoke)
        assert self.path == "/sts/" and form["Action"] == "AssumeRoleWithWebIdentity"
        stub.issued += 1
        expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=stub.sts_ttl)
        return self.reply(200, f"""<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
<AssumeRoleWithWebIdentityResult><Credentials>
<AccessKeyId>ASIAFIXTURE{stub.issued:09d}</AccessKeyId><SecretAccessKey>{SECRET}</SecretAccessKey>
<SessionToken>fixture-session-token-{stub.issued}</SessionToken><Expiration>{expiry.strftime("%Y-%m-%dT%H:%M:%SZ")}</Expiration>
</Credentials></AssumeRoleWithWebIdentityResult></AssumeRoleWithWebIdentityResponse>""", "text/xml")

    def log_message(self, *_args):
        pass


class SignInTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="memory-signin-test-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.home = self.root / "home"
        self.home.mkdir()
        self.signin = self.root / (PROFILE + ".signin.json")
        self.signin.write_text(json.dumps(SIGNIN))
        self.signin.chmod(0o600)
        self.cache = self.home / ".cache/tenant-ec2-memory-signin" / PROFILE
        self.stub = Stub()
        threading.Thread(target=self.stub.serve_forever, daemon=True).start()
        self.addCleanup(self.stub.server_close)
        self.addCleanup(self.stub.shutdown)
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "AWS_CONFIG_FILE": str(self.root / "config"),
                    "MEMORY_SIGNIN_TEST_ENDPOINT": f"http://127.0.0.1:{self.stub.server_address[1]}"}

    def helper(self, *args, ok=True, stdin=subprocess.DEVNULL):
        result = subprocess.run([sys.executable, str(HELPER), *args, "--signin", str(self.signin)], env=self.env,
                                stdin=stdin, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        for value in (REFRESH, "fixture-openid-token", "fixture-access"):
            self.assertNotIn(value, result.stdout + result.stderr)
        self.assertNotIn(SECRET, result.stderr)
        return result

    def login(self, wrong_state=False):
        process = subprocess.Popen([sys.executable, str(HELPER), "login", "--no-browser", "--signin", str(self.signin)],
                                   env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        url = urllib.parse.urlsplit(process.stdout.readline().strip())
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        self.assertEqual(url.netloc, SIGNIN["domain"])
        self.assertEqual((query["code_challenge_method"], query["redirect_uri"], query["client_id"]),
                         ("S256", CALLBACK, SIGNIN["clientId"]))
        self.stub.challenge = query["code_challenge"]
        state = "wrong" + query["state"] if wrong_state else query["state"]
        out, err = process.communicate(f"{CALLBACK}?code=fixture-code&state={state}\n", timeout=20)
        return process.returncode, out, err

    def credentials(self):
        result = self.helper("credential-process")
        data = json.loads(result.stdout)
        self.assertEqual(list(data), FIELDS)
        self.assertEqual(data["Version"], 1)
        return data

    def token_grants(self):
        return [form["grant_type"] for path, form in self.stub.calls if path == "/oauth2/token"]

    def test_pasted_redirect_login_then_the_credential_output(self):
        code, out, err = self.login()
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines(), [f"identity-id: {IDENTITY}", f"user-pool-sub: {SUB}"])
        self.assertEqual(self.token_grants(), ["authorization_code"])
        first = self.credentials()
        self.assertTrue(first["AccessKeyId"].startswith("ASIA"))
        sts = [form for path, form in self.stub.calls if path == "/sts/"]
        self.assertEqual([(f["RoleArn"], f["RoleSessionName"], f["DurationSeconds"]) for f in sts],
                         [(SIGNIN["roleArn"], "alice", "3600")])
        self.assertEqual([c[0] for c in self.stub.calls if c[0].startswith("Get")], ["GetId", "GetOpenIdToken"])
        # A session with more than 15 minutes left comes from the cache.
        self.assertEqual(self.credentials(), first)
        self.assertEqual(self.stub.issued, 1)

    def test_pasted_redirect_with_a_wrong_state_stops_before_the_exchange(self):
        code, out, err = self.login(wrong_state=True)
        self.assertEqual(code, 1)
        self.assertIn("state", err)
        self.assertEqual(self.token_grants(), [])
        self.assertFalse((self.cache / "token.json").exists())

    def test_no_prompt_and_a_refresh_with_the_refresh_token(self):
        # stdin stays open: a prompt would wait until the timeout.
        process = subprocess.Popen([sys.executable, str(HELPER), "credential-process", "--signin", str(self.signin)],
                                   env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            process.wait(timeout=10)
        finally:
            process.stdin.close()
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stdout.read(), "")
        lines = process.stderr.read().splitlines()
        process.stdout.close()
        process.stderr.close()
        self.assertEqual(len(lines), 1)
        self.assertIn(f"{HELPER} login --signin {self.signin}", lines[0])
        self.assertEqual(self.login()[0], 0)
        self.stub.sts_ttl = 800
        first = self.credentials()
        second = self.credentials()
        self.assertNotEqual(first["AccessKeyId"], second["AccessKeyId"])
        self.assertEqual(self.token_grants(), ["authorization_code", "refresh_token", "refresh_token"])

    def test_cache_modes_and_the_checks_before_a_read(self):
        self.assertEqual(self.login()[0], 0)
        self.credentials()
        for path, mode in ((self.home / ".cache/tenant-ec2-memory-signin", 0o700), (self.cache, 0o700),
                           (self.cache / "token.json", 0o600), (self.cache / "session.json", 0o600)):
            self.assertEqual(path.stat().st_mode & 0o777, mode, path)
        token = self.cache / "token.json"
        (self.cache / "session.json").unlink()

        def refused():
            result = self.helper("credential-process", ok=False)
            self.assertEqual((result.returncode, result.stdout), (1, ""))

        token.chmod(0o640)
        refused()
        token.chmod(0o600)
        moved = self.root / "token.json"
        token.rename(moved)
        token.symlink_to(moved)
        refused()
        token.unlink()
        moved.rename(token)
        os.link(token, self.root / "second-link")
        refused()
        (self.root / "second-link").unlink()
        self.cache.chmod(0o755)
        refused()
        self.cache.chmod(0o700)
        self.credentials()

    def test_logout_revokes_the_refresh_token_and_deletes_the_cache(self):
        self.assertEqual(self.login()[0], 0)
        self.credentials()
        self.helper("logout")
        self.assertIn(("/oauth2/revoke", {"token": REFRESH, "client_id": SIGNIN["clientId"]}), self.stub.calls)
        self.assertFalse(self.cache.exists())
        self.assertEqual(self.stub.valid, set())
        self.assertIn("login", self.helper("credential-process", ok=False).stderr)

    def test_parallel_calls_with_no_cached_session_all_pass(self):
        self.assertEqual(self.login()[0], 0)
        self.stub.sts_ttl = 800
        command = [sys.executable, str(HELPER), "credential-process", "--signin", str(self.signin)]
        processes = [subprocess.Popen(command, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(10)]
        results = [(process.communicate(timeout=30), process.returncode) for process in processes]
        self.assertEqual([code for _out, code in results], [0] * 10, [err for (_o, err), _c in results])
        self.assertEqual(sorted(os.listdir(self.cache)), ["session.json", "token.json"])

    def test_reads_while_a_write_replaces_the_file_all_pass(self):
        seconds, readers = 0.5, 4
        spec = importlib.util.spec_from_file_location("memory_signin", HELPER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        folder = self.root / "replace"
        folder.mkdir(mode=0o700)
        target = folder / "session.json"
        helper.write_private(target, {"n": 0})
        stop = time.monotonic() + seconds
        errors, unexpected = [], []

        def write():
            n = 0
            while time.monotonic() < stop:
                n += 1
                helper.write_private(target, {"n": n})

        def read():
            while time.monotonic() < stop:
                data = helper.read_private(target)
                if data is not None and not isinstance(data, dict):
                    unexpected.append(type(data).__name__)

        def guarded(function):
            try:
                function()
            except BaseException as error:
                errors.append(repr(error))

        threads = [threading.Thread(target=guarded, args=(write,))]
        threads += [threading.Thread(target=guarded, args=(read,)) for _ in range(readers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual((errors, unexpected), ([], []))

    def test_logout_keeps_the_token_when_the_revoke_fails(self):
        self.assertEqual(self.login()[0], 0)
        self.stub.revoke = (429, {"error": "too_many_requests"})
        self.assertIn("logout again", self.helper("logout", ok=False).stderr)
        self.assertTrue((self.cache / "token.json").exists())
        self.stub.revoke = (400, {"error": "invalid_token"})
        self.helper("logout")
        self.assertFalse(self.cache.exists())

    def test_setup_writes_the_profile_and_refuses_a_different_block(self):
        config = self.root / "config"
        config.write_text("[profile other]\nregion = us-east-1\n")
        self.helper("setup")
        text = config.read_text()
        self.assertIn(f"[profile {PROFILE}]\nregion = us-east-1\ncredential_process = {sys.executable} {HELPER} "
                      f"credential-process --signin {self.signin}\n", text)
        self.assertTrue(text.startswith("[profile other]\nregion = us-east-1\n\n"))
        self.helper("setup")
        self.assertEqual(config.read_text(), text)
        changed = text.replace("region = us-east-1\ncredential", "region = us-west-2\ncredential")
        config.write_text(changed)
        self.assertIn("different block", self.helper("setup", ok=False).stderr)
        self.assertEqual(config.read_text(), changed)

    def test_python_sdk_process_provider_refreshes(self):
        try:
            import botocore
        except ImportError:
            self.skipTest("Not verified: botocore is not installed")
        self.helper("setup")
        self.assertEqual(self.login()[0], 0)
        self.stub.sts_ttl = 800
        code = ("import botocore.session; s = botocore.session.Session(profile=%r); c = s.get_credentials(); "
                "print(c.method); print(c.get_frozen_credentials().access_key)" % PROFILE)
        env = {**self.env, "PYTHONPATH": str(Path(botocore.__file__).parent.parent),
               "AWS_SHARED_CREDENTIALS_FILE": str(self.root / "credentials")}
        result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        method, key = result.stdout.split()
        self.assertEqual(method, "custom-process")
        # 800 s is under the advisory window of the SDK, so it calls the process again for a new session.
        self.assertGreaterEqual(self.stub.issued, 2)
        self.assertEqual(key, f"ASIAFIXTURE{self.stub.issued:09d}")
        self.assertEqual(self.token_grants().count("refresh_token"), self.stub.issued)


if __name__ == "__main__":
    unittest.main()
