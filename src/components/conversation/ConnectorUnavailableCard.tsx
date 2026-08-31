import { PlugZap } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import type { ConnectorUnavailableInfo } from "../../types";

export function ConnectorUnavailableCard({ info }: { info: ConnectorUnavailableInfo }) {
  const { openSettings, openProjectSettings } = useAppState();
  const isProjectIssue = info.reason === "project_disabled";

  return (
    <div className="anim-fade mt-3 flex max-w-[420px] items-start gap-2.5 rounded-xl border border-subtle/60 px-4 py-3.5 transition-shadow duration-200 ease-premium hover:shadow-sm">
      <PlugZap className="mt-0.5 h-4 w-4 shrink-0 text-tertiary" />
      <div>
        <p className="text-base text-secondary">{info.connectorName} is not available in this workspace.</p>
        <button
          type="button"
          onClick={() => (isProjectIssue ? openProjectSettings("connectors") : openSettings("connectors"))}
          className="mt-2 text-sm font-medium text-accent transition-opacity duration-150 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          {isProjectIssue ? "Enable for this project" : "Manage connectors"}
        </button>
      </div>
    </div>
  );
}
