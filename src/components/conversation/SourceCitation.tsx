import { FileText } from "../ui/icons";
import type { Citation } from "../../types";
import { DrawerContent, DrawerRoot, DrawerTitle, DrawerTrigger } from "../ui/Drawer";

export function SourceCitation({ citation }: { citation: Citation }) {
  return (
    <DrawerRoot>
      <DrawerTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md bg-surface-hover/60 px-1.5 py-0.5 text-xs font-medium text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <FileText className="h-2.5 w-2.5" />
          {citation.docId}
        </button>
      </DrawerTrigger>
      <DrawerContent className="flex flex-col p-5">
        <DrawerTitle>{citation.docTitle}</DrawerTitle>

        <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className="rounded-full bg-success/10 px-2 py-0.5 font-medium text-success">
            Approved
          </span>
          <span className="text-tertiary">{citation.version}</span>
          <span className="text-tertiary">·</span>
          <span className="text-tertiary">{citation.docId}</span>
        </div>

        <p className="mt-1.5 text-sm text-tertiary">{citation.scopeLabel}</p>

        <div className="mt-5 text-sm font-medium text-tertiary">Section {citation.section}</div>

        <div className="mt-4">
          <p className="text-sm font-medium text-tertiary">Relevant excerpt</p>
          <p className="mt-2 border-l-2 border-accent/40 pl-3 text-base italic leading-relaxed text-secondary">
            "{citation.excerpt}"
          </p>
        </div>
      </DrawerContent>
    </DrawerRoot>
  );
}
