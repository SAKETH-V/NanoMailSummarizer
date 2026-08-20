"""
Phase 2: AI summarization.

One Claude API call per email. No tool use, no thread/sender context,
no agent loop -- just: one email in, one structured EmailSummary out.
That agent-y stuff is Phase 3.
"""

import json
from dataclasses import dataclass

import anthropic

from src.config import ANTHROPIC_API_KEY, SUMMARIZER_MODEL
from src.gmail.client import ParsedEmail

# Alias so this module reads the way the spec describes it
# (`summarize_email(email: EmailData) -> EmailSummary`), while still
# being the exact same ParsedEmail dataclass Phase 1 produces.
EmailData = ParsedEmail

# Fixed category enum. Downstream code (dashboard filters, DB columns)
# relies on this being a closed set, not free text -- if Claude returns
# anything outside this set, we treat it as a parse failure.
CATEGORIES = ("Urgent", "Action Needed", "FYI", "Newsletter", "Personal", "Spam-like")

MAX_SUMMARY_INPUT_CHARS = 4_000  # keep prompts small; body is already capped at 10k upstream

SYSTEM_PROMPT = f"""You are an email triage assistant. You will be given the \
contents of a single email. Your job is to classify and summarize it.

Respond with ONLY a single valid JSON object. No preamble, no explanation \
outside the JSON, no markdown code fences -- just the raw JSON object, \
starting with {{ and ending with }}.

The JSON object must have exactly these four keys:

- "category": one of exactly these strings (case-sensitive, no others allowed):
  {json.dumps(list(CATEGORIES))}
- "priority": an integer from 1 to 5, where 5 is the most urgent/important \
and 1 is the least (e.g. a newsletter is usually 1-2, an urgent request \
from a person needing a same-day reply is 4-5).
- "summary": 1-2 sentences in plain, everyday language describing what the \
email actually says. Do not just restate the subject line verbatim -- add \
real content (what's being asked, offered, or announced).
- "reasoning": one short sentence explaining why you picked this category \
and priority. This is shown to the user as your reasoning, so make it \
genuinely explanatory, not generic.

Category definitions:
- "Urgent": needs attention very soon (same day), time-sensitive.
- "Action Needed": the recipient needs to do something, but it's not time-critical.
- "FYI": informational, no action needed, from a person or organization the \
recipient has a direct relationship with.
- "Newsletter": recurring bulk/marketing content, digests, promotions.
- "Personal": from a known individual, non-work, conversational.
- "Spam-like": unsolicited, suspicious, or low-quality bulk content.

Example valid response:
{{"category": "Action Needed", "priority": 3, "summary": "The team is asking \
you to review and approve the Q3 budget spreadsheet by Friday.", \
"reasoning": "Requires a specific action from the recipient but has a few \
days of lead time, so it's not urgent."}}"""


@dataclass
class EmailSummary:
    category: str
    priority: int
    summary: str
    reasoning: str

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "priority": self.priority,
            "summary": self.summary,
            "reasoning": self.reasoning,
        }


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file "
                "(see .env.example) before running summarization."
            )
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def summarize_email(email: EmailData, client: anthropic.Anthropic | None = None) -> EmailSummary | None:
    """
    Sends one email through Claude and returns a validated EmailSummary.

    Returns None (and logs why) instead of raising, so that one bad email
    -- a malformed model response, an off-enum category, whatever -- never
    crashes a batch run over many emails.
    """
    active_client = client or _get_client()
    user_message = _build_user_message(email)

    try:
        response = active_client.messages.create(
            model=SUMMARIZER_MODEL,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        print(f"[summarize_email] Anthropic API error for email {email.id}: {exc}")
        return None

    _log_usage(email, response)

    raw_text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_summary(email.id, raw_text)


def _build_user_message(email: EmailData) -> str:
    body = email.body_text[:MAX_SUMMARY_INPUT_CHARS]
    return (
        f"Subject: {email.subject}\n"
        f"From: {email.sender}\n"
        f"Body:\n{body}"
    )


def _parse_summary(email_id: str, raw_text: str) -> EmailSummary | None:
    cleaned = _strip_markdown_fences(raw_text).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        print(
            f"[summarize_email] Could not parse JSON for email {email_id}. "
            f"Skipping. Raw response was:\n{raw_text!r}"
        )
        return None

    if not isinstance(data, dict):
        print(f"[summarize_email] Expected a JSON object for email {email_id}, got: {raw_text!r}")
        return None

    category = data.get("category")
    priority = data.get("priority")
    summary = data.get("summary")
    reasoning = data.get("reasoning")

    if category not in CATEGORIES:
        print(
            f"[summarize_email] Email {email_id}: category {category!r} is not "
            f"one of the allowed values {CATEGORIES}. Treating as a parse "
            f"failure. Raw response was:\n{raw_text!r}"
        )
        return None

    if not isinstance(priority, int) or not (1 <= priority <= 5):
        print(
            f"[summarize_email] Email {email_id}: priority {priority!r} is not "
            f"an int 1-5. Treating as a parse failure. Raw response was:\n{raw_text!r}"
        )
        return None

    if not isinstance(summary, str) or not summary.strip():
        print(f"[summarize_email] Email {email_id}: missing/empty summary. Raw response was:\n{raw_text!r}")
        return None

    if not isinstance(reasoning, str) or not reasoning.strip():
        print(f"[summarize_email] Email {email_id}: missing/empty reasoning. Raw response was:\n{raw_text!r}")
        return None

    return EmailSummary(
        category=category,
        priority=priority,
        summary=summary.strip(),
        reasoning=reasoning.strip(),
    )


def _strip_markdown_fences(text: str) -> str:
    """
    Defensive cleanup in case Claude wraps the JSON in ```json ... ```
    despite being told not to. Only strips fences if the whole response
    is wrapped in them -- doesn't touch JSON that's already clean.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return stripped


def _log_usage(email: EmailData, response) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    print(
        f"[usage] email={email.id} input_tokens={usage.input_tokens} "
        f"output_tokens={usage.output_tokens}"
    )
