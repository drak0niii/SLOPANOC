/** Reference cap shown in the UI ("10 files tops") and enforced when adding
 * — keeps a project's Context list from growing unbounded in this prototype. */
export const MAX_PROJECT_FILES = 10;

const TEXT_LIKE_EXTENSION = /\.(txt|md|markdown|csv|json|log|ya?ml)$/i;

/** Real text files are read as-is (genuinely useful and simple); anything
 * else (pptx, docx, pdf, images, ...) can't be parsed client-side in this
 * prototype, so it gets a clearly-labeled mock preview instead. */
export async function readProjectFileContent(file: File): Promise<string> {
  const isTextLike = file.type.startsWith("text/") || TEXT_LIKE_EXTENSION.test(file.name);
  if (isTextLike) {
    try {
      return await file.text();
    } catch {
      // fall through to the mock placeholder below
    }
  }
  return `This is a mock preview of "${file.name}".\n\nContent extraction for this file type isn't implemented in this prototype — in the real product this would show the file's extracted text.`;
}

export function countLines(content: string): number {
  return content.length === 0 ? 0 : content.split("\n").length;
}
