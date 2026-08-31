import { useEffect, useRef, useState } from "react";
import { useAppState } from "../state/AppState";
import { createId } from "../lib/id";
import { formatBytes } from "../lib/format";
import { MAX_PROJECT_FILES, readProjectFileContent } from "../lib/projectFiles";
import type { ProjectFile } from "../types";

/** Shared by the project home page's Context card and the project settings
 * Files panel, so adding a file behaves identically (same cap, same mock
 * content generation) no matter which surface it's added from. */
export function useAddProjectFiles(projectId: string, currentFileCount: number) {
  const { addProjectFiles } = useAppState();
  const inputRef = useRef<HTMLInputElement>(null);
  const atCapacity = currentFileCount >= MAX_PROJECT_FILES;
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  function triggerAdd() {
    if (atCapacity) return;
    // Deferred a tick, same as the composer's own file/folder triggers —
    // clicking a hidden file input synchronously from inside a Radix Dialog
    // (Project Settings) can race with the dialog's focus trap and silently
    // fail to open the native picker.
    window.setTimeout(() => inputRef.current?.click(), 0);
  }

  async function handleFilesSelected(event: React.ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files;
    event.target.value = "";
    if (!selected || selected.length === 0) return;

    const remainingSlots = Math.max(0, MAX_PROJECT_FILES - currentFileCount);
    const accepted = Array.from(selected).slice(0, remainingSlots);
    const skipped = selected.length - accepted.length;

    if (accepted.length === 0) {
      setNotice(`Project is at its ${MAX_PROJECT_FILES}-file limit — nothing was added.`);
      return;
    }

    const newFiles: ProjectFile[] = await Promise.all(
      accepted.map(async (file) => ({
        id: createId("pfile"),
        name: file.name,
        size: formatBytes(file.size),
        type: file.type || file.name.split(".").pop()?.toUpperCase() || "File",
        selectedAt: Date.now(),
        content: await readProjectFileContent(file),
      })),
    );
    addProjectFiles(projectId, newFiles);

    if (skipped > 0) {
      setNotice(
        `Only added ${accepted.length} of ${selected.length} files — project cap is ${MAX_PROJECT_FILES}.`,
      );
    }
  }

  return { inputRef, triggerAdd, handleFilesSelected, atCapacity, notice };
}
