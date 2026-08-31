/**
 * Mock profile and a year of token activity.
 *
 * The daily series is *generated* rather than hand-written, and every headline
 * figure is derived from it — so the totals, the peak and the streaks always
 * agree with the graph instead of being three numbers that happen to sit next
 * to each other. Generation is deterministic: a re-render must not reshuffle a
 * year of history.
 */

export const MOCK_PROFILE = {
  name: "Alex Nordin",
  handle: "anordin",
  plan: "Enterprise",
  initials: "AN",
};

export interface ActivityDay {
  date: Date;
  tokens: number;
}

/** Weeks shown across the graph — 53 covers a full year plus the part-week. */
export const ACTIVITY_WEEKS = 53;
const DAYS_PER_WEEK = 7;

/** Deterministic pseudo-random in [0, 1) for a given index. */
function noise(seed: number): number {
  const x = Math.sin(seed * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

function startOfDay(date: Date): Date {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function addDays(date: Date, days: number): Date {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
}

/**
 * Columns of 7 days, oldest first, ending on the current week. Usage ramps up
 * over the year and dips at weekends, so the graph reads like a real adoption
 * curve rather than uniform noise.
 */
export function buildActivity(today: Date = new Date()): ActivityDay[][] {
  const end = startOfDay(today);
  // 0 = Monday, so the last column is the week containing today.
  const weekdayFromMonday = (end.getDay() + 6) % 7;
  const firstDay = addDays(end, -weekdayFromMonday - (ACTIVITY_WEEKS - 1) * DAYS_PER_WEEK);

  const columns: ActivityDay[][] = [];
  const totalDays = ACTIVITY_WEEKS * DAYS_PER_WEEK;

  for (let week = 0; week < ACTIVITY_WEEKS; week += 1) {
    const column: ActivityDay[] = [];
    for (let day = 0; day < DAYS_PER_WEEK; day += 1) {
      const index = week * DAYS_PER_WEEK + day;
      const date = addDays(firstDay, index);
      const ramp = index / totalDays;

      // Days beyond today have not happened yet.
      if (date > end) {
        column.push({ date, tokens: 0 });
        continue;
      }

      const isWeekend = date.getDay() === 0 || date.getDay() === 6;
      const active = noise(index) < 0.12 + ramp * 0.78;
      const magnitude = (0.25 + noise(index + 977)) * ramp * (isWeekend ? 0.3 : 1);
      column.push({ date, tokens: active ? Math.round(magnitude * 9_400_000) : 0 });
    }
    columns.push(column);
  }

  return columns;
}

export interface ProfileStats {
  lifetimeTokens: number;
  peakTokens: number;
  currentStreak: number;
  longestStreak: number;
}

export function deriveStats(columns: ActivityDay[][]): ProfileStats {
  const days = columns.flat();
  let lifetimeTokens = 0;
  let peakTokens = 0;
  let longestStreak = 0;
  let running = 0;

  for (const day of days) {
    lifetimeTokens += day.tokens;
    peakTokens = Math.max(peakTokens, day.tokens);
    running = day.tokens > 0 ? running + 1 : 0;
    longestStreak = Math.max(longestStreak, running);
  }

  // Counted backwards from the most recent day that has actually happened.
  const today = startOfDay(new Date());
  const elapsed = days.filter((day) => day.date <= today);
  let currentStreak = 0;
  for (let i = elapsed.length - 1; i >= 0; i -= 1) {
    if (elapsed[i].tokens === 0) break;
    currentStreak += 1;
  }

  return { lifetimeTokens, peakTokens, currentStreak, longestStreak };
}

/** "1.4B", "170.4M" — compact enough to sit under a stat label. */
export function formatTokens(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}
