#!/usr/bin/env python3
"""Sign in to a memory stage through Cognito and give one-hour sessions of the person role.

Standard library only. See docs/memory-stack.md, "Sign in with your person role".

Commands (each needs --signin FILE, the sign-in file of `scripts/aws-memory.sh client-config --out`):
  login [--no-browser]   sign in at the hosted page (authorization code with PKCE S256 and a state);
                         a browser and a listener on http://localhost:8400/callback, or a printed URL
                         and a pasted redirect URL; prints the identity ID and the user pool sub
  credential-process     the AWS credential_process contract, Version 1; never asks for input;
                         with no valid sign-in it exits 1 and names `login` on one stderr line
  logout                 revokes the refresh token at Cognito and deletes the cache of the profile
  setup [--config PATH]  writes the profile block into ~/.aws/config; refuses a different block

The cache is ~/.cache/tenant-ec2-memory-signin/<profile>/: a 0700 directory with 0600 files.
Stdout of credential-process belongs to the SDK. No token goes to a terminal.
"""
import argparse
import base64
import datetime as dt
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ElementTree

CALLBACK = "http://localhost:8400/callback"
CALLBACK_PORT = 8400
SCOPES = "openid profile"
IDP_NAME = "Entra"
DURATION = 3600
# Under 900 s the SDK refreshes anyway, so a shorter cached session is not reused.
CACHE_MIN_TTL = 900
MIN_TTL = 120
LOGIN_WAIT = 300
TIMEOUT = 20
FIELDS = ("Version", "AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration")
SIGNIN_FIELDS = {"schemaVersion", "profile", "actorId", "region", "domain", "clientId",
                 "identityPoolId", "providerName", "roleArn"}
# The test seam: one local stub for the token endpoint, Cognito and STS. Only a loopback HTTP address.
TEST_ENDPOINT = "MEMORY_SIGNIN_TEST_ENDPOINT"


class Refused(Exception):
    """A refusal with a fixed text of this helper, never a token or a reply body."""


class SignInNeeded(Refused):
    """No valid refresh token: the person runs login."""


def private_dir(path):
    """Make or check a 0700 directory of this user. A link is refused."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise Refused(f"{path} must be a 0700 directory of this user, not a link")
    return path


def read_private(path, strict=True):
    """Read a JSON file after the owner, mode and link checks. None when the file is absent.

    strict: no group or other bit (a cache file); else no group or other write bit (the sign-in file).
    A parallel write_private can rename a new file over the open one; that file then has no link and is accepted.
    A second link is refused.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise Refused(f"{path} must be a regular file of this user, not a link")
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink > 1
                or info.st_mode & (0o077 if strict else 0o022)):
            raise Refused(f"{path} must be a regular {'0600' if strict else 'private'} file of this user with one link")
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
            raw = stream.read(65537)
    finally:
        os.close(fd)
    if len(raw) > 65536:
        raise Refused(f"{path} is too large")
    try:
        return json.loads(raw)
    except ValueError:
        raise Refused(f"{path} is not JSON")


def write_private(path, data):
    """Write a 0600 file through a temp file of its own, so parallel calls never share one."""
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def load_signin(path):
    path = Path(path).absolute()
    data = read_private(path, strict=False)
    if data is None:
        raise Refused(f"{path} is missing; ask the administrator for the sign-in file")
    if not isinstance(data, dict) or set(data) != SIGNIN_FIELDS or data["schemaVersion"] != 1:
        raise Refused("the sign-in file has other fields than schema version 1")
    if not all(isinstance(data[key], str) for key in SIGNIN_FIELDS - {"schemaVersion"}):
        raise Refused("the sign-in file has a value that is not text")
    region, actor = data["region"], data["actorId"]
    patterns = {
        "region": r"[a-z]{2}(-[a-z]+)+-[0-9]+",
        "actorId": r"[a-z0-9][a-z0-9-]{0,63}",
        "profile": r"[A-Za-z0-9_.-]{1,128}",
        "clientId": r"[A-Za-z0-9]{1,128}",
    }
    for key, pattern in patterns.items():
        if not re.fullmatch(pattern, data[key]):
            raise Refused(f"the sign-in file has an invalid {key}")
    region_text = re.escape(region)
    patterns = {
        # Only the prefix domain of the stack: a changed host would receive the tokens.
        "domain": r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.auth\." + region_text + r"\.amazoncognito\.com",
        "identityPoolId": region_text + r":[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "providerName": r"cognito-idp\." + region_text + r"\.amazonaws\.com/" + region_text + r"_[A-Za-z0-9]{1,64}",
        "roleArn": r"arn:aws[a-z-]*:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_-]{1,64}",
    }
    for key, pattern in patterns.items():
        if not re.fullmatch(pattern, data[key]):
            raise Refused(f"the sign-in file has an invalid {key}")
    if not data["roleArn"].endswith(f"-person-{actor}-role") or not data["profile"].endswith(f"-person-{actor}"):
        raise Refused("the role and the profile of the sign-in file must name its actorId")
    data["path"] = path
    return data


def cache_dir(signin, create=True):
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tenant-ec2-memory-signin"
    path = base / signin["profile"]
    if create:
        if not base.parent.exists():
            private_dir(base.parent)
        private_dir(base)
        private_dir(path)
    return path


def endpoints(signin):
    test = os.environ.get(TEST_ENDPOINT)
    if test:
        if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", test):
            raise Refused(f"{TEST_ENDPOINT} accepts only http://127.0.0.1:<port>")
        return test + "/oauth2", test + "/cognito-identity/", test + "/sts/"
    region = signin["region"]
    return (f"https://{signin['domain']}/oauth2", f"https://cognito-identity.{region}.amazonaws.com/",
            f"https://sts.{region}.amazonaws.com/")


def post(url, body, headers):
    """POST and return (status, body bytes). A network failure is a refusal with no detail."""
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(1 << 20)
    except urllib.error.HTTPError as error:
        return error.code, error.read(1 << 16)
    except (urllib.error.URLError, OSError):
        raise Refused(f"cannot reach {urllib.parse.urlsplit(url).netloc}")


def token_call(signin, form):
    url = endpoints(signin)[0] + "/token"
    status, body = post(url, urllib.parse.urlencode(form).encode(),
                        {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        reply = json.loads(body)
    except ValueError:
        reply = {}
    if status != 200 or not isinstance(reply, dict) or not isinstance(reply.get("id_token"), str):
        error = reply.get("error") if isinstance(reply, dict) else None
        if form["grant_type"] == "refresh_token" and status == 400:
            raise SignInNeeded("the sign-in expired or was revoked")
        raise Refused(f"the token endpoint answered HTTP {status}: {error if isinstance(error, str) else 'no error code'}"[:200])
    return reply


def cognito_call(signin, target, payload):
    status, body = post(endpoints(signin)[1], json.dumps(payload).encode(),
                        {"Content-Type": "application/x-amz-json-1.1",
                         "X-Amz-Target": "AWSCognitoIdentityService." + target})
    try:
        reply = json.loads(body)
    except ValueError:
        reply = {}
    if status != 200:
        kind = str(reply.get("__type", "no error code")).split("#")[-1] if isinstance(reply, dict) else "no error code"
        if kind == "NotAuthorizedException":
            raise SignInNeeded("Cognito refused the sign-in")
        raise Refused(f"Cognito {target} answered HTTP {status}: {kind}"[:200])
    return reply


def assume_role(signin, open_id_token):
    form = {"Action": "AssumeRoleWithWebIdentity", "Version": "2011-06-15", "RoleArn": signin["roleArn"],
            "RoleSessionName": signin["actorId"], "WebIdentityToken": open_id_token,
            "DurationSeconds": str(DURATION)}
    status, body = post(endpoints(signin)[2], urllib.parse.urlencode(form).encode(),
                        {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise Refused(f"STS answered HTTP {status} with no readable body")
    def find(name):
        element = next((e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == name), None)
        return element.text if element is not None and element.text else ""
    if status != 200:
        raise Refused(f"STS AssumeRoleWithWebIdentity answered HTTP {status}: {find('Code') or 'no error code'}"[:200])
    return validate({"Version": 1, "AccessKeyId": find("AccessKeyId"), "SecretAccessKey": find("SecretAccessKey"),
                     "SessionToken": find("SessionToken"), "Expiration": find("Expiration")})


def validate(data, min_ttl=MIN_TTL):
    """The output contract of config/macos/credential-process.py."""
    if not isinstance(data, dict) or set(data) != set(FIELDS) or type(data["Version"]) is not int or data["Version"] != 1:
        raise ValueError("credential-invalid")
    for key, pattern in (("AccessKeyId", r"ASIA[A-Z0-9]{12,124}"),
                         ("SecretAccessKey", r"[A-Za-z0-9/+=]{40}"),
                         ("SessionToken", r"[A-Za-z0-9/+=_-]{16,16384}")):
        if not isinstance(data[key], str) or not re.fullmatch(pattern, data[key]):
            raise ValueError("credential-invalid")
    if not isinstance(data["Expiration"], str) or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)", data["Expiration"]):
        raise ValueError("credential-invalid")
    expiry = dt.datetime.fromisoformat(data["Expiration"].replace("Z", "+00:00"))
    ttl = (expiry - dt.datetime.now(dt.timezone.utc)).total_seconds()
    if not min_ttl < ttl <= 86400:
        raise ValueError("credential-expired")
    return {key: data[key] for key in FIELDS}


def claims(token):
    """The payload of a JWT. No signature check: the token comes from the token endpoint over TLS."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise Refused("the ID token does not decode")
    if not isinstance(data, dict):
        raise Refused("the ID token does not decode")
    return data


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def redirect_code(url, state):
    """The code of a redirect URL. The state is checked first; a wrong state stops before the exchange."""
    parsed = urllib.parse.urlsplit(url.strip())
    if f"{parsed.scheme}://{parsed.netloc}{parsed.path}" != CALLBACK:
        raise Refused(f"the redirect URL must start with {CALLBACK}")
    params = urllib.parse.parse_qs(parsed.query)
    if params.get("state", [""])[0] != state:
        raise Refused("the state of the redirect does not match this login; nothing is exchanged")
    if "error" in params:
        raise Refused(f"the sign-in failed: {params['error'][0]}"[:200])
    code = params.get("code", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9._~-]{1,512}", code):
        raise Refused("the redirect URL has no valid code")
    return code


def has_browser():
    return sys.platform == "darwin" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def listen_for_code(server, state):
    """Wait for one redirect with the right state on the localhost listener."""
    found = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            # A request with another path or state is not the redirect of this login.
            if parsed.path != "/callback" or params.get("state", [""])[0] != state:
                self.send_error(400)
                return
            try:
                found["code"] = redirect_code(CALLBACK + "?" + parsed.query, state)
                text = b"Sign-in complete. Close this tab and return to the terminal.\n"
            except Refused as refusal:
                found["error"] = str(refusal)
                text = b"Sign-in failed. Read the terminal.\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(text)

        def log_message(self, *_args):
            pass

    server.RequestHandlerClass = Handler
    deadline = time.monotonic() + LOGIN_WAIT
    while not found and time.monotonic() < deadline:
        server.timeout = max(1, deadline - time.monotonic())
        server.handle_request()
    if "error" in found:
        raise Refused(found["error"])
    if "code" not in found:
        raise Refused(f"no redirect came in {LOGIN_WAIT} s; run login again, or login --no-browser")
    return found["code"]


def cmd_login(signin, args):
    verifier = b64url(secrets.token_bytes(64))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    state = b64url(secrets.token_bytes(24))
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": signin["clientId"], "redirect_uri": CALLBACK, "scope": SCOPES,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "identity_provider": IDP_NAME}, quote_via=urllib.parse.quote)
    url = f"https://{signin['domain']}/oauth2/authorize?{query}"
    directory = cache_dir(signin)
    if args.no_browser or not has_browser():
        print("Open this URL in a browser on any machine and sign in:", file=sys.stderr)
        print(url, flush=True)
        print("The browser then shows an error page at localhost:8400. That is expected.", file=sys.stderr)
        print("Copy the full address of that page from the address bar and paste it here:", file=sys.stderr)
        line = sys.stdin.readline()
        if not line.strip():
            raise Refused("no redirect URL was pasted")
        code = redirect_code(line, state)
    else:
        try:
            server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), http.server.BaseHTTPRequestHandler)
        except OSError:
            raise Refused(f"port {CALLBACK_PORT} is in use; run login --no-browser")
        with server:
            print("Sign in in the browser. If no browser opens, open this URL:", file=sys.stderr)
            print(url, flush=True)
            webbrowser.open(url)
            code = listen_for_code(server, state)
    tokens = token_call(signin, {"grant_type": "authorization_code", "client_id": signin["clientId"],
                                 "code": code, "redirect_uri": CALLBACK, "code_verifier": verifier})
    if not isinstance(tokens.get("refresh_token"), str):
        raise Refused("the token response has no refresh token")
    sub = claims(tokens["id_token"]).get("sub")
    identity = cognito_call(signin, "GetId", {"IdentityPoolId": signin["identityPoolId"],
                                              "Logins": {signin["providerName"]: tokens["id_token"]}}).get("IdentityId")
    if not isinstance(identity, str) or not re.fullmatch(re.escape(signin["region"]) + r":[0-9a-f-]{36}", identity):
        raise Refused("Cognito GetId gave no valid identity ID")
    for name in ("session.json",):
        if os.path.lexists(directory / name):
            os.unlink(directory / name)
    write_private(directory / "token.json", {"refreshToken": tokens["refresh_token"], "identityId": identity,
                                             "obtained": int(time.time())})
    print("Signed in. Give these two lines to the administrator for the binding:", file=sys.stderr)
    print(f"identity-id: {identity}")
    print(f"user-pool-sub: {sub}")
    return 0


def cmd_credential_process(signin, _args):
    if sys.stdout.isatty():
        raise Refused("credential-process writes credentials only to a pipe of the SDK")
    directory = cache_dir(signin)
    session = read_private(directory / "session.json")
    if session is not None:
        try:
            sys.stdout.write(json.dumps(validate(session, CACHE_MIN_TTL), separators=(",", ":")))
            return 0
        except (ValueError, TypeError, KeyError):
            pass
    token = read_private(directory / "token.json")
    if not isinstance(token, dict) or not isinstance(token.get("refreshToken"), str) \
            or not isinstance(token.get("identityId"), str):
        raise SignInNeeded("no sign-in")
    tokens = token_call(signin, {"grant_type": "refresh_token", "client_id": signin["clientId"],
                                 "refresh_token": token["refreshToken"]})
    logins = {signin["providerName"]: tokens["id_token"]}
    open_id = cognito_call(signin, "GetOpenIdToken", {"IdentityId": token["identityId"], "Logins": logins})
    if open_id.get("IdentityId") != token["identityId"] or not isinstance(open_id.get("Token"), str):
        raise SignInNeeded("Cognito gave a token of another identity")
    try:
        credentials = assume_role(signin, open_id["Token"])
    except ValueError:
        raise Refused("STS gave credentials that do not pass the checks")
    if isinstance(tokens.get("refresh_token"), str):
        token["refreshToken"] = tokens["refresh_token"]
        write_private(directory / "token.json", token)
    write_private(directory / "session.json", credentials)
    sys.stdout.write(json.dumps(credentials, separators=(",", ":")))
    return 0


def cmd_logout(signin, _args):
    directory = cache_dir(signin, create=False)
    if not directory.exists():
        print("No sign-in is cached for this profile.", file=sys.stderr)
        return 0
    private_dir(directory)
    session = directory / "session.json"
    if os.path.lexists(session):
        os.unlink(session)
    token = read_private(directory / "token.json")
    if isinstance(token, dict) and isinstance(token.get("refreshToken"), str):
        status, body = post(endpoints(signin)[0] + "/revoke",
                            urllib.parse.urlencode({"token": token["refreshToken"],
                                                    "client_id": signin["clientId"]}).encode(),
                            {"Content-Type": "application/x-www-form-urlencoded"})
        try:
            reply = json.loads(body)
        except ValueError:
            reply = {}
        error = reply.get("error") if isinstance(reply, dict) else None
        if status == 200:
            print("The refresh token is revoked at Cognito.", file=sys.stderr)
        elif status == 400 and error in ("invalid_token", "invalid_grant"):
            print(f"The revoke answered HTTP 400 {error}: the token was not valid. The cache goes.", file=sys.stderr)
        else:
            # Without the local copy, a token that is still valid could not be revoked any more.
            raise Refused(f"the revoke answered HTTP {status}; the token stays cached; run logout again")
    for entry in os.listdir(directory):
        if entry == "token.json" or entry.endswith(".tmp"):
            os.unlink(directory / entry)
    os.rmdir(directory)
    print("The cache of this profile is deleted.", file=sys.stderr)
    return 0


def profile_block(signin):
    helper, signin_path = Path(__file__).resolve(), signin["path"]
    words = (sys.executable, str(helper), "credential-process", "--signin", str(signin_path))
    if any(not re.fullmatch(r"[A-Za-z0-9_./@+-]+", word) for word in words):
        raise Refused("the paths of python3, the helper and the sign-in file need no blank or quote")
    return [f"[profile {signin['profile']}]", f"region = {signin['region']}",
            "credential_process = " + " ".join(words)]


def cmd_setup(signin, args):
    path = Path(args.config or os.environ.get("AWS_CONFIG_FILE") or Path.home() / ".aws" / "config")
    block = profile_block(signin)
    if not path.parent.exists():
        os.mkdir(path.parent, 0o700)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    if block[0] in (line.strip() for line in lines):
        start = [line.strip() for line in lines].index(block[0])
        existing = []
        for line in lines[start:]:
            if existing and line.strip().startswith("["):
                break
            if line.strip():
                existing.append(re.sub(r"\s*=\s*", " = ", line.strip(), count=1))
        if existing == block:
            print(f"The profile {signin['profile']} is already set up in {path}.", file=sys.stderr)
            return 0
        raise Refused(f"{path} has a different block for {signin['profile']}; remove it first, nothing is written")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(("\n" if text and not text.endswith("\n") else "") + ("\n" if text else "") + "\n".join(block) + "\n")
    print(f"The profile {signin['profile']} is written to {path}.", file=sys.stderr)
    return 0


COMMANDS = {"login": cmd_login, "credential-process": cmd_credential_process,
            "logout": cmd_logout, "setup": cmd_setup}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        sub = commands.add_parser(name)
        sub.add_argument("--signin", required=True, help="the sign-in file <profile>.signin.json")
        if name == "login":
            sub.add_argument("--no-browser", action="store_true", help="print the URL and read a pasted redirect")
        if name == "setup":
            sub.add_argument("--config", help="the AWS config file (default ~/.aws/config)")
    args = parser.parse_args(argv)
    os.umask(0o077)
    login_hint = f"{Path(__file__).resolve()} login --signin {Path(args.signin).absolute()}"
    try:
        return COMMANDS[args.command](load_signin(args.signin), args)
    except SignInNeeded as refusal:
        print(f"memory-signin: {refusal}; sign in with: {login_hint}", file=sys.stderr)
    except Refused as refusal:
        print(f"memory-signin: {refusal}", file=sys.stderr)
    except OSError as error:
        print(f"memory-signin: a file operation failed: {error.strerror or type(error).__name__}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
