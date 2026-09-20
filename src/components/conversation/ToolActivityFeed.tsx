import { useId, useState } from "react";
import type { RunTraceStep } from "../../types";
import { ChevronDown } from "../ui/icons";
import { ToolExecutionPill, parseToolExecutionFromLabel } from "./ToolExecutionPill";
import { cn } from "../../lib/cn";

export interface ToolActivityFeedProps {
  steps: RunTraceStep[];
  defaultExpanded?: boolean;
  className?: string;
}

export function ToolActivityFeed({ steps, defaultExpanded = false, className }: ToolActivityFeedProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const feedId = useId();

  if (!steps || steps.length === 0) {
    return null;
  }

  const completedCount = steps.filter((s) => s.status === "completed").length;

  return (
    <div
      data-testid="tool-activity-feed"
      className={cn("my-2 rounded-lg border border-subtle/80 bg-surface/50 p-2 text-xs", className)}
    >
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-controls={feedId}
        className="flex w-full items-center justify-between text-left font-medium text-secondary hover:text-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
      >
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          <span>
            Tool Executions ({completedCount}/{steps.length} completed)
          </span>
        </span>
        <ChevronDown
          className={cn("h-3.5 w-3.5 text-tertiary transition-transform duration-200", expanded && "rotate-180")}
          aria-hidden="true"
        />
      </button>

      {expanded && (
        <div id={feedId} className="anim-fade mt-2 flex flex-col gap-1.5 border-t border-subtle/60 pt-2">
          {steps.map((step) => {
            const parsed = parseToolExecutionFromLabel(step.label, step.category);
            return (
              <div key={step.stepId} className="flex items-center gap-2">
                <ToolExecutionPill
                  agentRole={parsed.agentRole}
                  toolName={parsed.toolName}
                  stage={step.status}
                />
                <span className="truncate text-[11px] text-tertiary">{step.label}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
