import { postForm } from "./client";
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
