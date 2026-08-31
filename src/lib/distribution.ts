const PREVIEW_LIMIT = 280;

/**
 * The text shown in a distribution proposal's preview. Fenced blocks are
 * presentation-only markup (```finding, ```cta, …) and would read as noise
 * quoted back at the user, so they're stripped before trimming.
 */
export function previewForDistribution(text: string): string {
  const withoutFences = text
    .split("```")
    .filter((_, index) => index % 2 === 0)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();

  if (withoutFences.length <= PREVIEW_LIMIT) return withoutFences;
  return `${withoutFences.slice(0, PREVIEW_LIMIT).trimEnd()}…`;
}
