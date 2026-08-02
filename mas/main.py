"""
v4 Multi-Agent System — Entry Point
Run a project through the full 6-phase workflow.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime

from state import ProjectState
from orchestrator import compile_workflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("v4-workflow")

from provider_telemetry import (  # noqa: E402 - after logging configuration
    ENTRY_POINT_CLI_SINGLE_PHASE,
    ENTRY_POINT_CLI_WORKFLOW,
    telemetry_scope,
)


async def run_project(brief: str, data: str = "", name: str = "New Project") -> ProjectState:
    """Execute the full v4 workflow on a project."""
    state = ProjectState(
        project_id=str(uuid.uuid4()),
        project_name=name,
        brief=brief,
        data=data,
        created_at=datetime.now(),
    )

    logger.info(f"🚀 Starting project: {name} ({state.project_id[:8]})")
    logger.info(f"   Brief: {brief[:100]}...")

    # Compile workflow (no checkpointer for local runs)
    workflow = compile_workflow()

    # Run the full pipeline inside a telemetry run scope. The CLI is a supported
    # entry point: it knows the project it created and names the run after it,
    # so telemetry never has to record an absent identity for a CLI workflow.
    async with telemetry_scope(
        entry_point=ENTRY_POINT_CLI_WORKFLOW,
        project_id=state.project_id,
        run_id=state.project_id,
    ):
        final_state = await workflow.ainvoke(state)

    # Summary
    logger.info("=" * 60)
    logger.info(f"✅ Project complete: {name}")
    logger.info(f"   Phase: {final_state.current_phase}")
    logger.info(f"   Classify: {final_state.classify.domain if final_state.classify else 'N/A'}")
    logger.info(f"   Hypotheses: {len(final_state.hypotheses or [])}")
    logger.info(f"   Audit: {'data-backed' if final_state.audit and final_state.audit.data_based else 'predicted'}")
    logger.info(f"   Strategies: {len(final_state.strategy.strategies) if final_state.strategy else 0}")
    logger.info(f"   SQI: {final_state.sqi.sqi_overall if final_state.sqi else 'N/A'}")
    logger.info(f"   Det Scores: {final_state.det_scores.overall if final_state.det_scores else 'N/A'}")
    logger.info(f"   Brier: {final_state.brier_score or 'N/A'}")
    logger.info(f"   Re-entries: {len(final_state.reentry_triggers_fired)}")
    logger.info("=" * 60)

    return final_state


async def run_single_phase(state: ProjectState, phase: str) -> ProjectState:
    """Run a single phase (for interactive/step-by-step mode)."""
    from orchestrator import run_phase_node

    async with telemetry_scope(
        entry_point=ENTRY_POINT_CLI_SINGLE_PHASE,
        project_id=state.project_id,
        run_id=state.project_id,
        phase=phase,
        expected_phases=(phase,),
    ):
        return await run_phase_node(state, phase)


# ═══ CLI ═══

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="v4 Multi-Agent Workflow")
    parser.add_argument("--brief", "-b", type=str, help="Project brief text")
    parser.add_argument("--brief-file", "-f", type=str, help="Path to brief file")
    parser.add_argument("--data", "-d", type=str, default="", help="Path to data file")
    parser.add_argument("--name", "-n", type=str, default="CLI Project", help="Project name")
    parser.add_argument("--output", "-o", type=str, default="output.json", help="Output file path")

    args = parser.parse_args()

    brief = args.brief
    if args.brief_file:
        with open(args.brief_file) as f:
            brief = f.read()

    if not brief:
        print("Error: provide --brief or --brief-file")
        exit(1)

    data = ""
    if args.data:
        with open(args.data) as f:
            data = f.read()

    final = asyncio.run(run_project(brief, data, args.name))

    # Save full state
    with open(args.output, "w") as f:
        json.dump(final.model_dump(mode="json"), f, indent=2, default=str)
    print(f"\n📁 Full state saved to {args.output}")
