import { useState } from "react";
import { Plug } from "../../ui/icons";
import { useAppState } from "../../../state/AppState";
import { CONNECTOR_STATE_LABEL } from "../../../data/mock";
import type { Connector } from "../../../types";
import { cn } from "../../../lib/cn";
import {
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogRoot,
  AlertDialogTitle,
} from "../../ui/AlertDialog";
import { DialogContent, DialogRoot, DialogTitle } from "../../ui/Dialog";

const STATUS_DOT_CLASS: Record<string, string> = {
  connected: "bg-success",
  not_connected: "bg-tertiary",
  permission_required: "bg-warning",
  error: "bg-danger",
};

export const CAPABILITY_LABEL: Record<string, string> = {
  read: "Read",
  read_write: "Read + Write",
};

const CONNECT_ACTION_LABEL: Record<string, string> = {
  not_connected: "Connect",
  permission_required: "Grant access",
  error: "Reconnect",
};

const secondaryButtonClass =
  "inline-flex h-8 items-center rounded-lg px-3 text-base font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50";

function ConnectDialog({
  connector,
  open,
  onOpenChange,
}: {
  connector: Connector;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { connectConnector } = useAppState();
  return (
    <AlertDialogRoot open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogTitle>Connect {connector.name}?</AlertDialogTitle>
        <AlertDialogDescription>
          This prototype will simulate access to {connector.name}. No real account connection is
          made.
        </AlertDialogDescription>
        <div className="mt-5 flex justify-end gap-2">
          <AlertDialogCancel asChild>
            <button type="button" className={secondaryButtonClass}>
              Cancel
            </button>
          </AlertDialogCancel>
          <AlertDialogAction asChild>
            <button
              type="button"
              onClick={() => connectConnector(connector.id)}
              className="inline-flex h-8 items-center rounded-lg bg-accent px-3 text-base font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              {CONNECT_ACTION_LABEL[connector.state] ?? "Connect"}
            </button>
          </AlertDialogAction>
        </div>
      </AlertDialogContent>
    </AlertDialogRoot>
  );
}

function ManageDialog({
  connector,
  open,
  onOpenChange,
}: {
  connector: Connector;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { disconnectConnector } = useAppState();
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);

  function handleOpenChange(next: boolean) {
    onOpenChange(next);
    if (!next) window.setTimeout(() => setConfirmingDisconnect(false), 150);
  }

  return (
    <DialogRoot open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="w-full max-w-[24rem] p-5">
        {confirmingDisconnect ? (
          <>
            <DialogTitle>Disconnect {connector.name}?</DialogTitle>
            <p className="mt-1.5 text-base leading-relaxed text-tertiary">
              Projects currently using this connector will no longer be able to access it until it
              is reconnected.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmingDisconnect(false)}
                className={secondaryButtonClass}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  disconnectConnector(connector.id);
                  handleOpenChange(false);
                }}
                className="inline-flex h-8 items-center rounded-lg bg-danger px-3 text-base font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/50"
              >
                Disconnect
              </button>
            </div>
          </>
        ) : (
          <>
            <DialogTitle>{connector.name}</DialogTitle>
            <div className="mt-4 flex flex-col gap-3 text-base">
              <div className="flex items-center justify-between">
                <span className="text-tertiary">Account</span>
                <span className="text-primary">Connected via workspace</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-tertiary">Capability</span>
                <span className="text-primary">{CAPABILITY_LABEL[connector.capability]}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-tertiary">Status</span>
                <span className="inline-flex items-center gap-1.5 text-primary">
                  <span
                    className={cn("h-1.5 w-1.5 rounded-full", STATUS_DOT_CLASS[connector.state])}
                  />
                  Healthy
                </span>
              </div>
            </div>
            <div className="mt-5 flex justify-end">
              <button
                type="button"
                onClick={() => setConfirmingDisconnect(true)}
                className="inline-flex h-8 items-center rounded-lg px-3 text-base font-medium text-danger transition-colors duration-150 hover:bg-danger/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/50"
              >
                Disconnect
              </button>
            </div>
          </>
        )}
      </DialogContent>
    </DialogRoot>
  );
}

function ConnectorRow({ connector }: { connector: Connector }) {
  const [connectOpen, setConnectOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const isConnected = connector.state === "connected";

  return (
    <div className="flex items-center gap-3 rounded-lg border border-subtle/50 px-3.5 py-3 transition-all duration-200 ease-premium hover:-translate-y-px hover:border-subtle hover:shadow-sm">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-hover text-secondary">
        <Plug className="h-4 w-4" />
      </span>
      {/* Name and purpose are the content here, so they get the flexible
          space and are allowed to wrap — every other column is sized to its
          own text and never squeezes them into an ellipsis. */}
      <div className="min-w-0 flex-1">
        <p className="text-base font-medium text-primary">{connector.name}</p>
        <p className="text-sm leading-relaxed text-tertiary">{connector.purpose}</p>
      </div>
      <div className="flex w-36 shrink-0 items-center gap-1.5 text-sm text-tertiary">
        <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", STATUS_DOT_CLASS[connector.state])} />
        <span className="whitespace-nowrap">{CONNECTOR_STATE_LABEL[connector.state]}</span>
      </div>
      <div className="hidden w-24 shrink-0 whitespace-nowrap text-right text-sm text-tertiary sm:block">
        {CAPABILITY_LABEL[connector.capability]}
      </div>
      {isConnected ? (
        <button
          type="button"
          onClick={() => setManageOpen(true)}
          className={cn(secondaryButtonClass, "shrink-0")}
        >
          Manage
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setConnectOpen(true)}
          className="inline-flex h-8 shrink-0 items-center rounded-lg bg-accent px-3 text-base font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          {CONNECT_ACTION_LABEL[connector.state] ?? "Connect"}
        </button>
      )}

      <ConnectDialog connector={connector} open={connectOpen} onOpenChange={setConnectOpen} />
      <ManageDialog connector={connector} open={manageOpen} onOpenChange={setManageOpen} />
    </div>
  );
}

export function ConnectorsPanel() {
  const { connectorList } = useAppState();

  return (
    <div>
      <h3 className="text-base font-medium text-primary">Connectors</h3>
      <p className="mt-1 text-sm leading-relaxed text-tertiary">
        Manage which external tools the assistant can access.
      </p>
      <div className="mt-4 flex flex-col gap-1.5">
        {connectorList.map((connector) => (
          <ConnectorRow key={connector.id} connector={connector} />
        ))}
      </div>
    </div>
  );
}
