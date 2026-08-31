import type { Project } from "../types";

/** Mirrors `sortChatsForDisplay` — pinned projects always float to the top
 * (oldest pin first, same convention as pinned chats), with the rest ordered
 * by whatever comparator the caller needs (creation date, name, ...). */
export function sortProjectsForDisplay(
  projects: Project[],
  compareRest?: (a: Project, b: Project) => number,
): Project[] {
  const pinned = projects
    .filter((project) => project.pinned)
    .sort((a, b) => (a.pinnedAt ?? 0) - (b.pinnedAt ?? 0));
  const rest = projects.filter((project) => !project.pinned);
  const sortedRest = compareRest ? [...rest].sort(compareRest) : rest;
  return [...pinned, ...sortedRest];
}
