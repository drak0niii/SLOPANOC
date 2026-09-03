import { useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { ArrowUp, Square } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { ComposerPlusMenu } from "./ComposerPlusMenu";
import { ThinkingEffortSelector } from "./ThinkingEffortSelector";
import { SkillSelector } from "./SkillSelector";
import { MicrophoneButton } from "./MicrophoneButton";
import { AttachmentChipRow } from "./AttachmentChipRow";
import { SourceChipRow } from "./SourceChipRow";
import { cn } from "../../lib/cn";
import { createId } from "../../lib/id";
import { LONG_PASTE_THRESHOLD } from "../../lib/constants";

const MAX_TEXTAREA_HEIGHT = 240;

export function PromptComposer() {
  const { state, activeChat, activeMessages, setDraftText, addAttachments, sendMessage, stopActiveRun } =
    useAppState();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [nudge, setNudge] = useState(false);
  // composerNudgeAt lives in global state, not on this component — a
  // composer that unmounts and remounts (e.g. this one, after visiting a
  // composer-less view like Scheduled tasks and coming back) would otherwise
  // see whatever stale timestamp is already sitting there on its first
  // effect run and misread it as a brand-new nudge.
  //
  // Comparing against the value captured at first render (rather than a
  // "have I run before?" flag) is what makes this correct under
  // StrictMode: it re-runs mount effects twice, so a flag set on the first
  // pass reads as already-seen on the second and lets the stale timestamp
  // through. A value comparison is idempotent — both passes see it unchanged.
  const lastNudgeRef = useRef(state.composerNudgeAt);

  useEffect(() => {
    if (state.composerNudgeAt === lastNudgeRef.current) return;
    lastNudgeRef.current = state.composerNudgeAt;
    // Force the class off, then back on next frame — re-applying the same
    // class while it's already set (e.g. a second "New chat" click before
    // the first nudge finishes) wouldn't restart the CSS animation, since
    // nothing in the DOM actually changed.
    setNudge(false);
    const raf = requestAnimationFrame(() => setNudge(true));
    const timeout = window.setTimeout(() => setNudge(false), 480);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(timeout);
    };
  }, [state.composerNudgeAt]);

  const lastMessage = activeMessages[activeMessages.length - 1];
  // Phase 4F: covers both the existing mock paths (status "pending") and
  // a real backend run (chat.run present, or the last message actively
  // "streaming") — a second send must be blocked in either case, since
  // the backend would serialize it anyway and silently queueing a
  // submission against one session would be confusing UX.
  const isRunActive =
    Boolean(activeChat?.run) ||
    (lastMessage?.role === "assistant" && (lastMessage.status === "pending" || lastMessage.status === "streaming"));
  // Pre-4H refinement: only a REAL backend run (`chat.run` present) has
  // an actual client transport to abort — a mock/demo-path "pending"
  // message (no `chat.run`) has nothing for Stop to do, so it keeps the
  // existing disabled-Send treatment instead of offering a Stop control
  // that would silently do nothing when clicked.
  const hasAbortableRun = Boolean(activeChat?.run);
  const canSend =
    (state.draft.text.trim().length > 0 ||
      state.draft.attachments.length > 0 ||
      state.draft.sources.length > 0) &&
    !isRunActive;

  function resize() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSend) {
        sendMessage();
        requestAnimationFrame(resize);
      }
    }
  }

  function handleSendClick() {
    if (!canSend) return;
    sendMessage();
    requestAnimationFrame(resize);
  }

  function handleStopClick() {
    if (!activeChat) return;
    stopActiveRun(activeChat.id);
  }

  /** A paste over the threshold becomes a "Pasted text.txt" attachment
   * immediately, rather than dumping a wall of text into the composer —
   * whatever's already typed is left alone. */
  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const pasted = event.clipboardData.getData("text");
    if (pasted.length <= LONG_PASTE_THRESHOLD) return;
    event.preventDefault();
    addAttachments([
      {
        id: createId("attachment"),
        kind: "file",
        name: "Pasted text.txt",
        meta: `${pasted.length.toLocaleString()} characters`,
        isPastedText: true,
        content: pasted,
      },
    ]);
  }

  return (
    <div className="mx-auto w-full max-w-[900px] px-6">
      <div
        data-testid="composer-frame"
        className={cn(
          "rounded-2xl border bg-surface shadow-md shadow-black/5",
          "transition-all duration-150 ease-premium",
          // Resting/focus treatment is driven ONLY by real keyboard/pointer
          // focus (:focus-within) — never by whether a response is
          // streaming, whether text has been typed, or any other state.
          // Previously this also switched to a permanent pulsing glow for
          // the whole duration of a run, making the composer look
          // continuously "active" regardless of real focus — removed.
          "border-subtle/70 focus-within:border-accent/40 focus-within:shadow-[0_0_0_3px_var(--app-focus-glow)]",
          nudge && "anim-nudge border-transparent",
        )}
      >
        <div className="px-3 pt-2">
          <SourceChipRow />
          <AttachmentChipRow />
          <textarea
            ref={textareaRef}
            rows={1}
            value={state.draft.text}
            onChange={(e) => {
              setDraftText(e.target.value);
              resize();
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder="Ask anything"
            aria-label="Message"
            className={cn(
              "max-h-60 w-full resize-none bg-transparent px-1 py-0.5 text-base leading-relaxed text-primary placeholder:text-tertiary focus:outline-none",
              "[&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent",
              "[&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-subtle",
            )}
          />
        </div>

        <div className="flex items-center gap-1 px-2.5 pb-1.5 pt-0.5">
          <ComposerPlusMenu />
          <SkillSelector />
          <div className="ml-auto flex items-center gap-2">
            <ThinkingEffortSelector />
            <MicrophoneButton />
            {hasAbortableRun ? (
              <button
                type="button"
                onClick={handleStopClick}
                aria-label="Stop generating"
                className={cn(
                  "inline-flex h-9 w-9 items-center justify-center rounded-full border transition-all duration-150 ease-premium",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-1 focus-visible:ring-offset-surface",
                  "border-transparent bg-accent text-on-accent shadow-sm hover:opacity-90 active:scale-95",
                )}
              >
                <Square className="h-[13px] w-[13px] fill-current" aria-hidden="true" />
              </button>
            ) : (
              <button
                type="button"
                onClick={handleSendClick}
                disabled={!canSend}
                aria-label={isRunActive ? "Waiting for the assistant to finish responding" : "Send message"}
                className={cn(
                  "inline-flex h-9 w-9 items-center justify-center rounded-full border transition-all duration-150 ease-premium",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-1 focus-visible:ring-offset-surface",
                  canSend
                    ? "border-transparent bg-accent text-on-accent shadow-sm hover:opacity-90 active:scale-95"
                    : "border-subtle text-tertiary",
                )}
              >
                <ArrowUp className="h-[18px] w-[18px]" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
