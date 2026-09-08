import { deleteRequest, getBlob, postForm } from "./client";
import type { AttachmentResponse } from "./types";

/**
 * POST-5.1 B3 — uploads one real image `File` to the backend's chat
 * attachment endpoint (`POST /api/sessions/{sessionId}/attachments`,
 * backend/api/app.py). Builds a `FormData` with EXACTLY the field name
 * the backend expects (`file`) and lets the browser compute the
 * multipart boundary itself — never manually sets `Content-Type` (see
 * `postForm`'s own docstring in client.ts). No GCS/bucket/storage
 * knowledge exists on this side at all: the response is already the
 * safe, backend-mapped `AttachmentResponse` DTO.
 */
export async function uploadAttachment(
  sessionId: string,
  file: File,
  signal?: AbortSignal,
): Promise<AttachmentResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return postForm<AttachmentResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}/attachments`,
    formData,
    signal,
  );
}

/**
 * POST-5.1 B4D — fetches one persisted attachment's real binary content
 * (`GET /api/attachments/{attachmentId}/content`, backend/api/app.py).
 * Backend-authenticated and ownership-checked (see
 * `backend/api/attachment_service.py`'s `get_attachment_content` — a
 * wrong-owner or unknown id both return the same generic "No such
 * attachment was found." SafeError, an anti-enumeration property this
 * function never needs to special-case). No `sessionId` — the B2 content
 * route was never scoped by session, only by `attachment_id` + the
 * resolved caller identity. Returns a `Blob` only for a genuinely
 * successful response; this side knows nothing about GCS/bucket/storage
 * internals, only the attachment id and the bytes the backend already
 * authorized. `signal`, when provided, lets a caller (a persisted-image
 * component unmounting or switching to a different reference) cancel an
 * in-flight request — mirrors `uploadAttachment`'s own `signal` handling.
 */
export async function getAttachmentContent(attachmentId: string, signal?: AbortSignal): Promise<Blob> {
  return getBlob(`/api/attachments/${encodeURIComponent(attachmentId)}/content`, signal);
}

/**
 * POST-5.1 B7 — deletes a still-`READY` (never sent) attachment the user
 * removed from their draft before sending (`DELETE /api/sessions/
 * {sessionId}/attachments/{attachmentId}`, backend/api/app.py). Session-
 * scoped, mirroring `uploadAttachment`'s own URL shape — never called for
 * an attachment already `attachmentId`-less (still uploading/pending/
 * failed on the frontend; see AppState.tsx's `removeAttachment`, the only
 * caller). The backend rejects deleting an attachment that has already
 * been LINKED to a sent message; this function does not need to guard
 * against that itself. Best-effort from the caller's perspective — see
 * `removeAttachment`'s own docstring for why a failure here never blocks
 * the local draft removal that already happened.
 */
export async function deleteAttachment(sessionId: string, attachmentId: string): Promise<void> {
  return deleteRequest(
    `/api/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(attachmentId)}`,
  );
}
