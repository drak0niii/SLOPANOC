import { useState } from "react";
import { X } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { ScrollingText } from "../ui/ScrollingText";

export function ConnectorSuggestionsCard({ connectorIds }: { connectorIds: string[] }) {
  const { state, connectConnector } = useAppState();
  const [dismissed, setDismissed] = useState(false);

  const connectors = connectorIds.map((id) => state.connectors[id]).filter(Boolean);
  if (dismissed || connectors.length === 0) return null;

  return (
    <div className="anim-fade mt-3 max-w-[420px] rounded-xl border border-subtle/60 bg-surface-raised px-4 py-3.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-primary">Connectors that could help</p>
        <button
          type="button"
          aria-label="Dismiss"
          onClick={() => setDismissed(true)}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3 flex flex-col gap-1.5">
        {connectors.map((connector) => (
          <div
            key={connector.id}
            className="flex items-center justify-between gap-3 rounded-lg border border-subtle/50 px-3 py-2"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium text-primary">{connector.name}</p>
              <ScrollingText className="text-xs text-tertiary">{connector.purpose}</ScrollingText>
            </div>
            {connector.state === "connected" ? (
              <span className="shrink-0 text-xs font-medium text-success">Connected</span>
            ) : (
              <button
                type="button"
                onClick={() => connectConnector(connector.id)}
                className="shrink-0 rounded-lg border border-subtle px-2.5 py-1 text-xs font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
              >
                Connect
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
