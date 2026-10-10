#!/usr/bin/env python3
"""Read bounded local inputs without evaluating or returning their values."""
import ipaddress
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest

LIMIT = 1024 * 1024
ASSIGN = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")
BLOCK = re.compile(r"# ----- (Option|DNS provider) ([a-z0-9]+) -----")
PART = re.compile(r"# ===== Part ([0-9]+)")
OPTIONS = {"pangolin", "copilot"}
PROVIDERS = {"manual", "porkbun", "route53", "cloudflare"}
EDGES = {"ok", "agent", "llm", "ov"}


def text_file(path):
    # Refuse links and special files before reading. Never wait on a FIFO.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("regular file required")
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("input too large")
    return data.decode("utf-8")


def value_text(raw):
    raw = raw.strip()
    if raw.startswith(("'", '"')):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end < 0 or (raw[end + 1:].strip() and not raw[end + 1:].lstrip().startswith("#")):
            raise ValueError("unsupported quote")
        return raw[1:end]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def env_rows(text):
    values, lines, problems, groups = {}, {}, [], {}
    part, block = 0, None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        marker = PART.match(stripped)
        section = BLOCK.fullmatch(stripped)
        if marker:
            part, block = int(marker[1]), None
        elif section:
            block = (section[1], section[2])
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGN.fullmatch(stripped)
        if not match:
            problems.append(number)
            continue
        name, raw = match.groups()
        if name in values:
            problems.append(number)
        try:
            values[name] = value_text(raw)
            lines[name] = number
            groups[name] = (part, block)
        except ValueError:
            problems.append(number)
    return values, lines, problems, groups


def advice(root, env_path):
    findings, notes = [], []

    def add(rank, fact, evidence, why, change, proof):
        findings.append((rank, fact, evidence, why, change, proof))

    try:
        values, lines, problems, _ = env_rows(text_file(env_path))
    except (OSError, ValueError, UnicodeError):
        return ["Advisory security review: suggestions only. No file changes.",
                "Not verified: the environment input is missing, unreadable, too large, or not a regular UTF-8 file."]
    for number in problems:
        notes.append(f"Not verified: environment input line {number} is duplicate or outside the supported assignment format.")

    def selected(name, default, allowed):
        raw = values.get(name, default)
        parts = raw.split(",") if raw else []
        if any(item not in allowed for item in parts) or len(parts) != len(set(parts)):
            notes.append(f"Not verified: {name} has an unsupported selection. Only recognized entries inform this review.")
        return set(parts) & allowed

    options = selected("EC2_OPTIONS", "", OPTIONS)
    edges = selected("EC2_EDGE_NAMES", "ok", EDGES)
    provider = values.get("EC2_DNS_PROVIDER", "manual")
    if provider not in PROVIDERS:
        notes.append("Not verified: EC2_DNS_PROVIDER has an unsupported selection.")

    def env_evidence(name):
        return f"environment name {name}, line {lines[name]}" if name in lines else f"environment name {name}, absent"

    if "pangolin" in options:
        add(4, "The Pangolin option selects an edge with extra public tunnel ports.", env_evidence("EC2_OPTIONS"),
            "The tunnel adds a path from the public host toward each connected site.",
            "Limit site targets with a separate network and local firewall rules.",
            "In an approved test, confirm that a site cannot reach an unlisted target.")
    if "copilot" in options:
        add(3, "The Copilot option needs a separate login token on the host.", env_evidence("EC2_OPTIONS"),
            "A copied token can use the account allowance until revocation.",
            "Limit access to token files and backups. Set model and request limits on client keys.",
            "Check file access and test revocation with a test account in an approved window.")
    if provider in PROVIDERS - {"manual"}:
        add(3, "A DNS provider integration is selected.", env_evidence("EC2_DNS_PROVIDER"),
            "A DNS credential can change records beyond this install when its scope is broad.",
            "Use the smallest zone and record scope. Keep the credential on the development machine.",
            "Check the provider policy without printing its credential. Test a denied record in an approved zone.")
    if "pangolin" in options:
        add(4, "The Pangolin certificate flow needs a DNS credential on the host.", env_evidence("EC2_OPTIONS"),
            "The public TLS process can read that credential.",
            "Use a dedicated challenge zone or tightly restrict the credential.",
            "In an approved test, confirm that the credential cannot change an unrelated domain.")
    if edges - {"ok"}:
        add(3, "An optional API edge name is selected.", env_evidence("EC2_EDGE_NAMES"),
            "The API adds an external entry point beyond the browser login.",
            "Keep the admin range narrow and retain the service key and path restrictions.",
            "In an approved test, try a denied address, a missing key, and an excluded path.")

    admin = values.get("EC2_ADMIN_CIDR", "")
    if admin:
        try:
            network = ipaddress.ip_network(admin, strict=False)
            if "/" not in admin:
                raise ValueError("prefix required")
            if network.num_addresses > 1:
                add(4, "The admin range admits more than one address.", env_evidence("EC2_ADMIN_CIDR"),
                    "Each address in the range can reach an admin entry point.",
                    "Use one administrator address or a private session instead of widening access.",
                    "Repeat this review. Then check access from allowed and denied addresses in an approved test.")
        except ValueError:
            notes.append("Not verified: EC2_ADMIN_CIDR is not a supported CIDR range.")
    else:
        add(3, "The admin range has no value.", env_evidence("EC2_ADMIN_CIDR"),
            "This review cannot determine the admin network boundary.",
            "Set the intended admin range in the private input file.",
            "Repeat this review without sharing the range.")

    files = ["compose.yaml", "compose.pangolin.yaml" if "pangolin" in options else "compose.caddy.yaml"]
    if "copilot" in options:
        files.append("compose.copilot.yaml")
    required = set()
    try:
        sample, _, _, groups = env_rows(text_file(root / ".env.example"))
        for name, (part, block) in groups.items():
            if block is None and part == 1:
                required.add(name)
            elif block and ((block[0] == "Option" and block[1] in options)
                            or (block[0] == "DNS provider" and block[1] == provider)):
                if not sample[name]:
                    required.add(name)
    except (OSError, ValueError, UnicodeError):
        notes.append("Not verified: the sample environment names are unavailable.")
    for name in files:
        try:
            content = text_file(root / name)
        except (OSError, ValueError, UnicodeError):
            notes.append(f"Not verified: {name} is unavailable for the selected file set.")
            continue
        service = None
        for number, line in enumerate(content.splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            required.update(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?", line))
            if re.match(r"^[^\s#]", line):
                service = None
            match = re.match(r"^  ([a-zA-Z0-9_-]+):", line)
            if match:
                service = match[1]
            if service and service not in {"caddy", "gerbil", "traefik"} and re.match(r"^    ports\s*:", line):
                if re.match(r"^    ports\s*:\s*(?:\[\s*\]|null)(?:\s*#.*)?\s*$", line):
                    continue
                add(4, "A non-edge service declares published ports.", f"{name}:{number}",
                    "A direct port can bypass the edge login and address checks.",
                    "Remove the publication or document a private-only binding.",
                    "Inspect the merged model privately and confirm that only the selected edge publishes ports.")
            if re.search(r"\bnetwork_mode:\s*['\"]?host\b|\bprivileged:\s*true\b", line):
                add(4, "A Compose entry requests host networking or privileged mode.", f"{name}:{number}",
                    "The entry weakens container separation.",
                    "Use a limited network and only the necessary capabilities.",
                    "Inspect the merged model privately and check the effective container rights in an approved test.")

    for name in sorted(required):
        if not values.get(name):
            add(3, f"The input has no value for {name}.", env_evidence(name),
                "A required credential or setting may be absent. Some names are generated after the first start.",
                "Check when this name is required. Supply it privately when its service needs it.",
                "Repeat the names-only review. Use the install guide for generated and optional names.")
    notes.append("Not verified: live cloud settings, credential scope, login enforcement, and the deployed network path.")
    notes.append("Not verified: full YAML semantics, inherited Compose files, shell overrides, and environment interpolation.")
    notes.append("This reader checks literal assignments and conventional Compose blocks. It is not a deploy validator.")
    output = ["Advisory security review: suggestions only. No file changes."]
    for rank, fact, evidence, why, change, proof in sorted(findings, key=lambda item: -item[0]):
        output += [f"\nRank {rank}: {fact}", f"Evidence: {evidence}", f"Why: {why}",
                   f"Suggestion: {change}", f"Proof: {proof}"]
    if not findings:
        output.append("No suggestion from these checks. This is not a security approval.")
    return output + notes


class SelfTest(unittest.TestCase):
    root = None

    def test_inputs(self):
        with tempfile.TemporaryDirectory(prefix="security-review-") as directory:
            root = Path(directory)
            (root / "skills/security-review").mkdir(parents=True)
            (root / "scripts").mkdir()
            for name in ("skills/security-review/review.py", "scripts/security-review.sh"):
                (root / name).write_bytes((self.root / name).read_bytes())
            (root / ".env.example").write_text(
                "# ===== Part 1: the names of the core =====\nEC2_EXAMPLE_KEY=\n"
                "# ===== Part 2: settings =====\nEC2_OPTIONS=\n"
                "# ----- Option pangolin -----\nEC2_PANGOLIN_SERVER_SECRET=\n"
                "# ----- Option copilot -----\nEC2_COPILOT_MASTER_KEY=\n"
                "# ----- DNS provider cloudflare -----\nEC2_DNS_EXAMPLE_KEY=\n")
            (root / "compose.yaml").write_text("services:\n  app:\n    ports:\n      - '9999:9999'\n")
            for name in ("compose.caddy.yaml", "compose.pangolin.yaml", "compose.copilot.yaml"):
                (root / name).write_text("services: {}\n")
            sentinel = "fixture-private-do-not-print-7qK2"
            marker = root / "not-created"
            inputs = {
                "sample": (self.root / ".env.example").read_text(),
                "options": "EC2_OPTIONS=pangolin,copilot\nEC2_DNS_PROVIDER=porkbun\nEC2_EDGE_NAMES=ok,agent,llm,ov\n",
                "wide": "EC2_ADMIN_CIDR=198.51.100.0/24\n",
                "single": "EC2_ADMIN_CIDR=198.51.100.7/32\n",
                "v6wide": "EC2_ADMIN_CIDR=2001:db8::/64\n",
                "v6single": "EC2_ADMIN_CIDR=2001:db8::1/128\n",
                "quoted": 'EC2_ADMIN_CIDR="198.51.100.0/24" # comment\nEC2_EXAMPLE_KEY=""\n',
                "malformed": f"EC2_ADMIN_CIDR={sentinel}\n{sentinel}\nEC2_OPTIONS={sentinel}\n",
                "duplicates": f"EC2_EXAMPLE_KEY='{sentinel}'\nEC2_EXAMPLE_KEY=\n",
                "shell": f"EC2_EXAMPLE_KEY=$(touch {marker})\n",
                "cloudflare": "EC2_DNS_PROVIDER=cloudflare\n",
                "route53": "EC2_DNS_PROVIDER=route53\n",
            }
            results = {}
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            for label, content in inputs.items():
                path = root / (label + ".env")
                path.write_text(content + f"\nUNRELATED_SECRET='{sentinel}'\n")
                original = path.read_bytes()
                call = subprocess.run(["sh", "-x", str(root / "scripts/security-review.sh"), "--env-file", str(path)],
                                      capture_output=True, text=True, env={"PATH": os.environ.get("PATH", ""),
                                                                         "EC2_ADMIN_CIDR": "0.0.0.0/0"}, timeout=10)
                self.assertEqual(call.returncode, 0)
                self.assertNotIn(sentinel, call.stdout + call.stderr)
                self.assertNotIn(str(path), call.stdout + call.stderr)
                self.assertNotIn("198.51.100.", call.stdout + call.stderr)
                self.assertNotIn("2001:db8", call.stdout + call.stderr)
                self.assertEqual(path.read_bytes(), original)
                results[label] = call.stdout
            for label in ("wide", "v6wide", "quoted"):
                self.assertIn("range admits more than one address", results[label])
            for label in ("single", "v6single"):
                self.assertNotIn("range admits more than one address", results[label])
            self.assertIn("extra public tunnel ports", results["options"])
            self.assertIn("Copilot option needs", results["options"])
            self.assertNotIn("extra public tunnel ports", results["single"])
            self.assertIn("non-edge service declares published ports", results["single"])
            self.assertIn("no value for EC2_EXAMPLE_KEY", results["quoted"])
            self.assertIn("no value for EC2_DNS_EXAMPLE_KEY", results["cloudflare"])
            self.assertNotIn("no value for EC2_DNS_EXAMPLE_KEY", results["route53"])
            self.assertFalse(marker.exists())
            link = root / "link.env"
            link.symlink_to(root / "single.env")
            fifo = root / "fifo.env"
            os.mkfifo(fifo)
            invalid = root / "invalid.env"
            invalid.write_bytes(b"\xff\x00")
            oversized = root / "oversized.env"
            oversized.write_bytes(b"x" * (LIMIT + 1))
            for path in (root / "missing.env", link, fifo, root, invalid, oversized):
                call = subprocess.run(["sh", str(root / "scripts/security-review.sh"), "--env-file", str(path)],
                                      capture_output=True, text=True, timeout=10)
                self.assertEqual(call.returncode, 0)
                self.assertIn("Not verified:", call.stdout)
                self.assertNotIn(str(path), call.stdout + call.stderr)
            call = subprocess.run(["sh", str(root / "scripts/security-review.sh"), "--unknown", sentinel],
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual(call.returncode, 0)
            self.assertIn("Not verified:", call.stdout)
            self.assertNotIn(sentinel, call.stdout + call.stderr)
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)


def main():
    root, args = Path(sys.argv[1]), sys.argv[2:]
    if args == ["--self-test"]:
        SelfTest.root = root
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(SelfTest).run(result)
        print("security-review self-test: " + ("pass" if result.wasSuccessful() else "FAIL (details withheld to protect fixture values)"))
    elif args in ([], ["--help"], ["-h"]) or (len(args) == 2 and args[0] == "--env-file"):
        if args in (["--help"], ["-h"]):
            print("usage: sh scripts/security-review.sh [--env-file FILE | --self-test]")
            print("Default input: .env.example. Reads literal values internally; prints names and derived facts only. Always exits 0.")
        else:
            path = Path(args[1]) if args else root / ".env.example"
            print("\n".join(advice(root, path)))
    else:
        print("Not verified: unsupported arguments. Use --help. Advice never blocks a change.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Not verified: the advisory reader could not finish. No input value is shown.")
