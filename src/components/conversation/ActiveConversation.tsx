import { useAppState } from "../../state/AppState";
import { MessageList } from "./MessageList";
import { PromptComposer } from "../composer/PromptComposer";

export function ActiveConversation() {
  const { activeMessages } = useAppState();

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <MessageList messages={activeMessages} />
      </div>
      <div className="relative shrink-0 pb-6 pt-1">
        <div className="pointer-events-none absolute inset-x-0 -top-10 h-10 bg-gradient-to-t from-bg to-transparent" />
        <PromptComposer />
      </div>
    </div>
  );
}
