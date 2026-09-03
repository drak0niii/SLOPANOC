import type { Citation } from "../../types";
import { SourceChip } from "./SourceChip";

/** MOP/document citation chip — a thin wrapper around the shared
 * `SourceChip` (pre-4H UX/provenance milestone unified the source drawer
 * across MOP documents and Teams conversations; see that component's own
 * module docstring). Kept as its own named export/file so every existing
 * call site (`Message.tsx`'s `message.citations` row) is unchanged. */
export function SourceCitation({ citation }: { citation: Citation }) {
  return <SourceChip kind="mop" citation={citation} />;
}
