import { lazy, Suspense, type ComponentType } from "react";
import { Plug, SlidersHorizontal, Wand2 } from "../../ui/icons";
import { useAppState } from "../../../state/AppState";
import type { SettingsSection } from "../../../types";
import { cn } from "../../../lib/cn";
import { DialogContent, DialogRoot, DialogTitle, DialogDescription } from "../../ui/Dialog";
import { UsagePanel } from "./UsagePanel";
import { ConnectorsPanel } from "./ConnectorsPanel";
import { SkillsPanel } from "./SkillsPanel";

const ObservabilitySettings = lazy(() => import("./observability/ObservabilitySettings"));

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
  { id: "observability", label: "Observability & FinOps", icon: SlidersHorizontal },
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
      <DialogContent className="flex h-[580px] max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-[880px] flex-col overflow-hidden p-0 sm:flex-row">
        <div className="sr-only"><DialogDescription>Application settings and operational diagnostics.</DialogDescription></div>
        <div className="flex shrink-0 flex-col border-b border-subtle/50 p-4 sm:w-52 sm:border-b-0 sm:border-r">
          <DialogTitle className="mb-4">Settings</DialogTitle>
          <nav aria-label="Settings categories" className="flex flex-wrap gap-0.5 sm:flex-col">
            {SECTIONS.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-current={section === item.id ? "page" : undefined}
                onClick={() => setSettingsSection(item.id)}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-base transition-colors duration-150",
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

        <div key={section} className="anim-fade min-h-0 min-w-0 flex-1 overflow-y-auto p-5">
          {section === "usage" && <UsagePanel />}
          {section === "connectors" && <ConnectorsPanel />}
          {section === "skills" && <SkillsPanel />}
          {open && section === "observability" && <Suspense fallback={<p role="status">Loading observability…</p>}><ObservabilitySettings /></Suspense>}
        </div>
      </DialogContent>
    </DialogRoot>
  );
}
