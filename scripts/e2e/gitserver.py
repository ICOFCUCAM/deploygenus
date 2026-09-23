"""git smart-HTTP over TLS, for scripts/e2e/run.sh.

DeployPro refuses file:// repositories on purpose, so the end-to-end run serves
its test repository the way a real host would: HTTPS, with a certificate the
run trusts through GIT_SSL_CAINFO. Verification stays on.

Usage: python3 gitserver.py <root> <port> <cert.pem> <key.pem>
"""
import http.server, os, ssl, subprocess, sys

ROOT, PORT, CERT, KEY = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]

class H(http.server.BaseHTTPRequestHandler):
    def _run(self):
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        env = dict(os.environ, GIT_PROJECT_ROOT=ROOT, GIT_HTTP_EXPORT_ALL="1",
                   PATH_INFO=path, QUERY_STRING=query, REQUEST_METHOD=self.command,
                   CONTENT_TYPE=self.headers.get("Content-Type", ""),
                   CONTENT_LENGTH=str(len(body)), GIT_PROTOCOL=self.headers.get("Git-Protocol", ""))
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status = 200
        headers = []
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
    do_GET = do_POST = _run
    def log_message(self, *a): pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
