import type { ComponentType } from "react";
import { Plug, SlidersHorizontal, Wand2 } from "../../ui/icons";
import { useAppState } from "../../../state/AppState";
import type { SettingsSection } from "../../../types";
import { cn } from "../../../lib/cn";
import { DialogContent, DialogRoot, DialogTitle } from "../../ui/Dialog";
import { UsagePanel } from "./UsagePanel";
import { ConnectorsPanel } from "./ConnectorsPanel";
import { SkillsPanel } from "./SkillsPanel";

// Typed structurally rather than as `typeof SlidersHorizontal`: this list
// mixes animated and static icons, which have different component types.
const SECTIONS: {
  id: SettingsSection;
  label: string;
  icon: ComponentType<{ className?: string }>;
}[] = [
  { id: "usage", label: "Usage", icon: SlidersHorizontal },
  { id: "connectors", label: "Connectors", icon: Plug },
  { id: "skills", label: "Skills", icon: Wand2 },
];

interface SettingsModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SettingsModal({ open, onOpenChange }: SettingsModalProps) {
  const { state, setSettingsSection } = useAppState();
  const section = state.settingsModal.section;

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[580px] w-full max-w-[880px] overflow-hidden p-0">
        <div className="flex w-52 shrink-0 flex-col border-r border-subtle/50 p-4">
          <DialogTitle className="mb-4">Settings</DialogTitle>
          <nav className="flex flex-col gap-0.5">
            {SECTIONS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setSettingsSection(item.id)}
                className={cn(
                  "flex items-center gap-2.5 whitespace-nowrap rounded-lg px-2.5 py-2 text-left text-base transition-colors duration-150",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
                  section === item.id
                    ? "bg-surface-hover text-primary"
                    : "text-secondary hover:bg-surface-hover hover:text-primary",
                )}
              >
                <item.icon className="h-4 w-4 shrink-0" />
                {item.label}
              </button>
            ))}
          </nav>
        </div>

        <div key={section} className="anim-fade min-w-0 flex-1 overflow-y-auto p-5">
          {section === "usage" && <UsagePanel />}
          {section === "connectors" && <ConnectorsPanel />}
          {section === "skills" && <SkillsPanel />}
        </div>
      </DialogContent>
    </DialogRoot>
  );
}
