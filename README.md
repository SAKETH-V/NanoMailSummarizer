# nano-mail-agent

**Phase 1: Foundation.** Reliable Gmail auth + read + parse. No AI, no
database, no writes to Gmail.

**Phase 2: AI summarization.** Adds exactly one Claude API call per
email, returning structured JSON (category / priority / summary /
reasoning). No tool use, no thread/sender context, no agent loop yet —
that's Phase 3. This is deliberately the simplest possible AI
integration: one email in, one structured object out.

**Phase 3: agent core (tool use).** The single fixed API call becomes
a loop: Claude can call `get_thread_context`, `get_sender_history`, and
`propose_gmail_action` zero or more times (capped at 5) before giving
its final categorization. Nothing is written to Gmail yet —
`propose_gmail_action` only *records* a suggestion for later review.
The interesting thing to check isn't the categorization quality (that
was Phase 2) — it's whether the agent calls tools *selectively*
(thread context on replies, sender history only when ambiguous) rather
than reflexively on every email.

**Phase 4: persistence (SQLite).** Every processed email — its parsed
content, its summary, every tool call the agent made, and any proposed
action — is written to a local SQLite DB via SQLAlchemy. The pipeline
checks `email_exists()` *before* running the agent, so a
previously-processed email is skipped before it ever reaches Claude —
no wasted tokens on emails you've already triaged.

```
nano-mail-agent/
  src/
    auth/           # OAuth flow, token storage
    gmail/          # Gmail API client wrapper
    ai/             # Claude summarization (Phase 2)
    agent/          # Tool-use agent loop (Phase 3)
    db/             # SQLite persistence via SQLAlchemy (Phase 4)
    pipeline.py      # fetch -> dedup -> agent -> persist, reusable (Phase 4)
    config.py       # env var loading
  scripts/
    fetch_test.py       # manual test script for Phase 1
    summarize_test.py   # manual test script for Phase 2
    agent_test.py        # manual test script for Phase 3
    pipeline_test.py     # manual test script for Phase 4
  tests/           # unit tests (parsing + summarization + agent + db, no network needed)
  .env.example
  requirements.txt
  README.md
```

**Acceptance test for Phase 1:** running `python scripts/fetch_test.py`
against a real (or test) Gmail account prints 10 parsed emails with
correct sender, subject, and readable body text — no raw MIME junk, no
crashes on HTML-only emails.

**Acceptance test for Phase 2:** running `python scripts/summarize_test.py`
prints 5 real emails, each with a sensible category, priority, and
summary (an FYI-style newsletter shouldn't come back `Urgent`), and
the run survives a malformed/off-enum model response without crashing.

**Acceptance test for Phase 3:** running `python scripts/agent_test.py`
shows `get_thread_context` firing on reply emails but not cold/first-
contact ones, and `get_sender_history` firing only on genuinely
ambiguous senders — not on every single email. That selectivity is the
evidence the agent is reasoning about each email, not running a fixed
pipeline.

**Acceptance test for Phase 4:** running `python scripts/pipeline_test.py`
twice back to back shows RUN 1 processing N emails and RUN 2 processing
zero, with no new `[usage]` lines logged during RUN 2 (proof no Claude
calls were made for already-processed mail). Deleting one row from the
`emails` table and rerunning reprocesses only that one email —
confirming dedup is tracked per-email, not all-or-nothing.

---

## 1. Google Cloud Console setup (manual, one-time)

This happens entirely in the browser, not in code. You're creating a
"Google Cloud project" that acts as the identity your app uses to ask
Gmail for permission.

### 1.1 Create a Google Cloud project

1. Go to https://console.cloud.google.com/
2. Top-left, click the project dropdown → **New Project**.
3. Give it a name, e.g. `nano-mail-agent`. Click **Create**.
4. Make sure the new project is selected (check the dropdown again).

### 1.2 Enable the Gmail API

1. In the left sidebar (or search bar at top), go to **APIs & Services
   > Library**.
2. Search for **Gmail API**.
3. Click it, then click **Enable**.

### 1.3 Configure the OAuth consent screen

This is the screen users see when they log in and grant your app
permission.

1. Go to **APIs & Services > OAuth consent screen**.
2. Choose **External** user type (this is fine even for personal/test
   use) → **Create**.
3. Fill in the required fields: app name (e.g. `nano-mail-agent`), your
   email as the user support email, and your email again as developer
   contact info. Click **Save and Continue**.
4. **Scopes** step: click **Add or Remove Scopes**, search for and
   select:
   - `.../auth/gmail.readonly`

   This is the *only* scope we request in this phase. Write scopes
   (labeling, archiving, sending) come in Phase 3, once read-only auth
   is proven solid. Requesting a smaller scope now also means a faster
   Google review later, since read-only scopes get lighter scrutiny
   than scopes that can modify your mailbox.
5. Click **Save and Continue** through the rest.
6. On the **Test users** step, add your own Gmail address as a test
   user. While the app is in "Testing" publishing status (the default,
   and totally fine for this project), only test users you list here
   can log in.
7. Leave publishing status as **Testing** — no need to submit for
   verification for a personal/dev project like this.

### 1.4 Create OAuth Client ID credentials

1. Go to **APIs & Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. Application type: **Desktop app**.
4. Give it a name (e.g. `nano-mail-agent-desktop`) → **Create**.
5. A dialog shows your client ID/secret — click **Download JSON**.
6. Rename the downloaded file to `credentials.json` and place it in
   the project root (same folder as `README.md`).

   **Do not commit this file.** It's already listed in `.gitignore`.

---

## 2. Anthropic API key (for Phase 2)

1. Go to https://console.anthropic.com/settings/keys and create an API
   key (you'll need an Anthropic account with billing set up).
2. You'll add this to `.env` in the next step. Unlike `credentials.json`
   and `token.json`, this key lives directly in `.env` as a plain
   string — still gitignored, still never committed.

---

## 3. Install dependencies

```bash
cd nano-mail-agent

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## 4. Configure environment variables

```bash
cp .env.example .env
```

Then open `.env` and paste in your real Anthropic API key from step 2:

```
ANTHROPIC_API_KEY=sk-ant-...
```

The Gmail-related defaults (`credentials.json` and `token.json`, both
in the project root) work fine as-is — you only need to edit those if
you want to store those files somewhere else.

## 5. Run the Phase 1 acceptance test (Gmail fetch + parse)

```bash
python scripts/fetch_test.py
```

**First run:** a browser window opens asking you to log in with the
Google account you added as a test user, and to consent to read-only
Gmail access. After you approve, the script saves a `token.json` file
(gitignored) so you won't have to log in again.

**Subsequent runs:** the script loads `token.json` and silently
refreshes it if the access token has expired. If Google ever revokes
or expires the refresh token itself (e.g. you changed your Google
password, or it's been unused too long), the script detects that,
deletes the stale `token.json`, and prompts you to log in again
automatically — you don't need to delete anything by hand.

You should see 10 emails printed, each with subject, sender, received
timestamp, message/thread IDs, and a readable body preview — including
emails that are HTML-only (they get stripped down to plain text
automatically).

## 6. Run the Phase 2 acceptance test (AI summarization)

```bash
python scripts/summarize_test.py
```

This authenticates with Gmail (reusing your cached `token.json` if you
already ran the Phase 1 test), fetches your 5 most recent inbox
emails, and sends each one to Claude for classification. For each
email you'll see the subject next to the model's `category`,
`priority`, `summary`, and `reasoning`, plus a `[usage] ...` line
logging real input/output token counts for that call — useful for a
back-of-envelope cost-per-email number.

If Claude ever returns something that doesn't parse as valid JSON, or
returns a `category` outside the fixed enum, that email is logged and
skipped rather than crashing the run — you'll see a
`[summarize_email] ...` line explaining why, followed by the rest of
the batch continuing normally.

## 7. Run the Phase 3 acceptance test (agent core / tool use)

```bash
python scripts/agent_test.py        # defaults to 10 emails
python scripts/agent_test.py 5      # or pass a count explicitly
```

This fetches the most recent inbox emails, then runs each through the
agent loop. Every tool call the agent makes is logged as it happens:

```
[agent tool_call] email=... tool=get_thread_context input={'thread_id': '...'} result={...}
```

For each email you'll see category/priority/summary/reasoning as
before, plus which tools (if any) were used and any proposed action.
At the end, a summary line totals tool calls across the whole batch —
if `get_thread_context` or `get_sender_history` show up on *every*
email (or never at all), that's a sign the prompt needs tightening,
since the point of this phase is selective tool use.

Nothing here writes to Gmail. `propose_gmail_action` calls are logged
and returned in `proposed_action`, but never executed — that's still
gated behind a future approval flow.

## 9. Run the Phase 4 acceptance test (persistence / dedup)

```bash
python scripts/pipeline_test.py        # defaults to 10 emails
python scripts/pipeline_test.py 5      # or pass a count explicitly
```

This runs the full pipeline (fetch → dedup check → agent → persist)
twice back to back against your inbox. RUN 1 processes and stores N
emails; RUN 2 should process **zero** — and since a skipped email
never reaches the agent, RUN 2 should show no new `[usage]` token-log
lines either. That's the real proof: not just that the DB has rows,
but that the second run made zero Claude API calls.

A SQLite file (`nano_mail_agent.db` by default, see `DB_PATH` in
`.env.example`) is created in the project root the first time you run
this. You can inspect it directly:

```bash
sqlite3 nano_mail_agent.db ".tables"
sqlite3 nano_mail_agent.db "SELECT id, subject FROM emails;"
sqlite3 nano_mail_agent.db "SELECT * FROM agent_logs;"
```

To confirm dedup is tracked **per email**, not all-or-nothing: delete
one row and rerun —

```bash
sqlite3 nano_mail_agent.db "DELETE FROM emails WHERE id='<some id>';"
python scripts/pipeline_test.py
```

Only that one email should be reprocessed; everything else stays
skipped.

## 10. Run the unit tests (optional but recommended)

These test the MIME-parsing logic directly with fake payloads — no
Gmail account or network access needed:

```bash
python -m pytest tests/
```

---

## Design notes for future phases

- `get_message()` returns a `ParsedEmail` dataclass with the shape
  `{id, thread_id, sender, subject, received_at, body_text}`. This is
  the contract every later phase (summarization, categorization,
  drafting replies) builds on — don't change this shape lightly.
- Email bodies are truncated at 10,000 characters (`MAX_BODY_CHARS` in
  `src/gmail/client.py`) to avoid blowing up context windows.
  Summarization additionally caps what it sends to Claude at 4,000
  characters (`MAX_SUMMARY_INPUT_CHARS` in `src/ai/summarizer.py`) —
  triage doesn't need the full body, just enough to classify.
- Gmail messages are MIME trees, not flat structures — the parser
  recursively walks all parts (handles multipart/alternative nested in
  multipart/mixed, etc.) rather than assuming the body is the first
  part.
- `EmailSummary` (`{category, priority, summary, reasoning}`) is the
  Phase 2 output contract. `category` is a closed enum
  (`src/ai/summarizer.py:CATEGORIES`) on purpose — free text would
  break any downstream dashboard filter or DB column. The `reasoning`
  field is intentionally captured now, even with no agent yet, because
  it becomes the "why did the agent do that" transparency story once
  Phase 3 adds an actual agent loop.
- Every summarization/agent call logs real `input_tokens`/`output_tokens`
  from `response.usage` — use these numbers for a real cost-per-email
  figure rather than guessing.
- `AgentResult.tools_used` and `.proposed_action` are populated from
  the tool calls actually executed in code, not re-parsed from the
  model's own JSON — the final prompt explicitly tells Claude not to
  self-report those fields, since tracked execution history is more
  trustworthy than an LLM's account of its own actions.
- `get_sender_history` only looks at the batch of emails already
  fetched this run (in-memory, passed in as `known_emails`) — no DB,
  no exhaustive mailbox search. It's a good-enough "regular or
  stranger" signal for triage, not a full contact history.
- The agent's tool-call loop is hard-capped at `AGENT_MAX_TOOL_CALLS`
  (default 5, in `src/config.py`) — once hit, the next request is sent
  with `tools=[]`, forcing Claude to give its best final answer instead
  of continuing to loop.
- `save_processed_email()` (`src/db/database.py`) is the one place that
  writes to the DB for a given email, and it wraps the email row +
  summary + every agent-log row + any proposed action in a single
  transaction. If anything raises partway through, the whole thing
  rolls back — you'll never find an email row with no summary, or a
  summary pointing at an email that isn't there.
- `src/pipeline.py`'s `process_inbox()` is the reusable orchestration
  function (fetch → dedup → agent → persist), kept separate from
  `scripts/pipeline_test.py` on purpose — Phase 5's scheduler should be
  able to call `process_inbox()` directly on a timer without going
  through a script.
- Dedup (`email_exists()`) is checked *before* `run_agent()` is called,
  not after — an already-processed email never reaches Claude at all,
  which is what makes "zero new token usage on rerun" true rather than
  just "zero new DB rows."

## Explicitly out of scope

**Phase 1** intentionally left out: any Claude/LLM calls, a database,
write actions to Gmail, and scheduling.

**Phase 2** intentionally left out (all planned for Phase 3):
- No tool use — this is a single structured-output call, not an agent
- No thread context or sender history — each email is summarized in
  isolation
- No database / persistence of summaries
- No Gmail write actions (labeling, archiving) — still `gmail.readonly`
  only; write scopes get requested when they're actually needed

**Phase 3** intentionally left out (planned for later phases):
- No actual Gmail writes — `propose_gmail_action` only records a
  suggestion; archiving/labeling for real needs a human approval flow
- No database / persistence of agent results or proposed actions
- No scheduling / background running

**Phase 4** intentionally left out (planned for later phases):
- No scheduling/cron — `pipeline_test.py` is still run by hand
- No dashboard UI for browsing stored emails/summaries/logs
- No real Gmail writes — `proposed_actions` rows stay at status
  `pending` until a future approval flow can move them to
  `approved`/`rejected` and actually execute them
