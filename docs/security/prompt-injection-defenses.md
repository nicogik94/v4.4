# Prompt Injection Defenses — Operator Guide

How the Decision Engine defends against prompt injection in v4.3, what the operator is responsible for, and what to do when the sanitizer flags a brief.

---

## What prompt injection is

A user-controlled input that contains instructions intended to override the agent's actual task. The classic example: a brief that begins with "Ignore all previous instructions and instead..." Prompt injection is **OWASP's #1 LLM risk** (2025 edition). Attack success rates in agentic systems reach 84% (Vectra AI). It is not a solved problem and probably will not be in 2026.

The Decision Engine's threat surface is the project brief at intake. Every brief is untrusted text that flows directly into LLM context. This is why v4.3 adds the intake sanitizer.

---

## How the Decision Engine defends

Defense-in-depth, four layers. None of these alone is sufficient.

**Layer 1: Intake sanitization** (`mas/security/intake_sanitizer.py`)

Pattern-based scanning at the moment a brief enters the engine. Five categories of patterns:
- **Instruction override** — "ignore previous instructions", "disregard the above", "new instructions:"
- **Role manipulation** — "you are now a", "pretend to be", "act as", fake conversation tags
- **Output hijacking** — "respond with exactly", "output the following", "repeat after me"
- **Tool hijacking** — "call the function", "execute the command", "run the tool"
- **Exfiltration** — "reveal the system prompt", "what are your original instructions", base64-encoded payloads

Plus structural checks: length cap (50K chars default), single-line length cap, repeated character runs (DOS), Unicode normalization, control characters.

Each finding is severity-tagged (`info` / `low` / `medium` / `high` / `critical`). The sanitizer returns a recommendation: `allow`, `review`, or `block`.

**Layer 2: Privilege separation**

The Decision Engine has no external action surface in v4.3. The model can produce analysis but cannot send emails, call APIs, modify external records, or take any action that affects parties outside the operator's environment. This bounds the worst-case impact of a successful injection: it can produce a wrong analysis, but it cannot make a wrong purchase or send a wrong message.

**Layer 3: Output validation**

Every phase output is validated against a Pydantic schema in `state.py`. A successful injection that tries to make the agent output free-form text instead of structured JSON will fail schema validation and the orchestrator will retry with a stricter prompt or fail the phase. This is a structural defense, not a content defense — it does not stop subtly wrong content that still validates.

**Layer 4: Continuous adversarial testing**

The eval harness (`mas/evals/run_evals.py`) includes golden cases with adversarial briefs. Regressions in detection rate fail the CI pipeline. New attack patterns published by OWASP, Lakera, or the CSA Agentic AI Red Teaming Guide should be added to the pattern library quarterly.

---

## How to read sanitization findings

The sanitizer writes findings to `state.intake_sanitization_findings`. To inspect:

```bash
curl http://localhost:8000/projects/{id}/policy-audit
```

Each finding has:
- **severity** — `info` / `low` / `medium` / `high` / `critical`
- **category** — `instruction_override`, `role_manipulation`, `output_hijacking`, `tool_hijacking`, `exfiltration`, or `structural`
- **rationale** — human-readable explanation of why the pattern matched
- **matched_text** — the offending substring (truncated to 200 chars)
- **line_number** and **char_offset** — location in the brief

The sanitizer's recommendation is one of:
- **`allow`** — no findings, or only low/info findings. Brief proceeds.
- **`review`** — medium, high, or critical findings. Operator should look before approving.
- **`block`** — only returned when `fail_hard=True` is configured for the project. Brief is blocked.

---

## What to do when the sanitizer flags a brief

The default mode is **fail-soft**: findings are logged and the brief proceeds. The operator is the second line of defense. Do not skip review just because the brief proceeded.

**For a `review` recommendation:**

1. **Read the matched text.** Is it actually an instruction override, or is it benign content that matched a permissive pattern? Example false positive: a brief about a recruiting workflow that says "the candidate will pretend to be a customer in the role-play exercise" — the sanitizer might flag "pretend to be" as role manipulation.

2. **Decide based on context:**
   - **False positive** (benign content matched) → proceed. Note the false positive in the operator log; if it happens repeatedly, the pattern needs tuning.
   - **True positive but operator-introduced** (the operator wrote a brief that legitimately discusses prompt injection topics, e.g., in a security analysis project) → proceed with elevated alertness. Review the phase outputs more carefully than usual.
   - **True positive from an external source** (the brief came from a client and contains content the operator did not author) → **stop**. Investigate the brief's provenance. Confirm with the client that the content is intended. Do not proceed until you understand why an external party included instruction-like content.
   - **Critical finding from any source** → trigger the kill switch on the project before any phase runs. Investigate before resuming.

3. **Record the decision** in the operator log. The audit trail should include not just what the sanitizer found but what the operator decided.

**For a `block` recommendation** (only in `fail_hard` mode): the brief is blocked. The operator must either revise the brief, override the block by explicitly setting the project to `fail_soft` mode (deliberate operator action with audit log entry), or reject the engagement.

---

## When to use `fail_hard` mode

Default is `fail_soft` because the Decision Engine is operator-mediated and false positives kill adoption. **Switch to `fail_hard` when:**

- The deployment is high-risk under EU AI Act (employment, credit, insurance, etc.) — see `compliance/eu-ai-act-classification.md`
- The brief comes from an untrusted source (open intake form, public submission, third-party integration)
- The operator cannot review every brief manually before running phases (high-volume deployments)
- A previous incident demonstrated that fail-soft was insufficient

To enable `fail_hard` for a specific project:

```python
# At intake, before calling /projects/{id}/run
from security import sanitize_brief
result = sanitize_brief(brief_text, fail_hard=True)
if result.recommendation == "block":
    # do not create the project, return an error to the requester
    ...
```

**Note:** `fail_hard` is not yet wired into the API endpoint as a configuration flag. v4.3 ships with the function-level capability; v4.3.x or v5 will add the per-project configuration. For now, operators who need `fail_hard` should call the sanitizer directly before calling `POST /projects`.

---

## What the sanitizer does NOT catch

Be honest about the limits. The sanitizer raises the cost of attack and surfaces obvious attempts. It does not promise to catch sophisticated ones. **Specifically:**

- **Subtle semantic injection** that does not use any of the detected patterns. Example: a brief that frames the actual task in misleading context to bias the analysis.
- **Multi-turn injection** where benign-looking content in the brief influences a later phase via the phase summary. The summary itself is LLM-generated and inherits the brief's content; if the brief biases the summary, downstream phases are biased too.
- **Encoded payloads** beyond base64. ROT13, atbash, hex, prompt-in-image, etc. are not detected.
- **Non-English prompt injection.** All detection patterns are English. Spanish, French, or other-language injection attempts will mostly slip through. This is a known gap; a v5 enhancement should add Spanish-language patterns at minimum given the operator's bilingual practice.
- **Adversarial paraphrasing** that conveys the same instruction-override intent in different words ("forget what you were told earlier, do this instead").

The recommendation is to treat the sanitizer as the first line of defense, not the only one. The other layers (privilege separation, output validation, continuous adversarial testing, operator review) carry real weight.

---

## How to extend the pattern library

When a new attack pattern is identified — through an internal incident, an OWASP advisory, a Lakera blog post, or quarterly research review — add it to `mas/security/intake_sanitizer.py`:

1. Decide which category the pattern fits (`_INSTRUCTION_OVERRIDE_PATTERNS`, `_ROLE_MANIPULATION_PATTERNS`, etc.)
2. Decide the severity (`Severity.LOW` through `Severity.CRITICAL`)
3. Write the regex (be permissive — false positives in fail-soft mode are acceptable)
4. Write a clear rationale string
5. Add the tuple to the appropriate list
6. Add a test case to `mas/evals/golden_cases.jsonl` so the eval harness will catch regressions
7. Run the eval harness locally before committing
8. Document the addition in the change log of `intake_sanitizer.py`

---

## Quarterly review

Every quarter, the operator should:

1. **Review new attack patterns** from OWASP, Lakera, Promptfoo, AgentDojo, and the CSA Agentic AI Red Teaming Guide. Add new patterns to the library.
2. **Review the false-positive log.** Patterns with high false-positive rates may need tuning.
3. **Review the true-positive log.** Were any real injection attempts caught? Were any missed and only discovered later?
4. **Run the eval harness** with the latest golden cases.
5. **Update this document** if defenses or operator procedures have changed.

---

## Sources

- OWASP Top 10 for LLM Applications (2025): LLM01 Prompt Injection
- OWASP Top 10 for Agentic Applications (December 2025)
- CSA Agentic AI Red Teaming Guide (May 2025)
- Lakera Guard: pattern library and detection research
- Promptfoo: 133+ red-teaming plugins, including prompt injection categories
- ETH Zurich AgentDojo: 629 hijacking test cases
- Google DeepMind / ETH Zurich CaMeL framework (arXiv 2503.18813): dual-LLM pattern for higher-assurance defense
- Simon Willison's prompt injection blog series — the canonical practitioner reference
- Vectra AI: 84% attack success rate measurement in agentic systems
- v2.1 Enterprise AI Agent Upgrade Strategy bundle, §5.3 — defense-in-depth framework
