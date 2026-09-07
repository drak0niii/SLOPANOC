import { ApiError } from "../api/client";

const GENERIC_UPLOAD_ERROR = "Upload failed. Try again.";

/** POST-5.1 B3 — maps a failed `uploadAttachment` call to a safe,
 * user-facing string. Branches on the backend's own structured
 * `errorCode` (backend/gateway/safe_error.py) rather than raw HTTP
 * status where possible — mirrors `classifyApprovalFailure`/
 * `classifySelectionFailure`'s exact same discipline. Never echoes GCS
 * bucket names, SQL detail, or a raw exception message — `ApiError`
 * already guarantees `message` is either the backend's own pre-written
 * `userMessage` or a fixed generic fallback (see api/client.ts), so this
 * function only ever refines which fixed string to show, never invents
 * detail beyond what the backend already decided was safe to say.
 */
export function classifyAttachmentUploadFailure(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.errorCode) {
      case "payload_too_large":
        return "Image is too large.";
      case "unsupported_media_type":
        return "This image format isn't supported.";
      case "not_found":
        return "Unable to upload this image in this conversation.";
      case "connector_unavailable":
        return "Image upload is temporarily unavailable.";
      default:
        return error.message || GENERIC_UPLOAD_ERROR;
    }
  }
  return GENERIC_UPLOAD_ERROR;
}
