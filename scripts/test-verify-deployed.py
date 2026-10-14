#!/usr/bin/env python3
"""Offline verifier regressions: python3 -I scripts/test-verify-deployed.py."""

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import yaml

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("verify_deployed", ROOT / "scripts/verify-deployed.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def contract_file_sets():
    """The file sets of the contract: EC2_OPTIONS and the Compose files of its file set, in
    the form of COMPOSE_FILE. The one table of the checks is FILE_SETS of scripts/check.sh.
    The check script compares it with the bootstrap and with the deploy script. This file
    holds no copy of the table, and the verifier holds no list of file sets."""
    text = (ROOT / "scripts/check.sh").read_text()
    block = re.search(r"^FILE_SETS='(.*?)'$", text, re.S | re.M).group(1)
    return {options: files.replace(" ", ":")
            for options, _, files in (line.partition("|") for line in block.splitlines())}


FILE_SETS = contract_file_sets()
COMPOSE_FILES = ("compose.yaml", "compose.caddy.yaml", "compose.pangolin.yaml", "compose.copilot.yaml")
PANGOLIN_SERVICES = {"pangolin", "gerbil", "traefik"}


def services_of(file_set):
    """The services of the Compose files of a file set, read from the files of the tree."""
    found = []
    for name in file_set.split(":"):
        for service in yaml.safe_load((ROOT / name).read_text())["services"]:
            if service not in found:
                found.append(service)
    return found


ALL_SERVICES = sorted(set(services_of(":".join(COMPOSE_FILES))))


class StackFixture(unittest.TestCase):
    # The original design is the file set with the two options.
    file_set = FILE_SETS["pangolin,copilot"]
    edge_names = "ok"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        for name in COMPOSE_FILES + ("kits.lock", "pangolin/blueprint.yaml", ".env.example",
                                     "litellm/config.yaml", "litellm/config.copilot.yaml"):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, self.repo / name)
        self.unpin_agent()
        self.host_env(self.file_set, self.edge_names)
        self.calls = []
        self.down = None
        self.state = "true|running|false|false|healthy"
        self.fail_exec = None
        self.pangolin_output = "200"

    def host_env(self, file_set, edge_names):
        # No kit clone or kit-local .env exists. The bootstrap writes each value of the
        # environment file of the host between single quotes.
        lines = ["EC2_LITELLM_MASTER_KEY='fixture-private-value'"]
        if file_set is not None:
            lines.append("COMPOSE_FILE='" + file_set + "'")
        if edge_names is not None:
            lines.append("EC2_EDGE_NAMES='" + edge_names + "'")
        (self.repo / ".env").write_text("\n".join(lines) + "\n")

    def docker(self, args, **kwargs):
        self.calls.append((args, kwargs.get("input")))
        self.assertEqual(kwargs["timeout"], 45)
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(set(kwargs["env"]), {"PATH", "HOME"})
        if args[:2] == ["docker", "compose"]:
            self.assertIn("--env-file", args)
            self.assertEqual(args[-4:-1], ["ps", "--all", "--quiet"])
            service = args[-1]
            index = ALL_SERVICES.index(service) + 1
            value = "" if service == self.down else f"{index:064x}\n"
        elif args[:2] == ["docker", "inspect"]:
            service = ALL_SERVICES[int(args[-1], 16) - 1]
            value = self.state if service == self.down else "true|running|false|false|healthy"
        elif args[:2] == ["docker", "exec"]:
            service = self.exec_service(args)
            if service == self.fail_exec:
                return subprocess.CompletedProcess(args, 1, "fixture-private-value", "fixture-private-value")
            value = self.pangolin_output if service == "pangolin" else "fixture-private-value"
            # Other successful commands must not relay their output either.
        else:
            self.fail("unexpected Docker call")
        return subprocess.CompletedProcess(args, 0, value, "")

    @staticmethod
    def exec_service(args):
        return ALL_SERVICES[int(args[3 if args[2] == "-i" else 2], 16) - 1]

    def execute(self, *options):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                patch.object(verify.subprocess, "run", self.docker):
            rc = verify.main(["--repo", str(self.repo)] + list(options))
        self.assertNotIn("fixture-private-value", out.getvalue() + err.getvalue())
        self.assertNotIn("fixture-private-value", json.dumps(self.calls))
        return rc, out.getvalue(), err.getvalue()

    def mutate(self, name, fn):
        path = self.repo / name
        data = yaml.safe_load(path.read_text())
        fn(data)
        path.write_text(yaml.safe_dump(data))

    def required(self):
        """The service of each `docker compose ps` call, in the order of the calls."""
        return [args[-1] for args, _ in self.calls if args[:2] == ["docker", "compose"]]

    def execs(self):
        return [(args, code) for args, code in self.calls if args[:2] == ["docker", "exec"]]

    def touched(self):
        """Each service that a Docker call of the run names."""
        return set(self.required()) | {self.exec_service(args) for args, _ in self.execs()}

    def unpin_agent(self):
        # Keep legacy skip cases independent of the release pin in the source tree.
        self.mutate("kits.lock", lambda d: d["server_kits"]["pi-durable"].update(
            source_tag="", source_commit=""))

    def pin_agent(self):
        self.mutate("kits.lock", lambda d: d["server_kits"]["pi-durable"].update(
            source_tag="fixture-tag", source_commit="a" * 40))


class StackTests(StackFixture):
    def test_healthy_current_lock_requires_the_pinned_agent(self):
        shutil.copyfile(ROOT / "kits.lock", self.repo / "kits.lock")
        rc, out, err = self.execute()
        self.assertEqual(rc, 0, err)
        self.assertEqual(set(self.required()), set(services_of(self.file_set)))
        self.assertNotIn("absent source pin", out)
        self.calls.clear()
        self.down = "pi-durable"
        rc, _, err = self.execute()
        self.assertEqual(rc, 1)
        self.assertIn("pi-durable: expected one deployed container", err)
        self.assertFalse(self.execs())

    def test_healthy_unpinned_stack_and_explicit_skips(self):
        rc, out, err = self.execute()
        self.assertEqual(rc, 0, err)
        self.assertIn("verify-deployed: pass (health/authentication only; explicit skips remain)", out)
        for text in ("absent source pin", "Entra login", "public llm", "public ov", "public agent",
                     "paid model answers", "xhigh", "standalone image smoke", "provider access"):
            self.assertIn(text, out)
        # Each service of the Compose files of the file set, but the agent with no pin.
        expected = [s for s in services_of(self.file_set) if s != "pi-durable"]
        self.assertEqual(sorted(self.required()), sorted(expected))
        self.assertEqual(self.required()[:3], ["pangolin", "gerbil", "traefik"])
        execs = self.execs()
        # Eight checks of a service, and the path from the edge for the public name ok.
        self.assertEqual(len(execs), 9)
        self.assertEqual(execs[-1][0][-1], "http://ok-auth:4180/ready")
        self.assertEqual(self.exec_service(execs[-1][0]), "gerbil")
        code = "\n".join(body or "" for _, body in execs)
        self.assertIn("http://copilot:4000", code)
        self.assertIn("http://litellm:4000", code)
        self.assertIn("/data/.ok", code)
        self.assertNotIn("/healthz", code)  # Gerbil's non-billable health is an argument, not agent code.
        self.assertNotIn("/v1/chat/completions", code)
        self.assertNotIn("/v1/embeddings", code)

    def test_every_missing_required_service_fails_before_probes(self):
        for service in (s for s in services_of(self.file_set) if s != "pi-durable"):
            with self.subTest(service=service):
                self.calls.clear()
                self.down = service
                rc, out, err = self.execute()
                self.assertEqual(rc, 1)
                self.assertIn(service + ": expected one deployed container", err)
                self.assertNotIn("verify-deployed: pass", out)
                self.assertFalse(self.execs())

    def test_stopped_unhealthy_starting_paused_and_restart_states_fail(self):
        original = self.docker
        for state in ("false|exited|false|false|unhealthy", "true|running|false|false|unhealthy",
                      "true|running|false|false|starting", "true|running|true|false|healthy",
                      "true|restarting|false|true|healthy", "true|running|false|false|missing"):
            with self.subTest(state=state):
                self.calls.clear()
                self.state = state
                self.down = "litellm"
                def stub(args, **kwargs):
                    if args[:2] == ["docker", "compose"] and args[-1] == self.down:
                        self.calls.append((args, kwargs.get("input")))
                        return subprocess.CompletedProcess(
                            args, 0, f"{ALL_SERVICES.index(self.down) + 1:064x}", "")
                    return original(args, **kwargs)
                with patch.object(self, "docker", stub):
                    rc, _, err = self.execute()
                self.assertEqual(rc, 1)
                self.assertIn("not running and healthy", err)
                self.assertFalse(self.execs())

    def test_each_internal_probe_failure_is_fatal_and_redacted(self):
        # The check of the gateway holds the path to the service copilot.
        for service in (s for s in services_of(self.file_set) if s not in ("copilot", "pi-durable")):
            with self.subTest(service=service):
                self.calls.clear()
                self.fail_exec = service
                rc, out, err = self.execute()
                self.assertEqual(rc, 1)
                self.assertIn(service + ":", err)
                self.assertNotIn("verify-deployed: pass", out)

    def test_pangolin_requires_exact_http_200(self):
        for status in ("200", "204", "301", "302", "307", "308", "401", "500", "000",
                       "200\nfixture-private-value"):
            with self.subTest(status=status):
                self.calls.clear()
                self.pangolin_output = status
                rc, out, err = self.execute()
                self.assertEqual(rc, 0 if status == "200" else 1)
                if status != "200":
                    self.assertIn("pangolin: internal API health failed", err)
                    self.assertNotIn("verify-deployed: pass", out)
                    self.assertEqual(len(self.execs()), 1)

    def test_command_failure_timeout_and_missing_cli_are_redacted(self):
        for effect in (OSError("fixture-private-value"),
                       subprocess.TimeoutExpired("fixture-private-value", 45, output="fixture-private-value")):
            with patch.object(verify.subprocess, "run", side_effect=effect):
                with self.assertRaises(verify.Failure) as error:
                    verify.Verifier(self.repo).run(["docker"], "bounded check")
                self.assertNotIn("fixture-private-value", str(error.exception))

    def test_missing_home_uses_account_home_without_environment_inheritance(self):
        home = str(self.repo / "account-home")
        environment = {"DOCKER_HOST": "fixture-private-value",
                       "HTTP_PROXY": "fixture-private-value",
                       "LITELLM_MASTER_KEY": "fixture-private-value"}
        with patch.dict(os.environ, environment, clear=True), \
                patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=home)) as account, \
                patch.object(verify.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            verify.Verifier(self.repo).run(["docker"], "bounded check")
        account.assert_called_once_with(os.getuid())
        self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin", "HOME": home})

    def test_explicit_home_and_path_keep_only_the_allowed_environment(self):
        for home in (str(self.repo / "caller-home"), ""):
            with self.subTest(home=home), \
                    patch.dict(os.environ, {"PATH": "/fixture/bin", "HOME": home,
                                            "DOCKER_CONFIG": "fixture-private-value",
                                            "LITELLM_MASTER_KEY": "fixture-private-value"}, clear=True), \
                    patch("pwd.getpwuid", side_effect=AssertionError("unexpected account lookup")), \
                    patch.object(verify.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                verify.Verifier(self.repo).run(["docker"], "bounded check")
            self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/fixture/bin", "HOME": home})

    def test_unavailable_home_fails_without_private_error_or_docker_call(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(verify.Path, "home", side_effect=RuntimeError("fixture-private-value")):
            rc, out, err = self.execute()
        self.assertEqual(rc, 1)
        self.assertEqual(err.strip(), "verify-deployed: FAIL - unsupported stack data")
        self.assertNotIn("verify-deployed: pass", out)
        self.assertFalse(self.calls)

    def test_invalid_container_ids_are_not_used_as_arguments(self):
        for value in ("bad-id", "a" * 64 + "\n" + "b" * 64, "fixture-private-value"):
            with patch.object(verify.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, value, "")):
                with self.assertRaises(verify.Failure):
                    verify.Verifier(self.repo).require("litellm")

    def test_changed_gate_or_enabled_public_resource_is_unsupported(self):
        # A resource of the blueprint is on only through its reference: the render
        # script sets it from EC2_EDGE_NAMES. A resource that is always on is a change.
        for name in ("ok", "ov", "agent", "llm"):
            with self.subTest(resource=name):
                self.mutate("pangolin/blueprint.yaml", lambda d: d["public-resources"][name].update(enabled=True))
                rc, _, err = self.execute()
                self.assertEqual(rc, 1)
                self.assertIn("public resource contract changed", err)
                self.assertFalse(self.calls)
                self.mutate("pangolin/blueprint.yaml", lambda d: d["public-resources"][name].update(
                    enabled="${EDGE_" + name.upper() + "}"))
        self.assertEqual(self.execute()[0], 0)
        self.calls.clear()
        # The gate of the kit is oauth2-proxy. The sleeping placeholder of the first design
        # is a change of the contract.
        self.mutate("compose.yaml", lambda d: d["services"]["ok-auth"].update(command=["sleep", "infinity"]))
        rc, _, err = self.execute()
        self.assertEqual(rc, 1)
        self.assertIn("unsupported ok-auth", err)
        self.assertFalse(self.calls)

    def test_bad_yaml_missing_and_unknown_pins_fail(self):
        self.mutate("kits.lock", lambda d: d["server_kits"]["litellm"].update(source_commit=""))
        rc, _, err = self.execute()
        self.assertEqual(rc, 1)
        self.assertIn("required kit has no source pin", err)
        self.assertFalse(self.calls)
        shutil.copyfile(ROOT / "kits.lock", self.repo / "kits.lock")
        self.mutate("kits.lock", lambda d: d["server_kits"].update(unknown={}))
        self.assertEqual(self.execute()[0], 1)
        (self.repo / "kits.lock").write_text("key: [fixture-private-value")
        rc, out, err = self.execute()
        self.assertEqual(rc, 1)
        self.assertNotIn("fixture-private-value", out + err)

    def test_pinned_agent_gets_non_billable_internal_check(self):
        self.pin_agent()
        rc, out, err = self.execute()
        self.assertEqual(rc, 0, err)
        self.assertNotIn("absent source pin", out)
        agent = [code for args, code in self.execs() if self.exec_service(args) == "pi-durable"]
        self.assertEqual(agent, [verify.AGENT_JS])
        self.assertIn("http://127.0.0.1:5470/v1/", agent[0])
        self.assertNotIn("/healthz", agent[0])
        self.down = "pi-durable"
        self.assertEqual(self.execute()[0], 1)


AWS_HOST = ROOT / "scripts/aws-host.sh"


def deploy_script_inputs(work, options, edge_names="ok"):
    """The inputs that the dry run of `verify` of the deploy script gives to the verifier
    for one value of EC2_OPTIONS: the file set and the public names."""
    env = Path(work) / "deploy.env"
    provider = "porkbun" if "pangolin" in options else "manual"
    env.write_text("EC2_PROJECT=tenant-ec2\nEC2_OPTIONS=%s\nEC2_EDGE_NAMES=%s\nEC2_DNS_PROVIDER=%s\n"
                   % (options, edge_names, provider))
    result = subprocess.run(["sh", str(AWS_HOST), "--dry-run", "--env-file", str(env), "verify"],
                            env={"PATH": os.environ["PATH"], "HOME": str(work)},
                            text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise AssertionError("the dry run of verify fails: " + result.stderr)
    lines = [line for line in result.stdout.splitlines() if "scripts/verify-deployed.py" in line]
    if len(lines) != 1:
        raise AssertionError("the dry run of verify does not print one verifier command")
    words = lines[0].split()
    return words[words.index("--file-set") + 1], words[words.index("--edge-names") + 1], result.stdout


class FileSetTests(StackFixture):
    """The verifier follows the file set of EC2_OPTIONS: one test for each of its values."""

    def test_contract_has_the_four_values_of_the_options(self):
        self.assertEqual(sorted(FILE_SETS), ["", "copilot", "pangolin", "pangolin,copilot"])
        for file_set in FILE_SETS.values():
            self.assertEqual(file_set.split(":")[0], "compose.yaml")
            self.assertTrue(set(file_set.split(":")) <= set(COMPOSE_FILES))
        self.assertEqual(len(set(FILE_SETS.values())), 4)

    def follow(self, options, pinned=False):
        file_set, edge_names, _ = deploy_script_inputs(self.repo, options)
        # The deploy script gives the file set of the contract. The verifier gets it as
        # its input, so it needs no list of its own.
        self.assertEqual(file_set, FILE_SETS[options])
        self.assertEqual(edge_names, "ok")
        self.host_env(file_set, edge_names)
        if pinned:
            self.pin_agent()
        self.calls.clear()
        rc, out, err = self.execute("--file-set", file_set, "--edge-names", edge_names)
        self.assertEqual(rc, 0, err)
        self.assertIn("ok - file set " + file_set + ": the host has it", out)
        files = file_set.split(":")
        services = set(services_of(file_set))
        if not pinned:
            services.discard("pi-durable")
        # Each service of the Compose files of the file set, and no other service.
        self.assertEqual(set(self.required()), services)
        self.assertEqual(self.touched(), services)
        for args, _ in self.calls:
            if args[:2] == ["docker", "compose"]:
                named = [args[i + 1] for i, word in enumerate(args) if word == "-f"]
                self.assertEqual(named, [str(self.repo / name) for name in files])
        text = json.dumps(self.calls)
        if "pangolin" not in options:
            self.assertFalse(self.touched() & PANGOLIN_SERVICES)
            for word in ("pangolin", "gerbil", "traefik"):
                self.assertNotIn(word, text.replace(str(self.repo), ""))
        else:
            self.assertTrue(PANGOLIN_SERVICES <= self.touched())
            self.assertNotIn("caddy", self.touched())
        if "copilot" not in options:
            self.assertNotIn("copilot", text.replace(str(self.repo), ""))
            self.assertNotIn("Copilot", out)
        else:
            self.assertIn("copilot", self.touched())
            self.assertIn("http://copilot:4000", text)
        # The public name ok: the member of the edge of this file set reaches the gate.
        edge = "gerbil" if "pangolin" in options else "caddy"
        last, _ = self.execs()[-1]
        self.assertEqual((self.exec_service(last), last[-1]), (edge, "http://ok-auth:4180/ready"))
        return out

    def test_core_checks_no_pangolin_service_and_no_copilot_service(self):
        self.follow("")
        self.assertEqual(set(self.required()), {"caddy", "litellm-db", "litellm", "openviking",
                                                "open-knowledge", "ok-auth"})
        self.follow("", pinned=True)
        self.assertEqual(set(self.required()), {"caddy", "litellm-db", "litellm", "openviking",
                                                "open-knowledge", "ok-auth", "pi-durable"})
        gateway = [code for args, code in self.execs() if self.exec_service(args) == "litellm"]
        self.assertIn("routes = ('bedrock/luna', 'bedrock/embed')\n", gateway[0])

    def test_core_reads_no_file_of_an_option(self):
        # A host of the core: the verifier needs no file of an option that is off.
        for name in ("compose.pangolin.yaml", "compose.copilot.yaml", "pangolin/blueprint.yaml",
                     "litellm/config.copilot.yaml"):
            (self.repo / name).unlink()
        self.host_env(FILE_SETS[""], "ok")
        rc, _, err = self.execute("--file-set", FILE_SETS[""], "--edge-names", "ok")
        self.assertEqual(rc, 0, err)

    def test_option_copilot_adds_the_copilot_service_and_its_routes(self):
        self.follow("copilot")
        gateway = [code for args, code in self.execs() if self.exec_service(args) == "litellm"]
        self.assertIn("routes = ('bedrock/luna', 'bedrock/embed', 'copilot/luna', 'copilot/embed')\n", gateway[0])

    def test_option_pangolin_replaces_the_core_edge(self):
        out = self.follow("pangolin")
        self.assertNotIn("caddy", out)

    def test_options_pangolin_and_copilot_check_the_ten_services(self):
        self.follow("pangolin,copilot", pinned=True)
        self.assertEqual(len(set(self.required())), 10)

    def test_file_set_of_the_caller_must_be_the_file_set_of_the_host(self):
        # The host has the core. After a change of EC2_OPTIONS, the host has the new file
        # set only after `secret` and a new run of the bootstrap.
        self.host_env(FILE_SETS[""], "ok")
        for options in ("copilot", "pangolin", "pangolin,copilot"):
            with self.subTest(options=options):
                rc, out, err = self.execute("--file-set", FILE_SETS[options])
                self.assertEqual(rc, 1)
                self.assertIn("COMPOSE_FILE of the host is another file set", err)
                self.assertFalse(self.calls)
        for value in ("compose.caddy.yaml:compose.yaml", "compose.yaml:../compose.caddy.yaml",
                      "compose.yaml:compose.yaml", "compose.yaml:fixture-private-value", ""):
            with self.subTest(value=value):
                rc, _, err = self.execute("--file-set", value)
                self.assertEqual(rc, 1)
                self.assertNotIn("..", err)
                self.assertFalse(self.calls)
        self.host_env(FILE_SETS[""] + ":compose.newt.yaml", "ok")
        rc, _, err = self.execute()
        self.assertEqual(rc, 1)
        self.assertIn("a Compose file is not in the host snapshot", err)

    def test_without_input_the_verifier_takes_the_file_set_of_the_host(self):
        for options, file_set in FILE_SETS.items():
            with self.subTest(options=options):
                self.calls.clear()
                self.host_env(file_set, "ok")
                rc, out, err = self.execute()
                self.assertEqual(rc, 0, err)
                self.assertEqual(set(self.required()),
                                 set(services_of(file_set)) - {"pi-durable"})
        self.calls.clear()
        self.host_env(None, "ok")
        rc, _, err = self.execute()
        self.assertEqual(rc, 1)
        self.assertIn("has no COMPOSE_FILE", err)
        self.assertFalse(self.calls)

    def test_public_names_of_the_host_get_a_path_check_from_the_edge(self):
        for options, edge in (("", "caddy"), ("pangolin", "gerbil")):
            with self.subTest(options=options):
                self.unpin_agent()
                self.calls.clear()
                self.host_env(FILE_SETS[options], "ok,agent,llm,ov")
                rc, out, err = self.execute("--file-set", FILE_SETS[options], "--edge-names", "ok,llm,ov,agent")
                self.assertEqual(rc, 0, err)
                paths = [(self.exec_service(args), args[-1]) for args, _ in self.execs()
                         if args[-1] in verify.EDGE_PATHS.values()]
                self.assertEqual(paths, [(edge, "http://ok-auth:4180/ready"),
                                         (edge, "http://litellm:4000/health/liveliness"),
                                         (edge, "http://openviking:1933/health")])
                self.assertIn("skip - public agent: the service pi-durable has no source pin", out)
                self.assertNotIn("not in EC2_EDGE_NAMES", out)
                self.pin_agent()
                rc, out, err = self.execute("--file-set", FILE_SETS[options], "--edge-names", "ok,llm,ov,agent")
                self.assertEqual(rc, 0, err)
                self.assertIn("skip - public agent: no path check from the edge without a bearer", out)
        self.assertNotIn("/healthz", json.dumps([args for args, _ in self.calls
                                                 if "gerbil-core" not in args[-1]]))

    def test_public_names_of_the_caller_must_be_the_names_of_the_host(self):
        self.host_env(FILE_SETS[""], "ok")
        rc, _, err = self.execute("--edge-names", "ok,llm")
        self.assertEqual(rc, 1)
        self.assertIn("EC2_EDGE_NAMES of the host is another list", err)
        self.assertFalse(self.calls)
        for names in ("ok,pangolin", "ok,fixture-private-value", "OK"):
            with self.subTest(names=names):
                self.host_env(FILE_SETS[""], names)
                self.assertEqual(self.execute()[0], 1)
                self.assertFalse(self.calls)
        self.host_env(FILE_SETS[""], None)
        rc, _, err = self.execute()
        self.assertEqual(rc, 1)
        self.assertIn("has no EC2_EDGE_NAMES", err)

    def test_service_with_no_check_stops_the_verifier(self):
        self.host_env(FILE_SETS[""], "ok")
        self.mutate("compose.caddy.yaml", lambda d: d["services"].update(newt={"image": "fixture"}))
        rc, _, err = self.execute()
        self.assertEqual(rc, 1)
        self.assertIn("newt: the verifier has no check for this service of the file set", err)
        self.assertFalse(self.calls)

    def test_each_service_of_the_compose_files_has_a_check(self):
        self.assertEqual(set(verify.Verifier(self.repo).checks()), set(ALL_SERVICES))

    def test_edge_paths_agree_with_the_files_of_each_edge(self):
        # The path from the edge to a service: the address is the target of the public
        # name in the site file of the core edge and in the blueprint of the Pangolin edge.
        blueprint = yaml.safe_load((ROOT / "pangolin/blueprint.yaml").read_text())["public-resources"]
        for options in FILE_SETS:
            file_set = FILE_SETS[options]
            self.host_env(file_set, "ok")
            checker = verify.Verifier(self.repo)
            _, known = checker.public_names(checker.model(file_set.split(":")))
            self.assertEqual(set(known), {"ok", "llm", "ov", "agent"})
            for name, (network, target, edge) in known.items():
                with self.subTest(options=options, name=name):
                    self.assertEqual(network, "edge-" + name)
                    self.assertEqual(edge, "gerbil" if "pangolin" in options else "caddy")
                    site = (ROOT / "caddy/sites" / (name + ".caddy")).read_text()
                    address = [line.split()[1] for line in site.splitlines()
                               if line.strip().startswith("reverse_proxy ")]
                    resource = blueprint[name]["targets"][0]
                    self.assertEqual(address, ["%s:%s" % (resource["hostname"], resource["port"])])
                    self.assertEqual(address[0].split(":")[0], target)
                    if target in verify.EDGE_PATHS:
                        self.assertTrue(verify.EDGE_PATHS[target].startswith("http://" + address[0] + "/"))
                    else:
                        self.assertEqual(target, "pi-durable")
        # The path check uses wget in the member of the edge: the health check of that
        # service in its Compose file uses the same tool, so its image holds it.
        for name, service in (("compose.caddy.yaml", "caddy"), ("compose.pangolin.yaml", "gerbil")):
            test = yaml.safe_load((ROOT / name).read_text())["services"][service]["healthcheck"]["test"]
            self.assertEqual(test[:2], ["CMD", "wget"])


@unittest.skipUnless(shutil.which("curl"), "curl is needed to execute the real Pangolin HTTP probe")
class PangolinCurlTests(unittest.TestCase):
    def test_real_curl_status_redirect_body_limit_and_suppression(self):
        fixture = StackTests("test_healthy_unpinned_stack_and_explicit_skips")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.assertEqual(fixture.execute()[0], 0)
        command = next(args[3:] for args, _ in fixture.calls
                       if args[:2] == ["docker", "exec"] and args[3:4] == ["curl"])
        self.assertEqual(command[:2], ["curl", "--disable"])
        self.assertNotIn("--location", command)
        self.assertNotIn("-L", command)
        self.assertEqual(command[command.index("--max-time") + 1], "5")
        self.assertEqual(command[command.index("--max-filesize") + 1], "65536")
        self.assertEqual(command[command.index("--max-redirs") + 1], "0")
        self.assertEqual(command[command.index("--output") + 1], "/dev/null")
        self.assertEqual(command[command.index("--write-out") + 1], "%{http_code}")
        requests = []
        response_status, payload = 200, b"fixture-sensitive-body"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(response_status)
                self.send_header("Content-Length", str(len(payload)))
                if 300 <= response_status < 400:
                    self.send_header("Location", "/fixture-redirect-target")
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # A curlrc that tries to follow redirects must not affect the probe.
            (fixture.repo / ".curlrc").write_text("location\n")
            command[-1] = f"http://127.0.0.1:{server.server_port}/api/v1/"
            for status in (200, 204, 301, 302, 307, 308, 401, 500):
                with self.subTest(status=status):
                    response_status = status
                    before = len(requests)
                    result = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                            env={"PATH": os.environ["PATH"], "HOME": str(fixture.repo)})
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, str(status))
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(requests[before:], ["/api/v1/"])
            response_status, payload = 200, b"x" * 65537
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertNotIn("x", result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class Response:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.body[:limit]


CORE_ROUTES = ("bedrock/luna", "bedrock/embed")
COPILOT_ROUTES = CORE_ROUTES + ("copilot/luna", "copilot/embed")


class HttpTests(unittest.TestCase):
    def exercise(self, mode, overrides=None, env=None, routes=COPILOT_ROUTES, copilot=True, listed=None):
        calls = []
        overrides = overrides or {}
        env = env if env is not None else {
            "LITELLM_MASTER_KEY": "fixture-gateway-value",
            "COPILOT_MASTER_KEY": "fixture-copilot-value",
            "OPENVIKING_LITELLM_API_KEY": "fixture-memory-value",
        }
        listing = json.dumps({"data": [{"id": name} for name in (routes if listed is None else listed)]}).encode()
        def open_request(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(timeout, 5)
            bearer = request.get_header("Authorization")
            path = request.full_url
            calls.append((path, bearer))
            self.assertNotIn("fixture-", path)
            default = (200, b"{}")
            if path.endswith("/v1/models"):
                if bearer in (None, "Bearer invalid-deployment-probe"):
                    default = (401, b"fixture-sensitive-error")
                elif path.startswith("http://copilot:"):
                    default = (200, b'{"data": []}')
                else:
                    default = (200, listing)
            elif path.endswith("/health"):
                default = (200, b'{"healthy": true}')
            elif path.endswith("/api/v1/system/status"):
                default = (403, b"fixture-sensitive-error")
            answer = overrides.get((path, bearer), default)
            if isinstance(answer, Exception):
                raise answer
            status, body = answer
            if status != 200:
                raise urllib.error.HTTPError(path, status, "fixture-sensitive-error", {}, io.BytesIO(body))
            return Response(status, body)
        opener = type("Opener", (), {"open": staticmethod(open_request)})()
        code = verify.gateway_code(routes, copilot) if mode == "gateway" else verify.memory_code()
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), \
                patch.object(urllib.request, "build_opener", return_value=opener) as builder, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                exec(code, {})
            except SystemExit as error:
                rc = error.code
            else:
                rc = 0
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        handlers = builder.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsNone(handlers[1].redirect_request(None, None, 302, "", {}, "http://invalid/"))
        return rc, calls

    def test_gateway_and_memory_healthy_auth_and_network_paths(self):
        for mode in ("gateway", "memory"):
            rc, calls = self.exercise(mode)
            self.assertEqual(rc, 0)
            if mode == "gateway":
                self.assertIn(("http://copilot:4000/v1/models", "Bearer fixture-copilot-value"), calls)
            else:
                self.assertIn(("http://litellm:4000/health/liveliness", None), calls)
                self.assertTrue(all(bearer is None for _, bearer in calls))
            self.assertTrue(all(path.startswith("http://") for path, _ in calls))
            self.assertFalse(any("completions" in path or "embeddings" in path for path, _ in calls))

    def test_core_gateway_makes_no_copilot_request_and_needs_the_core_routes(self):
        # The core: the two Bedrock routes, no request to the service copilot and no
        # read of its key.
        self.assertNotIn("copilot", verify.gateway_code(CORE_ROUTES, False).lower())
        rc, calls = self.exercise("gateway", routes=CORE_ROUTES, copilot=False,
                                  env={"LITELLM_MASTER_KEY": "fixture-gateway-value"})
        self.assertEqual(rc, 0)
        self.assertFalse(any("copilot" in path for path, _ in calls))
        for listed in (("bedrock/luna",), ("bedrock/embed",), ("copilot/luna", "copilot/embed"), ()):
            self.assertEqual(self.exercise("gateway", routes=CORE_ROUTES, copilot=False, listed=listed)[0], 1)
        # With the option copilot, the gateway must list the four routes.
        self.assertEqual(self.exercise("gateway", listed=CORE_ROUTES)[0], 1)
        self.assertEqual(self.exercise("gateway", listed=COPILOT_ROUTES)[0], 0)

    def test_gateway_code_refuses_a_route_name_that_is_not_plain(self):
        for name in ("bedrock/luna'", "x\nimport os", "", "a b", None):
            with self.assertRaises(verify.Failure):
                verify.gateway_code(("bedrock/luna", name), False)

    def test_gateway_bad_auth_listing_and_network_answers_fail(self):
        for answer in ((200, b"{}"), (302, b"fixture-sensitive-error"), (400, b"no_db_connection")):
            self.assertEqual(self.exercise("gateway", {("http://127.0.0.1:4000/v1/models", None): answer})[0], 1)
        for body in (b'{"data": []}', b'{}', b'not JSON fixture-sensitive-error', b'"fixture-sensitive-error"', b'a' * 65537):
            self.assertEqual(self.exercise("gateway", {
                ("http://127.0.0.1:4000/v1/models", "Bearer fixture-gateway-value"): (200, body)})[0], 1)
        for host in ("127.0.0.1", "copilot"):
            for answer in (OSError("fixture-sensitive-error"), (503, b"fixture-sensitive-error"), (302, b"")):
                self.assertEqual(self.exercise("gateway", {(f"http://{host}:4000/health/liveliness", None): answer})[0], 1)
        self.assertEqual(self.exercise("gateway", env={})[0], 1)
        self.assertEqual(self.exercise("gateway", env={"LITELLM_MASTER_KEY": "fixture\nvalue"})[0], 1)
        # With the option copilot, the key of that service is necessary too.
        self.assertEqual(self.exercise("gateway", env={"LITELLM_MASTER_KEY": "fixture-gateway-value"})[0], 1)

    def test_memory_health_auth_and_gateway_network_failures(self):
        for answer in ((200, b'{"healthy": false}'), (200, b"{}"), (200, b"invalid JSON"), (503, b"")):
            self.assertEqual(self.exercise("memory", {("http://127.0.0.1:1933/health", None): answer})[0], 1)
        self.assertEqual(self.exercise("memory", {
            ("http://127.0.0.1:1933/api/v1/system/status", None): (200, b"{}")})[0], 1)
        self.assertEqual(self.exercise("memory", {
            ("http://litellm:4000/health/liveliness", None): (503, b"fixture-sensitive-error")})[0], 1)
        self.assertEqual(self.exercise("memory", env={})[0], 0)


@unittest.skipUnless(shutil.which("node"), "Node is needed to execute the real container JS probes")
class NodeTests(unittest.TestCase):
    def node(self, code, status=200, exists=True, thinking="xhigh", throws=False, port="80"):
        wrapper = '''
const fixtureFs = require('node:fs');
fixtureFs.accessSync = () => { if (!@EXISTS@) throw new Error('fixture-sensitive-error'); };
process.env.RESEARCHER_THINKING = @THINKING@;
if (@PORT@ === null) delete process.env.PORT; else process.env.PORT = @PORT@;
global.fetch = async (url, opts) => {
  if (opts.redirect !== 'manual' || !opts.signal) throw new Error('bad deadline or redirect policy');
  if (!['http://127.0.0.1:80/readyz', 'http://127.0.0.1:5470/v1/'].includes(url)) throw new Error('bad URL');
  if (@THROWS@) throw new Error('fixture-sensitive-error');
  return {status: @STATUS@};
};
'''
        for name, value in {"EXISTS": exists, "THINKING": thinking, "THROWS": throws, "STATUS": status,
                            "PORT": port}.items():
            wrapper = wrapper.replace("@" + name + "@", json.dumps(value))
        result = subprocess.run(["node", "-"], input=wrapper + code, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.stdout + result.stderr, "")
        return result.returncode

    def test_deployed_knowledge_readiness_missing_data_redirect_and_error(self):
        self.assertEqual(self.node(verify.OK_JS), 0)
        self.assertEqual(self.node(verify.OK_JS, exists=False), 1)
        self.assertEqual(self.node(verify.OK_JS, status=503), 1)
        self.assertEqual(self.node(verify.OK_JS, status=302), 1)
        self.assertEqual(self.node(verify.OK_JS, throws=True), 1)
        # The port is the PORT setting of the container (compose.yaml: 80). A container
        # with another port, with no port or with a text gives no pass.
        for port in ("8080", None, "", "80/readyz#", "fixture"):
            self.assertEqual(self.node(verify.OK_JS, port=port), 1)

    def test_agent_no_bearer_health_and_xhigh(self):
        self.assertEqual(self.node(verify.AGENT_JS, status=401), 0)
        for status in (200, 302, 500):
            self.assertEqual(self.node(verify.AGENT_JS, status=status), 1)
        self.assertEqual(self.node(verify.AGENT_JS, status=401, thinking="high"), 1)
        self.assertEqual(self.node(verify.AGENT_JS, status=401, throws=True), 1)


AWS_STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['STUB_LOG'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
if 'send-command' in args:
    path = args[args.index('--parameters') + 1].removeprefix('file://')
    Path(os.environ['STUB_SSM']).write_text(Path(path).read_text())
    print('fixture-command-id')
elif 'get-command-invocation' in args:
    query = args[args.index('--query') + 1]
    print({'Status': os.environ.get('STUB_STATE', 'Success'),
           'StandardOutputContent': 'fixture internal health evidence',
           'StandardErrorContent': ''}[query])
elif 'get-secret-value' in args:
    names = ('EC2_OPENVIKING_LITELLM_API_KEY', 'EC2_PI_DURABLE_LITELLM_API_KEY',
             'EC2_PI_DURABLE_OPENVIKING_API_KEY')
    outcome = os.environ.get('STUB_GENERATED', 'present')
    if outcome in ('missing', 'read-failure'):
        error = 'ResourceNotFoundException' if outcome == 'missing' else 'AccessDeniedException'
        print(error + ': fixture-private-value', file=sys.stderr)
        raise SystemExit(254)
    data = dict.fromkeys(names, 'fixture-private-value')
    if outcome == 'partial':
        del data[names[0]]
    elif outcome == 'empty':
        data = {}
    print(json.dumps(data))
elif 'describe-stacks' in args:
    query = args[args.index('--query') + 1]
    print('CREATE_COMPLETE' if query == 'Stacks[0].StackStatus' else 'InstanceId\\ti-fixture')
else:
    raise SystemExit(99)
'''


class SsmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.env = self.work / "env"
        self.env.write_text("EC2_PROJECT=tenant-ec2\nEC2_LITELLM_MASTER_KEY=fixture-private-value\n")
        self.aws = self.work / "aws"
        self.aws.write_text(AWS_STUB)
        self.aws.chmod(0o755)
        self.log, self.ssm = self.work / "log", self.work / "ssm"
        # The shell environment does not reach the script: it can hold a setting or a key.
        self.environ = {"PATH": str(self.work) + os.pathsep + os.environ["PATH"], "HOME": str(self.work),
                        "STUB_LOG": str(self.log), "STUB_SSM": str(self.ssm)}

    def run_host(self, dry=False, trace=False, state="Success", generated="present"):
        args = ["sh"] + (["-x"] if trace else []) + [str(ROOT / "scripts/aws-host.sh"),
                "--env-file", str(self.env)] + (["--dry-run"] if dry else []) + ["verify"]
        result = subprocess.run(args, env=dict(self.environ, STUB_STATE=state, STUB_GENERATED=generated), text=True,
                                capture_output=True, timeout=10)
        self.assertNotIn("fixture-private-value", result.stdout + result.stderr)
        return result

    def test_dry_run_no_aws_no_public_domain_or_secrets(self):
        result = self.run_host(dry=True, trace=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertIn("compare the three required generated names with tenant-ec2/generated; "
                      "never compare or print values", result.stdout)
        # The environment file names no option: the file set of the core.
        self.assertIn("python3 -I scripts/verify-deployed.py --repo . --file-set " + FILE_SETS[""]
                      + " --edge-names ok || rc=1", result.stdout)
        for text in ("https://", "kits/", "healthz", "smoke.sh", "scripts/verify.py",
                     "pangolin", "gerbil", "traefik", "copilot"):
            self.assertNotIn(text, result.stdout)

    def test_generated_secret_outcomes_continue_or_fail_before_ssm(self):
        ok = "ok - generated secret: all required names are present"
        skip = "skip - generated secret: not in this deployment; apply the host stack update first"
        for outcome in ("present", "missing", "partial", "empty", "read-failure"):
            with self.subTest(outcome=outcome):
                self.log.unlink(missing_ok=True)
                self.ssm.unlink(missing_ok=True)
                result = self.run_host(generated=outcome, trace=True)
                continues = outcome in ("present", "missing")
                self.assertEqual(result.returncode, 0 if continues else 1)
                self.assertEqual(result.stdout.splitlines().count(ok), int(outcome == "present"))
                self.assertEqual(result.stdout.splitlines().count(skip), int(outcome == "missing"))
                self.assertEqual("generated: secret absent" in result.stdout, outcome == "missing")
                self.assertEqual("verify: pass" in result.stdout, continues)
                self.assertEqual(self.ssm.exists(), continues)
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertEqual(sum("get-secret-value" in args for args in calls), 1)
                self.assertEqual(sum("send-command" in args for args in calls), int(continues))
                if not continues:
                    self.assertEqual(len(calls), 1)
                    self.assertIn("the generated secret lacks a required name or cannot be read", result.stderr)
                if outcome == "partial":
                    self.assertIn("EC2_OPENVIKING_LITELLM_API_KEY: absent", result.stdout)
                    self.assertIn("EC2_PI_DURABLE_LITELLM_API_KEY: present", result.stdout)
                self.assertNotIn("fixture-private-value", self.log.read_text())
                if self.ssm.exists():
                    self.assertNotIn("fixture-private-value", self.ssm.read_text())

    def test_one_stub_ssm_command_uses_host_file_and_propagates_failure(self):
        for state in ("Success", "Failed", "TimedOut"):
            with self.subTest(state=state):
                if self.log.exists():
                    self.log.unlink()
                result = self.run_host(state=state)
                self.assertEqual(result.returncode, 0 if state == "Success" else 1)
                commands = json.loads(self.ssm.read_text())["commands"]
                self.assertEqual(commands[:3], ["set +x", "rc=0", "cd '/opt/tenant-ec2' || exit 1"])
                self.assertEqual(commands[-2:], [
                    "python3 -I scripts/verify-deployed.py --repo . --file-set " + FILE_SETS[""]
                    + " --edge-names ok || rc=1", "exit $rc"])
                # No error text of Compose reaches the output of the SSM command.
                for line in commands:
                    if line.startswith(("docker compose", "running=$(docker compose")):
                        self.assertIn("2>/dev/null", line)
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertEqual(sum('send-command' in args for args in calls), 1)
                self.assertEqual(sum('get-secret-value' in args for args in calls), 1)
                for name in ('EC2_OPENVIKING_LITELLM_API_KEY', 'EC2_PI_DURABLE_LITELLM_API_KEY',
                             'EC2_PI_DURABLE_OPENVIKING_API_KEY'):
                    self.assertIn(name + ': present', result.stdout)
                self.assertNotIn("fixture-private-value", self.log.read_text() + self.ssm.read_text())

    def test_each_value_of_the_options_gives_its_file_set_to_the_verifier(self):
        for options, wanted in FILE_SETS.items():
            with self.subTest(options=options):
                file_set, edge_names, text = deploy_script_inputs(self.work, options, "ok,llm")
                self.assertEqual(file_set, wanted)
                self.assertEqual(edge_names, "ok,llm")
                # The service list of the deploy script is the list of the Compose files.
                listed = [line.split('echo "service ')[1].split(":")[0] for line in text.splitlines()
                          if 'then echo "service ' in line]
                self.assertEqual(sorted(listed), sorted(services_of(wanted)))
        self.assertFalse(self.log.exists())

    def test_shell_injection_project_rejected_before_aws(self):
        self.env.write_text("EC2_PROJECT=tenant-ec2;echo unsafe\n")
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertIn("safe host directory name", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
