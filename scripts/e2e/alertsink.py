"""A stand-in for Slack, for scripts/e2e/run.sh: accepts alert POSTs and
appends each one's `text` to a file, one per line.

Usage: python3 alertsink.py <port> <file>
"""
import http.server
import json
import sys

PORT, OUT = int(sys.argv[1]), sys.argv[2]


class Sink(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with open(OUT, "a") as f:
            f.write(body["text"].replace("\n", " | ") + "\n")
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", PORT), Sink).serve_forever()
