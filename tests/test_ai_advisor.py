import json

import pytest

from boostai.ai.advisor import AIAdvisor, build_payload, deterministic_advice, parse_json_object, scrub, validate_advice
from boostai.ai.provider import AIError, AIProvider
from boostai.config.settings import AIProviderName, AISettings
from boostai.core.issues import ActionProposal, Confidence, Issue, IssueType, RootCause, Severity
from tests.fakes import snapshot


def leak_issue():
    return Issue(
        key="MEMORY_LEAK:example.exe:42", type=IssueType.MEMORY_LEAK, title="Possible memory leak: example.exe",
        severity=Severity.HIGH, confidence=Confidence.HIGH, root_cause=RootCause.POSSIBLE_MEMORY_LEAK,
        component="example.exe", explanation="Deterministic explanation.",
        evidence=["Private memory grew from 900 MB to 4.2 GB. Path C:\\Users\\alice\\secret\\example.exe"],
        proposals=[ActionProposal(action_id="RESTART_PROCESS", params={"pid": 42, "create_time": 1.0,
                                                                         "name": "example.exe"}, label="Restart")],
    )


def advice_json(**overrides):
    rec = {"issue_key": "MEMORY_LEAK:example.exe:42", "recommended_action_id": "RESTART_PROCESS",
           "proposal_index": 0, "explanation": "Restarting releases memory.", "user_warning": "Save work."}
    rec.update(overrides)
    return json.dumps({"summary": "One app is leaking.", "priority": "HIGH", "recommendations": [rec]})


def test_valid_advice_selects_existing_proposal(registry):
    issue = leak_issue()
    adv = validate_advice(advice_json(), [issue], registry)
    assert adv.source == "ai" and adv.items[0].proposal is issue.proposals[0]
    assert adv.rejected == []


def test_unknown_action_is_rejected(registry):
    adv = validate_advice(advice_json(recommended_action_id="FORMAT_C_DRIVE"), [leak_issue()], registry)
    assert adv.items == [] and adv.rejected and adv.rejected[0].startswith("REJECT")


def test_known_action_not_proposed_for_issue_is_rejected(registry):
    adv = validate_advice(advice_json(recommended_action_id="STOP_OPTIONAL_SERVICE"), [leak_issue()], registry)
    assert adv.items == [] and "not a candidate" in adv.rejected[0]


def test_invalid_proposal_index_rejected(registry):
    adv = validate_advice(advice_json(proposal_index=7), [leak_issue()], registry)
    assert adv.items == []


def test_unknown_issue_is_ignored(registry):
    adv = validate_advice(advice_json(issue_key="MADE_UP"), [leak_issue()], registry)
    assert adv.items == [] and "unknown issue" in adv.rejected[0]


@pytest.mark.parametrize("raw", ["not json", "[1,2,3]", '{"summary": ""}', '{"priority": "HIGH"}',
                                 '{"summary": "x", "priority": "URGENT", "recommendations": []}', ""])
def test_malformed_responses_return_none(raw, registry):
    assert validate_advice(raw, [leak_issue()], registry) is None


def test_code_fenced_and_wrapped_json_is_accepted(registry):
    raw = "Sure! ```json\n" + advice_json() + "\n```"
    assert validate_advice(raw, [leak_issue()], registry) is not None
    assert parse_json_object("prefix " + advice_json() + " suffix") is not None


def test_control_chars_and_length_limits(registry):
    raw = advice_json(explanation="ok\x00\x1b[31m" + "x" * 900)
    adv = validate_advice(raw, [leak_issue()], registry)
    assert "\x00" not in adv.items[0].explanation and "\x1b" not in adv.items[0].explanation


def test_overlong_fields_fail_schema(registry):
    assert validate_advice(advice_json(explanation="x" * 5000), [leak_issue()], registry) is None


def test_payload_is_privacy_scrubbed(registry):
    payload = build_payload([leak_issue()], {"ram_usage_percent": 96}, registry)
    text = json.dumps(payload)
    assert "C:\\\\Users" not in text and "secret" not in text
    assert scrub("owned by TESTPC\\alice") == "owned by [account]"


class FakeProvider(AIProvider):
    name = "fake"

    def __init__(self, response=None, error=None, cloud=False):
        super().__init__("m")
        self.response, self.error, self.is_cloud, self.calls = response, error, cloud, 0

    def complete_json(self, system, user):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response

    def health(self):
        return True, "ok"


def make_advisor(registry, provider, **settings):
    s = AISettings(provider=AIProviderName.OLLAMA, **settings)
    return AIAdvisor(lambda: s, registry, provider_factory=lambda _s: provider)


def test_ai_disabled_uses_rules(registry):
    adv = AIAdvisor(lambda: AISettings(provider=AIProviderName.NONE), registry).advise([leak_issue()], snapshot())
    assert adv.source == "rules" and adv.items[0].proposal.action_id == "RESTART_PROCESS"


def test_provider_error_falls_back(registry):
    p = FakeProvider(error=AIError("quota exhausted"))
    adv = make_advisor(registry, p).advise([leak_issue()], snapshot())
    assert adv.source == "rules" and "quota exhausted" in adv.note


def test_invalid_ai_output_falls_back(registry):
    adv = make_advisor(registry, FakeProvider(response="garbage")).advise([leak_issue()], snapshot())
    assert adv.source == "rules" and "failed validation" in adv.note


def test_cloud_blocked_in_local_only_mode(registry):
    p = FakeProvider(response=advice_json(), cloud=True)
    adv = make_advisor(registry, p, local_only=True).advise([leak_issue()], snapshot())
    assert adv.source == "rules" and p.calls == 0


def test_cloud_allowed_when_local_only_off(registry):
    p = FakeProvider(response=advice_json(), cloud=True)
    adv = make_advisor(registry, p, local_only=False).advise([leak_issue()], snapshot())
    assert adv.source == "ai" and p.calls == 1


def test_deterministic_advice_prioritises_highest_severity():
    adv = deterministic_advice([leak_issue()])
    assert adv.priority == "HIGH" and "memory leak" in adv.summary


def test_explain_issue_fallback(registry):
    issue = leak_issue()
    text, source = make_advisor(registry, FakeProvider(response="{}")).explain_issue(issue)
    assert source == "rules" and text == issue.explanation
    text, source = make_advisor(registry, FakeProvider(response='{"explanation": "Plain words."}')).explain_issue(issue)
    assert source == "fake" and text == "Plain words."
