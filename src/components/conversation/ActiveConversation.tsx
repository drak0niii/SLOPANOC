import { useAppState } from "../../state/AppState";
import { MessageList } from "./MessageList";
import { PromptComposer } from "../composer/PromptComposer";
import { Loader2 } from "../ui/icons";

/** Shared by both the loading and error states below — same composer
 * gradient/spacing shell as the normal transcript view, so switching
 * between them never shifts the composer's position on screen. */
function ConversationShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      <div className="relative shrink-0 pb-6 pt-1">
        <div className="pointer-events-none absolute inset-x-0 -top-10 h-10 bg-gradient-to-t from-bg to-transparent" />
        <PromptComposer />
      </div>
    </div>
  );
}

export function ActiveConversation() {
  const { activeChat, activeMessages, retryHistoryLoad } = useAppState();

  // POST-5.1 B4C — a hydrated saved chat's real transcript is fetched
  // lazily (see AppState.tsx's history-load effect); this is what the user
  // sees between selecting it and that fetch resolving.
  if (activeChat?.historyHydrationStatus === "loading") {
    return (
      <ConversationShell>
        <div className="flex h-full items-center justify-center">
          <p role="status" className="flex items-center gap-2 text-sm text-tertiary">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading conversation…
          </p>
        </div>
      </ConversationShell>
    );
  }

  if (activeChat?.historyHydrationStatus === "error") {
    return (
      <ConversationShell>
        <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
          <p role="status" className="text-sm text-tertiary">
            {activeChat.historyError ?? "This conversation could not be loaded. Please try again."}
          </p>
          <button
            type="button"
            onClick={() => retryHistoryLoad(activeChat.id)}
            className="rounded-lg border border-subtle px-3 py-1.5 text-sm font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
          >
            Retry
          </button>
        </div>
      </ConversationShell>
    );
  }

  return (
    <ConversationShell>
      <MessageList messages={activeMessages} />
    </ConversationShell>
  );
}
