"""
Phase 3: agent core.

Runs one email through a Claude tool-use loop: the model can call
get_thread_context, get_sender_history, and propose_gmail_action zero
or more times (capped) before giving its final category/priority/
summary/reasoning. Which tools it chooses to call -- and how many --
is the whole point of this phase; a fixed pipeline that always calls
every tool would defeat the purpose.
"""

import json

import anthropic

from src.ai.summarizer import CATEGORIES
from src.agent.tools import TOOL_DEFINITIONS, get_sender_history, get_thread_context, propose_gmail_action
from src.config import AGENT_MAX_TOOL_CALLS, AGENT_MODEL, ANTHROPIC_API_KEY
from src.gmail.client import ParsedEmail

EmailData = ParsedEmail  # same alias convention as src/ai/summarizer.py

MAX_BODY_INPUT_CHARS = 4_000  # mirrors summarizer.py's cap on prompt size

SYSTEM_PROMPT = f"""You are an email triage agent. You will be given the \
contents of a single email, including its Gmail message ID and thread ID. \
Your job is to categorize and summarize it, using tools ONLY when they \
genuinely help.

Available tools and when to use them:

- get_thread_context: ONLY call this if the email looks like a reply -- \
subject starts with "Re:", or the body clearly continues an existing \
conversation. Do NOT call it on obvious first-contact emails (newsletters, \
cold outreach, automated notifications, receipts) -- there's no thread \
history worth fetching and it wastes a call.

- get_sender_history: ONLY call this when it's genuinely ambiguous whether \
the sender is a known regular correspondent or a stranger. If the sender's \
name, domain, or content already makes that obvious (e.g. a clearly \
automated "noreply@" newsletter address, or someone you can already tell \
is a personal contact from the content), do NOT call this tool.

- propose_gmail_action: ONLY call this when you are highly confident an \
action is warranted -- e.g. an obvious newsletter or automated notification \
is a clear "archive" candidate. If you are at all uncertain, do NOT call \
this tool; it is better to leave no proposed action than to guess.

Cost and latency matter. Do not call tools reflexively on every email --\
 only call a tool when its description's condition is actually met. Many \
emails should get zero tool calls.

Once you have everything you need, respond with ONLY a single valid JSON \
object -- no preamble, no markdown fences, no tool calls in the same \
turn as this final answer. The JSON object must have exactly these four keys:

- "category": one of exactly these strings (case-sensitive):
  {json.dumps(list(CATEGORIES))}
- "priority": an integer from 1 to 5 (5 = most urgent/important).
- "summary": 1-2 sentences in plain language describing what the email \
actually says. Do not just restate the subject line.
- "reasoning": one short sentence explaining your category/priority \
choice, and if you used any tools, how what they returned informed your \
decision.

Do not include "tools_used" or "proposed_action" keys yourself -- those \
are tracked automatically from the tool calls you actually made."""


class AgentResult:
    """
    Final result of run_agent(). Deliberately a plain class (not a
    dataclass) so tools_used/proposed_action can be attached from the
    tracked tool-call history in code -- not re-parsed from the model's
    own JSON -- since that tracked state is ground truth and the model's
    self-report of it would just be a second, less reliable copy.
    """

    def __init__(self, category, priority, summary, reasoning, tools_used, proposed_action, tool_call_log):
        self.category = category
        self.priority = priority
        self.summary = summary
        self.reasoning = reasoning
        self.tools_used = tools_used
        self.proposed_action = proposed_action
        # Full per-call record (tool_name/tool_input/tool_result), one dict
        # per tool call made -- this is what Phase 4 persists into the
        # agent_logs table so the full reasoning trail is reconstructable.
        self.tool_call_log = tool_call_log

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "priority": self.priority,
            "summary": self.summary,
            "reasoning": self.reasoning,
            "tools_used": self.tools_used,
            "proposed_action": self.proposed_action,
            "tool_call_log": self.tool_call_log,
        }


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file "
                "(see .env.example) before running the agent."
            )
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def run_agent(
    email: EmailData,
    service,
    known_emails: list[ParsedEmail] | None = None,
    client: anthropic.Anthropic | None = None,
) -> AgentResult | None:
    """
    Runs the tool-use loop for a single email and returns an AgentResult,
    or None if something unrecoverable happened (API error, or a final
    response that never parses into valid structured output).

    `service` is an authenticated Gmail service (needed by
    get_thread_context). `known_emails` is the batch of ParsedEmail
    objects already fetched this run (needed by get_sender_history) --
    it does not query Gmail exhaustively.
    """
    known_emails = known_emails or []
    active_client = client or _get_client()

    messages = [{"role": "user", "content": _build_user_message(email)}]
    tools_used: list[str] = []
    tool_call_log: list[dict] = []
    proposed_action: dict | None = None
    tool_call_count = 0

    while True:
        tools_still_allowed = tool_call_count < AGENT_MAX_TOOL_CALLS

        try:
            response = active_client.messages.create(
                model=AGENT_MODEL,
                max_tokens=800,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS if tools_still_allowed else [],
                tool_choice={"type": "auto"} if tools_still_allowed else {"type": "none"},
                messages=messages,
            )
        except anthropic.APIError as exc:
            print(f"[run_agent] Anthropic API error for email {email.id}: {exc}")
            return None

        _log_usage(email, response)

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if tool_use_blocks and tools_still_allowed:
            messages.append({"role": "assistant", "content": response.content})
            tool_result_content = []

            for block in tool_use_blocks:
                if tool_call_count >= AGENT_MAX_TOOL_CALLS:
                    result = {
                        "error": "Tool call budget exhausted for this email. "
                        "Respond now with your best final JSON answer."
                    }
                else:
                    result = _execute_tool(
                        block.name, block.input, service=service, email=email, known_emails=known_emails
                    )
                    tool_call_count += 1
                    tools_used.append(block.name)
                    if block.name == "propose_gmail_action" and "error" not in result:
                        proposed_action = result

                _log_tool_call(email, block.name, block.input, result)
                tool_call_log.append({"tool_name": block.name, "tool_input": block.input, "tool_result": result})
                tool_result_content.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)}
                )

            messages.append({"role": "user", "content": tool_result_content})
            continue

        # No more tool calls -- this is the model's final answer.
        raw_text = "".join(b.text for b in response.content if b.type == "text")
        return _parse_agent_result(email.id, raw_text, tools_used, proposed_action, tool_call_log)


def _build_user_message(email: EmailData) -> str:
    body = email.body_text[:MAX_BODY_INPUT_CHARS]
    return (
        f"Message ID: {email.id}\n"
        f"Thread ID: {email.thread_id}\n"
        f"Subject: {email.subject}\n"
        f"From: {email.sender}\n"
        f"Body:\n{body}"
    )


def _execute_tool(name: str, tool_input: dict, *, service, email: EmailData, known_emails: list[ParsedEmail]) -> dict:
    if name == "get_thread_context":
        thread_id = tool_input.get("thread_id") or email.thread_id
        return {"messages": get_thread_context(service, thread_id, exclude_message_id=email.id)}

    if name == "get_sender_history":
        sender_email = tool_input.get("sender_email", "")
        return get_sender_history(sender_email, known_emails)

    if name == "propose_gmail_action":
        return propose_gmail_action(
            message_id=tool_input.get("message_id") or email.id,
            action=tool_input.get("action"),
            label=tool_input.get("label"),
        )

    return {"error": f"Unknown tool '{name}'"}


def _log_tool_call(email: EmailData, name: str, tool_input: dict, result: dict) -> None:
    print(f"[agent tool_call] email={email.id} tool={name} input={tool_input} result={result}")


def _parse_agent_result(
    email_id: str,
    raw_text: str,
    tools_used: list[str],
    proposed_action: dict | None,
    tool_call_log: list[dict],
) -> AgentResult | None:
    cleaned = _strip_markdown_fences(raw_text).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        print(
            f"[run_agent] Could not parse final JSON for email {email_id}. "
            f"Skipping. Raw response was:\n{raw_text!r}"
        )
        return None

    if not isinstance(data, dict):
        print(f"[run_agent] Expected a JSON object for email {email_id}, got: {raw_text!r}")
        return None

    category = data.get("category")
    priority = data.get("priority")
    summary = data.get("summary")
    reasoning = data.get("reasoning")

    if category not in CATEGORIES:
        print(
            f"[run_agent] Email {email_id}: category {category!r} is not one of "
            f"the allowed values {CATEGORIES}. Treating as a parse failure. "
            f"Raw response was:\n{raw_text!r}"
        )
        return None

    if not isinstance(priority, int) or not (1 <= priority <= 5):
        print(
            f"[run_agent] Email {email_id}: priority {priority!r} is not an "
            f"int 1-5. Treating as a parse failure. Raw response was:\n{raw_text!r}"
        )
        return None

    if not isinstance(summary, str) or not summary.strip():
        print(f"[run_agent] Email {email_id}: missing/empty summary. Raw response was:\n{raw_text!r}")
        return None

    if not isinstance(reasoning, str) or not reasoning.strip():
        print(f"[run_agent] Email {email_id}: missing/empty reasoning. Raw response was:\n{raw_text!r}")
        return None

    return AgentResult(
        category=category,
        priority=priority,
        summary=summary.strip(),
        reasoning=reasoning.strip(),
        tools_used=tools_used,
        proposed_action=proposed_action,
        tool_call_log=tool_call_log,
    )


def _strip_markdown_fences(text: str) -> str:
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
