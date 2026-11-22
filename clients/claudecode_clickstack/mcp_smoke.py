#!/usr/bin/env python3
"""Minimal Streamable-HTTP MCP handshake: initialize, notifications/initialized, tools/list.

Used as the agent preflight and to record the tool list. Prints server info and tool names.
With --call NAME [--args JSON] it calls one tool after the handshake and prints the result text.
"""
import argparse, json, sys
import httpx

def _payload(r: httpx.Response) -> dict:
    ct = r.headers.get("content-type", "")
    if ct.startswith("text/event-stream"):
        for line in r.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise RuntimeError("no data line in SSE response")
    return r.json()

def handshake(url: str, headers: dict | None = None, timeout: float = 60) -> dict:
    """initialize + notifications/initialized + tools/list. Returns {"server", "session", "tools"}."""
    hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})}
    with httpx.Client(timeout=timeout) as c:
        r = c.post(url, headers=hdrs, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "sregym-preflight", "version": "1"}}})
        if r.status_code != 200:
            raise RuntimeError(f"initialize failed: HTTP {r.status_code}: {r.text[:300]}")
        init = _payload(r)
        sid = r.headers.get("mcp-session-id")
        if sid:
            hdrs["Mcp-Session-Id"] = sid
        c.post(url, headers=hdrs, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = c.post(url, headers=hdrs, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = _payload(r).get("result", {}).get("tools", [])
        return {"server": init.get("result", {}).get("serverInfo", {}), "session": bool(sid),
                "tools": sorted(t["name"] for t in tools)}

def call_tool(url: str, name: str, arguments: dict, headers: dict | None = None, timeout: float = 120) -> dict:
    """initialize + notifications/initialized + tools/call. Returns {"is_error", "text", "raw"}."""
    hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})}
    with httpx.Client(timeout=timeout) as c:
        r = c.post(url, headers=hdrs, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "sregym-preflight", "version": "1"}}})
        if r.status_code != 200:
            raise RuntimeError(f"initialize failed: HTTP {r.status_code}: {r.text[:300]}")
        sid = r.headers.get("mcp-session-id")
        if sid:
            hdrs["Mcp-Session-Id"] = sid
        c.post(url, headers=hdrs, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = c.post(url, headers=hdrs, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                              "params": {"name": name, "arguments": arguments}})
        if r.status_code != 200:
            raise RuntimeError(f"tools/call failed: HTTP {r.status_code}: {r.text[:300]}")
        msg = _payload(r)
        if "error" in msg:
            return {"is_error": True, "text": json.dumps(msg["error"]), "raw": msg}
        result = msg.get("result", {})
        text = "\n".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
        return {"is_error": bool(result.get("isError")), "text": text, "raw": msg}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--header", action="append", default=[])
    ap.add_argument("--call", help="tool name to call after the handshake")
    ap.add_argument("--args", default="{}", help="JSON object with the tool arguments")
    args = ap.parse_args()
    headers = {}
    for h in args.header:
        k, _, v = h.partition(":"); headers[k.strip()] = v.strip()
    try:
        if args.call:
            result = call_tool(args.url, args.call, json.loads(args.args), headers)
            print(("ERROR " if result["is_error"] else "OK ") + result["text"][:600])
            return 1 if result["is_error"] else 0
        result = handshake(args.url, headers)
    except Exception as exc:  # noqa: BLE001
        print(str(exc)); return 1
    print(json.dumps({**result, "tool_count": len(result["tools"])}, indent=1))
    return 0

if __name__ == "__main__":
    sys.exit(main())
