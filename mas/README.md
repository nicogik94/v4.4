# v4 Multi-Agent System

**Universal Project Workflow v4.0 — A 6-phase decision engine implemented as a multi-agent system with 30 analytical frameworks, Bayesian convergence gates, and a meta-learning engine.**

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│              DETERMINISTIC ORCHESTRATOR                    │
│  (Python/LangGraph — NOT an LLM)                          │
│  ├── Phase State Machine: 6 states + gates                │
│  ├── Convergence Gate Evaluator (Bayesian thresholds)     │
│  ├── Re-entry Trigger Router (R1-R8)                      │
│  └── Downstream Invalidation Manager                      │
├──────────────────────────────────────────────────────────┤
│              SHARED STATE STORE (Blackboard)               │
│  PostgreSQL + Redis                                       │
├──────────────────────────────────────────────────────────┤
│           SPECIALIST PHASE AGENTS (8 agents)              │
│  Classify → Hypotheses → Gauntlet → Audit →              │
│  Strategy → SQI → Monitor → Report                        │
├──────────────────────────────────────────────────────────┤
│           DUAL SCORING LAYER                              │
│  📐 Deterministic (scipy) → 🤖 LLM-as-judge              │
├──────────────────────────────────────────────────────────┤
│           META-LEARNING ENGINE                            │
│  Brier scores · ECE · Framework effectiveness             │
└──────────────────────────────────────────────────────────┘
```

## Quick start

```bash
# 1. Clone and install
python -m pip install --upgrade pip==25.3
python scripts/validate_requirements_lock.py
python -m pip install -r requirements.lock.txt

# 2. Configure
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY and/or OPENAI_API_KEY

# 3. Start infrastructure
docker compose up -d  # PostgreSQL + Redis

# 4a. Run via CLI
python main.py --brief "Your project description" --name "My Project" --output result.json

# 4b. Run via API
uvicorn api:app --reload
# POST http://localhost:8000/projects {"name": "Project", "brief": "..."}
# POST http://localhost:8000/projects/{id}/run
```

## File structure

```
├── config.py                    # Model routing, gates, triggers, framework distribution
├── state.py                     # Pydantic state models (shared blackboard)
├── llm_client.py                # LLM client with retry, fallback, circuit breaker
├── orchestrator.py              # LangGraph state machine + prompt builders
├── main.py                      # CLI entry point
├── api.py                       # FastAPI REST server
├── requirements.txt            # Human-maintained direct dependency intent
├── requirements.lock.txt       # Exact Python 3.12 Linux dependency closure
├── Dockerfile
├── docker-compose.yml
├── .env.example
│
├── prompts/
│   ├── loader.py                # Router + phase module loader (v4.1+)
│   ├── router.md                # 200-line orchestrator prompt (v4.1+)
│   └── phases/                  # Phase-specific prompt modules (v4.1+)
│
├── tools/
│   └── scoring.py               # Deterministic scoring, Bayesian math, gate evaluator
│
├── sql/
│   └── init.sql                 # PostgreSQL schema (8 tables, 2 views)
│
├── tests/
│   └── test_core.py             # Test suite (gates, invalidation, scoring, Bayesian)
│
├── gpt_instructions/            # Deploy-ready GPT system prompts (9 files)
│   ├── 00-orchestrator.md
│   ├── 01-classify-agent.md
│   ├── 02-hypotheses-agent.md
│   ├── 03-gauntlet-agent.md
│   ├── 04-audit-agent.md
│   ├── 05-strategy-agent.md
│   ├── 06-sqi-agent.md
│   ├── 07-monitor-agent.md
│   └── 08-report-agent.md
│
└── shared_knowledge/
    └── v4-framework-encyclopedia.md  # All 30 frameworks, convergence math, gates, triggers
```

## Model routing

| Phase | Primary Model | Thinking Budget | Cost/Call |
|-------|--------------|-----------------|-----------|
| Classify | Haiku 4.5 | None | $0.01 |
| Hypotheses | Opus 4.6 | 15K tokens | $0.15-0.40 |
| Gauntlet | Sonnet 4.6 | 10K tokens | $0.08-0.20 |
| Audit | Sonnet 4.6 | 5K tokens | $0.08-0.25 |
| Strategy | Opus 4.6 | 20K tokens | $0.30-0.80 |
| SQI | Sonnet 4.6 | 5K tokens | $0.05-0.15 |
| Monitor | Sonnet 4.6 | None | $0.03-0.10 |
| Report | Sonnet 4.6 | 10K tokens | $0.15-0.40 |
| **Total** | | | **$0.88-2.35** |

With prompt caching (90% input savings): **$0.30-0.71/project**.

## API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/projects` | Create a project |
| GET | `/projects` | List all projects |
| GET | `/projects/:id` | Get project summary |
| GET | `/projects/:id/state` | Get full state (JSON) |
| POST | `/projects/:id/run` | Run full workflow (background) |
| POST | `/projects/:id/phase` | Run single phase |
| GET | `/projects/:id/gate/:phase` | Check gate status |
| GET | `/projects/:id/report` | Get final report |

## Testing

```bash
cd tests
pytest test_core.py -v
```

## GPT deployment

The `gpt_instructions/` folder contains 9 deploy-ready instruction files:
1. Upload `shared_knowledge/v4-framework-encyclopedia.md` as a knowledge file
2. Create 9 custom GPTs, each using its instruction file
3. The orchestrator GPT calls specialist GPTs as tools

## License

Proprietary — RegexSEO / Nicolás
