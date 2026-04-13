"""
v4.3 MAS — Intake Sanitizer

Scans every project brief at ingress for prompt injection content. The
Decision Engine processes user-provided briefs, which are untrusted text
that flows directly into LLM context. Per the v2.1 enterprise strategy,
prompt injection is OWASP's #1 LLM risk and reaches 84% attack success
rate in agentic systems (Vectra AI).

Defense-in-depth posture (NOT a silver bullet):
  - This sanitizer is one layer. The other layers are:
    1. Privilege separation — the Decision Engine cannot take external
       actions (no irreversible_external in PHASE_ACTION_MAP)
    2. Output validation — phase outputs are schema-validated before
       being written back to state
    3. Capability-based access control — agents have no tool access
       outside their declared phase scope
    4. Continuous adversarial testing — eval harness includes
       adversarial briefs (golden_cases.jsonl)
  - Prompt injection cannot be fully prevented at the input layer.
    Simon Willison: "we've known about [prompt injection] for more than
    two and a half years and we still don't have convincing mitigations."
    This sanitizer raises the cost of attack and surfaces obvious attempts;
    it does not promise to catch sophisticated ones.

Behavior contract:
  - Default mode is FAIL_SOFT: log findings, flag the project, allow the
    brief to proceed. The orchestrator decides whether to halt based on
    the highest severity finding.
  - FAIL_HARD mode (configured per project) blocks any brief with a
    HIGH or CRITICAL finding before it ever reaches the LLM.
  - All findings are recorded in state.intake_sanitization_findings and
    in the policy audit log.
"""
from __future__ import annotations
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_MAX_BRIEF_LENGTH = 50_000          # ~12,500 tokens; generous for most projects
DEFAULT_MAX_LINE_LENGTH = 5_000            # single-line attacks (DOS)
DEFAULT_MAX_REPETITION_RUN = 200           # repeated character DOS
DEFAULT_MIN_SENTENCE_RATIO = 0.05          # text should have some punctuation


class Severity(str, Enum):
    INFO = "info"          # benign observation, log only
    LOW = "low"            # mild signal, worth flagging
    MEDIUM = "medium"      # likely worth review
    HIGH = "high"          # very likely an injection attempt
    CRITICAL = "critical"  # unambiguous injection or DOS attempt


# ═══════════════════════════════════════════════════════════════════════════
# DETECTION PATTERNS
# ═══════════════════════════════════════════════════════════════════════════

# Each pattern is a (compiled_regex, severity, category, rationale) tuple.
# Patterns are intentionally broad rather than precise — false positives
# in fail-soft mode are acceptable; false negatives are not.

_INSTRUCTION_OVERRIDE_PATTERNS = [
    (r"\bignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?|directives?)\b",
     Severity.CRITICAL, "instruction_override",
     "explicit attempt to override prior instructions"),
    (r"\bdisregard\s+(?:all\s+)?(?:previous|prior|above|the\s+system)\b",
     Severity.CRITICAL, "instruction_override",
     "disregard pattern targeting prior context"),
    (r"\bforget\s+(?:everything|all|the\s+above|previous)\b",
     Severity.HIGH, "instruction_override",
     "memory-clearing pattern"),
    (r"\bnew\s+instructions?\s*[:\.]",
     Severity.HIGH, "instruction_override",
     "instruction-replacement pattern"),
    (r"\boverride\s+(?:the\s+)?(?:system|previous|safety|guard)",
     Severity.CRITICAL, "instruction_override",
     "explicit override directive"),
]

_ROLE_MANIPULATION_PATTERNS = [
    (r"\byou\s+are\s+(?:now\s+)?(?:a|an)\s+\w+",
     Severity.MEDIUM, "role_manipulation",
     "role assignment attempt"),
    (r"\bpretend\s+(?:to\s+be|that\s+you|you\s+are)\b",
     Severity.HIGH, "role_manipulation",
     "pretense framing"),
    (r"\bact\s+as\s+(?:a|an|if)\b",
     Severity.MEDIUM, "role_manipulation",
     "role-play directive"),
    (r"\broleplay\s+(?:as|that)\b",
     Severity.HIGH, "role_manipulation",
     "explicit roleplay request"),
    (r"\bsystem\s*(?:prompt|message|instruction)s?\s*[:\=]",
     Severity.HIGH, "role_manipulation",
     "system-prompt injection marker"),
    (r"</?(?:system|user|assistant|instruction)s?>",
     Severity.HIGH, "role_manipulation",
     "fake conversation tags"),
]

_OUTPUT_HIJACKING_PATTERNS = [
    (r"\brespond\s+(?:only\s+)?with\s+(?:exactly|just|the\s+(?:word|text|string))",
     Severity.HIGH, "output_hijacking",
     "constrained response hijacking"),
    (r"\boutput\s+the\s+following\b",
     Severity.HIGH, "output_hijacking",
     "literal output directive"),
    (r"\bprint\s+(?:the\s+)?(?:string|text|message|secret)",
     Severity.MEDIUM, "output_hijacking",
     "print directive"),
    (r"\brepeat\s+(?:after\s+me|the\s+following|exactly)",
     Severity.HIGH, "output_hijacking",
     "repetition coercion"),
]

_TOOL_HIJACKING_PATTERNS = [
    (r"\bcall\s+(?:the\s+)?(?:function|tool|api)\s+\w+",
     Severity.HIGH, "tool_hijacking",
     "function call injection"),
    (r"\bexecute\s+(?:the\s+)?(?:command|code|script|function)",
     Severity.HIGH, "tool_hijacking",
     "execution directive"),
    (r"\b(?:run|invoke)\s+(?:the\s+)?(?:tool|function|action)\s",
     Severity.MEDIUM, "tool_hijacking",
     "tool invocation directive"),
]

_EXFILTRATION_PATTERNS = [
    (r"\b(?:reveal|show|print|output|display|expose)\s+(?:the\s+)?(?:system\s+prompt|instructions|hidden|secret|password|api[\s_]?key)",
     Severity.CRITICAL, "exfiltration",
     "secret/prompt exfiltration attempt"),
    (r"\bwhat\s+(?:are|is|were)\s+your\s+(?:original|initial|system|hidden)\s+(?:instructions?|prompts?|rules?)",
     Severity.HIGH, "exfiltration",
     "system prompt query"),
    (r"\bbase64\s*[\(:]\s*[a-zA-Z0-9+/=]{40,}",
     Severity.MEDIUM, "exfiltration",
     "base64-encoded payload"),
]

_ALL_PATTERNS = (
    _INSTRUCTION_OVERRIDE_PATTERNS
    + _ROLE_MANIPULATION_PATTERNS
    + _OUTPUT_HIJACKING_PATTERNS
    + _TOOL_HIJACKING_PATTERNS
    + _EXFILTRATION_PATTERNS
)

# Pre-compile for performance
_COMPILED_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), sev, cat, rationale)
    for pattern, sev, cat, rationale in _ALL_PATTERNS
]


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SanitizationFinding:
    severity: Severity
    category: str
    rationale: str
    matched_text: str = ""
    line_number: Optional[int] = None
    char_offset: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "category": self.category,
            "rationale": self.rationale,
            "matched_text": self.matched_text[:200],  # truncate long matches
            "line_number": self.line_number,
            "char_offset": self.char_offset,
        }


@dataclass
class SanitizationResult:
    """Result of sanitizing a brief. Contains all findings, the highest
    severity, and a recommendation."""
    findings: list[SanitizationFinding] = field(default_factory=list)
    highest_severity: Optional[Severity] = None
    recommendation: str = "allow"  # "allow" | "review" | "block"
    brief_length: int = 0
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "highest_severity": self.highest_severity.value if self.highest_severity else None,
            "recommendation": self.recommendation,
            "brief_length": self.brief_length,
            "truncated": self.truncated,
            "finding_count_by_severity": self._severity_counts(),
        }

    def _severity_counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for f in self.findings:
            counts[f.severity.value] += 1
        return counts

    def has_high_or_critical(self) -> bool:
        return any(f.severity in (Severity.HIGH, Severity.CRITICAL) for f in self.findings)


# ═══════════════════════════════════════════════════════════════════════════
# SANITIZATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def sanitize_brief(
    brief: str,
    max_length: int = DEFAULT_MAX_BRIEF_LENGTH,
    fail_hard: bool = False,
) -> SanitizationResult:
    """Scan a project brief for prompt injection content and structural issues.

    Args:
        brief: the user-provided text to scan
        max_length: max brief length in characters (longer briefs are truncated
                    and flagged)
        fail_hard: if True, the recommendation will be "block" for any HIGH or
                   CRITICAL finding. If False (default), the recommendation
                   will be "review".

    Returns:
        SanitizationResult with all findings and a recommendation.
    """
    result = SanitizationResult()
    result.brief_length = len(brief)

    # 1. Length check (DOS prevention)
    if len(brief) > max_length:
        result.findings.append(SanitizationFinding(
            severity=Severity.MEDIUM,
            category="structural",
            rationale=f"brief exceeds max length {max_length}; truncated",
            matched_text=f"length={len(brief)}",
        ))
        brief = brief[:max_length]
        result.truncated = True

    # 2. Single-line length check (single-line DOS / hidden injection)
    for line_no, line in enumerate(brief.split("\n"), start=1):
        if len(line) > DEFAULT_MAX_LINE_LENGTH:
            result.findings.append(SanitizationFinding(
                severity=Severity.MEDIUM,
                category="structural",
                rationale=f"line {line_no} exceeds {DEFAULT_MAX_LINE_LENGTH} chars",
                matched_text=line[:100] + "...",
                line_number=line_no,
            ))
            break  # only report first

    # 3. Repetition detection (DOS via repeated characters)
    repetition_match = re.search(r"(.)\1{" + str(DEFAULT_MAX_REPETITION_RUN) + r",}", brief)
    if repetition_match:
        result.findings.append(SanitizationFinding(
            severity=Severity.HIGH,
            category="structural",
            rationale=f"repeated character run > {DEFAULT_MAX_REPETITION_RUN} chars",
            matched_text=repetition_match.group(0)[:50] + "...",
            char_offset=repetition_match.start(),
        ))

    # 4. Unicode normalization check (homoglyph / control char injection)
    normalized = unicodedata.normalize("NFKC", brief)
    if normalized != brief:
        # Check whether normalization changed anything material
        diff_count = sum(1 for a, b in zip(brief, normalized) if a != b)
        if diff_count > 5:  # tolerate some natural variation
            result.findings.append(SanitizationFinding(
                severity=Severity.LOW,
                category="structural",
                rationale=f"unicode normalization changed {diff_count} characters; possible homoglyph injection",
            ))

    # 5. Control character detection
    control_chars = sum(1 for c in brief if unicodedata.category(c).startswith("C") and c not in "\n\r\t")
    if control_chars > 0:
        result.findings.append(SanitizationFinding(
            severity=Severity.MEDIUM if control_chars > 5 else Severity.LOW,
            category="structural",
            rationale=f"{control_chars} control characters found",
        ))

    # 6. Pattern matching (the core injection detection)
    for pattern, severity, category, rationale in _COMPILED_PATTERNS:
        for match in pattern.finditer(brief):
            # Compute line number for context
            line_no = brief[:match.start()].count("\n") + 1
            result.findings.append(SanitizationFinding(
                severity=severity,
                category=category,
                rationale=rationale,
                matched_text=match.group(0),
                line_number=line_no,
                char_offset=match.start(),
            ))

    # 7. Compute highest severity and recommendation
    if result.findings:
        severities = [f.severity for f in result.findings]
        # Severity ordering
        order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        result.highest_severity = max(severities, key=lambda s: order.index(s))

        if result.highest_severity == Severity.CRITICAL:
            result.recommendation = "block" if fail_hard else "review"
        elif result.highest_severity == Severity.HIGH:
            result.recommendation = "block" if fail_hard else "review"
        elif result.highest_severity == Severity.MEDIUM:
            result.recommendation = "review"
        else:
            result.recommendation = "allow"

    if result.findings:
        logger.info(
            f"intake sanitization: {len(result.findings)} finding(s); "
            f"highest={result.highest_severity.value if result.highest_severity else 'none'}; "
            f"recommendation={result.recommendation}"
        )

    return result
