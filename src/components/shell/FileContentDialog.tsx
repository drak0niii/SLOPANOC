import { useEffect, useState } from "react";
import { Trash2 } from "../ui/icons";
import type { ProjectFile } from "../../types";
import { DialogContent, DialogRoot, DialogTitle } from "../ui/Dialog";

export function FileContentDialog({
  file,
  open,
  onOpenChange,
  onRemove,
}: {
  file: ProjectFile | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRemove: () => void;
}) {
  // The caller clears `file` to null in the same tick it closes the dialog,
  // which would otherwise unmount this component (and its content) before
  // Radix's own closing animation gets a chance to play. Keep showing the
  // last file while the dialog animates out.
  const [lastFile, setLastFile] = useState(file);
  useEffect(() => {
    if (file) setLastFile(file);
  }, [file]);
  const displayFile = file ?? lastFile;

  if (!displayFile) return null;
  const lines = displayFile.content.split("\n");

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[70vh] w-full max-w-2xl flex-col overflow-hidden p-0">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-subtle/50 px-5 py-4 pr-14">
          <DialogTitle className="truncate">{displayFile.name}</DialogTitle>
          <button
            type="button"
            onClick={() => {
              onRemove();
              onOpenChange(false);
            }}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium text-danger transition-colors duration-150 hover:bg-danger/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Remove file
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto bg-surface font-mono text-sm leading-relaxed">
          <table className="w-full border-collapse">
            <tbody>
              {lines.map((line, i) => (
                <tr key={i}>
                  <td className="select-none whitespace-nowrap px-3 py-0 text-right align-top text-tertiary/60">
                    {i + 1}
                  </td>
                  <td className="w-full whitespace-pre-wrap break-all px-3 py-0 align-top text-secondary">
                    {line}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </DialogContent>
    </DialogRoot>
  );
}
