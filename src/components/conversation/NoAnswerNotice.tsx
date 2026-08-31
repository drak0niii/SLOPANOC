import { useRef } from "react";
import { Info, Paperclip } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { createId } from "../../lib/id";
import { formatBytes } from "../../lib/format";
import type { Attachment } from "../../types";

export function NoAnswerNotice({ searchedScope }: { searchedScope?: string[] }) {
  const { addAttachments } = useAppState();
  const inputRef = useRef<HTMLInputElement>(null);

  function handleFilesSelected(event: React.ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    const attachments: Attachment[] = Array.from(files).map((file) => ({
      id: createId("att"),
      kind: "file",
      name: file.name,
      meta: formatBytes(file.size),
    }));
    addAttachments(attachments);
    event.target.value = "";
  }

  return (
    <div className="anim-fade mt-3">
      {searchedScope && searchedScope.length > 0 && (
        <p className="flex items-center gap-1.5 text-sm text-tertiary">
          <Info className="h-3 w-3 shrink-0" />
          Searched: {searchedScope.join(" · ")}
        </p>
      )}
      <input ref={inputRef} type="file" multiple className="hidden" onChange={handleFilesSelected} />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="mt-2 inline-flex items-center gap-1.5 text-sm font-medium text-accent transition-opacity duration-150 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      >
        <Paperclip className="h-3 w-3" />
        Add a file to this chat
      </button>
    </div>
  );
}
