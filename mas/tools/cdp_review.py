"""Internal read-only CDP v0.1 citation resolvability review tool."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdp.citation_resolvability import (  # noqa: E402
    build_defense_pass_result,
    build_evidence_locator_registry,
)
from extensions.runtime import GatewayRequest, RoutingContext  # noqa: E402
from state import ProjectState  # noqa: E402


PRIMARY_PROVIDER = "anthropic"
PRIMARY_MODEL = "claude-sonnet-4-6"
RESOLVER_STATUSES = (
    "resolved_exact",
    "resolved_id_only",
    "unknown_evidence_id",
    "locator_mismatch",
    "malformed",
)
TERMINAL_NONPASS_STATUSES = {"BLOCKED", "FAIL", "PROVIDER_INCONCLUSIVE"}
ANTI_OVERCLAIMING_LABELS = [
    "CDP v0.1 is review-only citation resolvability.",
    "resolved_id_only means evidence-ID traceability only.",
    "resolved_id_only is weaker than resolved_exact.",
    "This does not verify semantic support.",
    "This does not prove full claim defensibility.",
    "This does not rewrite, strip, or correct report text.",
    "Load-bearing findings are line-level review prompts, not claim cards.",
]
LOCATOR_PRECISION_CAVEAT = (
    "Locator precision caveat: this project traces primarily to evidence IDs without specific "
    "locator anchors. resolved_id_only is weaker than resolved_exact and does not prove "
    "page/chunk/row-level support."
)
ALL_ID_ONLY_CAVEAT = (
    "All resolved markers are ID-only. This shows evidence-ID traceability, not locator-level "
    "precision or semantic support."
)


@dataclass(frozen=True)
class ReviewOptions:
    regenerate_report: bool = False
    require_primary_anthropic: bool = False
    sample_limit: int = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Internal read-only CDP v0.1 citation resolvability review."
    )
    parser.add_argument(
        "--project-id",
        action="append",
        required=True,
        help="Project ID to review. Repeat for multiple projects.",
    )
    parser.add_argument(
        "--regenerate-report",
        action="store_true",
        help="Regenerate the report phase in memory before CDP review.",
    )
    parser.add_argument(
        "--confirm-regenerate",
        action="store_true",
        help="Required with --regenerate-report to confirm provider cost.",
    )
    parser.add_argument(
        "--require-primary-anthropic",
        action="store_true",
        help="Mark regenerated reviews provider-inconclusive unless primary Anthropic route is used.",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Print only structured JSON.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=3,
        help="Maximum sample resolutions and malformed candidates to print.",
    )
    return parser


def validate_args(args: argparse.Namespace) -> str:
    if args.regenerate_report and not args.confirm_regenerate:
        return (
            "--regenerate-report requires --confirm-regenerate. "
            "No provider call was made."
        )
    return ""


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _rate(count: int, total_markers: int) -> float | None:
    if total_markers == 0:
        return None
    return float(count / total_markers)


def build_report_request(project_id: str, system: str, prompt: str) -> GatewayRequest:
    return GatewayRequest(
        phase="report",
        system_prompt=system,
        user_prompt=prompt,
        project_id=project_id,
        agent_name="report",
        routing_context=RoutingContext(phase="report"),
        allow_cache=False,
    )


async def generate_report(project_id: str, state: ProjectState):
    from config import MODEL_ROUTING
    from llm_client import _get_provider_gateway
    from orchestrator import build_report_prompt, build_system_prompt

    prompt = build_report_prompt(state)
    system = build_system_prompt("report", json_mode=False)
    request = build_report_request(project_id, system, prompt)
    return await _get_provider_gateway().call(
        request,
        config_override=MODEL_ROUTING["report"],
    )


async def load_project_state(project_id: str) -> ProjectState | None:
    import store

    return await store.load(project_id)


def _blocked_result(project_id: str, reason: str, *, project_name: str = "") -> dict[str, Any]:
    return {
        "project_id": project_id,
        "project_name": project_name,
        "status": "BLOCKED",
        "status_reason": reason,
    }


def _fail_result(project_id: str, reason: str, *, project_name: str = "", error: str = "") -> dict[str, Any]:
    payload = {
        "project_id": project_id,
        "project_name": project_name,
        "status": "FAIL",
        "status_reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


def _provider_status(response) -> dict[str, Any]:
    provider = str(getattr(response, "provider_used", "") or "")
    model = str(getattr(response, "model_used", "") or "")
    fallback_used = bool(getattr(response, "fallback_used", False))
    route_status = (
        "primary_anthropic"
        if provider == PRIMARY_PROVIDER and model == PRIMARY_MODEL and fallback_used is False
        else "provider_inconclusive"
    )
    return {
        "provider": provider,
        "model": model,
        "fallback_used": fallback_used,
        "route_status": route_status,
    }


def serialize_review_result(
    *,
    project_id: str,
    project_name: str,
    report_text: str,
    result,
    regenerated_report: bool,
    sample_limit: int,
    status: str = "PASS",
    status_reason: str = "completed",
    provider_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status_counts = Counter(item.status for item in result.resolutions)
    resolver_status_counts = {
        status_name: status_counts.get(status_name, 0)
        for status_name in RESOLVER_STATUSES
    }
    total_markers = sum(resolver_status_counts.values())
    review_eligible_count = sum(1 for item in result.resolutions if item.review_eligible)
    resolved_exact = resolver_status_counts["resolved_exact"]
    resolved_id_only = resolver_status_counts["resolved_id_only"]
    malformed = resolver_status_counts["malformed"]

    payload = {
        "project_id": project_id,
        "project_name": project_name,
        "status": status,
        "status_reason": status_reason,
        "source_report_sha256": _sha256(report_text),
        "report_has_markers": "[Evidence:" in (report_text or ""),
        "regenerated_report": regenerated_report,
        "total_markers": total_markers,
        "canonical_marker_count": len(result.markers),
        "resolver_status_counts": resolver_status_counts,
        "malformed_candidate_count": len(result.malformed_candidates),
        "load_bearing_review_count": len(result.load_bearing_reviews),
        "review_eligible_resolution_count": review_eligible_count,
        "resolved_exact_rate": _rate(resolved_exact, total_markers),
        "resolved_id_only_rate": _rate(resolved_id_only, total_markers),
        "malformed_rate": _rate(malformed, total_markers),
        "review_eligible_resolution_rate": _rate(review_eligible_count, total_markers),
        "sample_resolutions": [
            {
                "status": item.status,
                "marker": item.marker,
                "evidence_id": item.evidence_id,
                "locator": item.locator,
                "review_eligible": item.review_eligible,
                "review_reason": item.review_reason,
            }
            for item in result.resolutions[:sample_limit]
        ],
        "sample_malformed_candidates": list(result.malformed_candidates[:sample_limit]),
        "report_text_preserved": bool(result.report_text_preserved),
        "source_hash_matches_report": result.source_report_sha256 == _sha256(report_text),
        "missing_inputs": list(result.missing_inputs),
        "extraction_limitations": list(result.extraction_limitations),
    }
    if provider_status:
        payload.update(provider_status)
    return payload


async def review_project(
    project_id: str,
    options: ReviewOptions,
    *,
    loader=load_project_state,
    report_generator=generate_report,
) -> dict[str, Any]:
    try:
        state = await loader(project_id)
    except ModuleNotFoundError as exc:
        return _blocked_result(project_id, f"dependency_unavailable: {exc}")
    except (ConnectionError, OSError) as exc:
        return _blocked_result(project_id, f"persistence_unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _fail_result(project_id, "store_load_error", error=f"{type(exc).__name__}: {exc}")

    if state is None:
        if not os.getenv("DATABASE_URL"):
            return _blocked_result(project_id, "DATABASE_URL unavailable and project was not found in memory")
        return _blocked_result(project_id, "project_not_found")

    project_name = str(getattr(state, "project_name", "") or "")
    original_report = str(getattr(state, "report", "") or "")
    if not original_report:
        return _blocked_result(project_id, "report_missing", project_name=project_name)

    if not build_evidence_locator_registry(state):
        return _blocked_result(project_id, "evidence_state_unavailable", project_name=project_name)

    original_dump = state.model_dump(mode="json")
    active_state = state
    provider_status: dict[str, Any] | None = None
    status = "PASS"
    status_reason = "completed"

    if options.regenerate_report:
        active_state = ProjectState.model_validate(state.model_dump(mode="json"))
        try:
            response = await report_generator(project_id, active_state)
        except Exception as exc:  # noqa: BLE001
            return _fail_result(project_id, "report_regeneration_error", project_name=project_name, error=f"{type(exc).__name__}: {exc}")

        provider_status = _provider_status(response)
        error = str(getattr(response, "error", "") or "")
        report_text = str(getattr(response, "text", "") or "")
        if error or not report_text:
            return _fail_result(
                project_id,
                error or "empty_report_text",
                project_name=project_name,
            ) | provider_status
        active_state.report = report_text
        if options.require_primary_anthropic and provider_status["route_status"] != "primary_anthropic":
            status = "PROVIDER_INCONCLUSIVE"
            status_reason = "provider_route_not_primary_anthropic"

    report_text = str(getattr(active_state, "report", "") or "")
    try:
        cdp_result = build_defense_pass_result(active_state)
        payload = serialize_review_result(
            project_id=project_id,
            project_name=project_name,
            report_text=report_text,
            result=cdp_result,
            regenerated_report=options.regenerate_report,
            sample_limit=max(0, options.sample_limit),
            status=status,
            status_reason=status_reason,
            provider_status=provider_status,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail_result(project_id, "cdp_review_error", project_name=project_name, error=f"{type(exc).__name__}: {exc}")

    if state.model_dump(mode="json") != original_dump:
        return _fail_result(project_id, "project_state_mutated", project_name=project_name)
    return payload


def build_payload(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tool": "cdp_review",
        "schema_version": "cdp.v0.1",
        "generated_at": datetime.now().isoformat(),
        "scope": {
            "read_only": True,
            "store_save_called": False,
            "persisted_state_mutated": False,
            "product_surface": False,
        },
        "anti_overclaiming_labels": list(ANTI_OVERCLAIMING_LABELS),
        "overall_status": "PASS" if all(item.get("status") == "PASS" for item in results) else "ATTENTION_REQUIRED",
        "results": results,
    }


def _format_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} ({value * 100:.0f}%)"


def human_summary(payload: dict[str, Any]) -> str:
    lines = [
        "HUMAN SUMMARY",
        "=============",
        f"overall_status: {payload.get('overall_status')}",
        "",
        "Anti-overclaiming labels:",
    ]
    lines.extend(f"- {label}" for label in ANTI_OVERCLAIMING_LABELS)

    for item in payload.get("results", []):
        counts = item.get("resolver_status_counts") or {}
        resolved_exact = int(counts.get("resolved_exact", 0) or 0)
        resolved_id_only = int(counts.get("resolved_id_only", 0) or 0)
        lines.extend(
            [
                "",
                f"Project {item.get('project_id')} ({item.get('project_name', '')})",
                f"  status: {item.get('status')} ({item.get('status_reason', '')})",
                f"  regenerated_report: {item.get('regenerated_report')}",
                f"  report_has_markers: {item.get('report_has_markers')}",
                f"  total_markers: {item.get('total_markers')}",
                f"  resolved_exact: {resolved_exact} rate={_format_rate(item.get('resolved_exact_rate'))}",
                f"  resolved_id_only: {resolved_id_only} rate={_format_rate(item.get('resolved_id_only_rate'))}",
                f"  malformed: {counts.get('malformed', 0)} rate={_format_rate(item.get('malformed_rate'))}",
                f"  review_eligible_resolution_count: {item.get('review_eligible_resolution_count')}",
                f"  review_eligible_resolution_rate: {_format_rate(item.get('review_eligible_resolution_rate'))}",
                f"  load_bearing_review_count: {item.get('load_bearing_review_count')}",
                f"  report_text_preserved: {item.get('report_text_preserved')}",
            ]
        )
        if item.get("provider") or item.get("model"):
            lines.append(
                f"  provider_route: {item.get('provider')} / {item.get('model')} / "
                f"fallback_used={item.get('fallback_used')} ({item.get('route_status')})"
            )
        if resolved_exact == 0 and resolved_id_only > 0:
            lines.append(f"  NOTE: {ALL_ID_ONLY_CAVEAT}")
        elif (
            item.get("resolved_id_only_rate") is not None
            and item.get("resolved_exact_rate") is not None
            and item["resolved_id_only_rate"] > item["resolved_exact_rate"]
        ):
            lines.append(f"  NOTE: {LOCATOR_PRECISION_CAVEAT}")
        if item.get("sample_resolutions"):
            lines.append("  sample_resolutions:")
            for sample in item["sample_resolutions"]:
                lines.append(f"    [{sample['status']}] {sample['marker'][:140]}")
        if item.get("sample_malformed_candidates"):
            lines.append("  sample_malformed_candidates:")
            for sample in item["sample_malformed_candidates"]:
                lines.append(f"    {sample[:140]}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    options = ReviewOptions(
        regenerate_report=bool(args.regenerate_report),
        require_primary_anthropic=bool(args.require_primary_anthropic),
        sample_limit=max(0, int(args.sample_limit or 0)),
    )
    if not os.getenv("DATABASE_URL"):
        results = [
            _blocked_result(project_id, "DATABASE_URL unavailable")
            for project_id in args.project_id
        ]
    else:
        results = [
            await review_project(project_id, options)
            for project_id in args.project_id
        ]
    payload = build_payload(results)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not args.json_only:
        print()
        print(human_summary(payload))
    return 1 if any(item.get("status") in TERMINAL_NONPASS_STATUSES for item in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validation_error = validate_args(args)
    if validation_error:
        print(validation_error, file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
