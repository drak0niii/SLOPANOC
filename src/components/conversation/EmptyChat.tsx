import { useMemo } from "react";
import { PromptComposer } from "../composer/PromptComposer";
import { currentGreeting } from "../../lib/greeting";

export function EmptyChat() {
  // Resolved once per mount: the hour will not change while someone is
  // looking at an empty composer, and recomputing on every render would let
  // the greeting flip mid-session at a period boundary.
  const greeting = useMemo(() => currentGreeting(), []);

  return (
    <div className="anim-fade flex h-full flex-col items-center justify-center px-6">
      <div className="w-full">
        <p className="mx-auto mb-7 w-full max-w-[900px] px-6 text-center text-3xl font-semibold text-primary">
          {greeting}
        </p>
        <PromptComposer />
      </div>
    </div>
  );
}
