#!/usr/bin/env python3
"""Bridge an MCP server that speaks the HTTP+SSE transport to a stdio MCP client (the Codex CLI).

Codex only knows streamable HTTP for remote servers: `mcp_servers.<name>.url` POSTs JSON-RPC to the URL, which an
SSE endpoint answers with 405, so SREGym's Prometheus, Loki and Jaeger mounts never attached and the agent wrote its
own client in the shell (every Codex attempt of the no-kubectl arm on 21 November did that). Codex does know stdio
servers, so this script is run as `mcp_servers.<name>.command` with the SSE URL as its argument:

  python3 sse_stdio_bridge.py http://host.docker.internal:9954/prometheus/sse

It opens the SSE stream, learns the message endpoint from the `endpoint` event, POSTs every JSON-RPC message read
from stdin to that endpoint, and writes every `message` event from the stream to stdout, one JSON object per line.
Standard library only, so it needs nothing installed in the agent image.
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
import urllib.parse

READ_TIMEOUT_S = 600


def log(msg: str) -> None:
    sys.stderr.write(f"sse-bridge: {msg}\n")
    sys.stderr.flush()


class Bridge:
    def __init__(self, sse_url: str):
        self.sse_url = sse_url
        self.parts = urllib.parse.urlsplit(sse_url)
        self.endpoint: str | None = None
        self.endpoint_ready = threading.Event()
        self.out_lock = threading.Lock()
        self.stream_conn: http.client.HTTPConnection | None = None

    def _conn(self) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self.parts.scheme == "https" else http.client.HTTPConnection
        return cls(self.parts.hostname, self.parts.port, timeout=READ_TIMEOUT_S)

    def emit(self, text: str) -> None:
        with self.out_lock:
            sys.stdout.write(text.strip() + "\n")
            sys.stdout.flush()

    def read_stream(self) -> None:
        conn = self._conn()
        self.stream_conn = conn
        path = self.parts.path + (f"?{self.parts.query}" if self.parts.query else "")
        conn.request("GET", path, headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"})
        resp = conn.getresponse()
        if resp.status != 200:
            log(f"SSE connect failed: HTTP {resp.status}")
            self.endpoint_ready.set()
            return
        event, data = "message", []
        while True:
            raw = resp.readline()
            if not raw:
                log("SSE stream closed by server")
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if data:
                    payload = "\n".join(data)
                    if event == "endpoint":
                        self.endpoint = urllib.parse.urljoin(self.sse_url, payload.strip())
                        log(f"message endpoint {self.endpoint}")
                        self.endpoint_ready.set()
                    elif event == "message":
                        self.emit(payload)
                event, data = "message", []
                continue
            if line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key == "event":
                event = value
            elif key == "data":
                data.append(value)
        self.endpoint_ready.set()

    def post(self, body: str) -> None:
        assert self.endpoint
        ep = urllib.parse.urlsplit(self.endpoint)
        conn = self._conn()
        path = ep.path + (f"?{ep.query}" if ep.query else "")
        conn.request("POST", path, body=body.encode(), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        if resp.status >= 300:
            log(f"POST {resp.status} for {body[:120]}")
        conn.close()

    def run(self) -> int:
        t = threading.Thread(target=self.read_stream, daemon=True)
        t.start()
        if not self.endpoint_ready.wait(60) or not self.endpoint:
            log("no endpoint event within 60 s; giving up")
            return 1
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                log(f"ignoring non-JSON line from client: {line[:80]}")
                continue
            try:
                self.post(line)
            except OSError as exc:
                log(f"POST failed: {exc}")
                return 1
        return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(Bridge(sys.argv[1]).run())
