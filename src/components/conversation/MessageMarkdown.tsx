import { createContext, memo, useContext } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "../../lib/cn";

/**
 * Centralized Markdown rendering + typography for normal assistant
 * message text (frontend Markdown pass). Every real backend assistant
 * message goes through this ONE component — see Message.tsx's own
 * `isBackendMessage` branch — so Markdown behavior/typography lives in
 * exactly one place, never scattered across message/card components.
 *
 * SECURITY (instruction section 24): `react-markdown` never uses
 * `dangerouslySetInnerHTML` (verified directly against the installed
 * package's own source — zero occurrences) and treats raw HTML in the
 * input as literal text unless the `rehype-raw` plugin is added, which
 * this component deliberately never does — a `<script>` tag in a
 * message renders as the visible text "<script>...", never executes.
 * Link URLs go through react-markdown's own `defaultUrlTransform`
 * (also verified directly against source), which only allows
 * `http`/`https`/`irc`/`ircs`/`mailto`/`xmpp` — a `javascript:` URL is
 * rewritten to an empty, inert href. Neither behavior is overridden or
 * weakened here.
 *
 * ESCAPED-MARKDOWN ROOT CAUSE (instruction section 3): traced the full
 * pipeline (Gemini event content -> chat_service.py's `_extract_delta_
 * text`/`_extract_final_text` -> `StreamEvent.model_dump_json()` -> SSE
 * wire format -> `sseParser.ts`'s `JSON.parse` -> AppState message text)
 * end to end — every layer does a plain, transformation-free pass-through
 * (pydantic's standard JSON serialization on the way out, `JSON.parse` on
 * the way in; no `.replace`/regex/escaping logic exists anywhere in that
 * path). No prompt in this codebase mentions Markdown or escaping at all.
 * By elimination, a literal `\*\*Decisions:\*\*` reaching the UI is
 * character-for-character what the model itself generated — a known,
 * unprompted behavior of models asked to produce prose that will also
 * pass through a JSON-structured-output boundary elsewhere in the same
 * turn (Incident Manager's own `output_schema`-constrained fields).
 * `react-markdown`'s standard CommonMark backslash-escape handling turns
 * that into the readable literal text "**Decisions:**" (no visible
 * backslashes) — not bold, because a `\*` is, correctly, an ESCAPED
 * literal asterisk, not markdown syntax; making it bold anyway would
 * require exactly the kind of blind unescape section 4 forbids (it would
 * equally "unescape" a legitimately escaped `\*` in a Windows path, a
 * regex, or code). Unescaped Markdown (`**Decisions:**`, the common case,
 * especially from the compact presentation-only turns) renders correctly
 * as bold with no special handling needed.
 */

const CodeBlockContext = createContext(false);

const components: Components = {
  p: ({ children }) => <p className="mb-2.5 whitespace-pre-wrap break-words">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-primary">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  h1: ({ children }) => (
    <h1 className="mb-2 mt-4 text-lg font-semibold leading-snug text-primary">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-1.5 mt-3.5 text-[17px] font-semibold leading-snug text-primary">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1 mt-3 text-base font-semibold leading-snug text-primary">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="mb-1 mt-2.5 text-base font-semibold leading-snug text-secondary">{children}</h4>
  ),
  h5: ({ children }) => (
    <h5 className="mb-1 mt-2.5 text-base font-semibold leading-snug text-secondary">{children}</h5>
  ),
  h6: ({ children }) => (
    <h6 className="mb-1 mt-2.5 text-base font-semibold leading-snug text-secondary">{children}</h6>
  ),
  ul: ({ children }) => (
    <ul className="mb-2.5 list-disc space-y-1 pl-5 marker:text-tertiary">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-2.5 list-decimal space-y-1 pl-5 marker:text-tertiary">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="pl-1 leading-relaxed [&>ol]:mb-0 [&>ol]:mt-1 [&>p]:mb-0 [&>ul]:mb-0 [&>ul]:mt-1">
      {children}
    </li>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mb-2.5 border-l-2 border-subtle pl-3 text-secondary [&>p]:mb-0">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-subtle/60" />,
  a: ({ children, href, ...rest }) => (
    <a
      {...rest}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent underline underline-offset-2 hover:opacity-80"
    >
      {children}
    </a>
  ),
  pre: ({ children }) => (
    <CodeBlockContext.Provider value={true}>
      <pre className="mb-2.5 max-w-full overflow-x-auto rounded-lg border border-subtle/60 bg-surface-hover/60 px-3.5 py-3 font-mono text-sm leading-relaxed text-secondary">
        {children}
      </pre>
    </CodeBlockContext.Provider>
  ),
  code: ({ children, className }) => {
    // Fenced code blocks are always wrapped in <pre> (regardless of
    // whether a language tag was given); genuinely inline code never is
    // — this context flag is a robust way to tell them apart, since a
    // fenced block's own `className` is only present when the fence
    // itself named a language (see module docstring — no syntax
    // highlighting is added either way, section 16).
    const inBlock = useContext(CodeBlockContext);
    if (inBlock) {
      return <code className={className}>{children}</code>;
    }
    return (
      <code className="rounded bg-surface-hover px-1.5 py-0.5 font-mono text-[0.875em] text-primary">
        {children}
      </code>
    );
  },
  table: ({ children }) => (
    <div className="mb-2.5 overflow-x-auto rounded-lg border border-subtle/60">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-surface-hover/60">{children}</thead>,
  tr: ({ children }) => <tr className="border-b border-subtle/60 last:border-0">{children}</tr>,
  th: ({ children }) => (
    <th className="px-3 py-1.5 text-left font-semibold text-primary">{children}</th>
  ),
  td: ({ children }) => <td className="px-3 py-1.5 text-secondary">{children}</td>,
};

const remarkPlugins = [remarkGfm];

export const MessageMarkdown = memo(function MessageMarkdown({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  return (
    <div className={cn("text-base leading-relaxed text-primary [&>*:first-child]:mt-0 [&>*:last-child]:mb-0", className)}>
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
