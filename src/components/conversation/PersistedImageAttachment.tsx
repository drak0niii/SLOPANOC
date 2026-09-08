import { useEffect, useRef, useState } from "react";
import { Image, ImageOff } from "../ui/icons";
import { getAttachmentContent } from "../../api/attachments";
import { ACCEPTED_IMAGE_MIME_TYPES } from "../../lib/constants";
import { cn } from "../../lib/cn";
import type { PersistedAttachmentReference } from "../../types";

type LoadState = "loading" | "loaded" | "error" | "unsupported";

function isAcceptedImageMime(mimeType: string): boolean {
  return (ACCEPTED_IMAGE_MIME_TYPES as readonly string[]).includes(mimeType);
}

const PLACEHOLDER_CLASS =
  "flex h-24 w-24 shrink-0 flex-col items-center justify-center gap-1 rounded-xl border border-subtle/60 bg-surface-hover px-2 text-center";

/**
 * POST-5.1 B4D — renders ONE durable, server-owned image reference
 * (`PersistedAttachmentReference`) inside a rehydrated saved-chat user
 * message. Fully self-contained: no AppState dependency, no global binary
 * cache, no localStorage/sessionStorage/IndexedDB. On mount, fetches the
 * real bytes through the authenticated `GET /api/attachments/{id}/content`
 * route (via `getAttachmentContent`), turns the successful `Blob` into a
 * transient `URL.createObjectURL` for `<img>` to render, and revokes that
 * URL the moment it's no longer needed (reference change, retry, or
 * unmount) — the object URL is never stored anywhere durable, never
 * serialized, and is not itself the source of truth (that remains private
 * GCS + the Cloud SQL attachment row this reference points at).
 */
export function PersistedImageAttachment({ reference }: { reference: PersistedAttachmentReference }) {
  const supported = isAcceptedImageMime(reference.mimeType);
  const [state, setState] = useState<LoadState>(supported ? "loading" : "unsupported");
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [retryToken, setRetryToken] = useState(0);
  const objectUrlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!supported) {
      setState("unsupported");
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    setState("loading");

    void (async () => {
      let blob: Blob;
      try {
        blob = await getAttachmentContent(reference.attachmentId, controller.signal);
      } catch {
        // Covers both a genuine backend failure (unknown/foreign-owner
        // attachment, storage inconsistency -- both already collapsed by
        // the backend into the same generic SafeError) AND this specific
        // request having been aborted because a newer reference/retry
        // superseded it. Either way, a cancelled/superseded attempt must
        // never flash an error for content nobody is waiting on anymore.
        if (!cancelled) setState("error");
        return;
      }
      // A request that resolved AFTER this effect was superseded (reference
      // changed, component unmounted, or a retry started a newer request)
      // must never install a stale image over the new one -- bail out
      // BEFORE creating any object URL, so there is nothing to revoke here.
      if (cancelled) return;

      // Defensive content-type check (instruction: "at minimum ensure the
      // fetched binary is one of the accepted image types") -- the
      // backend's own Content-Type is authoritative when present; a
      // disagreement with this reference's own recorded mimeType is a
      // backend/data inconsistency that must fail safely, never render
      // something the reference never claimed to be.
      const effectiveType = blob.type || reference.mimeType;
      if (!isAcceptedImageMime(effectiveType)) {
        setState("error");
        return;
      }

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
  }, [reference.attachmentId, reference.mimeType, retryToken, supported]);

  // True unmount safety net — revokes whatever is still outstanding at
  // that point, mirroring AppState.tsx's own draft-image object-URL
  // cleanup pattern. In normal operation the effect above already revokes
  // a superseded URL as soon as a newer one replaces it; this only
  // matters for the LAST object URL a mounted instance ever held.
  useEffect(() => {
    return () => {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    };
  }, []);

  function retry() {
    setState("loading");
    setRetryToken((token) => token + 1);
  }

  if (state === "unsupported" || state === "error") {
    return (
      <div className={PLACEHOLDER_CLASS}>
        <ImageOff className="h-4 w-4 text-tertiary" aria-hidden="true" />
        <span className="w-full truncate text-[11px] text-tertiary">{reference.filename}</span>
        <span role="status" className="text-[11px] text-tertiary">
          {state === "unsupported" ? "Unsupported format" : "Image unavailable"}
        </span>
        {state === "error" && (
          <button
            type="button"
            onClick={retry}
            className="rounded text-[11px] font-medium text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
          >
            Retry
          </button>
        )}
      </div>
    );
  }

  if (state === "loading") {
    return (
      <div className={PLACEHOLDER_CLASS}>
        <Image className="h-4 w-4 text-tertiary" aria-hidden="true" />
        <span className="w-full truncate text-[11px] text-tertiary">{reference.filename}</span>
        <span role="status" className="text-[11px] text-tertiary">
          Loading image…
        </span>
      </div>
    );
  }

  return (
    <img
      src={objectUrl ?? undefined}
      alt={`Attached image: ${reference.filename}`}
      className={cn("max-h-64 max-w-full rounded-xl border border-subtle/60 object-contain")}
    />
  );
}
