import { Plus, X } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { createId } from "../../lib/id";
import { SOURCE_KINDS, SOURCE_KIND_BY_ID } from "../../data/workspaceSources";
import type { SourceKindId, TaskSource } from "../../types";
import { IconButton } from "../ui/IconButton";

const selectClass =
  "min-w-0 flex-1 rounded-lg border border-subtle bg-surface px-2.5 py-1.5 text-sm text-primary transition-colors duration-150 focus:border-accent/40 focus:outline-none";

/**
 * The task's inputs, one line per source. Deliberately plain rows rather than
 * cards — this sits inside a dialog and is the likeliest part of the feature
 * to balloon into a dashboard.
 */
export function TaskSourceRows({
  sources,
  onChange,
}: {
  sources: TaskSource[];
  onChange: (sources: TaskSource[]) => void;
}) {
  const { state, connectConnector } = useAppState();

  function addSource() {
    const kind = SOURCE_KINDS[0];
    onChange([
      ...sources,
      { id: createId("src"), kind: kind.id, scope: kind.scopeOptions[0] },
    ]);
  }

  function updateKind(id: string, kindId: SourceKindId) {
    const kind = SOURCE_KIND_BY_ID[kindId];
    onChange(
      sources.map((source) =>
        // Scope options are per-kind, so reset to the new kind's first option
        // rather than carrying over a scope that no longer exists.
        source.id === id ? { ...source, kind: kindId, scope: kind.scopeOptions[0] } : source,
      ),
    );
  }

  function updateScope(id: string, scope: string) {
    onChange(sources.map((source) => (source.id === id ? { ...source, scope } : source)));
  }

  return (
    <div>
      <p className="text-sm font-medium text-primary">Sources</p>
      <p className="mt-1 text-xs leading-relaxed text-tertiary">
        What each run reads before writing your brief.
      </p>

      {sources.length > 0 && (
        <div className="mt-2.5 flex flex-col gap-1.5">
          {sources.map((source) => {
            const kind = SOURCE_KIND_BY_ID[source.kind];
            const connected = state.connectors[kind.connectorId]?.state === "connected";

            return (
              <div key={source.id}>
                <div className="flex items-center gap-1.5">
                  <select
                    value={source.kind}
                    onChange={(event) => updateKind(source.id, event.target.value as SourceKindId)}
                    aria-label="Source type"
                    className={selectClass}
                  >
                    {SOURCE_KINDS.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <select
                    value={source.scope}
                    onChange={(event) => updateScope(source.id, event.target.value)}
                    aria-label={kind.scopeLabel}
                    className={selectClass}
                  >
                    {kind.scopeOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                  <IconButton
                    label={`Remove ${kind.label}`}
                    size="sm"
                    onClick={() => onChange(sources.filter((s) => s.id !== source.id))}
                  >
                    <X className="h-3.5 w-3.5" />
                  </IconButton>
                </div>

                {!connected && (
                  <p className="mt-1 pl-0.5 text-xs text-warning">
                    {kind.label} isn't connected — this source will be skipped.{" "}
                    <button
                      type="button"
                      onClick={() => connectConnector(kind.connectorId)}
                      className="font-medium text-accent transition-opacity duration-150 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                    >
                      Connect
                    </button>
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      <button
        type="button"
        onClick={addSource}
        className="mt-2 inline-flex items-center gap-1.5 rounded-lg px-1 py-1 text-sm font-medium text-secondary transition-colors duration-150 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      >
        <Plus className="h-3.5 w-3.5" />
        Add source
      </button>
    </div>
  );
}
