import { getBlob } from "./client";

/**
 * Teams Visual Evidence milestone — fetches one real, already-Gemini-
 * delivered Teams image's binary content (`GET /api/sessions/{sessionId}/
 * sources/{sourceId}/images/{imageId}`, backend/api/app.py).
 * Backend-authenticated and session-ownership-
 * checked (see `backend/api/source_images.py`'s own module docstring) —
 * an unknown/foreign session, unknown source, or unknown image all return
 * the identical generic SafeError, an anti-enumeration property this
 * function never needs to special-case, mirroring `getAttachmentContent`'s
 * own contract exactly. `sourceId`/`imageId` are both OPAQUE, server-
 * minted tokens from `SourceReferenceDTO`/`SourceVisualEvidenceItemDTO` —
 * this side never knows, and never needs to know, the real Teams chat_id/
 * message_id/hosted_content_id they resolve to server-side. `signal`, when
 * provided, lets a caller (a thumbnail/preview component unmounting or
 * re-targeting) cancel an in-flight request.
 */
export async function getSourceImageContent(
  sessionId: string,
  sourceId: string,
  imageId: string,
  signal?: AbortSignal,
): Promise<Blob> {
  return getBlob(
    `/api/sessions/${encodeURIComponent(sessionId)}/sources/${encodeURIComponent(sourceId)}/images/${encodeURIComponent(imageId)}`,
    signal,
  );
}
