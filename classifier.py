"""Stage 2: Gemini-based intent classifier.

Decides whether a forum post is a DAO actively soliciting external help
for governance / tokenomics / go-to-market / research work, or just
mentions those topics.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


KEYCHAIN_SERVICE = "gemini-api-key"
# As of Apr 2026 the Gemini free tier was cut hard: gemini-2.5-flash is 20 RPD
# and gemini-2.0-flash is 0 RPD. gemini-2.5-flash-lite still has a usable free
# bucket and is more than capable for this classification. Override via env if paid.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")


class Classification(BaseModel):
    is_opportunity: bool
    opportunity_type: Literal["rfp", "grant", "hire", "advisory_request", "other"]
    call_to_action: str = Field(description="One-sentence quote or paraphrase of what they're asking for. Empty string if is_opportunity is false.")
    confidence: float = Field(ge=0.0, le=1.0)
    one_line_reason: str


SYSTEM_PROMPT = """You classify DAO governance forum posts to find consulting opportunities for a governance / tokenomics / go-to-market / research consultant.

ACCEPT (is_opportunity=true) ONLY if the post is authored by a DAO, protocol team, foundation, or clearly empowered working group that is ACTIVELY SOLICITING EXTERNAL HELP in governance, tokenomics, go-to-market, or research. A clear call to action is required: a formal RFP, a scoped consulting/vendor ask, a paid advisory or research role, a grant PROGRAM where the DAO is calling for researchers/consultants/teams to apply and deliver work, or an explicit "we need outside help" post with a way to respond.

REJECT (is_opportunity=false):
- Routine governance votes or proposal announcements.
- Delegate self-promotion / candidate statements.
- Proposal drafts authored by internal contributors who are not seeking outside help.
- Status updates, retrospectives, quarterly reports.
- Analysis, opinion, or debate posts.
- Celebration / milestone threads.
- Meta-discussion ABOUT governance that isn't asking for help.
- Posts that only MENTION governance / tokenomics / go-to-market / research in passing.
- Grant REQUESTS where an internal team, working group, or contributor is asking a DAO treasury to fund their own project (the DAO is the funder, the author is the beneficiary). Also reject generic bounty-only programs and vague community funding announcements with no call for external expertise.
- Service providers, contractors, researchers, or individuals advertising their own services TO a DAO. The DAO must be the one asking, not the other way around.
- Exploratory "any thoughts?" discussion that has no concrete path to engage.
- Roles unrelated to governance, tokenomics, go-to-market, or research.

Tie-breakers:
- When uncertain, set confidence <= 0.5 and is_opportunity=false.
- Prefer missed opportunities over false alerts. Every false alert costs the consultant's attention.
- The DAO must be the demander. If the author is pitching their own services, reject it even if the work sounds relevant.
- Grant distinction: ACCEPT grant PROGRAMS (DAO is funder, external experts apply to deliver work — a consultant can submit); REJECT grant REQUESTS (internal team asking DAO treasury to fund them — not consulting).
- opportunity_type: pick "rfp" for formal requests for proposals, "hire" for paid roles or contractor hires, "advisory_request" for informal but concrete requests for outside guidance with a clear path to respond, "grant" for open grant rounds/programs where the DAO is calling for external researchers or consultants to apply, "other" only if none fit.

Return JSON matching the schema exactly. call_to_action must be a single short sentence (<= 200 chars). one_line_reason must explain WHY you accepted or rejected (<= 200 chars)."""


# Few-shot examples. Synthetic but realistic in shape, to calibrate the model
# without quoting or misattributing any real forum author.
FEW_SHOTS: list[tuple[str, Classification]] = [
    (
        "Forum: Arbitrum Foundation\nTitle: [RFP] Governance framework review — seeking external teams\nExcerpt: The Arbitrum Foundation is opening a Request for Proposals for a comprehensive review of our delegation and proposal lifecycle framework. Budget up to $150k. Proposals due May 30. Please reply in this thread or email governance@arbitrum.foundation to express interest.",
        Classification(
            is_opportunity=True,
            opportunity_type="rfp",
            call_to_action="Submit proposal to review Arbitrum's delegation and proposal lifecycle framework; budget up to $150k, due May 30.",
            confidence=0.95,
            one_line_reason="Formal RFP with budget, deadline, and contact path — textbook external solicitation.",
        ),
    ),
    (
        "Forum: Uniswap Governance\nTitle: Announcing the Uniswap Governance Research Grants — Round 3\nExcerpt: We're opening Round 3 of the Uniswap Governance Research Grants. Applications open for researchers studying voter participation, delegation dynamics, and incentive design. Individual grants $10k-$50k. Apply via the form linked below by June 15.",
        Classification(
            is_opportunity=True,
            opportunity_type="grant",
            call_to_action="Apply to Uniswap Governance Research Grants Round 3 (voter participation, delegation, incentive design); $10k-$50k; due June 15.",
            confidence=0.92,
            one_line_reason="Grant PROGRAM: DAO is calling for external researchers to apply and deliver work — consultants can submit.",
        ),
    ),
    (
        "Forum: Aave Governance\nTitle: [ARFC] Funding request — $75k to build a security framework for Aave V4\nExcerpt: We are proposing a Phase 1 grant of $50k-$75k from the Aave DAO treasury to fund development of AaveShield, a modular security framework for Aave V4. Requesting community feedback and treasury approval.",
        Classification(
            is_opportunity=False,
            opportunity_type="other",
            call_to_action="",
            confidence=0.95,
            one_line_reason="Grant REQUEST: internal team asking DAO treasury to fund its own project — not an external consulting opportunity.",
        ),
    ),
    (
        "Forum: Optimism Governance\nTitle: Vote: Proposal 042 — Update sequencer fee parameters\nExcerpt: This proposal updates the sequencer fee parameters as discussed in the previous season. Voting opens Monday. Please review the full rationale in the linked document.",
        Classification(
            is_opportunity=False,
            opportunity_type="other",
            call_to_action="",
            confidence=0.95,
            one_line_reason="Internal governance vote — not soliciting outside work.",
        ),
    ),
    (
        "Forum: ENS Governance\nTitle: Delegate introduction — voting record and philosophy\nExcerpt: Hi everyone, I'm running to be a delegate. Here's my voting philosophy and a summary of my governance participation across Uniswap, Compound, and ENS over the past two years. Happy to answer any questions.",
        Classification(
            is_opportunity=False,
            opportunity_type="other",
            call_to_action="",
            confidence=0.95,
            one_line_reason="Delegate self-introduction — not a request for external help.",
        ),
    ),
    (
        "Forum: ENS Governance\nTitle: Governance Advisor search for the ENS DAO\nExcerpt: The DAO is seeking a named external Governance Advisor for a 12-month term. Responsibilities include process review, governance design guidance, and written recommendations. Compensation is paid quarterly. Interested candidates should reply in thread with relevant experience.",
        Classification(
            is_opportunity=True,
            opportunity_type="hire",
            call_to_action="Reply in thread to be considered for a paid 12-month Governance Advisor role.",
            confidence=0.95,
            one_line_reason="Named paid role with scope, term, and a direct path to apply.",
        ),
    ),
    (
        "Forum: Compound Governance\nTitle: Seeking outside advisor on tokenomics redesign\nExcerpt: The working group wants outside help evaluating COMP emissions, delegate incentives, and market positioning. If your firm has tokenomics or go-to-market strategy experience, reply in-thread with examples of prior work before May 18.",
        Classification(
            is_opportunity=True,
            opportunity_type="advisory_request",
            call_to_action="Reply in-thread with prior work to advise on COMP tokenomics and market positioning before May 18.",
            confidence=0.88,
            one_line_reason="Protocol-originated request for outside strategy help with a clear response path.",
        ),
    ),
    (
        "Forum: Arbitrum Foundation\nTitle: Fixed-scope mechanism-risk review services available\nExcerpt: I'm offering fixed-scope mechanism-risk reviews for upcoming Arbitrum governance proposals. Happy to support delegates over the next month. DM me if useful.",
        Classification(
            is_opportunity=False,
            opportunity_type="other",
            call_to_action="",
            confidence=0.98,
            one_line_reason="Service provider pitching into the DAO; the protocol is not soliciting outside help.",
        ),
    ),
]


_client_cache: genai.Client | None = None


def _get_api_key() -> str:
    """Resolve the Gemini API key from macOS Keychain, falling back to env.

    Never echoes the key. Raises a clear error if neither source has it.
    """
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            key = result.stdout.strip()
            if key:
                return key
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

    raise RuntimeError(
        "Gemini API key not found. Store it in macOS Keychain "
        f"(service '{KEYCHAIN_SERVICE}') or set GEMINI_API_KEY env var."
    )


def get_client() -> genai.Client:
    global _client_cache
    if _client_cache is None:
        _client_cache = genai.Client(api_key=_get_api_key())
    return _client_cache


def _build_contents(forum_name: str, title: str, excerpt: str) -> list[types.Content]:
    """Interleave few-shots with the real user turn in Gemini's chat format."""
    contents: list[types.Content] = []
    for user_text, classification in FEW_SHOTS:
        contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        contents.append(types.Content(role="model", parts=[types.Part(text=classification.model_dump_json())]))
    real = f"Forum: {forum_name}\nTitle: {title}\nExcerpt: {excerpt}"
    contents.append(types.Content(role="user", parts=[types.Part(text=real)]))
    return contents


def _short_error(e: Exception) -> str:
    """One-line summary of a google-genai exception: status + message prefix."""
    msg = str(e)
    head = msg.split("{", 1)[0].strip().rstrip(".")
    return head[:200] or type(e).__name__


def build_system_prompt(user_preference_note: str = "") -> str:
    if not user_preference_note.strip():
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + "\n\n" + user_preference_note.strip()


def classify_post(
    forum_name: str,
    title: str,
    excerpt: str,
    client: genai.Client | None = None,
    user_preference_note: str = "",
) -> Classification | None:
    """Classify a single forum post. Returns None on API failure (caller continues)."""
    client = client or get_client()
    contents = _build_contents(forum_name, title, excerpt)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=build_system_prompt(user_preference_note),
                response_mime_type="application/json",
                response_schema=Classification,
                temperature=0.1,
            ),
        )
    except Exception as e:
        print(f"    WARN: Gemini call failed: {_short_error(e)}")
        return None

    if response.parsed is not None:
        return response.parsed  # type: ignore[return-value]

    try:
        data = json.loads(response.text)
        return Classification(**data)
    except Exception as e:
        print(f"    WARN: could not parse Gemini response: {type(e).__name__}")
        return None


if __name__ == "__main__":
    import sys
    forum = sys.argv[1] if len(sys.argv) > 1 else "Test Forum"
    title = sys.argv[2] if len(sys.argv) > 2 else "Looking for governance consultants"
    excerpt = sys.argv[3] if len(sys.argv) > 3 else "We are opening an RFP for governance consulting work. Budget $50k. Reply in thread."
    result = classify_post(forum, title, excerpt)
    print(json.dumps(result.model_dump() if result else None, indent=2))
