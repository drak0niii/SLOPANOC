import { useRef } from "react";
import { Paperclip, MessagesSquare, Plug, Plus } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { CONNECTOR_STATE_LABEL } from "../../data/mock";
import { CHAT_ROOM_KIND_IDS, SOURCE_KIND_BY_ID } from "../../data/workspaceSources";
import { createId } from "../../lib/id";
import { ACCEPTED_IMAGE_MIME_TYPES } from "../../lib/constants";
import { IconButton } from "../ui/IconButton";
import { ScrollingText } from "../ui/ScrollingText";
import { Tooltip } from "../ui/Tooltip";
import {
  MenuCheckboxItem,
  MenuContent,
  MenuItem,
  MenuRoot,
  MenuSub,
  MenuSubContent,
  MenuSubTrigger,
  MenuTrigger,
} from "../ui/Menu";

function RowLabel({ label, description }: { label: string; description: string }) {
  return (
    <span className="flex min-w-0 flex-1 items-baseline gap-2.5 overflow-hidden">
      <span className="shrink-0 whitespace-nowrap font-medium text-primary">{label}</span>
      {/* The description is the part that gets clipped when the menu is
          narrower than the copy, so it reveals itself on hover the same way
          long sidebar rows do. */}
      <ScrollingText className="flex-1 text-tertiary">{description}</ScrollingText>
    </span>
  );
}

export function ComposerPlusMenu() {
  const {
    state,
    activeChat,
    activeConnectorIds,
    toggleConnector,
    queueImageFiles,
    addDraftSource,
    activeProject,
    connectorList,
  } = useAppState();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const availableConnectors = activeProject
    ? connectorList.filter((connector) => activeProject.connectorIds.includes(connector.id))
    : connectorList;

  // POST-5.1 B3 — mirrors `queueImageFiles`'s own eligibility gate in
  // AppState.tsx exactly (general workspace scope, no drafted ad-hoc chat
  // room source, no active demo script) so the affordance is disabled
  // rather than silently doing nothing when clicked.
  const canAttachImages =
    state.workspaceScope.type === "general" && state.draft.sources.length === 0 && !activeChat?.demoRun;

  function handleFilesSelected(event: React.ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    queueImageFiles(Array.from(files));
    event.target.value = "";
  }

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPTED_IMAGE_MIME_TYPES.join(",")}
        className="hidden"
        onChange={handleFilesSelected}
      />

      <MenuRoot>
        <Tooltip label="Add files, chat rooms, or connectors">
          <MenuTrigger asChild>
            <IconButton label="Add files, chat rooms, or connectors">
              <Plus className="h-[18px] w-[18px]" />
            </IconButton>
          </MenuTrigger>
        </Tooltip>
        <MenuContent align="start" className="w-96">
          <MenuItem
            icon={<Paperclip className="h-5 w-5 shrink-0 text-secondary" />}
            disabled={!canAttachImages}
            onSelect={(e) => {
              e.preventDefault();
              if (!canAttachImages) return;
              window.setTimeout(() => fileInputRef.current?.click(), 0);
            }}
          >
            <RowLabel label="Add images" description="PNG, JPEG, or WebP from your computer" />
          </MenuItem>
          <MenuSub>
            <MenuSubTrigger icon={<MessagesSquare className="h-5 w-5 shrink-0 text-secondary" />}>
              <RowLabel label="Add chat room" description="Read a channel and summarize it" />
            </MenuSubTrigger>
            <MenuSubContent>
              {CHAT_ROOM_KIND_IDS.flatMap((kindId) => {
                const kind = SOURCE_KIND_BY_ID[kindId];
                const connected = connectorList.find((c) => c.id === kind.connectorId)?.state === "connected";
                return kind.scopeOptions.map((scope) => (
                  <MenuItem
                    key={`${kindId}:${scope}`}
                    disabled={!connected}
                    onSelect={() => addDraftSource({ id: createId("src"), kind: kindId, scope })}
                  >
                    <span className="flex flex-1 items-center justify-between gap-3">
                      <span className="truncate">{scope}</span>
                      <span className="shrink-0 text-sm text-tertiary">
                        {connected ? kind.shortLabel : "Not connected"}
                      </span>
                    </span>
                  </MenuItem>
                ));
              })}
            </MenuSubContent>
          </MenuSub>

          <MenuSub>
            <MenuSubTrigger icon={<Plug className="h-5 w-5 shrink-0 text-secondary" />}>
              <RowLabel label="Add connector" description="Use an available connector" />
            </MenuSubTrigger>
            <MenuSubContent>
              {availableConnectors.length === 0 ? (
                <div className="max-w-56 px-3 py-2.5">
                  <p className="text-base font-medium text-primary">No connectors enabled</p>
                  <p className="mt-1 text-sm leading-relaxed text-tertiary">
                    Enable connectors for this project in Project settings.
                  </p>
                </div>
              ) : (
                availableConnectors.map((connector) => (
                  <MenuCheckboxItem
                    key={connector.id}
                    checked={activeConnectorIds.includes(connector.id)}
                    disabled={connector.state !== "connected"}
                    onSelect={(e) => e.preventDefault()}
                    onCheckedChange={() => toggleConnector(connector.id)}
                  >
                    <span className="flex flex-1 items-center justify-between gap-3">
                      <span>{connector.name}</span>
                      {connector.state !== "connected" && (
                        <span className="text-sm text-tertiary">
                          {CONNECTOR_STATE_LABEL[connector.state]}
                        </span>
                      )}
                    </span>
                  </MenuCheckboxItem>
                ))
              )}
            </MenuSubContent>
          </MenuSub>
        </MenuContent>
      </MenuRoot>
    </>
  );
}
