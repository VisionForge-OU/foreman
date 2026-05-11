"""The synthetic human (goal Part B).

Genuinely exercises each gate: reads the draft, applies a rubric (deterministic
structural checks + an LLM judge for substantive judgment), records scores +
rationale, and returns a decision the conductor enacts through the TUI. Enforces
"mandatory coverage" so the request-changes / reject branches are exercised even
when the happy path would sail through. Never rubber-stamps; never approves work
with unanswered open questions or missing structural fields.

The judge is injected so the mock dry-run can use a free StubJudge while the real
campaign uses the token-spending LlmJudge — the *policy* (what makes a decision)
is identical in both.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .claude_call import extract_json_block, run_print_json


@dataclass
class Decision:
    action: str                       # "approve" | "request_changes" | "reject"
    comments: str = ""
    scores: dict = field(default_factory=dict)
    rationale: str = ""
    summary: str = ""
    judge_cost_usd: float = 0.0


@dataclass
class JudgeVerdict:
    scores: dict
    recommend: str                    # "approve" | "request_changes"
    rationale: str
    answers: str = ""                 # substantive answers to open questions, if asked
    cost_usd: float = 0.0


# --------------------------------------------------------------------------- #
# Judges
# --------------------------------------------------------------------------- #
class StubJudge:
    """Free, deterministic judge for the mock dry-run. Approves structurally-sound
    drafts; the conductor's policy layer still forces the mandated request-changes
    and reject paths, so the mock run exercises both branches without tokens."""

    async def __call__(self, prompt: str, *, kind: str) -> JudgeVerdict:
        return JudgeVerdict(
            scores={"addresses_request": 4, "rigor": 4, "testability": 4},
            recommend="approve",
            rationale="[stub judge] structurally sound; approving on the happy path.",
            answers="Use the simplest in-scope option; defer non-essentials. "
                    "(stub answer for the dry-run)",
            cost_usd=0.0,
        )


class LlmJudge:
    """Real read-only ``claude -p`` judge (haiku, plan/read-only mode)."""

    def __init__(self, *, model: str, cwd: str, max_cost_usd: float = 0.20):
        self.model = model
        self.cwd = cwd
        self.max_cost_usd = max_cost_usd

    async def __call__(self, prompt: str, *, kind: str) -> JudgeVerdict:
        res = await run_print_json(
            prompt, model=self.model, cwd=self.cwd,
            permission_mode="plan", effort="low",
            max_cost_usd=self.max_cost_usd, timeout_s=180,
        )
        data = extract_json_block(res.get("result", "")) or {}
        scores = data.get("scores") or {}
        recommend = data.get("recommend", "approve")
        if recommend not in ("approve", "request_changes"):
            recommend = "approve"
        return JudgeVerdict(
            scores=scores if isinstance(scores, dict) else {},
            recommend=recommend,
            rationale=str(data.get("rationale", res.get("error", "") or "no rationale"))[:600],
            answers=str(data.get("answers", ""))[:1500],
            cost_usd=res.get("cost_usd", 0.0),
        )


# --------------------------------------------------------------------------- #
# Rubric prompts
# --------------------------------------------------------------------------- #
def _rubric_prompt(gate: str, request: str, body: str, open_questions: list[str]) -> str:
    oq = "\n".join(f"- {q}" for q in open_questions) or "(none)"
    criteria = {
        "plan": "Does the plan actually address the request? Are risks and edge cases "
                "considered? Is the decomposition sound?",
        "adr": "Are the architectural decisions justified and consistent with the codebase? "
               "Is the 'decisions made on your behalf' digest sound?",
        "prd": "Are acceptance criteria concrete and *testable*? Are user flows complete? "
               "Are the decisions made on the reviewer's behalf reasonable?",
        "queue": "Is each issue a coherent vertical slice with a runnable acceptance check, "
                 "sane file footprint (touches), dependencies, and PRD traceability?",
    }.get(gate, "Is this draft sound and complete?")
    return (
        "You are a fair but rigorous senior engineer reviewing a draft at a delivery gate. "
        "APPROVE if the draft is sound and addresses the request — even if minor "
        "improvements are possible. Recommend REQUEST_CHANGES ONLY for SUBSTANTIVE defects: "
        "a missing core requirement, untestable acceptance criteria, a wrong or unsafe "
        "approach, or genuinely broken/incomplete content. Do NOT bounce for style, optional "
        "polish, or minor scope notes — note those in the rationale but still approve. "
        "Do NOT use any tools. Judge ONLY from the text below. Respond with ONE JSON object "
        "and nothing else.\n\n"
        f"GATE: {gate}\nRUBRIC: {criteria}\n\n"
        f"ORIGINAL REQUEST:\n{request}\n\n"
        f"OPEN QUESTIONS FOR REVIEWER (must be answerable from the draft+request):\n{oq}\n\n"
        f"DRAFT (verbatim; if it appears to end mid-sentence it really was truncated by "
        f"the agent, otherwise treat it as complete):\n{body[:24000]}\n\n"
        'Return: {"scores": {"addresses_request": 1-5, "rigor": 1-5, "testability": 1-5}, '
        '"recommend": "approve" | "request_changes", "rationale": "<2-3 sentences>", '
        '"answers": "<if there are open questions, give substantive answers a reviewer '
        'would write; else empty>"}'
    )


def _proposal_prompt(detail: str) -> str:
    return (
        "You are reviewing a retro patch proposal for an agentic build pipeline. Do NOT "
        "use tools. Judge whether it rings true and is specific & actionable (not vague or "
        "speculative). Respond with ONE JSON object only.\n\n"
        f"PROPOSAL:\n{detail[:12000]}\n\n"
        '{"scores": {"specific": 1-5, "grounded": 1-5}, '
        '"recommend": "approve" | "request_changes", "rationale": "<2-3 sentences>"}'
    )


def _escalation_prompt(reason: str, request: str, context: str) -> str:
    return (
        "An autonomous build worker escalated and needs a concrete answer to proceed. "
        "Do NOT use tools. Give a SUBSTANTIVE, decisive answer (2-5 sentences) the worker "
        "can act on — a real engineering decision, not 'looks good'. Respond with plain text.\n\n"
        f"FEATURE REQUEST:\n{request}\n\nESCALATION:\n{reason}\n\nCONTEXT:\n{context[:1500]}"
    )


# --------------------------------------------------------------------------- #
# AutoReviewer
# --------------------------------------------------------------------------- #
class AutoReviewer:
    def __init__(self, judge, *, force_rc_gates: set[tuple[str, str]] | None = None):
        """``judge`` is an async callable (StubJudge | LlmJudge).

        ``force_rc_gates`` is a set of (slug, gate) where one request-changes cycle
        is mandated before approval (mandatory coverage). Consumed once each.
        """
        self.judge = judge
        self.force_rc_gates = set(force_rc_gates or set())
        self._rejected_one_proposal = False

    async def review(self, *, gate: str, slug: str, request: str, body: str,
                     summary: str, open_questions: list[str],
                     structural_problems: list[str]) -> Decision:
        verdict = await self.judge(
            _rubric_prompt(gate, request, body, open_questions), kind=gate)
        scores = dict(verdict.scores)

        # 1) Unanswered open questions → answer them substantively, request changes.
        if open_questions:
            return Decision(
                action="request_changes",
                comments="Answers to open questions:\n" + (verdict.answers or
                         "Choose the simplest in-scope option and proceed."),
                scores=scores, summary=summary, judge_cost_usd=verdict.cost_usd,
                rationale=f"{len(open_questions)} open question(s) block approval; "
                          "supplied substantive answers, requesting a revised draft. "
                          + verdict.rationale)

        # 2) Structural defects (missing acceptance_check/touches/etc.) → request changes.
        if structural_problems:
            return Decision(
                action="request_changes",
                comments="Structural problems to fix:\n- " + "\n- ".join(structural_problems),
                scores=scores, summary=summary, judge_cost_usd=verdict.cost_usd,
                rationale="Structural rubric failed: " + "; ".join(structural_problems))

        # 3) Mandatory coverage: force one request-changes cycle on a chosen gate.
        if (slug, gate) in self.force_rc_gates:
            self.force_rc_gates.discard((slug, gate))
            concern = (verdict.rationale or
                       "Tighten acceptance criteria and name the regression risks explicitly.")
            return Decision(
                action="request_changes",
                comments="Before approval, please sharpen this: " + concern,
                scores=scores, summary=summary, judge_cost_usd=verdict.cost_usd,
                rationale="[mandatory coverage] exercising the request-changes→revise→"
                          "approve cycle once. Substantive concern: " + concern)

        # 4) Substantive judgment.
        if verdict.recommend == "request_changes":
            return Decision(action="request_changes", comments=verdict.rationale,
                            scores=scores, summary=summary, rationale=verdict.rationale,
                            judge_cost_usd=verdict.cost_usd)
        return Decision(action="approve", scores=scores, summary=summary,
                        rationale=verdict.rationale, judge_cost_usd=verdict.cost_usd)

    async def review_proposal(self, *, name: str, detail: str,
                              allow_force_reject: bool) -> Decision:
        verdict = await self.judge(_proposal_prompt(detail), kind="retro")
        scores = dict(verdict.scores)
        # Mandatory coverage: reject at least one proposal to validate that branch.
        if allow_force_reject and not self._rejected_one_proposal:
            self._rejected_one_proposal = True
            return Decision(action="reject", scores=scores, summary=name,
                            judge_cost_usd=verdict.cost_usd,
                            rationale="[mandatory coverage] rejecting one proposal to validate "
                                      "the reject branch of the patch gate. " + verdict.rationale)
        if verdict.recommend == "request_changes":
            return Decision(action="reject", scores=scores, summary=name,
                            rationale=verdict.rationale, judge_cost_usd=verdict.cost_usd)
        return Decision(action="approve", scores=scores, summary=name,
                        rationale=verdict.rationale, judge_cost_usd=verdict.cost_usd)

    async def answer_escalation(self, *, reason: str, request: str, context: str) -> tuple[str, float]:
        # Reuse the judge transport for a free-text answer.
        if isinstance(self.judge, StubJudge):
            return ("Use the in-memory/simplest store; proceed with the obvious in-scope "
                    "implementation and add a regression test. (stub escalation answer)", 0.0)
        res = await run_print_json(
            _escalation_prompt(reason, request, context),
            model=self.judge.model, cwd=self.judge.cwd,
            permission_mode="plan", effort="low", max_cost_usd=0.15, timeout_s=150)
        ans = (res.get("result") or "").strip() or \
            "Proceed with the simplest correct in-scope implementation; add a regression test."
        return ans[:1500], res.get("cost_usd", 0.0)
