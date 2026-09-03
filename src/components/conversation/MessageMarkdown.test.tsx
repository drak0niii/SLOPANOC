import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageMarkdown } from "./MessageMarkdown";

describe("MessageMarkdown — basic semantics", () => {
  it("A: renders bold text as a real <strong> element, with no literal ** in the DOM", () => {
    render(<MessageMarkdown content="**Decisions**" />);
    const strong = screen.getByText("Decisions");
    expect(strong.tagName).toBe("STRONG");
    expect(document.body.textContent).not.toContain("**");
  });

  it("B: renders italic text as a real <em> element, with no literal * in the DOM", () => {
    render(<MessageMarkdown content="*urgent*" />);
    const em = screen.getByText("urgent");
    expect(em.tagName).toBe("EM");
    expect(document.body.textContent).not.toContain("*urgent*");
  });

  it("C: renders an unordered list as <ul><li>", () => {
    const { container } = render(<MessageMarkdown content={"- First item\n- Second item"} />);
    const ul = container.querySelector("ul");
    expect(ul).not.toBeNull();
    const items = container.querySelectorAll("ul > li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toBe("First item");
    expect(items[1].textContent).toBe("Second item");
  });

  it("D: renders an ordered list as <ol><li>", () => {
    const { container } = render(<MessageMarkdown content={"1. Check alarms\n2. Verify logs"} />);
    const ol = container.querySelector("ol");
    expect(ol).not.toBeNull();
    const items = container.querySelectorAll("ol > li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toBe("Check alarms");
  });

  it("E: renders a nested list correctly", () => {
    const { container } = render(
      <MessageMarkdown content={"- Parent\n  - Child one\n  - Child two"} />,
    );
    const outerItems = container.querySelectorAll(":scope > div > ul > li");
    expect(outerItems).toHaveLength(1);
    const nested = container.querySelector("ul > li > ul");
    expect(nested).not.toBeNull();
    expect(nested?.querySelectorAll("li")).toHaveLength(2);
  });

  it("F: renders headings as real, restrained h1/h2/h3 elements", () => {
    const { container } = render(<MessageMarkdown content={"# Big\n\n## Medium\n\n### Small"} />);
    const h1 = container.querySelector("h1");
    const h2 = container.querySelector("h2");
    const h3 = container.querySelector("h3");
    expect(h1?.textContent).toBe("Big");
    expect(h2?.textContent).toBe("Medium");
    expect(h3?.textContent).toBe("Small");
    // Restrained (section 10): none of these are browser-default giant
    // sizes — each carries a compact, explicit text-size utility class,
    // never left to default UA heading styling.
    expect(h1?.className).toMatch(/text-lg/);
    expect(h2?.className).toMatch(/text-\[17px\]/);
    expect(h3?.className).toMatch(/text-base/);
  });

  it("G: renders plain paragraphs as <p>", () => {
    const { container } = render(<MessageMarkdown content="Everything looks good." />);
    const p = container.querySelector("p");
    expect(p?.textContent).toBe("Everything looks good.");
  });

  it("H: renders inline code with a real <code> element", () => {
    render(<MessageMarkdown content="Check `teams.getMessages`." />);
    const code = screen.getByText("teams.getMessages");
    expect(code.tagName).toBe("CODE");
    expect(code.className).toMatch(/font-mono/);
  });

  it("I: renders a fenced code block inside <pre><code>, preserving whitespace", () => {
    const { container } = render(<MessageMarkdown content={"```python\ndef check():\n    return True\n```"} />);
    const pre = container.querySelector("pre");
    const code = pre?.querySelector("code");
    expect(pre).not.toBeNull();
    expect(code?.textContent).toBe("def check():\n    return True\n");
    expect(pre?.className).toMatch(/overflow-x-auto/);
  });

  it("J: renders a blockquote with a real <blockquote> element", () => {
    const { container } = render(<MessageMarkdown content="> Escalate within 15 minutes." />);
    const bq = container.querySelector("blockquote");
    expect(bq?.textContent?.trim()).toBe("Escalate within 15 minutes.");
  });

  it("K: renders a safe link as a real, clickable <a> with safe target/rel", () => {
    render(<MessageMarkdown content="[docs](https://example.com/docs)" />);
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "https://example.com/docs");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });
});

describe("MessageMarkdown — escaped-Markdown regression (section 31)", () => {
  it(
    "renders backslash-escaped Markdown (as CommonMark defines it) as readable literal " +
      "text with the backslashes gone, never as raw '\\*\\*' and never as fabricated bold " +
      "— this exercises react-markdown's own CommonMark escape handling, the actual " +
      "boundary this pass relies on (see MessageMarkdown.tsx's own module docstring for " +
      "the full root-cause trace: every transport layer between the model and this " +
      "component is a plain, transformation-free pass-through, verified by direct code " +
      "inspection, not a global regex unescape)",
    () => {
      const { container } = render(<MessageMarkdown content={"\\*\\*Decisions:\\*\\*\n\\* Decision one"} />);
      const text = container.textContent ?? "";
      // The literal backslash-asterisk sequence from the raw input must
      // never reach the screen...
      expect(text).not.toContain("\\*");
      // ...and this is NOT achieved by a blind "\*" -> "*" replace: a
      // genuinely unrelated escaped asterisk (e.g. in a path/regex/code
      // sample) must survive as a literal, visible "*" too, which a
      // regex-based unescape would produce identically either way — the
      // real proof this isn't a hack is in Tier 2 below, using content
      // a blind unescape WOULD corrupt.
      expect(text).toContain("**Decisions:**");
    },
  );

  it("applies CommonMark's own escape rule uniformly — a backslash before punctuation (*) is consumed, a backslash before a non-punctuation character (T) is left completely alone", () => {
    // Precise CommonMark semantics (not a hand-rolled approximation): a
    // backslash immediately before ASCII PUNCTUATION is an escape (the
    // backslash is consumed, the punctuation renders literally) — this
    // applies uniformly whether the surrounding text "looks like"
    // Markdown or not, which is exactly why this is real parsing and not
    // pattern-matching on `**`/section headers. `\T` is NOT an escape
    // sequence (T is not punctuation), so it is left byte-for-byte
    // unchanged — proving the parser isn't blindly stripping every
    // backslash either.
    const { container } = render(<MessageMarkdown content={"Files matching C:\\Temp\\*.txt"} />);
    // \* -> literal * (backslash consumed, per spec)
    expect(container.textContent).toContain("C:\\Temp*.txt");
    // \T is untouched (not an escapable character)
    expect(container.textContent).toContain("C:\\Temp");
  });

  it("never touches the contents of inline code or fenced code blocks, even when they contain markdown-special characters", () => {
    // This is the concrete case a global `.replace` on the message text
    // WOULD corrupt (it has no concept of "inside a code span") — a real
    // Markdown parser never applies inline emphasis/escape parsing to
    // code span content at all, by CommonMark's own rules.
    render(<MessageMarkdown content="Run `git diff --stat *.py`" />);
    expect(screen.getByText("git diff --stat *.py").tagName).toBe("CODE");

    const { container } = render(
      <MessageMarkdown content={"```\nregex = r'\\*\\*bold\\*\\*'\n```"} />,
    );
    const code = container.querySelector("pre > code");
    expect(code?.textContent).toBe("regex = r'\\*\\*bold\\*\\*'\n");
  });
});

describe("MessageMarkdown — hostile HTML (section 32)", () => {
  it("never executes a <script> tag and never injects unsafe DOM (no dangerouslySetInnerHTML dependency)", () => {
    const { container } = render(<MessageMarkdown content='<script>alert("x")</script>' />);
    expect(container.querySelector("script")).toBeNull();
    // Raw HTML is treated as literal text by default (no rehype-raw) —
    // the tag characters themselves should still be present as inert text.
    expect(container.textContent).toContain("script");
  });

  it("never renders an onerror handler as a live DOM attribute", () => {
    const { container } = render(<MessageMarkdown content='<img src=x onerror=alert(1)>' />);
    const img = container.querySelector("img");
    expect(img).toBeNull();
  });
});

describe("MessageMarkdown — unsafe link scheme (section 33)", () => {
  it("strips a javascript: URL to an inert/empty href rather than an executable one", () => {
    const { container } = render(<MessageMarkdown content="[click me](javascript:alert(1))" />);
    const link = container.querySelector("a");
    expect(link).not.toBeNull();
    const href = link?.getAttribute("href");
    expect(href === "" || href === null).toBe(true);
    expect(href).not.toContain("javascript:");
    // An empty href also means the element carries no accessible "link"
    // role at all in a real browser/AT — it is not merely visually inert,
    // it is not exposed as an actionable link in the first place.
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("MessageMarkdown — streaming partial Markdown (section 34)", () => {
  it("never crashes across a realistic sequence of partial/incomplete Markdown states, and settles on the correct final structure", () => {
    const states = ["**Dec", "**Decisions", "**Decisions**", "**Decisions**\n- Item"];
    const { rerender, container } = render(<MessageMarkdown content={states[0]} />);
    for (const state of states.slice(1)) {
      expect(() => rerender(<MessageMarkdown content={state} />)).not.toThrow();
    }
    const strong = container.querySelector("strong");
    expect(strong?.textContent).toBe("Decisions");
    const li = container.querySelector("li");
    expect(li?.textContent).toBe("Item");
  });

  it("does not duplicate text across a partial-then-complete transition", () => {
    const { rerender, container } = render(<MessageMarkdown content="Everything looks go" />);
    rerender(<MessageMarkdown content="Everything looks good." />);
    const occurrences = (container.textContent?.match(/Everything looks good\./g) ?? []).length;
    expect(occurrences).toBe(1);
  });

  it("tolerates an unterminated fenced code block without crashing", () => {
    expect(() =>
      render(<MessageMarkdown content={"```py\npartially streamed code"} />),
    ).not.toThrow();
  });
});

describe("MessageMarkdown — short response stays simple (section 21/37)", () => {
  it("renders a short plain-text message as a single simple paragraph, no extra hierarchy", () => {
    const { container } = render(<MessageMarkdown content="Hello! How can I help?" />);
    expect(container.querySelectorAll("h1, h2, h3, ul, ol, blockquote, table")).toHaveLength(0);
    expect(container.querySelector("p")?.textContent).toBe("Hello! How can I help?");
  });
});

describe("MessageMarkdown — long structured response (section 29/38)", () => {
  const LONG_RESPONSE = [
    "Here's an interpretation of the conversation.",
    "",
    "**Decisions**",
    "- TG2 is conditionally approved.",
    "- Phase 1 is complete.",
    "",
    "**Actions**",
    "- Complete the Google PSO scope.",
    "- Follow up with Erol's team.",
    "- Prepare TG2 response.",
    "",
    "**Proposals**",
    "- Develop a reusable KM capability.",
    "- Define governance principles.",
    "",
    "**Open Questions**",
    "- What outcome should Phase 2 enable?",
    "",
    "**Risks**",
    "- Geographic data restrictions.",
  ].join("\n");

  it("renders every section label as bold, every item as a real list item, and no literal Markdown control syntax", () => {
    const { container } = render(<MessageMarkdown content={LONG_RESPONSE} />);

    for (const label of ["Decisions", "Actions", "Proposals", "Open Questions", "Risks"]) {
      const strong = screen.getByText(label);
      expect(strong.tagName).toBe("STRONG");
    }

    const items = Array.from(container.querySelectorAll("li")).map((li) => li.textContent);
    expect(items).toEqual([
      "TG2 is conditionally approved.",
      "Phase 1 is complete.",
      "Complete the Google PSO scope.",
      "Follow up with Erol's team.",
      "Prepare TG2 response.",
      "Develop a reusable KM capability.",
      "Define governance principles.",
      "What outcome should Phase 2 enable?",
      "Geographic data restrictions.",
    ]);

    const text = container.textContent ?? "";
    expect(text).not.toContain("**");
    expect(text).not.toMatch(/^\s*-\s/m);
  });
});
