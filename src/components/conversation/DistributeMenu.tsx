import { Share2 } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { DESTINATION_OPTIONS } from "../../data/workspaceSources";
import { CONNECTOR_STATE_LABEL } from "../../data/mock";
import { MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger } from "../ui/Menu";
import { Tooltip } from "../ui/Tooltip";

/**
 * "Send to…" on an assistant response. Picking a destination only *proposes*
 * the post — it lands as a normal action-proposal card that has to be
 * approved, same as any other outward-facing action.
 */
export function DistributeMenu({ messageId }: { messageId: string }) {
  const { state, proposeDistribution, openSettings } = useAppState();

  return (
    <MenuRoot>
      <Tooltip label="Send to…">
        <MenuTrigger asChild>
          <button
            type="button"
            aria-label="Send to…"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
          >
            <Share2 className="h-3.5 w-3.5" />
          </button>
        </MenuTrigger>
      </Tooltip>
      <MenuContent align="start" className="w-64">
        {DESTINATION_OPTIONS.map((option) => {
          const connector = state.connectors[option.connectorId];
          const connected = connector?.state === "connected";
          return (
            <MenuItem
              key={option.id}
              disabled={!connected}
              onSelect={() => proposeDistribution(messageId, option.id)}
            >
              <span className="flex flex-1 items-center justify-between gap-3">
                <span className="truncate">{option.label}</span>
                {!connected && connector && (
                  <span className="shrink-0 text-sm text-tertiary">
                    {CONNECTOR_STATE_LABEL[connector.state]}
                  </span>
                )}
              </span>
            </MenuItem>
          );
        })}
        <MenuSeparator />
        <MenuItem onSelect={() => openSettings("connectors")}>Manage connectors</MenuItem>
      </MenuContent>
    </MenuRoot>
  );
}
