import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Attachment, Message as MessageType } from "../../types";
import {
  Check,
  Copy,
  File,
  FolderClosed,
  Maximize2,
  MessagesSquare,
  Pencil,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
} from "../ui/icons";
import { Chip } from "../ui/Chip";
import { ScrollingText } from "../ui/ScrollingText";
import { SOURCE_KIND_BY_ID } from "../../data/workspaceSources";
import { SourceCitation } from "./SourceCitation";
import { SourceChip } from "./SourceChip";
import { groupKnowledgeSourceReferences } from "../../lib/sourceReference";
import { ActionProposalCard } from "./ActionProposalCard";
import { ConnectorUnavailableCard } from "./ConnectorUnavailableCard";
import { ConnectorSuggestionsCard } from "./ConnectorSuggestionsCard";
import { DistributeMenu } from "./DistributeMenu";
import { NoAnswerNotice } from "./NoAnswerNotice";
import { RunTrace } from "./RunTrace";
import { ApprovalCard } from "./ApprovalCard";
import { SelectionCard } from "./SelectionCard";
import { MessageMarkdown } from "./MessageMarkdown";
import { PersistedImageAttachment } from "./PersistedImageAttachment";
import { cn } from "../../lib/cn";
import { hasPersistedImageAttachment } from "../../lib/persistedAttachments";
import { useAppState } from "../../state/AppState";
import { Tooltip } from "../ui/Tooltip";
import { formatFullTimestamp, formatShortDate } from "../../lib/format";

/** Neutral "working on it" indicator — three plain animated dots, no text.
 * Used both by the existing mock paths (pending → complete, unchanged)
 * and, for a real backend run, in the brief window before its first
 * `status` event has arrived (see Message()'s render branch below) —
 * deliberately never a hardcoded string like "Thinking…" in either case;
 * once the backend supplies a real activity label, CurrentActivity takes
 * over instead. */
function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-1.5 py-1">
      <span className="flex gap-1">
        <span className="anim-breathe-dot h-1.5 w-1.5 rounded-full bg-tertiary [animation-delay:0ms]" />
        <span className="anim-breathe-dot h-1.5 w-1.5 rounded-full bg-tertiary [animation-delay:200ms]" />
        <span className="anim-breathe-dot h-1.5 w-1.5 rounded-full bg-tertiary [animation-delay:400ms]" />
      </span>
    </div>
  );
}

/** Inline, safe error state for a failed real backend run (Phase 4F) —
 * `text` is already backend-sanitized (or a generic transport-failure
 * string), never a raw exception/stack trace. Renders above/alongside
 * whatever partial text had already streamed in before the failure. */
function AssistantErrorNotice({ text }: { text: string }) {
  return (
    <p role="status" className="anim-fade mt-2 text-sm text-tertiary">
      {text || "Something went wrong. Please try again."}
    </p>
  );
}

function AttachmentCard({ attachment }: { attachment: Attachment }) {
  return (
    <div className="flex items-center gap-2.5 rounded-xl border border-subtle/60 bg-surface-raised px-3 py-2">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-hover text-secondary">
        {attachment.kind === "folder" ? (
          <FolderClosed className="h-3.5 w-3.5" />
        ) : (
          <File className="h-3.5 w-3.5" />
        )}
      </span>
      <div className="flex min-w-0 flex-col leading-tight">
        <ScrollingText className="max-w-[11rem] text-sm font-medium text-primary">
          {attachment.name}
        </ScrollingText>
        {attachment.meta && <span className="text-xs text-tertiary">{attachment.meta}</span>}
      </div>
    </div>
  );
}

/**
 * Demo/presentation block tags. A fenced fence body can start with one of
 * these tags on its own line (```check, ```output, ...) to render as a
 * discreet, labeled command/output/diagnostic block instead of plain prose.
 * An untagged fence still renders as a generic neutral monospace block, and
 * any message that doesn't use fences at all is completely unaffected.
 */
type BlockTag = "check" | "action" | "output" | "finding" | "success" | "automation" | "cta";

const BLOCK_LABELS: Record<Exclude<BlockTag, "cta">, string> = {
  check: "CHECK",
  action: "ACTION",
  output: "OUTPUT",
  finding: "FINDING",
  success: "RESOLVED",
  automation: "AUTOMATION",
};

interface TextBlock {
  type: "p" | "fence";
  tag: BlockTag | null;
  content: string;
}

function parseTextBlocks(text: string): TextBlock[] {
  const segments = text.split("```");
  const blocks: TextBlock[] = [];
  segments.forEach((segment, i) => {
    if (i % 2 === 1) {
      const match = segment.match(/^([a-zA-Z-]*)\n([\s\S]*)$/);
      const rawTag = match ? match[1].toLowerCase() : "";
      const tag = (
        ["check", "action", "output", "finding", "success", "automation", "cta"] as string[]
      ).includes(rawTag)
        ? (rawTag as BlockTag)
        : null;
      const content = (match ? match[2] : segment).replace(/\n$/, "");
      if (content.trim().length > 0) blocks.push({ type: "fence", tag, content });
    } else {
      segment.split(/\n\n+/).forEach((para) => {
        if (para.trim().length > 0) blocks.push({ type: "p", tag: null, content: para });
      });
    }
  });
  return blocks;
}

function CommandBlock({ label, content }: { label: string; content: string }) {
  return (
    <div className="rounded-lg border border-info/30 bg-info/[0.06] px-3.5 py-3">
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-info">{label}</div>
      <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-sm leading-relaxed text-primary">
        {content}
      </pre>
    </div>
  );
}

function OutputBlock({ content }: { content: string }) {
  return (
    <div className="rounded-lg border border-subtle/60 bg-surface-hover/60 px-3.5 py-3">
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-tertiary">
        {BLOCK_LABELS.output}
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-sm leading-relaxed text-secondary">
        {content}
      </pre>
    </div>
  );
}

const ACCENT_TONE = {
  finding: { border: "border-warning/60", label: "text-warning" },
  success: { border: "border-success/60", label: "text-success" },
  automation: { border: "border-automation/60", label: "text-automation" },
} as const;

function AccentLine({
  label,
  content,
  tone,
}: {
  label: string;
  content: string;
  tone: keyof typeof ACCENT_TONE;
}) {
  const colors = ACCENT_TONE[tone];
  return (
    <div className={cn("border-l-2 py-0.5 pl-3", colors.border)}>
      <div className={cn("text-[11px] font-semibold uppercase tracking-wide", colors.label)}>{label}</div>
      <p className="whitespace-pre-wrap text-base leading-relaxed text-primary">{content}</p>
    </div>
  );
}

/**
 * A demo quick-reply CTA. Content is authored as "Label|message to insert";
 * clicking it inserts that message as a normal user turn and advances the
 * chat's demoRun exactly like manually typing the same text would — same
 * demo-state logic, no separate execution path. Only clickable while its
 * own message is still the latest one in the chat; once the conversation
 * has moved on it settles into a resolved, inert state automatically.
 */
function QuickReplyPill({ messageId, content }: { messageId: string; content: string }) {
  const { activeChat, sendMessage } = useAppState();
  const [label, insertTextRaw] = content.split("|");
  const insertText = (insertTextRaw ?? label).trim();
  const isLatest = activeChat ? activeChat.messageIds[activeChat.messageIds.length - 1] === messageId : false;

  return (
    <div>
      <button
        type="button"
        disabled={!isLatest}
        onClick={() => sendMessage(insertText)}
        className={cn(
          "inline-flex items-center rounded-full border px-3 py-1 text-sm font-medium transition-colors duration-150",
          "border-accent/30 bg-accent/5 text-accent",
          isLatest
            ? "cursor-pointer hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            : "cursor-default opacity-50",
        )}
      >
        {label.trim()}
      </button>
    </div>
  );
}

function MessageBody({
  text,
  proseClassName,
  messageId,
}: {
  text: string;
  proseClassName: string;
  messageId: string;
}) {
  return (
    <>
      {parseTextBlocks(text).map((block, i) => {
        if (block.type === "p") {
          return (
            <p key={i} className={proseClassName}>
              {block.content}
            </p>
          );
        }

        switch (block.tag) {
          case "check":
          case "action":
            return <CommandBlock key={i} label={BLOCK_LABELS[block.tag]} content={block.content} />;
          case "output":
            return <OutputBlock key={i} content={block.content} />;
          case "finding":
            return <AccentLine key={i} label={BLOCK_LABELS.finding} content={block.content} tone="finding" />;
          case "success":
            return <AccentLine key={i} label={BLOCK_LABELS.success} content={block.content} tone="success" />;
          case "automation":
            return (
              <AccentLine key={i} label={BLOCK_LABELS.automation} content={block.content} tone="automation" />
            );
          case "cta":
            return <QuickReplyPill key={i} messageId={messageId} content={block.content} />;
          default:
            return (
              <pre
                key={i}
                className="overflow-x-auto rounded-lg border border-subtle/60 bg-surface-hover/60 px-3.5 py-3 font-mono text-sm leading-relaxed text-secondary"
              >
                {block.content}
              </pre>
            );
        }
      })}
    </>
  );
}

/** Brief "copied" affordance shared by the Copy and Share actions — both are
 * "put something on the clipboard" gestures that just copy different text. */
function useClipboardFeedback(): [boolean, (value: string) => void] {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<number | undefined>(undefined);

  function copy(value: string) {
    navigator.clipboard?.writeText(value).catch(() => {});
    setCopied(true);
    if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => setCopied(false), 1600);
  }

  useEffect(() => () => window.clearTimeout(timeoutRef.current), []);

  return [copied, copy];
}

function ActionIconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip label={label}>
      <button
        type="button"
        aria-label={label}
        onClick={onClick}
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      >
        {children}
      </button>
    </Tooltip>
  );
}

function CopyMessageButton({ text }: { text: string }) {
  const [copied, copy] = useClipboardFeedback();
  return (
    <ActionIconButton label={copied ? "Copied" : "Copy"} onClick={() => copy(text)}>
      {copied ? (
        <Check key="check" className="anim-check-in h-3.5 w-3.5 text-success" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
    </ActionIconButton>
  );
}

const MESSAGE_ACTIONS_ROW_CLASS = cn(
  "flex items-center gap-0.5 opacity-60 transition-opacity duration-150",
  // Touch/coarse-pointer devices have no real hover state, so the row stays
  // visible (subdued) there by default. Only on devices with true hover does
  // it start hidden and reveal on hover/focus — keyboard access via
  // focus-within always works either way, and buttons stay in the a11y tree.
  "[@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-within:opacity-100 [@media(hover:hover)]:group-hover:opacity-100",
);

/** A message's stored text is the typed text and any pasted-attachment
 * content joined together (see AppState's sendMessage). Recover just the
 * typed portion for display, so text typed alongside a long paste (e.g.
 * "See attached:" before pasting a big block) still shows in the bubble
 * instead of disappearing behind the attachment chip. */
function getVisibleUserText(message: MessageType): string {
  const pastedAttachments = message.attachments?.filter((a) => a.isPastedText) ?? [];
  if (pastedAttachments.length === 0) return message.text;

  const pastedContent = pastedAttachments.map((a) => a.content ?? "").join("\n\n");
  if (!pastedContent) return message.text;
  if (message.text === pastedContent) return "";
  const suffix = `\n\n${pastedContent}`;
  return message.text.endsWith(suffix) ? message.text.slice(0, -suffix.length) : message.text;
}

/** Loads a message's full text back into the composer — the shared mechanic
 * behind both "Edit" (plain text messages) and "Expand message" (messages
 * whose text is hidden behind a pasted-text attachment). */
function loadTextIntoComposer(setDraftText: (text: string) => void, text: string) {
  setDraftText(text);
  requestAnimationFrame(() => {
    const composer = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]');
    composer?.focus();
    composer?.setSelectionRange(composer.value.length, composer.value.length);
  });
}

function UserMessageActions({
  message,
  onEdit,
}: {
  message: MessageType;
  onEdit: () => void;
}) {
  const { state, setDraftText } = useAppState();
  const hasAttachments = Boolean(message.attachments && message.attachments.length > 0);
  const pastedTextAttachment = message.attachments?.find((a) => a.isPastedText);

  // POST-5.1 B4D correction pass — LOCKED PRODUCT RULE: no Edit affordance
  // at all for a user turn that owns a durable, server-linked image
  // (see src/lib/persistedAttachments.ts). This is the UI-level
  // reflection only — the real enforcement boundary is AppState.tsx's
  // `editMessage` hard guard below.
  if (hasPersistedImageAttachment(message)) return null;

  if (hasAttachments) {
    if (!pastedTextAttachment) return null;
    return (
      <div className="mt-1.5 flex justify-end">
        <button
          type="button"
          onClick={() => {
            // Loading a message's text into the composer replaces whatever's
            // already being typed there — confirm first so an in-progress
            // draft is never silently discarded.
            const hasUnsavedDraft =
              state.draft.text.trim().length > 0 || state.draft.attachments.length > 0;
            if (hasUnsavedDraft && !window.confirm("Replace what you're currently typing with this message?")) {
              return;
            }
            loadTextIntoComposer(setDraftText, message.text);
          }}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <Maximize2 className="h-3.5 w-3.5" />
          Expand message
        </button>
      </div>
    );
  }

  return (
    <div className={cn(MESSAGE_ACTIONS_ROW_CLASS, "mt-1.5 justify-end")}>
      <ActionIconButton label="Edit" onClick={onEdit}>
        <Pencil className="h-3.5 w-3.5" />
      </ActionIconButton>
    </div>
  );
}

/** Edits happen in place, in the same bubble, rather than round-tripping
 * through the composer — Send commits the new text and discards everything
 * that came after this message, so the conversation continues from here. */
function EditMessageBox({ message, onCancel }: { message: MessageType; onCancel: () => void }) {
  const { editMessage } = useAppState();
  const [value, setValue] = useState(message.text);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function resize() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    resize();
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);

  function handleSend() {
    const trimmed = value.trim();
    if (!trimmed) return;
    editMessage(message.id, trimmed);
    onCancel();
  }

  return (
    <div className="w-full max-w-[720px] rounded-2xl bg-surface-raised px-4 py-3">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => {
          setValue(event.target.value);
          resize();
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            handleSend();
          }
          if (event.key === "Escape") onCancel();
        }}
        rows={1}
        aria-label="Edit message"
        className="max-h-60 w-full resize-none bg-transparent text-base leading-relaxed text-primary placeholder:text-tertiary focus:outline-none"
      />
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex h-9 items-center rounded-full bg-surface-hover px-4 text-sm font-medium text-secondary transition-colors duration-150 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSend}
          disabled={!value.trim()}
          className="inline-flex h-9 items-center rounded-full bg-accent px-4 text-sm font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:opacity-40"
        >
          Send
        </button>
      </div>
    </div>
  );
}

/** Good/bad response feedback — purely local and mutually exclusive (picking
 * one clears the other, picking the active one again clears it), with no
 * backend to submit to in this prototype. */
function ResponseFeedbackButtons() {
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);

  function toggle(next: "up" | "down") {
    setFeedback((current) => (current === next ? null : next));
  }

  return (
    <>
      <ActionIconButton label="Good response" onClick={() => toggle("up")}>
        <ThumbsUp className={cn("h-3.5 w-3.5", feedback === "up" && "fill-current text-primary")} />
      </ActionIconButton>
      <ActionIconButton label="Bad response" onClick={() => toggle("down")}>
        <ThumbsDown className={cn("h-3.5 w-3.5", feedback === "down" && "fill-current text-primary")} />
      </ActionIconButton>
    </>
  );
}

function AssistantMessageActions({
  message,
  isBackendMessage,
}: {
  message: MessageType;
  isBackendMessage: boolean;
}) {
  const { regenerateMessage } = useAppState();
  return (
    <div className={cn(MESSAGE_ACTIONS_ROW_CLASS, "-ml-1.5 mt-2")}>
      <CopyMessageButton text={message.text} />
      <ResponseFeedbackButtons />
      {/* Phase 4F: no regenerate-turn endpoint exists on the real backend
       * yet (see AppState.tsx's regenerateMessage guard) — hidden rather
       * than left to silently do nothing. */}
      {!isBackendMessage && (
        <ActionIconButton label="Regenerate" onClick={() => regenerateMessage(message.id)}>
          <RefreshCw className="h-3.5 w-3.5" />
        </ActionIconButton>
      )}
      <DistributeMenu messageId={message.id} />
      <Tooltip label={formatFullTimestamp(message.createdAt)}>
        <span className="ml-1 cursor-default text-xs text-tertiary">{formatShortDate(message.createdAt)}</span>
      </Tooltip>
    </div>
  );
}

function SpeakerLabel({ label }: { label: string }) {
  return <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-tertiary">{label}</p>;
}

function useProgressiveReveal(message: MessageType) {
  const [revealed, setRevealed] = useState(message.text);
  const [done, setDone] = useState(true);
  const prevStatusRef = useRef(message.status);
  const hasAnimatedRef = useRef(false);

  useEffect(() => {
    const prevStatus = prevStatusRef.current;
    prevStatusRef.current = message.status;

    // Regenerating flips a completed message back to "pending" before its
    // new text arrives — allow the reveal to play again for that new text,
    // same as it did the first time.
    if (prevStatus === "complete" && message.status === "pending") {
      hasAnimatedRef.current = false;
    }

    if (prevStatus === "pending" && message.status === "complete" && !hasAnimatedRef.current) {
      hasAnimatedRef.current = true;
      const words = message.text.split(" ");
      setRevealed("");
      setDone(false);
      let i = 0;
      const interval = window.setInterval(() => {
        i += 2;
        setRevealed(words.slice(0, i).join(" "));
        if (i >= words.length) {
          window.clearInterval(interval);
          setDone(true);
        }
      }, 20);
      return () => window.clearInterval(interval);
    }

    setRevealed(message.text);
    setDone(true);
  }, [message.status, message.text]);

  return { revealed, done };
}

export function Message({ message }: { message: MessageType }) {
  const isUser = message.role === "user";
  const { revealed, done } = useProgressiveReveal(message);
  const [isEditing, setIsEditing] = useState(false);
  const { activeChat, toggleRunTraceExpanded } = useAppState();
  const isBackendMessage = Boolean(activeChat?.backendSessionId);
  // Only the message a real run is actively targeting shows the backend's
  // live activity label — a different (e.g. older) pending message never
  // borrows it.
  const isRunTarget = activeChat?.run?.assistantMessageId === message.id;
  const currentActivityLabel = isRunTarget ? (activeChat!.run!.currentActivity?.label ?? null) : null;
  // Expandable, sanitized run trace (pre-4H milestone) — permanently
  // owned by this message once a real backend run has ever targeted it
  // (see AppState.tsx's ownership model, mirroring actionCards/
  // selectionCards). `undefined` for a mock-path message or a chat with
  // no real backend run yet.
  const runTrace = activeChat?.runTraces?.[message.id];
  const runTraceCompleted = runTrace?.finalDurationSeconds !== undefined && runTrace.outcome !== undefined;
  const toggleThisRunTrace = () => {
    if (activeChat) toggleRunTraceExpanded(activeChat.id, message.id);
  };
  // The live trace must stay mounted for the run's ENTIRE lifetime — not
  // just while `status === "pending"` — so it survives the
  // pending -> streaming transition (status.clear firing / the first
  // message.delta arriving) instead of disappearing the moment real text
  // starts flowing. It still steps aside once a terminal status
  // ("complete"/"error") has actually been reached on THIS message, even
  // if `chat.run`/the owning trace record technically haven't been torn
  // down/frozen yet (an existing, intentionally tested transitional
  // window — see Message.test.tsx) — at that point either the completed
  // trace (once frozen) or nothing (during the brief gap before it
  // freezes) takes over, never both/neither at once by design.
  const showLiveTrace = isRunTarget && (message.status === "pending" || message.status === "streaming");
  // Once the backend's own current-activity label is cleared (status.clear
  // fired after the first token) but the run is still active, fall back
  // to one neutral, deterministic label — never stale status text, never
  // a chain-of-thought-flavored phrase ("Thinking"/"Thought"/"Reasoning"),
  // and never per-operation-specific wording (instruction section 4/8).
  // "Thinking…" is reserved for the genuine pre-status window only (no
  // token has streamed yet); once real text has started streaming,
  // "Working" is what's actually true.
  const liveLabel = currentActivityLabel ?? (message.status === "streaming" ? "Working" : "Thinking…");

  if (isUser) {
    const hasAttachments = Boolean(message.attachments && message.attachments.length > 0);
    const visibleText = getVisibleUserText(message);
    const hasText = visibleText.trim().length > 0;

    if (isEditing) {
      return (
        <div className="anim-fade flex flex-col items-end gap-1.5">
          {message.speakerLabel && <SpeakerLabel label={message.speakerLabel} />}
          <EditMessageBox message={message} onCancel={() => setIsEditing(false)} />
        </div>
      );
    }

    return (
      <div className="group anim-fade flex flex-col items-end gap-1.5">
        {message.speakerLabel && <SpeakerLabel label={message.speakerLabel} />}
        {message.sources && message.sources.length > 0 && (
          <div className="flex flex-wrap justify-end gap-1.5">
            {message.sources.map((source) => (
              <Chip
                key={source.id}
                tone="accent"
                icon={<MessagesSquare className="h-3 w-3" />}
                label={source.scope}
                meta={SOURCE_KIND_BY_ID[source.kind]?.shortLabel}
              />
            ))}
          </div>
        )}
        {hasAttachments && (
          <div className="flex flex-wrap justify-end gap-1.5">
            {message.attachments!.map((a) => (
              <AttachmentCard key={a.id} attachment={a} />
            ))}
          </div>
        )}
        {/* POST-5.1 B4D — durable, server-owned image references from a
            rehydrated saved chat. A SEPARATE field/row from the mock
            `attachments` block above (never merged) — preserves exact
            server order, one PersistedImageAttachment per reference, and
            one image's failure never affects the others or the text
            below. */}
        {message.persistedAttachments && message.persistedAttachments.length > 0 && (
          <div className="flex flex-wrap justify-end gap-1.5">
            {message.persistedAttachments.map((reference) => (
              <PersistedImageAttachment key={reference.attachmentId} reference={reference} />
            ))}
          </div>
        )}
        {hasText && (
          <div className="max-w-[75%] space-y-2.5 break-words rounded-2xl border border-subtle/60 bg-surface-raised px-4 py-2.5 text-base leading-relaxed text-primary">
            <MessageBody
              text={visibleText}
              proseClassName="whitespace-pre-wrap break-words"
              messageId={message.id}
            />
          </div>
        )}
        <UserMessageActions message={message} onEdit={() => setIsEditing(true)} />
      </div>
    );
  }

  return (
    <div className="group anim-fade max-w-[720px]">
      {showLiveTrace && activeChat?.run ? (
        <RunTrace
          mode="live"
          label={liveLabel}
          startedAt={activeChat.run.runStartedAt}
          steps={runTrace?.steps ?? []}
          expanded={runTrace?.expanded ?? false}
          onToggle={toggleThisRunTrace}
        />
      ) : (
        message.status === "pending" && <ThinkingIndicator />
      )}
      {message.status !== "pending" && (
        <>
          {isBackendMessage && runTraceCompleted && (
            <RunTrace
              mode="completed"
              outcome={runTrace!.outcome!}
              durationSeconds={runTrace!.finalDurationSeconds!}
              steps={runTrace!.steps}
              expanded={runTrace!.expanded}
              onToggle={toggleThisRunTrace}
            />
          )}
          {message.speakerLabel && <SpeakerLabel label={message.speakerLabel} />}
          {message.text.length > 0 &&
            (isBackendMessage ? (
              // Real backend text — rendered as Markdown (frontend Markdown
              // pass). Mock/demo messages (below) keep the existing
              // block-tag system (```check/action/output/... — authored
              // only in src/data/demoScript.ts, never model output) exactly
              // as before; the two are mutually exclusive per message, so
              // there is no double-rendering.
              <MessageMarkdown content={revealed} />
            ) : (
              <div className="space-y-3">
                <MessageBody
                  text={revealed}
                  proseClassName="whitespace-pre-wrap break-words text-base leading-relaxed text-primary"
                  messageId={message.id}
                />
              </div>
            ))}
          {message.status === "error" && <AssistantErrorNotice text={message.errorMessage ?? ""} />}
          {done && message.status === "complete" && message.groundingResult === "insufficient" && (
            <NoAnswerNotice searchedScope={message.searchedScope} />
          )}
          {done && message.status === "complete" && message.citations && message.citations.length > 0 && (
            <div className="anim-fade mt-4 flex flex-wrap gap-1.5">
              {message.citations.map((c) => (
                <SourceCitation key={c.id} citation={c} />
              ))}
            </div>
          )}
          {done &&
            message.status === "complete" &&
            isBackendMessage &&
            (activeChat?.sources?.[message.id] ||
              (activeChat?.knowledgeSources?.[message.id]?.length ?? 0) > 0) && (
              // SOURCE CHIP ALIGNMENT CORRECTION: a clean, left-aligned
              // vertical stack — never a wrapping horizontal chip row. A
              // long knowledge-group label (e.g. "Source · Rogers
              // ERICSSON_4G_... · v1") needs its own full row to wrap
              // cleanly; a flex-wrap row let a long chip's wrapped second
              // line render ambiguously alongside whatever chip happened
              // to sit next to it. `items-start` keeps each chip only as
              // wide as its own content (never arbitrarily full-width —
              // see SourceChip.tsx's trigger classes for the matching
              // per-chip wrap/alignment fix). Presentation only — grouping
              // identity/ordering/drawer content are untouched.
              <div className="anim-fade mt-4 flex flex-col items-start gap-1.5">
                {activeChat?.sources?.[message.id] && (
                  <SourceChip
                    kind="teams"
                    source={activeChat.sources[message.id]}
                    sessionId={activeChat?.backendSessionId ?? ""}
                  />
                )}
                {groupKnowledgeSourceReferences(activeChat?.knowledgeSources?.[message.id]).map((group) => (
                  <SourceChip key={group.groupKey} kind="knowledge-group" group={group} />
                ))}
              </div>
            )}
          {done && message.status === "complete" && message.connectorUnavailable && (
            <ConnectorUnavailableCard info={message.connectorUnavailable} />
          )}
          {done &&
            message.status === "complete" &&
            message.suggestedConnectorIds &&
            message.suggestedConnectorIds.length > 0 && (
              <ConnectorSuggestionsCard connectorIds={message.suggestedConnectorIds} />
            )}
          {done && message.status === "complete" && message.actionProposalId && (
            <ActionProposalCard proposalId={message.actionProposalId} />
          )}
          {done && message.status === "complete" && message.distributionProposalId && (
            <ActionProposalCard proposalId={message.distributionProposalId} />
          )}
          {done && message.status === "complete" && isBackendMessage && activeChat?.selectionCards?.[message.id] && (
            <SelectionCard chatId={activeChat.id} messageId={message.id} />
          )}
          {done && message.status === "complete" && isBackendMessage && activeChat?.actionCards?.[message.id] && (
            <ApprovalCard chatId={activeChat.id} messageId={message.id} />
          )}
          {done && message.status === "complete" && (
            <AssistantMessageActions message={message} isBackendMessage={isBackendMessage} />
          )}
        </>
      )}
    </div>
  );
}
