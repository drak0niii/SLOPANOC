import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolExecutionPill, parseToolExecutionFromLabel } from "./ToolExecutionPill";
import { ToolActivityFeed } from "./ToolActivityFeed";
import type { RunTraceStep } from "../../types";

describe("ToolExecutionPill", () => {
  it("renders with specialist agent badge and tool name", () => {
    render(
      <ToolExecutionPill
        agentRole="technical_authority"
        toolName="knowledge_search"
        parametersSummary="query: 'VSWR threshold'"
        stage="completed"
      />
    );

    expect(screen.getByText("Technical Authority")).toBeInTheDocument();
    expect(screen.getByText("knowledge_search")).toBeInTheDocument();
    expect(screen.getByText("query: 'VSWR threshold'")).toBeInTheDocument();
  });

  it("renders spinner when stage is invoking", () => {
    const { container } = render(
      <ToolExecutionPill
        agentRole="incident_manager"
        toolName="teams_get_messages"
        stage="invoking"
      />
    );

    expect(screen.getByText("Incident Manager")).toBeInTheDocument();
    expect(screen.getByText("teams_get_messages")).toBeInTheDocument();
    const spinner = container.querySelector(".animate-spin");
    expect(spinner).toBeInTheDocument();
  });

  it("parses tool execution from trace step label accurately", () => {
    const parsedTAE = parseToolExecutionFromLabel(
      "Consulting Level 2 Technical Authority Engineer",
      "context"
    );
    expect(parsedTAE.agentRole).toBe("technical_authority");

    const parsedIM = parseToolExecutionFromLabel(
      "Retrieving Teams messages",
      "teams"
    );
    expect(parsedIM.agentRole).toBe("incident_manager");
    expect(parsedIM.toolName).toBe("teams_get_messages");

    const parsedPM = parseToolExecutionFromLabel(
      "Consulting ITIL Problem Manager for root cause analysis",
      "case"
    );
    expect(parsedPM.agentRole).toBe("problem_manager");

    const parsedTM = parseToolExecutionFromLabel(
      "Coordinating with Technical Authority Engineer",
      "context"
    );
    expect(parsedTM.agentRole).toBe("team_manager");
    expect(parsedTM.toolName).toBe("technical_authority_engineer");
  });
});

describe("ToolActivityFeed", () => {
  const mockSteps: RunTraceStep[] = [
    {
      stepId: "step-1",
      category: "teams",
      label: "Retrieving Teams messages",
      status: "completed",
    },
    {
      stepId: "step-2",
      category: "context",
      label: "Searching governed knowledge",
      status: "completed",
    },
  ];

  it("renders tool activity header with completion count", () => {
    render(<ToolActivityFeed steps={mockSteps} />);

    expect(screen.getByText(/Tool Executions \(2\/2 completed\)/)).toBeInTheDocument();
  });

  it("toggles step details when clicked", () => {
    render(<ToolActivityFeed steps={mockSteps} />);

    expect(screen.queryByText("teams_get_messages")).not.toBeInTheDocument();

    const button = screen.getByRole("button");
    fireEvent.click(button);

    expect(screen.getByText("teams_get_messages")).toBeInTheDocument();
    expect(screen.getByText("knowledge_search")).toBeInTheDocument();
  });
});
