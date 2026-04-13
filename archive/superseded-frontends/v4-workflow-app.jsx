import { useState, useEffect, useCallback, useRef } from "react";
import { RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from "recharts";

// ═══ CORRECTED PHASE ORDER ═══
// 0: Classify (what kind of problem?)
// 1: Hypotheses (what do we think is true?)
// 2: Audit (what does the data say?)
// 3: Strategy (what should we do + why it will work?)
// 4: Monitor (observe strategy in action)
// 5: Report (final results + handoff)

const PHASES = [
  { id: 0, name: "Classify", icon: "🎯", col: "#0d9488" },
  { id: 1, name: "Hypotheses", icon: "🔬", col: "#7c3aed" },
  { id: 2, name: "Audit", icon: "🔍", col: "#0369a1" },
  { id: 3, name: "Strategy", icon: "🎯", col: "#b45309" },
  { id: 4, name: "Monitor", icon: "👁", col: "#059669" },
  { id: 5, name: "Report", icon: "📋", col: "#dc2626" },
];
const DQ = [
  { k: "frame", l: "Frame", p: 0 }, { k: "alt", l: "Alternatives", p: 1 },
  { k: "info", l: "Information", p: 2 }, { k: "val", l: "Values", p: 3 },
  { k: "reas", l: "Reasoning", p: 4 }, { k: "commit", l: "Commitment", p: 5 },
];
const newProject = (name = "New Project") => ({
  name, createdAt: new Date().toISOString(), phase: 0,
  brief: "", data: "", p0: null, hyps: null, gauntlet: null, sealed: false, sealDate: null,
  audit: null, auditRaw: null, strategy: null, strategyRaw: null,
  obs: {}, timerLogs: [], monitorLog: [],
  report: null, dq: {},
});
const K = "v4wf5";
const cc = { teal: "#0d9488", purple: "#7c3aed", blue: "#0369a1", amber: "#b45309", green: "#059669", red: "#dc2626", slate: "#64748b" };

// ═══ v4 SYSTEM CONTEXT ═══
const V4 = `You are executing the Universal Project Workflow v4.0 — a 6-phase decision engine with 30 frameworks, mathematical convergence gates, 3 learning loops, and a meta-learning engine.

ARCHITECTURE: 5-layer VSM (Operations→Coordination via Decision Dossier→Control/Audit→Intelligence→Policy). Spiral re-entry. 3 loops: Single-loop (PDCA within phases), Double-loop (re-entry when assumptions violated >2σ), Triple-loop (question the workflow every 3-5 projects).

30 FRAMEWORKS:
[#1]STEELMAN [#2]PREMORTEM [#3]DOUBLE_CRUX [#4]BAYES_LITE [#5]SISTÉMICO [#6]LADDER
[#7]FMEA [#8]HAZOP [#9]FTA [#10]Swiss_Cheese [#11]STPA
[#12]RPD [#13]Sensemaking [#14]Mental_Models [#15]Prospect_Theory [#16]Cynefin [#17]OODA
[#18]Chaos_Engineering [#19]Circuit_Breaker [#20]Canary [#21]HDD [#22]ODD
[#23]Ablation [#24]Causal_Inference [#25]EVOI [#26]Thompson_Sampling [#27]Information_Gain
[#28]Red_Teaming [#29]HRO [#30]Requisite_Variety

CONVERGENCE: BF>10, H_norm<0.15, D_KL<0.01, EVSI/ENBS>0, OBF sequential (z=4.56/3.23/2.63/2.28/2.04), Futility<15%, Real-options Seed→Expand→Scale, Thompson BETA.INV, Graduation>0.95/Drop<0.05, Brier, ECE, Portfolio ρ<0.5, MECE 5 tests.

EXIT GATES: P0:BF>10+DQ≥60%+gaps. P1:MECE+ρ<0.5+priors+sealed+DQ≥60%. P2:H_norm<0.15 or ENBS≤0+DQ≥60%. P3:strategy justified with evidence chains. P4:all monitored+trends visible. P5:Commitment≥70%+Agent Cards+Meta-Learner fed.

RE-ENTRY: R1:assumption>2σ→P1, R2:domain reclassified→P0, R3:scope→P0, R4:ρ>0.5→P1, R5:all futile→P1, R6:>50%futile→P2, R7:SLO 3+cycles→P3, R8:commitment<50%→P4.

MATURITY: 1=Ad Hoc, 2=Defined, 3=Quantitative, 4=Managed, 5=Optimizing (Brier<0.15, data flywheel).

Be specific, quantitative, actionable.`;
const V4J = V4 + "\n\nReturn ONLY valid JSON, no markdown fences, no preamble.";

// ═══ STORAGE ADAPTER — works in Claude artifacts (window.storage) or standalone (localStorage) ═══
const storage = {
  async get(key) {
    if (typeof window !== "undefined" && window.storage?.get) {
      return window.storage.get(key);
    }
    try { const v = localStorage.getItem(key); return v ? { value: v } : null; } catch { return null; }
  },
  async set(key, value) {
    if (typeof window !== "undefined" && window.storage?.set) {
      return window.storage.set(key, value);
    }
    try { localStorage.setItem(key, value); return { key, value }; } catch { return null; }
  },
  async del(key) {
    if (typeof window !== "undefined" && window.storage?.delete) {
      return window.storage.delete(key);
    }
    try { localStorage.removeItem(key); return true; } catch { return null; }
  }
};

// ═══ API ADAPTER — proxy-ready. Set API_BASE to your backend proxy URL for production ═══
const API_BASE = "https://api.anthropic.com"; // Change to "/api" or "https://your-proxy.com" for production
const API_MODEL = "claude-sonnet-4-6";

// ═══ STORE (with downstream invalidation) ═══

// State machine: which phases invalidate which when data changes
const INVALIDATES = {
  // Changing Phase 0 inputs invalidates everything downstream
  brief: ["p0","hyps","gauntlet","sealed","sealDate","audit","auditRaw","strategy","strategyRaw","sqi","obs","timerLogs","analysis","analysisRaw","report"],
  data: ["audit","auditRaw","strategy","strategyRaw","sqi"],
  p0: ["hyps","gauntlet","sealed","sealDate","audit","auditRaw","strategy","strategyRaw","sqi"],
  hyps: ["gauntlet","sealed","sealDate","audit","auditRaw","strategy","strategyRaw","sqi"],
  gauntlet: ["strategy","strategyRaw","sqi"],
  audit: ["strategy","strategyRaw","sqi"],
  auditRaw: ["strategy","strategyRaw","sqi"],
};

function useStore() {
  const [projects, setProjects] = useState([]);
  const [idx, setIdx] = useState(0);
  const [ready, setReady] = useState(false);
  const [dirty, setDirty] = useState(0);

  useEffect(() => { (async () => {
    try { const r = await storage.get(K); if (r?.value) { const d = JSON.parse(r.value); if (d.projects?.length) { setProjects(d.projects); setIdx(d.idx||0); setReady(true); return; } } } catch {}
    setProjects([]); setIdx(0); setReady(true);
  })(); }, []);

  useEffect(() => { if (!ready || dirty === 0) return; (async () => { try { await storage.set(K, JSON.stringify({ projects, idx })); } catch {} })(); }, [dirty]); // eslint-disable-line

  const mark = () => setDirty(d => d + 1);

  // Smart update: if changing an upstream field, nullify downstream outputs
  const [invalidated, setInvalidated] = useState(null);
  const u = useCallback((fn) => { setProjects(prev => {
    const n=[...prev]; const c={...(n[idx]||{})};
    const updated = typeof fn==="function" ? fn(c) : {...c,...fn};
    const changedKeys = Object.keys(typeof fn==="function" ? updated : fn);
    const toNullify = new Set();
    changedKeys.forEach(k => { (INVALIDATES[k]||[]).forEach(d => toNullify.add(d)); });
    const wiped = [];
    toNullify.forEach(k => { if (updated[k] != null && !changedKeys.includes(k)) { updated[k] = null; wiped.push(k); } });
    if (wiped.length > 0) setInvalidated(wiped);
    n[idx] = updated;
    return n;
  }); mark(); }, [idx]);

  const add = useCallback((p) => { setProjects(prev => { const n=[...prev,p]; setIdx(n.length-1); return n; }); mark(); }, []);
  const sw = useCallback((i) => { setIdx(i); mark(); }, []);
  const del = useCallback((i) => { setProjects(prev => { const n=prev.filter((_,j)=>j!==i); const ni=Math.max(0,Math.min(idx,n.length-1)); setIdx(ni); return n; }); mark(); }, [idx]);
  return { projects, idx, ready, u, add, sw, del, d: projects[idx]||null, invalidated, clearInvalidated: () => setInvalidated(null) };
}

// ═══ AI (with retry, AbortController timeout, error discrimination, schema validation) ═══
const MAX_RETRIES = 2;
const RETRY_DELAY = [1000, 3000];
const TIMEOUT_MS = 60000; // 60s timeout per request

// Error types for discrimination
const ERR = { AUTH: "auth", RATE: "rate", FORMAT: "format", NETWORK: "network", TIMEOUT: "timeout", SERVER: "server" };

function classifyError(status, msg) {
  if (msg?.includes("abort") || msg?.includes("timeout")) return ERR.TIMEOUT;
  if (status === 401 || status === 403) return ERR.AUTH;
  if (status === 429) return ERR.RATE;
  if (status === 400 || status === 422) return ERR.FORMAT;
  if (status >= 500) return ERR.SERVER;
  return ERR.NETWORK;
}

async function _fetch(prompt, sys, maxTok) {
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const r = await fetch(`${API_BASE}/v1/messages`, {
        method: "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: API_MODEL, max_tokens: maxTok, system: sys, messages: [{ role: "user", content: prompt }] })
      });
      clearTimeout(timer);
      const errType = classifyError(r.status);
      if (errType === ERR.AUTH) return { ok: false, text: "Authentication failed — configure API proxy for production", errType };
      if (errType === ERR.RATE || errType === ERR.SERVER) {
        if (attempt < MAX_RETRIES) { await new Promise(r => setTimeout(r, RETRY_DELAY[attempt])); continue; }
        return { ok: false, text: `${errType} error (${r.status}) after ${MAX_RETRIES+1} attempts`, errType };
      }
      const j = await r.json();
      const text = j.content?.[0]?.text || "";
      if (!text) return { ok: false, text: "Empty response from AI", errType: ERR.FORMAT };
      return { ok: true, text };
    } catch(e) {
      clearTimeout(timer);
      const errType = classifyError(0, e.message);
      if (attempt < MAX_RETRIES && errType !== ERR.AUTH) { await new Promise(r => setTimeout(r, RETRY_DELAY[attempt])); continue; }
      return { ok: false, text: `${errType}: ${e.message}`, errType };
    }
  }
  return { ok: false, text: "Max retries exceeded", errType: ERR.NETWORK };
}

// ═══ JSON SCHEMA VALIDATION (structural, not just key-checking) ═══

function validateSchema(obj, schema) {
  // schema: { key: "string"|"number"|"boolean"|"array"|"object"|{type,items,required} }
  if (!obj || typeof obj !== "object") return ["Response is not a JSON object"];
  const errors = [];
  for (const [key, rule] of Object.entries(schema)) {
    if (!(key in obj)) { errors.push(`Missing required field: ${key}`); continue; }
    const val = obj[key];
    const type = typeof rule === "string" ? rule : rule.type;
    if (type === "array") {
      if (!Array.isArray(val)) { errors.push(`${key} should be array, got ${typeof val}`); continue; }
      if (rule.minItems && val.length < rule.minItems) errors.push(`${key} needs ≥${rule.minItems} items, got ${val.length}`);
      if (rule.items && val.length > 0) {
        // Check first item structure
        const itemErrs = validateSchema(val[0], rule.items);
        if (itemErrs.length) errors.push(`${key}[0]: ${itemErrs.join(", ")}`);
      }
    } else if (type === "number" && typeof val !== "number") {
      errors.push(`${key} should be number, got ${typeof val}`);
    } else if (type === "string" && typeof val !== "string") {
      errors.push(`${key} should be string, got ${typeof val}`);
    } else if (type === "object" && (typeof val !== "object" || Array.isArray(val))) {
      errors.push(`${key} should be object, got ${Array.isArray(val)?"array":typeof val}`);
    }
  }
  return errors;
}

// Schemas for each phase's expected output
const SCHEMAS = {
  classify: { domain: "string", bf: "number", variety_gaps: "string", ooda: "object", dq: { type: "array", minItems: 4 } },
  hypotheses: { type: "array", minItems: 3, items: { id: "string", text: "string", alpha: "number", beta: "number", confirm: "string", reject: "string" } },
  gauntlet: { results: { type: "array", minItems: 1, items: { id: "string", risk_rank: "number", crux: "string" } } },
  audit: { fmea: { type: "array", minItems: 1, items: { component: "string", rpn: "number" } } },
  strategy: { strategies: { type: "array", minItems: 1, items: { priority: "string", action: "string", justification: "string" } } },
  sqi: { sqi_overall: "number", dimensions: { type: "array", minItems: 7, items: { name: "string", score: "number" } } },
};

// JSON response with schema validation
async function ai(prompt, schemaName) {
  const r = await _fetch(prompt, V4J, 3000);
  if (!r.ok) return `{"error":"${r.text}","_errType":"${r.errType||"unknown"}"}`;
  const text = r.text;
  const parsed = pJ(text);
  if (!parsed) return text; // Return raw — caller handles with pJ fallback

  if (schemaName && SCHEMAS[schemaName]) {
    const schema = SCHEMAS[schemaName];
    // Handle array-type schemas (hypotheses)
    if (schema.type === "array") {
      if (!Array.isArray(parsed)) {
        // Retry asking for array
        const r2 = await _fetch(prompt + "\n\nCRITICAL: Return a JSON ARRAY, not an object.", V4J, 3000);
        return r2.ok ? r2.text : text;
      }
      if (schema.minItems && parsed.length < schema.minItems) {
        const r2 = await _fetch(prompt + `\n\nCRITICAL: Return at least ${schema.minItems} items in the array.`, V4J, 3000);
        return r2.ok ? r2.text : text;
      }
    } else {
      const errors = validateSchema(parsed, schema);
      if (errors.length > 0) {
        // Auto-retry with specific error feedback
        const r2 = await _fetch(prompt + `\n\nCRITICAL: Your response had schema errors: ${errors.slice(0,3).join("; ")}. Fix these and return valid JSON.`, V4J, 3000);
        return r2.ok ? r2.text : text;
      }
    }
  }
  return text;
}

// Text response (markdown)
async function aiT(prompt) {
  const r = await _fetch(prompt, V4+"\n\nWrite structured professional output. Markdown.", 4000);
  return r.ok ? r.text : "Error: " + r.text;
}

function pJ(s) { try { return JSON.parse(s.replace(/```json\n?|```/g,"").trim()); } catch { return null; } }

// ═══ DETERMINISTIC SCORING (computed, not LLM-judged) ═══

function computeDetScores(strategy) {
  if (!strategy?.strategies) return null;
  const strats = strategy.strategies;
  // 1. Specificity: count how many of [who,what,how,when,howmuch] each action addresses
  const specWords = { who: /team|user|client|operator|stakeholder|department/i, what: /implement|create|build|fix|add|remove|deploy/i, how: /by |using |via |through /i, when: /within|week|month|day|sprint|quarter/i, howmuch: /\d+%|\$\d|target|\d+ point/i };
  const specScores = strats.map(s => {
    const txt = `${s.action} ${s.justification||""} ${s.expected_impact||""}`;
    return Object.values(specWords).filter(rx => rx.test(txt)).length;
  });
  const specAvg = specScores.reduce((a,b)=>a+b,0) / (specScores.length||1);
  const specificity = Math.round((specAvg / 5) * 100);

  // 2. MECE: check for overlap (similar actions) and gaps (missing priority levels)
  const priorities = new Set(strats.map(s => s.priority));
  const priorityCoverage = ["CRITICAL","HIGH","MEDIUM","LOW"].filter(p => priorities.has(p)).length;
  const mece = Math.round((priorityCoverage / 4) * 100);

  // 3. Evidence linkage: does each strategy reference a hypothesis, FMEA, or data point?
  const evidenceWords = /H\d|FMEA|RPN|hypothesis|audit|data|observation|finding/i;
  const evidenced = strats.filter(s => evidenceWords.test(`${s.justification||""} ${s.evidence_chain||""}`)).length;
  const evidenceScore = Math.round((evidenced / (strats.length||1)) * 100);

  // 4. Consistency: check for contradictory keywords
  const contradictions = [];
  for (let i = 0; i < strats.length; i++) {
    for (let j = i+1; j < strats.length; j++) {
      const a = (strats[i].action||"").toLowerCase();
      const b = (strats[j].action||"").toLowerCase();
      if ((a.includes("increase") && b.includes("decrease") && a.split(" ").some(w => b.includes(w))) ||
          (a.includes("add") && b.includes("remove") && a.split(" ").some(w => b.includes(w)))) {
        contradictions.push(`${strats[i].priority} vs ${strats[j].priority}`);
      }
    }
  }
  const consistency = contradictions.length === 0 ? 100 : Math.max(0, 100 - contradictions.length * 25);

  // 5. Actionability: does each strategy have timeline + effort?
  const actionable = strats.filter(s => s.timeline && s.effort).length;
  const actionability = Math.round((actionable / (strats.length||1)) * 100);

  const overall = Math.round((specificity + mece + evidenceScore + consistency + actionability) / 5);

  return { overall, specificity, mece, evidenceScore, consistency, actionability, contradictions, strats_count: strats.length };
}

// Source badge for AI-generated content
function AiBadge({ label = "AI-generated" }) {
  return <span style={{fontSize:10,padding:"2px 8px",borderRadius:6,background:"#7c3aed12",color:cc.purple,fontWeight:600,marginLeft:6}}>🤖 {label}</span>;
}

// Deterministic score badge
function DetBadge({ label = "Computed" }) {
  return <span style={{fontSize:10,padding:"2px 8px",borderRadius:6,background:"#0d948812",color:cc.teal,fontWeight:600,marginLeft:6}}>📐 {label}</span>;
}

// ═══ UI ═══
function Btn({children,onClick,c=cc.teal,outline,small,full,loading,disabled}) {
  return <button onClick={onClick} disabled={disabled||loading} style={{padding:small?"6px 14px":"12px 24px",borderRadius:12,fontSize:small?12:15,fontWeight:700,cursor:disabled||loading?"default":"pointer",width:full?"100%":"auto",border:outline?`2px solid ${c}`:"none",opacity:disabled?.35:1,background:outline?"transparent":loading?c+"aa":c,color:outline?c:"#fff",display:"flex",alignItems:"center",justifyContent:"center",gap:8,transition:"all .15s"}}>{loading&&<Sp/>}{children}</button>;
}
function Sp() { return <div style={{width:16,height:16,border:"2px solid rgba(255,255,255,.3)",borderTopColor:"#fff",borderRadius:"50%",animation:"spin .6s linear infinite"}}><style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style></div>; }
function Box({children,title,c=cc.teal,right}) {
  return <div style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:16,overflow:"hidden",marginBottom:16}}>
    {title&&<div style={{padding:"14px 20px",borderBottom:"1px solid #e2e8f0",display:"flex",alignItems:"center",gap:10,background:"#fafafa"}}>
      <div style={{width:4,height:22,borderRadius:2,background:c}}/><span style={{fontWeight:700,fontSize:15,color:"#1e293b",flex:1}}>{title}</span>{right}</div>}
    <div style={{padding:20}}>{children}</div></div>;
}
function Field({label,value,onChange,ph="",area,type="text"}) {
  const T=area?"textarea":"input";
  return <label style={{display:"block",marginBottom:14}}><span style={{fontSize:12,fontWeight:600,color:cc.slate,display:"block",marginBottom:4}}>{label}</span>
    <T type={type} value={value||""} onChange={e=>onChange(e.target.value)} placeholder={ph} rows={area?5:undefined}
      style={{display:"block",width:"100%",padding:"10px 14px",border:"1.5px solid #e2e8f0",borderRadius:12,fontSize:14,background:"#fffbeb",color:"#1e293b",boxSizing:"border-box",fontFamily:"inherit",resize:area?"vertical":"none"}}/></label>;
}
function Info({icon,text,c=cc.teal,btn,onBtn,loading}) {
  return <div style={{padding:"14px 20px",background:c+"0a",borderRadius:14,border:`1.5px solid ${c}25`,display:"flex",alignItems:"center",gap:14,marginBottom:16}}>
    <span style={{fontSize:28}}>{icon}</span><span style={{fontSize:14,color:"#1e293b",flex:1,lineHeight:1.5}}>{text}</span>
    {btn&&<Btn onClick={onBtn} c={c} loading={loading}>{btn}</Btn>}</div>;
}
function Pill({ok,yes="✅ Pass",no="⬜ Pending"}) {
  return <span style={{fontSize:12,fontWeight:600,padding:"3px 10px",borderRadius:12,background:ok?"#dcfce7":"#f1f5f9",color:ok?cc.green:"#94a3b8"}}>{ok?yes:no}</span>;
}

// ═══ TIMER (was missing — runtime crash bug) ═══
function Timer({ logs, onLog }) {
  const [on, setOn] = useState(false);
  const [t, setT] = useState(0);
  const [lbl, setLbl] = useState("");
  const ref = useRef(null);
  useEffect(() => { if (on) ref.current = setInterval(() => setT(v => v + 1), 1000); else clearInterval(ref.current); return () => clearInterval(ref.current); }, [on]);
  const f = s => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  return (
    <Box title="⏱ Live Session Timer" c={cc.amber}>
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: 48, fontWeight: 800, color: cc.amber, fontFamily: "monospace", letterSpacing: 3 }}>{f(t)}</div>
        <div style={{ display: "flex", gap: 8, justifyContent: "center", margin: "12px 0" }}>
          <Btn onClick={() => setOn(!on)} c={on ? cc.red : cc.green} small>{on ? "⏸ Pause" : "▶ Start"}</Btn>
          <Btn onClick={() => { if (t > 0) { onLog({ time: f(t), label: lbl || `Event ${(logs||[]).length + 1}` }); setLbl(""); } }} c={cc.amber} small outline>📌 Mark</Btn>
          <Btn onClick={() => { setT(0); setOn(false); }} c={cc.slate} small outline>↺</Btn>
        </div>
        <input value={lbl} onChange={e => setLbl(e.target.value)} placeholder="What just happened?" style={{
          width: "100%", padding: "8px 14px", border: "1.5px solid #e2e8f0", borderRadius: 10, fontSize: 14, boxSizing: "border-box", background: "#fffbeb" }} />
      </div>
      {(logs||[]).length > 0 && <div style={{ marginTop: 12, borderTop: "1px solid #e2e8f0", paddingTop: 8 }}>
        {(logs||[]).map((l, i) => <div key={i} style={{ display: "flex", gap: 10, padding: "5px 0", fontSize: 13 }}>
          <span style={{ fontFamily: "monospace", fontWeight: 700, color: cc.amber, minWidth: 50 }}>{l.time}</span>
          <span>{l.label}</span></div>)}</div>}
    </Box>
  );
}

// ═══ PHASE ONBOARDING DESCRIPTIONS ═══
const PHASE_DESC = {
  0: "Define the problem. AI classifies using Cynefin, Requisite Variety, OODA, RPD pattern matching, and Bayes Factor to determine what kind of problem you're solving.",
  1: "Generate testable hypotheses. Each gets a Bayesian prior, a 10-framework stress test, and sealed thresholds that can't move after data arrives.",
  2: "Input available data. AI runs FMEA, HAZOP, STPA, Swiss Cheese, and 6 more frameworks. Findings are labeled as data-backed or predicted.",
  3: "Design the strategy. Each recommendation links to evidence. The SQI scores quality across 7 dimensions before you deliver.",
  4: "Observe strategy execution. Collect evidence. The timer logs events. NEEDS_MONITORING hypotheses are highlighted.",
  5: "Final report. Compares monitoring data to strategy predictions. Causal verification, defense audit, DQ Spider Chart, and meta-learner input.",
};

// ═══ EXPORT UTILITIES ═══

function buildStrategyMD(d) {
  const s = d.strategy; if (!s) return "";
  const hyps = d.hyps || [];
  let md = `# STRATEGY PLAN\n## ${d.name || "Project"}\n**Date:** ${new Date().toLocaleDateString()}\n**Domain:** ${d.p0?.domain || "—"} (BF=${d.p0?.bf || "—"})\n\n---\n\n`;
  if (s.executive_strategy) md += `## Executive Strategy\n\n${s.executive_strategy}\n\n---\n\n`;
  // Preliminary verdicts
  const pv = s.preliminary_verdicts || [];
  if (pv.length) {
    md += `## Preliminary Verdicts\n\n| # | Hypothesis | Verdict | Evidence | Monitor |\n|---|---|---|---|---|\n`;
    pv.forEach(v => { const h = hyps.find(x=>x.id===v.id); md += `| ${v.id} | ${h?.text?.slice(0,60)||"—"} | ${v.verdict} | ${v.evidence||"—"} | ${v.monitoring_plan||"—"} |\n`; });
    md += "\n---\n\n";
  }
  // Strategies
  md += `## Strategy Actions\n\n`;
  (s.strategies || []).forEach((st, i) => {
    md += `### ${i+1}. [${st.priority}] ${st.action}\n\n`;
    md += `**Why this will work:** ${st.justification}\n\n`;
    if (st.evidence_chain) md += `**Evidence chain:** ${st.evidence_chain}\n\n`;
    md += `| Expected Impact | Risk If Ignored |\n|---|---|\n| ${st.expected_impact||"—"} | ${st.risk_if_ignored||"—"} |\n\n`;
    md += `**Effort:** ${st.effort||"—"} · **Timeline:** ${st.timeline||"—"} · **Framework:** ${st.framework_source||"—"}\n\n---\n\n`;
  });
  if (s.implementation_sequence) md += `## Implementation Order\n\n${s.implementation_sequence}\n\n---\n\n`;
  if (s.success_metrics?.length) { md += `## Success Metrics\n\n`; s.success_metrics.forEach(m => md += `- ${m}\n`); md += "\n"; }
  if (s.monitoring_plan) md += `**Monitoring plan:** ${s.monitoring_plan}\n\n`;
  if (s.review_date) md += `**Review date:** ${s.review_date}\n\n`;
  if (s.confidence) md += `**Confidence:** ${s.confidence}\n\n`;
  md += `---\n\n*Generated by v4.0 Universal Project Workflow — 30 Frameworks + Convergence Architecture*\n`;
  return md;
}

function buildReportMD(d) {
  let md = `# FINAL REPORT\n## ${d.name || "Project"}\n**Date:** ${new Date().toLocaleDateString()}\n**Domain:** ${d.p0?.domain || "—"} (BF=${d.p0?.bf || "—"})\n**Methodology:** v4.0 Universal Project Workflow — 6 phases, 30 frameworks, Bayesian convergence\n\n---\n\n`;
  if (d.report) md += d.report + "\n\n---\n\n";
  // DQ
  const dq = d.dq || {};
  const vals = DQ.map(x => dq[x.k] || 0).filter(v => v > 0);
  const geo = vals.length > 0 ? Math.round(Math.pow(vals.reduce((a,b)=>a*b,1), 1/vals.length)) : 0;
  md += `## Decision Quality\n\n| Dimension | Phase | Score |\n|---|---|---|\n`;
  DQ.forEach(x => md += `| ${x.l} | P${x.p} | ${dq[x.k]||0}% |\n`);
  md += `| **Overall (geometric mean)** | | **${geo}%** |\n\n`;
  md += `---\n\n*Generated by v4.0 Universal Project Workflow*\n`;
  return md;
}

function ExportBar({ label, markdown }) {
  const [copied, setCopied] = useState(false);
  const copy = () => { navigator.clipboard?.writeText(markdown); setCopied(true); setTimeout(() => setCopied(false), 2500); };
  const print = () => {
    const w = window.open("", "_blank");
    if (!w) return;
    // Escape HTML entities in markdown content to prevent XSS from LLM output
    const esc = (s) => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    const safemd = esc(markdown);
    w.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>${esc(label)}</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>body{font-family:Georgia,serif;max-width:800px;margin:40px auto;padding:0 20px;color:#1e293b;line-height:1.7}
h1{color:#0d9488;border-bottom:2px solid #0d9488;padding-bottom:8px}h2{color:#7c3aed;margin-top:30px}h3{color:#1e293b}
table{width:100%;border-collapse:collapse;margin:16px 0;font-size:14px}th,td{border:1px solid #e2e8f0;padding:8px 12px;text-align:left}
th{background:#f8fafc;font-weight:700}hr{border:none;border-top:1px solid #e2e8f0;margin:24px 0}
strong{color:#0d9488}pre{background:#f1f5f9;padding:12px;border-radius:8px;overflow-x:auto;font-size:13px}
@media print{body{margin:20px}}</style></head><body>`);
    // Safer markdown→HTML: line-by-line processing instead of global regex
    const lines = safemd.split("\n");
    let html = "";
    let inTable = false;
    for (const line of lines) {
      if (line.startsWith("### ")) { html += `<h3>${line.slice(4)}</h3>`; }
      else if (line.startsWith("## ")) { html += `<h2>${line.slice(3)}</h2>`; }
      else if (line.startsWith("# ")) { html += `<h1>${line.slice(2)}</h1>`; }
      else if (line.startsWith("---")) { html += "<hr>"; }
      else if (line.startsWith("- ")) { html += `<li>${line.slice(2)}</li>`; }
      else if (line.startsWith("|") && line.includes("---")) { /* skip table separator */ }
      else if (line.startsWith("|")) {
        const cells = line.split("|").filter(Boolean).map(c => c.trim());
        if (!inTable) { html += "<table><thead><tr>" + cells.map(c=>`<th>${c}</th>`).join("") + "</tr></thead><tbody>"; inTable = true; }
        else { html += "<tr>" + cells.map(c=>`<td>${c}</td>`).join("") + "</tr>"; }
      } else {
        if (inTable) { html += "</tbody></table>"; inTable = false; }
        if (line.trim()) { html += `<p>${line.replace(/\*\*(.*?)\*\*/g,"<strong>$1</strong>").replace(/\*(.*?)\*/g,"<em>$1</em>")}</p>`; }
      }
    }
    if (inTable) html += "</tbody></table>";
    w.document.write(html + "</body></html>");
    w.document.close();
  };

  return (
    <div style={{display:"flex",gap:8,marginBottom:16}}>
      <Btn onClick={copy} c={cc.teal} small outline>{copied ? "✓ Copied!" : `📋 Copy ${label} as Markdown`}</Btn>
      <Btn onClick={print} c={cc.purple} small outline>🖨 Open as Printable Page</Btn>
    </div>
  );
}

// ═══ STRATEGY QUALITY INDEX (SQI) ═══
// 7 dimensions: Evidence, Specificity, Consistency, Falsifiability, Counterfactual, Bias, Cross-Dept Coherence
// Plus: Rumelt 4 tests, Martin opposite test, WWHTBT kill criteria, conflict detection

function SQI({d,u}) {
  const [l,sL]=useState(false);
  const q = d.sqi;

  const run=async()=>{sL(true);
    const strats = d.strategy?.strategies||[];
    const r=await ai(`STRATEGY QUALITY EVALUATION — Score this strategy using domain-agnostic frameworks.

STRATEGY TO EVALUATE:
Executive: ${d.strategy?.executive_strategy||"N/A"}
Actions: ${strats.map((s,i)=>`${i+1}.[${s.priority}] ${s.action} — justification: ${s.justification} — evidence: ${s.evidence_chain}`).join("\n")}
Implementation: ${d.strategy?.implementation_sequence||"N/A"}
Metrics: ${(d.strategy?.success_metrics||[]).join(", ")}

PROJECT CONTEXT: ${d.brief?.slice(0,400)}
DOMAIN: ${d.p0?.domain} | AUDIT FINDINGS: ${d.audit?JSON.stringify(d.audit.top_findings||[]).slice(0,300):"N/A"}

Score each dimension 0-100. Be harsh — a score of 70+ should mean genuinely strong.

Return JSON:
{"sqi_overall":0-100,
"dimensions":[
  {"name":"Evidence Quality","score":0-100,"grade":"A|B|C|D|F","finding":"1-2 sentence assessment using GRADE criteria: is evidence from data, inference, or assumption?"},
  {"name":"Specificity","score":0-100,"grade":"A-F","finding":"SMART scoring: who/what/how/when/how much specified?"},
  {"name":"Internal Consistency","score":0-100,"grade":"A-F","finding":"do actions contradict each other? resource conflicts?"},
  {"name":"Falsifiability","score":0-100,"grade":"A-F","finding":"can each action be proven wrong? are kill criteria defined?"},
  {"name":"Counterfactual Coverage","score":0-100,"grade":"A-F","finding":"pre-mortem: are failure scenarios identified across market/execution/competitive/regulatory?"},
  {"name":"Bias Detection","score":0-100,"grade":"A-F","finding":"Kahneman checklist: confirmation bias, anchoring, planning fallacy, sunk cost, availability?"},
  {"name":"Cross-Dept Coherence","score":0-100,"grade":"A-F","finding":"do strategies across different areas conflict? resource competition? metric contradictions?"}
],
"rumelt_test":{"consistency":{"pass":true/false,"note":""},"consonance":{"pass":true/false,"note":""},"advantage":{"pass":true/false,"note":""},"feasibility":{"pass":true/false,"note":""}},
"opposite_test":[{"strategy":"action text","opposite":"the opposite of this action","is_stupid":true/false,"verdict":"if opposite is stupid, this is a platitude not a real choice"}],
"wwhtbt":[{"strategy":"action","must_be_true":"what must be true for this to work","kill_criterion":"observable evidence that would prove this wrong","current_status":"likely true|uncertain|likely false"}],
"conflicts":[{"area_a":"dept/function A","area_b":"dept/function B","conflict":"description","severity":"HIGH|MEDIUM|LOW","resolution":"how to resolve"}],
"weakest_link":"which dimension is the biggest vulnerability",
"improvement_actions":["specific action to improve the weakest dimension","action 2","action 3"]}`, "sqi");
    sL(false);const j=pJ(r);if(j)u({sqi:j});};

  const gc=s=>{if(s>=80)return cc.green;if(s>=60)return cc.amber;return cc.red;};
  const gg=s=>{if(s>=80)return"#dcfce7";if(s>=60)return"#fef3c7";return"#fee2e2";};

  if(!q) return <Info icon="📊" text="Score your strategy's quality across 7 dimensions: Evidence, Specificity, Consistency, Falsifiability, Counterfactual Coverage, Bias Detection, and Cross-Dept Coherence. Plus Rumelt's 4 tests and Martin's opposite test." btn="🤖 Score Quality" onBtn={run} c={cc.purple} loading={l}/>;

  const dims=q.dimensions||[];
  const rumelt=q.rumelt_test||{};
  const opp=q.opposite_test||[];
  const wwh=q.wwhtbt||[];
  const conf=q.conflicts||[];
  const rTests=["consistency","consonance","advantage","feasibility"];

  return <>
    {/* Overall SQI Score */}
    <Box title={<span>Strategy Quality Index (SQI) <AiBadge label="LLM-as-judge"/></span>} c={cc.purple} right={<Btn onClick={run} c={cc.purple} small loading={l}>🤖 Re-score</Btn>}>
      <div style={{textAlign:"center",marginBottom:20}}>
        <div style={{fontSize:64,fontWeight:900,color:gc(q.sqi_overall||0),lineHeight:1}}>{q.sqi_overall||0}</div>
        <div style={{fontSize:14,color:cc.slate,marginTop:4}}>/ 100</div>
        <div style={{fontSize:13,fontWeight:700,color:gc(q.sqi_overall||0),marginTop:4}}>
          {(q.sqi_overall||0)>=80?"Strong":(q.sqi_overall||0)>=60?"Adequate":"Needs Improvement"}</div>
      </div>

      {/* 7 Dimension Bars */}
      <div style={{display:"grid",gap:8}}>
        {dims.map((dim,i)=>(
          <div key={i} style={{padding:10,borderRadius:10,background:gg(dim.score),border:`1px solid ${gc(dim.score)}25`}}>
            <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
              <span style={{fontWeight:800,fontSize:24,color:gc(dim.score),minWidth:36}}>{dim.score}</span>
              <div style={{flex:1}}>
                <div style={{display:"flex",alignItems:"center",gap:6}}>
                  <span style={{fontWeight:700,fontSize:13,color:"#1e293b"}}>{dim.name}</span>
                  <span style={{fontSize:11,fontWeight:800,padding:"1px 8px",borderRadius:6,background:gc(dim.score)+"20",color:gc(dim.score)}}>{dim.grade}</span>
                </div>
                <p style={{fontSize:12,color:cc.slate,marginTop:2}}>{dim.finding}</p>
              </div>
              <div style={{width:80,height:6,borderRadius:3,background:"#e2e8f0"}}>
                <div style={{width:`${dim.score}%`,height:"100%",borderRadius:3,background:gc(dim.score),transition:"width .3s"}}/>
              </div>
            </div>
          </div>
        ))}
      </div>

      {q.weakest_link&&<div style={{padding:12,background:"#fef2f2",borderRadius:10,marginTop:12,border:"1px solid #fecaca"}}>
        <p style={{fontSize:13,color:cc.red}}>⚠ <strong>Weakest link:</strong> {q.weakest_link}</p></div>}
    </Box>

    {/* Rumelt's 4 Tests */}
    <Box title="Rumelt's 4 Strategy Tests" c={cc.teal}>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
        {rTests.map(t=>{const r=rumelt[t]||{};return(
          <div key={t} style={{padding:10,borderRadius:10,background:r.pass?"#f0fdf4":"#fef2f2",border:`1px solid ${r.pass?"#bbf7d0":"#fecaca"}`}}>
            <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:4}}>
              <span style={{fontSize:16}}>{r.pass?"✅":"❌"}</span>
              <span style={{fontWeight:700,fontSize:13,textTransform:"capitalize"}}>{t}</span></div>
            <p style={{fontSize:12,color:cc.slate}}>{r.note}</p>
          </div>);})}
      </div>
    </Box>

    {/* Martin's Opposite Test + WWHTBT */}
    <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,marginBottom:16}}>
      {opp.length>0&&<Box title="Martin's Opposite Test" c={cc.amber}>
        {opp.map((o,i)=>(
          <div key={i} style={{padding:8,marginBottom:6,borderRadius:8,background:o.is_stupid?"#fef3c7":"#f0fdf4",border:`1px solid ${o.is_stupid?"#fde68a":"#bbf7d0"}`}}>
            <p style={{fontSize:12,fontWeight:600}}>{o.strategy?.slice(0,80)}</p>
            <p style={{fontSize:11,color:cc.slate}}>Opposite: {o.opposite}</p>
            <p style={{fontSize:11,color:o.is_stupid?cc.amber:cc.green,fontWeight:600}}>{o.is_stupid?"⚠ Platitude — opposite is absurd":"✅ Real strategic choice"}</p>
          </div>))}
      </Box>}

      {wwh.length>0&&<Box title="WWHTBT — Kill Criteria" c={cc.red}>
        {wwh.map((w,i)=>(
          <div key={i} style={{padding:8,marginBottom:6,borderRadius:8,background:"#f8fafc",border:"1px solid #e2e8f0"}}>
            <p style={{fontSize:12,fontWeight:600}}>{w.strategy?.slice(0,60)}</p>
            <p style={{fontSize:11,color:cc.slate}}>Must be true: {w.must_be_true}</p>
            <p style={{fontSize:11,color:cc.red}}>Kill if: {w.kill_criterion}</p>
            <span style={{fontSize:10,padding:"1px 6px",borderRadius:4,fontWeight:600,
              background:w.current_status==="likely true"?"#dcfce7":w.current_status==="likely false"?"#fee2e2":"#fef3c7",
              color:w.current_status==="likely true"?cc.green:w.current_status==="likely false"?cc.red:cc.amber}}>{w.current_status}</span>
          </div>))}
      </Box>}
    </div>

    {/* Cross-Department Conflicts */}
    {conf.length>0&&<Box title={`⚠ ${conf.length} Cross-Department Conflicts Detected`} c={cc.red}>
      {conf.map((c,i)=>(
        <div key={i} style={{padding:10,marginBottom:8,borderRadius:10,border:`1.5px solid ${c.severity==="HIGH"?cc.red:c.severity==="MEDIUM"?cc.amber:cc.slate}30`,background:c.severity==="HIGH"?"#fef2f2":"#fff"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
            <span style={{fontSize:11,fontWeight:700,padding:"2px 8px",borderRadius:6,background:c.severity==="HIGH"?"#fee2e2":c.severity==="MEDIUM"?"#fef3c7":"#f1f5f9",color:c.severity==="HIGH"?cc.red:c.severity==="MEDIUM"?cc.amber:cc.slate}}>{c.severity}</span>
            <span style={{fontSize:13,fontWeight:600}}>{c.area_a} ↔ {c.area_b}</span></div>
          <p style={{fontSize:12,color:cc.slate}}>{c.conflict}</p>
          <p style={{fontSize:12,color:cc.green,marginTop:4}}>→ {c.resolution}</p>
        </div>))}
    </Box>}

    {/* Improvement Actions */}
    {(q.improvement_actions||[]).length>0&&<Box title="How to Improve This Strategy" c={cc.green}>
      {q.improvement_actions.map((a,i)=><div key={i} style={{display:"flex",gap:8,padding:"8px 0",borderBottom:"1px solid #f1f5f9"}}>
        <span style={{fontWeight:800,color:cc.green}}>{i+1}</span><p style={{fontSize:13}}>{a}</p></div>)}
    </Box>}
  </>;
}

const DQ_RUBRIC = {
  frame: "0-25: Is the right problem being solved? Are stakeholders identified? Is scope bounded?",
  alt: "0-25: Were meaningfully different alternatives considered? Not just 'do it or don't'?",
  info: "0-25: Is evidence timely, accurate, and from reliable sources? GRADE score?",
  val: "0-25: Are priorities and tradeoffs explicit? Values clear to all stakeholders?",
  reas: "0-25: Is the logic correct? Are assumptions stated? Is reasoning falsifiable?",
  commit: "0-25: Can this be executed? Are resources committed? Is the organization ready?"
};

// ═══ PHASE 0 — CLASSIFY ═══
function P0({d,u}) {
  const [l,sL]=useState(false); const [err,sErr]=useState(""); const p=d.p0;
  const run=async()=>{if(!d.brief){sErr("Paste a project brief above first.");return;}sL(true);sErr("");
    const r=await ai(`PHASE 0: Classify using Cynefin[#16], Bayes Factor, Requisite Variety[#30], OODA[#17], RPD[#12], Sensemaking[#13], DQ Frame, reference-class.

EXAMPLE OUTPUT (for a SaaS onboarding audit):
{"domain":"Complicated","justification":"Expert-discoverable cause-effect in user adoption funnels. Known patterns from similar SaaS audits.","bf":85,"variety_env":"3 user types, 5 onboarding steps, 2 platforms","variety_sys":"Tutorial system, in-app guides, support docs","variety_gaps":"1. No offline mode. 2. Single session training insufficient.","variety_decision":"Amplify","ooda":{"observe":"Usage analytics, support tickets","orient":"FMEA on onboarding funnel","decide":"Gate review after 2 weeks","act":"UX fixes prioritized by RPN","freq":"Weekly"},"rpd_pattern":"SaaS platform adoption audit","sensemaking_anchors":"user confusion patterns","expectancy_violations":"if experienced users also struggle","reference_class":"Similar SaaS audits show 30-40% adoption within 1 month","dq":[20,15,18,12],"maturity_assessment":"Level 2 - Defined","spiral_depth":"Spiral 1 (lightweight)"}

NOW CLASSIFY THIS PROJECT. Return JSON with the EXACT same keys:
{"domain":"","justification":"","bf":number,"variety_env":"","variety_sys":"","variety_gaps":"","variety_decision":"Amplify|Attenuate","ooda":{"observe":"","orient":"","decide":"","act":"","freq":"Weekly"},"rpd_pattern":"","sensemaking_anchors":"","expectancy_violations":"","reference_class":"","dq":[0-25,0-25,0-25,0-25],"maturity_assessment":"","spiral_depth":""}
PROJECT:\n${d.brief}\n${d.data?"DATA:\n"+d.data:""}`, "classify");
    sL(false);
    const j=pJ(r);
    if(j && j.domain) { sErr(""); u({p0:j}); }
    else if(j && j.error) { sErr("AI error: " + j.error); }
    else { sErr("Could not parse classification. Try again or simplify your brief."); }
  };
  if(!p)return <>
    <Info icon="🎯" text="AI classifies your problem using Cynefin[#16], computes Bayes Factor, audits Requisite Variety[#30], designs OODA[#17] loop, checks RPD[#12] pattern match, and scores DQ Frame." btn="🤖 Auto-Classify" onBtn={run} c={cc.teal} loading={l}/>
    {err&&<div style={{padding:12,background:"#fef2f2",borderRadius:12,marginBottom:14,border:"1px solid #fecaca"}}><p style={{fontSize:13,color:cc.red}}>❌ {err}</p></div>}
  </>;
  const bfNum = typeof p.bf === "number" ? p.bf : parseFloat(p.bf) || 0;
  const dqT=(p.dq||[]).reduce((a,b)=>a+(typeof b==="number"?b:parseFloat(b)||0),0);
  const gateChecks = [
    { ok: bfNum > 10, label: `BF = ${bfNum.toFixed(1)}`, need: "BF > 10" },
    { ok: dqT >= 60, label: `DQ = ${dqT}%`, need: "DQ ≥ 60%" },
    { ok: !!p.variety_gaps, label: "Gaps documented", need: "Variety gaps identified" },
  ];
  const gate = gateChecks.every(c => c.ok);
  const blocking = gateChecks.filter(c => !c.ok);
  return <>
    <Box title="Classification" c={cc.teal} right={<Btn onClick={run} c={cc.teal} small loading={l}>🤖 Redo</Btn>}>
      <div style={{display:"flex",gap:12,flexWrap:"wrap",marginBottom:12}}>
        <div style={{padding:"8px 18px",borderRadius:12,background:cc.teal+"12",fontWeight:800,fontSize:18,color:cc.teal}}>{p.domain}</div>
        <div style={{padding:"8px 18px",borderRadius:12,background:bfNum>10?"#dcfce7":"#fef3c7",fontWeight:700,color:bfNum>10?cc.green:cc.amber}}>BF={bfNum.toFixed(1)}</div>
        <div style={{padding:"8px 18px",borderRadius:12,background:dqT>=60?"#dcfce7":"#fef3c7",fontWeight:700,color:dqT>=60?cc.green:cc.amber}}>DQ={dqT}%</div>
        {p.spiral_depth&&<div style={{padding:"8px 18px",borderRadius:12,background:"#f1f5f9",fontWeight:600,fontSize:12,color:cc.slate}}>{p.spiral_depth}</div>}
      </div>
      <p style={{fontSize:13,color:cc.slate,lineHeight:1.6}}>{p.justification}</p>
      {p.rpd_pattern&&<p style={{fontSize:13,color:cc.purple,fontStyle:"italic"}}>🔄 Pattern: {p.rpd_pattern}</p>}
      {p.reference_class&&<p style={{fontSize:13,color:cc.blue}}>📊 Reference: {p.reference_class}</p>}
      {p.expectancy_violations&&<p style={{fontSize:13,color:cc.red}}>⚠ Violations: {p.expectancy_violations}</p>}
      <details style={{marginTop:12}}>
        <summary style={{cursor:"pointer",fontSize:13,fontWeight:600,color:cc.slate}}>Full details</summary>
        <div style={{marginTop:10,display:"grid",gap:10}}>
          {[["Environment [#30]",p.variety_env,"#f8fafc"],["System [#30]",p.variety_sys,"#f8fafc"],["Gaps → "+p.variety_decision,p.variety_gaps,"#fef2f2"]].map(([t,v,bg],i)=>
            <div key={i}><span style={{fontSize:12,fontWeight:700,color:cc.slate}}>{t}</span><p style={{fontSize:13,whiteSpace:"pre-wrap",background:bg,padding:10,borderRadius:8}}>{v}</p></div>)}
          {p.ooda&&<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
            {["observe","orient","decide","act"].map(k=><div key={k} style={{background:"#f8fafc",padding:8,borderRadius:8}}>
              <span style={{fontSize:11,fontWeight:700,color:cc.teal,textTransform:"uppercase"}}>OODA {k}</span>
              <p style={{fontSize:12,margin:"4px 0 0"}}>{p.ooda[k]}</p></div>)}</div>}
        </div>
      </details>
    </Box>
    <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:14}}>
      {gateChecks.map((c,i)=><Pill key={i} ok={c.ok} yes={c.label} no={`❌ ${c.need}`}/>)}
    </div>

    {gate ? <Btn onClick={()=>u({phase:1})} c={cc.teal} full>✅ Phase 0 Complete → Hypotheses</Btn>
    : <div style={{marginBottom:14}}>
        <div style={{padding:14,background:"#fef3c7",borderRadius:12,marginBottom:10,border:"1px solid #fde68a"}}>
          <p style={{fontSize:13,color:"#92400e",fontWeight:600,marginBottom:6}}>⚠ Exit gate not met — {blocking.length} condition{blocking.length>1?"s":""} blocking:</p>
          {blocking.map((b,i)=><p key={i} style={{fontSize:12,color:"#92400e",marginBottom:2}}>• {b.need} (currently: {b.label})</p>)}
          <p style={{fontSize:12,color:"#92400e",marginTop:8}}>Try: click 🤖 Redo to re-classify, or add more detail to your brief.</p>
        </div>
        <Btn onClick={()=>u({phase:1})} c={cc.amber} full outline>⚠ Proceed Anyway (gate not met)</Btn>
      </div>}
  </>;
}

// ═══ PHASE 1 — HYPOTHESES ═══
function P1({d,u}) {
  const [lG,sLG]=useState(false);const [lGa,sLGa]=useState(false);const [err,sErr]=useState("");const hyps=d.hyps;
  const gen=async()=>{sLG(true);sErr("");
    const r=await ai(`PHASE 1: Generate 8-12 hypotheses using HDD[#21]+BAYES_LITE[#4]. Check MECE, portfolio ρ, EVOI[#25].

EXAMPLE (one hypothesis):
{"id":"H1","text":"We believe the mobile app workflow takes <3 minutes per fruit. We will know by timing 5 users during practical exercise.","signal":"Median time per fruit (minutes)","alpha":6,"beta":4,"confirm":"Median <3 min","reject":"Median >5 min","evoi":"high","portfolio_cluster":"speed"}

Generate 8-12 like this. Return JSON array:
[{"id":"H1","text":"We believe...","signal":"measurable","alpha":number,"beta":number,"confirm":"threshold","reject":"threshold","evoi":"high|medium|low","portfolio_cluster":"cluster name"}]
PROJECT:${d.brief}\nDOMAIN:${d.p0?.domain}\nGAPS:${d.p0?.variety_gaps}\n${d.data?"DATA:\n"+d.data:""}`, "hypotheses");
    sLG(false);
    const j=pJ(r);
    if(j && Array.isArray(j) && j.length>0) { sErr(""); u({hyps:j.map(h=>({...h,status:"OPEN"}))}); }
    else if(j && j.error) { sErr("AI error: " + (j.error || "unknown")); }
    else if(j && Array.isArray(j) && j.length===0) { sErr("AI returned empty. Try adding more detail to your brief."); }
    else { sErr("Could not parse response. Try again."); }
  };
  const gauntlet=async()=>{if(!hyps?.length)return;sLGa(true);
    const r=await ai(`Run 10-framework gauntlet on 3 riskiest hypotheses. Return JSON:
{"results":[{"id":"H_","risk_rank":1,"frameworks":[{"fw":"STEELMAN","finding":"","action":true/false},...all 10],"crux":"testable belief","top_fmea":{"mode":"","s":1-10,"o":1-10,"d":1-10,"rpn":number},"fta_cut_set":""}],"portfolio_correlation":number,"mece_gaps":"","thompson_priority":"","evoi_ranking":""}
HYPOTHESES:\n${hyps.map(h=>`${h.id}:${h.text} [α=${h.alpha},β=${h.beta}]`).join("\n")}\nPROJECT:${d.brief?.slice(0,500)}`, "gauntlet");
    sLGa(false);const j=pJ(r);if(j)u({gauntlet:j});};
  if(!hyps)return <>
    <Info icon="🔬" text="AI generates hypotheses with Bayesian priors, MECE coverage, portfolio diversification, and EVOI ranking." btn="🤖 Generate" onBtn={gen} c={cc.purple} loading={lG}/>
    {err&&<div style={{padding:12,background:"#fef2f2",borderRadius:12,marginBottom:14,border:"1px solid #fecaca"}}><p style={{fontSize:13,color:cc.red}}>❌ {err}</p></div>}
  </>;
  return <>
    {err&&<div style={{padding:12,background:"#fef2f2",borderRadius:12,marginBottom:14,border:"1px solid #fecaca"}}><p style={{fontSize:13,color:cc.red}}>❌ {err}</p></div>}
    <Box title={`${hyps.length} Hypotheses`} c={cc.purple} right={<div style={{display:"flex",gap:6}}>
      <Btn onClick={gen} c={cc.purple} small loading={lG}>🤖 Regen</Btn>
      {!d.gauntlet&&<Btn onClick={gauntlet} c={cc.purple} small outline loading={lGa}>🤖 Stress-Test</Btn>}</div>}>
      {hyps.map((h,i)=>{const p=h.alpha+h.beta>0?(h.alpha/(h.alpha+h.beta)*100):0;const gR=d.gauntlet?.results?.find(r=>r.id===h.id);
        return <div key={i} style={{padding:14,marginBottom:8,borderRadius:12,border:"1px solid #e2e8f0",background:"#fafafa"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:6}}>
            <span style={{fontWeight:800,color:cc.purple}}>{h.id}</span>
            <span style={{fontSize:12,fontWeight:700,padding:"2px 10px",borderRadius:10,background:cc.teal+"15",color:cc.teal}}>P={p.toFixed(0)}%</span>
            {h.evoi&&<span style={{fontSize:11,padding:"2px 8px",borderRadius:8,background:h.evoi==="high"?"#dcfce7":"#fef3c7",color:h.evoi==="high"?cc.green:cc.amber}}>EVOI:{h.evoi}</span>}
            <span style={{fontSize:12,color:cc.slate,marginLeft:"auto"}}>α={h.alpha} β={h.beta}</span></div>
          <p style={{fontSize:13,color:"#1e293b",lineHeight:1.5,margin:"0 0 6px"}}>{h.text}</p>
          <div style={{display:"flex",gap:6,fontSize:12}}>
            <span style={{padding:"2px 8px",borderRadius:6,background:"#dcfce7",color:cc.green}}>✓ {h.confirm}</span>
            <span style={{padding:"2px 8px",borderRadius:6,background:"#fee2e2",color:cc.red}}>✗ {h.reject}</span></div>
          {gR&&<div style={{marginTop:8,padding:10,background:"#f8fafc",borderRadius:8}}>
            <div style={{fontSize:12,fontWeight:700,color:cc.purple,marginBottom:4}}>Gauntlet #{gR.risk_rank}</div>
            {gR.crux&&<p style={{fontSize:12,margin:"0 0 4px"}}>🎯 Crux: {gR.crux}</p>}
            {gR.top_fmea&&<p style={{fontSize:12,color:cc.red}}>⚠ FMEA: {gR.top_fmea.mode} (RPN={gR.top_fmea.rpn})</p>}
            {gR.fta_cut_set&&<p style={{fontSize:12,color:cc.amber}}>🔗 FTA: {gR.fta_cut_set}</p>}
          </div>}
        </div>;})}
      {d.gauntlet?.portfolio_correlation!=null&&<div style={{padding:10,background:"#f1f5f9",borderRadius:8,fontSize:13,marginTop:8}}>
        📊 Portfolio ρ={d.gauntlet.portfolio_correlation} {d.gauntlet.portfolio_correlation<0.5?"✅":"⚠ Too correlated"}
        {d.gauntlet.mece_gaps&&<span style={{display:"block",marginTop:4,color:cc.amber}}>⚠ MECE: {d.gauntlet.mece_gaps}</span>}
        {d.gauntlet.thompson_priority&&<span style={{display:"block",marginTop:4,color:cc.teal}}>🎲 Priority: {d.gauntlet.thompson_priority}</span>}</div>}
    </Box>
    {d.gauntlet&&!d.sealed&&<Box title="🔒 Seal Thresholds" c={cc.red}>
      <p style={{fontSize:14,color:"#7f1d1d",lineHeight:1.6,padding:14,background:"#fef2f2",borderRadius:12,marginBottom:14}}>
        <strong>After sealing, thresholds cannot change.</strong> This prevents moving goalposts.</p>
      <Btn onClick={()=>u({sealed:true,sealDate:new Date().toLocaleDateString()})} c={cc.red} full>🔒 Seal — {new Date().toLocaleDateString()}</Btn></Box>}
    {d.sealed&&<div style={{padding:12,background:"#dcfce7",borderRadius:12,textAlign:"center",marginBottom:14,fontSize:13,fontWeight:700,color:cc.green}}>🔒 Sealed {d.sealDate}</div>}
    <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:14}}>
      <Pill ok={hyps.length>=3} yes={`${hyps.length} hypotheses`}/><Pill ok={!!d.gauntlet} yes="Tested" no="Not tested"/><Pill ok={d.sealed} yes="Sealed"/></div>
    {d.sealed&&<Btn onClick={()=>u({phase:2})} c={cc.purple} full>✅ Phase 1 Complete → Audit</Btn>}
  </>;
}

// ═══ PHASE 2 — AUDIT (with data) ═══
// NOW: takes real data, not just predictions. User pastes data here.
function P2({d,u}) {
  const [l,sL]=useState(false);const [err,sErr]=useState("");
  const run=async()=>{sL(true);sErr("");
    const r=await ai(`PHASE 2: Audit using FMEA[#7],HAZOP[#8],FTA[#9],Swiss Cheese[#10],STPA[#11],Mental Models[#14],ODD[#22],Chaos[#18],Circuit Breaker[#19],Canary[#20]. Compute entropy convergence and EVSI/ENBS.

${d.data?"REAL DATA PROVIDED — base your analysis on this actual data, not just the brief.":"NO REAL DATA — these are PREDICTIONS from the brief only. Label them as predicted."}

Return JSON:
{"data_based":${!!d.data},"fmea":[{"component":"","failure_mode":"","effect":"","s":1-10,"o":1-10,"d":1-10,"rpn":0,"action":"","evidence":"what data supports this finding"}],"hazop":[{"node":"","guide_word":"","deviation":"","consequence":"","evidence":""}],"stpa":[{"control_action":"","uca_type":"","hazard":"","constraint":""}],"fta":{"top_event":"","cut_sets":[],"prevention":""},"swiss_cheese":{"layers":[],"holes":[]},"top_findings":["finding 1","finding 2","finding 3","finding 4","finding 5"],"h_norm_estimate":"estimated entropy after audit","observation_needs":["what additional data would improve this audit"]}

PROJECT:${d.brief}\nDOMAIN:${d.p0?.domain}\nGAPS:${d.p0?.variety_gaps}
HYPOTHESES:${(d.hyps||[]).map(h=>`${h.id}:${h.text?.slice(0,80)}`).join("; ")}
GAUNTLET:${d.gauntlet?.results?.map(r=>`${r.id}:crux="${r.crux}"`).join("; ")||"N/A"}
${d.data?"DATA:\n"+d.data:""}`, "audit");
    sL(false);const j=pJ(r);
    if(j && j.fmea) { sErr(""); u({audit:j,auditRaw:null}); }
    else if(j && j.error) { sErr("AI error: " + j.error); u({auditRaw:null,audit:null}); }
    else if(r && typeof r === "string" && r.length > 50) { u({auditRaw:r,audit:null}); }
    else { sErr("Could not generate audit. Try again."); }
  };
  const a=d.audit;
  return <>
    {err&&<div style={{padding:12,background:"#fef2f2",borderRadius:12,marginBottom:14,border:"1px solid #fecaca"}}><p style={{fontSize:13,color:cc.red}}>❌ {err}</p></div>}
    {/* Data input */}
    <Box title="📊 Audit Data Input" c={cc.blue}>
      <p style={{fontSize:13,color:cc.slate,lineHeight:1.6,marginBottom:10}}>
        <strong>Paste any available data below.</strong> The more data you provide, the more grounded the audit findings. Without data, the audit produces predictions from the brief only.
      </p>
      <Field label="Available data" value={d.data} onChange={v=>u({data:v})} area ph="Paste here: GSC exports, GA4 data, crawl results, error logs, API responses, user feedback, screenshots description, performance metrics, competitor analysis, survey results..." />
      <div style={{display:"flex",gap:6,flexWrap:"wrap",marginBottom:10}}>
        {["GSC/GA4 exports","Crawl data","Error logs","User feedback","API docs","Screenshots","Performance metrics","Competitor data"].map(t=>
          <span key={t} style={{fontSize:11,padding:"3px 10px",borderRadius:8,background:"#f1f5f9",color:cc.slate}}>{t}</span>)}
      </div>
    </Box>

    {!a&&!d.auditRaw?<Info icon="🔍" text={d.data?"Data provided — AI will run a data-backed audit using 10 safety and resilience frameworks.":"No data yet — AI will generate PREDICTED risks from the brief. You can add data above anytime."} btn={d.data?"🤖 Run Audit":"🤖 Predict Risks"} onBtn={run} c={cc.blue} loading={l}/>
    :<>
      {a&&!a.data_based&&<div style={{padding:14,background:"#fef3c7",borderRadius:12,marginBottom:16,border:"1px solid #fde68a"}}>
        <p style={{fontSize:13,color:"#92400e"}}>⚠ <strong>Predicted findings</strong> — based on brief only, not real data. Add data above and re-run for evidence-backed audit.</p></div>}

      {a&&<Box title={<span>Audit — {(a.fmea||[]).length} FMEA + {(a.hazop||[]).length} HAZOP + {(a.stpa||[]).length} STPA <AiBadge label={a.data_based?"AI + Data":"AI-inferred"}/></span>} c={cc.blue} right={<Btn onClick={run} c={cc.blue} small loading={l}>🤖 Redo</Btn>}>
        {(a.fmea||[]).sort((a,b)=>b.rpn-a.rpn).map((f,i)=>(
          <div key={i} style={{padding:12,marginBottom:8,borderRadius:10,border:"1px solid #e2e8f0",background:f.rpn>200?"#fef2f2":f.rpn>100?"#fffbeb":"#f8fafc"}}>
            <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
              <span style={{fontWeight:800,fontSize:13,color:f.rpn>200?cc.red:f.rpn>100?cc.amber:cc.slate}}>RPN {f.rpn}</span>
              <span style={{fontSize:12,color:cc.slate}}>S={f.s}×O={f.o}×D={f.d}</span>
              <span style={{marginLeft:"auto",fontSize:11,padding:"2px 8px",borderRadius:6,fontWeight:600,background:f.rpn>200?"#fee2e2":f.rpn>100?"#fef3c7":"#f1f5f9",color:f.rpn>200?cc.red:f.rpn>100?cc.amber:cc.slate}}>{f.rpn>200?"CRITICAL":f.rpn>100?"HIGH":"MEDIUM"}</span></div>
            <p style={{fontSize:13,fontWeight:600,marginBottom:2}}>{f.component}: {f.failure_mode}</p>
            <p style={{fontSize:12,color:cc.slate,marginBottom:4}}>{f.effect}</p>
            {f.evidence&&<p style={{fontSize:12,color:cc.blue}}>📊 {f.evidence}</p>}
            <p style={{fontSize:12,color:cc.green}}>→ {f.action}</p>
          </div>))}
        {(a.hazop||[]).length>0&&<details style={{marginTop:12}}><summary style={{cursor:"pointer",fontWeight:700,fontSize:13,color:cc.amber}}>HAZOP ({(a.hazop||[]).length} deviations)</summary>
          {(a.hazop||[]).map((h,i)=><div key={i} style={{display:"flex",gap:10,padding:"8px 0",borderBottom:"1px solid #f1f5f9"}}>
            <span style={{fontFamily:"monospace",fontSize:11,fontWeight:700,padding:"2px 8px",borderRadius:6,background:cc.amber+"15",color:cc.amber,whiteSpace:"nowrap"}}>{h.guide_word}</span>
            <div><p style={{fontSize:13,fontWeight:600}}>{h.node}: {h.deviation}</p><p style={{fontSize:12,color:cc.slate}}>{h.consequence}</p></div></div>)}</details>}
      </Box>}

      {d.auditRaw&&!a&&<Box title="Audit (raw)" c={cc.blue} right={<Btn onClick={run} c={cc.blue} small loading={l}>🤖 Redo</Btn>}>
        <div style={{fontSize:13,lineHeight:1.7,whiteSpace:"pre-wrap",maxHeight:400,overflow:"auto"}}>{d.auditRaw}</div></Box>}

      {a?.observation_needs&&<Box title="📋 What additional data would improve this audit?" c={cc.amber}>
        {(a.observation_needs||[]).map((n,i)=><div key={i} style={{display:"flex",gap:8,padding:"6px 0",borderBottom:"1px solid #f1f5f9"}}>
          <span>📌</span><p style={{fontSize:13}}>{n}</p></div>)}</Box>}

      <div style={{display:"flex",gap:6,flexWrap:"wrap",marginBottom:14}}>
        <Pill ok yes={a?.data_based?"Data-backed ✓":"Predicted"}/><Pill ok yes="FMEA[#7]✓"/><Pill ok yes="HAZOP[#8]✓"/><Pill ok yes="STPA[#11]✓"/></div>
      <Btn onClick={()=>u({phase:3})} c={cc.blue} full>✅ Audit Complete → Strategy</Btn>
    </>}
  </>;
}

// ═══ PHASE 3 — STRATEGY (analyze + plan + justify) ═══
function P3({d,u}) {
  const [l,sL]=useState(false);const [err,sErr]=useState("");
  const ctx=`PROJECT:${d.brief?.slice(0,500)}\nDOMAIN:${d.p0?.domain} BF=${d.p0?.bf}\nGAPS:${d.p0?.variety_gaps}\nAUDIT:${d.audit?JSON.stringify(d.audit.top_findings||d.audit.fmea?.slice(0,5)).slice(0,600):d.auditRaw?.slice(0,500)||"N/A"}\nGAUNTLET:${d.gauntlet?JSON.stringify(d.gauntlet.results?.map(r=>({id:r.id,crux:r.crux,fmea:r.top_fmea}))).slice(0,400):"N/A"}\nHYPOTHESES:${(d.hyps||[]).map(h=>`${h.id}[P=${(h.alpha/(h.alpha+h.beta)*100).toFixed(0)}%]:${h.text?.slice(0,60)}`).join("; ")}\n${d.data?"DATA:\n"+d.data.slice(0,500):""}`;

  const run=async()=>{sL(true);
    const r=await ai(`PHASE 3: Analyze audit findings and generate STRATEGY PLAN WITH JUSTIFICATION.

For each hypothesis, give a PRELIMINARY VERDICT based on audit data (before monitoring):
LIKELY_CONFIRMED, LIKELY_REJECTED, NEEDS_MONITORING.

Then generate the strategy. Each action must link to evidence.

Return JSON:
{"preliminary_verdicts":[{"id":"H1","verdict":"LIKELY_CONFIRMED|LIKELY_REJECTED|NEEDS_MONITORING","evidence":"from audit data","monitoring_plan":"what to track if NEEDS_MONITORING"}],
"executive_strategy":"2-3 sentence summary",
"strategies":[{"priority":"CRITICAL|HIGH|MEDIUM|LOW","action":"specific action","justification":"WHY this will work — link to hypothesis, FMEA, crux, or data","evidence_chain":"H_ + FMEA RPN + audit finding → action","expected_impact":"measurable outcome","effort":"Low|Medium|High","timeline":"","risk_if_ignored":"","framework_source":"which framework identified this"}],
"implementation_sequence":"ordered steps and why this order",
"success_metrics":["metric 1","metric 2","metric 3"],
"monitoring_plan":"what to observe during Phase 4 to verify strategy works",
"review_date":"when to check",
"confidence":"High|Medium|Low with reasoning",
"reentry_check":"any R1-R8 triggers? which?"}

${ctx}`, "strategy");
    sL(false);const j=pJ(r);
    if(j && j.strategies) { sErr(""); u({strategy:j,strategyRaw:null}); }
    else if(j && j.error) { sErr("AI error: " + j.error); }
    else if(r && typeof r === "string" && r.length > 50) { u({strategyRaw:r,strategy:null}); }
    else { sErr("Could not generate strategy. Try again."); }
  };

  const s=d.strategy;
  const pC={CRITICAL:cc.red,HIGH:cc.amber,MEDIUM:cc.blue,LOW:cc.slate};
  const pI={CRITICAL:"🔴",HIGH:"🟠",MEDIUM:"🔵",LOW:"⚪"};
  const vC={LIKELY_CONFIRMED:cc.green,LIKELY_REJECTED:cc.red,NEEDS_MONITORING:cc.amber};
  const vI={LIKELY_CONFIRMED:"✅",LIKELY_REJECTED:"❌",NEEDS_MONITORING:"👁"};

  if(!s&&!d.strategyRaw)return <>
    <Info icon="🎯" text="AI analyzes audit findings, gives preliminary verdicts on each hypothesis, and generates a strategy plan where every action links to evidence." btn="🤖 Generate Strategy" onBtn={run} c={cc.amber} loading={l}/>
    {err&&<div style={{padding:12,background:"#fef2f2",borderRadius:12,marginBottom:14,border:"1px solid #fecaca"}}><p style={{fontSize:13,color:cc.red}}>❌ {err}</p></div>}
  </>;

  if(d.strategyRaw&&!s)return <><Box title="Strategy (raw)" c={cc.amber} right={<Btn onClick={run} c={cc.amber} small loading={l}>🤖 Redo</Btn>}>
    <div style={{fontSize:13,lineHeight:1.7,whiteSpace:"pre-wrap",maxHeight:500,overflow:"auto"}}>{d.strategyRaw}</div></Box>
    <Btn onClick={()=>u({phase:4})} c={cc.amber} full>👁 Strategy Ready → Monitor</Btn></>;

  return <>
    {/* Preliminary Verdicts */}
    {(s.preliminary_verdicts||[]).length>0&&<Box title={<span>Preliminary Verdicts (from audit data) <AiBadge/></span>} c={cc.green} right={<Btn onClick={run} c={cc.amber} small loading={l}>🤖 Redo</Btn>}>
      {s.preliminary_verdicts.map((v,i)=>(
        <div key={i} style={{padding:12,marginBottom:8,borderRadius:10,border:`1.5px solid ${(vC[v.verdict]||cc.slate)}30`,background:(vC[v.verdict]||cc.slate)+"06"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
            <span>{vI[v.verdict]||"❓"}</span>
            <span style={{fontWeight:800,color:cc.purple}}>{v.id}</span>
            <span style={{fontWeight:700,fontSize:13,color:vC[v.verdict]||cc.slate}}>{v.verdict?.replace(/_/g," ")}</span></div>
          <p style={{fontSize:12,color:"#1e293b"}}>{v.evidence}</p>
          {v.monitoring_plan&&<p style={{fontSize:12,color:cc.amber,marginTop:4}}>👁 Monitor: {v.monitoring_plan}</p>}
        </div>))}
    </Box>}

    {/* Strategy */}
    {s.executive_strategy&&<div style={{padding:14,background:cc.teal+"08",borderRadius:12,marginBottom:16,border:`1px solid ${cc.teal}25`}}>
      <p style={{fontSize:15,fontWeight:600,color:"#1e293b",lineHeight:1.6}}>{s.executive_strategy}</p></div>}

    <Box title={<span>Strategy Plan <AiBadge/></span>} c={cc.amber}>
      {(s.strategies||[]).map((st,i)=>(
        <div key={i} style={{padding:16,marginBottom:12,borderRadius:14,border:`1.5px solid ${(pC[st.priority]||cc.slate)}30`}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:8}}>
            <span>{pI[st.priority]||"⚪"}</span>
            <span style={{fontWeight:800,fontSize:13,color:pC[st.priority]||cc.slate}}>{st.priority}</span>
            <span style={{fontSize:11,padding:"2px 8px",borderRadius:6,background:"#f1f5f9",color:cc.slate}}>{st.effort} effort · {st.timeline}</span>
            {st.framework_source&&<span style={{fontSize:10,padding:"2px 6px",borderRadius:4,background:cc.purple+"12",color:cc.purple,fontWeight:600,marginLeft:"auto"}}>{st.framework_source}</span>}</div>
          <p style={{fontSize:14,fontWeight:700,color:"#1e293b",marginBottom:8}}>{st.action}</p>
          <div style={{padding:10,background:"#f0fdf4",borderRadius:8,marginBottom:6}}>
            <span style={{fontSize:11,fontWeight:700,color:cc.green}}>WHY THIS WILL WORK</span>
            <p style={{fontSize:12,marginTop:2,lineHeight:1.5}}>{st.justification}</p></div>
          {st.evidence_chain&&<div style={{padding:10,background:"#f1f5f9",borderRadius:8,marginBottom:6}}>
            <span style={{fontSize:11,fontWeight:700,color:cc.blue}}>EVIDENCE CHAIN</span>
            <p style={{fontSize:12,color:cc.slate,marginTop:2}}>{st.evidence_chain}</p></div>}
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
            <div style={{padding:8,background:"#dcfce7",borderRadius:6}}><span style={{fontSize:11,fontWeight:700,color:cc.green}}>EXPECTED IMPACT</span><p style={{fontSize:12,marginTop:2}}>{st.expected_impact}</p></div>
            <div style={{padding:8,background:"#fee2e2",borderRadius:6}}><span style={{fontSize:11,fontWeight:700,color:cc.red}}>IF IGNORED</span><p style={{fontSize:12,marginTop:2}}>{st.risk_if_ignored}</p></div></div>
        </div>))}
    </Box>

    <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,marginBottom:16}}>
      {s.implementation_sequence&&<Box title="Implementation Order" c={cc.amber}><p style={{fontSize:13,lineHeight:1.6,whiteSpace:"pre-wrap"}}>{s.implementation_sequence}</p></Box>}
      <Box title="Success Metrics" c={cc.green}>
        {(s.success_metrics||[]).map((m,i)=><div key={i} style={{display:"flex",gap:8,padding:"6px 0",borderBottom:"1px solid #f1f5f9"}}><span>📈</span><p style={{fontSize:13}}>{m}</p></div>)}
        {s.review_date&&<p style={{fontSize:13,marginTop:8}}>📅 Review: <strong>{s.review_date}</strong></p>}
        {s.confidence&&<p style={{fontSize:13,marginTop:4}}>🎯 Confidence: <strong>{s.confidence}</strong></p>}</Box>
    </div>

    {s.monitoring_plan&&<div style={{padding:14,background:cc.green+"08",borderRadius:12,marginBottom:16,border:`1px solid ${cc.green}25`}}>
      <p style={{fontSize:13,color:"#1e293b"}}>👁 <strong>What to monitor in Phase 4:</strong> {s.monitoring_plan}</p></div>}

    {s.reentry_check&&<div style={{padding:10,background:s.reentry_check.toLowerCase().includes("none")?"#f1f5f9":"#fef2f2",borderRadius:10,marginBottom:14,fontSize:13}}>🔄 Re-entry: {s.reentry_check}</div>}

    <SQI d={d} u={u} />

    {/* Deterministic scores (computed, not LLM-judged) */}
    {d.strategy?.strategies && (() => {
      const det = computeDetScores(d.strategy);
      if (!det) return null;
      const gc = s => s >= 80 ? cc.green : s >= 60 ? cc.amber : cc.red;
      const dims = [
        ["Specificity (SMART)", det.specificity],
        ["MECE Coverage", det.mece],
        ["Evidence Linkage", det.evidenceScore],
        ["Internal Consistency", det.consistency],
        ["Actionability", det.actionability],
      ];
      return <Box title={<span>Deterministic Quality Scores <DetBadge/></span>} c={cc.teal}>
        <div style={{textAlign:"center",marginBottom:16}}>
          <span style={{fontSize:48,fontWeight:900,color:gc(det.overall)}}>{det.overall}</span>
          <span style={{fontSize:14,color:cc.slate,display:"block"}}>/ 100 (computed from {det.strats_count} strategies)</span>
        </div>
        <div style={{display:"grid",gap:6}}>
          {dims.map(([name,score],i) => (
            <div key={i} style={{display:"flex",alignItems:"center",gap:10,padding:"6px 10px",borderRadius:8,background:gc(score)+"08"}}>
              <span style={{fontWeight:800,fontSize:18,color:gc(score),minWidth:36}}>{score}</span>
              <span style={{fontSize:13,flex:1}}>{name}</span>
              <div style={{width:80,height:6,borderRadius:3,background:"#e2e8f0"}}><div style={{width:`${score}%`,height:"100%",borderRadius:3,background:gc(score)}}/></div>
            </div>))}
        </div>
        {det.contradictions.length > 0 && <div style={{padding:10,background:"#fef2f2",borderRadius:8,marginTop:10,fontSize:12,color:cc.red}}>
          ⚠ Contradictions detected: {det.contradictions.join(", ")}</div>}
        <p style={{fontSize:11,color:cc.slate,marginTop:10}}>These scores are computed deterministically from the strategy text — no LLM involved. They complement the SQI (LLM-as-judge) scores above.</p>
      </Box>;
    })()}

    <ExportBar label="Strategy" markdown={buildStrategyMD(d)} />

    <Btn onClick={()=>u({phase:4})} c={cc.amber} full>👁 Strategy Ready → Monitor Execution</Btn>
  </>;
}

// ═══ PHASE 4 — MONITOR (observe strategy execution) ═══
function P4({d,u}) {
  const obs=d.obs||{};const hyps=d.hyps||[];
  const setOb=(hId,v)=>u(p=>({...p,obs:{...p.obs,[hId]:v}}));
  const filled=hyps.filter(h=>obs[h.id]?.trim()).length;
  const needsMonitoring=d.strategy?.preliminary_verdicts?.filter(v=>v.verdict==="NEEDS_MONITORING")||[];

  return <>
    <Info icon="👁" text={`Strategy is defined. Now observe execution and collect evidence. ${filled}/${hyps.length} hypotheses have data.${needsMonitoring.length>0?` ${needsMonitoring.length} hypotheses need monitoring.`:""}`} c={cc.green}/>

    {needsMonitoring.length>0&&<Box title={`${needsMonitoring.length} Hypotheses Need Monitoring`} c={cc.amber}>
      {needsMonitoring.map((v,i)=><div key={i} style={{padding:10,marginBottom:6,borderRadius:8,background:"#fef3c7",border:"1px solid #fde68a"}}>
        <span style={{fontWeight:700,color:cc.purple}}>{v.id}</span>
        <span style={{fontSize:12,color:cc.amber,marginLeft:8}}>👁 {v.monitoring_plan}</span></div>)}</Box>}

    <Timer logs={d.timerLogs||[]} onLog={l=>u(p=>({...p,timerLogs:[...(p.timerLogs||[]),l]}))}/>

    <Box title="Observations — Strategy Execution" c={cc.green}>
      {hyps.map((h,i)=>{const p=(h.alpha/(h.alpha+h.beta)*100).toFixed(0);const pv=d.strategy?.preliminary_verdicts?.find(v=>v.id===h.id);
        return <div key={i} style={{padding:12,marginBottom:8,borderRadius:12,border:"1px solid #e2e8f0",background:obs[h.id]?.trim()?"#f0fdf4":"#fff"}}>
          <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:4}}>
            <span style={{fontWeight:800,color:cc.purple,fontSize:14}}>{h.id}</span>
            <span style={{fontSize:12,color:cc.teal,fontWeight:600}}>P={p}%</span>
            {pv&&<span style={{fontSize:11,padding:"2px 8px",borderRadius:6,background:pv.verdict==="NEEDS_MONITORING"?"#fef3c7":"#f1f5f9",color:pv.verdict==="NEEDS_MONITORING"?cc.amber:cc.slate}}>{pv.verdict?.replace(/_/g," ")}</span>}
            <span style={{fontSize:12,color:cc.slate,flex:1}}>{h.text?.slice(0,60)}...</span>
            {obs[h.id]?.trim()&&<span>✅</span>}</div>
          <div style={{display:"flex",gap:6,fontSize:11,marginBottom:6}}>
            <span style={{padding:"2px 6px",borderRadius:4,background:"#dcfce7",color:cc.green}}>✓ {h.confirm}</span>
            <span style={{padding:"2px 6px",borderRadius:4,background:"#fee2e2",color:cc.red}}>✗ {h.reject}</span></div>
          <textarea value={obs[h.id]||""} onChange={e=>setOb(h.id,e.target.value)} placeholder="Numbers, counts, times, results observed..."
            rows={2} style={{width:"100%",padding:"8px 12px",border:"1.5px solid #e2e8f0",borderRadius:10,fontSize:13,background:"#fffbeb",boxSizing:"border-box",fontFamily:"inherit",resize:"vertical"}}/></div>;})}
    </Box>

    <div style={{display:"flex",gap:8,marginBottom:14}}>
      <Pill ok={filled>=hyps.length} yes={`${filled}/${hyps.length}`} no={`${filled}/${hyps.length}`}/>
      <Pill ok={(d.timerLogs||[]).length>0} yes={`${(d.timerLogs||[]).length} events`} no="No events"/></div>
    {filled>=3&&<Btn onClick={()=>u({phase:5})} c={cc.green} full>📋 Done Monitoring → Final Report</Btn>}
  </>;
}

// ═══ PHASE 5 — FINAL REPORT ═══
function P5({d,u}) {
  const [l,sL]=useState(false);
  const run=async()=>{sL(true);
    const obsText=(d.hyps||[]).map(h=>`${h.id}[P=${(h.alpha/(h.alpha+h.beta)*100).toFixed(0)}%]: ${h.text?.slice(0,60)}\n  Confirm:${h.confirm} Reject:${h.reject}\n  Observed:${(d.obs||{})[h.id]||"(none)"}`).join("\n");
    const r=await aiT(`PHASE 5: Final report. Compare monitoring observations to sealed thresholds. Use Causal Inference[#24], Swiss Cheese[#10], HRO[#29], Red Teaming[#28], Ablation[#23].

# EXECUTIVE SUMMARY
# METHODOLOGY (v4: 6 phases, 30 frameworks, 3 learning loops)
# FINAL VERDICTS (table: ID | Prior P | Verdict | Evidence from monitoring)
# STRATEGY RESULTS (which strategy actions were validated by monitoring?)
# CAUSAL VERIFICATION [#24]
# DEFENSE AUDIT — Swiss Cheese [#10]
# HRO DEBRIEF [#29] (5 principles)
# RED TEAM [#28]
# ABLATION [#23]
# AGENT CARDS
# COMMITMENT SCORE (/100, gate ≥70%)
# RECOMMENDATIONS UPDATED (based on monitoring data)
# META-LEARNER INPUT (Brier score, calibration, key learning)
# NEXT STEPS

STRATEGY:${d.strategy?JSON.stringify(d.strategy.strategies?.slice(0,5)).slice(0,500):"N/A"}
MONITORING:\n${obsText}\nTIMER:${(d.timerLogs||[]).map(l=>`${l.time}—${l.label}`).join("; ")||"None"}
AUDIT:${d.audit?JSON.stringify(d.audit.top_findings||[]).slice(0,300):d.auditRaw?.slice(0,300)||"N/A"}
PROJECT:${d.brief?.slice(0,400)}\nDOMAIN:${d.p0?.domain}\nGAPS:${d.p0?.variety_gaps}`);
    sL(false);u({report:r});};

  const dq=d.dq||{};const setDQ=(k,v)=>u(p=>({...p,dq:{...p.dq,[k]:parseInt(v)||0}}));
  const vals=DQ.map(x=>dq[x.k]||0).filter(v=>v>0);
  const geo=vals.length>0?Math.round(Math.pow(vals.reduce((a,b)=>a*b,1),1/vals.length)):0;

  return <>
    {!d.report?<Info icon="📋" text="AI generates the final report: compares monitoring data to strategy predictions, runs Causal[#24], Swiss Cheese[#10], HRO[#29], Red Team[#28], Ablation[#23], Agent Cards, and Meta-Learner input." btn="🤖 Generate Report" onBtn={run} c={cc.red} loading={l}/>
    :<Box title="Final Report" c={cc.red} right={<Btn onClick={run} c={cc.red} small loading={l}>🤖 Redo</Btn>}>
      <div style={{fontSize:13,lineHeight:1.7,whiteSpace:"pre-wrap",maxHeight:600,overflow:"auto"}}>{d.report}</div></Box>}

    <Box title="Decision Quality — 6 Dimensions" c={cc.purple}>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
        <div>
          <ResponsiveContainer width="100%" height={220}>
            <RadarChart data={DQ.map(x=>({d:x.l,v:dq[x.k]||0,m:100}))}>
              <PolarGrid stroke="#e2e8f0"/><PolarAngleAxis dataKey="d" tick={{fontSize:11,fill:cc.slate}}/>
              <PolarRadiusAxis angle={90} domain={[0,100]} tick={false} axisLine={false}/>
              <Radar dataKey="v" stroke={cc.purple} fill={cc.purple} fillOpacity={.15} strokeWidth={2}/></RadarChart></ResponsiveContainer>
          <div style={{textAlign:"center"}}><span style={{fontSize:32,fontWeight:800,color:cc.purple}}>{geo}%</span>
            <span style={{display:"block",fontSize:12,color:"#94a3b8"}}>Overall DQ (geometric mean)</span></div></div>
        <div>{DQ.map(x=><div key={x.k} style={{marginBottom:12}}>
          <div style={{display:"flex",justifyContent:"space-between",marginBottom:2}}>
            <span style={{fontSize:12,fontWeight:600,color:cc.slate}}>P{x.p} {x.l}</span>
            <span style={{fontSize:13,fontWeight:700,color:cc.purple}}>{dq[x.k]||0}%</span></div>
          <input type="range" min={0} max={100} value={dq[x.k]||0} onChange={e=>setDQ(x.k,e.target.value)} style={{width:"100%",accentColor:cc.purple}}/>
          <p style={{fontSize:10,color:"#94a3b8",marginTop:2,lineHeight:1.4}}>{DQ_RUBRIC[x.k]||""}</p>
        </div>)}</div></div></Box>

    {d.report&&<>
      <ExportBar label="Report" markdown={buildReportMD(d)} />
      <div style={{padding:20,background:"#f0fdf4",borderRadius:16,textAlign:"center",border:"1.5px solid #bbf7d0"}}>
      <span style={{fontSize:24}}>🎉</span>
      <div style={{fontWeight:800,fontSize:18,color:cc.green,marginTop:4}}>Workflow Complete</div>
      <p style={{fontSize:13,color:cc.slate,marginTop:4}}>Strategy designed, monitored, and verified. Deliver to client.</p></div>
    </>}
  </>;
}

// ═══ MAIN ═══
export default function App() {
  const {projects,idx,ready,u,add,sw,del,d,invalidated,clearInvalidated}=useStore();
  const [showList,setShowList]=useState(false);
  if(!ready)return <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",fontFamily:"'DM Sans',sans-serif",color:"#94a3b8"}}>Loading...</div>;
  if(!projects.length)return <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",flexDirection:"column",gap:16,fontFamily:"'DM Sans',sans-serif",background:"#f8fafc"}}>
    <div style={{width:64,height:64,borderRadius:16,background:`linear-gradient(135deg,${cc.teal},${cc.purple})`,display:"flex",alignItems:"center",justifyContent:"center"}}><span style={{fontSize:28,filter:"brightness(10)"}}>🎯</span></div>
    <div style={{fontSize:32,fontWeight:800,color:"#1e293b",letterSpacing:-1.5}}>v4.0 Workflow</div>
    <div style={{fontSize:14,color:cc.slate,maxWidth:420,textAlign:"center",lineHeight:1.6}}>Audit → Strategy → Monitor → Report<br/>30 frameworks · Evidence-based decisions · Self-improving</div>
    <Btn onClick={()=>add(newProject("My First Project"))} c={cc.teal}>Start First Project</Btn></div>;

  const phase=d?.phase||0;const pc=PHASES[phase];
  return <div style={{fontFamily:"'DM Sans',sans-serif",background:"#f8fafc",minHeight:"100vh"}}>
    <div style={{background:"#fff",borderBottom:"1px solid #e2e8f0",padding:"8px 16px",display:"flex",alignItems:"center",gap:10,position:"sticky",top:0,zIndex:10}}>
      <div style={{width:30,height:30,borderRadius:8,background:`linear-gradient(135deg,${cc.teal},${cc.purple})`,display:"flex",alignItems:"center",justifyContent:"center"}}><span style={{fontSize:14,filter:"brightness(10)"}}>🎯</span></div>
      <button onClick={()=>setShowList(!showList)} style={{padding:"4px 12px",border:"1px solid #e2e8f0",borderRadius:8,fontSize:13,fontWeight:700,color:"#1e293b",background:"#fff",cursor:"pointer",maxWidth:220,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{d?.name||"Select"} ▾</button>
      <div style={{marginLeft:"auto"}}><Btn onClick={()=>{const n=prompt("Project name:");if(n)add(newProject(n));}} c={cc.teal} small>+ New</Btn></div></div>

    {showList&&<div style={{position:"fixed",top:48,left:0,right:0,bottom:0,zIndex:20}} onClick={()=>setShowList(false)}>
      <div style={{maxWidth:400,margin:"0 auto",background:"#fff",borderRadius:"0 0 16px 16px",boxShadow:"0 8px 30px rgba(0,0,0,.12)",border:"1px solid #e2e8f0",maxHeight:"60vh",overflow:"auto"}} onClick={e=>e.stopPropagation()}>
        <div style={{padding:"12px 16px",borderBottom:"1px solid #e2e8f0",fontWeight:700,fontSize:14}}>Projects ({projects.length})</div>
        {projects.map((p,i)=><div key={i} style={{padding:"12px 16px",borderBottom:"1px solid #f1f5f9",display:"flex",alignItems:"center",gap:10,cursor:"pointer",background:i===idx?cc.teal+"08":"#fff"}} onClick={()=>{sw(i);setShowList(false);}}>
          <div style={{width:8,height:8,borderRadius:"50%",background:i===idx?cc.teal:"#e2e8f0"}}/>
          <div style={{flex:1}}><div style={{fontWeight:600,fontSize:14}}>{p.name}</div><div style={{fontSize:11,color:cc.slate}}>Phase {p.phase||0} · {PHASES[p.phase||0]?.name}</div></div>
          <button onClick={e=>{e.stopPropagation();if(confirm(`Delete "${p.name}"?`))del(i);}} style={{padding:"2px 8px",border:"1px solid #fecaca",borderRadius:6,background:"#fff",color:cc.red,fontSize:11,cursor:"pointer"}}>✕</button></div>)}
        <div style={{padding:12}}><Btn onClick={()=>{const n=prompt("Name:");if(n){add(newProject(n));setShowList(false);}}} c={cc.teal} small full outline>+ Create</Btn></div></div></div>}

    {d&&<div style={{maxWidth:780,margin:"0 auto",padding:"12px 16px 60px"}}>
      <input value={d.name||""} onChange={e=>u({name:e.target.value})} style={{fontSize:20,fontWeight:800,color:"#1e293b",border:"none",background:"transparent",padding:0,width:"100%",outline:"none",fontFamily:"inherit",marginBottom:12}}/>
      <div style={{display:"flex",gap:3,marginBottom:16}}>
        {PHASES.map(p=>{const a=p.id===phase,done=p.id<phase,locked=p.id>phase;
          return <button key={p.id} onClick={()=>{if(!locked)u({phase:p.id});}} style={{flex:1,padding:"8px 4px",border:a?`2px solid ${p.col}`:"1px solid #e2e8f0",borderRadius:10,background:done?p.col+"12":a?p.col+"08":"#fff",cursor:locked?"not-allowed":"pointer",opacity:locked?.4:1,display:"flex",flexDirection:"column",alignItems:"center",gap:2}}>
            <span style={{fontSize:18}}>{done?"✅":locked?"🔒":p.icon}</span>
            <span style={{fontSize:9,fontWeight:700,color:a?p.col:done?p.col:"#cbd5e1",textTransform:"uppercase"}}>{p.name}</span></button>;})}
      </div>
      <div style={{padding:"12px 18px",background:pc.col+"08",borderRadius:14,marginBottom:16}}>
        <div style={{display:"flex",alignItems:"center",gap:12}}>
          <span style={{fontSize:28}}>{pc.icon}</span><div style={{fontWeight:700,fontSize:16,color:pc.col}}>Phase {pc.id} — {pc.name}</div></div>
        {PHASE_DESC[phase]&&<p style={{fontSize:12,color:cc.slate,marginTop:6,lineHeight:1.5}}>{PHASE_DESC[phase]}</p>}
      </div>

      {/* Invalidation warning */}
      {invalidated&&invalidated.length>0&&<div style={{padding:12,background:"#fef3c7",borderRadius:12,marginBottom:14,border:"1px solid #fde68a",display:"flex",alignItems:"center",gap:10}}>
        <span style={{fontSize:18}}>🔄</span>
        <div style={{flex:1}}>
          <p style={{fontSize:13,color:"#92400e",fontWeight:600}}>Upstream data changed — downstream outputs reset</p>
          <p style={{fontSize:12,color:"#92400e"}}>Cleared: {invalidated.join(", ")}. Re-generate these phases.</p>
        </div>
        <Btn onClick={clearInvalidated} c={cc.amber} small outline>Dismiss</Btn>
      </div>}

      {/* Brief length indicator */}
      {phase===0&&d.brief&&<div style={{fontSize:11,color:d.brief.length>15000?cc.red:d.brief.length>8000?cc.amber:cc.slate,marginBottom:8}}>
        Brief: {d.brief.length.toLocaleString()} chars {d.brief.length>15000?"⚠ Very long — may exceed AI context":""}
      </div>}

      {phase===0&&!d.p0&&<Box title="Project Brief" c={cc.teal}>
        <p style={{fontSize:13,color:cc.slate,marginBottom:10,lineHeight:1.6}}>Describe the project. AI will classify, generate hypotheses, and audit it.</p>
        <Field label="Project brief" value={d.brief} onChange={v=>u({brief:v})} area ph="WHAT: ...\nWHO: ...\nPROBLEM: ...\nCURRENT STATE: ...\nGOAL: ...\nCONSTRAINTS: ..."/></Box>}

      {phase===0&&<P0 d={d} u={u}/>}
      {phase===1&&<P1 d={d} u={u}/>}
      {phase===2&&<P2 d={d} u={u}/>}
      {phase===3&&<P3 d={d} u={u}/>}
      {phase===4&&<P4 d={d} u={u}/>}
      {phase===5&&<P5 d={d} u={u}/>}
    </div>}
  </div>;
}
