/** POST-5.1 B3 — frontend UX guards only, not security/authority. The
 * backend (backend/config/settings.py's `SLOPANOC_CHAT_ATTACHMENT_MAX_BYTES`
 * / `_MAX_IMAGES_PER_TURN`) remains the one authoritative enforcement
 * point; these exist purely so the composer can reject an obviously-too-
 * large or over-limit selection locally, before ever starting a network
 * request, without duplicating the backend's own separately-configured
 * values. Centralized here (rather than scattered literals) per the
 * "one place, not many magic numbers" discipline. */
export const MAX_DRAFT_IMAGES = 4;
/** Mirrors the backend's default `SLOPANOC_CHAT_ATTACHMENT_MAX_BYTES` (8
 * MiB) — a preflight UX check only; the backend re-validates independently
 * and authoritatively on every upload regardless of what this constant is. */
export const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

/** The exact three image types POST-5.1 B currently supports end-to-end —
 * mirrors backend/attachments/validation.py's `SUPPORTED_MIME_TYPES`
 * exactly. Never GIF/SVG/PDF/Word/Excel/audio/video — the backend rejects
 * all of those regardless of what the frontend does, but rejecting them
 * locally (via the file picker's `accept` attribute and paste/drop
 * filtering) avoids an always-doomed upload attempt and a confusing error. */
export const ACCEPTED_IMAGE_MIME_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export type AcceptedImageMimeType = (typeof ACCEPTED_IMAGE_MIME_TYPES)[number];

/** POST-5.1 B3 overflow-UX closure pass — how long the "Up to N images can
 * be attached." notice stays visible before auto-dismissing. Non-blocking
 * and non-persistent by design (see `DraftState.attachmentLimitNotice`) —
 * long enough to read, short enough to never feel like a stuck error. */
export const ATTACHMENT_LIMIT_NOTICE_DURATION_MS = 4000;
