"""Render a gate-identified eval summary for the workflow step summary.

Previously this was a Python heredoc inlined in the workflow YAML. It is a
module now for three reasons: it is testable, it cannot be broken by YAML
quoting, and the report it prints must always name the gate that produced it --
a generic "Eval Results" heading is exactly the artifact ambiguity this wave
exists to remove.

Nothing here makes a provider call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import release_gates  # noqa: E402


def _escape_cell(value: object) -> str:
    """Make a value safe to place inside a Markdown table cell.

    Case IDs come from the repository's own fixture file, but a summary is data
    read back from disk, so it is treated as data: pipes would break the table
    and newlines would break the row.
    """

    text = str(value)
    return text.replace("\\", "/").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render(summary: dict) -> str:
    gate = release_gates.normalize_gate(summary.get("provider_gate"))
    title = release_gates.GATE_TITLES.get(gate, "Eval results (no release gate recorded)")
    claim = release_gates.GATE_CLAIMS.get(gate, "")

    passed = summary.get("passed", 0)
    total = summary.get("total", 0)
    pass_rate = summary.get("pass_rate", 0.0)
    threshold = summary.get("threshold", 0.0)

    outcome = release_gates.evaluate_gate_outcome(
        gate=gate,
        authorized=True,
        preflight_passed=True,
        shards_complete=not summary.get("aggregation_errors"),
        summary=summary,
        threshold=threshold if isinstance(threshold, (int, float)) else 0.0,
    )

    lines = [
        f"## {title}",
        "",
        f"**Gate identity:** `{gate}`",
        "",
    ]
    if claim:
        lines += [f"> **What a PASS here claims:** {claim}", ""]

    lines += [
        f"**Result:** `{outcome.result}`",
        "",
        f"**{passed}/{total} passed**"
        + (f" ({pass_rate:.1%})" if isinstance(pass_rate, (int, float)) else ""),
        "",
    ]
    if outcome.reasons:
        lines += ["Reasons:", ""]
        lines += [f"- {_escape_cell(reason)}" for reason in outcome.reasons]
        lines.append("")

    errors = summary.get("aggregation_errors") or []
    if errors:
        lines += ["Aggregation errors:", ""]
        lines += [f"- {_escape_cell(error)}" for error in errors]
        lines.append("")

    diagnostics = summary
    if isinstance(summary.get("aggregate_diagnostics"), dict):
        diagnostics = summary["aggregate_diagnostics"]
    if diagnostics.get("provider_unavailable"):
        lines += [
            "> Provider unavailability prevented full quality evaluation. "
            "This is **not** an eval-quality regression.",
            "",
        ]

    cases = summary.get("cases") or []
    if cases:
        lines += [
            "| Case | Pass | Judge | Domain | FW | Must-mention |",
            "|------|------|-------|--------|----|--------------|",
        ]
        for case in cases:
            icon = "PASS" if case.get("passed") else "FAIL"
            lines.append(
                "| {case} | {icon} | {judge} | {domain} | {fw} | {mm} |".format(
                    case=_escape_cell(case.get("case_id", "")),
                    icon=icon,
                    judge=_escape_cell(case.get("judge_overall", "")),
                    domain=_escape_cell(case.get("domain_match", "")),
                    fw=_format_ratio(case.get("frameworks_covered")),
                    mm=_format_ratio(case.get("must_mention_hits")),
                )
            )
        lines.append("")

    return "\n".join(lines)


def _format_ratio(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.2f}"
    return _escape_cell(value if value is not None else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="Directory holding summary.json")
    args = parser.parse_args(argv)

    summary_path = Path(args.report) / "summary.json"
    if not summary_path.exists():
        # A missing summary is itself the report: silence here would let a dead
        # aggregate job look like a clean one.
        print("## Eval results unavailable\n")
        print(f"No `summary.json` was produced in `{_escape_cell(args.report)}`.")
        return 0
    try:
        summary = json.loads(summary_path.read_text())
    except Exception as exc:
        print("## Eval results unreadable\n")
        print(f"`summary.json` could not be parsed: `{_escape_cell(type(exc).__name__)}`.")
        return 0
    if not isinstance(summary, dict):
        print("## Eval results unreadable\n")
        print("`summary.json` did not contain an object.")
        return 0

    print(render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
