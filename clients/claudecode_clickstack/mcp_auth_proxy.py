#!/usr/bin/env python3
"""Host-side OAuth-holding reverse proxy for the managed ClickStack MCP server.

Agent containers run Claude Code in -p mode and cannot complete the browser OAuth flow the
managed ClickStack MCP requires. This process runs on the runner host instead. It reuses the
token Claude Code stored when `claude mcp login clickstack` was completed once, refreshes it
with the OAuth refresh grant (writing the rotated tokens back so the CLI stays valid), and
exposes an unauthenticated Streamable-HTTP MCP endpoint on the Docker bridge address only.
Agent containers reach it as http://host.docker.internal:<port>/mcp and never see cloud
credentials. The pattern mirrors clients/claudecode_radar, where Radar runs on the host.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

log = logging.getLogger("mcp-auth-proxy")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "host", "content-length", "authorization", "accept-encoding",
}
PASS_RESPONSE_HEADERS = {"content-type", "mcp-session-id", "mcp-protocol-version", "cache-control", "www-authenticate"}
REFRESH_AHEAD_S = 180
# Tools that change ClickStack state are refused here, so the guarantee holds for every harness,
# not only for clients that can pass an allowlist to their CLI.
WRITE_TOOL_MARKERS = ("save_", "delete_", "patch_", "backtest_", "submit_")


class TokenStore:
    """The mcpOAuth entry Claude Code wrote for one server, with refresh."""

    def __init__(self, path: Path, server_name: str):
        self.path = path
        self.server_name = server_name
        self._lock = asyncio.Lock()
        self._token_endpoint: str | None = None

    def _load(self) -> tuple[dict, str, dict]:
        data = json.loads(self.path.read_text())
        for key, entry in (data.get("mcpOAuth") or {}).items():
            if key == self.server_name or key.startswith(self.server_name + "|"):
                return data, key, entry
        raise RuntimeError(f"no mcpOAuth entry for server {self.server_name!r} in {self.path}")

    def current(self) -> tuple[str, float]:
        """Return (access token, seconds until expiry)."""
        _, _, e = self._load()
        return e["accessToken"], e.get("expiresAt", 0) / 1000 - time.time()

    async def refresh(self, client: httpx.AsyncClient, reason: str) -> str:
        async with self._lock:
            data, key, e = self._load()
            remaining = e.get("expiresAt", 0) / 1000 - time.time()
            if reason == "expiring" and remaining > REFRESH_AHEAD_S:
                return e["accessToken"]  # another request refreshed first
            token_endpoint = await self._discover_token_endpoint(client, e)
            form = {"grant_type": "refresh_token", "refresh_token": e["refreshToken"], "client_id": e["clientId"]}
            r = await client.post(token_endpoint, data=form, headers={"Accept": "application/json"})
            if r.status_code != 200:
                raise RuntimeError(f"token refresh failed: HTTP {r.status_code}: {r.text[:300]}")
            tok = r.json()
            e["accessToken"] = tok["access_token"]
            if tok.get("refresh_token"):
                e["refreshToken"] = tok["refresh_token"]
            expires_in = int(tok.get("expires_in", 3600))
            e["expiresAt"] = int((time.time() + expires_in) * 1000)
            data["mcpOAuth"][key] = e
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            log.info("access token refreshed (%s); valid for %ds; refresh token %s", reason, expires_in,
                     "rotated" if tok.get("refresh_token") else "unchanged")
            return e["accessToken"]

    async def _discover_token_endpoint(self, client: httpx.AsyncClient, e: dict) -> str:
        if self._token_endpoint:
            return self._token_endpoint
        issuer = (e.get("issuer") or (e.get("discoveryState") or {}).get("authorizationServerUrl") or "").rstrip("/")
        for url in (f"{issuer}/.well-known/oauth-authorization-server", f"{issuer}/.well-known/openid-configuration"):
            try:
                r = await client.get(url, headers={"Accept": "application/json"})
                if r.status_code == 200 and r.json().get("token_endpoint"):
                    self._token_endpoint = r.json()["token_endpoint"]
                    log.info("token endpoint discovered at %s", self._token_endpoint)
                    return self._token_endpoint
            except Exception as exc:  # noqa: BLE001
                log.debug("discovery via %s failed: %s", url, exc)
        self._token_endpoint = f"{issuer}/token"
        log.warning("no OAuth metadata found; assuming token endpoint %s", self._token_endpoint)
        return self._token_endpoint


def build_app(store: TokenStore, upstream: str, extra_headers: dict[str, str], allowed_sources: set[str] | None = None) -> Starlette:
    """allowed_sources: when several clusters share one ClickStack, the source ids this cluster's agents may see
    and use. list_sources answers are filtered to them and calls naming any other sourceId are refused."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=None, write=60, pool=15), follow_redirects=False)
    sources_cache: dict[str, str] = {}

    async def filtered_sources_text() -> str:
        """The upstream list_sources result, filtered to the allowed ids (fetched once)."""
        if "text" in sources_cache:
            return sources_cache["text"]
        token = await token_for_request()
        hdrs = {"content-type": "application/json", "accept": "application/json, text/event-stream", "Authorization": f"Bearer {token}", **extra_headers}
        init = await client.post(upstream, headers=hdrs, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "sregym-proxy", "version": "1"}}})
        sid = init.headers.get("mcp-session-id")
        if sid:
            hdrs["mcp-session-id"] = sid
        await client.post(upstream, headers=hdrs, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        res = await client.post(upstream, headers=hdrs, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "clickstack_list_sources", "arguments": {}}})
        txt = res.text
        payload = json.loads(txt.split("data: ", 1)[1].split("\n", 1)[0]) if "data: " in txt else json.loads(txt)
        text = payload["result"]["content"][0]["text"]
        data = json.loads(text)
        data["sources"] = [src for src in data.get("sources", []) if str(src.get("id")) in allowed_sources]
        sources_cache["text"] = json.dumps(data, indent=2)
        log.info("list_sources filtered to %d source(s) for this cluster", len(data["sources"]))
        return sources_cache["text"]

    def foreign_source(body: bytes) -> JSONResponse | None:
        """Refuse tools/call requests that name a sourceId outside this cluster's allowed set."""
        if not allowed_sources:
            return None
        try:
            message = json.loads(body) if body else None
        except json.JSONDecodeError:
            return None
        for m in (message if isinstance(message, list) else [message] if isinstance(message, dict) else []):
            if m.get("method") == "tools/call":
                args = (m.get("params") or {}).get("arguments") or {}
                sid = args.get("sourceId") or args.get("source_id")
                if sid and str(sid) not in allowed_sources:
                    log.warning("refused call with foreign sourceId %s", sid)
                    return JSONResponse({"jsonrpc": "2.0", "id": m.get("id"),
                                         "error": {"code": -32602, "message": f"sourceId {sid} is not available in this environment; call clickstack_list_sources"}})
        return None

    def wants_source_list(body: bytes) -> dict | None:
        if not allowed_sources:
            return None
        try:
            message = json.loads(body) if body else None
        except json.JSONDecodeError:
            return None
        if isinstance(message, dict) and message.get("method") == "tools/call" and (message.get("params") or {}).get("name") == "clickstack_list_sources":
            return message
        return None

    async def token_for_request() -> str:
        token, remaining = store.current()
        if remaining < REFRESH_AHEAD_S:
            token = await store.refresh(client, "expiring")
        return token

    def blocked_call(body: bytes) -> JSONResponse | None:
        """Refuse JSON-RPC tools/call requests for write tools with a JSON-RPC error."""
        try:
            message = json.loads(body) if body else None
        except json.JSONDecodeError:
            return None
        for m in (message if isinstance(message, list) else [message] if isinstance(message, dict) else []):
            if m.get("method") == "tools/call":
                name = str((m.get("params") or {}).get("name", ""))
                if any(marker in name for marker in WRITE_TOOL_MARKERS):
                    log.warning("refused write tool %s", name)
                    return JSONResponse({"jsonrpc": "2.0", "id": m.get("id"),
                                         "error": {"code": -32601, "message": f"tool {name} is not permitted: this endpoint is read-only"}})
        return None

    async def forward(request: Request) -> StreamingResponse:
        body = await request.body()
        if request.method == "POST" and (refused := blocked_call(body)) is not None:
            return refused
        if request.method == "POST" and (refused := foreign_source(body)) is not None:
            return refused
        if request.method == "POST" and (m := wants_source_list(body)) is not None:
            text = await filtered_sources_text()
            return JSONResponse({"jsonrpc": "2.0", "id": m.get("id"), "result": {"content": [{"type": "text", "text": text}]}})
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}

        async def send(token: str) -> httpx.Response:
            h = dict(headers)
            h["Authorization"] = f"Bearer {token}"
            h.update(extra_headers)
            req = client.build_request(request.method, upstream, headers=h, content=body, params=request.query_params)
            return await client.send(req, stream=True)

        resp = await send(await token_for_request())
        if resp.status_code == 401:
            await resp.aclose()
            resp = await send(await store.refresh(client, "upstream 401"))
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() in PASS_RESPONSE_HEADERS}
        return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out_headers,
                                 background=BackgroundTask(resp.aclose))

    async def healthz(_: Request) -> JSONResponse:
        try:
            _, remaining = store.current()
            return JSONResponse({"ok": True, "server": store.server_name, "upstream": upstream, "token_expires_in_s": int(remaining)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)

    return Starlette(routes=[
        Route("/healthz", healthz, methods=["GET"]),
        Route("/{path:path}", forward, methods=["GET", "POST", "DELETE", "OPTIONS"]),
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", default="172.17.0.1:9300", help="host:port; default is the Docker bridge address only")
    ap.add_argument("--credentials", default=str(Path.home() / ".claude/.credentials.json"))
    ap.add_argument("--server", default="clickstack", help="MCP server name as registered with `claude mcp add`")
    ap.add_argument("--upstream", default="https://mcp.clickhouse.cloud/clickstack")
    ap.add_argument("--header", action="append", default=[], metavar="NAME: VALUE", help="extra header to add upstream (repeatable)")
    ap.add_argument("--refresh-now", action="store_true", help="refresh the token once, print the new expiry, exit")
    ap.add_argument("--allowed-sources", default=os.environ.get("CLICKSTACK_ALLOWED_SOURCES", ""),
                    help="comma-separated source ids this cluster's agents may use when several clusters share one ClickStack")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    extra = {}
    for h in args.header:
        name, _, value = h.partition(":")
        if not value:
            ap.error(f"bad --header {h!r}")
        extra[name.strip()] = value.strip()
    store = TokenStore(Path(args.credentials), args.server)
    _, remaining = store.current()
    log.info("loaded token for %r from %s; expires in %d s", args.server, args.credentials, int(remaining))

    if args.refresh_now:
        async def once() -> None:
            async with httpx.AsyncClient(timeout=30) as client:
                await store.refresh(client, "manual")
            _, rem = store.current()
            print(json.dumps({"refreshed": True, "token_expires_in_s": int(rem)}))
        asyncio.run(once())
        return 0

    host, _, port = args.listen.rpartition(":")
    allowed = {s.strip() for s in args.allowed_sources.split(",") if s.strip()} or None
    if allowed:
        log.info("source filter active: %d allowed source id(s)", len(allowed))
    uvicorn.run(build_app(store, args.upstream, extra, allowed), host=host, port=int(port), log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
