import { Check, Wand2 } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger } from "../ui/Menu";
import { Tooltip } from "../ui/Tooltip";
import { cn } from "../../lib/cn";

export function SkillSelector() {
  const { activeSkillId, setSkill, skillList } = useAppState();
  const activeSkill = skillList.find((s) => s.id === activeSkillId) ?? null;

  return (
    <MenuRoot>
      <Tooltip label="Skill for this chat">
        <MenuTrigger asChild>
          <button
            type="button"
            className={cn(
              "inline-flex h-9 max-w-40 items-center gap-1.5 rounded-lg px-2.5 text-base transition-colors duration-150",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
              activeSkill
                ? "bg-accent/10 text-accent hover:bg-accent/15"
                : "text-secondary hover:bg-surface-hover hover:text-primary",
            )}
          >
            <Wand2 className="h-4 w-4 shrink-0" />
            <span className="truncate hidden sm:inline">{activeSkill ? activeSkill.name : "Skill"}</span>
          </button>
        </MenuTrigger>
      </Tooltip>
      <MenuContent align="start" className="min-w-[16rem]">
        <MenuItem onSelect={() => setSkill(null)}>
          <div className="flex flex-1 items-center justify-between">
            <span>No skill</span>
            {activeSkillId === null && <Check className="h-4 w-4 text-accent" />}
          </div>
        </MenuItem>
        <MenuSeparator />
        {skillList.map((skill) => (
          <MenuItem key={skill.id} onSelect={() => setSkill(skill.id)}>
            <div className="flex flex-1 items-center justify-between gap-3">
              <div>
                <p className="text-base text-primary">{skill.name}</p>
                <p className="text-sm text-tertiary">{skill.description}</p>
              </div>
              {activeSkillId === skill.id && <Check className="h-4 w-4 shrink-0 text-accent" />}
            </div>
          </MenuItem>
        ))}
      </MenuContent>
    </MenuRoot>
  );
}
