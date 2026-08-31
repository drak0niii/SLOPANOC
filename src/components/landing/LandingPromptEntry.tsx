import { useRef, useState, type KeyboardEvent } from "react";
import { ArrowRight } from "lucide-react";
import { useRouter } from "../../lib/router";
import { APP_ROUTE, DRAFT_HANDOFF_KEY } from "../../lib/routes";
import { cn } from "../../lib/cn";

/**
 * A compact preview of the real /app composer, styled to match it (border,
 * radius, focus glow, send-button treatment) but deliberately not wired
 * directly to chat state — this stays a decorative, standalone entry point
 * regardless of where AppStateProvider happens to live in the tree.
 * Submitting hands off any typed text to the real composer via a one-shot
 * sessionStorage key (see ProductApp.tsx) and navigates into /app.
 */
export function LandingPromptEntry({ placeholder, className }: { placeholder: string; className?: string }) {
  const { navigate } = useRouter();
  const [text, setText] = useState("");
  const [leaving, setLeaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function enter() {
    if (leaving) return;
    setLeaving(true);
    const trimmed = text.trim();
    if (trimmed) {
      sessionStorage.setItem(DRAFT_HANDOFF_KEY, trimmed);
    }
    window.setTimeout(() => navigate(APP_ROUTE), 300);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      enter();
    }
  }

  return (
    <div
      onClick={() => inputRef.current?.focus()}
      className={cn(
        "mx-auto flex w-full max-w-[520px] items-center gap-2 rounded-2xl border bg-surface px-3 py-2 shadow-md shadow-black/5 ease-premium",
        "focus-within:shadow-[0_0_0_3px_var(--app-focus-glow)]",
        leaving
          ? "pointer-events-none scale-95 border-subtle/70 opacity-0 transition-all duration-300"
          : "border-subtle/70 opacity-100 transition-all duration-150 focus-within:border-accent/40",
        className,
      )}
    >
      <input
        ref={inputRef}
        type="text"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        aria-label="Ask a question"
        className="min-w-0 flex-1 bg-transparent px-1 py-1 text-sm text-primary placeholder:text-tertiary focus:outline-none md:text-[15px]"
      />
      <button
        type="button"
        onClick={enter}
        aria-label="Enter the application"
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-transparent bg-accent text-on-accent shadow-sm transition-all duration-150 ease-premium hover:opacity-90 active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-1 focus-visible:ring-offset-surface"
      >
        <ArrowRight className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  );
}
