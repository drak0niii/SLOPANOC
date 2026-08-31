/**
 * Route constants shared across the eagerly-loaded App shell and the
 * lazy-loaded landing page. Kept in their own module (rather than living in
 * data/landingNav.ts) so importing a route string doesn't drag the whole
 * landing nav config — icons included — into the shared bundle.
 */
export const APP_ROUTE = "/app";

/**
 * One-shot sessionStorage key used to hand typed text from the landing
 * page's entry prompt to the real /app composer. Read once on ProductApp
 * mount and cleared immediately after, so it never lingers as state.
 */
export const DRAFT_HANDOFF_KEY = "landing-draft-handoff";
