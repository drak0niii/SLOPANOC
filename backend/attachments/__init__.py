"""POST-5.1 B1: the durable Chat Attachment domain.

Answers: "how does a normal sent chat image become a real, saved
conversation resource -- surviving restart, reload, and follow-up turns --
without ever putting image binary in Cloud SQL PostgreSQL?" See
docs/ (B0 architecture audit) for the full rationale; this package
implements only the persistent-metadata foundation B0 designed.

  - models.py     -- `ChatAttachmentRecord` (SQLAlchemy ORM, table
                      `slopanoc_chat_attachments`) and `ChatAttachmentStatus`
                      (the closed `READY`/`LINKED`/`DELETED` lifecycle).
                      Metadata/reference only -- no binary/base64/data-URL
                      column exists or should ever exist here.
  - repository.py -- `AttachmentRepository`, the async-SQLAlchemy data
                      access layer. Owns its own engine/table set, never
                      shared with Case or ADK session tables. Lifecycle
                      transitions are atomic conditional UPDATEs, never
                      check-then-write.
  - service.py    -- `AttachmentService`, the deterministic domain layer:
                      ownership re-verification (anti-enumeration, same
                      discipline as Cases/ADK sessions), and the legal
                      state-machine transitions (`READY -> LINKED`,
                      `READY|LINKED -> DELETED`).
  - storage.py    -- `ChatAttachmentStorage`, a thin private-GCS wrapper
                      (put/get/delete) plus the opaque object-key naming
                      contract (`chat-attachments/<session_id>/
                      <attachment_id>` -- no filename, no owner identity,
                      no user-entered value in the physical path).

NOT BUILT IN B1 (later passes, per the locked B0-B7 sequence): no HTTP
upload/retrieve endpoints (B2), no frontend wiring (B3), no conversation/
attachment rehydration (B4), no Gemini/ADK `Part.from_uri` construction or
`attachment_ids` on the message-send path (B5), no AgentTool propagation
to Incident Manager (B6), no lifecycle cleanup/retention sweep (B7).

LOCKED ARCHITECTURE PRINCIPLE (proven in B0, preserved here for B5 to
consume): a durable chat image's binary lives ONLY in private GCS;
`google.genai.types.Part.from_uri(file_uri="gs://...", ...)` is the sole
sanctioned construction for referencing it in model input, because ADK's
`DatabaseSessionService` persists only the URI string for that
construction. `Part.from_bytes(...)` is forbidden for this path --
proven (B0, disposable local experiment) to serialize the full image as
base64 directly into the `events` table. This package's own schema
enforces the same discipline independently: no `LargeBinary`/`BYTEA`/
`BLOB` column exists in `ChatAttachmentRecord`.
"""
