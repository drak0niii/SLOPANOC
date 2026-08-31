/**
 * The welcome shown beside the mark above the composer on an empty chat.
 *
 * One line, deliberately. It also says nothing about the workspace's actual
 * state — no counts, no "you have 3 alerts" — because none of that is real
 * here, and a greeting that quietly invents activity is worse than one that
 * just sets the tone. Kept short so it stays on a single line at display size.
 */
export function greetingForHour(hour: number): string {
  if (hour >= 5 && hour < 12) return "Good morning. Fresh shift, clear head.";
  if (hour >= 12 && hour < 17) return "Good afternoon. What needs a look?";
  if (hour >= 17 && hour < 22) return "Good evening. Let's tie off the ends.";
  return "Working late. Let's keep it green.";
}

export function currentGreeting(now: Date = new Date()): string {
  return greetingForHour(now.getHours());
}
