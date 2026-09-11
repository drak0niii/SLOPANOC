import { useEffect, useRef, useState } from "react";
import { Image, ImageOff, Maximize2 } from "../ui/icons";
import { getSourceImageContent } from "../../api/sourceImages";
import { cn } from "../../lib/cn";
import { DialogContent, DialogRoot, DialogTitle, DialogTrigger } from "../ui/Dialog";
import { formatEvidenceTimestamp } from "../../lib/sourceReference";
import type { SourceVisualEvidenceItemDTO } from "../../api/types";

type LoadState = "loading" | "loaded" | "error";

const THUMBNAIL_PLACEHOLDER_CLASS =
  "flex h-20 w-20 shrink-0 flex-col items-center justify-center gap-1 rounded-xl border border-subtle/60 bg-surface-hover px-2 text-center";

/**
 * Teams Visual Evidence milestone — ONE lazily-fetched, authenticated
 * Source image (`GET /api/sessions/{sessionId}/sources/{sourceId}/images/
 * {imageId}`, via `getSourceImageContent`). Mirrors `PersistedImage
 * Attachment.tsx`'s already-proven lifecycle exactly: fetch-on-mount ->
 * `Blob` -> `URL.createObjectURL` -> `<img>`, with correct
 * `AbortController` cancellation and object-URL revocation on
 * unmount/replacement, loading/error states, and a retry affordance for a
 * transient failure. Never persisted to localStorage/sessionStorage/
 * IndexedDB — the object URL is transient, recreated on every mount.
 *
 * Clicking the thumbnail opens the SAME already-fetched `objectUrl` in a
 * full preview Dialog — never a second fetch (mirrors `PersistedImage
 * Attachment`'s own "reuse, never refetch" click-to-preview contract).
 */
function VisualEvidenceThumbnail({
  sessionId,
  sourceId,
  item,
  total,
  conversationTitle,
}: {
  sessionId: string;
  sourceId: string;
  item: SourceVisualEvidenceItemDTO;
  total: number;
  conversationTitle: string | null;
}) {
  const [state, setState] = useState<LoadState>("loading");
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [retryToken, setRetryToken] = useState(0);
  const objectUrlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setState("loading");

    void (async () => {
      let blob: Blob;
      try {
        blob = await getSourceImageContent(sessionId, sourceId, item.image_id, controller.signal);
      } catch {
        // Covers both a genuine backend failure (the underlying Teams
        // hosted content has since become unavailable — section 13's own
        // "keep the Source metadata, render 'Image unavailable', never
        // remove/fabricate the source" requirement) AND a
        // cancelled/superseded request — either way, never flash an error
        // for content nobody is waiting on anymore.
        if (!cancelled) setState("error");
        return;
      }
      if (cancelled) return;

      const url = URL.createObjectURL(blob);
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = url;
      setObjectUrl(url);
      setState("loaded");
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [sessionId, sourceId, item.image_id, retryToken]);

  useEffect(() => {
    return () => {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    };
  }, []);

  function retry() {
    setState("loading");
    setRetryToken((token) => token + 1);
  }

  const label = `Image ${item.ordinal}`;

  if (state === "error") {
    return (
      <div className={THUMBNAIL_PLACEHOLDER_CLASS}>
        <ImageOff className="h-4 w-4 text-tertiary" aria-hidden="true" />
        <span role="status" className="text-[11px] text-tertiary">
          Image unavailable
        </span>
        <button
          type="button"
          onClick={retry}
          className="rounded text-[11px] font-medium text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          Retry
        </button>
      </div>
    );
  }

  if (state === "loading") {
    return (
      <div className={THUMBNAIL_PLACEHOLDER_CLASS}>
        <Image className="h-4 w-4 text-tertiary" aria-hidden="true" />
        <span role="status" className="text-[11px] text-tertiary">
          Loading…
        </span>
      </div>
    );
  }

  const alt = `${label}: Teams visual evidence`;

  return (
    <div className="flex flex-col items-center gap-1">
      <DialogRoot>
        <DialogTrigger asChild>
          <button
            type="button"
            aria-label={`Open larger preview of ${label}`}
            className={cn(
              "group relative block h-20 w-20 shrink-0 overflow-hidden rounded-xl border border-subtle/60",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
            )}
          >
            <img src={objectUrl ?? undefined} alt={alt} className="h-full w-full object-cover" />
            <span
              aria-hidden="true"
              className={cn(
                "pointer-events-none absolute inset-0 flex items-center justify-center bg-inverse/0 opacity-0",
                "transition-opacity duration-150 group-hover:bg-inverse/25 group-hover:opacity-100",
                "group-focus-visible:bg-inverse/25 group-focus-visible:opacity-100",
              )}
            >
              <Maximize2 className="h-4 w-4 text-white drop-shadow" />
            </span>
          </button>
        </DialogTrigger>
        <DialogContent className="flex max-h-[90vh] max-w-[90vw] flex-col gap-2 p-4">
          <DialogTitle className="sr-only">{label}</DialogTitle>
          <img src={objectUrl ?? undefined} alt={alt} className="max-h-[75vh] max-w-[85vw] rounded-lg object-contain" />
          <div className="text-sm text-tertiary">
            <p className="font-medium text-secondary">
              {label} of {total}
            </p>
            <p>
              {item.author} · {formatEvidenceTimestamp(item.sent_at)}
              {conversationTitle ? ` · ${conversationTitle}` : ""}
            </p>
          </div>
        </DialogContent>
      </DialogRoot>
      <span className="text-[11px] text-tertiary">{label}</span>
    </div>
  );
}

/**
 * Teams Visual Evidence milestone — the Source drawer's "VISUAL EVIDENCE"
 * section (rendered after Contributors, before the explanatory footer —
 * see SourceChip.tsx's own `TeamsSourceDetails`). Renders nothing at all
 * for an empty `items` list — an answer that never actually attached a
 * Teams image to Gemini shows no such section, exactly as before this
 * milestone (never a placeholder/empty-state block).
 */
export function VisualEvidenceSection({
  sessionId,
  sourceId,
  items,
  conversationTitle,
}: {
  sessionId: string;
  sourceId: string;
  items: SourceVisualEvidenceItemDTO[];
  conversationTitle: string | null;
}) {
  if (items.length === 0) return null;

  return (
    <div className="mt-5">
      <p className="text-sm font-medium text-tertiary">
        Visual evidence · {items.length} {items.length === 1 ? "image" : "images"} analyzed
      </p>
      <div className="mt-2 flex flex-wrap gap-3">
        {items.map((item) => (
          <VisualEvidenceThumbnail
            key={item.image_id}
            sessionId={sessionId}
            sourceId={sourceId}
            item={item}
            total={items.length}
            conversationTitle={conversationTitle}
          />
        ))}
      </div>
    </div>
  );
}
