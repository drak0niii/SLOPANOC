import { Info } from "../ui/icons";

/** POST-5.1 B3 — this notice is reachable only from the mock/demo response
 * paths (`groundingResult === "insufficient"` is set exclusively by
 * `dispatchMockResponse`/`COMPLETE_ASSISTANT_MESSAGE`, never by a real
 * backend `message.completed` event — confirmed by reading every call site
 * that sets it). It previously offered its own "Add a file" affordance that
 * discarded the picked `File` into a metadata-only mock `Attachment`, a
 * second, stale attachment entry point separate from the real one now wired
 * through `ComposerPlusMenu`/`queueImageFiles`. Per instruction ("there must
 * be ONE attachment implementation, not one real path and one stale mock
 * path"), that affordance is removed here rather than wired to the real
 * pipeline — this notice only ever appears on a mock/demo chat, which has no
 * backend session for a real upload to attach to. */
export function NoAnswerNotice({ searchedScope }: { searchedScope?: string[] }) {
  if (!searchedScope || searchedScope.length === 0) return null;

  return (
    <div className="anim-fade mt-3">
      <p className="flex items-center gap-1.5 text-sm text-tertiary">
        <Info className="h-3 w-3 shrink-0" />
        Searched: {searchedScope.join(" · ")}
      </p>
    </div>
  );
}
