"use client";

import { useEffect, useRef, useState } from "react";

type Attempt = { run_dir: string; [k: string]: unknown };

/** Fetches the configuration and the attempts from the server, then hands them to the shared report core, which
 *  builds the filters, tabs and tables inside the mount element. Attempt details and the agents' SQL queries are
 *  fetched on demand through the loader. */
export default function Dashboard() {
  const mount = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<{ phase: "loading" | "ready" | "error"; detail?: string; count?: number; at?: string }>({ phase: "loading" });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setState({ phase: "loading" });
      try {
        const [cfgRes, dataRes] = await Promise.all([fetch("/api/config"), fetch("/api/attempts")]);
        if (!cfgRes.ok) throw new Error(`config: ${await cfgRes.text()}`);
        if (!dataRes.ok) throw new Error(`attempts: ${await dataRes.text()}`);
        const cfg = await cfgRes.json();
        const data: Attempt[] = await dataRes.json();
        if (cancelled || !mount.current) return;
        const core = await import("../lib/report-core.js");
        const loader = {
          steps: async (a: Attempt) => {
            const r = await fetch(`/api/attempt?id=${encodeURIComponent(a.run_dir)}`);
            if (!r.ok) throw new Error(await r.text());
            return r.json();
          },
          queries: async (items: Attempt[]) => {
            const r = await fetch("/api/queries", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: items.map((a) => a.run_dir) }) });
            if (!r.ok) throw new Error(await r.text());
            return r.json();
          },
        };
        mount.current.innerHTML = "";
        core.initReport(mount.current, { ...cfg, data, loader, notesHtml: "" });
        setState({ phase: "ready", count: data.length, at: new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC" });
      } catch (e) {
        if (!cancelled) setState({ phase: "error", detail: String(e) });
      }
    })();
    return () => { cancelled = true; };
  }, [nonce]);

  return (
    <>
      <div className="topbar">
        <h1>SREGym × ClickStack: diagnosis success by model, arm and skill tier</h1>
        <span className="status">
          {state.phase === "ready" && `${state.count?.toLocaleString()} attempts from ClickHouse, loaded ${state.at}`}
          {state.phase === "loading" && "loading…"}
        </span>
        <button type="button" onClick={() => setNonce((n) => n + 1)} disabled={state.phase === "loading"}>reload</button>
      </div>
      {state.phase === "error" && <div className="err">Could not load the data: {state.detail}</div>}
      {state.phase === "loading" && <div className="loading">Loading attempts from ClickHouse…</div>}
      <main ref={mount} id="report" />
    </>
  );
}
