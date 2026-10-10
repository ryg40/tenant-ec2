"""The probe of the local run of the gate (ok-auth/local-run.sh).

    python probe.py edge        a client in the place of the edge: the login, the cookie, the group
    python probe.py neighbour   a client on the network of OpenKnowledge: a forged forwarded header
    python probe.py agent       a client in the place of the agent: OpenKnowledge with no gate
    python probe.py outsider    a client on another network of the stack: no path at all

Each check prints one line: `ok`, `skip` or `FAIL`, then its name. The exit status is 1
when a check fails. Only the standard library. The probe prints no cookie and no token.
"""

import base64
import http.client
import ipaddress
import json
import os
import socket
import ssl
import sys
import urllib.parse

DOMAIN = os.environ["PROBE_DOMAIN"]
HOST = "ok." + DOMAIN
TENANT = os.environ["PROBE_TENANT"]
CLIENT_ID = os.environ["PROBE_CLIENT_ID"]
GROUP = os.environ["PROBE_GROUP"]
ISSUER_BASE = os.environ["PROBE_ISSUER_BASE"].rstrip("/")
CA_FILE = os.environ["PROBE_CA_FILE"]
UPSTREAM = os.environ["PROBE_UPSTREAM"]
EDGE_RANGE = ipaddress.ip_network(os.environ["PROBE_EDGE_RANGE"])
COOKIE = "__Host-ok"
# Addresses of the documentation ranges (RFC 5737): the client behind the edge, and a forged one.
CLIENT_ADDRESS = "203.0.113.7"
FORGED_ADDRESS = "203.0.113.9"

failed = 0


def report(passed, name, detail=""):
    global failed
    if not passed:
        failed += 1
    print(("ok   " if passed else "FAIL ") + name + (f" ({detail})" if detail and not passed else ""), flush=True)


def skip(name, reason):
    print(f"skip {name} ({reason})", flush=True)


def addresses(name):
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(name, None, socket.AF_INET)})
    except socket.gaierror:
        return []


def request(address, port, method, path, headers=None, body=None, tls=False):
    """One request. The result is the status, the header list and the body."""
    if tls:
        # Only the stub issuer: the probe trusts the certificate of the run and no other.
        connection = http.client.HTTPSConnection(address, port, timeout=15, context=ssl.create_default_context(cafile=CA_FILE))
    else:
        connection = http.client.HTTPConnection(address, port, timeout=15)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.getheaders(), response.read()
    finally:
        connection.close()


def header(headers, name):
    return next((value for key, value in headers if key.lower() == name.lower()), "")


def set_cookies(headers):
    """Each Set-Cookie header as (name, value, attributes in lower case)."""
    result = []
    for key, value in headers:
        if key.lower() != "set-cookie":
            continue
        parts = [part.strip() for part in value.split(";")]
        name, _, content = parts[0].partition("=")
        attributes = {}
        for part in parts[1:]:
            attribute, _, attribute_value = part.partition("=")
            attributes[attribute.lower()] = attribute_value
        result.append((name, content, attributes))
    return result


def cookie_header(jar):
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def safe_flags(attributes):
    """The attributes that the ticket and the prefix `__Host-` need."""
    return ("secure" in attributes and "httponly" in attributes and attributes.get("path") == "/"
            and "domain" not in attributes and attributes.get("samesite", "").lower() == "lax")


def state_target(location):
    """The page that the gate returns to after the login. It is the end of the parameter `state`."""
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))
    return query, query.get("state", "").partition(":")[2]


class Edge:
    """A client in the place of the edge. Its address is in the range that the gate trusts."""

    def __init__(self):
        found = [a for a in addresses("ok-auth") if ipaddress.ip_address(a) in EDGE_RANGE]
        if len(found) != 1:
            sys.exit(f"probe: ok-auth has no address in the edge range {EDGE_RANGE}")
        self.gate = found[0]

    def gate_request(self, method, path, jar=None, extra=None):
        # The headers that the edge sets. The edge keeps the Host header of the browser.
        headers = {
            "Host": HOST, "Accept": "text/html", "User-Agent": "local-run-probe",
            "X-Forwarded-Proto": "https", "X-Forwarded-Host": HOST, "X-Forwarded-For": CLIENT_ADDRESS,
        }
        if jar:
            headers["Cookie"] = cookie_header(jar)
        headers.update(extra or {})
        return request(self.gate, 4180, method, path, headers)

    def start(self, path, extra=None):
        """A request with no session. The result is the login URL and the CSRF cookie."""
        status, headers, _ = self.gate_request("GET", path, extra=extra)
        jar = {name: value for name, value, _ in set_cookies(headers)}
        return status, header(headers, "Location"), jar, set_cookies(headers)

    def login(self, persona, path="/docs/page?x=1"):
        """The whole login of one persona. The result is the answer of the callback."""
        status, location, jar, _ = self.start(path)
        if status != 302:
            return status, [], b""
        back = self.issuer_login(location, persona)
        return self.gate_request("GET", back.path + "?" + back.query, jar)

    @staticmethod
    def issuer_login(location, persona):
        """The login of one persona at the stub issuer. The result is the callback URL."""
        issuer = urllib.parse.urlsplit(location)
        _, headers, _ = request(issuer.hostname, 443, "GET", issuer.path + "?" + issuer.query + "&persona=" + persona, tls=True)
        return urllib.parse.urlsplit(header(headers, "Location"))

    def run(self):
        for path in ("/ready", "/ping"):
            status, _, _ = request(self.gate, 4180, "GET", path)
            report(status == 200, f"the health path {path} answers 200 with no session", f"status {status}")

        # ----- A request with no session -----
        status, location, _, cookies = self.start("/docs/page?x=1")
        query, target = state_target(location)
        authorize = f"{ISSUER_BASE}/{TENANT}/oauth2/v2.0/authorize"
        report(status == 302 and location.split("?")[0] == authorize and ISSUER_BASE == "https://login.microsoftonline.com",
               "a request with no session gets the redirect to the login of the one tenant at login.microsoftonline.com",
               f"status {status}, {location.split('?')[0]}")
        report(query.get("client_id") == CLIENT_ID and query.get("response_type") == "code"
               and query.get("redirect_uri") == f"https://{HOST}/oauth2/callback",
               "the login request has the client id and the redirect URI https://ok.<domain>/oauth2/callback")
        report(query.get("scope") == "openid profile User.Read", "the login request has the scope openid profile User.Read",
               query.get("scope", ""))
        report(query.get("code_challenge_method") == "S256" and bool(query.get("code_challenge")) and bool(query.get("nonce")),
               "the login request has a PKCE challenge (S256) and a nonce")
        report(target == "/docs/page?x=1", "the page after the login is the path of the request", target)
        _, edge_location, _, _ = self.start("/docs/page?x=1", {"X-Forwarded-Uri": "/edge/page?y=2"})
        report(state_target(edge_location)[1] == "/edge/page?y=2",
               "from the edge, the gate reads a forwarded header: X-Forwarded-Uri gives the page after the login",
               state_target(edge_location)[1])
        csrf = [(name, attributes) for name, _, attributes in cookies if name.startswith(COOKIE)]
        report(len(csrf) == 1 and all(safe_flags(attributes) for _, attributes in csrf),
               "the cookie of the login request is Secure, HttpOnly, SameSite=Lax, Path=/, with no Domain",
               str([sorted(attributes) for _, attributes in csrf]))
        status, headers, _ = self.gate_request("GET", "/api/config", extra={"Accept": "application/json"})
        report(status == 401 and not header(headers, "Location"),
               "a JSON request with no session gets 401 and no redirect", f"status {status}")
        status, headers, _ = self.gate_request("POST", "/mcp", extra={"Accept": "application/json", "Content-Length": "0"})
        report(status == 401, "the path /mcp has no exception: 401 with no session", f"status {status}")

        # ----- The member of the group -----
        status, headers, _ = self.login("member")
        session = {name: value for name, value, _ in set_cookies(headers) if name.startswith(COOKIE) and value
                   and not name.endswith("_csrf")}
        flags = [attributes for name, value, attributes in set_cookies(headers) if name in session]
        report(status == 302 and bool(session), "a member of the group in the tenant passes: 302 and a session cookie",
               f"status {status}")
        report(header(headers, "Location") == "/docs/page?x=1",
               "after the login the browser goes back to its page", header(headers, "Location"))
        report(bool(flags) and all(safe_flags(attributes) for attributes in flags),
               "the session cookie is Secure, HttpOnly, SameSite=Lax, Path=/, with no Domain, with the prefix __Host-",
               str([sorted(attributes) for attributes in flags]))
        ages = [int(attributes["max-age"]) for attributes in flags if attributes.get("max-age", "").isdigit()]
        report(bool(ages) and all(abs(age - 12 * 3600) <= 60 for age in ages), "the session cookie ends after 12 hours", str(ages))
        if not session:
            return

        if UPSTREAM == "real":
            status, _, body = self.gate_request("GET", "/api/config", session, {"Accept": "application/json"})
            try:
                collab = json.loads(body).get("collabUrl")
            except ValueError:
                collab = None
            report(status == 200 and collab == f"wss://{HOST}/collab",
                   "with the session, OpenKnowledge answers through the gate and gives the editor wss://ok.<domain>/collab",
                   f"status {status}, {collab}")
            self.websocket(session)
        else:
            skip("OpenKnowledge answers through the gate", "the upstream is the stub")
            self.upstream_headers(session)

        # ----- More than 200 groups: the groups come from Microsoft Graph -----
        status, headers, _ = self.login("overage-member")
        got = [name for name, value, _ in set_cookies(headers) if name.startswith(COOKIE) and value and not name.endswith("_csrf")]
        report(status == 302 and bool(got),
               "a member with more than 200 groups passes: the gate reads the groups from the stub of Microsoft Graph, two pages",
               f"status {status}")

        # ----- A session cookie that is not from the gate -----
        forged = {name: value[:-8] + "AAAAAAAA" for name, value in session.items()}
        status, headers, _ = self.gate_request("GET", "/docs/page", forged)
        report(status == 302 and "/oauth2/v2.0/authorize" in header(headers, "Location"),
               "a changed session cookie does not pass: the redirect to the login", f"status {status}")

        # ----- Each persona that must not pass -----
        for persona, why in (
            ("stranger", "a user of the tenant who is not in the group"),
            ("nogroups", "a token with no group claim"),
            ("othertenant", "a user of another tenant, with the group id in the token"),
            ("wrongaud", "a token for another application"),
            ("badnonce", "a token with a wrong nonce"),
            ("badsig", "a token with a signature of another key"),
            ("expired", "a token after its end time"),
            ("noname", "a token with no preferred_username"),
            ("overage-stranger", "a user with more than 200 groups who is not in the group"),
            ("overage-refused", "a user with more than 200 groups when Microsoft Graph refuses the read"),
        ):
            status, headers, _ = self.login(persona)
            got = [name for name, value, _ in set_cookies(headers) if name.startswith(COOKIE) and value
                   and not name.endswith("_csrf")]
            report(status in (401, 403, 500) and not got, f"does not pass: {why} (status {status})",
                   f"status {status}, session cookie {bool(got)}")

        # ----- The callback of another login (CSRF) -----
        # The cookie of one login request and the code and the state of another one. The gate
        # sends the PKCE verifier of its cookie, so the issuer refuses the code (status 500).
        _, location, jar, _ = self.start("/docs/page")
        _, other_location, _, _ = self.start("/docs/page")
        back = self.issuer_login(other_location, "member")
        status, headers, _ = self.gate_request("GET", back.path + "?" + back.query, jar)
        got = [name for name, value, _ in set_cookies(headers) if name.startswith(COOKIE) and value and not name.endswith("_csrf")]
        report(status in (403, 500) and not got, f"a callback with the code and the state of another login does not pass (status {status})",
               f"status {status}")

        # ----- A redirect to another site -----
        status, headers, _ = self.gate_request("GET", "/oauth2/start?rd=https%3A%2F%2Fevil.example%2Fx")
        _, target = state_target(header(headers, "Location"))
        report(status == 302 and target == "/", "a login with a return page on another site returns to / only", target)

        # ----- Sign out -----
        status, headers, _ = self.gate_request("GET", "/oauth2/sign_out", session)
        cleared = [name for name, value, attributes in set_cookies(headers) if name in session and not value]
        report(status == 302 and sorted(cleared) == sorted(session), "the path /oauth2/sign_out removes the session cookie",
               f"status {status}")

    def websocket(self, session):
        """An upgrade to a WebSocket on the path of the editor, with and without the session."""
        results = {}
        for label, jar in (("with", session), ("without", None)):
            lines = [
                "GET /collab HTTP/1.1", f"Host: {HOST}", "Upgrade: websocket", "Connection: Upgrade",
                "Sec-WebSocket-Version: 13", "Sec-WebSocket-Key: " + base64.b64encode(os.urandom(16)).decode(),
                f"Origin: https://{HOST}", "X-Forwarded-Proto: https", f"X-Forwarded-Host: {HOST}",
                f"X-Forwarded-For: {CLIENT_ADDRESS}",
            ]
            if jar:
                lines.append("Cookie: " + cookie_header(jar))
            with socket.create_connection((self.gate, 4180), timeout=15) as sock:
                sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
                results[label] = sock.recv(64).decode("latin-1").split("\r\n")[0]
        report(" 101 " in results["with"] + " ", "with the session, the WebSocket of the editor opens through the gate (101)",
               results["with"])
        report(" 101 " not in results["without"] + " ", "with no session, the WebSocket does not open", results["without"])

    def upstream_headers(self, session):
        """The headers that the stub upstream gets from the gate."""
        status, _, body = self.gate_request("GET", "/echo", session, {
            "X-Forwarded-User": "forged-user", "X-Forwarded-Email": "forged@evil.example",
            "X-Forwarded-Groups": "forged-group", "X-Forwarded-Preferred-Username": "forged-name",
        })
        try:
            got = json.loads(body)["headers"]
        except (ValueError, KeyError):
            return report(False, "the stub upstream answers through the gate", f"status {status}")
        report(status == 200 and got.get("host") == [HOST], "the upstream gets the Host header of the browser", str(got.get("host")))
        report(got.get("x-forwarded-proto") == ["https"], "the upstream gets X-Forwarded-Proto: https from the edge",
               str(got.get("x-forwarded-proto")))
        report(got.get("x-forwarded-preferred-username") == ["member@local-run.test"]
               and got.get("x-forwarded-email") == ["member@local-run.test"]
               and GROUP in ",".join(got.get("x-forwarded-groups", [])).split(","),
               "the upstream gets the name and the groups of the user from the gate")
        report("forged" not in json.dumps({k: v for k, v in got.items() if k != "cookie"}),
               "a user header from the browser does not reach the upstream")
        report("authorization" not in got and "x-forwarded-access-token" not in got, "no token reaches the upstream")


def neighbour():
    """A client on the network `knowledge`. It is not in the range of the edge."""
    found = addresses("ok-auth")
    if not found:
        sys.exit("probe: ok-auth does not resolve")
    report(all(ipaddress.ip_address(a) not in EDGE_RANGE for a in found), "the client is not in the range of the edge")
    status, headers, _ = request(found[0], 4180, "GET", "/neighbour/page", {
        "Host": HOST, "Accept": "text/html", "User-Agent": "local-run-probe",
        "X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example", "X-Forwarded-Uri": "/stolen",
        "X-Forwarded-For": FORGED_ADDRESS, "X-Real-IP": FORGED_ADDRESS,
    })
    location = header(headers, "Location")
    _, target = state_target(location)
    report(status == 302 and target == "/neighbour/page" and "evil.example" not in location and "stolen" not in location,
           "not from the edge, the gate does not read a forwarded header: X-Forwarded-Uri and X-Forwarded-Host change nothing",
           f"status {status}, {target}")
    status, headers, _ = request(found[0], 4180, "GET", "/docs/page", {
        "Host": HOST, "Accept": "text/html", "X-Forwarded-User": "forged-user", "X-Forwarded-Email": "forged@evil.example"})
    report(status == 302, "not from the edge, a user header gives no session: the redirect to the login", f"status {status}")


def agent():
    """A client on the network `knowledge-agent`, in the place of the agent."""
    report(addresses(HOST) != [] and addresses(HOST) == addresses("open-knowledge"),
           "on the network of the agent, the public name is the address of OpenKnowledge")
    report(addresses("ok-auth") == [], "the agent has no path to the gate: the name ok-auth does not resolve")
    if UPSTREAM != "real":
        status, _, body = request(HOST, 80, "GET", "/echo", {"Accept": "application/json"})
        try:
            got = json.loads(body)["headers"].get("host")
        except (ValueError, KeyError):
            got = None
        report(status == 200 and got == [HOST], "a call to http://ok.<domain>/ has the Host header ok.<domain>, with no port", str(got))
        skip("the MCP endpoint of OpenKnowledge answers the agent", "the upstream is the stub")
        return
    initialize = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "local-run-probe", "version": "0"}}})
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    status, _, body = request(HOST, 80, "POST", "/mcp", headers, initialize)
    report(status == 200 and b"serverInfo" in body,
           "with no login, the MCP endpoint answers the agent at http://ok.<domain>/mcp", f"status {status}")
    status, _, body = request("open-knowledge", 80, "POST", "/mcp", headers, initialize)
    report(status == 403 and b"Host header not allowed" in body,
           "the service name does not work: OpenKnowledge refuses the Host header open-knowledge (403)", f"status {status}")


def outsider():
    """A client on another internal network of the stack."""
    for name in ("open-knowledge", HOST, "ok-auth"):
        report(addresses(name) == [], f"a service of another network has no path: {name.replace(DOMAIN, '<domain>')} does not resolve")


def main():
    role = sys.argv[1] if len(sys.argv) == 2 else ""
    roles = {"edge": lambda: Edge().run(), "neighbour": neighbour, "agent": agent, "outsider": outsider}
    if role not in roles:
        sys.exit("usage: probe.py edge|neighbour|agent|outsider")
    roles[role]()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
