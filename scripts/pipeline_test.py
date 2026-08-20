"""
Manual acceptance test for Phase 4.

Runs the full pipeline (fetch -> dedup check -> agent -> persist)
twice in a row against the same inbox:

  - RUN 1 should process and store N emails.
  - RUN 2 should process zero -- everything's already in the DB, so
    every email gets skipped before the agent ever runs. Watch for
    [usage] lines (from Phase 2/3's token logging): RUN 2 should
    produce none, since a skipped email never reaches Claude.

To test that dedup is per-email rather than all-or-nothing: after this
script finishes, manually delete one row from the `emails` table (e.g.
`sqlite3 nano_mail_agent.db "DELETE FROM emails WHERE id='<some id>';"`)
and rerun this script -- only that one email should be reprocessed.

Run with:
    python scripts/pipeline_test.py [N]

N defaults to 10.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import process_inbox

SEPARATOR = "-" * 70


def _run_once(label: str, max_results: int) -> None:
    print(SEPARATOR)
    print(f"{label}  (max_results={max_results})")
    print(SEPARATOR)

    try:
        stats = process_inbox(max_results=max_results)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"\nSetup error: {exc}")
        sys.exit(1)

    print(
        f"\nFetched: {stats.fetched}  |  Skipped (already in DB): "
        f"{stats.skipped_existing}  |  Newly processed: {stats.processed}  |  "
        f"Failed: {stats.failed}"
    )
    if stats.processed_email_ids:
        print(f"Newly processed IDs: {stats.processed_email_ids}")


def main() -> None:
    max_results = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    _run_once("RUN 1", max_results)
    print(
        "\nRunning the exact same pipeline again -- everything above should "
        "already be in the DB now.\n"
    )
    _run_once("RUN 2", max_results)

    print(SEPARATOR)
    print(
        "\nCheck the output above: RUN 2 should show 0 newly processed "
        "emails, and -- since skipped emails never reach the agent -- no "
        "new [usage] lines should have appeared during RUN 2 either. That's "
        "the proof no Claude API calls were made for already-processed mail."
    )
    print(
        "\nTo verify dedup is per-email rather than all-or-nothing: delete "
        "one row from the `emails` table and rerun this script -- only that "
        "one email should be reprocessed, everything else should still be "
        "skipped."
    )


if __name__ == "__main__":
    main()
