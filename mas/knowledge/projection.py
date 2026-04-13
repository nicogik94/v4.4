"""Structured, prompt-safe projection for knowledge items.

This module does not expose raw payload dumps. It builds a bounded projection
that later orchestration layers may consume, but tranche 3B keeps this
projection API-facing only.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from state import KnowledgeItem, SourceRegistryEntry


class KnowledgeProjectionFact(BaseModel):
    key: str
    value: str


class ProjectedKnowledgeItem(BaseModel):
    item_id: str
    source_id: str
    source_name: str = ""
    title: str = ""
    summary: str = ""
    facts: list[KnowledgeProjectionFact] = Field(default_factory=list)
    observed_at: str = ""
    freshness_status: str = ""
    trust_tier: str = ""
    sensitivity: str = ""
    untrusted_source: bool = True
    prompt_exposure_note: str = ""


def project_knowledge_item(
    item: KnowledgeItem,
    source: SourceRegistryEntry | None,
    *,
    allowed_text_fields: list[str],
    allowed_structured_keys: list[str],
    max_title_chars: int,
    max_summary_chars: int,
    max_fact_value_chars: int,
    max_facts: int,
) -> ProjectedKnowledgeItem | None:
    title = ""
    if "title" in allowed_text_fields:
        title = _clip(item.title, max_title_chars)

    summary = ""
    if "summary" in allowed_text_fields:
        summary = _clip(item.summary, max_summary_chars)

    allowed_key_set = {str(key).strip() for key in allowed_structured_keys if str(key).strip()}
    facts: list[KnowledgeProjectionFact] = []
    for key in allowed_structured_keys:
        if len(facts) >= max_facts:
            break
        value = (item.structured_payload or {}).get(key)
        if not _is_prompt_safe_scalar(value):
            continue
        rendered = _clip(_stringify_scalar(value), max_fact_value_chars)
        if not rendered:
            continue
        facts.append(KnowledgeProjectionFact(key=str(key), value=rendered))

    # Also permit any explicitly allowed scalar key present in the payload,
    # preserving the declared policy order first and then filling remaining
    # slots deterministically.
    if len(facts) < max_facts and allowed_key_set:
        seen = {fact.key for fact in facts}
        for key in sorted((item.structured_payload or {}).keys()):
            if len(facts) >= max_facts:
                break
            if key in seen or key not in allowed_key_set:
                continue
            value = item.structured_payload.get(key)
            if not _is_prompt_safe_scalar(value):
                continue
            rendered = _clip(_stringify_scalar(value), max_fact_value_chars)
            if not rendered:
                continue
            facts.append(KnowledgeProjectionFact(key=str(key), value=rendered))

    if not title and not summary and not facts:
        return None

    return ProjectedKnowledgeItem(
        item_id=item.item_id,
        source_id=item.source_id,
        source_name=source.name if source else "",
        title=title,
        summary=summary,
        facts=facts,
        observed_at=item.observed_at,
        freshness_status=item.freshness_status.value if hasattr(item.freshness_status, "value") else str(item.freshness_status),
        trust_tier=item.trust_tier,
        sensitivity=item.sensitivity,
        untrusted_source=item.untrusted_source,
        prompt_exposure_note="Structured projection only; raw payload and prompt dumps are not exposed.",
    )


def _is_prompt_safe_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and value is not None


def _stringify_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _clip(value: str | None, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
