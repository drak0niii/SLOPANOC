import { useEffect, useRef, useState, type ReactNode } from "react";
import { Circle, MoreHorizontal, Pin } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { cn } from "../../lib/cn";
import type { Chat } from "../../types";
import { ScrollingText } from "../ui/ScrollingText";
import { ChatRowMenu } from "./ChatRowMenu";
import { DeleteChatDialog } from "./DeleteChatDialog";

interface SidebarChatRowProps {
  chat: Chat;
  active: boolean;
  onSelect: () => void;
  showPinIndicator?: boolean;
  /** Small dot before the title, marking this as a chat nested under a
   * project (matches the project tree's visual convention). */
  showChatIcon?: boolean;
  /** Extra content shown before the menu trigger — e.g. a relative
   * timestamp on the project home page's Recents list. */
  trailing?: ReactNode;
}

export function SidebarChatRow({
  chat,
  active,
  onSelect,
  showPinIndicator,
  showChatIcon,
  trailing,
}: SidebarChatRowProps) {
  const { renameChat, deleteChat, togglePin } = useAppState();
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(chat.title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  function startRename() {
    setDraftTitle(chat.title);
    setEditing(true);
  }

  function commitRename() {
    const trimmed = draftTitle.trim();
    if (trimmed) {
      renameChat(chat.id, trimmed);
    }
    setEditing(false);
  }

  function cancelRename() {
    setDraftTitle(chat.title);
    setEditing(false);
  }

  if (editing) {
    return (
      <div className="flex items-center rounded-lg bg-surface-hover px-2.5 py-2">
        <input
          ref={inputRef}
          value={draftTitle}
          onChange={(e) => setDraftTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commitRename();
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancelRename();
            }
          }}
          onBlur={commitRename}
          aria-label="Chat title"
          className="w-full min-w-0 bg-transparent text-base text-primary outline-none"
        />
      </div>
    );
  }

  return (
    <div
      className={cn(
        // SIDEBAR CHAT ROW CORRECTION: `text-base` (16px) made a saved
        // chat's own title render LARGER than its own "Chats" section
        // header (`text-sm`, 14px) — a hierarchy inversion, and the
        // reported "slightly too large/heavy" feel. `text-sm` is an
        // EXISTING design-system step (matches the section header
        // exactly), not an invented value — weight/color still
        // distinguish the header from a row.
        "group relative flex w-full items-center rounded-lg py-2 pl-2.5 pr-1 text-sm transition-colors duration-150",
        active || menuOpen
          ? "bg-surface-hover/50 text-primary"
          : "text-secondary hover:bg-surface-hover hover:text-primary",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? "true" : undefined}
        // SIDEBAR CHAT ROW CORRECTION: `gap-1.5` (6px) read as too tight
        // between the leading dot and the title ("○chat title") — bumped
        // to `gap-2` (8px), then (UI MICRO-POLISH follow-up) one more
        // existing gap step to `gap-2.5` (10px) — still a "consistent
        // flex gap" per the row's own existing pattern, not a manual
        // space/margin. Dot size, row height, and font size untouched.
        className="flex min-w-0 flex-1 items-center gap-2.5 text-left focus-visible:outline-none"
      >
        {/* UI MICRO-POLISH: one step smaller than before (h-2 -> h-1.5) —
            still the same hollow-circle glyph, muted color, and
            alignment; only the diameter changed. */}
        {showChatIcon && <Circle className="h-1.5 w-1.5 shrink-0 text-tertiary" aria-hidden="true" />}
        {showPinIndicator && chat.pinned && (
          <Pin className="h-3 w-3 shrink-0 text-tertiary" />
        )}
        {chat.unread && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />}
        {/* SIDEBAR CHAT ROW CORRECTION: the currently-selected row keeps a
            stable, clean truncation — it never starts the delayed
            hover-scroll animation, so its highlighted background never
            has its own title sliding out from under it. Every other
            (non-selected) row's existing delayed-scroll behavior is
            completely unaffected.

            UI MICRO-POLISH: deliberately NOT `flex-1` — that used to
            stretch ScrollingText's own hover-listening element to fill
            all remaining row width, so hovering empty space to the
            right of a short title (before the menu button) could also
            arm the hover timer. Without `flex-1`, the element sizes to
            its own content (shrink-to-fit) and only shrinks down to the
            available width when the title genuinely overflows — i.e.
            its hit-region is now exactly the rendered title text, never
            wider. `min-w-0` (inside ScrollingText itself) still permits
            that shrink; nothing about title-start alignment, row
            height, or available width for the text itself changes. */}
        <ScrollingText
          className={cn(chat.unread && "font-medium text-primary")}
          disableHoverScroll={active}
        >
          {chat.title}
        </ScrollingText>
      </button>

      {trailing}

      <ChatRowMenu
        chat={chat}
        open={menuOpen}
        onOpenChange={setMenuOpen}
        onRename={startRename}
        onTogglePin={() => togglePin(chat.id)}
        onRequestDelete={() => setConfirmDeleteOpen(true)}
        trigger={
          <button
            type="button"
            aria-label="Chat options"
            className={cn(
              "ml-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md opacity-0 transition-opacity duration-100",
              "hover:bg-surface-hover hover:text-primary",
              "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
              "group-hover:opacity-100",
              // Ternary, not an appended override: cn() is plain string
              // concatenation, and `.text-tertiary` is emitted after
              // `.text-primary`, so both together would leave this muted.
              menuOpen ? "bg-surface-hover text-primary opacity-100" : "text-tertiary",
            )}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
        }
      />

      <DeleteChatDialog
        open={confirmDeleteOpen}
        onOpenChange={setConfirmDeleteOpen}
        chatTitle={chat.title}
        onConfirm={() => deleteChat(chat.id)}
      />
    </div>
  );
}
