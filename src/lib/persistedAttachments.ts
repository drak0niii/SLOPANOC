import type { Message } from "../types";

/** POST-5.1 B4D correction pass — LOCKED PRODUCT RULE: a user turn that
 * owns a durable, server-linked image reference is intentionally
 * immutable until multimodal edit/rewrite semantics are explicitly
 * designed — editing it while silently retaining/dropping/re-associating
 * the image would create ambiguous attachment ownership against the
 * original ADK turn/invocation. This is the ONE authoritative signal,
 * used identically by Message.tsx's `UserMessageActions` (the UI
 * reflection) and AppState.tsx's `editMessage` (the real, structural
 * enforcement boundary) — never inferred from message text, filename
 * matching, regex, or DOM state.
 *
 * Future B5 invariant: once a live-sent turn can carry a real image, the
 * frontend Message representing it must expose this same
 * `persistedAttachments` signal (or an equivalent structured
 * image-ownership field) immediately, in the same turn — not only after
 * a later reload/rehydration — so this prohibition holds both before and
 * after refresh. B5 has not implemented that yet.
 */
export function hasPersistedImageAttachment(message: Pick<Message, "persistedAttachments">): boolean {
  return Boolean(message.persistedAttachments && message.persistedAttachments.length > 0);
}
