"""Shared fixtures for client delivery tests."""


def fake_state() -> dict:
    """Return a complete ProjectState-like payload with clean delivery fields."""
    return {
        "project_id": "client-delivery-fixture",
        "project_name": "Placeholder Decision Review",
        "brief": "Decide whether Placeholder Co. should launch a bounded operational pilot.",
        "report": (
            "# Executive Summary\n"
            "Run a bounded pilot before committing to the full rollout.\n\n"
            "# The Decision\n"
            "Decide whether to launch the operational pilot now or defer until more data is collected.\n\n"
            "# Recommended Path\n"
            "Launch a bounded 90-day pilot with explicit stop criteria.\n\n"
            "# Why This Is Recommended\n"
            "The pilot validates operational assumptions while limiting irreversible commitment.\n\n"
            "# Roadmap\n"
            "30d: confirm owners and baseline data.\n"
            "60d: run controlled pilot workflows.\n"
            "90d: decide whether to scale or stop.\n\n"
            "# Monitoring and Kill Criteria\n"
            "Review weekly. Stop if activation remains red for two review cycles.\n"
        ),
        "strategy": {
            "executive_strategy": "Launch a bounded 90-day pilot with explicit stop criteria.",
            "confidence": "medium",
            "strategies": [
                {
                    "action": "Confirm pilot owner, data access, and decision rights.",
                    "timeline": "30d",
                    "justification": "The pilot needs an accountable operator and usable baseline data.",
                    "expected_impact": "Clear launch readiness.",
                    "evidence_chain": ["brief", {"source": "operator_note"}],
                    "risk_if_ignored": "Execution stalls before useful evidence is collected.",
                },
                {
                    "action": "Run the controlled workflow with weekly operator review.",
                    "timeline": "60 day",
                    "justification": "Controlled operation produces comparable evidence.",
                    "expected_impact": "Measured workflow reliability.",
                    "evidence_chain": ["strategy", 42],
                    "risk_if_ignored": "The team cannot distinguish adoption risk from execution risk.",
                },
                {
                    "action": "Hold scale, revise, or stop decision review.",
                    "timeline": "90",
                    "justification": "The decision needs a bounded commitment point.",
                    "expected_impact": "Explicit scale/no-scale decision.",
                    "evidence_chain": ["monitor"],
                    "risk_if_ignored": "The pilot drifts into an unreviewed rollout.",
                },
            ],
            "success_metrics": [
                "Pilot activation rate reaches at least 65%.",
                "Cycle time falls below 5 days.",
            ],
            "reentry_check": "Re-enter strategy if pilot evidence contradicts the recommendation.",
        },
        "execution_plan": [
            {
                "phase": "30d",
                "action": "Confirm pilot owner, data access, and decision rights.",
                "owner": "Pilot owner",
                "dependencies": ["executive sponsor", "data steward"],
                "evidence": ["brief", {"source": "operator_note"}],
                "notes": "The pilot needs an accountable operator and usable baseline data.",
                "success_criteria": "Clear launch readiness.",
            },
            {
                "phase": "60d",
                "action": "Run the controlled workflow with weekly operator review.",
                "owner": "Operations lead",
                "dependencies": ["pilot owner"],
                "evidence": ["strategy", 42],
                "notes": "Controlled operation produces comparable evidence.",
                "success_criteria": "Measured workflow reliability.",
            },
            {
                "phase": "90d",
                "action": "Hold scale, revise, or stop decision review.",
                "owner": "Executive sponsor",
                "dependencies": ["KPI tracker", "assumption review"],
                "evidence": ["monitor"],
                "notes": "The decision needs a bounded commitment point.",
                "success_criteria": "Explicit scale/no-scale decision.",
            },
        ],
        "critical_assumptions": [
            {
                "assumption": "Pilot teams can access required operational data.",
                "falsification_trigger": "Data access is unavailable after the first review cycle.",
                "confidence": "medium",
                "evidence": ["operator-confirmed access plan"],
            },
            {
                "assumption": "The workflow owner can hold weekly review cadence.",
                "falsification_trigger": "Two consecutive reviews are missed.",
                "confidence": "medium",
                "evidence": [{"meeting": "weekly review"}],
            },
        ],
        "kpis": [
            {
                "name": "Pilot activation rate",
                "indicator_type": "leading",
                "threshold_red": 40,
                "threshold_amber": 60,
                "actual_value": 0,
                "status": "unknown",
                "owner": "Pilot owner",
                "cadence": "weekly",
            },
            {
                "name": "Workflow cycle time",
                "indicator_type": "leading",
                "threshold_red": 8,
                "threshold_amber": 5,
                "actual_value": 0,
                "status": "unknown",
                "owner": "Operations lead",
                "cadence": "weekly",
            },
            {
                "name": "Scale decision quality",
                "indicator_type": "lagging",
                "threshold_red": 1,
                "threshold_amber": 2,
                "actual_value": 0,
                "status": "unknown",
                "owner": "Executive sponsor",
                "cadence": "90d",
            },
        ],
        "review": {
            "cadence": "weekly during pilot; final review at day 90",
            "owner": "Executive sponsor",
            "reentry_triggers": [
                "Pilot activation remains red for two review cycles.",
                "Critical data access assumption is contradicted.",
            ],
            "notes": "Human operator reviews evidence before client delivery.",
        },
    }
