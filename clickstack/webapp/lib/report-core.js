/* Shared analysis and rendering core of the SREGym × ClickStack report.
   Used by clickstack/report.py (single-file page, data embedded) and clickstack/webapp (Next.js, data from the API).
   initReport(rootElement, cfg): cfg = {data, famOrder, famLabel, series, sets, areaLabel, areaMinTier, failureModes, notesHtml,
   ch: {endpoint|proxy, database, tables, schema}, loader: {steps(attempt) -> Promise, queries(items) -> Promise}}. */
(function (global) {
const SKELETON = "<div id=\"filters\" class=\"filters\"></div>\n<div id=\"render-note\" class=\"small muted\" style=\"margin:-10px 0 4px\"></div>\n<div id=\"sat-note\" class=\"small\" style=\"margin:0 0 12px\"></div>\n<div id=\"js-error\" class=\"card hidden\" style=\"color:var(--bad)\"></div>\n<nav class=\"tabs\"><button class=\"active\" data-panel=\"p-overview\">Overview</button><button data-panel=\"p-tooluse\">Tool use</button><button data-panel=\"p-gaps\">Gaps vs stock</button><button data-panel=\"p-model\">By model</button><button data-panel=\"p-arm\">By arm / tier</button><button data-panel=\"p-problem\">By problem</button><button data-panel=\"p-attempts\">Attempts</button><button data-panel=\"p-tools\">Queries</button><button data-panel=\"p-sql\">SQL</button><button data-panel=\"p-method\">Method &amp; notes</button></nav>\n<div id=\"p-overview\" class=\"panel active\"><div id=\"tiles\" class=\"tiles\"></div><h2>Headline numbers</h2><div id=\"headlines\" class=\"card\"></div><h2>Diagnosis pass@1, model \u00d7 arm</h2><div id=\"matrix\" class=\"card\"></div></div>\n<div id=\"p-tooluse\" class=\"panel\"><p class=\"muted\">Which tools each model and arm called, how often, in passing against failing attempts, and which telemetry areas they touched. Everything follows the filters above.</p><div id=\"p-tooluse-body\"></div></div>\n<div id=\"p-gaps\" class=\"panel\"><p class=\"muted\">For every model, each arm against the same model's stock arm: problems the stock agent solves that the arm does not, a rule-based reading of why (live probing, data not captured at the tier, captured but never queried, or read and misjudged), the judge-derived failure modes, and the telemetry that would have helped.</p><div id=\"p-gaps-body\"></div></div>\n<div id=\"p-model\" class=\"panel\"></div><div id=\"p-arm\" class=\"panel\"></div><div id=\"p-problem\" class=\"panel\"></div><div id=\"p-attempts\" class=\"panel\"></div><div id=\"p-tools\" class=\"panel\"></div><div id=\"p-sql\" class=\"panel\"></div><div id=\"p-method\" class=\"panel\"></div>\n";
function initReport(root, cfg) {

// ---------------------------------------------------------------- kubectl reads and their telemetry equivalents (mirrors report.py)
const CORE_KINDS = new Set(['pod','deployment','replicaset','statefulset','daemonset','service','endpoints','endpointslice','node','namespace','job','cronjob','configmap','event','persistentvolumeclaim','horizontalpodautoscaler']);
const FIX_VERBS = new Set(['apply','patch','edit','delete','scale','set','label','annotate','create','replace','cordon','uncordon','drain','taint','expose','autoscale','certificate']);
const PROBE_VERBS = new Set(['exec','port-forward','run','debug','cp','attach','proxy']);
function splitToken(token){ const w=token.split(' '); if(['rollout','set','auth'].includes(w[0]) && w.length>1) return [w.slice(0,2).join(' '), w.slice(2).join(' ')]; return [w[0], w.slice(1).join(' ')]; }
function kubectlClass(token){ const [verb]=splitToken(token); if(FIX_VERBS.has(verb) || (verb.startsWith('rollout ') && !['rollout status','rollout history'].includes(verb)) || verb.startsWith('set ')) return 'fix'; if(PROBE_VERBS.has(verb)) return 'probe'; return 'diag'; }
function kubectlEquivalent(token){ const [verb, kind]=splitToken(token); const cls=kubectlClass(token);
  if(cls==='fix') return ['fix','a change to the cluster (mitigation, not diagnosis)',null];
  if(cls==='probe' || verb==='run' || verb==='debug') return ['probe','a live probe from inside the cluster (exec, port-forward, debug pod): nothing in the telemetry answers it',null];
  if(verb==='logs') return ['logs',"application logs (tier 0); a crashed container's previous logs only if the collector read them before the restart",0];
  if(verb==='top') return ['metrics',`${kind||'pod'} CPU and memory metrics (tier 0)`,0];
  if(kind==='event' || verb==='events') return ['events','Kubernetes events (tier 0)',0];
  if(verb==='auth can-i') return ['objects','RBAC objects: roles and bindings snapshots (tier 2)',2];
  if(verb==='rollout status' || verb==='rollout history') return ['changes',`${kind||'deployment'} changes and ReplicaSet snapshots (tier 1)`,1];
  if(kind==='secret') return ['objects','Secret metadata only: data is stripped from the telemetry by design',1];
  if(kind==='node') return ['objects','node snapshots (tier 1), node metrics (tier 0), node settings such as sysctls (tier 3)',1];
  if(['','all','manifest','api-resources','version','cluster-info','config'].includes(kind)) return ['meta','discovery, no data needed',0];
  if(CORE_KINDS.has(kind)) return ['objects',`${kind} snapshots and changes (tier 1)`,1];
  return ['objects',`${kind} snapshots and changes (tier 2, every kind)`,2]; }

const DATA = cfg.data;
const FAM_ORDER = cfg.famOrder; const FAM_LABEL = cfg.famLabel; const SERIES = cfg.series;
const CH = cfg.ch || {};   // {endpoint, database, schema, proxy, tables}
const SETS = cfg.sets || {}; const AREA_LABEL = cfg.areaLabel; const AREA_MIN_TIER = cfg.areaMinTier; const FAILURE_MODES = cfg.failureModes; const NOTES_HTML = cfg.notesHtml || '';
const LOADER = cfg.loader || { steps: a => Promise.resolve({steps: a.steps || []}), queries: items => Promise.resolve(items.flatMap(a => (a.steps || []).filter(s => s.sql).map(s => ({a, s})))) };
const KV_EQUIV = {}; for (const a of DATA) for (const t of Object.keys(a.kverbs || {})) if (!(t in KV_EQUIV)) KV_EQUIV[t] = kubectlEquivalent(t);
root.innerHTML = SKELETON;
const AREAS = ['events','objects','changes','logs','metrics','traces','node-state','machine-logs'];
const dataTier = f => (f==='stock'||f==='obs_only')? null : (/_t(\d)$/.test(f)? Math.min(3, +f.match(/_t(\d)$/)[1]) : 2);   // tier 4 ships tier-3 data
const OBS_MISSING = new Set(['events','objects','changes','node-state','machine-logs']);   // not in SREGym's Prometheus/Loki/Jaeger
let REF_ITEMS = [];
const models = [...new Set(DATA.map(a=>a.model))].sort(); const fams = FAM_ORDER.filter(f=>DATA.some(a=>a.family===f));
const harnesses = [...new Set(DATA.map(a=>a.harness))].sort(); const campaigns = [...new Set(DATA.map(a=>a.campaign||''))].sort(); const problems = [...new Set(DATA.map(a=>a.problem))].sort();
const colour = m => SERIES[models.indexOf(m) % SERIES.length];
const esc = s => String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const HELP = {
 attempts: "Attempts in view. One attempt is one agent run on one problem from a fresh deployment and empty ClickStack tables; each problem is run three times per model and arm. Unfinished or undeployed attempts carry a yellow mark and are left out of the rates.",
 pass1: "pass@1: the share of scored attempts whose written diagnosis the judge accepted. The judge (claude-sonnet-5-5) works through a checklist against the fault specification; a composite score of 0.70 or more passes. The strictest view: every attempt counts on its own.",
 passk: "pass@k: the share of (model, arm, problem) cells where at least one of the k attempts (k = 3 here) was accepted. The bracketed number is how many cells are in view. It answers whether the setup can solve the problem at all given a few tries, and shows how much of a pass@1 gap is inconsistency rather than inability.",
 mitigation: "Share of attempts where SREGym's live oracle confirmed the fix after the agent applied it and submitted. Only arms with kubectl can fix; ClickStack-only arms stop after the diagnosis and show –.",
 ttd: "Mean time to diagnosis over all scored attempts, right or wrong: SREGym's clock from agent start to the diagnosis submission, including every tool call and model response.",
 ttdc: "Mean time to diagnosis over accepted diagnoses only. Wrong answers can be fast (a quick guess) or slow (a 30-minute timeout), so this isolates how quickly the setup reaches the right answer.",
 ttm: "Mean time to mitigation over attempts whose fix was confirmed: agent start to the mitigation submission.",
 tokens: "Mean total tokens per attempt as the harness reports them at the end of the run: input, output, cached input and cache-creation tokens. In long runs most of it is cached input.",
 mcp: "Mean number of MCP tool calls per attempt, all servers together: ClickStack's tools (search, table, timeseries, SQL, service health, event patterns, trace tools) in the ClickStack arms, the ClickHouse server's run_query and list tools in the SQL arms, or SREGym's Prometheus, Loki and Jaeger tools in the telemetry-without-kubectl arm. Calls an agent made through an MCP client it wrote itself in the shell (Codex did this when the SSE servers would not attach) are counted here too and flagged in the attempt detail. In brackets: their share of all tool calls in the cell (MCP, kubectl and other shell commands).",
 kubectl: "Mean number of kubectl invocations per attempt (several in one shell command count separately), through SREGym's filtering proxy. Zero for arms without cluster access. In brackets: their share of all tool calls in the cell.",
 calls_total: "Mean tool calls per attempt, everything the agent ran: MCP tools, kubectl, and other shell commands. The columns to the right split it; their shares add up to 100%.",
 cs: "Mean calls per attempt to ClickStack's MCP tools (search, table, timeseries, sql, service_health, event_patterns, trace tools, list and describe sources). In brackets: their share of all tool calls in the cell. Only the ClickStack arms have these.",
 sqlmcp: "Mean calls per attempt to the ClickHouse SQL MCP server (run_query, list_tables, list_databases). In brackets: share of all tool calls. Only the ClickHouse SQL arms have these.",
 obsmcp: "Mean calls per attempt to SREGym's own Prometheus, Loki and Jaeger MCP tools (get_metrics, get_logs, get_traces ...). In brackets: share of all tool calls. Only the stock-telemetry-without-kubectl arm has these; calls Codex made through a client it wrote itself are included.",
 othershell: "Mean shell commands per attempt that are not kubectl and not a telemetry tool call, with their share of all tool calls. What they are, in every arm: the curl that submits the diagnosis (1 to 2 per attempt), reading the skill or the run's files (about 2), live probes such as curl or dig against a service, writing or running a small script, and plain text processing (grep, jq, sort). The Tool use tab breaks them down per arm.",
 sql: "Share of the ClickStack MCP calls that were clickstack_sql, raw SQL instead of the search, table and timeseries tools. Shown only when ClickStack arms are in view.",
 errors: "Share of all tool calls (ClickStack, kubectl and other shell) that returned an error: invalid arguments, results too large for the harness, blocked commands, non-zero exits.",
 cost: "Mean API cost per attempt as the harness reports it. Claude Code reports a dollar figure; Codex reports none, so Codex rows show –.",
 problems: "Distinct problems among the attempts in view. SREGym-Lite has 17.",
 matrix: "Each cell is diagnosis pass@1 for that model and arm, with accepted/scored underneath and, for kubectl arms, the mitigation count. Darker is higher.",
 allarms: "All arms in view pooled for the model.",
 arm: "stock: SREGym's unchanged agent: kubectl through the filtering proxy, with Prometheus, Loki and Jaeger deployed in the cluster and reachable through it (no MCP tools; the stock clients attach none). kubectl + ClickStack: the same plus the ClickStack MCP tools, with tier 2 state and skill. ClickStack only, tier N: no cluster access, telemetry alone, diagnosis only. Tier 0: logs with pod labels, events, metrics, traces. Tier 1: plus snapshots and changes of the core Kubernetes objects. Tier 2: plus every kind, control-plane and machine logs. Tier 3: plus node settings. Tier 4: the same data as tier 3 plus investigation rules in the skill (read whole manifests, look for a second fault, name the origin not the victim). Stock telemetry MCP only: SREGym's own MCP server's Prometheus, Loki and Jaeger tools (get_metrics, get_logs, get_traces and so on), no kubectl; the only arm that uses those tools. kubectl + ClickHouse SQL and ClickHouse SQL only, tier N: the same arms with the open-source ClickHouse MCP server (run_query, list_tables) instead of ClickStack's tools, over the same data; they measure what ClickStack's purpose-built tools add over raw SQL.",
 harness: "The agent program driving the model: Claude Code for Claude models, Codex for OpenAI models. Each has its own tool loop and prompts, so compare models within a harness.",
 campaign: "lite-sweep: the Lite sweep, 17 SREGym-Lite problems × 3 attempts for the first six arms. phase1: the other arms on the same problems. phase2: every arm on the 54 further problems. *-fill: re-runs of attempts a campaign could not deploy or finish. anthropic-sweep, codex-shakedown, sql-sweep*, phase1-shakedown: pilots and checks, a problem or two each. Use the phase buttons above for the usual selections.",
 balanced: "Keep only problems with a scored attempt in every selected model × arm cell, so arms are compared on the same problems when some attempts are missing.",
 saturated: "A saturated problem is one that the reference arms solve essentially every time, so it cannot separate setups. Definition: over the selected campaigns, models and harnesses, and the reference arms chosen in 'judged on' (kubectl arms by default), the problem's overall pass rate is at least the first threshold and no single model × arm cell is below the second (67% means at most one failed attempt out of three). A problem needs scored attempts in at least three reference cells before it can count as saturated, so a campaign that has only started does not hide problems on one cell's say-so. The list below the filters shows which problems qualify; with the switch on they are removed from every number on the page, whatever arms are in view.",
 sat_ref: "Which arms decide whether a problem is saturated. kubectl arms: stock and every kubectl + telemetry arm, the agents with cluster access (the default, and the reading that a problem is 'always solved'). stock arm: SREGym's unchanged agent alone. selected arms: whatever arms are ticked, which with telemetry-only arms in view usually saturates nothing. telemetry-only arms: the arms without kubectl. The arm filter itself does not change the saturation judgement.",
 phases: "Phase 1 is every arm on the 17 SREGym-Lite problems: the Lite sweep (stock, kubectl + ClickStack tier 2, ClickStack only tiers 0 to 3) together with the phase1 campaign that added the other arms. Phase 2 is every arm on the 54 further problems of the 71-problem set. Everything adds the pilots and shakedowns. The buttons set the campaign checkboxes under 'campaigns'; ?phase=p1, p2 or all in the URL does the same.",
 mit_hide: "Mitigation is attempted only by the kubectl arms and graded by SREGym's live oracle after the agent's fix; the telemetry-only arms stop after the diagnosis. Hiding the mitigation columns (mitigation rate, mean TTM, the fix counts in the matrix, the Fix and TTM columns of the attempts table) keeps the diagnosis comparison compact. Untick to show them; ?mit=1 in the URL does the same.",
 models_default: "claude-fable-5-2, claude-opus-5-5 and gpt-6.1-sol are the study's models and start ticked; claude-fable-5-1 and gpt-6.1-terra ran only in the Lite sweep and start unticked. Tick them to add them to every number.",
 problems_pick: "Tick the problems to include; every number follows. 'exclude saturated' unticks the problems in the saturated list under the current definition, 'all' restores them, 'none' clears them so a few can be ticked by hand.",
 delta: "Each tier's difference from tier 0 among the ClickStack-only arms in view: pass@1 in percentage points; tokens, ClickStack calls and TTD-when-correct as percentage changes.",
 problem: "SREGym problem id: the fault type in the prefix, the application in the suffix (hotel_reservation, astronomy_shop, social_network).",
 fault: "The ground-truth root cause, from the fault specification SREGym writes beside each attempt.",
 diag: "Judge verdict with the composite score in brackets. Green: accepted (0.70 or more). Red: rejected. Yellow: not finished, not deployed or not scored. Hover a mark for the judge's reasoning.",
 fix: "SREGym oracle verdict on the mitigation: ✓ confirmed, ✗ not confirmed, – not attempted (ClickStack-only arms).",
 attempt_no: "Attempt number within the (model, arm, problem) cell, 1 to 3.",
 ttd_a: "Time from agent start to the diagnosis submission.",
 ttm_a: "Time from agent start to the mitigation submission, kubectl arms only.",
 tokens_a: "Total tokens the harness reported for this attempt.",
 mcp_a: "MCP tool calls in this attempt: ClickStack, ClickHouse SQL or SREGym's Prometheus, Loki and Jaeger tools, whichever the arm has.",
 kubectl_a: "Shell commands invoking kubectl in this attempt.",
 sql_a: "MCP calls in this attempt that carried raw SQL.",
 errors_a: "Tool calls in this attempt that returned an error.",
 blocked: "Network requests from the agent container that the sandbox refused. Almost always the harness's own telemetry endpoints (datadoghq.com for Claude Code, chatgpt.com for Codex), nothing the agent needed.",
 cost_a: "API cost the harness reported for this attempt, Claude Code only.",
 status: "SREGym's run status and, for incomplete attempts, the reason: the agent exited early, a timeout, or a deploy that failed after every try.",
 tool: "MCP tool name (ClickStack, ClickHouse SQL, or SREGym's Prometheus, Loki and Jaeger tools). Tools that write (save, patch, delete, backtest, submit feedback) are refused by the proxy and never appear here.",
 pre: "The agent's last message before it issued the call: its own stated reason for the query.",
 set: "Restrict every number to a named problem set: SREGym-Lite (17 problems), the 54 further problems of the 71-problem set, or the whole set. Lite results stay comparable with the earlier Lite-only campaign.",
 calls: "Mean tool calls per attempt, split into MCP tools (ClickStack, ClickHouse SQL or SREGym's Prometheus/Loki/Jaeger), kubectl invocations (several per shell command count separately), live probes (kubectl exec, port-forward, run/debug pods, and shell curl/dig/nc against the cluster) and fixes (apply, patch, delete, scale, rollout restart ...).",
 areas: "Share of attempts in which the agent queried each telemetry area at least once: Kubernetes events, object snapshots (k8s-objects), object changes (k8s-changes), application logs, metrics, traces, node settings. Classified from the query text and the tool used. kubectl reads are classified the same way for the stock arm (get/describe = objects, get events = events, logs = logs, top = metrics).",
 toptools: "The tools this model and arm used most, as mean calls per attempt. MCP tool names for the telemetry arms; 'verb kind' for kubectl (get pod, describe deployment, logs, exec ...).",
 toolerr: "Share of this cell's tool calls that returned an error.",
 passfail: "Mean calls per attempt among accepted attempts versus rejected ones; a tool used far more in failures than in passes often marks where the agent got stuck.",
 gaps: "Gap problems: the stock arm (kubectl plus Prometheus, Loki and Jaeger) with the same model passed at least half of its scored attempts and this arm passed fewer than half. Gains: the reverse. Shared failures: both below half. Rates are computed on the problems in view.",
 verdict: "A rule-based reading of each gap: 'live probing' when the stock agent's successful runs leaned on exec, port-forward or debug pods (at least a quarter of their cluster reads), which the telemetry cannot replace; 'not captured at this tier' when the data the stock agent read arrives only at a higher collector tier; 'captured but not queried' when this arm never touched the area the stock agent used; otherwise 'read the right data, wrong conclusion', with the judge-derived failure modes.",
 modes: "Failure modes derived from the judge's checklist and the submission text; the Method tab defines each. 'pass' and 'unscored' are not failures.",
 helpful: "Telemetry the stock agent's successful runs relied on in the gap problems, ranked by how many gap problems needed it, with whether this arm's tier ships it and whether this arm's failing attempts queried it at all.",
 sqltab: "Runs SQL against the ClickHouse database that clickstack/load_results.py fills from the same files this page is built from (one row per attempt, one per tool call). Queries go straight from this browser to the ClickHouse HTTP endpoint with the user and password entered here; they are kept only in this browser if you tick remember. Results are capped at 2,000 rows.",
 sqlwhere: "The filters at the top of the page, expressed as a WHERE clause over the attempts table: campaigns, models, arms, harness, problem set, the problem checklist and the hidden saturated problems. Presets insert it where they say {where}; paste it into your own queries to keep the page and the SQL on the same slice.",
 stockref: "The reference for this model is its stock arm (kubectl + Prometheus/Loki/Jaeger) over the same filters; the arm filter is ignored for the reference so it stays available while you look at other arms.",
};
const PHASES = { p1: {label:'Phase 1 · Lite problems, every arm', campaigns:['lite-sweep','lite-sweep-fill','phase1','phase1-fill']},
                 p2: {label:'Phase 2 · 54 further problems', campaigns:['phase2','phase2-fill']},
                 all: {label:'Everything', campaigns:null} };
function applyPhase(key){ const ph=PHASES[key]; if(!ph) return; document.querySelectorAll('.f-camp').forEach(x=>{ x.checked = ph.campaigns? ph.campaigns.includes(x.value) : true; }); }
function currentPhase(){ const on=new Set([...document.querySelectorAll('.f-camp:checked')].map(x=>x.value)); for(const [k,ph] of Object.entries(PHASES)){ const want=new Set((ph.campaigns||campaigns).filter(c=>campaigns.includes(c))); if(want.size===on.size && [...want].every(c=>on.has(c))) return k; } return null; }
const DEFAULT_MODELS = ['claude-fable-5-2','claude-opus-5-5','gpt-6.1-sol'];   // the study's models; others (earlier pilots) start unticked
const defaultModel = m => models.some(x=>DEFAULT_MODELS.includes(x)) ? DEFAULT_MODELS.includes(m) : true;
const SHOW_HELP = new URLSearchParams(location.search).get('help');  // ?help=passk keeps that explanation open (for links and screenshots)
const H = (label,key) => `${label}${label?' ':''}<span class="info${SHOW_HELP===key?' show':''}" tabindex="0" data-tip="${esc(HELP[key]||'')}">i</span>`;
const pct = (n,d) => d? Math.round(100*n/d)+'%' : '–'; const mean = xs => { const v=xs.filter(x=>typeof x==='number'&&isFinite(x)); return v.length? v.reduce((a,b)=>a+b,0)/v.length : null; };
const fmt = (x,d=1) => x==null? '–' : (Math.abs(x)>=1000? Math.round(x).toLocaleString() : x.toFixed(d));
const fmtS = x => x==null? '–' : (x>=90? (x/60).toFixed(1)+' min' : Math.round(x)+' s');
const tokTotal = a => a.tokens? (a.tokens.total_tokens ?? ((a.tokens.input_tokens||0)+(a.tokens.output_tokens||0))) : null;
const SEQ=["#cde2fb","#b7d3f6","#9ec5f4","#86b6ef","#6da7ec","#5598e7","#3987e5","#2a78d6","#256abf","#1c5cab","#184f95","#104281","#0d366b"];
const seq = (n,d) => d? SEQ[Math.min(SEQ.length-1, Math.round((n/d)*(SEQ.length-1)))] : 'transparent';
const fg = (n,d) => (d && n/d>0.55)? '#ffffff' : '#0b0b0b'; const cellStyle = (n,d) => `background:${seq(n,d)};color:${fg(n,d)}`;

function mark(a){ const sc = a.composite!=null? ` (${a.composite.toFixed(2)})` : '';
  if(a.deploy_failed) return '<span class="st warn">! deploy</span>'; if(a.unscored) return '<span class="st warn">! unscored</span>';
  if(a.diagnosis===true) return `<span class="st good" title="${esc((a.judge_reasoning||'').slice(0,600))}">✓ pass${sc}</span>`;
  if(a.diagnosis===false) return `<span class="st bad" title="${esc((a.judge_reasoning||'').slice(0,600))}">✗ fail${sc}</span>`;
  if(a.timed_out) return '<span class="st warn">! timeout</span>'; if(a.run_status&&a.run_status!=='complete') return `<span class="st warn">! ${esc(a.run_status)}</span>`; if(!a.run_status) return '<span class="st warn">! unfinished</span>'; return '<span class="st muted">– none</span>'; }

function agg(items){ const scored=items.filter(a=>a.diagnosis!==null); const passes=scored.filter(a=>a.diagnosis).length;
  const cells={}; for(const a of items){ const k=a.model+'|'+a.family+'|'+a.problem; cells[k]=cells[k]||[]; cells[k].push(a); }
  const cellList=Object.values(cells); const anyPass=cellList.filter(c=>c.some(a=>a.diagnosis)).length;
  const mit=items.filter(a=>a.mitigation!==null); const nmcp=items.reduce((s,a)=>s+a.n_mcp,0);
  return { n:items.length, passes, scored:scored.length, pass1: scored.length? passes/scored.length : null, passk: cellList.length? anyPass/cellList.length : null, cells: cellList.length,
    mitPass: mit.filter(a=>a.mitigation).length, mitN: mit.length, ttd: mean(items.filter(a=>a.diagnosis!==null).map(a=>a.ttd_s)), ttdPass: mean(items.filter(a=>a.diagnosis).map(a=>a.ttd_s)),
    ttm: mean(items.filter(a=>a.mitigation).map(a=>a.ttm_s)), tokens: mean(items.map(tokTotal)), cost: mean(items.map(a=>a.cost_usd)),
    mcp: mean(items.map(a=>a.n_mcp)), kubectl: mean(items.map(a=>a.n_kubectl)), calls: mean(items.map(a=>a.n_mcp+a.n_kubectl+a.n_shell)),
    mcpShare: (()=>{const c=items.reduce((s,a)=>s+a.n_mcp+a.n_kubectl+a.n_shell,0); return c? nmcp/c : null;})(), kubectlShare: (()=>{const c=items.reduce((s,a)=>s+a.n_mcp+a.n_kubectl+a.n_shell,0); return c? items.reduce((s,a)=>s+a.n_kubectl,0)/c : null;})(),
    cs: mean(items.map(a=>a.n_cs||0)), sqlmcp: mean(items.map(a=>a.n_sqlmcp||0)), obsmcp: mean(items.map(a=>a.n_obsmcp||0)), shell: mean(items.map(a=>a.n_shell||0)),
    shares: (()=>{const c=items.reduce((s,a)=>s+a.n_mcp+a.n_kubectl+a.n_shell,0); const f=k=>c? items.reduce((s,a)=>s+(a[k]||0),0)/c : null; return {cs:f('n_cs'), sqlmcp:f('n_sqlmcp'), obsmcp:f('n_obsmcp'), kubectl:f('n_kubectl'), shell:f('n_shell')};})(),
    shellKinds: (()=>{const o={}; for(const a of items) for(const [k,v] of Object.entries(a.shell_kinds||{})) o[k]=(o[k]||0)+v; return o;})(),
    sqlShare: nmcp? items.reduce((s,a)=>s+a.n_sql,0)/nmcp : null, errRate: (()=>{const c=items.reduce((s,a)=>s+a.n_mcp+a.n_kubectl+a.n_shell,0); return c? items.reduce((s,a)=>s+a.n_errors,0)/c : null;})(),
    skill: items.filter(a=>a.skill_read).length, unfinished: items.filter(a=>a.diagnosis===null).length }; }

let SATURATED = new Map(); const MIN_REF_CELLS = 3;   // model × arm cells with scored attempts a problem needs before it can count as saturated
function saturatedProblems(items, thr, minCell){
  // a problem is saturated when its overall pass rate is >= thr and no (model, arm) cell is below minCell
  const stat = {};
  for(const a of items){ if(a.diagnosis===null) continue; const s = stat[a.problem] = stat[a.problem] || {p:0,n:0,cells:{}}; s.p += a.diagnosis?1:0; s.n++;
    const k = a.model+'|'+a.family; const c = s.cells[k] = s.cells[k] || [0,0]; c[0] += a.diagnosis?1:0; c[1]++; }
  const out = new Map();
  for(const [prob, s] of Object.entries(stat)){ const rate = s.p/s.n; const cells = Object.values(s.cells); const weakest = Math.min(...cells.map(c=>c[0]/c[1]));
    if(cells.length < MIN_REF_CELLS) continue;   // too few reference cells to call a problem saturated (early in a campaign)
    if(rate >= thr && weakest >= minCell) out.set(prob, {rate, weakest, passes:s.p, n:s.n, cells:cells.length, imperfect: cells.filter(c=>c[0]<c[1]).length}); }
  return out; }
function selected(){ const on = cls => [...document.querySelectorAll(cls+':checked')].map(x=>x.value);
  const M=new Set(on('.f-model')), F=new Set(on('.f-fam')), H=new Set(on('.f-har')), C=new Set(on('.f-camp')); const Q=document.getElementById('f-text').value.toLowerCase();
  const S=document.getElementById('f-set').value; const setP = S? new Set(SETS[S]||[]) : null;
  const PSEL = new Set(on('.f-prob'));
  const base0 = DATA.filter(a=>C.has(a.campaign||'')&&M.has(a.model)&&H.has(a.harness)&&(!setP||setP.has(a.problem)));
  const base = base0.filter(a=>F.has(a.family));
  const ref = document.getElementById('f-sat-ref').value;
  const isK = f => f==='stock' || f.startsWith('kubectl_');
  const refFam = f => ref==='all'? F.has(f) : ref==='stock'? f==='stock' : ref==='kubectl'? isK(f) : !isK(f);
  SATURATED = saturatedProblems(base0.filter(a=>refFam(a.family)), parseFloat(document.getElementById('f-sat-thr').value), parseFloat(document.getElementById('f-sat-cell').value));
  const hideSat = document.getElementById('f-sat').checked;
  const keep = a => PSEL.has(a.problem) && !(hideSat&&SATURATED.has(a.problem));
  REF_ITEMS = base0.filter(keep);
  let items = base.filter(a=>keep(a)&&(Q===''||(a.problem+' '+a.model+' '+a.agent+' '+a.submission).toLowerCase().includes(Q)));
  const cnt=document.getElementById('f-prob-count'); if(cnt) cnt.textContent = `${new Set(items.map(a=>a.problem)).size} of ${problems.length} in view`;
  if(document.getElementById('f-balanced').checked){ const cells=new Set(items.map(a=>a.model+'|'+a.family)); const have={}; for(const a of items) if(a.diagnosis!==null){ (have[a.problem]=have[a.problem]||new Set()).add(a.model+'|'+a.family); }
    const ok=new Set(Object.keys(have).filter(p=>have[p].size===cells.size)); items=items.filter(a=>ok.has(a.problem)); }
  return items; }

const withShare = (n, sh) => `${fmt(n)} <span class="small">(${sh==null?'–':Math.round(100*sh)+'%'})</span>`;
const AGG_COLS = [
  {k:'n', h:['attempts','attempts'], cell:g=>g.n},
  {k:'pass1', h:['pass@1','pass1'], cell:g=>pct(g.passes,g.scored)},
  {k:'passk', h:['pass@k (cells)','passk'], cell:g=>`${g.passk==null?'–':Math.round(100*g.passk)+'%'} <span class="small">(${g.cells})</span>`},
  {k:'mit', h:['mitigation','mitigation'], cell:g=>g.mitN? pct(g.mitPass,g.mitN):'–'},
  {k:'ttd', h:['mean TTD','ttd'], cell:g=>fmtS(g.ttd)},
  {k:'ttdc', h:['TTD when correct','ttdc'], cell:g=>fmtS(g.ttdPass)},
  {k:'ttm', h:['mean TTM','ttm'], cell:g=>fmtS(g.ttm)},
  {k:'tokens', h:['mean tokens','tokens'], cell:g=>fmt(g.tokens,0)},
  {k:'calls', h:['calls / attempt','calls_total'], cell:g=>fmt(g.calls)},
  {k:'cs', h:['ClickStack calls','cs'], cell:g=>withShare(g.cs, g.shares.cs), present:items=>items.some(a=>a.n_cs>0)},
  {k:'sqlmcp', h:['ClickHouse SQL calls','sqlmcp'], cell:g=>withShare(g.sqlmcp, g.shares.sqlmcp), present:items=>items.some(a=>a.n_sqlmcp>0)},
  {k:'obsmcp', h:['Prometheus/Loki/Jaeger calls','obsmcp'], cell:g=>withShare(g.obsmcp, g.shares.obsmcp), present:items=>items.some(a=>a.n_obsmcp>0)},
  {k:'kubectl', h:['kubectl calls','kubectl'], cell:g=>withShare(g.kubectl, g.shares.kubectl), present:items=>items.some(a=>a.n_kubectl>0)},
  {k:'shell', h:['other shell','othershell'], cell:g=>withShare(g.shell, g.shares.shell)},
  {k:'sql', h:['SQL share of ClickStack calls','sql'], cell:g=>g.sqlShare==null?'–':Math.round(100*g.sqlShare)+'%', present:items=>items.some(a=>a.n_cs>0)},
  {k:'err', h:['tool errors','errors'], cell:g=>g.errRate==null?'–':Math.round(100*g.errRate)+'%'},
  {k:'cost', h:['mean cost','cost'], cell:g=>g.cost==null?'–':'$'+g.cost.toFixed(2)},
];
const showMit = () => !document.getElementById('f-mit').checked;
const aggCols = items => AGG_COLS.filter(c=>(!c.present || c.present(items)) && !(['mit','ttm'].includes(c.k) && !showMit()));
const aggHead = cols => `<thead><tr><th></th>${cols.map(c=>`<th class="num">${H(c.h[0],c.h[1])}</th>`).join('')}</tr></thead>`;
function aggRow(label, g, cols){ return `<tr><th>${label}</th>${cols.map(c=>`<td class="num">${c.cell(g)}</td>`).join('')}</tr>`; }

let ITEMS=[]; const DIRTY=new Set(); let ATT_SORTED=[];
function renderCore(items){ const all=agg(items);
  const hideSat = document.getElementById('f-sat').checked; const satList=[...SATURATED.entries()].sort((x,y)=>y[1].rate-x[1].rate);
  document.getElementById('sat-note').innerHTML = satList.length ? `<details ${hideSat?'open':''}><summary>${satList.length} saturated problem(s) under the current definition${hideSat?', hidden from every number on this page':''} (details)</summary><table><thead><tr><th>Problem</th><th class="num">pass rate</th><th class="num">attempts</th><th class="num">weakest model × arm cell</th><th class="num">cells with a failure</th></tr></thead><tbody>${satList.map(([p,s])=>`<tr><td>${esc(p)}</td><td class="num">${Math.round(100*s.rate)}%</td><td class="num">${s.passes}/${s.n}</td><td class="num">${Math.round(100*s.weakest)}%</td><td class="num">${s.imperfect}/${s.cells}</td></tr>`).join('')}</tbody></table></details>` : '<span class="muted">No problem is saturated under the current definition.</span>';
  document.getElementById('tiles').innerHTML = [[all.pass1==null?'–':Math.round(100*all.pass1)+'%',H('diagnosis pass@1','pass1')],[all.passk==null?'–':Math.round(100*all.passk)+'%',H('pass@k over '+all.cells+' cells','passk')],[items.length,H('attempts'+(all.unfinished?` (${all.unfinished} unfinished)`:''),'attempts')],[new Set(items.map(a=>a.problem)).size,H('problems in view'+((hideSat&&SATURATED.size)? ` (${[...SATURATED.keys()].length} saturated hidden)` : ''),'problems')],[fmtS(all.ttdPass),H('mean TTD when correct','ttdc')],[fmt(all.tokens,0),H('mean tokens','tokens')],[fmt(all.mcp),H('MCP calls / attempt','mcp')]].map(([n,l])=>`<div class="tile"><div class="num">${n}</div><div class="lbl">${l}</div></div>`).join('');
  // headlines
  const cols=aggCols(items);
  let h='<table>'+aggHead(cols)+'<tbody>'; for(const f of fams){ const g=agg(items.filter(a=>a.family===f)); if(g.n) h+=aggRow(esc(FAM_LABEL[f]),g,cols);} h+='</tbody></table>';
  let d='';
  for(const [base,tiers,label] of [['only_t0',['only_t1','only_t2','only_t3','only_t4'],'ClickStack-only arms'],['chsql_only_t0',['chsql_only_t1','chsql_only_t2','chsql_only_t3','chsql_only_t4'],'ClickHouse-SQL-only arms']]){ const t0=agg(items.filter(a=>a.family===base)); if(!t0.n) continue;
    d+='<p class="muted">Change against tier 0 ('+label+')'+H('','delta')+': '+tiers.filter(f=>fams.includes(f)).map(f=>{const g=agg(items.filter(a=>a.family===f)); if(!g.n) return ''; const dp=(g.pass1!=null&&t0.pass1!=null)? (Math.round(100*(g.pass1-t0.pass1))+' pts pass@1') : ''; const dt=(g.tokens!=null&&t0.tokens!=null)? Math.round(100*(g.tokens-t0.tokens)/t0.tokens)+'% tokens':''; const dc=(g.mcp!=null&&t0.mcp!=null)? Math.round(100*(g.mcp-t0.mcp)/t0.mcp)+'% MCP calls':''; const dd=(g.ttdPass!=null&&t0.ttdPass!=null)? Math.round(100*(g.ttdPass-t0.ttdPass)/t0.ttdPass)+'% TTD':''; return `<b>${esc(FAM_LABEL[f])}</b>: ${[dp,dt,dc,dd].filter(Boolean).join(', ')}`;}).filter(Boolean).join(' · ')+'</p>'; }
  let hm='<table>'+aggHead(cols)+'<tbody>'; for(const m of models){ const g=agg(items.filter(a=>a.model===m)); if(g.n) hm+=aggRow(`<span class="swatch" style="background:${colour(m)}"></span>${esc(m)}`,g,cols);} hm+='</tbody></table>';
  document.getElementById('headlines').innerHTML = `<h3>By arm</h3>${h}${d}<h3>By model</h3>${hm}`;
  // matrix
  let mx='<table class="matrix"><thead><tr><th>'+H('Model','matrix')+'</th>'+fams.map(f=>`<th>${esc(FAM_LABEL[f])}</th>`).join('')+'<th>'+H('All arms','allarms')+'</th></tr></thead><tbody>';
  for(const m of models){ mx+=`<tr><th><span class="swatch" style="background:${colour(m)}"></span>${esc(m)}</th>`; for(const f of [...fams,null]){ const g=agg(items.filter(a=>a.model===m&&(f===null||a.family===f))); mx+=`<td class="rate" style="${cellStyle(g.passes,g.scored)}"><b>${pct(g.passes,g.scored)}</b><br><span class="small">${g.passes}/${g.scored}${(g.mitN&&showMit())?` · fix ${g.mitPass}/${g.mitN}`:''}</span></td>`; } mx+='</tr>'; }
  mx+='<tr><th>All models</th>'; for(const f of [...fams,null]){ const g=agg(items.filter(a=>f===null||a.family===f)); mx+=`<td class="rate" style="${cellStyle(g.passes,g.scored)}"><b>${pct(g.passes,g.scored)}</b><br><span class="small">${g.passes}/${g.scored}</span></td>`; } mx+='</tr></tbody></table>';
  document.getElementById('matrix').innerHTML=mx;
}
function renderByModel(){ const items=ITEMS;
  document.getElementById('p-model').innerHTML = models.map(m=>{ const mi=items.filter(a=>a.model===m); if(!mi.length) return ''; const g=agg(mi);
    const bars='<div class="bars">'+fams.map(f=>{const gg=agg(mi.filter(a=>a.family===f)); if(!gg.n) return ''; const w=gg.scored? 100*gg.passes/gg.scored:0; return `<div>${esc(FAM_LABEL[f])}</div><div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${colour(m)}"></div></div><div>${pct(gg.passes,gg.scored)} <span class="small">(${gg.passes}/${gg.scored}, TTD ${fmtS(gg.ttdPass)})</span></div>`;}).join('')+'</div>';
    return `<section class="card"><h3><span class="swatch" style="background:${colour(m)}"></span>${esc(m)} <span class="small">${esc(mi[0].harness)}</span></h3><p class="muted">${g.n} attempts · pass@1 ${pct(g.passes,g.scored)} · mean TTD when correct ${fmtS(g.ttdPass)} · mean tokens ${fmt(g.tokens,0)} · mean cost ${g.cost==null?'–':'$'+g.cost.toFixed(2)}</p>${bars}${problemTable(mi, fams, 'family')}</section>`; }).join('');
}
function renderByArm(){ const items=ITEMS;
  document.getElementById('p-arm').innerHTML = fams.map(f=>{ const fi=items.filter(a=>a.family===f); if(!fi.length) return ''; const g=agg(fi);
    const bars='<div class="bars">'+models.map(m=>{const gg=agg(fi.filter(a=>a.model===m)); if(!gg.n) return ''; const w=gg.scored? 100*gg.passes/gg.scored:0; return `<div><span class="swatch" style="background:${colour(m)}"></span>${esc(m)}</div><div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${colour(m)}"></div></div><div>${pct(gg.passes,gg.scored)} <span class="small">(${gg.passes}/${gg.scored}, TTD ${fmtS(gg.ttdPass)})</span></div>`;}).join('')+'</div>';
    const tools={}; for(const a of fi) for(const [t,c] of Object.entries(a.tools)) tools[t]=(tools[t]||0)+c; const tl=Object.entries(tools).sort((x,y)=>y[1]-x[1]).map(([t,c])=>`${esc(t)} ${c}`).join(' · ');
    return `<section class="card"><h3>${esc(FAM_LABEL[f])}</h3><p class="muted">${g.n} attempts · pass@1 ${pct(g.passes,g.scored)} · mean TTD when correct ${fmtS(g.ttdPass)} · mean tokens ${fmt(g.tokens,0)} · ${fmt(g.mcp)} MCP and ${fmt(g.kubectl)} kubectl calls per attempt · SQL share ${g.sqlShare==null?'–':Math.round(100*g.sqlShare)+'%'} · tool errors ${g.errRate==null?'–':Math.round(100*g.errRate)+'%'}</p>${bars}${tl?`<p class="small">MCP tools used: ${tl}</p>`:''}${problemTable(fi, models, 'model')}</section>`; }).join('');
}
function renderByProblem(){ const items=ITEMS;
  const cols=[]; for(const m of models) for(const f of fams) if(items.some(a=>a.model===m&&a.family===f)) cols.push([m,f]);
  let pt='<table class="rows-sep"><thead><tr><th>'+H('Problem','problem')+'</th><th>'+H('Fault','fault')+'</th>'+cols.map(([m,f])=>`<th><span class="swatch" style="background:${colour(m)}"></span>${esc(m)}<br><span class="small">${esc(FAM_LABEL[f])}</span></th>`).join('')+'</tr></thead><tbody>';
  for(const p of problems){ const pi=items.filter(a=>a.problem===p); if(!pi.length) continue; pt+=`<tr><th>${esc(p)}</th><td class="small fault">${esc((pi.find(a=>a.fault)||{}).fault||'')}</td>`+cols.map(([m,f])=>{const c=pi.filter(a=>a.model===m&&a.family===f).sort((x,y)=>x.attempt-y.attempt); const g=agg(c); return `<td>${c.map(mark).join(' ')||'<span class="st muted">–</span>'}<br><span class="small">${c.length? 'TTD '+fmtS(g.ttdPass):''}</span></td>`;}).join('')+'</tr>'; }
  document.getElementById('p-problem').innerHTML=`<section class="card">${pt}</tbody></table></section>`;
}
function renderAttempts(){ const items=[...ITEMS];
  let at=`<table class="runs"><thead><tr><th></th><th>Model</th><th>${H('Arm','arm')}</th><th>${H('Problem','problem')}</th><th>${H('#','attempt_no')}</th><th>${H('Diagnosis','diag')}</th><th class="mit">${H('Fix','fix')}</th><th class="num">${H('TTD','ttd_a')}</th><th class="num mit">${H('TTM','ttm_a')}</th><th class="num">${H('Tokens','tokens_a')}</th><th class="num">${H('MCP','mcp_a')}</th><th class="num">${H('kubectl','kubectl_a')}</th><th class="num">${H('SQL','sql_a')}</th><th class="num">${H('Errors','errors_a')}</th><th class="num">${H('Blocked','blocked')}</th><th class="num">${H('Cost','cost_a')}</th><th>${H('Status','status')}</th></tr></thead><tbody>`;
  items.sort((x,y)=>(x.model+x.family+x.problem).localeCompare(y.model+y.family+y.problem)||x.attempt-y.attempt).forEach((a,i)=>{ const id='r'+i;
    at+=`<tr><td><button class="exp" data-t="${id}">+</button></td><td><span class="swatch" style="background:${colour(a.model)}"></span>${esc(a.model)}</td><td>${esc(FAM_LABEL[a.family])}<br><span class="small">${esc(a.agent)}</span></td><td>${esc(a.problem)}</td><td>${a.attempt}</td><td>${mark(a)}</td><td class="mit">${a.mitigation===true?'✓':a.mitigation===false?'✗':'–'}</td><td class="num">${fmtS(a.ttd_s)}</td><td class="num mit">${fmtS(a.ttm_s)}</td><td class="num">${fmt(tokTotal(a),0)}</td><td class="num">${a.n_mcp}</td><td class="num">${a.n_kubectl}</td><td class="num">${a.n_sql}</td><td class="num">${a.n_errors}</td><td class="num">${a.blocked_requests==null?'–':a.blocked_requests}</td><td class="num">${a.cost_usd==null?'–':'$'+a.cost_usd.toFixed(2)}</td><td class="small">${esc(a.run_status)} ${esc(a.incomplete_reason)}</td></tr>`;
    at+=`<tr id="${id}" class="row-exp hidden"><td></td><td colspan="16" data-i="${i}"></td></tr>`; });
  ATT_SORTED=items;
  document.getElementById('p-attempts').innerHTML=`<section class="card"><p class="muted">${items.length} attempts. <button id="exp-all">Expand all</button> Click + to open an attempt: fault, submitted diagnosis, judge verdict, every tool call in order with arguments, errors and the agent's words before each SQL query.</p>${at}</tbody></table></section>`;
  const fill = r => { const td=r.querySelector('td[data-i]'); if(!td || td.innerHTML) return; const a=ATT_SORTED[+td.dataset.i]; td.innerHTML='<span class="muted">loading…</span>';
    Promise.resolve(LOADER.steps(a)).then(d=>{ if(d){ if(d.steps) a.steps=d.steps; for(const k of ['submission','judge_reasoning','checks','dims','fault','tokens']) if(d[k]!=null) a[k]=d[k]; } td.innerHTML = detail(a); }).catch(e=>{ td.innerHTML = `<span class="muted">could not load the attempt: ${esc(String(e))}</span>`; }); };
  document.querySelectorAll('#p-attempts button.exp').forEach(b=>b.addEventListener('click',()=>{const r=document.getElementById(b.dataset.t); fill(r); r.classList.toggle('hidden'); b.textContent=r.classList.contains('hidden')?'+':'−';}));
  const expAll=document.getElementById('exp-all'); let expanded=new URLSearchParams(location.search).get('expand')==='1'; const setAll=()=>{document.querySelectorAll('#p-attempts .row-exp').forEach(r=>{ if(expanded) fill(r); r.classList.toggle('hidden',!expanded);}); document.querySelectorAll('#p-attempts button.exp').forEach(b=>b.textContent=expanded?'−':'+'); expAll.textContent=expanded?'Collapse all':'Expand all';}; expAll.addEventListener('click',()=>{expanded=!expanded; setAll();}); if(expanded) setAll();
}
function renderQueries(){ const items=ITEMS; const el=document.getElementById('p-tools'); el.innerHTML='<p class="muted">loading the queries…</p>';
  Promise.resolve(LOADER.queries(items)).then(sqls=>renderQueriesWith(items, sqls)).catch(e=>{ el.innerHTML=`<p class="muted">could not load the queries: ${esc(String(e))}</p>`; }); }
function renderQueriesWith(items, sqls){ const toolsAll={}; for(const a of items) for(const [t,c] of Object.entries(a.tools)) toolsAll[t]=(toolsAll[t]||0)+c;
  let tt=`<section class="card"><h3>MCP tool calls (${Object.values(toolsAll).reduce((x,y)=>x+y,0)} across ${items.length} attempts)</h3><table><thead><tr><th>${H('Tool','tool')}</th><th class="num">Calls</th><th class="num">Per attempt</th></tr></thead><tbody>`+Object.entries(toolsAll).sort((x,y)=>y[1]-x[1]).map(([t,c])=>`<tr><td>${esc(t)}</td><td class="num">${c}</td><td class="num">${(c/items.length).toFixed(1)}</td></tr>`).join('')+'</tbody></table></section>';
  tt+=`<section class="card"><h3>SQL queries (${sqls.length}) with the agent's words immediately before each</h3><table class="trace"><thead><tr><th>Model · arm · problem</th><th>${H("Agent's words before the query",'pre')}</th><th>SQL</th><th>Error</th></tr></thead><tbody>`+sqls.map(({a,s})=>`<tr><td>${esc(a.model)} · ${esc(FAM_LABEL[a.family])} · ${esc(a.problem)} #${a.attempt}</td><td>${esc(s.pre_text||'(no text before the call)')}</td><td class="sqlcol">${esc(s.sql)}</td><td class="res">${s.error?esc(s.error_text||'error'):'–'}</td></tr>`).join('')+'</tbody></table></section>';
  document.getElementById('p-tools').innerHTML=tt;
}
const TAB_RENDER = { 'p-model': renderByModel, 'p-arm': renderByArm, 'p-problem': renderByProblem, 'p-attempts': renderAttempts, 'p-tools': renderQueries,
  'p-tooluse': ()=>renderToolUse(ITEMS, REF_ITEMS), 'p-gaps': ()=>renderGaps(ITEMS, REF_ITEMS),
  'p-sql': ()=>{ const w=document.getElementById('ch-where'); if(w) w.textContent=filtersAsWhere(); else renderSqlTab(); } };
function renderActive(){ const active=document.querySelector('.panel.active'); if(active && DIRTY.has(active.id) && TAB_RENDER[active.id]){ const t0=performance.now(); TAB_RENDER[active.id](); DIRTY.delete(active.id); noteRender(`${active.querySelector('button')? '' : ''}tab rendered in ${Math.round(performance.now()-t0)} ms`); } }
function noteRender(txt){ const n=document.getElementById('render-note'); if(n) n.textContent = `${ITEMS.length} attempts in view · ${txt}`; }
function render(){ const t0=performance.now(); document.body.classList.toggle('hide-mit', !showMit()); const cur=currentPhase(); document.querySelectorAll('button.phase').forEach(b=>b.classList.toggle('active', b.dataset.phase===cur)); ITEMS=selected(); renderCore(ITEMS); for(const id of Object.keys(TAB_RENDER)) DIRTY.add(id); noteRender(`recomputed in ${Math.round(performance.now()-t0)} ms`); renderActive(); }
// ---------------------------------------------------------------- tool use
const sum = xs => xs.reduce((a,b)=>a+b,0);
function toolCounts(items, field){ const c={}; for(const a of items) for(const [t,n] of Object.entries(a[field]||{})) c[t]=(c[t]||0)+n; return c; }
function areaShare(items){ const out={}; for(const ar of AREAS) out[ar]=items.length? items.filter(a=>(a.areas||{})[ar]>0 || Object.keys(a.kverbs||{}).some(t=>(KV_EQUIV[t]||[])[0]===ar)).length/items.length : 0; return out; }
function topList(counts, n, denom){ return Object.entries(counts).sort((x,y)=>y[1]-x[1]).slice(0,n).map(([t,c])=>`${esc(t)} <span class="small">${(c/denom).toFixed(1)}</span>`).join(', '); }
function calls(a){ return a.n_mcp + a.n_kubectl + a.n_shell; }
function renderToolUse(items, ref){
  const el=document.getElementById('p-tooluse-body'); let out='';
  for(const m of models){ const mi=items.filter(a=>a.model===m); if(!mi.length) continue;
    const stockRef = ref.filter(a=>a.model===m&&a.family==='stock');
    let t=`<table><thead><tr><th>${H('Arm','arm')}</th><th class="num">${H('attempts','attempts')}</th><th class="num">${H('pass@1','pass1')}</th><th>${H('calls per attempt by kind (share of all calls)','calls_total')}</th><th class="num">${H('tool errors','toolerr')}</th><th>${H('areas queried (share of attempts)','areas')}</th><th>${H('most used tools (per attempt)','toptools')}</th><th class="num">skill read</th></tr></thead><tbody>`;
    const rows=[]; for(const f of fams){ const fi=mi.filter(a=>a.family===f); if(!fi.length) continue; const g=agg(fi);
      const n=fi.length; const sh=areaShare(fi); const tools=toolCounts(fi,'tools'); const kv=toolCounts(fi,'kverbs');
      const areaTxt = AREAS.filter(ar=>sh[ar]>0).map(ar=>`${esc(AREA_LABEL[ar]||ar)} <span class="small">${Math.round(100*sh[ar])}%</span>`).join(', ') || '<span class="muted">none</span>';
      const toolTxt = [topList(tools,5,n), topList(kv,5,n)].filter(Boolean).join('<br>') || '<span class="muted">no tool calls recorded</span>';
      const errs = sum(fi.map(a=>a.n_errors)), tot = sum(fi.map(calls));
      rows.push(`<tr class="tu-row" data-k="${esc(m)}|${f}"><td>${esc(FAM_LABEL[f])} <button class="exp tu-exp" data-t="tu-${models.indexOf(m)}-${f}">+</button></td><td class="num">${n}</td><td class="num">${pct(g.passes,g.scored)}</td><td class="small">${[['ClickStack',g.cs,g.shares.cs],['ClickHouse SQL',g.sqlmcp,g.shares.sqlmcp],['Prometheus/Loki/Jaeger',g.obsmcp,g.shares.obsmcp],['kubectl',g.kubectl,g.shares.kubectl],['other shell',g.shell,g.shares.shell]].filter(([,n])=>n>0).map(([l,n,sh])=>`${l} ${withShare(n,sh)}`).join(' · ')}<br>of which live probes ${fmt(mean(fi.map(a=>a.n_probe||0)))}, fixes ${fmt(mean(fi.map(a=>a.n_fix||0)))} per attempt; other shell: ${Object.entries(g.shellKinds).sort((x,y)=>y[1]-x[1]).map(([k,v])=>`${esc(k)} ${(v/fi.length).toFixed(1)}`).join(', ')||'–'}</td><td class="num">${pct(errs,tot)}</td><td class="small">${areaTxt}</td><td class="small">${toolTxt}</td><td class="num">${pct(fi.filter(a=>a.skill_read).length,n)}</td></tr>`);
      rows.push(`<tr id="tu-${models.indexOf(m)}-${f}" class="row-exp hidden"><td colspan="8">${toolDetail(fi, stockRef, f)}</td></tr>`); }
    t+=rows.join('')+'</tbody></table>';
    out+=`<section class="card"><h3><span class="swatch" style="background:${colour(m)}"></span>${esc(m)} <span class="small">${esc(mi[0].harness)}</span></h3><p class="muted">What each arm actually called, per attempt. Click + for every tool with its use in accepted versus rejected attempts.</p>${t}</section>`; }
  el.innerHTML = out || '<p class="muted">No attempts in view.</p>';
  el.querySelectorAll('button.tu-exp').forEach(b=>b.addEventListener('click',()=>{const r=document.getElementById(b.dataset.t); r.classList.toggle('hidden'); b.textContent=r.classList.contains('hidden')?'+':'−';}));
}
function toolDetail(fi, stockRef, f){
  const pass=fi.filter(a=>a.diagnosis===true), fail=fi.filter(a=>a.diagnosis===false);
  const per = (items, counts, t) => items.length? ((counts[t]||0)/items.length).toFixed(1) : '–';
  const row = (t, c, err, kindLabel) => `<tr><td>${esc(t)} <span class="small">${kindLabel}</span></td><td class="num">${c}</td><td class="num">${(c/fi.length).toFixed(1)}</td><td class="num">${per(pass, cp, t)}</td><td class="num">${per(fail, cf, t)}</td><td class="num">${err==null?'–':pct(err,c)}</td></tr>`;
  const tools=toolCounts(fi,'tools'), terr=toolCounts(fi,'tool_err'), kv=toolCounts(fi,'kverbs');
  let cp=Object.assign({}, toolCounts(pass,'tools'), toolCounts(pass,'kverbs')); let cf=Object.assign({}, toolCounts(fail,'tools'), toolCounts(fail,'kverbs'));
  let t=`<table><thead><tr><th>${H('tool','tool')}</th><th class="num">calls</th><th class="num">per attempt</th><th class="num">${H('per passing attempt','passfail')}</th><th class="num">per failing attempt</th><th class="num">${H('errors','toolerr')}</th></tr></thead><tbody>`;
  t+=Object.entries(tools).sort((x,y)=>y[1]-x[1]).map(([k,c])=>row(k,c,terr[k]||0,'MCP')).join('');
  t+=Object.entries(kv).sort((x,y)=>y[1]-x[1]).map(([k,c])=>row(k,c,null,(KV_EQUIV[k]||[])[0]==='fix'?'kubectl, fix':(KV_EQUIV[k]||[])[0]==='probe'?'kubectl, probe':'kubectl')).join('');
  t+='</tbody></table>';
  // comparison with the stock arm's passing attempts
  let cmp='';
  if(f!=='stock' && stockRef.length){ const sp=stockRef.filter(a=>a.diagnosis===true); const skv=toolCounts(sp,'kverbs'); const top=Object.entries(skv).filter(([k])=>!['fix'].includes((KV_EQUIV[k]||[])[0])).sort((x,y)=>y[1]-x[1]).slice(0,6);
    const shArm=areaShare(fi); const tier=dataTier(f);
    const never=AREAS.filter(ar=>shArm[ar]===0 && (f==='obs_only'? !OBS_MISSING.has(ar) : (tier==null || (AREA_MIN_TIER[ar]??0)<=tier)));
    const missing=AREAS.filter(ar=> f==='obs_only'? OBS_MISSING.has(ar) : (tier!=null && (AREA_MIN_TIER[ar]??0)>tier));
    cmp=`<p class="small">The same model's stock arm passed ${sp.length} attempt(s) here reading mostly ${top.map(([k,c])=>`${esc(k)} (${(c/Math.max(1,sp.length)).toFixed(1)}/attempt)`).join(', ')||'–'}.`+
        (never.length? ` This arm never queried ${never.map(ar=>esc(AREA_LABEL[ar])).join(', ')} although its tier ships ${never.length>1?'them':'it'}.`:'')+
        (missing.length? ` Not shipped at this tier: ${missing.map(ar=>esc(AREA_LABEL[ar])).join(', ')}.`:'')+`</p>`; }
  return cmp+t;
}
// ---------------------------------------------------------------- gaps against the stock arm
function rateBy(items){ const r={}; for(const a of items){ if(a.diagnosis===null) continue; const s=r[a.problem]=r[a.problem]||{p:0,n:0}; s.p+=a.diagnosis?1:0; s.n++; } return r; }
function modeCounts(items){ const c={}; for(const a of items) if(a.diagnosis===false) c[a.mode||'unknown']=(c[a.mode||'unknown']||0)+1; return c; }
function modeText(c){ const e=Object.entries(c).sort((x,y)=>y[1]-x[1]); return e.length? e.map(([k,v])=>`${esc(k)} ${v}`).join(', ') : '–'; }
function gapVerdict(f, tier, stockPass, armFail){
  // what the stock agent's successful runs read, and whether this arm could have / did read the same
  const skv=toolCounts(stockPass,'kverbs'); const stockTools=toolCounts(stockPass,'tools');
  const reads=Object.entries(skv).filter(([k])=>!['fix','meta','probe'].includes((KV_EQUIV[k]||[])[0]));
  const totalReads=sum(reads.map(([,c])=>c)); const probeReads=sum(stockPass.map(a=>a.n_probe||0));   // n_probe already counts kubectl exec/port-forward/run and shell probes
  const probeShare=probeReads/Math.max(1,totalReads+probeReads);
  const needed={}; for(const [k,c] of reads){ const eq=KV_EQUIV[k]; if(!eq) continue; const [ar,desc,mt]=eq; if(ar==='probe') continue; const n=needed[ar]=needed[ar]||{c:0,descs:new Set(),tier:mt}; n.c+=c; n.descs.add(desc); }
  for(const [t,c] of Object.entries(stockTools)){ const ar = /metric|alert/.test(t)?'metrics':/log/.test(t)?'logs':/trace|service|operation|dependency/.test(t)?'traces':null; if(ar){ const n=needed[ar]=needed[ar]||{c:0,descs:new Set(),tier:0}; n.c+=c; n.descs.add('stock telemetry ('+t+')'); } }
  const armAreas={}; for(const a of armFail) for(const [ar,c] of Object.entries(a.areas||{})) armAreas[ar]=(armAreas[ar]||0)+c;
  const avail = ar => f==='obs_only'? !OBS_MISSING.has(ar) : (tier==null || (AREA_MIN_TIER[ar]??0)<=tier);
  const notCaptured=Object.keys(needed).filter(ar=>!avail(ar)); const notQueried=Object.keys(needed).filter(ar=>avail(ar)&&!(armAreas[ar]>0)&&ar!=='meta');
  const modes=modeCounts(armFail);
  let kind, text;
  const probeNote = probeReads? ` The stock runs also used ${probeReads} live probe${probeReads===1?'':'s'} (${Math.round(100*probeShare)}% of their cluster reads).` : '';
  if(notCaptured.length && probeShare<0.5){ kind='not captured at this tier'; text=`the stock agent read ${notCaptured.map(ar=>AREA_LABEL[ar]).join(' and ')}, which ${f==='obs_only'?'SREGym\'s Prometheus, Loki and Jaeger do not hold':'arrives only at tier '+Math.max(...notCaptured.map(ar=>AREA_MIN_TIER[ar]))}.`+probeNote; }
  else if(probeShare>=0.25){ kind='live probing'; text=`the stock agent's accepted runs leaned on live probes (${probeReads} of ${totalReads+probeReads} cluster reads: kubectl exec, port-forward, debug pods, curl or dig from a pod); the telemetry has no stand-in for those.`; }
  else if(notQueried.length){ kind='captured but not queried'; text=`this arm's failing attempts never read ${notQueried.map(ar=>AREA_LABEL[ar]).join(' or ')}, which the stock agent used (${notQueried.map(ar=>[...needed[ar].descs][0]).join('; ')}).`+probeNote; }
  else { kind='right data, wrong conclusion'; text=`this arm read the same areas the stock agent used (${Object.keys(needed).map(ar=>AREA_LABEL[ar]).join(', ')||'–'}) and still failed: ${modeText(modes)}.`+probeNote; }
  return {kind, text, needed, notCaptured, notQueried, modes, stockReads: reads.sort((x,y)=>y[1]-x[1]).slice(0,6)};
}
function renderGaps(items, ref){
  const el=document.getElementById('p-gaps-body'); let out='';
  for(const m of models){ const mi=items.filter(a=>a.model===m); if(!mi.length) continue;
    const stock=ref.filter(a=>a.model===m&&a.family==='stock'); const sr=rateBy(stock); const sg=agg(stock);
    out+=`<section class="card"><h3><span class="swatch" style="background:${colour(m)}"></span>${esc(m)} <span class="small">${esc(mi[0].harness)}</span></h3>`;
    if(!stock.length){ out+=`<p class="muted">No stock-arm attempts for this model in the selected campaigns, so there is no reference to measure gaps against.${H('','stockref')}</p>`; }
    else out+=`<p class="muted">Reference${H('','stockref')}: stock arm pass@1 ${pct(sg.passes,sg.scored)} over ${sg.scored} scored attempts, failure modes ${modeText(modeCounts(stock))}.</p>`;
    for(const f of fams){ const fi=mi.filter(a=>a.family===f); if(!fi.length) continue; const g=agg(fi); const ar=rateBy(fi); const tier=dataTier(f);
      if(f==='stock'){ // where other arms of the same model beat stock
        const others=mi.filter(a=>a.family!=='stock'); const orr={}; for(const a of others){ if(a.diagnosis===null) continue; const k=a.family+'|'+a.problem; const s=orr[k]=orr[k]||{p:0,n:0}; s.p+=a.diagnosis?1:0; s.n++; }
        const beat=Object.entries(orr).filter(([k,s])=>s.p/s.n>=0.5 && ar[k.split('|')[1]] && ar[k.split('|')[1]].p/ar[k.split('|')[1]].n<0.5);
        out+=`<details class="card"><summary><b>${esc(FAM_LABEL[f])}</b> · pass@1 ${pct(g.passes,g.scored)} · failure modes: ${modeText(modeCounts(fi))}</summary><p class="small">Problems where stock failed (under half of its attempts) but a telemetry arm of the same model passed: ${beat.length? beat.map(([k])=>`${esc(k.split('|')[1])} <span class="muted">(${esc(FAM_LABEL[k.split('|')[0]])})</span>`).join('; ') : 'none in view'}.</p>${stockFailTable(fi)}</details>`; continue; }
      if(!stock.length){ out+=`<details class="card"><summary><b>${esc(FAM_LABEL[f])}</b> · pass@1 ${pct(g.passes,g.scored)} · failure modes: ${modeText(modeCounts(fi))}</summary>${stockFailTable(fi)}</details>`; continue; }
      const probs=Object.keys(ar).filter(p=>sr[p]); const gaps=probs.filter(p=>sr[p].p/sr[p].n>=0.5 && ar[p].p/ar[p].n<0.5); const gains=probs.filter(p=>sr[p].p/sr[p].n<0.5 && ar[p].p/ar[p].n>=0.5); const shared=probs.filter(p=>sr[p].p/sr[p].n<0.5 && ar[p].p/ar[p].n<0.5);
      const d = (g.pass1!=null&&sg.pass1!=null)? Math.round(100*(g.pass1-sg.pass1)) : null;
      // per gap problem
      const verdicts=gaps.map(p=>{ const sp=stock.filter(a=>a.problem===p&&a.diagnosis===true); const af=fi.filter(a=>a.problem===p&&a.diagnosis===false); return [p, gapVerdict(f, tier, sp, af), sp, af]; });
      const kinds={}; for(const [,v] of verdicts) kinds[v.kind]=(kinds[v.kind]||0)+1;
      // what would have helped: areas the stock runs needed across gap problems
      const help={}; for(const [p,v] of verdicts) for(const [ar,n] of Object.entries(v.needed)){ const h=help[ar]=help[ar]||{probs:0,queried:0,tier:n.tier,descs:new Set()}; h.probs++; [...n.descs].forEach(x=>h.descs.add(x)); if(!v.notQueried.includes(ar)&&!v.notCaptured.includes(ar)) h.queried++; }
      const avail = ar => f==='obs_only'? !OBS_MISSING.has(ar) : (tier==null || (AREA_MIN_TIER[ar]??0)<=tier);
      const helpRows=Object.entries(help).sort((x,y)=>y[1].probs-x[1].probs).map(([ar,h])=>`<tr><td>${esc(AREA_LABEL[ar]||ar)}<br><span class="small">${[...h.descs].slice(0,3).map(esc).join('; ')}</span></td><td class="num">${h.probs}</td><td>${avail(ar)? 'yes' : (f==='obs_only'? 'no (not in stock telemetry)' : `no (tier ${AREA_MIN_TIER[ar]} needed, this arm has tier ${tier})`)}</td><td class="num">${h.queried} of ${h.probs}</td></tr>`).join('');
      const summary=`<p>pass@1 <b>${pct(g.passes,g.scored)}</b> against the stock arm's ${pct(sg.passes,sg.scored)}${d==null?'':` (${d>0?'+':''}${d} pts)`}. On the ${probs.length} problems both arms were scored on: <b>${gaps.length} gap${gaps.length===1?'':'s'}</b>, ${gains.length} gain${gains.length===1?'':'s'}, ${shared.length} shared failure${shared.length===1?'':'s'}.`+
        (gaps.length? ` Gap reasons: ${Object.entries(kinds).sort((x,y)=>y[1]-x[1]).map(([k,v])=>`${esc(k)} ×${v}`).join(', ')}.`:'')+
        ` Failure modes across all ${fi.filter(a=>a.diagnosis===false).length} rejected attempts: ${modeText(modeCounts(fi))}.</p>`;
      let gt='';
      if(gaps.length){ gt=`<table class="rows-sep"><thead><tr><th>${H('gap problem','gaps')}</th><th>${H('fault','fault')}</th><th>this arm</th><th>stock</th><th>${H('what the stock agent read','toptools')}</th><th>this arm read</th><th>${H('reading','verdict')}</th></tr></thead><tbody>`+
        verdicts.map(([p,v,sp,af])=>{ const ci=fi.filter(a=>a.problem===p).sort((x,y)=>x.attempt-y.attempt); const si=stock.filter(a=>a.problem===p).sort((x,y)=>x.attempt-y.attempt); const armAreas={}; for(const a of af) for(const [ar,c] of Object.entries(a.areas||{})) armAreas[ar]=(armAreas[ar]||0)+c;
          return `<tr><td>${esc(p)}</td><td class="small fault">${esc((ci.find(a=>a.fault)||{}).fault||'')}</td><td>${ci.map(mark).join(' ')}<br><span class="small">${modeText(modeCounts(ci))}</span></td><td>${si.map(mark).join(' ')}</td><td class="small">${v.stockReads.map(([k,c])=>`${esc(k)} ${c}`).join(', ')||'–'}${sum(sp.map(a=>a.n_probe||0))?`; probes ${sum(sp.map(a=>a.n_probe||0))}`:''}</td><td class="small">${Object.entries(armAreas).sort((x,y)=>y[1]-x[1]).map(([ar,c])=>`${esc(AREA_LABEL[ar]||ar)} ${c}`).join(', ')||'no telemetry calls'}</td><td class="small"><b>${esc(v.kind)}</b>: ${esc(v.text)}</td></tr>`; }).join('')+'</tbody></table>'; }
      const gainTxt = gains.length? `<p class="small"><b>Gains over stock</b>${H('','gaps')}: ${gains.map(p=>`${esc(p)} (${ar[p].p}/${ar[p].n} vs stock ${sr[p].p}/${sr[p].n})`).join('; ')}.</p>` : '';
      const helpTxt = helpRows? `<h4>${H('What would have helped','helpful')}</h4><table><thead><tr><th>telemetry the stock agent relied on</th><th class="num">gap problems</th><th>shipped at this tier</th><th class="num">queried by this arm's failing attempts</th></tr></thead><tbody>${helpRows}</tbody></table>` : '';
      out+=`<details class="card" ${gaps.length?'open':''}><summary><b>${esc(FAM_LABEL[f])}</b> · pass@1 ${pct(g.passes,g.scored)} vs stock ${pct(sg.passes,sg.scored)} · ${gaps.length} gap(s), ${gains.length} gain(s)</summary>${summary}${gt}${gainTxt}${helpTxt}</details>`; }
    out+='</section>'; }
  el.innerHTML = out || '<p class="muted">No attempts in view.</p>';
}
function stockFailTable(fi){ const fails=fi.filter(a=>a.diagnosis===false); if(!fails.length) return '<p class="small">No rejected attempts in view.</p>';
  const byP={}; for(const a of fails) (byP[a.problem]=byP[a.problem]||[]).push(a);
  return `<table class="rows-sep"><thead><tr><th>problem</th><th>fault</th><th>rejected attempts</th><th>${H('failure modes','modes')}</th><th>${H('tools used in them','toptools')}</th></tr></thead><tbody>`+Object.entries(byP).map(([p,as])=>`<tr><td>${esc(p)}</td><td class="small fault">${esc((as.find(a=>a.fault)||{}).fault||'')}</td><td>${as.sort((x,y)=>x.attempt-y.attempt).map(mark).join(' ')}</td><td class="small">${modeText(modeCounts(as))}</td><td class="small">${[topList(toolCounts(as,'tools'),4,as.length), topList(toolCounts(as,'kverbs'),4,as.length)].filter(Boolean).join('<br>')||'–'}</td></tr>`).join('')+'</tbody></table>'; }
// ---------------------------------------------------------------- SQL tab
const sqlStr = v => "'" + String(v).replace(/\\/g, "\\\\").replace(/'/g, "\\'") + "'";
function filtersAsWhere(){ const on = cls => [...document.querySelectorAll(cls+':checked')].map(x=>x.value);
  const parts=[];
  const all = (cls, col, universe) => { const v=on(cls); if(v.length && v.length<universe.length) parts.push(`${col} IN (${v.map(sqlStr).join(', ')})`); };
  all('.f-camp','campaign',campaigns); all('.f-model','model',models); all('.f-fam','family',fams); all('.f-har','harness',harnesses);
  const inView=[...new Set(ITEMS.map(a=>a.problem))]; if(inView.length && inView.length<problems.length) parts.push(`problem IN (${inView.map(sqlStr).join(', ')})`);
  return parts.length? parts.join('\n  AND ') : '1';
}
const PRESETS = [
 ['pass@1 by model and arm', `SELECT model, family, count() AS attempts, countIf(diagnosis) AS passes, round(100 * countIf(diagnosis) / count()) AS pass1_pct,
       round(avgIf(ttd_s, diagnosis)) AS ttd_when_correct_s, round(avg(n_mcp), 1) AS mcp_per_attempt, round(avg(n_kubectl), 1) AS kubectl_per_attempt
FROM {scored}
WHERE {where}
GROUP BY model, family ORDER BY model, family`],
 ['pass@k (any of the attempts) by model and arm', `SELECT model, family, count() AS cells, countIf(any_pass) AS cells_passed, round(100 * countIf(any_pass) / count()) AS passk_pct
FROM (SELECT model, family, problem, max(diagnosis) AS any_pass FROM {scored} WHERE {where} GROUP BY model, family, problem)
GROUP BY model, family ORDER BY model, family`],
 ['failure modes by arm', `SELECT family, mode, count() AS rejected
FROM {scored}
WHERE {where} AND diagnosis = false
GROUP BY family, mode ORDER BY family, rejected DESC`],
 ['tool use by arm: calls per attempt and share', `SELECT family, tool, kind, count() AS calls, round(count() / uniq(run_dir), 1) AS per_attempt, round(100 * countIf(error) / count()) AS error_pct
FROM {calls} FINAL
WHERE run_dir IN (SELECT run_dir FROM {scored} WHERE {where})
GROUP BY family, tool, kind ORDER BY family, calls DESC`],
 ['gap problems: stock passes, arm fails (same model)', `WITH stock AS (SELECT model, problem, countIf(diagnosis) / count() AS stock_rate FROM {scored} WHERE family = 'stock' GROUP BY model, problem)
SELECT a.model, a.family, a.problem, round(countIf(a.diagnosis) / count(), 2) AS arm_rate, round(any(s.stock_rate), 2) AS stock_rate, groupArray(a.mode) AS modes
FROM {scored} AS a INNER JOIN stock AS s ON a.model = s.model AND a.problem = s.problem
WHERE {where} AND a.family != 'stock'
GROUP BY a.model, a.family, a.problem
HAVING arm_rate < 0.5 AND stock_rate >= 0.5
ORDER BY a.model, a.family, a.problem`],
 ['telemetry areas queried per attempt, by arm', `SELECT family, area, round(count() / uniq(run_dir), 1) AS calls_per_attempt, round(100 * uniq(run_dir) / (SELECT uniq(run_dir) FROM {scored} WHERE {where} AND family = t.family)) AS attempts_touching_pct
FROM {calls} FINAL AS t
WHERE kind = 'mcp' AND run_dir IN (SELECT run_dir FROM {scored} WHERE {where})
GROUP BY family, area ORDER BY family, calls_per_attempt DESC`],
 ['every SQL query the agents wrote that errored', `SELECT model, family, problem, attempt, left(sql, 200) AS sql, left(error_text, 120) AS error
FROM {calls} FINAL
WHERE sql != '' AND error AND run_dir IN (SELECT run_dir FROM {scored} WHERE {where})
ORDER BY model, family, problem LIMIT 500`],
 ['saturation: problems every kubectl cell solves', `SELECT problem, round(100 * countIf(diagnosis) / count()) AS pass_pct, min(cell_rate) AS weakest_cell
FROM (SELECT problem, model, family, diagnosis, countIf(diagnosis) OVER (PARTITION BY problem, model, family) / count() OVER (PARTITION BY problem, model, family) AS cell_rate
      FROM {scored} WHERE {where} AND (family = 'stock' OR family LIKE 'kubectl_%'))
GROUP BY problem HAVING pass_pct >= 95 AND weakest_cell >= 0.67 ORDER BY problem`],
];
function renderSqlTab(){
  const el=document.getElementById('p-sql'); if(!el || !CH || !(CH.endpoint || CH.proxy)) { if(el) el.innerHTML='<p class="muted">No ClickHouse endpoint was configured when this page was built.</p>'; return; }
  const saved = (()=>{ try { return JSON.parse(localStorage.getItem('sregym-ch')||'{}'); } catch(e){ return {}; } })();
  const schema = Object.entries(CH.schema||{}).map(([t,cols])=>`<details><summary><code>${esc(CH.database)}.${esc(t)}</code> <span class="small">${cols.length} columns</span></summary><table><tbody>${cols.map(([c,ty])=>`<tr><td><code>${esc(c)}</code></td><td class="small">${esc(ty)}</td></tr>`).join('')}</tbody></table></details>`).join('');
  el.innerHTML = `<section class="card"><h3>Query the results in ClickHouse${H('','sqltab')}</h3>
  ${CH.proxy? `<p class="small">Queries run on the server with its configured read-only user; nothing is entered here.</p>` : `<div class="sqlconn"><label>endpoint <input id="ch-endpoint" size="48" value="${esc(saved.endpoint||CH.endpoint)}"></label> <label>user <input id="ch-user" size="18" value="${esc(saved.user||'clickstack_readonly')}"></label> <label>password <input id="ch-pass" type="password" size="18" value="${esc(saved.pass||'')}"></label> <label><input type="checkbox" id="ch-remember" ${saved.pass?'checked':''}> remember in this browser</label></div>`}
  <p class="small">Loaded by <code>clickstack/load_results.py</code> into <code>${esc(CH.database)}</code>: <code>attempts</code> (one row per attempt), <code>tool_calls</code> (one row per tool call), <code>problem_sets</code>, and the views <code>attempts_scored</code>, <code>pass_rates</code> and <code>cells</code>. Query the tables with <code>FINAL</code> or use the views.</p>
  <div class="sqlgrid"><div><label class="small">${H('current filters as SQL','sqlwhere')}</label><textarea id="ch-where" rows="4" readonly></textarea></div>
  <div><label class="small">preset</label> <select id="ch-preset"><option value="">choose a query</option>${PRESETS.map((p,i)=>`<option value="${i}">${esc(p[0])}</option>`).join('')}</select><br>
  <textarea id="ch-sql" rows="12" spellcheck="false" placeholder="SELECT ... FROM ${esc(CH.database)}.attempts_scored WHERE ..."></textarea><br>
  <button id="ch-run">Run</button> <span id="ch-status" class="small muted"></span></div></div>
  <div id="ch-result"></div></section><section class="card"><h3>Schema</h3>${schema}</section>`;
  const whereBox=document.getElementById('ch-where'); whereBox.textContent=filtersAsWhere();
  const T = Object.assign({scored: `${CH.database}.attempts_scored`, calls: `${CH.database}.tool_calls`}, CH.tables||{});
  document.getElementById('ch-preset').addEventListener('change', e=>{ const p=PRESETS[+e.target.value]; if(p) document.getElementById('ch-sql').value = p[1].replace(/\{scored\}/g, T.scored).replace(/\{calls\}/g, T.calls).replace(/\{db\}/g, CH.database).replace(/\{where\}/g, filtersAsWhere()); });
  document.getElementById('ch-run').addEventListener('click', runSql);
  document.getElementById('ch-sql').addEventListener('keydown', e=>{ if((e.metaKey||e.ctrlKey) && e.key==='Enter') runSql(); });
}
async function runSql(){
  const sql=document.getElementById('ch-sql').value; const status=document.getElementById('ch-status'); const out=document.getElementById('ch-result');
  if(!sql.trim()){ status.textContent='nothing to run'; return; }
  status.textContent='running…'; out.innerHTML='';
  const t0=performance.now();
  try {
    let r;
    if(CH.proxy){ r = await fetch(CH.proxy, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({sql})}); }
    else {
      const endpoint=document.getElementById('ch-endpoint').value.trim().replace(/\/$/,''); const user=document.getElementById('ch-user').value.trim(); const pass=document.getElementById('ch-pass').value;
      try { localStorage.setItem('sregym-ch', JSON.stringify(document.getElementById('ch-remember').checked? {endpoint,user,pass} : {endpoint,user})); } catch(e){}
      const params=new URLSearchParams({add_http_cors_header:'1', default_format:'JSONCompact', max_result_rows:'2000', result_overflow_mode:'break', max_execution_time:'60', user, password:pass});
      r = await fetch(`${endpoint}/?${params}`, {method:'POST', headers:{'Content-Type':'text/plain'}, body: sql});
    }
    const text = await r.text();
    if(!r.ok){ status.textContent=`error (HTTP ${r.status})`; out.innerHTML=`<pre class="pre" style="color:var(--bad)">${esc(text.slice(0,2000))}</pre>`; return; }
    const d = JSON.parse(text); const cols=d.meta.map(m=>m.name);
    out.innerHTML = `<table><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${d.data.map(row=>`<tr>${row.map(v=>`<td>${esc(v==null?'∅':(typeof v==='object'? JSON.stringify(v) : v))}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    status.textContent = `${d.rows} row(s)${d.rows_before_limit_at_least && d.rows_before_limit_at_least>d.rows? ` of at least ${d.rows_before_limit_at_least}`:''} in ${Math.round(performance.now()-t0)} ms${d.statistics? ` · ${d.statistics.rows_read.toLocaleString()} rows read` : ''}`;
  } catch(e){ status.textContent='request failed'; out.innerHTML=`<pre class="pre" style="color:var(--bad)">${esc(String(e))}\nIf the endpoint is right, the browser may have refused a cross-origin request; the ClickHouse HTTP interface answers browser requests when add_http_cors_header=1 is set, which this page sends.</pre>`; }
}
// ---------------------------------------------------------------- method
function renderMethod(){
  const metric=[['attempts','attempts'],['pass@1','pass1'],['pass@k','passk'],['mitigation','mitigation'],['mean TTD','ttd'],['TTD when correct','ttdc'],['mean TTM','ttm'],['tokens','tokens'],['MCP calls','mcp'],['kubectl calls','kubectl'],['SQL share','sql'],['tool errors','errors'],['cost','cost'],['saturated problems','saturated'],['balanced problems','balanced'],['problem set','set'],['calls per attempt','calls'],['areas queried','areas'],['gaps and gains','gaps'],['gap reading','verdict'],['what would have helped','helpful']];
  let h=`<section class="card"><h3>How to read the numbers</h3><table><tbody>${metric.map(([l,k])=>`<tr><th style="width:220px">${esc(l)}</th><td>${esc(HELP[k])}</td></tr>`).join('')}</tbody></table></section>`;
  h+=`<section class="card"><h3>Arms and tiers</h3><p>${esc(HELP.arm)}</p><p>${esc(HELP.harness)}</p><table><thead><tr><th>telemetry area</th><th>first shipped at tier</th></tr></thead><tbody>${AREAS.map(ar=>`<tr><td>${esc(AREA_LABEL[ar])}</td><td>${AREA_MIN_TIER[ar]}</td></tr>`).join('')}</tbody></table><p class="small">Tier 4 ships the same data as tier 3; it adds investigation rules to the skill. The stock-telemetry arm (Prometheus, Loki, Jaeger) holds application logs, metrics and traces only.</p></section>`;
  h+=`<section class="card"><h3>Failure modes</h3><p>Derived for every rejected attempt from the judge's checklist (D1 fault localisation, D2 fault characterisation, D3 scope precision; three questions each) and the submission text.</p><table><tbody>${Object.entries(FAILURE_MODES).map(([k,v])=>`<tr><th style="width:220px">${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</tbody></table></section>`;
  h+=`<section class="card"><h3>kubectl reads and their telemetry equivalents</h3><p class="small">How the gap analysis maps what the stock agent did to what a telemetry arm could have queried.</p><table><thead><tr><th>kubectl</th><th>area</th><th>equivalent</th><th>tier</th></tr></thead><tbody>${Object.entries(KV_EQUIV).sort().map(([k,[ar,d,t]])=>`<tr><td>${esc(k)}</td><td>${esc(ar)}</td><td>${esc(d)}</td><td>${t==null?'–':t}</td></tr>`).join('')}</tbody></table></section>`;
  if(NOTES_HTML) h+=`<section class="card"><h3>Analyst notes</h3>${NOTES_HTML}</section>`;
  document.getElementById('p-method').innerHTML=h;
}
function problemTable(items, cols, key){ const label=k=> key==='family'? FAM_LABEL[k] : k; let t='<table class="rows-sep"><thead><tr><th>Problem</th>'+cols.filter(c=>items.some(a=>a[key]===c)).map(c=>`<th>${esc(label(c))}</th>`).join('')+'</tr></thead><tbody>';
  for(const p of problems){ const pi=items.filter(a=>a.problem===p); if(!pi.length) continue; t+=`<tr><th>${esc(p)}</th>`+cols.filter(c=>items.some(a=>a[key]===c)).map(c=>{const cc=pi.filter(a=>a[key]===c).sort((x,y)=>x.attempt-y.attempt); return `<td>${cc.map(mark).join(' ')||'<span class="st muted">–</span>'}</td>`;}).join('')+'</tr>'; } return t+'</tbody></table>'; }
function detail(a){ const steps=a.steps.map(s=>`<tr><td>${s.i}</td><td>${esc(s.kind)}</td><td>${esc(s.tool)}</td><td>${esc(s.sql||s.args)}</td><td>${s.dur_s==null?'':s.dur_s+' s'}</td><td class="res">${s.error?'<span class="st bad">error</span> '+esc(s.error_text):''}</td><td class="small">${esc(s.pre_text||'')}</td></tr>`).join('');
  const tok=a.tokens? Object.entries(a.tokens).filter(([k,v])=>v!=null).map(([k,v])=>`${k.replace('_tokens','')} ${Number(v).toLocaleString()}`).join(', ') : '–';
  return `<div class="small">ClickStack MCP server: ${esc(a.mcp_version||'–')}${a.n_scripted? ` · ${a.n_scripted} MCP call(s) made through a client the agent wrote itself (shell), counted as MCP calls`:''}</div><div class="pre"><b>Fault (ground truth):</b> ${esc(a.fault||'–')}</div><div class="pre"><b>Submitted diagnosis:</b> ${esc(a.submission||'–')}</div><details><summary>Judge verdict and checklist</summary><div class="pre">${esc(a.judge_reasoning||'–')}</div></details><p class="small">tokens: ${tok} · agent wall ${fmtS(a.agent_wall_s)} · turns ${a.turns||'–'} · skill read: ${a.skill_read===true?'yes':a.skill_read===false?'no':'–'} · artifacts: ${esc(a.run_dir)}</p><table class="trace"><thead><tr><th>#</th><th>kind</th><th>tool</th><th>arguments / command / SQL</th><th>took</th><th>result</th><th>agent's words before</th></tr></thead><tbody>${steps}</tbody></table>`; }
function exportCsv(){ const items=selected(); const cols=['campaign','model','harness','agent','family','tier','problem','attempt','diagnosis','composite','mitigation','ttd_s','ttm_s','tokens_total','n_mcp','n_kubectl','n_sql','n_errors','blocked_requests','cost_usd','run_status','submission'];
  const rows=[cols.join(',')].concat(items.map(a=>cols.map(c=>{let v=c==='tokens_total'? tokTotal(a) : a[c]; v= v==null? '' : String(v).replace(/"/g,'""'); return /[",\n]/.test(v)? `"${v}"`:v;}).join(','))); const blob=new Blob([rows.join('\n')],{type:'text/csv'}); const u=URL.createObjectURL(blob); const l=document.createElement('a'); l.href=u; l.download='sregym-clickstack-attempts.csv'; l.click(); URL.revokeObjectURL(u); }
// filters UI
const fl=document.getElementById('filters');
fl.innerHTML = `<div class="phases">${Object.entries(PHASES).map(([k,ph])=>`<button type="button" class="phase" data-phase="${k}">${esc(ph.label)}</button>`).join('')}${H('','phases')}</div><div class="grp"><details class="camps"><summary><b>campaigns</b>${H('','campaign')}</summary>${campaigns.map(c=>`<label><input type="checkbox" class="f-camp" value="${esc(c)}" checked> ${esc(c)}</label>`).join('')}</details></div><span class="sep"></span><div class="grp"><b>Model</b>${H('','models_default')} ${models.map(m=>`<label><input type="checkbox" class="f-model" value="${esc(m)}" ${defaultModel(m)?'checked':''}> <span class="swatch" style="background:${colour(m)}"></span>${esc(m)}</label>`).join('')}</div><span class="sep"></span>`+
 `<div class="grp"><b>Arm</b>${H('','arm')} ${fams.map(f=>`<label><input type="checkbox" class="f-fam" value="${f}" checked> ${esc(FAM_LABEL[f])}</label>`).join('')}</div><span class="sep"></span>`+
 `<div class="grp"><b>Harness</b>${H('','harness')} ${harnesses.map(h=>`<label><input type="checkbox" class="f-har" value="${h}" checked> ${esc(h)}</label>`).join('')}</div><span class="sep"></span>`+
 `<div class="grp"><b>Problem set</b>${H('','set')} <select id="f-set"><option value="">all</option>${Object.keys(SETS).filter(k=>SETS[k].some(p=>problems.includes(p))).map(k=>`<option value="${esc(k)}">${esc(k)} (${SETS[k].filter(p=>problems.includes(p)).length} in data)</option>`).join('')}</select></div>`+
 `<div class="grp"><details id="f-problems-box"><summary><b>Problems</b>${H('','problems_pick')} <span class="small" id="f-prob-count"></span></summary><div class="probs"><div class="probs-actions"><button type="button" id="prob-all">all</button> <button type="button" id="prob-none">none</button> <button type="button" id="prob-nosat">exclude saturated</button></div>${problems.map(p=>`<label><input type="checkbox" class="f-prob" value="${esc(p)}" checked> ${esc(p)}</label>`).join('')}</div></details></div>`+
 `<div class="grp"><input type="search" id="f-text" placeholder="search problem, model, diagnosis text"></div><div class="grp"><label><input type="checkbox" id="f-balanced"> balanced problems only</label>${H('','balanced')}</div><div class="grp"><label><input type="checkbox" id="f-mit" checked> hide mitigation</label>${H('','mit_hide')}</div><div class="grp"><label><input type="checkbox" id="f-sat" checked> hide saturated problems</label>${H('','saturated')} <span class="small">judged on</span> <select id="f-sat-ref"><option value="kubectl" selected>kubectl arms</option><option value="stock">stock arm</option><option value="all">selected arms</option><option value="telemetry">telemetry-only arms</option></select>${H('','sat_ref')} <span class="small">pass rate ≥</span> <select id="f-sat-thr"><option value="0.9">90%</option><option value="0.95" selected>95%</option><option value="1">100%</option></select> <span class="small">and weakest cell ≥</span> <select id="f-sat-cell"><option value="0">any</option><option value="0.67" selected>67%</option><option value="1">100%</option></select></div><div class="grp"><button id="f-csv">Export CSV</button></div>`;
document.querySelectorAll('.f-model,.f-fam,.f-har,.f-camp,.f-prob,#f-sat,#f-sat-thr,#f-sat-cell,#f-sat-ref,#f-set,#f-mit').forEach(x=>x.addEventListener('change',safeRender));
const setProbs = pred => { document.querySelectorAll('.f-prob').forEach(x=>{ x.checked = pred(x.value); }); safeRender(); };
document.querySelectorAll('button.phase').forEach(b=>b.addEventListener('click',()=>{ applyPhase(b.dataset.phase); safeRender(); }));
document.getElementById('prob-all').addEventListener('click',()=>setProbs(()=>true)); document.getElementById('prob-none').addEventListener('click',()=>setProbs(()=>false)); document.getElementById('prob-nosat').addEventListener('click',()=>setProbs(p=>!SATURATED.has(p))); document.getElementById('f-text').addEventListener('input',safeRender); document.getElementById('f-balanced').addEventListener('change',safeRender); document.getElementById('f-csv').addEventListener('click',exportCsv);
{ const q=new URLSearchParams(location.search); if(q.get('sat')==='1') document.getElementById('f-sat').checked=true; if(q.get('sat')==='0') document.getElementById('f-sat').checked=false; if(q.get('mit')==='1') document.getElementById('f-mit').checked=false; if(q.get('balanced')==='1') document.getElementById('f-balanced').checked=true;
  if(q.get('set') && SETS[q.get('set')]) document.getElementById('f-set').value=q.get('set');
  if(q.get('satref')) document.getElementById('f-sat-ref').value=q.get('satref');
  applyPhase(PHASES[q.get('phase')]? q.get('phase') : 'p1'); }  // ?sat=1, ?satref=stock, ?balanced=1 and ?set=sregym-lite pre-set the filters, for links
const tabs=[...document.querySelectorAll('nav.tabs button')]; const panels=[...document.querySelectorAll('.panel')];
const showTab=id=>{tabs.forEach(x=>x.classList.toggle('active',x.dataset.panel===id));panels.forEach(p=>p.classList.toggle('active',p.id===id));};
tabs.forEach(b=>b.addEventListener('click',()=>{showTab(b.dataset.panel); history.replaceState(null,'','#'+b.dataset.panel); try{ renderActive(); }catch(e){ console.error(e); }}));
if(panels.some(p=>p.id===location.hash.slice(1))) showTab(location.hash.slice(1));
function safeRender(){ const box=document.getElementById('js-error'); try{ render(); box.textContent=''; box.classList.add('hidden'); } catch(e){ box.textContent='The page could not render part of the report: '+(e&&e.stack||e); box.classList.remove('hidden'); console.error(e); } }
renderMethod();
safeRender();

}
global.initReport = initReport;
if (typeof module !== 'undefined' && module.exports) module.exports = { initReport };
})(typeof window !== 'undefined' ? window : globalThis);
