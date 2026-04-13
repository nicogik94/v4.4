"""
v4 MAS — Observability
Thin Langfuse wrapper. Enable by setting LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY.
If keys are absent, every call is a no-op — no behavior change.
"""
import os
import logging
from contextlib import contextmanager
from typing import Optional, Any

logger = logging.getLogger(__name__)

_langfuse = None
_enabled = False

try:
    from langfuse import Langfuse
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        _langfuse = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        _enabled = True
        logger.info("Langfuse tracing enabled")
except Exception as e:
    logger.info(f"Langfuse disabled: {e}")


def enabled() -> bool:
    return _enabled


@contextmanager
def trace_phase(project_id: str, phase: str, metadata: Optional[dict] = None):
    """Wrap a phase execution. Use as: `with trace_phase(pid, 'classify') as t: ...`"""
    if not _enabled:
        yield None
        return
    trace = _langfuse.trace(
        name=f"phase.{phase}",
        session_id=project_id,
        metadata=metadata or {},
    )
    try:
        yield trace
    except Exception as e:
        trace.update(level="ERROR", status_message=str(e))
        raise
    finally:
        _langfuse.flush()


def log_llm_call(
    trace,
    model: str,
    phase: str,
    prompt: str,
    response: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    latency_ms: float = 0.0,
    cost_usd: float = 0.0,
):
    """Attach an LLM generation to an existing trace."""
    if not _enabled or trace is None:
        return
    try:
        trace.generation(
            name=f"llm.{phase}",
            model=model,
            input=prompt[:4000],  # truncate for UI readability
            output=response[:4000],
            usage={
                "input": input_tokens,
                "output": output_tokens,
                "cache_read_input_tokens": cache_read_tokens,
                "total_cost": cost_usd,
            },
            metadata={"latency_ms": latency_ms, "phase": phase},
        )
    except Exception as e:
        logger.warning(f"Langfuse log failed (non-fatal): {e}")


def flush():
    if _enabled:
        _langfuse.flush()
