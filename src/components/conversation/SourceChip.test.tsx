import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SourceChip } from "./SourceChip";
import type { Citation } from "../../types";
import type { SourceReferenceDTO } from "../../api/types";

function makeCitation(overrides: Partial<Citation> = {}): Citation {
  return {
    id: "c1",
    docId: "MOP-042",
    docTitle: "Incident Response Runbook",
    version: "v3",
    status: "approved",
    section: "4.2",
    excerpt: "Escalate to on-call within 15 minutes.",
    scope: "global",
    scopeLabel: "Global approved knowledge",
    ...overrides,
  };
}

function makeSource(overrides: Partial<SourceReferenceDTO> = {}): SourceReferenceDTO {
  return {
    source_id: "src1",
    source_type: "teams",
    label: "Teams conversation",
    title: "Ops Bridge",
    message_count: 29,
    period_start: "2026-08-26T09:00:00Z",
    period_end: "2026-09-01T09:00:00Z",
    contributors: ["Alex", "Priya"],
    evidence: [
      { author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: "We should escalate this now." },
      { author: "Priya", sent_at: "2026-08-27T10:00:00Z", snippet: "Agreed, paging on-call." },
    ],
    ...overrides,
  };
}

describe("SourceChip — MOP (kind: 'mop') — no regression", () => {
  it("renders the docId as the trigger label, unchanged from the prior SourceCitation", () => {
    render(<SourceChip kind="mop" citation={makeCitation()} />);
    expect(screen.getByRole("button", { name: /MOP-042/ })).toBeInTheDocument();
  });

  it("opens the drawer on click, showing the document title, version, section, and excerpt", () => {
    render(<SourceChip kind="mop" citation={makeCitation()} />);
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("Incident Response Runbook")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.getByText("Section 4.2")).toBeInTheDocument();
    expect(screen.getByText('"Escalate to on-call within 15 minutes."')).toBeInTheDocument();
  });

  it("never shows Teams-specific fields for a MOP source", () => {
    render(<SourceChip kind="mop" citation={makeCitation()} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Microsoft Teams")).not.toBeInTheDocument();
    expect(screen.queryByText("Messages reviewed")).not.toBeInTheDocument();
  });
});

describe("SourceChip — Teams (kind: 'teams')", () => {
  it("renders a compact 'Source · <label>' trigger", () => {
    render(<SourceChip kind="teams" source={makeSource()} />);
    expect(screen.getByRole("button", { name: /Source · Teams conversation/ })).toBeInTheDocument();
  });

  it("opens the SAME drawer shell on click (Radix dialog content becomes visible)", () => {
    render(<SourceChip kind="teams" source={makeSource()} />);
    expect(screen.queryByText("Microsoft Teams")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Microsoft Teams")).toBeInTheDocument();
  });

  it("shows conversation topic, message count, period, and contributors", () => {
    render(<SourceChip kind="teams" source={makeSource()} />);
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("Ops Bridge")).toBeInTheDocument();
    expect(screen.getByText("29")).toBeInTheDocument();
    expect(screen.getByText("Alex, Priya")).toBeInTheDocument();
  });

  it("shows supporting evidence entries with author and timestamp, never a raw message id", () => {
    render(<SourceChip kind="teams" source={makeSource()} />);
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("Supporting evidence")).toBeInTheDocument();
    expect(screen.getAllByText("Alex")).not.toHaveLength(0);
    expect(screen.getAllByText("Priya")).not.toHaveLength(0);
  });

  it("never renders a raw chat_id, membership id, or any internal identifier (not present in the DTO at all)", () => {
    render(<SourceChip kind="teams" source={makeSource()} />);
    fireEvent.click(screen.getByRole("button"));
    const drawerText = document.body.textContent ?? "";
    expect(drawerText).not.toMatch(/19:[a-f0-9-]+@thread/);
    expect(drawerText.toLowerCase()).not.toContain("chat_id");
    expect(drawerText.toLowerCase()).not.toContain("payload_hash");
  });

  it("omits Conversation/Messages reviewed/Period/Contributors sections when not authoritative, rather than showing a placeholder", () => {
    const minimal = makeSource({
      title: null,
      message_count: null,
      period_start: null,
      period_end: null,
      contributors: [],
      evidence: [],
    });
    render(<SourceChip kind="teams" source={minimal} />);
    fireEvent.click(screen.getByRole("button"));

    expect(screen.queryByText("Conversation")).not.toBeInTheDocument();
    expect(screen.queryByText("Messages reviewed")).not.toBeInTheDocument();
    expect(screen.queryByText("Period")).not.toBeInTheDocument();
    expect(screen.queryByText("Contributors")).not.toBeInTheDocument();
    expect(screen.queryByText("Supporting evidence")).not.toBeInTheDocument();
    // The header/type/generic explanation still render regardless.
    expect(screen.getByText("Microsoft Teams")).toBeInTheDocument();
  });

  it("never dumps entire conversation content — only a short snippet per evidence entry", () => {
    render(<SourceChip kind="teams" source={makeSource()} />);
    fireEvent.click(screen.getByRole("button"));
    // Each evidence entry shows its own short snippet, never the full
    // conversation dumped as one block.
    expect(screen.getByText('"We should escalate this now."')).toBeInTheDocument();
    expect(screen.getByText('"Agreed, paging on-call."')).toBeInTheDocument();
  });

  it("has a visibly larger trigger than the MOP chip (more padding, distinct classes)", () => {
    const { unmount } = render(<SourceChip kind="mop" citation={makeCitation()} />);
    const mopButton = screen.getByRole("button");
    const mopClasses = mopButton.className;
    unmount();

    render(<SourceChip kind="teams" source={makeSource()} />);
    const teamsButton = screen.getByRole("button", { name: /Source · Teams conversation/ });
    expect(teamsButton.className).not.toBe(mopClasses);
    expect(teamsButton.className).toMatch(/px-2\.5/);
    expect(teamsButton.className).toMatch(/py-1\.5/);
  });
});

describe("SourceChip — Teams supporting evidence (snippet-authenticity fix)", () => {
  it("renders a short snippet alongside author/timestamp", () => {
    const source = makeSource({
      evidence: [{ author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: "We should escalate this now." }],
    });
    render(<SourceChip kind="teams" source={source} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText('"We should escalate this now."')).toBeInTheDocument();
  });

  it("never renders an empty author/timestamp-only row — an item with an empty snippet is skipped entirely", () => {
    const source = makeSource({
      evidence: [
        { author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: "" },
        { author: "Priya", sent_at: "2026-08-27T09:00:00Z", snippet: "A real excerpt." },
      ],
    });
    render(<SourceChip kind="teams" source={source} />);
    fireEvent.click(screen.getByRole("button"));

    // The valid item still renders...
    expect(screen.getByText('"A real excerpt."')).toBeInTheDocument();
    // ...but "Alex" (whose only entry had an empty snippet) never appears
    // as a bare author/timestamp row anywhere in the drawer.
    expect(screen.queryByText("Alex")).not.toBeInTheDocument();
  });

  it("skips an item whose snippet is whitespace-only", () => {
    const source = makeSource({
      evidence: [{ author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: "   \n\t  " }],
    });
    render(<SourceChip kind="teams" source={source} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Supporting evidence")).not.toBeInTheDocument();
  });

  it("defensively skips a malformed (non-string) snippet from an untrusted payload", () => {
    const source = makeSource({
      evidence: [
        { author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: null as unknown as string },
        { author: "Priya", sent_at: "2026-08-27T09:00:00Z", snippet: "A real excerpt." },
      ],
    });
    render(<SourceChip kind="teams" source={source} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText('"A real excerpt."')).toBeInTheDocument();
    expect(screen.queryByText("Alex")).not.toBeInTheDocument();
  });

  it("omits the whole Supporting Evidence section when every candidate lacks a valid snippet", () => {
    const source = makeSource({
      evidence: [
        { author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: "" },
        { author: "Priya", sent_at: "2026-08-27T09:00:00Z", snippet: "" },
      ],
    });
    render(<SourceChip kind="teams" source={source} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Supporting evidence")).not.toBeInTheDocument();
    // The rest of the drawer is unaffected.
    expect(screen.getByText("Microsoft Teams")).toBeInTheDocument();
  });

  it("never renders more than 5 supporting evidence items, even if given more", () => {
    const source = makeSource({
      evidence: Array.from({ length: 12 }, (_, i) => ({
        author: `User ${i}`,
        sent_at: `2026-08-${10 + i}T09:00:00Z`,
        snippet: `Message number ${i}.`,
      })),
    });
    render(<SourceChip kind="teams" source={source} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getAllByText(/^"Message number \d+\.".*/)).toHaveLength(5);
  });

  it("keeps scanning past invalid candidates to still show 5 valid examples, not the first 5 records", () => {
    const evidence = Array.from({ length: 7 }, (_, i) => ({
      author: `User ${i + 1}`,
      sent_at: `2026-08-${10 + i}T09:00:00Z`,
      // #3 and #5 (1-indexed) have no usable snippet.
      snippet: [3, 5].includes(i + 1) ? "" : `Message body ${i + 1}.`,
    }));
    const source = makeSource({ evidence });
    render(<SourceChip kind="teams" source={source} />);
    fireEvent.click(screen.getByRole("button"));

    for (const n of [1, 2, 4, 6, 7]) {
      expect(screen.getByText(`"Message body ${n}."`)).toBeInTheDocument();
    }
    expect(screen.queryByText("User 3")).not.toBeInTheDocument();
    expect(screen.queryByText("User 5")).not.toBeInTheDocument();
  });

  it("the drawer content is scrollable so a long panel doesn't clip", () => {
    render(<SourceChip kind="teams" source={makeSource()} />);
    fireEvent.click(screen.getByRole("button"));
    const drawer = screen.getByRole("dialog");
    expect(drawer.className).toMatch(/overflow-y-auto/);
  });
});
