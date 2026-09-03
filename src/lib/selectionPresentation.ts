import type { SelectionCardView } from "./selectionCard";

/**
 * Deterministic view-kind -> collapsed-one-line-label mapping for
 * SelectionCard (mirrors actionOperationLabels.ts's own philosophy: a
 * structured presentation mapping, never a hardcoded literal example
 * sentence baked into the component). `selectedLabel` is the real,
 * backend-returned candidate label — never invented here.
 *
 * Deliberately carries no glyph/symbol of its own (no "✓"/"×" prefix) —
 * `SelectionCard.tsx` already renders a dedicated `StatusIcon` next to
 * this text for every terminal state; embedding a matching character
 * here as well would duplicate that single visual status signal.
 */
export function getSelectionCollapsedLabel(view: SelectionCardView): string {
  switch (view.kind) {
    case "resolved":
      return `Teams chat selected: ${view.selectedLabel ?? "—"}`;
    case "skipped":
      return "No Teams chat selected";
    case "stale":
      return "Teams chat selection no longer active";
    case "failed":
      return "Teams chat selection failed";
    case "choosing":
      return "Selecting Teams chat…";
    case "skipping":
      return "Skipping…";
    case "pending":
    default:
      return "Choose a Teams chat";
  }
}
