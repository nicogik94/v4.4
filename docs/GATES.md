# Delivery Generator Gates

## Client Delivery Generator v0.5 Gate

Client Delivery Generator v0.5 is a local/operator-first post-report export module. It may generate:

- `strategic_decision_board_memo.docx`
- `decision_execution_tracker.xlsx`
- `delivery_manifest.json`

The v0.5 manifest must carry:

```json
{
  "validation_status": "awaiting_side_by_side_defense_test"
}
```

Warnings do not block export in v0.5. Every generated artifact requires human review before client delivery.

## v0.5 to v0.6 Side-by-Side Defense Test

The Side-by-Side Defense Test is the release gate before expanding the delivery surface beyond the v0.5 local artifacts. Until that gate passes, do not add:

- PPTX renderer
- 5-page executive summary template
- Dashboard UI button or frontend wiring
- Monitoring portal, Slack webhook, or email digest
- Bayesian feedback module
- Sheets API integration
- Asana, ClickUp, Notion, ServiceTitan, or HubSpot connectors
- Public SaaS endpoint
- Multi-tenant workspace, SSO, SAML, or RBAC
- Mobile app
- Autonomous action agent on monitoring data

## Timebox

The v0.5 implementation is capped at 10 working days from branch creation. If tests are not green and the worked example does not render cleanly inside that window, the fallback is to reduce the XLSX to three sheets:

- Decision Summary
- 30-60-90 Actions
- KPI Tracker
