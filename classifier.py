"""Stage 2: Gemini-based intent classifier.

Decides whether a forum post is a DAO actively soliciting external help
for governance / tokenomics / research work, or just mentions those topics.
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
MODEL = "gemini-2.5-flash"


class Classification(BaseModel):
    is_opportunity: bool
    opportunity_type: Literal["rfp", "grant", "hire", "advisory_request", "other"]
    call_to_action: str = Field(description="One-sentence quote or paraphrase of what they're asking for. Empty string if is_opportunity is false.")
    confidence: float = Field(ge=0.0, le=1.0)
    one_line_reason: str


SYSTEM_PROMPT = """You classify DAO governance forum posts to find consulting opportunities for a governance / tokenomics / research consultant.

ACCEPT (is_opportunity=true) ONLY if the post is a DAO, protocol team, or foundation ACTIVELY SOLICITING EXTERNAL WORK in governance, tokenomics, or governance-research. A clear call to action is required: an RFP, a grant round seeking applicants, an open role, a contracting ask, or an explicit "we need X help" post with a way to respond.

REJECT (is_opportunity=false):
- Routine governance votes or proposal announcements.
- Delegate self-promotion / candidate statements.
- Proposal drafts authored by internal contributors who are not seeking outside help.
- Status updates, retrospectives, quarterly reports.
- Analysis, opinion, or debate posts.
- Celebration / milestone threads.
- Meta-discussion ABOUT governance that isn't asking for help.
- Posts that only MENTION governance / tokenomics / research in passing.

Tie-breakers:
- When uncertain, set confidence <= 0.5 and is_opportunity=false.
- Prefer missed opportunities over false alerts. Every false alert costs the consultant's attention.
- opportunity_type: pick "rfp" for formal requests for proposals, "grant" for grant programs / open grant rounds, "hire" for paid roles or contractor hires, "advisory_request" for informal "looking for advice / guidance" posts with a clear path to respond, "other" only if none fit.

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
            confidence=0.95,
            one_line_reason="Open grant round with concrete scope, amounts, and an application path.",
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
        "Forum: Compound Governance\nTitle: Thinking about tokenomics redesign — anyone done this before?\nExcerpt: We've been discussing whether COMP's incentive structure still fits our current stage. Curious if any contributors here have experience with mid-stage tokenomics overhauls, or can point us to firms that have done this well. Just exploring options at this point, nothing formal yet.",
        Classification(
            is_opportunity=True,
            opportunity_type="advisory_request",
            call_to_action="Asking for contributors or firms with experience in mid-stage tokenomics redesigns; informal but open to outside input.",
            confidence=0.72,
            one_line_reason="Informal but real: explicit ask for outside expertise with a path to engage via reply. Lower confidence because 'exploring' not 'hiring'.",
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


def classify_post(forum_name: str, title: str, excerpt: str, client: genai.Client | None = None) -> Classification | None:
    """Classify a single forum post. Returns None on API failure (caller continues)."""
    client = client or get_client()
    contents = _build_contents(forum_name, title, excerpt)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=Classification,
                temperature=0.1,
            ),
        )
    except Exception as e:
        print(f"    WARN: Gemini call failed: {type(e).__name__}")
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
