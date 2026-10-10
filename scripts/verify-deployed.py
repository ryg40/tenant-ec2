#!/usr/bin/env python3
"""Read-only checks of this stack's deployed containers. No model request or image smoke.

Run on the host: python3 -I scripts/verify-deployed.py [--repo PATH]
                     [--file-set FILES] [--edge-names NAMES]
The host supplies Docker Compose and python3-pyyaml. Container keys stay inside
container processes; no key enters a Docker argument or the SSM command text.

The file set. FILES is the list of the Compose files of the host in the form of
COMPOSE_FILE: file names with a colon between two names. `scripts/aws-host.sh verify`
gives the file set of EC2_OPTIONS of its checkout. Without the option, the verifier
takes COMPOSE_FILE of the environment file of the host. The two must be equal. The
verifier holds no list of file sets and no list of services: it reads the services
from the Compose files of the file set, and it checks these services only. A service
of a Compose file that is not in the file set gets no check.

The public names. NAMES is EC2_EDGE_NAMES of the caller, with commas. It must name the
same names as EC2_EDGE_NAMES of the environment file of the host. For each name of
that list, the verifier checks the path from the edge to the service of the name on
the internal network. It makes no public request.
"""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

STATE_FORMAT = (
    "{{.State.Running}}|{{.State.Status}}|{{.State.Paused}}|{{.State.Restarting}}|"
    "{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}"
)
# The form of each name that reaches a command or a message.
FILE_NAME = re.compile(r"compose(\.[a-z0-9-]+)?\.yaml")
SERVICE_NAME = re.compile(r"[a-z][a-z0-9-]*")
ROUTE_FILE = re.compile(r"config(\.[a-z0-9-]+)?\.yaml")
ROUTE_NAME = re.compile(r"[a-z0-9]+(/[a-z0-9._-]+)?")
KITS = ("litellm", "openviking", "open-knowledge", "pi-durable")
# The network of the edge itself. Each other network `edge-<name>` holds the edge and
# the service of the public name <name>.
EDGE_CORE = "edge-core"
# The request that proves the path from the edge to a service of a public name: a
# health path that needs no key and makes no model call. The agent has no such path:
# it answers 401 with no bearer, and its /healthz can make a paid model call.
EDGE_PATHS = {
    "ok-auth": "http://ok-auth:4180/ready",
    "litellm": "http://litellm:4000/health/liveliness",
    "openviking": "http://openviking:1933/health",
}

# This code travels on stdin to the existing container. It reads only that
# container's keys and never prints response bodies, errors, headers or values.
HTTP_PY = '''
import json
import os
import urllib.error
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

def get(base, path, key=None, expected=(200,), json_body=False):
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(base + path, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=5) as response:
            if response.status not in expected:
                raise ValueError()
            if not json_body:
                return None
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError()
            return json.loads(body)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if status not in expected or json_body:
            raise ValueError()
        return None

def key(name):
    value = os.environ.get(name, "")
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError()
    return value

def models(base, bearer, names):
    data = get(base, "/v1/models", bearer, json_body=True)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError()
    ids = {item.get("id") for item in data["data"] if isinstance(item, dict)}
    if not set(names).issubset(ids):
        raise ValueError()
'''

# The gateway. `routes` is the list of the routes of the route file of the file set.
GATEWAY_PY = '''
try:
    base = "http://127.0.0.1:4000"
    get(base, "/health/liveliness")
    get(base, "/v1/models", expected=(401, 403))
    get(base, "/v1/models", "invalid-deployment-probe", expected=(401, 403))
    models(base, key("LITELLM_MASTER_KEY"), routes)
except Exception:
    raise SystemExit(1)
'''

# Only with the service `copilot` in the file set. It runs in the gateway container.
COPILOT_PY = '''
try:
    # The gateway already holds this internal service key. Listing can be
    # empty before manual Copilot login; it proves no provider access.
    base = "http://copilot:4000"
    get(base, "/health/liveliness")
    get(base, "/v1/models", expected=(401, 403))
    models(base, key("COPILOT_MASTER_KEY"), ())
except Exception:
    raise SystemExit(1)
'''

MEMORY_PY = '''
try:
    base = "http://127.0.0.1:1933"
    data = get(base, "/health", json_body=True)
    if not isinstance(data, dict) or data.get("healthy") is not True:
        raise ValueError()
    get(base, "/api/v1/system/status", expected=(401, 403))
    # The service virtual key permits model calls, not administrative
    # checks. Prove the network path without widening its route scope.
    get("http://litellm:4000", "/health/liveliness")
except Exception:
    raise SystemExit(1)
'''


def gateway_code(routes, copilot):
    """The text for the gateway container: the routes of the file set, then the probes."""
    if any(not isinstance(name, str) or not ROUTE_NAME.fullmatch(name) for name in routes):
        raise Failure("unsupported gateway route contract")
    return ("routes = " + repr(tuple(routes)) + "\n" + HTTP_PY + GATEWAY_PY
            + (COPILOT_PY if copilot else ""))


def memory_code():
    return HTTP_PY + MEMORY_PY


# OpenKnowledge listens on the port of its PORT setting (compose.yaml).
OK_JS = '''
const fs = require('node:fs');
(async () => {
  fs.accessSync('/data/.ok', fs.constants.R_OK);
  if (!/^[0-9]{1,5}$/.test(process.env.PORT || '')) process.exit(1);
  const r = await fetch('http://127.0.0.1:' + process.env.PORT + '/readyz', {
    signal: AbortSignal.timeout(5000), redirect: 'manual'
  });
  process.exit(r.status === 200 ? 0 : 1);
})().catch(() => process.exit(1));
'''

AGENT_JS = '''
(async () => {
  if (process.env.RESEARCHER_THINKING !== 'xhigh') process.exit(1);
  const r = await fetch('http://127.0.0.1:5470/v1/', {
    signal: AbortSignal.timeout(5000), redirect: 'manual'
  });
  process.exit(r.status === 401 ? 0 : 1);
})().catch(() => process.exit(1));
'''


class Failure(Exception):
    """Only fixed, public-safe messages enter this exception."""


def read_yaml(path):
    try:
        import yaml
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except Exception:
        raise Failure("cannot read stack YAML (python3-pyyaml is required)") from None


class Verifier:
    def __init__(self, repo, file_set=None, edge_names=None):
        self.repo = repo
        self.file_set = file_set
        self.edge_names = edge_names
        # verify() adds one -f for each Compose file of the file set.
        self.base = [
            "docker", "compose", "--project-directory", str(repo),
            "--env-file", str(repo / ".env"),
        ]
        self.compose = list(self.base)
        self.containers = {}

    def run(self, args, label, code=None):
        try:
            home = os.environ.get("HOME")
            if home is None:
                home = str(Path.home())
            result = subprocess.run(
                args, input=code, text=True, capture_output=True, timeout=45,
                cwd=self.repo, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                   "HOME": home},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise Failure(label + " (command unavailable or timed out)") from None
        if result.returncode:
            # Compose, Docker, and application errors can contain credentials.
            raise Failure(label) from None
        return result.stdout.strip()

    def require(self, service):
        ids = self.run(self.compose + ["ps", "--all", "--quiet", service],
                       service + ": cannot find deployed container").splitlines()
        if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
            raise Failure(service + ": expected one deployed container; missing or ambiguous")
        state = self.run(["docker", "inspect", "--format", STATE_FORMAT, ids[0]],
                         service + ": cannot inspect deployed container")
        if state != "true|running|false|false|healthy":
            raise Failure(service + ": required container is not running and healthy")
        self.containers[service] = ids[0]
        print("ok - " + service + ": deployed container is running and healthy")

    def probe(self, service, command, label, code=None, expected_output=None):
        output = self.run(["docker", "exec"] + (["-i"] if code is not None else [])
                          + [self.containers[service]] + command,
                          service + ": " + label + " failed", code)
        if expected_output is not None and output != expected_output:
            raise Failure(service + ": " + label + " failed")
        print("ok - " + service + ": " + label)

    def host_setting(self, name):
        """The value of one name of the environment file of the host, or None. The file
        holds each secret of the host: no other line of it reaches a message."""
        try:
            text = (self.repo / ".env").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise Failure("cannot read the environment file of the host") from None
        value = None
        for line in text.splitlines():
            if line.startswith(name + "="):
                value = line[len(name) + 1:].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
        return value

    def files(self):
        """The Compose files of the file set: the input, compared with the host."""
        host = self.host_setting("COMPOSE_FILE")
        wanted = host if self.file_set is None else self.file_set
        if not wanted:
            raise Failure("file set: the environment file of the host has no COMPOSE_FILE; "
                          "run the bootstrap again")
        names = wanted.split(":")
        if (names[0] != "compose.yaml" or len(set(names)) != len(names)
                or any(not FILE_NAME.fullmatch(name) for name in names)):
            raise Failure("file set: not a list of Compose files that starts with compose.yaml")
        if host != wanted:
            # The two values hold file names only.
            raise Failure("file set " + wanted + ": COMPOSE_FILE of the host is another file set. "
                          "Write the secret, then run the bootstrap again")
        if any(not (self.repo / name).is_file() for name in names):
            raise Failure("file set " + wanted + ": a Compose file is not in the host snapshot")
        print("ok - file set " + wanted + ": the host has it")
        return names

    def model(self, names):
        """Each service of the Compose files of the file set, with its networks and its
        command. An overlay names a service of compose.yaml again: the networks add up,
        and the last command wins, as in Compose."""
        services = {}
        for name in names:
            found = read_yaml(self.repo / name).get("services", {})
            if not isinstance(found, dict):
                raise Failure("unsupported Compose service contract")
            for service, body in found.items():
                if (not isinstance(service, str) or not SERVICE_NAME.fullmatch(service)
                        or not isinstance(body, dict)):
                    raise Failure("unsupported Compose service contract")
                item = services.setdefault(service, {"networks": set(), "command": None})
                # No networks: a service that uses the network of another service.
                networks = body.get("networks") or []
                if not isinstance(networks, (dict, list)):
                    raise Failure("unsupported Compose service contract")
                item["networks"].update(str(network) for network in networks)
                if "command" in body:
                    item["command"] = body["command"]
        return services

    def routes(self, name, depth=0):
        """The routes of one route file of the gateway, with the files that it includes."""
        if depth > 3 or not isinstance(name, str) or not ROUTE_FILE.fullmatch(name):
            raise Failure("unsupported gateway route contract")
        data = read_yaml(self.repo / "litellm" / name)
        found = []
        for other in data.get("include") or ():
            found += self.routes(other, depth + 1)
        for item in data.get("model_list") or ():
            route = item.get("model_name") if isinstance(item, dict) else None
            if not isinstance(route, str) or not ROUTE_NAME.fullmatch(route):
                raise Failure("unsupported gateway route contract")
            found.append(route)
        return found

    def gateway_routes(self, services):
        """The routes of the file that the gateway of the file set reads."""
        command = services["litellm"]["command"]
        if (not isinstance(command, list) or "--config" not in command[:-1]
                or not isinstance(command[command.index("--config") + 1], str)):
            raise Failure("unsupported gateway route contract")
        routes = self.routes(command[command.index("--config") + 1].rsplit("/", 1)[-1])
        if not routes:
            raise Failure("unsupported gateway route contract")
        return routes

    def public_names(self, services):
        """EC2_EDGE_NAMES of the host, and for each public name of the file set its
        network, its service and the member of the edge on that network."""
        host = self.host_setting("EC2_EDGE_NAMES")
        if not host:
            raise Failure("public names: the environment file of the host has no EC2_EDGE_NAMES")
        active = host.split(",")
        wanted = active if self.edge_names is None else self.edge_names.split(",")
        if any(not re.fullmatch(r"[a-z]+", name) for name in active + wanted):
            raise Failure("public names: EC2_EDGE_NAMES is not a list of names with commas")
        if sorted(active) != sorted(wanted):
            raise Failure("public names: EC2_EDGE_NAMES of the host is another list. "
                          "Write the secret, then run the bootstrap again")
        known = {}
        networks = {network for item in services.values() for network in item["networks"]}
        for network in sorted(networks):
            if not network.startswith("edge-") or network == EDGE_CORE:
                continue
            members = sorted(name for name, item in services.items() if network in item["networks"])
            edge = [name for name in members if EDGE_CORE in services[name]["networks"]]
            target = [name for name in members if name not in edge]
            if len(edge) != 1 or len(target) != 1:
                raise Failure("unsupported edge network contract")
            known[network[len("edge-"):]] = (network, target[0], edge[0])
        if any(name not in known for name in active):
            raise Failure("public names: a name of EC2_EDGE_NAMES has no edge network in the file set")
        return active, known

    def verify(self):
        names = self.files()
        self.compose = self.base + [word for name in names for word in ("-f", str(self.repo / name))]
        services = self.model(names)
        # Each service of the file set needs a check of this file. A new service of a
        # Compose file stops the verifier here, so no service passes with no check.
        checks = self.checks()
        unknown = sorted(name for name in services if name not in checks)
        if unknown:
            raise Failure(unknown[0] + ": the verifier has no check for this service of the file set")
        lock = read_yaml(self.repo / "kits.lock").get("server_kits", {})
        for kit in KITS:
            item = lock.get(kit)
            if not isinstance(item, dict) or item.get("service") != kit or kit not in services:
                raise Failure("unsupported server kit contract")
            tag, commit = item.get("source_tag"), item.get("source_commit")
            if not tag or not commit:
                if kit != "pi-durable":
                    raise Failure(kit + ": required kit has no source pin")
            elif not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise Failure(kit + ": invalid source pin")
        if set(lock) != set(KITS):
            raise Failure("unsupported server kit contract")
        agent_pinned = bool(lock["pi-durable"].get("source_tag")
                            and lock["pi-durable"].get("source_commit"))
        absent = set()
        if not agent_pinned:
            # The bootstrap starts no service of a kit that has no pin.
            absent.add("pi-durable")
            print("skip - pi-durable: absent source pin; no agent deployment proof")
        if "ok-auth" not in services or services["ok-auth"]["command"] == ["sleep", "infinity"]:
            raise Failure("unsupported ok-auth contract; the gate of the kit is not a sleeping placeholder")
        print("skip - Entra login: a ready gate proves no login of a user; no request goes to the tenant")
        routes = self.gateway_routes(services)
        active, known = self.public_names(services)
        if "pangolin" in services:
            # The blueprint is a file of the Pangolin edge. A resource is on only through
            # its reference: scripts/render-pangolin.sh sets it from EC2_EDGE_NAMES.
            resources = read_yaml(self.repo / "pangolin/blueprint.yaml").get("public-resources", {})
            if not isinstance(resources, dict) or set(resources) != set(known):
                raise Failure("public resource contract changed; separate edge verification is required")
            for name in sorted(resources):
                item = resources[name]
                if not isinstance(item, dict) or item.get("enabled") != "${EDGE_" + name.upper() + "}":
                    raise Failure("public resource contract changed; separate edge verification is required")
        for name in sorted(known):
            if name not in active:
                print("skip - public " + name + ": not in EC2_EDGE_NAMES of the host; the edge has no route to it")
        print("skip - public routes, certificates and logins: no public request runs")
        print("skip - paid model answers and embeddings: later approval required; Luna probes must use xhigh")
        print("skip - service virtual-key model calls: no billable request or key scope change")
        if "copilot" in services:
            print("skip - Copilot login and provider access: health and listing do not prove them")
        print("skip - provider access of the gateway routes: health and listing do not prove it")
        print("skip - standalone image smoke: separate from deployed-service checks")
        # Check every required container before a network probe. Never start or
        # repair a missing service, and never treat an empty ps result as success.
        required = [name for name in checks if name in services and name not in absent]
        for service in required:
            self.require(service)
        for service in required:
            checks[service](services, routes)
        for name in active:
            network, target, edge = known[name]
            if target in absent:
                print("skip - public " + name + ": the service " + target + " has no source pin")
            elif target not in EDGE_PATHS:
                print("skip - public " + name + ": no path check from the edge without a bearer; "
                      "the check of the service " + target + " stays")
            else:
                self.probe(edge, ["wget", "-q", "-T", "5", "-O", "/dev/null", EDGE_PATHS[target]],
                           "path from the edge to " + target + " for the public name " + name
                           + " (network " + network + "; no public request)")
        print("verify-deployed: pass (health/authentication only; explicit skips remain)")

    def checks(self):
        """The check of each service, in the order of the checks: the edge first. A service
        gets its check only when a Compose file of the file set names it. This is not a
        list of the services of a file set: verify() reads that list from the files."""
        return {
            # Disable curlrc and redirects. Capture only the status, never the body.
            "pangolin": lambda services, routes: self.probe(
                "pangolin", ["curl", "--disable", "--silent", "--max-time", "5",
                             "--max-filesize", "65536", "--max-redirs", "0", "--noproxy", "*",
                             "--output", "/dev/null", "--write-out", "%{http_code}",
                             "http://127.0.0.1:3001/api/v1/"],
                "internal API health", expected_output="200"),
            "gerbil": lambda services, routes: self.probe(
                "gerbil", ["wget", "-q", "-T", "5", "-O", "/dev/null",
                           "http://gerbil-core:3004/healthz"], "internal health"),
            "traefik": lambda services, routes: self.probe(
                "traefik", ["traefik", "healthcheck",
                            "--configFile=/etc/traefik/traefik_config.yml"], "internal ping"),
            "caddy": lambda services, routes: self.probe(
                "caddy", ["wget", "-q", "-T", "5", "-O", "/dev/null", "http://127.0.0.1:8082/"],
                "internal health listener (no certificate and no public route proof)"),
            "litellm-db": lambda services, routes: self.probe(
                "litellm-db", ["pg_isready", "-U", "litellm", "-d", "litellm"],
                "database readiness (not a storage write test)"),
            "litellm": lambda services, routes: self.probe(
                "litellm", ["python", "-I", "-S", "-"],
                "internal health, authentication and listing of the routes " + " ".join(routes)
                + (" and Copilot network path" if "copilot" in services else ""),
                gateway_code(routes, "copilot" in services)),
            # The check of the gateway holds the path to this service.
            "copilot": lambda services, routes: None,
            "openviking": lambda services, routes: self.probe(
                "openviking", ["python", "-I", "-S", "-"],
                "internal health, authentication rejection and gateway health network path",
                memory_code()),
            "open-knowledge": lambda services, routes: self.probe(
                "open-knowledge", ["node", "-"],
                "deployed /data/.ok readable and internal readiness", OK_JS),
            "ok-auth": lambda services, routes: self.probe(
                "ok-auth", ["wget", "-q", "-T", "5", "-O", "/dev/null", "http://127.0.0.1:4180/ready"],
                "gate ready endpoint (no login proof)"),
            "pi-durable": lambda services, routes: self.probe(
                "pi-durable", ["node", "-"], "internal API rejects no bearer; xhigh setting", AGENT_JS),
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--file-set", help="the Compose files of the host, in the form of COMPOSE_FILE")
    parser.add_argument("--edge-names", help="EC2_EDGE_NAMES of the caller, with commas")
    args = parser.parse_args(argv)
    try:
        Verifier(args.repo.resolve(), args.file_set, args.edge_names).verify()
    except Failure as error:
        print("verify-deployed: FAIL - " + str(error), file=sys.stderr)
        return 1
    except Exception:
        # Do not leak a YAML error, process output or a credential in a traceback.
        print("verify-deployed: FAIL - unsupported stack data", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
