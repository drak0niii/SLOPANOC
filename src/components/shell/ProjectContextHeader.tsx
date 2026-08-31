import { Folder, Settings2 } from "../ui/icons";
import type { Project } from "../../types";
import { useAppState } from "../../state/AppState";
import { IconButton } from "../ui/IconButton";
import { ScrollingText } from "../ui/ScrollingText";
import { Tooltip } from "../ui/Tooltip";
import { ProjectSettingsModal } from "./ProjectSettingsModal";

export function ProjectContextHeader({ project }: { project: Project }) {
  const { state, openProjectSettings, closeProjectSettings } = useAppState();

  return (
    <div className="anim-fade flex shrink-0 items-center justify-between px-6 py-3">
      <div className="flex min-w-0 items-center gap-2 text-secondary">
        <Folder className="h-3.5 w-3.5 shrink-0" />
        <ScrollingText className="text-base font-medium">{project.name}</ScrollingText>
      </div>

      <Tooltip label="Project settings">
        <IconButton
          label="Project settings"
          size="sm"
          onClick={() => openProjectSettings()}
        >
          <Settings2 className="h-[15px] w-[15px]" />
        </IconButton>
      </Tooltip>

      <ProjectSettingsModal
        project={project}
        open={state.projectSettingsModal.open}
        onOpenChange={(next) => (next ? openProjectSettings() : closeProjectSettings())}
      />
    </div>
  );
}
