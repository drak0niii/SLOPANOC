import { useEffect } from "react";
import { useAppState } from "./state/AppState";
import { TooltipProvider } from "./components/ui/Tooltip";
import { AppShell } from "./components/shell/AppShell";
import { DRAFT_HANDOFF_KEY } from "./lib/routes";

/**
 * Picks up any text typed into the landing page's entry prompt (see
 * LandingPromptEntry) and hands it to the real composer's existing draft
 * state via the already-exported setDraftText action — no new state, no
 * changes to AppState.tsx or PromptComposer.tsx. Reads the handoff key once
 * and clears it immediately so it never lingers.
 */
function DraftHandoff() {
  const { setDraftText } = useAppState();

  useEffect(() => {
    const pending = sessionStorage.getItem(DRAFT_HANDOFF_KEY);
    if (!pending) return;
    sessionStorage.removeItem(DRAFT_HANDOFF_KEY);
    setDraftText(pending);

    requestAnimationFrame(() => {
      const composer = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]');
      composer?.focus();
      composer?.setSelectionRange(composer.value.length, composer.value.length);
    });
  }, [setDraftText]);

  return null;
}

/**
 * Extracted into its own module (rather than defined inline in App.tsx) so
 * it can be a React.lazy() boundary — visiting `/` shouldn't pull the whole
 * product application (sidebar, chat, settings, etc.) into the same chunk
 * as the landing page. AppStateProvider itself lives above this in App.tsx
 * so the chat/project/task data it holds survives this component
 * unmounting and remounting as you navigate between "/" and "/app".
 */
export default function ProductApp() {
  return (
    <TooltipProvider>
      <DraftHandoff />
      <AppShell />
    </TooltipProvider>
  );
}
