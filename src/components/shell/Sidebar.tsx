import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  ChevronRight,
  CircleHelp,
  Clock,
  Folder,
  PanelLeft,
  Plus,
  Settings,
  SquarePen,
  User,
} from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { Link } from "../../lib/router";
import { cn } from "../../lib/cn";
import { sortChatsForDisplay } from "../../lib/chatSort";
import { sortProjectsForDisplay } from "../../lib/projectSort";
import type { ScheduledTask } from "../../types";
import { IconButton } from "../ui/IconButton";
import { Tooltip } from "../ui/Tooltip";
import { SidebarChatRow } from "./SidebarChatRow";
import { ProjectRow } from "./ProjectRow";
import { SidebarTaskRow } from "./SidebarTaskRow";
import { CreateProjectDialog } from "./CreateProjectDialog";
import { SettingsModal } from "./settings/SettingsModal";
import {
  MenuContent,
  MenuItem,
  MenuRoot,
  MenuSeparator,
  MenuTrigger,
} from "../ui/Menu";
import { ProfileDialog } from "./ProfileDialog";

function SidebarActionRow({
  icon,
  label,
  collapsed,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  collapsed: boolean;
  onClick?: () => void;
}) {
  if (collapsed) {
    return (
      <Tooltip label={label}>
        <IconButton label={label} onClick={onClick} className="mx-auto">
          {icon}
        </IconButton>
      </Tooltip>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className="anim-label-in flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-lg font-medium text-primary transition-colors duration-150 hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
    >
      {icon}
      {label}
    </button>
  );
}

function SidebarFooterButton({
  icon,
  label,
  collapsed,
  onClick,
  disabled,
}: {
  icon: React.ReactNode;
  label: string;
  collapsed: boolean;
  onClick?: () => void;
  disabled?: boolean;
}) {
  if (collapsed) {
    return (
      <Tooltip label={disabled ? `${label} (coming soon)` : label}>
        <IconButton label={label} onClick={onClick} disabled={disabled}>
          {icon}
        </IconButton>
      </Tooltip>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="anim-label-in flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-base text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:pointer-events-none disabled:opacity-40"
    >
      {icon}
      {label}
    </button>
  );
}

/** Empty-state line for a section. Small and uppercase so it reads as a
 * status note rather than a row you could click. */
const SECTION_EMPTY_CLASS =
  "px-2.5 py-1.5 text-[11px] font-medium uppercase tracking-wide text-tertiary";

/** Colour is inherited from the header row, so the marker brightens with the
 * label on hover instead of staying muted. */
const SECTION_ICON_CLASS = "h-4 w-4 shrink-0";

/**
 * Buttons inside a section header need a stronger hover than IconButton's
 * default: the row itself is already `bg-surface-hover` by the time you reach
 * one, so the default would paint the same colour on the same colour and read
 * as no feedback at all.
 *
 * `bg-inverse` flips per theme — dark in light mode, light in dark mode — so a
 * 10% wash darkens or lightens as appropriate. `!` is needed because cn() only
 * concatenates, and `bg-surface-hover` sorts later in the stylesheet.
 */
const SECTION_ACTION_CLASS = "hover:!bg-inverse/10";

/**
 * Collapsible header for a sidebar browsing list.
 *
 * The chevron sits at the far end of the row, after any `actions`, so every
 * section's disclosure control lines up in one column regardless of how many
 * buttons it has. Label and chevron are separate buttons that both toggle —
 * a button cannot legally contain another button, and the chevron is the part
 * people reach for.
 */
function SidebarSectionHeader({
  label,
  icon,
  expanded,
  onToggle,
  actions,
}: {
  label: string;
  icon: React.ReactNode;
  expanded: boolean;
  onToggle: () => void;
  actions?: React.ReactNode;
}) {
  return (
    // Same row treatment as SidebarChatRow: padding and hover live on the
    // container so the whole line highlights, not just the label's own box.
    <div className="group flex items-center rounded-lg py-1.5 pl-2.5 pr-1 text-sm font-medium text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-primary">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline-none"
      >
        {icon}
        <span className="truncate">{label}</span>
      </button>
      {actions && (
        // Revealed on hover, matching the "…" menu triggers on the rows
        // below. opacity-0 keeps them focusable — unlike `hidden` — so
        // focus-within brings them back for keyboard users.
        <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-100 focus-within:opacity-100 group-hover:opacity-100">
          {actions}
        </span>
      )}
      {/* The chevron stays visible: it is the section's expanded/collapsed
          indicator, not just a control. */}
      <IconButton
        label={`${expanded ? "Collapse" : "Expand"} ${label}`}
        size="sm"
        onClick={onToggle}
        aria-expanded={expanded}
        className={cn("ml-0.5", SECTION_ACTION_CLASS)}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 transition-transform duration-150 ease-premium",
            expanded && "rotate-90",
          )}
        />
      </IconButton>
    </div>
  );
}

/** Mirrors the landing header's "Open app ->" link, in reverse: same
 * plain-navigation logic (a Link back to "/"), just living next to the
 * app shell's sidebar Account row instead of a page header. Always
 * icon-only so it sits comfortably beside the Account button. */
function SidebarBackToSiteLink() {
  return (
    <Tooltip label="Back to site">
      <Link
        to="/"
        aria-label="Back to site"
        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      >
        <ArrowLeft className="h-[18px] w-[18px]" />
      </Link>
    </Tooltip>
  );
}

export function Sidebar() {
  const {
    state,
    chatList,
    activeChat,
    projects,
    newChat,
    selectChat,
    toggleSidebar,
    openSettings,
    closeSettings,
    openScheduledTasks,
    openAllProjects,
    startScheduledTaskSetup,
    retrySavedChats,
  } = useAppState();
  const collapsed = state.sidebarCollapsed;
  // Task-owned chats are listed under Tasks instead, so a scheduling
  // conversation produces exactly one sidebar row rather than a chat and a
  // task that look identical.
  const generalChats = useMemo(
    () => sortChatsForDisplay(chatList.filter((chat) => !chat.projectId && !chat.scheduledTaskId)),
    [chatList],
  );

  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(new Set());
  // Collapsed by default: the sidebar opens as a short list of section
  // headers, and you drill into the one you want.
  const [profileOpen, setProfileOpen] = useState(false);
  const [projectsOpen, setProjectsOpen] = useState(false);
  const [schedulerOpen, setSchedulerOpen] = useState(false);
  const [chatsOpen, setChatsOpen] = useState(false);

  // Only auto-expand a project when one of its own chats is active, or when
  // its task's detail page is open — simply navigating into the project's
  // home page (workspaceScope) should leave it folded, including right after
  // the project is created. The task case is what stops a task appearing to
  // vanish the moment you assign it to a project.
  const activeTask = state.activeScheduledTaskId
    ? state.scheduledTasks[state.activeScheduledTaskId]
    : null;
  const relevantProjectId =
    activeChat?.projectId ??
    (state.mainView === "scheduledTaskDetail" ? (activeTask?.projectId ?? null) : null);

  useEffect(() => {
    if (!relevantProjectId) return;
    setExpandedProjects((prev) => {
      if (prev.has(relevantProjectId)) return prev;
      const next = new Set(prev);
      next.add(relevantProjectId);
      return next;
    });
  }, [relevantProjectId]);

  function toggleProjectExpanded(projectId: string) {
    setExpandedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) {
        next.delete(projectId);
      } else {
        next.add(projectId);
      }
      return next;
    });
  }

  const sortedProjects = useMemo(() => sortProjectsForDisplay(projects), [projects]);

  // A project's things live under the project; general things live under the
  // global sections — the same rule already applied to chats.
  const generalTasks = useMemo(
    () =>
      Object.values(state.scheduledTasks)
        .filter((task) => !task.projectId)
        .sort((a, b) => b.createdAt - a.createdAt),
    [state.scheduledTasks],
  );

  const tasksByProject = useMemo(() => {
    const map = new Map<string, ScheduledTask[]>();
    for (const project of projects) {
      map.set(
        project.id,
        Object.values(state.scheduledTasks)
          .filter((task) => task.projectId === project.id)
          .sort((a, b) => b.createdAt - a.createdAt),
      );
    }
    return map;
  }, [projects, state.scheduledTasks]);

  const chatsByProject = useMemo(() => {
    const map = new Map<string, typeof chatList>();
    for (const project of projects) {
      map.set(
        project.id,
        chatList.filter((chat) => chat.projectId === project.id && !chat.scheduledTaskId),
      );
    }
    return map;
  }, [projects, chatList]);


  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-subtle/60 bg-sidebar transition-[width] duration-200 ease-premium",
        collapsed ? "w-[68px]" : "w-[270px]",
      )}
    >
      <div
        className={cn(
          "flex items-center gap-2 px-3 pb-2 pt-2",
          collapsed ? "justify-center" : "justify-end",
        )}
      >
        <Tooltip label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          <IconButton
            label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={toggleSidebar}
          >
            <PanelLeft className="h-[18px] w-[18px]" />
          </IconButton>
        </Tooltip>
      </div>

      {/* The single primary entry point, set off from the browsing lists
          below. Tasks and projects are created from their own sections. */}
      <div
        className={cn(
          // No border here: the rule that separates this from the lists below
          // lives on the Projects block instead, so all three sit inside the
          // same padded column and line up with each other.
          "flex flex-col gap-0.5 px-3 pb-6 pt-2",
          collapsed && "items-center",
        )}
      >
        <SidebarActionRow
          icon={<SquarePen className={cn("h-[18px] w-[18px]", !collapsed && "text-secondary")} />}
          label="New chat"
          collapsed={collapsed}
          onClick={newChat}
        />
      </div>

      {/* Everything below scrolls as one region, so a long Projects list can
          never crowd the chats out of view. Both groups collapse, so a long
          list of either can be folded away rather than scrolled past.
          SIDEBAR SCROLLBAR-GUTTER CORRECTION: `[scrollbar-gutter:stable]`
          permanently reserves the scrollbar's own width in this
          container's layout — whether or not a scrollbar is actually
          rendered right now (e.g. expanding Chats pushes total content
          past the viewport). Without this, expanding Chats could make the
          scrollbar appear, silently shrinking every row's available width
          in this SAME shared scroll region (Projects/Scheduler headers
          included, since all three sections live in one scroll container)
          and visibly shifting every chevron left. One CSS property on the
          one real scroll container — no per-row compensation, no JS
          measurement, no state-dependent margin. */}
      {!collapsed && (
        <div className="anim-label-in min-h-0 flex-1 overflow-y-auto px-3 pb-3 [scrollbar-gutter:stable]">
          {/* Each section owns equal padding above and below its own content
              (py-1), so the rule sits the same distance from the row on both
              sides. Putting the lower gap on the *next* section instead made
              it asymmetric. */}
          <div className="border-t border-subtle/60 py-1">
            <SidebarSectionHeader
              label="Projects"
              icon={<Folder className={SECTION_ICON_CLASS} />}
              expanded={projectsOpen}
              onToggle={() => setProjectsOpen((open) => !open)}
              actions={
                <>
                  <Tooltip label="All projects">
                    <IconButton
                      label="All projects"
                      size="sm"
                      onClick={openAllProjects}
                      className={SECTION_ACTION_CLASS}
                    >
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    </IconButton>
                  </Tooltip>
                  <CreateProjectDialog
                    trigger={
                      <IconButton label="New project" size="sm" className={SECTION_ACTION_CLASS}>
                        <Plus className="h-3.5 w-3.5" />
                      </IconButton>
                    }
                  />
                </>
              }
            />
            {projectsOpen &&
              (sortedProjects.length === 0 ? (
                <p className={SECTION_EMPTY_CLASS}>No projects yet</p>
              ) : (
                <div className="mt-1 flex flex-col gap-0.5">
                  {sortedProjects.map((project) => (
                    <ProjectRow
                      key={project.id}
                      project={project}
                      chats={chatsByProject.get(project.id) ?? []}
                      tasks={tasksByProject.get(project.id) ?? []}
                      expanded={expandedProjects.has(project.id)}
                      onToggleExpand={() => toggleProjectExpanded(project.id)}
                      isActiveWorkspace={
                        state.workspaceScope.type === "project" &&
                        state.workspaceScope.projectId === project.id
                      }
                      activeChatId={activeChat?.id ?? null}
                      onSelectChat={selectChat}
                    />
                  ))}
                </div>
              ))}
          </div>

          <div className="border-t border-subtle/60 py-1">
            <SidebarSectionHeader
              label="Scheduler"
              icon={<Clock className={SECTION_ICON_CLASS} />}
              expanded={schedulerOpen}
              onToggle={() => setSchedulerOpen((open) => !open)}
              actions={
                <>
                  <Tooltip label="All tasks">
                    <IconButton
                      label="All tasks"
                      size="sm"
                      onClick={openScheduledTasks}
                      className={SECTION_ACTION_CLASS}
                    >
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip label="New task">
                    <IconButton
                      label="New task"
                      size="sm"
                      onClick={() => startScheduledTaskSetup()}
                      className={SECTION_ACTION_CLASS}
                    >
                      <Plus className="h-3.5 w-3.5" />
                    </IconButton>
                  </Tooltip>
                </>
              }
            />
            {schedulerOpen &&
              (generalTasks.length === 0 ? (
                <p className={SECTION_EMPTY_CLASS}>No scheduled tasks yet</p>
              ) : (
                <div className="mt-1 flex flex-col gap-0.5">
                  {generalTasks.map((task) => (
                    <SidebarTaskRow key={task.id} task={task} />
                  ))}
                </div>
              ))}
          </div>

          <div className="border-t border-subtle/60 py-1">
            <SidebarSectionHeader
              label="Chats"
              icon={<SquarePen className={SECTION_ICON_CLASS} />}
              expanded={chatsOpen}
              onToggle={() => setChatsOpen((open) => !open)}
            />
            {chatsOpen && (
              <div className="mt-1 flex flex-col gap-0.5">
                {/* POST-5.1 B4C correction pass — the GLOBAL saved-chat
                    list request state, distinct from any one chat's own
                    lazy history load. "Loading chats…" only replaces the
                    empty state while nothing is known yet — it must never
                    read as "you have no saved chats" during a real
                    network round trip. */}
                {state.savedChatsHydrationStatus === "loading" && generalChats.length === 0 && (
                  <p className={SECTION_EMPTY_CLASS}>Loading chats…</p>
                )}
                {state.savedChatsHydrationStatus === "error" && (
                  <div className="flex items-center justify-between gap-2 rounded-lg px-2.5 py-1.5">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-tertiary">
                      Couldn&rsquo;t load saved chats
                    </p>
                    <button
                      type="button"
                      onClick={retrySavedChats}
                      className="shrink-0 rounded px-1 text-[11px] font-medium text-accent transition-colors duration-150 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                    >
                      Retry
                    </button>
                  </div>
                )}
                {state.savedChatsHydrationStatus === "loaded" && generalChats.length === 0 && (
                  <p className={SECTION_EMPTY_CLASS}>No chats yet</p>
                )}
                {generalChats.map((chat) => (
                  <SidebarChatRow
                    key={chat.id}
                    chat={chat}
                    active={activeChat?.id === chat.id}
                    onSelect={() => selectChat(chat.id)}
                    showPinIndicator
                    showChatIcon
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {collapsed && <div className="flex-1" />}

      <div
        className={cn(
          "flex gap-1 border-t border-subtle/60 px-3 py-3",
          collapsed ? "flex-col items-center" : "flex-col",
        )}
      >
        <SidebarFooterButton
          icon={<Settings className="h-[18px] w-[18px]" />}
          label="Settings"
          collapsed={collapsed}
          onClick={() => openSettings()}
        />
        <SidebarFooterButton
          icon={<CircleHelp className="h-[18px] w-[18px]" />}
          label="Help"
          collapsed={collapsed}
          disabled
        />
        <div className={cn("flex w-full items-center gap-1", collapsed && "w-auto flex-col")}>
          <MenuRoot>
            <MenuTrigger asChild>
              {collapsed ? (
                <Tooltip label="Account">
                  <IconButton label="Account">
                    <User className="h-[18px] w-[18px]" />
                  </IconButton>
                </Tooltip>
              ) : (
                <button
                  type="button"
                  className="anim-label-in flex flex-1 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-base text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                >
                  <User className="h-[18px] w-[18px]" />
                  Account
                </button>
              )}
            </MenuTrigger>
            <MenuContent align="start">
              <MenuItem
                icon={<User className="h-4 w-4 text-secondary" />}
                onSelect={() => setProfileOpen(true)}
              >
                Profile
              </MenuItem>
              <MenuSeparator />
              <MenuItem disabled>Log out</MenuItem>
            </MenuContent>
          </MenuRoot>
          <SidebarBackToSiteLink />
        </div>
      </div>

      <SettingsModal
        open={state.settingsModal.open}
        onOpenChange={(next) => (next ? openSettings() : closeSettings())}
      />

      <ProfileDialog open={profileOpen} onOpenChange={setProfileOpen} />
    </aside>
  );
}
