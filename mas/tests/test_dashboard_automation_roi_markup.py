"""Markup/behavior regression checks for the Automation ROI operator workspace UI.

These assert the canonical dashboard wires the Slice B operator workspace safely:
project-type + feature gating, the exact six roles, no manual result/provenance/
formula/sequence inputs, permitted-API-only writes, no auto-retry, authoritative
reload after every mutation, completeness-only calculate gating, safe error
mapping, escaping, and read-only rendering/refresh.
"""
import re
import unittest
from pathlib import Path


_HERE = Path(__file__).resolve()
HTML_PATH = None
for root in (_HERE.parents[1], _HERE.parents[2]):
    candidate = root / "dashboards" / "index.html"
    if candidate.exists():
        HTML_PATH = candidate
        break
if HTML_PATH is None:
    HTML_PATH = _HERE.parents[1] / "dashboards" / "index.html"


class TestAutomationRoiWorkspaceMarkup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HTML_PATH.exists():
            raise unittest.SkipTest("dashboard bundle is not mounted in this environment")
        cls.html = HTML_PATH.read_text(encoding="utf-8")

    # 1 — workspace appears only for Automation ROI projects
    def test_workspace_is_project_type_and_feature_gated(self):
        # Regression: isAutomationRoiProject() is called with no argument from
        # renderTabs/normalizeWorkspaceTab, so it MUST default to the currently
        # loaded project — otherwise the tab never renders for ROI projects.
        self.assertIn(
            "function isAutomationRoiProject(project = state.project?.fullState) {",
            self.html,
        )
        self.assertNotIn("function isAutomationRoiProject(project) {", self.html)
        self.assertIn("classToken(project?.project_type) === 'automation_roi'", self.html)
        self.assertIn("function workspaceTabsForProject", self.html)
        # renderTabs gates the Automation ROI tab on the (argument-less) check.
        render_tabs = re.search(r"function renderTabs\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(render_tabs)
        self.assertIn("if (isAutomationRoiProject()) tabs.push(AUTOMATION_ROI_TAB);", render_tabs.group(0))
        self.assertIn("if (isAutomationRoiProject()) tabs.push(AUTOMATION_ROI_TAB);", self.html)
        self.assertIn("const AUTOMATION_ROI_TAB = 'automation_roi';", self.html)
        # WORKSPACE_TABS itself is never mutated (other markup tests rely on it).
        self.assertIn("const WORKSPACE_TABS = ['overview', 'workflow', 'evidence', 'trace', 'report']", self.html)
        self.assertIn("if (state.activeTab === AUTOMATION_ROI_TAB) return renderAutomationRoiWorkspace();", self.html)

    # 2 — all six roles render exactly once
    def test_six_roles_present_exactly_once(self):
        roles = [
            "baseline_hours_per_period",
            "post_automation_hours_per_period",
            "fully_loaded_rate_per_hour",
            "periods_per_year",
            "annual_recurring_cost",
            "one_time_implementation_cost",
        ]
        match = re.search(r"const ROI_ROLES = \[(.*?)\];", self.html, re.DOTALL)
        self.assertIsNotNone(match)
        listed = re.findall(r"'([^']+)'", match.group(1))
        self.assertEqual(listed, roles)
        self.assertEqual(len(listed), len(set(listed)))
        for role in roles:
            self.assertIn(f"{role}:", self.html)  # has a ROI_ROLE_LABELS entry
        # The frozen-six and calculate sections iterate the canonical role list.
        self.assertIn("ROI_ROLES.map(role =>", self.html)

    # 3 — no manual result/provenance/formula/sequence fields exist
    def test_no_manual_result_provenance_formula_sequence_inputs(self):
        for forbidden in ("provenance_fingerprint", "formula_input_digest", "decision_seq", "storage_ref"):
            self.assertNotIn(forbidden, self.html)
        # No editable inputs for resolved value / provenance / formula in the fact form.
        for field_id in ('id="roi-fact-resolved', 'id="roi-fact-provenance', 'id="roi-fact-formula', 'id="roi-fact-status'):
            self.assertNotIn(field_id, self.html)

    # 4 — write requests use only permitted API fields
    def test_writes_use_only_permitted_api_fields(self):
        self.assertIn("function readRoiFactForm()", self.html)
        self.assertIn("body: { decision_type: action },", self.html)
        self.assertIn("input_role: role,", self.html)
        self.assertIn("approval_decision_id: fact.active_approval_id,", self.html)
        self.assertIn("body: { inputs },", self.html)
        # The fact value is sent as a string, never a JS number.
        self.assertIn("if (value) fact.value = value;", self.html)

    # 5 — no automatic mutation retry
    def test_no_automatic_mutation_retry(self):
        self.assertIn("never auto-retry", self.html)
        self.assertIn("async function roiMutate", self.html)
        # The mutate helper does not schedule a retry.
        mutate = re.search(r"async function roiMutate\(.*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(mutate)
        self.assertNotIn("setTimeout", mutate.group(0))

    # 6 — every mutation reloads authoritative workspace state
    def test_mutation_reloads_authoritative_state(self):
        self.assertIn(
            "if (state.selectedProjectId) await loadAutomationRoiWorkspace(state.selectedProjectId);",
            self.html,
        )

    # 7 — incomplete/duplicate/extra maps disable calculate
    def test_calculate_gated_on_role_map_completeness(self):
        self.assertIn("function classifyRoiRoleMap(roles)", self.html)
        self.assertIn("function roiCalcMapComplete()", self.html)
        self.assertIn("const complete = roiCalcMapComplete();", self.html)
        self.assertIn("const calcDisabled = (!complete || busy) ? 'disabled' : '';", self.html)
        self.assertIn("if (!roiCalcMapComplete()) return;", self.html)

    # 8 — six-role maps with availability warnings still allow server submission
    def test_calculate_not_gated_on_availability(self):
        # Gating depends only on map shape; availability is shown as a warning but
        # never disables submission (the server owns blocked / not_applicable).
        self.assertIn("gates the Calculate action", self.html)
        self.assertIn("never on evidence availability", self.html)
        self.assertIn("evidence unavailable — server decides", self.html)

    # 9 — 404 / 409 / 422 / 503 map to safe messages
    def test_error_status_mapping_is_safe(self):
        self.assertIn("function classifyRoiError(error, context)", self.html)
        self.assertIn("Automation ROI is not enabled in this environment.", self.html)
        self.assertIn("This ROI record is no longer available.", self.html)
        self.assertIn("Automation ROI data is temporarily unavailable.", self.html)
        self.assertIn("The information provided was incomplete or invalid.", self.html)
        self.assertIn("This action conflicts with the current state. The workspace was refreshed.", self.html)
        # Raw response detail is never rendered for ROI errors (title only).
        self.assertIn("toast(classifyRoiError(e, 'mutation').title, 'err');", self.html)

    # 10 — hostile text is escaped
    def test_api_derived_text_is_escaped(self):
        self.assertIn("escapeHtml(f.subject_label", self.html)
        self.assertIn("escapeHtml(roi.error.title)", self.html)
        self.assertIn("escapeHtml(s.source_kind", self.html)
        self.assertIn("escapeHtml(c)", self.html)  # client caveats

    # 11 — opaque ids live in data-* values, not as display text
    def test_opaque_ids_only_in_data_values(self):
        self.assertIn('data-roi-fact="${escapeHtml(f.candidate_fact_revision_id)}"', self.html)
        self.assertIn('data-roi-result="${escapeHtml(r.result_id)}"', self.html)
        self.assertIn('data-fact="${escapeHtml(f.candidate_fact_revision_id)}"', self.html)
        # Immutability + correction-not-edit affordance.
        self.assertIn("Create correction", self.html)
        self.assertIn("corrections create a new fact, never an edit", self.html)

    # 12 — rendering and refresh issue no writes (read-only GET only)
    def test_workspace_load_is_read_only(self):
        self.assertIn(
            "await apiGet(`/projects/${encodeURIComponent(pid)}/automation-roi/workspace`);",
            self.html,
        )
        # The client-safe preview is the existing authoritative endpoint, not a
        # browser-built transform of operator data.
        self.assertIn("await apiGet(`${base}/client`);", self.html)
        self.assertIn("browser never derives a", self.html)
        # The render function itself performs no POSTs.
        render = re.search(r"function renderAutomationRoiWorkspace\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(render)
        self.assertNotIn("apiPost", render.group(0))

    # ──────────────────────────────────────────────────────────────────────
    # Candidate-fact draft preservation across refresh (regression: a 15s
    # scheduled/manual refresh rebuilt the DOM and wiped unsaved form entries).
    # state.roi.factDraft is the sole source of truth for unsaved form state.
    # ──────────────────────────────────────────────────────────────────────

    # 13 — fresh ROI state carries a blank draft with every required field
    def test_fact_draft_in_fresh_roi_state(self):
        self.assertIn("function freshRoiFactDraft()", self.html)
        fresh = re.search(r"function freshRoiState\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(fresh)
        self.assertIn("factDraft: freshRoiFactDraft()", fresh.group(0))
        draft = re.search(r"function freshRoiFactDraft\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(draft)
        for field in (
            "source_snapshot_id", "subject_label", "metric_label", "fact_type",
            "value", "unit", "currency_code", "time_unit", "counted_entity",
            "as_of_date", "period_basis", "source_locator", "extraction_rationale",
        ):
            self.assertIn(f"{field}:", draft.group(0))

    # 14 — the form renders every control from draft state, not hard-coded blanks
    def test_fact_form_renders_from_draft(self):
        form = re.search(r"function renderRoiFactForm\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(form)
        body = form.group(0)
        self.assertIn("const draft = state.roi.factDraft;", body)
        self.assertIn("const chosenSnapshot = resolveRoiDraftSnapshot();", body)
        # Text inputs render their persisted value from the draft.
        for field in ("subject_label", "metric_label", "value", "unit",
                      "currency_code", "time_unit", "counted_entity",
                      "as_of_date", "period_basis", "source_locator"):
            self.assertIn(f'value="${{escapeHtml(draft.{field})}}"', body)
        # Textarea content and selects also come from the draft.
        self.assertIn(">${escapeHtml(draft.extraction_rationale)}</textarea>", body)
        self.assertIn("draft.fact_type === t ? 'selected' : ''", body)
        self.assertIn("s.source_snapshot_id === chosenSnapshot ? 'selected' : ''", body)
        # No empty hard-coded value attributes for draftable text fields.
        self.assertNotIn('id="roi-fact-subject" ${disabled} maxlength="200" aria-label="Subject label" required>', body)

    # 15 — capture/update/resolve helpers exist and behave per the design
    def test_draft_helpers_present(self):
        cap = re.search(r"function captureRoiFactDraft\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(cap)
        # Capture is a no-op unless the form is actually mounted, and mirrors ids→draft.
        self.assertIn("if (!document.getElementById('roi-fact-form')) return;", cap.group(0))
        self.assertIn("ROI_FACT_FIELD_IDS", cap.group(0))
        self.assertIn("draft[field] = el.value;", cap.group(0))

        upd = re.search(r"function updateRoiFactDraft\(.*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(upd)
        self.assertIn("ROI_FACT_FIELD_BY_ID[el.id]", upd.group(0))
        self.assertIn("state.roi.factDraft[field] = el.value;", upd.group(0))

        res = re.search(r"function resolveRoiDraftSnapshot\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(res)
        # Keep the chosen snapshot only while still available...
        self.assertIn("availableIds.includes(draft.source_snapshot_id)", res.group(0))
        self.assertIn("s.available", res.group(0))
        # ...otherwise first available, else blank.
        self.assertIn("draft.source_snapshot_id = availableIds.length ? availableIds[0] : '';", res.group(0))

    # 16 — every form control updates the draft on input AND change
    def test_form_controls_update_draft_on_input_and_change(self):
        wire = re.search(r"function wireAutomationRoiWorkspace\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(wire)
        body = wire.group(0)
        self.assertIn("Object.values(ROI_FACT_FIELD_IDS).forEach(id =>", body)
        self.assertIn("el.addEventListener('input', updateRoiFactDraft);", body)
        self.assertIn("el.addEventListener('change', updateRoiFactDraft);", body)

    # 17 — refresh captures the draft BEFORE any ROI workspace load or render
    def test_refresh_captures_draft_before_load(self):
        refresh = re.search(r"async function refresh\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(refresh)
        body = refresh.group(0)
        # The capture is gated on the active ROI tab and runs before any await.
        self.assertIn("if (state.activeTab === AUTOMATION_ROI_TAB) {", body)
        self.assertIn("captureRoiFactDraft();", body)
        capture_at = body.index("captureRoiFactDraft();")
        self.assertLess(capture_at, body.index("loadAutomationRoiWorkspace("))
        self.assertLess(capture_at, body.index("renderMain();"))
        self.assertLess(capture_at, body.index("await loadHealth();"))

    # 18 — global refresh skips ROI workspace reload/render while a mutation is busy
    def test_refresh_skips_roi_workspace_while_busy(self):
        refresh = re.search(r"async function refresh\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(refresh)
        body = refresh.group(0)
        self.assertIn("if (state.activeTab === AUTOMATION_ROI_TAB && state.roi.busy) return;", body)
        guard_at = body.index("state.roi.busy) return;")
        self.assertLess(guard_at, body.index("loadAutomationRoiWorkspace("))
        self.assertLess(guard_at, body.index("renderMain();"))

    # 19 — successful creation clears the draft ONLY after confirmed success
    def test_successful_creation_clears_draft_only_after_success(self):
        wire = re.search(r"function wireAutomationRoiWorkspace\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(wire)
        body = wire.group(0)
        ok_await = body.index("const ok = await roiMutate({")
        if_ok = body.index("if (ok) {")
        reset = body.index("state.roi.factDraft = freshRoiFactDraft();")
        # Reset is gated by the success check and happens strictly after it.
        self.assertLess(ok_await, if_ok)
        self.assertLess(if_ok, reset)
        # The success-only reset is the single draft clear in the create path.
        self.assertEqual(body.count("state.roi.factDraft = freshRoiFactDraft();"), 1)

    # 20 — a failed/cancelled write preserves the draft unchanged
    def test_failed_creation_preserves_draft(self):
        # roiMutate (the only write path) never touches the draft, so any failure,
        # 409/422/503, network error, or confirm-cancel leaves it intact.
        mutate = re.search(r"async function roiMutate\(.*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(mutate)
        self.assertNotIn("factDraft", mutate.group(0))
        self.assertIn("return ok;", mutate.group(0))

    # 21 — switching projects resets ROI state, including a blank draft
    def test_project_switch_resets_roi_draft(self):
        fresh = re.search(r"function freshRoiState\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(fresh)
        self.assertIn("factDraft: freshRoiFactDraft()", fresh.group(0))
        hash_change = re.search(r"async function onHashChange\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(hash_change)
        self.assertIn("state.roi = freshRoiState();", hash_change.group(0))

    # 22 — correction restores only safe values, through the draft, snapshot-valid
    def test_correction_writes_through_draft(self):
        wire = re.search(r"function wireAutomationRoiWorkspace\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(wire)
        body = wire.group(0)
        self.assertIn("[data-roi-correct]", body)
        # Correction builds a fresh draft and assigns it (never edits the DOM directly).
        self.assertIn("const draft = freshRoiFactDraft();", body)
        self.assertIn("state.roi.factDraft = draft;", body)
        self.assertIn("draft.subject_label = fact.subject_label", body)
        self.assertIn("draft.metric_label = fact.metric_label", body)
        # Snapshot selection stays valid through the resolver.
        self.assertIn("resolveRoiDraftSnapshot();", body)
        # The old direct-DOM prefill is gone, and no opaque id is restored.
        self.assertNotIn("set('roi-fact-subject', fact.subject_label)", body)
        self.assertNotIn("draft.candidate_fact_revision_id", body)

    # ──────────────────────────────────────────────────────────────────────
    # Section 4 frozen-input pending selection preservation across refresh
    # (companion regression): state.roi.freezeDraft (role → fact id) survives a
    # scheduled/manual refresh until Freeze is clicked.
    # ──────────────────────────────────────────────────────────────────────

    # 23 — fresh ROI state starts with an empty freeze draft
    def test_freeze_draft_in_fresh_roi_state(self):
        fresh = re.search(r"function freshRoiState\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(fresh)
        self.assertIn("freezeDraft: {}", fresh.group(0))

    # 24 — frozen-input dropdowns render their selection from freezeDraft
    def test_frozen_dropdown_renders_from_freeze_draft(self):
        frozen = re.search(r"function renderRoiFrozen\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(frozen)
        body = frozen.group(0)
        # Stale entries are resolved before rendering.
        self.assertIn("resolveRoiFreezeDraft(approvedFacts);", body)
        # Each dropdown's selection comes from freezeDraft[role].
        self.assertIn("const chosen = state.roi.freezeDraft[role] || '';", body)
        self.assertIn("f.candidate_fact_revision_id === chosen ? 'selected' : ''", body)

    # 25 — a freeze-role select change updates only that role's draft entry
    def test_freeze_select_change_updates_only_its_role(self):
        upd = re.search(r"function updateRoiFreezeDraft\(.*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(upd)
        b = upd.group(0)
        self.assertIn("sel.getAttribute('data-roi-freeze-role')", b)
        self.assertIn("state.roi.freezeDraft[role] = sel.value;", b)
        self.assertIn("delete state.roi.freezeDraft[role];", b)
        wire = re.search(r"function wireAutomationRoiWorkspace\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(wire)
        self.assertIn("document.querySelectorAll('[data-roi-freeze-role]').forEach(sel =>", wire.group(0))
        self.assertIn("sel.addEventListener('change', updateRoiFreezeDraft);", wire.group(0))

    # 26 — stale/unapproved/unavailable selections are cleared, never replaced
    def test_stale_freeze_selections_cleared_no_fallback(self):
        res = re.search(r"function resolveRoiFreezeDraft\(.*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(res)
        b = res.group(0)
        self.assertIn("fact.decision_state !== 'approved'", b)
        self.assertIn("!fact.active_approval_id", b)
        self.assertIn("delete draft[role];", b)
        # Never auto-selects a replacement (only deletes; no assignment, no first-fact).
        self.assertNotIn("draft[role] =", b)
        self.assertNotIn("approvedFacts[0]", b)

    # 27 — capture reads rendered freeze selects; blank clears only that role
    def test_capture_freeze_draft_helper(self):
        cap = re.search(r"function captureRoiFreezeDraft\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(cap)
        b = cap.group(0)
        self.assertIn("document.querySelectorAll('[data-roi-freeze-role]')", b)
        self.assertIn("state.roi.freezeDraft[role] = sel.value;", b)
        self.assertIn("delete state.roi.freezeDraft[role];", b)

    # 28 — refresh captures freeze selections before async work and re-checks busy
    def test_refresh_captures_and_rechecks_busy_for_freeze(self):
        refresh = re.search(r"async function refresh\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(refresh)
        b = refresh.group(0)
        self.assertIn("captureRoiFreezeDraft();", b)
        self.assertLess(b.index("captureRoiFreezeDraft();"), b.index("await loadHealth();"))
        # The busy guard appears twice: before async work AND before ROI load/render.
        guard = "if (state.activeTab === AUTOMATION_ROI_TAB && state.roi.busy) return;"
        self.assertEqual(b.count(guard), 2)
        # The second guard sits immediately before the ROI workspace load + render.
        self.assertLess(b.rindex(guard), b.index("loadAutomationRoiWorkspace("))
        self.assertLess(b.rindex(guard), b.index("renderMain();"))

    # 29 — successful freeze clears only the selected role; failure preserves it
    def test_freeze_success_clears_only_selected_role(self):
        wire = re.search(r"function wireAutomationRoiWorkspace\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(wire)
        freeze = re.search(r"querySelectorAll\('\[data-roi-freeze\]'\)\.forEach.*?\n  \}\);",
                           wire.group(0), re.DOTALL)
        self.assertIsNotNone(freeze)
        fb = freeze.group(0)
        # The Freeze button reads the pending selection from the draft.
        self.assertIn("const factId = state.roi.freezeDraft[role];", fb)
        self.assertIn("const ok = await roiMutate({", fb)
        self.assertIn("if (ok) {", fb)
        self.assertIn("delete state.roi.freezeDraft[role];", fb)
        # The clear is gated by success and happens strictly after the mutate.
        self.assertLess(fb.index("const ok = await roiMutate({"), fb.index("delete state.roi.freezeDraft[role];"))
        self.assertLess(fb.index("if (ok) {"), fb.index("delete state.roi.freezeDraft[role];"))
        # Only the selected role is cleared — never a blanket reset of all roles.
        self.assertNotIn("state.roi.freezeDraft = {}", fb)
        # The single write path never touches freezeDraft, so a failure/cancel keeps it.
        mutate = re.search(r"async function roiMutate\(.*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(mutate)
        self.assertNotIn("freezeDraft", mutate.group(0))

    # 30 — switching projects clears all freeze draft entries (fresh ROI state path)
    def test_project_switch_resets_freeze_draft(self):
        fresh = re.search(r"function freshRoiState\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(fresh)
        self.assertIn("freezeDraft: {}", fresh.group(0))
        hash_change = re.search(r"async function onHashChange\(\).*?\n}", self.html, re.DOTALL)
        self.assertIsNotNone(hash_change)
        self.assertIn("state.roi = freshRoiState();", hash_change.group(0))


if __name__ == "__main__":
    unittest.main()
