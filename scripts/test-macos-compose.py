#!/usr/bin/env python3
"""Validate the Mac Compose model without a daemon, host or private environment."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.macos.yaml"


class ComposeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix="macos-compose-test-"))
        cls.env = cls.work / "stub.env"
        cls.names = sorted(set(re.findall(r'\$\{([A-Z0-9_]+):\?', COMPOSE.read_text())))
        cls.values = {name: "offline-fixture" for name in cls.names}
        cls.values["EC2_AWS_REGION"] = "us-east-1"
        cls.values["EC2_TZ"] = "UTC"
        cls.env.write_text("".join(f"{name}={value}\n" for name, value in cls.values.items()))
        cls.model = cls.render(profile=True)

    @classmethod
    def render(cls, profile=False, env=None, code=0):
        args = ["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(env or cls.env), "-f", str(COMPOSE)]
        if profile:
            args += ["--profile", "agentcore-pending"]
        result = subprocess.run(args + ["config", "--format", "json"], capture_output=True, text=True,
                                env={"PATH": os.environ["PATH"], "HOME": str(cls.work)}, cwd=cls.work, timeout=30)
        if result.returncode != code:
            raise AssertionError("Compose parser failed: " + result.stderr)
        return json.loads(result.stdout) if code == 0 else None

    def test_service_gate_and_images(self):
        self.assertEqual(set(self.model["services"]), {"litellm", "litellm-db", "open-knowledge", "ok-auth", "pi-durable"})
        self.assertEqual(set(self.render()["services"]), {"litellm", "litellm-db", "open-knowledge", "ok-auth"})
        lock = yaml.safe_load((ROOT / "kits.lock").read_text())
        for name, service in self.model["services"].items():
            self.assertEqual(service["platform"], "linux/arm64")
            candidates = list(lock["server_kits"].values()) + list(lock["support_images"].values())
            self.assertEqual(service["image"], next(row["image"] for row in candidates if row["service"] == name))
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertIn("no-new-privileges:true", service["security_opt"])
            self.assertFalse(service.get("privileged"))
            self.assertIn("healthcheck", service)
            self.assertNotIn("/healthz", str(service["healthcheck"]))
            self.assertNotIn("env_file", service)
        agent = self.model["services"]["pi-durable"]
        self.assertEqual(agent["profiles"], ["agentcore-pending"])
        self.assertNotIn("OPENVIKING_URL", agent["environment"])
        self.assertNotIn("PI_DURABLE_MEMORY_BACKEND", agent["environment"])
        self.assertNotIn("AGENTCORE_MEMORY_CONFIG_FILE", agent["environment"])

    def test_ports_networks_volumes_and_credentials(self):
        for name, service in self.model["services"].items():
            ports = service.get("ports", [])
            if name == "ok-auth":
                self.assertEqual(len(ports), 1)
                self.assertEqual((ports[0]["host_ip"], str(ports[0]["published"]), ports[0]["target"]), ("127.0.0.1", "4180", 4180))
            else:
                self.assertEqual(ports, [])
            for mount in service.get("volumes", []):
                if mount["type"] == "bind":
                    self.assertTrue(mount["source"].startswith(("/run/tenant-ec2/", "/var/lib/tenant-ec2/aws-")))
                    self.assertTrue(mount["read_only"])
                    self.assertFalse(mount["bind"]["create_host_path"])
                self.assertNotIn("sock", mount["source"])
                self.assertNotIn("/Users", mount["source"])
            env = service["environment"]
            for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
                self.assertNotIn(key, env)
            if name in ("litellm", "pi-durable"):
                self.assertEqual(env["AWS_PROFILE"], "ec2-instance")
                self.assertEqual(env["AWS_EC2_METADATA_DISABLED"], "true")
                self.assertEqual(env["AWS_SHARED_CREDENTIALS_FILE"], "/run/tenant-ec2/aws/empty")
        members = {n: {s for s, v in self.model["services"].items() if n in v["networks"]} for n in self.model["networks"]}
        self.assertEqual(members["db"], {"litellm", "litellm-db"})
        self.assertEqual(members["knowledge"], {"open-knowledge", "ok-auth"})
        self.assertEqual(members["gate-egress"], {"ok-auth"})
        for name, network in self.model["networks"].items():
            self.assertEqual(network.get("internal", False), name not in ("egress", "gate-egress"))
        self.assertEqual(self.model["services"]["litellm"]["depends_on"]["litellm-db"]["condition"], "service_healthy")
        self.assertEqual(set(self.model["volumes"]), {"litellm-db", "open-knowledge", "pi-durable"})

    def test_required_values(self):
        self.assertFalse(any("OPENVIKING" in n or "DOMAIN" in n or "PI_DURABLE" in n for n in self.names))
        for name in self.names:
            env = self.work / "missing.env"
            env.write_text("".join(f"{k}={v}\n" for k, v in self.values.items() if k != name))
            self.render(env=env, code=1)

    def test_gate_and_luna(self):
        gate = tomllib.loads((ROOT / "ok-auth/oauth2-proxy.macos.cfg").read_text())
        self.assertFalse(gate["reverse_proxy"])
        self.assertFalse(gate["cookie_secure"])
        self.assertFalse(gate["cookie_name"].startswith("__Host"))
        self.assertTrue(gate["cookie_httponly"])
        self.assertEqual(gate["cookie_samesite"], "lax")
        self.assertFalse(gate["insecure_oidc_skip_nonce"])
        self.assertEqual(gate["code_challenge_method"], "S256")
        self.assertTrue(gate["proxy_websockets"])
        self.assertTrue(gate["skip_auth_strip_headers"])
        self.assertFalse(gate["pass_access_token"])
        self.assertFalse(gate["pass_authorization_header"])
        for name in ("skip_auth_routes", "skip_auth_regex", "skip_auth_preflight", "trusted_ips", "trusted_proxy_ips",
                     "insecure_oidc_skip_issuer_verification", "ssl_insecure_skip_verify", "skip_jwt_bearer_tokens"):
            self.assertNotIn(name, gate)
        auth = self.model["services"]["ok-auth"]["environment"]
        self.assertTrue(auth["OAUTH2_PROXY_OIDC_ISSUER_URL"].startswith("https://login.microsoftonline.com/"))
        self.assertIn("OAUTH2_PROXY_ALLOWED_GROUPS", auth)
        self.assertEqual(auth["OAUTH2_PROXY_REDIRECT_URL"], "http://localhost:4180/oauth2/callback")
        self.assertEqual(self.model["services"]["open-knowledge"]["environment"]["OK_EXTERNAL_URL"], "http://localhost:4180")
        routes = yaml.safe_load((ROOT / "litellm/config.yaml").read_text())
        luna = next(m for m in routes["model_list"] if m["model_name"] == "bedrock/luna")
        self.assertEqual(luna["litellm_params"]["reasoning_effort"], "xhigh")
        self.assertNotIn("drop_params", json.dumps(routes))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ComposeTest))
    if result.wasSuccessful():
        print("macos compose: pass (parser, dormant agent, mounts, credential settings and gate)")
        print("Not verified: Podman runtime, ARM64 images, named-volume ownership, Entra login or WebSockets")
    raise SystemExit(not result.wasSuccessful())
