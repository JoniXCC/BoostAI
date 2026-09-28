"""AI advisor: optional explanations and prioritisation on top of deterministic diagnostics.

Guarantees
----------
* The model receives only minimal, structured performance metadata (no file paths,
  user names, file contents, browsing data or credentials).
* The model can only *select* among the candidate actions that the rule engine already
  proposed for an issue. Unknown action IDs are rejected; out-of-range selections are
  rejected; free-text fields are length-limited, stripped of control characters and only
  ever displayed - never executed.
* Any failure (disabled, offline, quota, timeout, invalid JSON, schema violation) falls
  back to the deterministic recommendation, so core functionality never depends on AI.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from pydantic import ValidationError

from boostai.actions.registry import ActionRegistry
from boostai.ai import credentials
from boostai.ai.gemini_provider import GeminiProvider
from boostai.ai.groq_provider import GroqProvider
from boostai.ai.ollama_provider import OllamaProvider
from boostai.ai.provider import AIError, AIProvider
from boostai.ai.schemas import AIAdvice, AIExplanation
from boostai.config.logging_config import log_event
from boostai.config.settings import AIProviderName, AISettings
from boostai.core.issues import ActionProposal, Issue, Severity
from boostai.core.models import MB

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the explanation assistant of BoostAI, a Windows performance diagnostic tool.
You receive structured measurements and issues already detected by deterministic rules.
Rules you must follow:
- Respond with ONE JSON object only, matching exactly:
  {"summary": str, "priority": "INFO"|"LOW"|"MEDIUM"|"HIGH"|"CRITICAL",
   "recommendations": [{"issue_key": str, "recommended_action_id": str|null, "proposal_index": int|null,
                        "explanation": str, "user_warning": str|null}]}
- You may only recommend an action by choosing one of the candidate_actions listed for that issue
  (copy its action_id and index). Use null when manual action is better. Never invent actions,
  commands, scripts, registry edits or file paths.
- Order recommendations by importance. Explain in plain language for a non-expert (max 3 sentences each).
- Never claim a memory leak is certain; the data only shows a possibility with the given confidence.
- Never call software malware. For unusual behaviour, recommend a scan with Windows Security.
- Do not promise improvements that were not measured."""

EXPLAIN_PROMPT = """You are the explanation assistant of BoostAI, a Windows performance diagnostic tool.
Explain one detected issue in plain language. Respond with ONE JSON object: {"explanation": str}.
Never claim a memory leak is certain, never call software malware, never suggest commands, scripts,
registry edits or file deletions, and do not promise improvements that were not measured."""

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PATH = re.compile(r"([A-Za-z]:\\[^\s,;\"']+|\\\\[^\s,;\"']+)")
_USER = re.compile(r"\b[\w.-]+\\[\w.-]+\b")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def clean_text(text: str, limit: int) -> str:
    return _CONTROL.sub("", text).strip()[:limit]


def scrub(text: str) -> str:
    """Remove file paths and DOMAIN\\user strings from anything sent to a model."""
    return _USER.sub("[account]", _PATH.sub("[path]", text))


@dataclass(slots=True)
class AdviceItem:
    issue: Issue
    proposal: ActionProposal | None
    explanation: str
    warning: str | None = None


@dataclass(slots=True)
class ValidatedAdvice:
    summary: str
    priority: str
    items: list[AdviceItem]
    source: str                       # "ai" or "rules"
    provider: str | None = None
    rejected: list[str] = field(default_factory=list)
    note: str | None = None


def build_payload(issues: list[Issue], system: dict, registry: ActionRegistry) -> dict:
    return {
        "system": system,
        "issues": [
            {
                "issue_key": i.key,
                "type": i.type.value,
                "title": scrub(i.title),
                "severity": i.severity.value,
                "confidence": i.confidence.value,
                "root_cause": i.root_cause.value,
                "evidence": [scrub(e)[:400] for e in i.evidence[:5]],
                "candidate_actions": [
                    {"index": n, "action_id": p.action_id, "label": scrub(p.label)} for n, p in enumerate(i.proposals)
                ],
                "manual_steps": [scrub(m)[:200] for m in i.manual_steps[:3]],
            }
            for i in issues[:12]
        ],
        "allowed_action_ids": registry.ids(),
    }


def system_summary(snapshot) -> dict:
    sysdisk = snapshot.system_disk
    return {
        "ram_usage_percent": round(snapshot.memory.percent, 1),
        "available_ram_mb": int(snapshot.memory.available / MB),
        "total_ram_mb": int(snapshot.memory.total / MB),
        "commit_percent": round(snapshot.memory.commit_percent or 0, 1),
        "memory_pressure": snapshot.memory_pressure.value,
        "cpu_percent": snapshot.cpu.total_percent,
        "process_count": snapshot.process_count,
        "system_disk_free_percent": round(100 - sysdisk.percent, 1) if sysdisk else None,
    }


def parse_json_object(raw: str) -> dict | None:
    text = _FENCE.sub("", raw.strip())
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def validate_advice(raw: str, issues: list[Issue], registry: ActionRegistry) -> ValidatedAdvice | None:
    """Parse and validate model output. Returns None if unusable (caller falls back)."""
    data = parse_json_object(raw)
    if data is None:
        log_event("ai_response_rejected", reason="not a JSON object")
        return None
    try:
        advice = AIAdvice.model_validate(data)
    except ValidationError as exc:
        log_event("ai_response_rejected", reason="schema", errors=len(exc.errors()))
        return None

    by_key = {i.key: i for i in issues}
    items: list[AdviceItem] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for rec in advice.recommendations:
        issue = by_key.get(rec.issue_key)
        if issue is None:
            rejected.append(f"unknown issue '{rec.issue_key[:60]}'")
            continue
        if rec.issue_key in seen:
            continue
        proposal = None
        if rec.recommended_action_id is not None:
            if not registry.is_known(rec.recommended_action_id):
                rejected.append(f"REJECT unknown action '{rec.recommended_action_id[:40]}'")
                log_event("ai_response_rejected", reason="unknown action", action_id=rec.recommended_action_id[:40])
                continue
            matching = [(n, p) for n, p in enumerate(issue.proposals) if p.action_id == rec.recommended_action_id]
            if not matching:
                rejected.append(f"REJECT {rec.recommended_action_id}: not a candidate for {issue.key}")
                log_event("ai_response_rejected", reason="action not a candidate", action_id=rec.recommended_action_id)
                continue
            if rec.proposal_index is not None:
                chosen = [p for n, p in matching if n == rec.proposal_index]
                if not chosen:
                    rejected.append(f"REJECT proposal index {rec.proposal_index} for {issue.key}")
                    continue
                proposal = chosen[0]
            else:
                proposal = matching[0][1]
        seen.add(rec.issue_key)
        items.append(AdviceItem(issue, proposal, clean_text(rec.explanation, 1000),
                                clean_text(rec.user_warning, 400) if rec.user_warning else None))
    return ValidatedAdvice(clean_text(advice.summary, 1500), advice.priority, items, "ai", rejected=rejected)


def deterministic_advice(issues: list[Issue], note: str | None = None) -> ValidatedAdvice:
    actionable = [i for i in issues if i.severity != Severity.INFO]
    if not actionable:
        summary = "No significant performance problems were detected in this scan."
        priority = "INFO"
    else:
        top = actionable[0]
        summary = (f"{len(actionable)} issue(s) found. The most important is: {top.title} "
                   f"(severity {top.severity.value.lower()}, confidence {top.confidence.value.lower()}).")
        priority = max(actionable, key=lambda i: i.severity.rank).severity.value
    items = [AdviceItem(i, i.proposals[0] if i.proposals else None, i.explanation,
                        i.proposals[0].warning if i.proposals else None) for i in actionable]
    return ValidatedAdvice(summary, priority, items, "rules", note=note)


class AIAdvisor:
    def __init__(self, settings_provider: Callable[[], AISettings], registry: ActionRegistry,
                 provider_factory: Callable[[AISettings], AIProvider | None] | None = None) -> None:
        self.settings_provider = settings_provider
        self.registry = registry
        self.provider_factory = provider_factory or make_provider

    def provider(self) -> AIProvider | None:
        return self.provider_factory(self.settings_provider())

    def status(self) -> tuple[bool, str]:
        s = self.settings_provider()
        if s.provider == AIProviderName.NONE:
            return False, "AI disabled - BoostAI uses its rule-based recommendations."
        provider = self.provider()
        if provider is None:
            return False, "Provider not configured."
        if provider.is_cloud and s.local_only:
            return False, "Local-only mode is on, so cloud AI is blocked. No data leaves this computer."
        return provider.health()

    def _usable_provider(self) -> tuple[AIProvider | None, str | None]:
        s = self.settings_provider()
        if s.provider == AIProviderName.NONE:
            return None, "AI is disabled; showing rule-based recommendations."
        provider = self.provider()
        if provider is None:
            return None, "AI provider is not configured; showing rule-based recommendations."
        if provider.is_cloud and s.local_only:
            return None, "Local-only mode blocks cloud AI; showing rule-based recommendations."
        return provider, None

    def advise(self, issues: list[Issue], snapshot) -> ValidatedAdvice:
        provider, reason = self._usable_provider()
        if provider is None:
            return deterministic_advice(issues, reason)
        if not issues:
            return deterministic_advice(issues)
        payload = build_payload(issues, system_summary(snapshot), self.registry)
        log_event("ai_request", provider=provider.name, cloud=provider.is_cloud, issues=len(payload["issues"]))
        try:
            raw = provider.complete_json(SYSTEM_PROMPT, json.dumps(payload))
        except AIError as exc:
            return deterministic_advice(issues, f"AI unavailable ({exc}); showing rule-based recommendations.")
        except Exception as exc:  # defensive: provider bugs never break the app
            log.exception("AI provider failure")
            return deterministic_advice(issues, f"AI error ({type(exc).__name__}); showing rule-based recommendations.")
        advice = validate_advice(raw, issues, self.registry)
        if advice is None:
            return deterministic_advice(issues, "The AI response failed validation; showing rule-based recommendations.")
        advice.provider = provider.name
        # Issues the model skipped still get their deterministic recommendation.
        covered = {i.issue.key for i in advice.items}
        for issue in issues:
            if issue.key not in covered and issue.severity != Severity.INFO:
                advice.items.append(AdviceItem(issue, issue.proposals[0] if issue.proposals else None,
                                               issue.explanation))
        return advice

    def explain_issue(self, issue: Issue) -> tuple[str, str]:
        """(text, source). Plain-language explanation of one issue."""
        provider, reason = self._usable_provider()
        if provider is None:
            return issue.explanation, "rules"
        payload = build_payload([issue], {}, self.registry)
        prompt = ("Explain this detected issue to a non-expert in at most 5 sentences: what it means, how sure the "
                  "evidence is, and what they can do. Respond as JSON {\"explanation\": str}.\n" + json.dumps(payload))
        log_event("ai_request", provider=provider.name, cloud=provider.is_cloud, issues=1)
        try:
            data = parse_json_object(provider.complete_json(EXPLAIN_PROMPT, prompt))
            text = AIExplanation.model_validate(data or {}).explanation
            return clean_text(text, 1500), provider.name
        except (AIError, ValidationError) as exc:
            log_event("ai_response_rejected", reason=type(exc).__name__)
            return issue.explanation, "rules"


def make_provider(s: AISettings) -> AIProvider | None:
    if s.provider == AIProviderName.OLLAMA:
        return OllamaProvider(s.ollama_model, s.ollama_url, s.timeout_s)
    if s.provider == AIProviderName.GEMINI:
        return GeminiProvider(s.gemini_model, credentials.get_api_key("gemini"), s.timeout_s)
    if s.provider == AIProviderName.GROQ:
        return GroqProvider(s.groq_model, credentials.get_api_key("groq"), s.timeout_s)
    return None
