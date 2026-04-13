import { useState } from "react";

const EN = {
  hero: "Analyze any strategic decision from every angle — in days, not weeks.",
  sub: "You're making a high-stakes call with one framework and one perspective. We run yours through financial, competitive, operational, and risk lenses simultaneously — then show you where they agree and where they conflict. One clear recommendation, backed by converged evidence.",
  cta: "Book a free 30-minute strategy call",
  
  prob_title: "The problem",
  prob: [
    ["Most consulting is intuition disguised as process.", "You run a project. You make calls based on experience. Some work. Some don't. You never know why — because there was no structure to learn from."],
    ["Analysis paralysis is real — but so is premature action.", "Without convergence criteria, you either analyze forever or ship too early. Both waste time and trust."],
    ["Knowledge walks out the door.", "When the consultant leaves, the methodology leaves too. The client is back to square one."],
  ],

  sol_title: "What this solves",
  sol: [
    { icon: "🎯", title: "Phase 0 — Classify", desc: "AI classifies your problem using Cynefin, computes a Bayes Factor for confidence, audits system capacity vs. environment complexity, and designs your decision loop." },
    { icon: "🔬", title: "Phase 1 — Hypotheses", desc: "Generates testable bets from your brief. Each gets a Bayesian prior, a 10-framework stress test (STEELMAN through PROSPECT THEORY), and sealed thresholds that can't move after data arrives." },
    { icon: "🔍", title: "Phase 2 — Audit", desc: "Runs FMEA, HAZOP, FTA, Swiss Cheese, STPA, Mental Models, Chaos Engineering, and more. Stops automatically when entropy drops below 15% — meaning 85% of uncertainty is resolved." },
    { icon: "👁", title: "Phase 3 — Observe", desc: "Built-in session timer with event marking. Enter observations against each hypothesis. Thresholds are visible so you know exactly what you're looking for." },
    { icon: "⚡", title: "Phase 4 — Analyze", desc: "AI compares every observation to its pre-sealed threshold. CONFIRMED, REJECTED, or INCONCLUSIVE — no ambiguity. Thompson Sampling picks the next focus. Reflexion self-corrects errors." },
    { icon: "📋", title: "Phase 5 — Report", desc: "Generates a structured audit report: causal verification, defense-layer analysis (Swiss Cheese), HRO debrief, Red Team assessment, Agent Cards, and a DQ Spider Chart scoring decision quality across 6 dimensions." },
  ],

  diff_title: "Why this is different",
  diffs: [
    ["Catch blind spots before they become costly mistakes", "Every recommendation traces to a named analytical framework — financial, competitive, operational, risk, behavioral. We test your decision from 30 angles so nothing gets missed. Not 'best practices' — testable methods with published academic origins."],
    ["Know when the analysis is actually done", "Most consultants stop when it 'feels done.' Our system uses mathematical convergence — it measures remaining uncertainty and stops automatically when 85%+ is resolved. You get a clear signal, not a judgment call."],
    ["No moving the goalposts", "Success and failure criteria are locked before any data arrives. This eliminates the #1 failure mode in consulting: redefining 'success' to match whatever happened."],
    ["Gets smarter with every project", "After each engagement, the meta-learner logs what worked. Over time, it recommends the right frameworks for your industry, predicts bottlenecks, and tracks how well calibrated the analysis actually was."],
    ["Your team keeps running after we leave", "You get a decision system, not a PDF. Operating manuals, decision playbooks, and trained workflows — the methodology stays when the consultant leaves."],
  ],

  how_title: "How to use it",
  how: [
    "Paste your project brief into the app",
    "AI classifies, generates hypotheses, and audits — one click per phase",
    "Observe your live session with the built-in timer and checklist",
    "AI analyzes observations against sealed thresholds and generates the report",
    "Score decision quality, deliver to client, feed the meta-learner",
  ],

  who_title: "Who it's for",
  whos: [
    ["AI consultants", "auditing platforms, agents, and workflows"],
    ["UX researchers", "running structured usability evaluations"],
    ["Product managers", "making evidence-based build/kill decisions"],
    ["Strategy consultants", "replacing gut-feel with testable hypotheses"],
    ["Anyone", "who needs to make a high-stakes decision with limited data"],
  ],

  close: "The goal isn't to be right — it's to be updateable.",
  close2: "The problem isn't lack of data — it's lack of architecture.",
  close3: "The system stays. The consultant leaves. The team keeps running.",
  foot: "v4.0 Decision Intelligence Engine — Faster decisions. Fewer blind spots. Evidence that converges.",
};

const ES = {
  hero: "Analiza cualquier decisión estratégica desde todos los ángulos — en días, no semanas.",
  sub: "Estás tomando una decisión importante con un solo marco y una sola perspectiva. Nosotros la analizamos desde lo financiero, competitivo, operativo y de riesgo simultáneamente — y te mostramos dónde coinciden y dónde no. Una recomendación clara, respaldada por evidencia convergente.",
  cta: "Agenda una llamada estratégica gratuita de 30 min",

  prob_title: "El problema",
  prob: [
    ["La mayoría de la consultoría es intuición disfrazada de proceso.", "Ejecutas un proyecto. Tomas decisiones basadas en experiencia. Algunas funcionan. Otras no. Nunca sabes por qué — porque no había estructura para aprender."],
    ["La parálisis por análisis es real — pero la acción prematura también.", "Sin criterios de convergencia, o analizas para siempre o lanzas demasiado temprano. Ambos desperdician tiempo y confianza."],
    ["El conocimiento se va cuando se va el consultor.", "Cuando el consultor sale, la metodología sale también. El cliente vuelve a empezar desde cero."],
  ],

  sol_title: "Qué resuelve",
  sol: [
    { icon: "🎯", title: "Fase 0 — Clasificar", desc: "La IA clasifica tu problema usando Cynefin, calcula un Factor de Bayes para confianza, audita la capacidad del sistema vs. la complejidad del entorno, y diseña tu ciclo de decisión." },
    { icon: "🔬", title: "Fase 1 — Hipótesis", desc: "Genera apuestas verificables desde tu brief. Cada una recibe un prior bayesiano, un stress test de 10 marcos (STEELMAN hasta PROSPECT THEORY), y umbrales sellados que no pueden moverse después de que llegan los datos." },
    { icon: "🔍", title: "Fase 2 — Auditoría", desc: "Ejecuta FMEA, HAZOP, FTA, Swiss Cheese, STPA, Modelos Mentales, Ingeniería del Caos, y más. Se detiene automáticamente cuando la entropía baja del 15% — el 85% de la incertidumbre está resuelta." },
    { icon: "👁", title: "Fase 3 — Observar", desc: "Cronómetro integrado con marcado de eventos. Ingresa observaciones contra cada hipótesis. Los umbrales están visibles para saber exactamente qué buscar." },
    { icon: "⚡", title: "Fase 4 — Analizar", desc: "La IA compara cada observación contra su umbral pre-sellado. CONFIRMADA, RECHAZADA, o INCONCLUSA — sin ambigüedad. Thompson Sampling elige el siguiente foco. Reflexion autocorrige errores." },
    { icon: "📋", title: "Fase 5 — Reporte", desc: "Genera un reporte de auditoría estructurado: verificación causal, análisis de capas de defensa (Swiss Cheese), debrief HRO, evaluación Red Team, Agent Cards, y un DQ Spider Chart con calidad de decisión en 6 dimensiones." },
  ],

  diff_title: "Por qué es diferente",
  diffs: [
    ["Detecta puntos ciegos antes de que cuesten caro", "Cada recomendación se traza a un marco analítico con nombre — financiero, competitivo, operativo, riesgo, conductual. Analizamos tu decisión desde 30 ángulos para que nada se escape. No 'mejores prácticas' — métodos verificables con orígenes académicos."],
    ["Sabe cuándo el análisis realmente terminó", "La mayoría de los consultores paran cuando 'se siente listo.' Nuestro sistema usa convergencia matemática — mide la incertidumbre restante y se detiene automáticamente cuando el 85%+ está resuelto."],
    ["Sin mover los postes de la portería", "Los criterios de éxito y fracaso se bloquean antes de que lleguen los datos. Elimina el error #1 en consultoría: redefinir 'éxito' para que coincida con lo que pasó."],
    ["Mejora con cada proyecto", "Después de cada engagement, el meta-learner registra qué funcionó. Con el tiempo, recomienda los marcos correctos para tu industria, predice cuellos de botella, y rastrea qué tan calibrado fue el análisis."],
    ["Tu equipo sigue operando cuando nos vamos", "Recibes un sistema de decisión, no un PDF. Manuales operativos, playbooks, flujos entrenados — la metodología se queda cuando el consultor se va."],
  ],

  how_title: "Cómo usarlo",
  how: [
    "Pega tu brief de proyecto en la app",
    "La IA clasifica, genera hipótesis, y audita — un clic por fase",
    "Observa tu sesión en vivo con el cronómetro y checklist integrados",
    "La IA analiza observaciones contra umbrales sellados y genera el reporte",
    "Califica la calidad de decisión, entrega al cliente, alimenta el meta-learner",
  ],

  who_title: "Para quién es",
  whos: [
    ["Consultores de IA", "auditando plataformas, agentes y workflows"],
    ["Investigadores UX", "ejecutando evaluaciones de usabilidad estructuradas"],
    ["Product managers", "tomando decisiones build/kill basadas en evidencia"],
    ["Consultores de estrategia", "reemplazando intuición con hipótesis verificables"],
    ["Cualquiera", "que necesite tomar una decisión de alto impacto con datos limitados"],
  ],

  close: "El objetivo no es tener razón — es ser actualizable.",
  close2: "El problema no es falta de datos — es falta de arquitectura.",
  close3: "El sistema se queda. El consultor se va. El equipo sigue operando.",
  foot: "v4.0 Motor de Inteligencia Decisional — Decisiones más rápidas. Menos puntos ciegos. Evidencia que converge.",
};

export default function Pitch() {
  const [lang, setLang] = useState("en");
  const t = lang === "en" ? EN : ES;

  return (
    <div style={{ background: "#0a0f1a", color: "#e2e8f0", minHeight: "100vh", fontFamily: "'Playfair Display', Georgia, serif", overflowX: "hidden" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400&family=JetBrains+Mono:wght@400;700&display=swap');
        * { margin: 0; padding: 0; box-sizing: border-box; }
        .fade-in { animation: fadeUp .8s ease both; }
        .fade-in-d1 { animation: fadeUp .8s ease .15s both; }
        .fade-in-d2 { animation: fadeUp .8s ease .3s both; }
        .fade-in-d3 { animation: fadeUp .8s ease .45s both; }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes pulse { 0%,100% { opacity: .4; } 50% { opacity: .8; } }
        .glow { position: absolute; width: 400px; height: 400px; borderRadius: 50%; filter: blur(120px); animation: pulse 6s ease infinite; pointer-events: none; }
      `}</style>

      {/* Language toggle */}
      <div style={{ position: "fixed", top: 20, right: 20, zIndex: 50, display: "flex", gap: 4, background: "#1e293b", borderRadius: 12, padding: 4 }}>
        {["en", "es"].map(l => (
          <button key={l} onClick={() => setLang(l)} style={{ padding: "6px 16px", borderRadius: 10, border: "none", fontSize: 13, fontWeight: 700, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace", background: lang === l ? "#0d9488" : "transparent", color: lang === l ? "#fff" : "#64748b", transition: "all .2s" }}>
            {l.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Hero */}
      <div style={{ position: "relative", padding: "120px 40px 80px", maxWidth: 900, margin: "0 auto", textAlign: "center" }}>
        <div className="glow" style={{ background: "#0d9488", top: -100, left: "10%", opacity: .15 }} />
        <div className="glow" style={{ background: "#7c3aed", top: -50, right: "5%", opacity: .1, animationDelay: "3s" }} />
        <div className="fade-in" style={{ display: "inline-block", padding: "6px 20px", borderRadius: 20, border: "1px solid #1e293b", background: "#1e293b80", fontSize: 13, fontFamily: "'JetBrains Mono', monospace", color: "#0d9488", marginBottom: 24, letterSpacing: 1 }}>
          v4.0 — DECISION INTELLIGENCE
        </div>
        <h1 className="fade-in-d1" style={{ fontSize: "clamp(36px, 6vw, 72px)", fontWeight: 900, lineHeight: 1.1, letterSpacing: -2, color: "#f8fafc", marginBottom: 24 }}>
          {t.hero}
        </h1>
        <p className="fade-in-d2" style={{ fontSize: 18, lineHeight: 1.7, color: "#94a3b8", maxWidth: 640, margin: "0 auto 40px" }}>
          {t.sub}
        </p>
        <a className="fade-in-d3" href="https://calendly.com/YOUR-LINK" target="_blank" rel="noopener" style={{ display: "inline-block", padding: "14px 36px", background: "linear-gradient(135deg, #0d9488, #7c3aed)", borderRadius: 14, fontSize: 16, fontWeight: 700, color: "#fff", textDecoration: "none", fontFamily: "'JetBrains Mono', monospace", letterSpacing: .5 }}>
          {t.cta} →
        </a>
      </div>

      {/* Problem */}
      <div style={{ padding: "80px 40px", maxWidth: 900, margin: "0 auto" }}>
        <h2 style={{ fontSize: 14, fontFamily: "'JetBrains Mono', monospace", color: "#dc2626", letterSpacing: 3, textTransform: "uppercase", marginBottom: 40 }}>{t.prob_title}</h2>
        {t.prob.map(([title, desc], i) => (
          <div key={i} style={{ marginBottom: 40, paddingLeft: 24, borderLeft: "2px solid #1e293b" }}>
            <h3 style={{ fontSize: 22, fontWeight: 700, color: "#f8fafc", marginBottom: 8, lineHeight: 1.3 }}>{title}</h3>
            <p style={{ fontSize: 16, color: "#94a3b8", lineHeight: 1.7 }}>{desc}</p>
          </div>
        ))}
      </div>

      {/* Solution — 6 Phases */}
      <div style={{ padding: "80px 40px", maxWidth: 900, margin: "0 auto" }}>
        <h2 style={{ fontSize: 14, fontFamily: "'JetBrains Mono', monospace", color: "#0d9488", letterSpacing: 3, textTransform: "uppercase", marginBottom: 40 }}>{t.sol_title}</h2>
        <div style={{ display: "grid", gap: 20 }}>
          {t.sol.map((s, i) => (
            <div key={i} style={{ padding: 28, borderRadius: 16, background: "#111827", border: "1px solid #1e293b", display: "flex", gap: 20, alignItems: "start", transition: "border-color .2s" }}
              onMouseEnter={e => e.currentTarget.style.borderColor = "#0d9488"} onMouseLeave={e => e.currentTarget.style.borderColor = "#1e293b"}>
              <span style={{ fontSize: 32, flexShrink: 0 }}>{s.icon}</span>
              <div>
                <h3 style={{ fontSize: 18, fontWeight: 700, color: "#f8fafc", marginBottom: 8 }}>{s.title}</h3>
                <p style={{ fontSize: 15, color: "#94a3b8", lineHeight: 1.7 }}>{s.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Differentiators */}
      <div style={{ padding: "80px 40px", maxWidth: 900, margin: "0 auto" }}>
        <h2 style={{ fontSize: 14, fontFamily: "'JetBrains Mono', monospace", color: "#7c3aed", letterSpacing: 3, textTransform: "uppercase", marginBottom: 40 }}>{t.diff_title}</h2>
        {t.diffs.map(([title, desc], i) => (
          <div key={i} style={{ marginBottom: 32, paddingLeft: 24, borderLeft: `2px solid ${["#0d9488","#7c3aed","#dc2626","#b45309","#059669"][i]}` }}>
            <h3 style={{ fontSize: 20, fontWeight: 700, color: "#f8fafc", marginBottom: 8 }}>{title}</h3>
            <p style={{ fontSize: 15, color: "#94a3b8", lineHeight: 1.7 }}>{desc}</p>
          </div>
        ))}
      </div>

      {/* How it works */}
      <div id="how" style={{ padding: "80px 40px", maxWidth: 900, margin: "0 auto" }}>
        <h2 style={{ fontSize: 14, fontFamily: "'JetBrains Mono', monospace", color: "#0d9488", letterSpacing: 3, textTransform: "uppercase", marginBottom: 40 }}>{t.how_title}</h2>
        <div style={{ display: "grid", gap: 16 }}>
          {t.how.map((step, i) => (
            <div key={i} style={{ display: "flex", gap: 16, alignItems: "center", padding: "16px 20px", borderRadius: 12, background: "#111827", border: "1px solid #1e293b" }}>
              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 28, fontWeight: 900, color: "#0d9488", minWidth: 40, textAlign: "center" }}>{i + 1}</span>
              <p style={{ fontSize: 16, color: "#e2e8f0", lineHeight: 1.5 }}>{step}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Who it's for */}
      <div style={{ padding: "80px 40px", maxWidth: 900, margin: "0 auto" }}>
        <h2 style={{ fontSize: 14, fontFamily: "'JetBrains Mono', monospace", color: "#7c3aed", letterSpacing: 3, textTransform: "uppercase", marginBottom: 40 }}>{t.who_title}</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
          {t.whos.map(([who, what], i) => (
            <div key={i} style={{ padding: 20, borderRadius: 12, background: "#111827", border: "1px solid #1e293b" }}>
              <div style={{ fontSize: 16, fontWeight: 700, color: "#f8fafc", marginBottom: 4 }}>{who}</div>
              <div style={{ fontSize: 14, color: "#64748b" }}>{what}</div>
            </div>
          ))}
        </div>
      </div>

      {/* CTA Section */}
      <div style={{ padding: "60px 40px", maxWidth: 700, margin: "0 auto", textAlign: "center" }}>
        <div style={{ padding: 40, borderRadius: 20, background: "linear-gradient(135deg, #0d948815, #7c3aed15)", border: "1px solid #1e293b" }}>
          <h2 style={{ fontSize: 28, fontWeight: 900, color: "#f8fafc", marginBottom: 12, lineHeight: 1.2 }}>
            {lang === "en" ? "Have a decision worth getting right?" : "¿Tienes una decisión que vale la pena acertar?"}
          </h2>
          <p style={{ fontSize: 16, color: "#94a3b8", lineHeight: 1.7, marginBottom: 24, maxWidth: 500, margin: "0 auto 24px" }}>
            {lang === "en" ? "30-minute strategy call. We'll map your decision, identify blind spots, and show you exactly how a structured analysis would work for your specific case. No pitch — just clarity." : "Llamada estratégica de 30 minutos. Mapeamos tu decisión, identificamos puntos ciegos, y te mostramos exactamente cómo funcionaría un análisis estructurado para tu caso. Sin pitch — solo claridad."}
          </p>
          <a href="https://calendly.com/YOUR-LINK" target="_blank" rel="noopener" style={{ display: "inline-block", padding: "16px 40px", background: "linear-gradient(135deg, #0d9488, #7c3aed)", borderRadius: 14, fontSize: 17, fontWeight: 700, color: "#fff", textDecoration: "none", fontFamily: "'JetBrains Mono', monospace" }}>
            {lang === "en" ? "Book your free call →" : "Agenda tu llamada gratuita →"}
          </a>
          <p style={{ fontSize: 12, color: "#475569", marginTop: 12, fontFamily: "'JetBrains Mono', monospace" }}>
            {lang === "en" ? "or email: nicolas@regexseo.com" : "o escríbeme: nicolas@regexseo.com"}
          </p>
        </div>
      </div>

      {/* Closing quotes */}
      <div style={{ padding: "100px 40px 60px", maxWidth: 700, margin: "0 auto", textAlign: "center" }}>
        <div style={{ position: "relative" }}>
          <div className="glow" style={{ background: "#7c3aed", top: -80, left: "30%", opacity: .08 }} />
          <p style={{ fontSize: 24, fontStyle: "italic", color: "#94a3b8", lineHeight: 1.5, marginBottom: 24 }}>"{t.close}"</p>
          <p style={{ fontSize: 24, fontStyle: "italic", color: "#94a3b8", lineHeight: 1.5, marginBottom: 24 }}>"{t.close2}"</p>
          <p style={{ fontSize: 24, fontStyle: "italic", color: "#f8fafc", lineHeight: 1.5, marginBottom: 40 }}>"{t.close3}"</p>
        </div>
        <div style={{ width: 60, height: 2, background: "linear-gradient(90deg, #0d9488, #7c3aed)", margin: "0 auto 20px", borderRadius: 1 }} />
        <p style={{ fontSize: 13, fontFamily: "'JetBrains Mono', monospace", color: "#475569", lineHeight: 1.6 }}>{t.foot}</p>
        <p style={{ fontSize: 12, fontFamily: "'JetBrains Mono', monospace", color: "#334155", marginTop: 8 }}>Nicolás Grinberg · RegexSEO</p>
      </div>
    </div>
  );
}
