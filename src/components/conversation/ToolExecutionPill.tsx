import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Check, Clock, Loader2, TriangleAlert, X } from "../ui/icons";

export interface ToolExecutionInfo {
  agentRole?: "incident_manager" | "technical_authority" | "problem_manager" | "automated_ops" | "team_manager";
  toolName?: string;
  parametersSummary?: string;
  stage: "invoking" | "completed" | "warning" | "failed";
}

const AGENT_CONFIG: Record<
  NonNullable<ToolExecutionInfo["agentRole"]>,
  { badge: string; badgeColor: string; bgClass: string; textClass: string; borderClass: string }
> = {
  incident_manager: {
    badge: "Incident Manager",
    badgeColor: "bg-info/10 text-info border-info/30",
    bgClass: "bg-info/[0.04]",
    textClass: "text-info",
    borderClass: "border-info/30",
  },
  technical_authority: {
    badge: "Technical Authority",
    badgeColor: "bg-accent/10 text-accent border-accent/30",
    bgClass: "bg-accent/[0.04]",
    textClass: "text-accent",
    borderClass: "border-accent/30",
  },
  problem_manager: {
    badge: "Problem Manager",
    badgeColor: "bg-warning/10 text-warning border-warning/30",
    bgClass: "bg-warning/[0.04]",
    textClass: "text-warning",
    borderClass: "border-warning/30",
  },
  automated_ops: {
    badge: "Automated Ops",
    badgeColor: "bg-automation/10 text-automation border-automation/30",
    bgClass: "bg-automation/[0.04]",
    textClass: "text-automation",
    borderClass: "border-automation/30",
  },
  team_manager: {
    badge: "Team Manager",
    badgeColor: "bg-subtle text-secondary border-subtle",
    bgClass: "bg-surface-hover/40",
    textClass: "text-secondary",
    borderClass: "border-subtle",
  },
};

export function parseToolExecutionFromLabel(label: string, category?: string): ToolExecutionInfo {
  const normalized = label.toLowerCase();

  let agentRole: ToolExecutionInfo["agentRole"] = undefined;
  if (normalized.includes("technical authority") || normalized.includes("troubleshooting") || category === "context") {
    agentRole = "technical_authority";
  } else if (normalized.includes("teams") || normalized.includes("incident") || category === "teams") {
    agentRole = "incident_manager";
  } else if (normalized.includes("problem manager") || normalized.includes("root cause")) {
    agentRole = "problem_manager";
  } else if (normalized.includes("automated ops") || normalized.includes("automated operations")) {
    agentRole = "automated_ops";
  } else if (normalized.includes("case") || category === "case") {
    agentRole = "team_manager";
  }

  let toolName: string | undefined = undefined;
  if (normalized.includes("searching governed knowledge") || normalized.includes("search")) {
    toolName = "knowledge_search";
  } else if (normalized.includes("validating supporting evidence") || normalized.includes("evidence")) {
    toolName = "knowledge_select_evidence";
  } else if (normalized.includes("finding the teams conversation") || normalized.includes("conversations")) {
    toolName = "teams_list_chats";
  } else if (normalized.includes("retrieving teams messages") || normalized.includes("messages")) {
    toolName = "teams_get_messages";
  } else if (normalized.includes("participants") || normalized.includes("members")) {
    toolName = "teams_get_members";
  } else if (normalized.includes("case analysis") || normalized.includes("case")) {
    toolName = "record_case_analysis";
  }

  return {
    agentRole,
    toolName,
    stage: "completed",
  };
}

export interface ToolExecutionPillProps {
  agentRole?: ToolExecutionInfo["agentRole"];
  toolName?: string;
  parametersSummary?: string;
  stage: ToolExecutionInfo["stage"];
  className?: string;
}

export function ToolExecutionPill({
  agentRole,
  toolName,
  parametersSummary,
  stage,
  className,
}: ToolExecutionPillProps) {
  const config = agentRole ? AGENT_CONFIG[agentRole] : null;

  let statusIcon: ReactNode = null;
  switch (stage) {
    case "invoking":
      statusIcon = <Loader2 className="h-3 w-3 animate-spin text-accent" aria-hidden="true" />;
      break;
    case "completed":
      statusIcon = <Check className="h-3 w-3 text-success" aria-hidden="true" />;
      break;
    case "warning":
      statusIcon = <TriangleAlert className="h-3 w-3 text-warning" aria-hidden="true" />;
      break;
    case "failed":
      statusIcon = <X className="h-3 w-3 text-danger" aria-hidden="true" />;
      break;
    default:
      statusIcon = <Clock className="h-3 w-3 text-tertiary" aria-hidden="true" />;
      break;
  }

  return (
    <div
      data-testid="tool-execution-pill"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs transition-colors",
        config ? config.borderClass : "border-subtle/80",
        config ? config.bgClass : "bg-surface-hover/30",
        className,
      )}
    >
      <span className="flex items-center">{statusIcon}</span>

      {config && (
        <span
          className={cn(
            "rounded px-1 py-0.2 text-[10px] font-medium border tracking-wide uppercase",
            config.badgeColor,
          )}
        >
          {config.badge}
        </span>
      )}

      {toolName && <span className="font-mono text-[11px] font-semibold text-primary">{toolName}</span>}

      {parametersSummary && (
        <span className="max-w-[200px] truncate text-[11px] text-tertiary" title={parametersSummary}>
          {parametersSummary}
        </span>
      )}
    </div>
  );
}
