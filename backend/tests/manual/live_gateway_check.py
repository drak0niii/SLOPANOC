"""Manual, local-only check against the REAL Power Automate gateway.

NOT collected by pytest -- this file is deliberately named outside the
`test_*.py` pattern and lives in a package pytest is never pointed at, so
the automated suite can never accidentally hit a live gateway. Run it
explicitly, with a real gateway URL:

    PYTHONPATH=. SLOPANOC_POWER_AUTOMATE_GATEWAY_URL="<real gateway url>" \\
        python -m backend.tests.manual.live_gateway_check "<exact chat title>" [max_messages]

Or, for a deployed gateway resolved via Secret Manager:

    PYTHONPATH=. SLOPANOC_POWER_AUTOMATE_SECRET_RESOURCE="projects/<p>/secrets/<n>/versions/latest" \\
        python -m backend.tests.manual.live_gateway_check "<exact chat title>" [max_messages]

`max_messages` is optional (defaults to `teams_get_messages`'s own
default, currently 200) -- pass a smaller number (e.g. `60`) against a
chat with more history than that to manually exercise multi-page
retrieval and `truncated`/`next_before`.

Prints only chat titles/ids and message counts/content -- never the
configured gateway URL.
"""
from __future__ import annotations

import sys

from backend.tools.teams.get_messages import teams_get_messages
from backend.tools.teams.list_chats import teams_list_chats


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(
            'Usage: python -m backend.tests.manual.live_gateway_check '
            '"<exact chat title>" [max_messages]'
        )
        return 2

    topic = sys.argv[1]
    max_messages_kwargs = {}
    if len(sys.argv) == 3:
        max_messages_kwargs["max_messages"] = int(sys.argv[2])

    list_result = teams_list_chats(topic=topic)
    if "error" in list_result:
        error = list_result["error"]
        print(f"teams_list_chats error: {error['errorCode']} -- {error['userMessage']}")
        return 1

    print(f"match: {list_result['match']}")
    if list_result["match"] != "matched":
        candidate_titles = [c["title"] for c in list_result["candidates"]]
        print(f"candidates: {candidate_titles}")
        return 0

    matched_chat = list_result["matched_chat"]
    print(f"matched chat: {matched_chat['title']} ({matched_chat['chat_id']})")

    messages_result = teams_get_messages(chat_id=matched_chat["chat_id"], **max_messages_kwargs)
    if "error" in messages_result:
        error = messages_result["error"]
        print(f"teams_get_messages error: {error['errorCode']} -- {error['userMessage']}")
        return 1

    messages = messages_result["messages"]
    print(
        f"retrieved {messages_result['retrieved_count']} message(s) "
        f"(raw={messages_result['retrieved_count_raw']}, "
        f"filtered_system_events={messages_result['filtered_system_event_count']}, "
        f"oldest={messages_result['oldest_retrieved_at']}, "
        f"newest={messages_result['newest_retrieved_at']}, "
        f"truncated={messages_result['truncated']}, "
        f"next_before={messages_result['next_before']})"
    )
    for msg in messages:
        preview = msg["text"][:80]
        print(f"  [{msg['sent_at']}] {msg['author']}: {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
