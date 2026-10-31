import itertools

import pytest

from app.agents.testability import review_acceptance_criteria
from app.domain.jira_schema import SchemaView
from app.orchestration.policy import (
    Disposition,
    Mode,
    Op,
    Outcome,
    PolicyInput,
    disposition,
    evaluate,
    stricter_mode,
)

READY = dict(
    project_allowlisted=True,
    requirement_status="ready_for_delivery",
    evidence_present=True,
    ac_measurable=True,
    draft_valid=True,
    hierarchy_ok=True,
    change_clearly_evidenced=True,
    target_unambiguous=True,
)


def P(op=Op.CREATE_STORY_TASK, **kw):
    return PolicyInput(op=op, **{**READY, **kw})


# ---- autonomy matrix (CLAUDE.md section 9) ----
@pytest.mark.parametrize("op", [Op.DELETE_ISSUE, Op.CHANGE_PERMISSIONS, Op.CHANGE_SPRINT_RELEASE])
def test_prohibited_operations_always_rejected_whatever_else_is_true(op):
    r = evaluate(P(op))
    assert r.outcome is Outcome.REJECT and r.rule_ids == ("R-PROHIBITED",)


def test_resolve_conflict_never_automatic():
    assert evaluate(P(Op.RESOLVE_CONFLICT)).outcome is Outcome.ESCALATE


def test_happy_paths_execute():
    for op in (Op.CREATE_STORY_TASK, Op.CREATE_EPIC, Op.UPDATE_TEXT):
        assert evaluate(P(op)).outcome is Outcome.EXECUTE
    assert evaluate(P(Op.ADD_REFERENCE)).outcome is Outcome.EXECUTE


@pytest.mark.parametrize(
    ("kw", "outcome", "rule"),
    [
        ({"kill_switch": True}, Outcome.ESCALATE, "R-KILL-SWITCH"),
        ({"project_allowlisted": False}, Outcome.REJECT, "R-ALLOWLIST"),
        ({"evidence_present": False}, Outcome.REJECT, "R-EVIDENCE"),
        ({"requirement_status": "confirmed"}, Outcome.CLARIFY, "R-STATUS"),
        ({"draft_valid": False}, Outcome.REJECT, "R-DRAFT-INVALID"),
        ({"ac_measurable": False}, Outcome.CLARIFY, "R-AC"),
        ({"open_blocking_questions": 2}, Outcome.CLARIFY, "R-BLOCKING-Q"),
        ({"open_conflicts": 1}, Outcome.ESCALATE, "R-OPEN-CONFLICT"),
        ({"duplicate_candidates": 1}, Outcome.ESCALATE, "R-DUPLICATE"),
    ],
)
def test_create_story_blocking_rules(kw, outcome, rule):
    r = evaluate(P(**kw))
    assert r.outcome is outcome and r.rule_ids == (rule,) and r.reason


def test_epic_needs_hierarchy_and_no_duplicates():
    assert evaluate(P(Op.CREATE_EPIC, hierarchy_ok=False)).rule_ids == ("R-EPIC-HIERARCHY",)
    assert evaluate(P(Op.CREATE_EPIC, duplicate_candidates=1)).rule_ids == ("R-DUPLICATE",)


def test_update_needs_clear_evidence_and_unambiguous_target():
    assert evaluate(P(Op.UPDATE_TEXT, change_clearly_evidenced=False)).outcome is Outcome.CLARIFY
    assert evaluate(P(Op.UPDATE_TEXT, target_unambiguous=False)).outcome is Outcome.ESCALATE
    assert evaluate(P(Op.ADD_REFERENCE, target_unambiguous=False)).outcome is Outcome.ESCALATE


def test_kill_switch_beats_everything_but_prohibition():
    assert evaluate(P(kill_switch=True, project_allowlisted=False)).rule_ids == ("R-KILL-SWITCH",)


def test_every_decision_has_rule_ids_and_reason_exhaustively():
    flags = [
        "kill_switch",
        "evidence_present",
        "ac_measurable",
        "draft_valid",
        "project_allowlisted",
    ]
    for op in Op:
        for combo in itertools.product([False, True], repeat=len(flags)):
            r = evaluate(PolicyInput(op=op, **dict(zip(flags, combo, strict=True))))
            assert r.rule_ids and r.reason
            if op in {Op.DELETE_ISSUE, Op.CHANGE_PERMISSIONS, Op.CHANGE_SPRINT_RELEASE}:
                assert r.outcome is Outcome.REJECT


# ---- modes ----
def test_stricter_mode_wins():
    assert stricter_mode("autonomous", "propose") is Mode.PROPOSE
    assert stricter_mode("propose", "autonomous") is Mode.PROPOSE
    assert stricter_mode("dry_run", None) is Mode.DRY_RUN


def test_disposition_by_mode():
    ex = evaluate(P())
    assert disposition(ex, Mode.DRY_RUN) is Disposition.DRY_RUN
    assert disposition(ex, Mode.PROPOSE) is Disposition.AWAITING_APPROVAL
    assert disposition(ex, Mode.AUTONOMOUS) is Disposition.EXECUTE_NOW
    # a non-execute outcome is never promoted by a permissive mode
    assert disposition(evaluate(P(open_conflicts=1)), Mode.AUTONOMOUS) is Disposition.HELD
    assert disposition(evaluate(P(ac_measurable=False)), Mode.AUTONOMOUS) is Disposition.CLARIFY
    assert disposition(evaluate(P(evidence_present=False)), Mode.AUTONOMOUS) is Disposition.REJECTED


# ---- schema view ----
SNAP = {
    "key": "SCRUM",
    "issue_types": [
        {
            "id": "10001",
            "name": "Epic",
            "subtask": False,
            "hierarchy_level": 1,
            "required_fields": ["summary", "issuetype", "project", "reporter"],
            "field_keys": ["summary", "issuetype", "project", "reporter", "labels", "description"],
        },
        {
            "id": "10004",
            "name": "Story",
            "subtask": False,
            "hierarchy_level": 0,
            "required_fields": ["summary", "issuetype", "project", "reporter"],
            "field_keys": [
                "summary",
                "issuetype",
                "project",
                "reporter",
                "labels",
                "parent",
                "description",
            ],
        },
        {
            "id": "10002",
            "name": "Subtask",
            "subtask": True,
            "hierarchy_level": -1,
            "required_fields": ["summary"],
            "field_keys": ["summary", "parent"],
        },
    ],
}


def test_schema_validation():
    v = SchemaView(SNAP)
    assert v.validate_draft("Story", "ok", None, {"labels", "description"}) == []
    assert "does not exist" in v.validate_draft("Bug", "x", None, set())[0]
    assert any("sub-tasks" in p for p in v.validate_draft("Subtask", "x", None, set()))
    assert any(
        "does not support a parent" in p for p in v.validate_draft("Epic", "x", "SCRUM-1", set())
    )
    assert any(
        "not available" in p for p in v.validate_draft("Story", "x", None, {"customfield_1"})
    )
    assert any("255" in p for p in v.validate_draft("Story", "x" * 256, None, set()))
    assert v.epic_type().name == "Epic"


def test_required_field_missing_detected():
    snap = {
        **SNAP,
        "issue_types": [
            {**SNAP["issue_types"][1], "required_fields": ["summary", "customfield_9"]}
        ],
    }
    assert any(
        "customfield_9" in p for p in SchemaView(snap).validate_draft("Story", "x", None, set())
    )


# ---- testability ----
def test_testability_reviewer():
    good = [
        "Reset link expires within 15 minutes of being sent",
        "Admin dashboard shows failed logins from the last 30 days",
    ]
    assert review_acceptance_criteria(good).measurable
    bad = review_acceptance_criteria(["The system should be fast", "Easy to use"])
    assert not bad.measurable and any("vague" in f for f in bad.flags)
    assert not review_acceptance_criteria([]).measurable
    assert review_acceptance_criteria(
        ["Response is fast: under 200 ms for 95% of requests"]
    ).measurable


def test_testability_accepts_negated_and_passive_outcomes_but_still_flags_vague():
    ok = [
        "Successful logins are not included in the failed login attempts view",
        "Failed login attempts older than 30 days are not shown",
        "Locked accounts are notified by email",
    ]
    assert review_acceptance_criteria(ok).measurable
    assert not review_acceptance_criteria(
        ["Dashboard feels modern and seamless to everyone"]
    ).measurable


def test_testability_accepts_real_world_capability_criteria_from_the_rehearsal():
    ok = [
        "A signed-in parent can view the marks recorded for their own child",
        "Each fee entry identifies what the fee is for",
        "A parent can initiate a school fee payment online from a phone",
        "Introducing any other payment method does not remove cash payment at the office",
        "A user with the head teacher role can view a list of all classes in the system",
    ]
    assert review_acceptance_criteria(ok).measurable
    # the real quality gate still blocks vague wording and fragments
    assert not review_acceptance_criteria(
        ["The portal should be easy and modern for everyone"]
    ).measurable
    assert not review_acceptance_criteria(["Nice portal"]).measurable
