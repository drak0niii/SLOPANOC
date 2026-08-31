import { useState } from "react";
import { Mic, Square } from "../ui/icons";
import { Tooltip } from "../ui/Tooltip";
import { cn } from "../../lib/cn";

export function MicrophoneButton() {
  const [recording, setRecording] = useState(false);

  if (recording) {
    return (
      <button
        type="button"
        onClick={() => setRecording(false)}
        className="inline-flex h-9 items-center gap-2 rounded-lg bg-danger/10 px-3 text-base font-medium text-danger transition-colors duration-150 hover:bg-danger/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/50"
      >
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-danger opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-danger" />
        </span>
        Recording…
        <Square className="h-3 w-3 fill-current" />
      </button>
    );
  }

  return (
    <Tooltip label="Voice input">
      <button
        type="button"
        onClick={() => setRecording(true)}
        aria-label="Voice input"
        className={cn(
          "inline-flex h-9 w-9 items-center justify-center rounded-full text-secondary transition-all duration-150",
          "hover:bg-surface-hover hover:text-primary active:scale-90",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
        )}
      >
        <Mic className="h-[18px] w-[18px]" />
      </button>
    </Tooltip>
  );
}
