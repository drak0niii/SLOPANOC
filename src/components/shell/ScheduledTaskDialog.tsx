import { useEffect, useState } from "react";
import { useAppState } from "../../state/AppState";
import { MOCK_MODELS, DEFAULT_MODEL_ID } from "../../data/mock";
import { FREQUENCY_LABEL } from "../../lib/scheduledTasks";
import type {
  Distribution,
  ScheduledTask,
  ScheduledTaskFrequency,
  ScheduledTaskPermission,
  TaskSource,
} from "../../types";
import { DialogClose, DialogContent, DialogRoot, DialogTitle } from "../ui/Dialog";
import { TaskSourceRows } from "./TaskSourceRows";
import { TaskDistributionRows } from "./TaskDistributionRows";

const FREQUENCY_OPTIONS: ScheduledTaskFrequency[] = ["manual", "daily", "weekdays", "weekly", "monthly"];

const PERMISSION_OPTIONS: { id: ScheduledTaskPermission; label: string }[] = [
  { id: "manual_approve", label: "Manually approve" },
  { id: "auto_run", label: "Auto-run" },
];

const NO_PROJECT = "__none__";

const inputClass =
  "mt-1.5 w-full rounded-lg border border-subtle bg-surface px-3 py-2 text-base text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none";

const selectClass =
  "rounded-lg border border-subtle bg-surface px-3 py-1.5 text-sm text-primary transition-colors duration-150 focus:border-accent/40 focus:outline-none";

interface ScheduledTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The task being edited. Creation happens through the scheduling
   * conversation, not this form — there is no create branch. */
  existingTask: ScheduledTask;
}

export function ScheduledTaskDialog({ open, onOpenChange, existingTask }: ScheduledTaskDialogProps) {
  const { projects, updateScheduledTask } = useAppState();

  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [modelId, setModelId] = useState(DEFAULT_MODEL_ID);
  const [frequency, setFrequency] = useState<ScheduledTaskFrequency>("manual");
  const [timeOfDay, setTimeOfDay] = useState("09:00");
  const [permission, setPermission] = useState<ScheduledTaskPermission>("manual_approve");
  const [sources, setSources] = useState<TaskSource[]>([]);
  const [distributions, setDistributions] = useState<Distribution[]>([]);
  const [projectId, setProjectId] = useState(NO_PROJECT);

  // Re-seed the form whenever the dialog is opened, rather than on every keystroke.
  useEffect(() => {
    if (!open) return;
    setName(existingTask.name);
    setInstructions(existingTask.instructions);
    setModelId(existingTask.modelId);
    setFrequency(existingTask.frequency);
    setTimeOfDay(existingTask.timeOfDay);
    setPermission(existingTask.permission);
    setSources(existingTask.sources);
    setDistributions(existingTask.distributions);
    setProjectId(existingTask.projectId ?? NO_PROJECT);
  }, [open, existingTask]);

  const canSave = name.trim().length > 0 && instructions.trim().length > 0;

  function handleSave() {
    if (!canSave) return;
    updateScheduledTask({
      ...existingTask,
      name: name.trim(),
      instructions: instructions.trim(),
      modelId,
      frequency,
      timeOfDay,
      permission,
      sources,
      distributions,
      projectId: projectId === NO_PROJECT ? undefined : projectId,
    });
    onOpenChange(false);
  }

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-full max-w-lg p-6">
        <DialogTitle className="text-lg">Edit scheduled task</DialogTitle>

        <div className="mt-5 flex max-h-[65vh] flex-col gap-5 overflow-y-auto pr-1">
          <div>
            <label htmlFor="task-name" className="text-sm font-medium text-primary">
              Name <span className="text-danger">*</span>
            </label>
            <input
              id="task-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Daily briefing"
              className={inputClass}
            />
          </div>

          <div>
            <label htmlFor="task-instructions" className="text-sm font-medium text-primary">
              Instructions <span className="text-danger">*</span>
            </label>
            <div className="mt-1.5 rounded-xl border border-subtle bg-surface transition-colors duration-150 focus-within:border-accent/40">
              <textarea
                id="task-instructions"
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                rows={4}
                placeholder="Round up the AI news I should know about, in five bullets."
                className="w-full resize-none bg-transparent px-3 py-2.5 text-base leading-relaxed text-primary placeholder:text-tertiary focus:outline-none"
              />
              <div className="flex items-center justify-end border-t border-subtle/60 px-3 py-2">
                <select
                  value={modelId}
                  onChange={(event) => setModelId(event.target.value)}
                  aria-label="Model"
                  className="bg-transparent text-sm font-medium text-secondary outline-none"
                >
                  {MOCK_MODELS.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <div className="flex items-center justify-between gap-4">
            <label htmlFor="task-frequency" className="text-sm font-medium text-primary">
              Frequency
            </label>
            <div className="flex items-center gap-2">
              <select
                id="task-frequency"
                value={frequency}
                onChange={(event) => setFrequency(event.target.value as ScheduledTaskFrequency)}
                className={selectClass}
              >
                {FREQUENCY_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {FREQUENCY_LABEL[option]}
                  </option>
                ))}
              </select>
              {frequency !== "manual" && (
                <input
                  type="time"
                  aria-label="Time of day"
                  value={timeOfDay}
                  onChange={(event) => setTimeOfDay(event.target.value)}
                  className={selectClass}
                />
              )}
            </div>
          </div>

          <div className="flex items-center justify-between gap-4">
            <label htmlFor="task-permission" className="text-sm font-medium text-primary">
              Permissions
            </label>
            <select
              id="task-permission"
              value={permission}
              onChange={(event) => setPermission(event.target.value as ScheduledTaskPermission)}
              className={selectClass}
            >
              {PERMISSION_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center justify-between gap-4">
            <label htmlFor="task-project" className="text-sm font-medium text-primary">
              Project
            </label>
            <select
              id="task-project"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              className={selectClass}
            >
              <option value={NO_PROJECT}>No project</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </div>

          <TaskSourceRows sources={sources} onChange={setSources} />

          <TaskDistributionRows distributions={distributions} onChange={setDistributions} />
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <DialogClose asChild>
            <button
              type="button"
              className="inline-flex h-8 items-center rounded-lg px-3 text-base font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              Cancel
            </button>
          </DialogClose>
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            className="inline-flex h-8 items-center rounded-lg bg-accent px-3 text-base font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:opacity-40"
          >
            Save
          </button>
        </div>
      </DialogContent>
    </DialogRoot>
  );
}
