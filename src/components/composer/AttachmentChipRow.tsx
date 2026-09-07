import { File, FolderClosed, Loader2, RefreshCw, TriangleAlert, X } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { Chip } from "../ui/Chip";
import { cn } from "../../lib/cn";
import type { DraftImageAttachment } from "../../types";

/** POST-5.1 B3 — one real image draft's chip: a local `objectUrl` thumbnail
 * (never re-fetched, never base64) plus a truthful upload-state overlay.
 * Deliberately a small, dedicated component rather than an extension of the
 * generic `Chip` (instruction section 19) — a thumbnail + retry + upload
 * overlay is genuinely different from the label/meta/remove shape every
 * other attachment chip uses, and forcing it into `Chip` would leak
 * image-specific concerns into a component every non-image chip also uses.
 * Still reuses the same design tokens (border-subtle, surface-raised,
 * rounded-lg, text-tertiary hover treatment) as `Chip` itself. */
function ImageAttachmentChip({ attachment }: { attachment: DraftImageAttachment }) {
  const { removeAttachment, retryImageAttachment } = useAppState();
  const isUploading = attachment.uploadState === "pending" || attachment.uploadState === "uploading";
  const isFailed = attachment.uploadState === "failed";

  return (
    <span
      className={cn(
        "inline-flex max-w-56 items-center gap-1.5 rounded-lg border py-1.5 pl-1.5 pr-2.5 text-sm",
        isFailed ? "border-danger/40 bg-danger/5" : "border-subtle bg-surface-raised text-secondary",
      )}
      title={attachment.error}
    >
      <span className="relative h-7 w-7 shrink-0 overflow-hidden rounded-md bg-surface-hover">
        {/* Local preview only — this is the same File the upload sends, never
            re-fetched from the backend (instruction section 17). */}
        <img src={attachment.objectUrl} alt="" className="h-full w-full object-cover" />
        {isUploading && (
          <span className="absolute inset-0 flex items-center justify-center bg-black/45">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-white" aria-hidden="true" />
          </span>
        )}
        {isFailed && (
          <span className="absolute inset-0 flex items-center justify-center bg-black/45">
            <TriangleAlert className="h-3.5 w-3.5 text-white" aria-hidden="true" />
          </span>
        )}
      </span>
      <span className="min-w-0 flex-1 truncate font-medium">{attachment.filename}</span>
      {isFailed && (
        <button
          type="button"
          onClick={() => retryImageAttachment(attachment.id)}
          aria-label={`Retry uploading ${attachment.filename}`}
          className="shrink-0 rounded-full p-0.5 text-tertiary hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <RefreshCw className="h-3 w-3" />
        </button>
      )}
      <button
        type="button"
        onClick={() => removeAttachment(attachment.id)}
        aria-label={`Remove ${attachment.filename}`}
        className="shrink-0 rounded-full p-0.5 text-tertiary hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      >
        <X className="h-3 w-3" />
      </button>
    </span>
  );
}

export function AttachmentChipRow() {
  const { state, removeAttachment } = useAppState();
  const attachments = state.draft.attachments;
  const limitNotice = state.draft.attachmentLimitNotice;

  if (attachments.length === 0 && !limitNotice) return null;

  return (
    <div className="px-1 pb-2">
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {attachments.map((a) =>
            a.kind === "image" ? (
              <ImageAttachmentChip key={a.id} attachment={a} />
            ) : (
              <Chip
                key={a.id}
                icon={a.kind === "folder" ? <FolderClosed className="h-3 w-3" /> : <File className="h-3 w-3" />}
                label={a.name}
                meta={a.meta}
                onRemove={() => removeAttachment(a.id)}
              />
            ),
          )}
        </div>
      )}
      {/* POST-5.1 B3 overflow-UX closure pass — brief, non-blocking; never
          an alert()/modal/persistent error card. Auto-dismisses itself
          (see AppState.tsx's own effect) — this component only renders
          whatever `state.draft.attachmentLimitNotice` currently is. */}
      {limitNotice && (
        <p role="status" className={cn("text-sm text-tertiary", attachments.length > 0 && "mt-1.5")}>
          {limitNotice.message}
        </p>
      )}
    </div>
  );
}
