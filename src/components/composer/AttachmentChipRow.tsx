import { File, FolderClosed } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { Chip } from "../ui/Chip";

export function AttachmentChipRow() {
  const { state, removeAttachment } = useAppState();
  const attachments = state.draft.attachments;

  if (attachments.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5 px-1 pb-2">
      {attachments.map((a) => (
        <Chip
          key={a.id}
          icon={a.kind === "folder" ? <FolderClosed className="h-3 w-3" /> : <File className="h-3 w-3" />}
          label={a.name}
          meta={a.meta}
          onRemove={() => removeAttachment(a.id)}
        />
      ))}
    </div>
  );
}
