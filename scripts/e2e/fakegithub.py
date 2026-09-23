"""A stand-in for GitHub, for scripts/e2e/run.sh: the parts of the REST API
the GitHub App uses, and git over HTTPS for one private repository.

It checks what GitHub checks. App endpoints need a JWT signed with the key
it handed out when the app was "created"; repository endpoints need an
installation token it issued; and git needs such a token too, sent the way
GitHub expects (Basic, user x-access-token), with the repository inside the
token's scope. So a clone that works here is one that authenticates the way
it would against github.com.

Usage: python3 fakegithub.py <git root> <port> <cert.pem> <key.pem> <secret file>

The app's webhook secret is written to <secret file> once the app exists, so
the run can sign push events as GitHub would.
"""

import base64
import http.server
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT, PORT, CERT, KEY, SECRET_FILE = sys.argv[1:6]
PORT = int(PORT)
BASE = f"https://127.0.0.1:{PORT}"
OWNER, REPO, INSTALLATION = "e2e-owner", "private-app", 77

STATE = {"key": None, "tokens": {}}


def b64url_decode(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def jwt_ok(header):
    if not header.startswith("Bearer ") or STATE["key"] is None:
        return False
    try:
        head, payload, signature = header[7:].split(".")
        STATE["key"].public_key().verify(
            b64url_decode(signature),
            f"{head}.{payload}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        claims = json.loads(b64url_decode(payload))
    except Exception:
        return False
    return claims.get("iss") == "1" and claims.get("exp", 0) > time.time()


def token_scope(header):
    """The repositories an installation token may read, or None."""
    if header.startswith("Bearer ") or header.startswith("token "):
        return STATE["tokens"].get(header.split(" ", 1)[1])
    if header.startswith("Basic "):
        user, _, token = base64.b64decode(header[6:]).decode().partition(":")
        if user == "x-access-token":
            return STATE["tokens"].get(token)
    return None


REPO_JSON = {
    "full_name": f"{OWNER}/{REPO}",
    "private": True,
    "default_branch": "main",
    "pushed_at": "2026-09-23T10:00:00Z",
    "html_url": f"{BASE}/{OWNER}/{REPO}",
}
INSTALLATION_JSON = {"id": INSTALLATION, "account": {"login": OWNER, "type": "User"}}


class H(http.server.BaseHTTPRequestHandler):
    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def handle_any(self):
        path = self.path.partition("?")[0]
        auth = self.headers.get("Authorization", "")
        if path.startswith("/api/"):
            return self.api(path[4:], auth)
        if path.startswith(f"/{OWNER}/{REPO}.git/"):
            scope = token_scope(auth)
            if scope is None or (scope != "all" and REPO not in scope):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="GitHub"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self.git(path[len(f"/{OWNER}/{REPO}.git") :])
        self.reply(404, {"message": "Not Found"})

    def api(self, path, auth):
        m = re.fullmatch(r"/app-manifests/([^/]+)/conversions", path)
        if m and self.command == "POST":
            if m.group(1) != "good-code" or STATE["key"] is not None:
                return self.reply(404, {"message": "Not Found"})
            STATE["key"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = STATE["key"].private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ).decode()
            secret = secrets.token_hex(20)
            with open(SECRET_FILE, "w") as f:
                f.write(secret)
            return self.reply(
                201,
                {
                    "id": 1,
                    "slug": "deploypro-e2e",
                    "name": "DeployPro e2e",
                    "owner": {"login": OWNER},
                    "html_url": f"{BASE}/apps/deploypro-e2e",
                    "pem": pem,
                    "webhook_secret": secret,
                },
            )

        m = re.fullmatch(r"/app/installations/(\d+)", path)
        if m:
            if not jwt_ok(auth):
                return self.reply(401, {"message": "A JSON web token could not be decoded"})
            if int(m.group(1)) != INSTALLATION:
                return self.reply(404, {"message": "Not Found"})
            return self.reply(200, INSTALLATION_JSON)

        m = re.fullmatch(r"/app/installations/(\d+)/access_tokens", path)
        if m and self.command == "POST":
            if not jwt_ok(auth) or int(m.group(1)) != INSTALLATION:
                return self.reply(401, {"message": "Bad credentials"})
            raw = self.body()
            requested = (json.loads(raw) if raw else {}).get("repositories")
            token = "ghs_e2e" + secrets.token_hex(16)
            STATE["tokens"][token] = requested or "all"
            expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
            return self.reply(201, {"token": token, "expires_at": expires})

        m = re.fullmatch(r"/repos/([^/]+)/([^/]+)/installation", path)
        if m:
            if not jwt_ok(auth):
                return self.reply(401, {"message": "Bad credentials"})
            if (m.group(1), m.group(2)) != (OWNER, REPO):
                return self.reply(404, {"message": "Not Found"})
            return self.reply(200, INSTALLATION_JSON)

        if path == "/installation/repositories":
            if token_scope(auth) is None:
                return self.reply(401, {"message": "Bad credentials"})
            return self.reply(200, {"total_count": 1, "repositories": [REPO_JSON]})

        if path == f"/repos/{OWNER}/{REPO}":
            if token_scope(auth) is None:
                return self.reply(401, {"message": "Bad credentials"})
            return self.reply(200, REPO_JSON)

        self.reply(404, {"message": "Not Found"})

    def git(self, rest):
        query = self.path.partition("?")[2]
        body = self.body()
        env = dict(
            os.environ,
            GIT_PROJECT_ROOT=ROOT,
            GIT_HTTP_EXPORT_ALL="1",
            PATH_INFO="/app.git" + rest,
            QUERY_STRING=query,
            REQUEST_METHOD=self.command,
            CONTENT_TYPE=self.headers.get("Content-Type", ""),
            CONTENT_LENGTH=str(len(body)),
            GIT_PROTOCOL=self.headers.get("Git-Protocol", ""),
        )
        out = subprocess.run(
            ["git", "http-backend"], input=body, env=env, capture_output=True
        ).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status, headers = 200, []
        for line in head.decode().split("\r\n"):
            k, _, v = line.partition(": ")
            if k.lower() == "status":
                status = int(v.split()[0])
            elif k:
                headers.append((k, v))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = handle_any

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
