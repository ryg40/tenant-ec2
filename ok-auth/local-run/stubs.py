"""The two stubs of the local run of the gate (ok-auth/local-run.sh).

    python stubs.py issuer     a stub in the place of Entra ID and of Microsoft Graph
    python stubs.py upstream   a stub upstream that returns the headers that it gets

Only the standard library, and the command openssl for one certificate. The stubs run
on Compose networks with no route to the internet. They hold no real credential: the
local run makes each value new.

The issuer has the form of one Entra ID tenant. oauth2-proxy with the provider
`entra-id` accepts an ID token only from an issuer of the form
https://login.microsoftonline.com/<tenant>/v2.0. So the stub answers that name with
HTTPS: on the one network of the run it has the alias, and it makes a certificate at each
start. The gate of the run trusts only that certificate. Nothing of this leaves the run.

The issuer makes a new RSA key at each start and signs each ID token with it (RS256).
It checks the login request and the token request of the gate, as Entra ID does:
the client id, the redirect URI, the client secret and the PKCE code verifier.
The probe selects a persona with the query parameter `persona` of the login request.
A persona says which claims the ID token has (function `claims_of`) and which groups
Microsoft Graph gives (function `graph_groups`).
"""

import base64
import hashlib
import json
import math
import os
import secrets
import ssl
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SMALL_PRIMES = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p**0.5) + 1, 2))]
# The DER prefix of a SHA-256 digest in a PKCS #1 v1.5 signature (RFC 8017, section 9.2).
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def probable_prime(n: int) -> bool:
    if any(n % p == 0 for p in SMALL_PRIMES):
        return False
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(24):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def prime(bits: int) -> int:
    while True:
        n = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if probable_prime(n):
            return n


class Key:
    """An RSA key of 2048 bits for the test tokens."""

    def __init__(self, kid: str):
        self.kid = kid
        self.e = 65537
        while True:
            p, q = prime(1024), prime(1024)
            phi = (p - 1) * (q - 1)
            if p != q and math.gcd(self.e, phi) == 1:
                break
        self.n = p * q
        self.d = pow(self.e, -1, phi)

    def jwk(self) -> dict:
        size = (self.n.bit_length() + 7) // 8
        return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid,
                "n": b64url(self.n.to_bytes(size, "big")), "e": b64url(self.e.to_bytes(3, "big"))}

    def jwt(self, claims: dict) -> str:
        header = {"alg": "RS256", "typ": "JWT", "kid": self.kid}
        text = b64url(json.dumps(header).encode()) + "." + b64url(json.dumps(claims).encode())
        size = (self.n.bit_length() + 7) // 8
        digest = hashlib.sha256(text.encode()).digest()
        block = b"\x00\x01" + b"\xff" * (size - len(SHA256_PREFIX) - len(digest) - 3) + b"\x00" + SHA256_PREFIX + digest
        signature = pow(int.from_bytes(block, "big"), self.d, self.n).to_bytes(size, "big")
        return text + "." + b64url(signature)


def need(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"stubs: the environment has no {name}")
    return value


class Issuer:
    def __init__(self):
        self.tenant = need("STUB_TENANT")
        self.other_tenant = need("STUB_OTHER_TENANT")
        self.client_id = need("STUB_CLIENT_ID")
        self.client_secret = need("STUB_CLIENT_SECRET")
        self.redirect_uri = need("STUB_REDIRECT_URI")
        self.group = need("STUB_GROUP")
        self.other_group = need("STUB_OTHER_GROUP")
        self.base = need("STUB_BASE_URL").rstrip("/")
        self.issuer = f"{self.base}/{self.tenant}/v2.0"
        self.key = Key("local-run-1")
        # A key that the issuer does not publish: the persona `badsig` gets it.
        self.unknown_key = Key("local-run-1")
        self.codes = {}

    def discovery(self) -> dict:
        root = f"{self.base}/{self.tenant}"
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{root}/oauth2/v2.0/authorize",
            "token_endpoint": f"{root}/oauth2/v2.0/token",
            "jwks_uri": f"{root}/discovery/v2.0/keys",
            "response_types_supported": ["code"],
            "subject_types_supported": ["pairwise"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "profile", "email", "offline_access"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        }

    def claims_of(self, persona: str, nonce: str) -> dict:
        """The claims of the ID token of one persona. `member` is the one that must pass."""
        now = int(time.time())
        claims = {
            "iss": self.issuer, "aud": self.client_id, "tid": self.tenant, "ver": "2.0",
            "sub": f"sub-{persona}", "oid": f"00000000-0000-4000-8000-{abs(hash(persona)) % 10**12:012d}",
            "name": f"Persona {persona}", "preferred_username": f"{persona}@local-run.test",
            "nonce": nonce, "iat": now, "nbf": now, "exp": now + 600,
            "groups": [self.other_group, self.group],
        }
        if persona == "member":
            pass
        elif persona == "stranger":
            # A user of the tenant who is not in the group.
            claims["groups"] = [self.other_group]
        elif persona == "nogroups":
            del claims["groups"]
        elif persona == "othertenant":
            # A user of another tenant, with the id of the group in the token.
            claims["iss"] = f"{self.base}/{self.other_tenant}/v2.0"
            claims["tid"] = self.other_tenant
        elif persona == "wrongaud":
            claims["aud"] = "11111111-1111-4111-8111-111111111111"
        elif persona == "badnonce":
            claims["nonce"] = "not-the-nonce-of-the-request"
        elif persona == "expired":
            claims.update({"iat": now - 7200, "nbf": now - 7200, "exp": now - 3600})
        elif persona == "noname":
            del claims["preferred_username"]
        elif persona in ("overage-member", "overage-stranger", "overage-refused"):
            # More than 200 groups: Entra ID sends a pointer to Microsoft Graph, not the list.
            del claims["groups"]
            claims["_claim_names"] = {"groups": "src1"}
            claims["_claim_sources"] = {"src1": {"endpoint": "https://graph.microsoft.com/v1.0/users/x/getMemberObjects"}}
        elif persona != "badsig":
            raise KeyError(persona)
        return claims

    def graph_groups(self, persona: str, page: str):
        """One page of the groups that Microsoft Graph gives for a persona, or None for a refusal."""
        if persona == "overage-member":
            # Two pages. The group of the gate is on the second page.
            if page != "2":
                more = "https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$select=id&$top=100&page=2"
                return {"value": [{"id": self.other_group}], "@odata.nextLink": more}
            return {"value": [{"id": self.group}]}
        if persona == "overage-stranger":
            return {"value": [{"id": self.other_group}]}
        return None


def make_issuer_handler(issuer: Issuer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("issuer: " + (fmt % args) + "\n")

        def send(self, status: int, body, headers=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def refuse(self, reason: str):
            self.send(400, {"error": "invalid_request", "error_description": reason})

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            root = f"/{issuer.tenant}"
            if url.path == "/healthz":
                self.send(200, {"ok": True})
            elif url.path == f"{root}/v2.0/.well-known/openid-configuration":
                self.send(200, issuer.discovery())
            elif url.path == f"{root}/discovery/v2.0/keys":
                self.send(200, {"keys": [issuer.key.jwk()]})
            elif url.path == f"{root}/oauth2/v2.0/authorize":
                self.authorize(dict(urllib.parse.parse_qsl(url.query)))
            elif url.path == "/v1.0/me/transitiveMemberOf":
                # Microsoft Graph. The access token of the stub names the persona.
                bearer = self.headers.get("Authorization", "")
                page = dict(urllib.parse.parse_qsl(url.query)).get("page", "1")
                groups = issuer.graph_groups(bearer.removeprefix("Bearer stub-access-"), page)
                if groups is None:
                    self.send(403, {"error": {"code": "Authorization_RequestDenied", "message": "no right to read the groups"}})
                else:
                    self.send(200, groups)
            else:
                self.send(404, {"error": "not_found"})

        def authorize(self, q: dict):
            if q.get("client_id") != issuer.client_id:
                return self.refuse("the client id is not the id of the app registration")
            if q.get("redirect_uri") != issuer.redirect_uri:
                return self.refuse("the redirect URI is not the URI of the app registration")
            if q.get("response_type") != "code":
                return self.refuse("the response type is not code")
            if "openid" not in q.get("scope", "").split():
                return self.refuse("the scope has no openid")
            if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
                return self.refuse("the request has no PKCE challenge with S256")
            if not q.get("nonce") or not q.get("state"):
                return self.refuse("the request has no nonce or no state")
            persona = q.get("persona", "")
            try:
                issuer.claims_of(persona, "")
            except KeyError:
                return self.refuse("the probe must name a persona")
            code = secrets.token_urlsafe(24)
            issuer.codes[code] = {"persona": persona, "nonce": q["nonce"], "challenge": q["code_challenge"]}
            back = issuer.redirect_uri + "?" + urllib.parse.urlencode({"code": code, "state": q["state"]})
            self.send(302, {"location": back}, {"Location": back})

        def do_POST(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path != f"/{issuer.tenant}/oauth2/v2.0/token":
                return self.send(404, {"error": "not_found"})
            form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()))
            client_id, client_secret = form.get("client_id"), form.get("client_secret")
            basic = self.headers.get("Authorization", "")
            if basic.startswith("Basic "):
                pair = base64.b64decode(basic[6:]).decode().split(":", 1)
                client_id, client_secret = (urllib.parse.unquote_plus(part) for part in pair)
            if client_id != issuer.client_id or client_secret != issuer.client_secret:
                return self.send(401, {"error": "invalid_client"})
            entry = issuer.codes.pop(form.get("code", ""), None)
            if entry is None or form.get("grant_type") != "authorization_code":
                return self.send(400, {"error": "invalid_grant", "error_description": "the code is not known"})
            if form.get("redirect_uri") != issuer.redirect_uri:
                return self.send(400, {"error": "invalid_grant", "error_description": "the redirect URI is not equal"})
            verifier = form.get("code_verifier", "")
            if b64url(hashlib.sha256(verifier.encode()).digest()) != entry["challenge"]:
                return self.send(400, {"error": "invalid_grant", "error_description": "the PKCE verifier is not correct"})
            persona = entry["persona"]
            key = issuer.unknown_key if persona == "badsig" else issuer.key
            self.send(200, {
                "token_type": "Bearer", "scope": "openid profile User.Read", "expires_in": 3600,
                "access_token": "stub-access-" + persona,
                "id_token": key.jwt(issuer.claims_of(persona, entry["nonce"])),
            }, {"Cache-Control": "no-store"})

    return Handler


class UpstreamHandler(BaseHTTPRequestHandler):
    """Return the request line and the headers of each request as JSON."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def answer(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length:
            self.rfile.read(length)
        headers = {}
        for name, value in self.headers.items():
            headers.setdefault(name.lower(), []).append(value)
        data = json.dumps({"stub": "upstream", "method": self.command, "path": self.path, "headers": headers}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = answer


def certificate(directory: str) -> ssl.SSLContext:
    """Make one certificate for the two names of the stub. The gate gets the file ca.pem."""
    key, cert = os.path.join(directory, "key.pem"), os.path.join(directory, "ca.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
         "-keyout", key, "-out", cert + ".new", "-subj", "/CN=local run of the gate",
         "-addext", "subjectAltName=DNS:login.microsoftonline.com,DNS:graph.microsoft.com"],
        check=True, capture_output=True)
    os.chmod(key, 0o600)
    os.chmod(cert + ".new", 0o644)
    os.replace(cert + ".new", cert)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "issuer":
        issuer = Issuer()
        server = ThreadingHTTPServer(("0.0.0.0", 443), make_issuer_handler(issuer))
        server.socket = certificate(need("STUB_TLS_DIR")).wrap_socket(server.socket, server_side=True)
    elif mode == "upstream":
        # Port 80 as a user that is not root, as the OpenKnowledge image.
        server = ThreadingHTTPServer(("0.0.0.0", 80), UpstreamHandler)
    else:
        sys.exit("usage: stubs.py issuer|upstream")
    sys.stderr.write(f"stubs: the stub {mode} listens\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
