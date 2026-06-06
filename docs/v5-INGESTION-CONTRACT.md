# v5 Ingestion Contract

This document describes the additive project-creation contract adapter in the local v4.4/v5 runtime foundation. It is an API intake contract only. It does not change prompts, report semantics, provider routing, workflow phase order, queue/runtime architecture, export profiles, or machine archive schema.

## Supported Inputs

`POST /projects` accepts the legacy project shape and the versioned `case.v1` shape.

### `case.v1`

Use `case.v1` when an upstream system has a case record or wants explicit provenance labels:

```json
{
  "contract_version": "case.v1",
  "name": "Expansion decision",
  "brief": "Decide whether to expand the pilot to the next cohort.",
  "data": "Activation is 41% for the last cohort.",
  "source": "crm",
  "external_case_id": "case-123",
  "metadata": {
    "segment": "midmarket",
    "priority": "high"
  }
}
```

Runtime normalization maps both accepted shapes to the canonical fields used by the engine:

- `name` -> `ProjectState.project_name`
- `brief` -> `ProjectState.brief`
- `data` -> `ProjectState.data`

The provenance fields are stored separately as metadata defaults on `ProjectState`:

- `ingestion_contract_version`
- `ingestion_source`
- `ingestion_external_case_id`
- `ingestion_metadata`

Workspace and queue responses expose additive provenance labels under `input_contract` and `response_metadata` so operators can see which intake path created the project. These labels are for debugging and operator orientation; they are not authorization controls.

### Legacy Compatibility

Existing clients can continue sending:

```json
{
  "name": "Expansion decision",
  "brief": "Decide whether to expand the pilot.",
  "data": "Optional supporting notes."
}
```

`data` remains optional and defaults to an empty string. Legacy projects default to:

```json
{
  "ingestion_contract_version": "legacy.v1",
  "ingestion_source": "operator",
  "ingestion_external_case_id": "",
  "ingestion_metadata": {}
}
```

Old stored `ProjectState` snapshots that do not contain ingestion metadata load with those defaults.

## Conflict Rejection

Do not mix legacy top-level project fields with a separate `case` envelope. A request that sends both a legacy `name`, `brief`, or `data` and a `case` object is rejected with HTTP 400. Unsupported `contract_version` values are also rejected with HTTP 400.

## Request Correlation

Every API response includes `X-Request-ID`.

- A safe supplied `X-Request-ID` value is echoed.
- If the header is absent or unsafe, the API generates a UUID.
- The request ID is returned in the response header only.
- It is not authentication, not a security boundary, and not persisted into project state.

Use this header to correlate local logs, API calls, and dashboard/debug sessions. Do not treat it as a tenant, user, session, or authorization identifier.

## Run IDs

`POST /projects/{project_id}/run` returns a `run_id` when a workflow run starts. The `run_id` tracks workflow execution state and duplicate active-run guards. It is separate from `X-Request-ID`.

If a project is already complete, the run endpoint returns `already_complete` without starting a new run. If a workflow is already queued or running for a project, duplicate starts are rejected with HTTP 409.

## Local-Only Caveats

This repository remains a local, single-operator runtime foundation unless separately hardened.

- No API authentication is implemented.
- No tenancy or tenant isolation is implemented.
- No public deployment hardening is implemented.
- No rate limiting, encryption layer, or hosted security boundary is added by this contract.

Do not expose this API publicly without adding and testing the required auth, network controls, tenancy model, rate limits, secret management, logging policy, and deployment hardening.

## Non-Changes

The ingestion contract adapter is deliberately narrow. It does not:

- alter prompt text or prompt routing
- alter report wording, report section semantics, or report generation behavior
- change provider routing, fallback, or model selection
- change workflow phase order, run-start behavior, or queue/runtime architecture
- change export profiles
- change machine archive file names, manifest `included_files`, or `export_schema_version`
