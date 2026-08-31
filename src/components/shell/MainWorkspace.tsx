import { useAppState } from "../../state/AppState";
import { EmptyChat } from "../conversation/EmptyChat";
import { ActiveConversation } from "../conversation/ActiveConversation";
import { ProjectContextHeader } from "./ProjectContextHeader";
import { ProjectHome } from "./ProjectHome";
import { AllProjectsPage } from "./AllProjectsPage";
import { ScheduledTasksPage } from "./ScheduledTasksPage";
import { ScheduledTaskDetailPage } from "./ScheduledTaskDetailPage";

export function MainWorkspace() {
  const { state, activeChat, activeProject } = useAppState();

  if (state.mainView === "allProjects") {
    return (
      <main className="flex min-w-0 flex-1 bg-bg">
        <AllProjectsPage />
      </main>
    );
  }

  if (state.mainView === "scheduledTasks") {
    return (
      <main className="flex min-w-0 flex-1 bg-bg">
        <ScheduledTasksPage />
      </main>
    );
  }

  if (state.mainView === "scheduledTaskDetail") {
    return (
      <main className="flex min-w-0 flex-1 bg-bg">
        <ScheduledTaskDetailPage />
      </main>
    );
  }

  // A project with no active chat gets its own landing page (composer +
  // instructions/memory/context panel); once a chat is open, a project is
  // just a normal conversation with a slim context strip above it.
  if (activeProject && !activeChat) {
    return (
      <main className="flex min-w-0 flex-1 bg-bg">
        <ProjectHome project={activeProject} />
      </main>
    );
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col bg-bg">
      {activeProject && <ProjectContextHeader project={activeProject} />}
      <div className="min-h-0 flex-1">{activeChat ? <ActiveConversation /> : <EmptyChat />}</div>
    </main>
  );
}
