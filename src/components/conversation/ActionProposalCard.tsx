import { Check, Loader2, X } from "../ui/icons";
import { useAppState } from "../../state/AppState";

interface ActionCopy {
  /** Primary button label while the proposal is pending. */
  confirm: string;
  processing: string;
  cancelled: string;
  /** Terminal text; `subject` is the proposal's most identifying field. */
  completed: (subject: string) => string;
}

/** Keyed by `actionType`, which is a plain string — a new action type needs
 * only an entry here, no type change and no new branch. */
const ACTION_COPY: Record<string, ActionCopy> = {
  schedule_task: {
    confirm: "Schedule",
    processing: "Creating scheduled task…",
    cancelled: "Scheduling cancelled",
    completed: (subject) => `"${subject}" scheduled`,
  },
  post_to_channel: {
    confirm: "Post",
    processing: "Posting…",
    cancelled: "Post cancelled",
    completed: (subject) => `Posted to ${subject}`,
  },
};

const DEFAULT_COPY: ActionCopy = {
  confirm: "Approve & Send",
  processing: "Sending…",
  cancelled: "Action cancelled",
  completed: (subject) => `${subject} sent`,
};

export function ActionProposalCard({ proposalId }: { proposalId: string }) {
  const { state, approveAction, cancelAction, viewScheduledTask } = useAppState();
  const proposal = state.actionProposals[proposalId];
  if (!proposal) return null;

  const isSchedule = proposal.actionType === "schedule_task";
  const copy = ACTION_COPY[proposal.actionType] ?? DEFAULT_COPY;
  const taskName = proposal.fields.find((f) => f.label === "Name")?.value ?? proposal.title;
  const subject = isSchedule
    ? taskName
    : (proposal.fields.find((f) => f.label === "Destination")?.value ??
      proposal.title.replace(/^Send /, ""));

  if (proposal.status === "cancelled") {
    return (
      <div className="anim-fade mt-3 flex max-w-[420px] items-center gap-2 rounded-xl border border-subtle/50 px-4 py-3 text-base text-tertiary">
        <X className="h-3.5 w-3.5 shrink-0" />
        {copy.cancelled}
      </div>
    );
  }

  if (proposal.status === "completed") {
    const scheduledTask =
      isSchedule && proposal.createdTaskId ? state.scheduledTasks[proposal.createdTaskId] ?? null : null;

    return (
      <div className="anim-fade mt-3 flex max-w-[420px] items-center gap-2 rounded-xl border border-subtle/50 px-4 py-3 text-base">
        <Check className="h-3.5 w-3.5 shrink-0 text-success" />
        <span className="text-secondary">{copy.completed(subject)}</span>
        {isSchedule ? (
          scheduledTask && (
            <button
              type="button"
              onClick={() => viewScheduledTask(scheduledTask.id)}
              className="text-sm font-medium text-accent transition-opacity duration-150 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              View
            </button>
          )
        ) : (
          <span className="text-tertiary">· Simulated action</span>
        )}
      </div>
    );
  }

  const isProcessing = proposal.status === "approved" || proposal.status === "processing";

  return (
    <div className="anim-fade mt-3 max-w-[420px] rounded-xl border border-subtle/60 bg-surface-raised px-4 py-3.5 transition-shadow duration-200 ease-premium hover:shadow-sm">
      <p className="text-base font-medium text-primary">{proposal.title}</p>

      <div className="mt-2.5 flex flex-col gap-1">
        {proposal.fields.map((field) => (
          <div key={field.label} className="flex gap-1.5 text-sm">
            <span className="shrink-0 text-tertiary">{field.label}:</span>
            <span className="text-secondary">{field.value}</span>
          </div>
        ))}
      </div>

      <p className="mt-3 border-l-2 border-subtle pl-3 text-sm italic leading-relaxed text-tertiary">
        "{proposal.body}"
      </p>

      <div className="mt-3.5 flex items-center justify-end gap-2">
        {isProcessing ? (
          <span key="processing" className="anim-fade inline-flex items-center gap-1.5 text-sm text-tertiary">
            <Loader2 className="h-3 w-3 animate-spin" />
            {proposal.status === "approved" ? "Preparing…" : copy.processing}
          </span>
        ) : (
          <>
            <button
              type="button"
              onClick={() => cancelAction(proposal.id)}
              className="inline-flex h-7 items-center rounded-lg px-2.5 text-sm font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => approveAction(proposal.id)}
              className="inline-flex h-7 items-center rounded-lg bg-accent px-2.5 text-sm font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              {copy.confirm}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
