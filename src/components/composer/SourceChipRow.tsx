import { MessagesSquare } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { SOURCE_KIND_BY_ID } from "../../data/workspaceSources";
import { Chip } from "../ui/Chip";

/** Chat rooms and folders attached to the next message, shown beside file
 * attachments so both read as "things this message carries". */
export function SourceChipRow() {
  const { state, removeDraftSource } = useAppState();
  const sources = state.draft.sources;

  if (sources.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5 px-1 pb-2">
      {sources.map((source) => (
        <Chip
          key={source.id}
          tone="accent"
          icon={<MessagesSquare className="h-3 w-3" />}
          label={source.scope}
          meta={SOURCE_KIND_BY_ID[source.kind]?.shortLabel}
          onRemove={() => removeDraftSource(source.id)}
        />
      ))}
    </div>
  );
}
