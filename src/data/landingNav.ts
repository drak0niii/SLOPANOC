import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  BookOpen,
  FileText,
  HelpCircle,
  History,
  ListChecks,
  Lock,
  Map,
  MessagesSquare,
  Plug,
  Rocket,
  ShieldCheck,
  Sparkles,
  Users2,
  Wand2,
  Workflow,
} from "lucide-react";

/**
 * Centralized landing-page navigation config. Labels, copy, and routes here
 * are temporary placeholders — swap them out once real destinations exist.
 * Nothing in the landing components should hardcode this content directly.
 */

export interface NavLinkItem {
  id: string;
  label: string;
  description?: string;
  href: string;
  icon?: LucideIcon;
}

export interface MegaMenuColumn {
  heading: string;
  items: NavLinkItem[];
}

export interface MegaMenuFeatured {
  eyebrow: string;
  title: string;
  description: string;
  ctaLabel: string;
  href: string;
  /** Present only for the Resources menu's image-backed featured card. */
  image?: { src?: string; alt: string };
}

export interface MegaMenuConfig {
  id: string;
  label: string;
  kind: "mega";
  columns: [MegaMenuColumn, MegaMenuColumn];
  featured: MegaMenuFeatured;
}

export type NavMenuConfig = MegaMenuConfig;

export const SOLUTIONS_MENU: MegaMenuConfig = {
  id: "solutions",
  label: "Solutions",
  kind: "mega",
  columns: [
    {
      heading: "Solutions",
      items: [
        {
          id: "conversational-ai",
          label: "Conversational AI",
          description: "Natural, grounded conversations across your workspace.",
          href: "/solutions/conversational-ai",
          icon: MessagesSquare,
        },
        {
          id: "grounded-knowledge",
          label: "Grounded Knowledge",
          description: "Answers sourced from approved baseline documents.",
          href: "/solutions/grounded-knowledge",
          icon: ShieldCheck,
        },
        {
          id: "workflow-automation",
          label: "Workflow Automation",
          description: "Turn recurring tasks into repeatable skills.",
          href: "/solutions/workflow-automation",
          icon: Workflow,
        },
        {
          id: "connector-actions",
          label: "Connector Actions",
          description: "Read and act across the tools your team already uses.",
          href: "/solutions/connector-actions",
          icon: Plug,
        },
      ],
    },
    {
      heading: "Capabilities",
      items: [
        {
          id: "team-workspaces",
          label: "Team Workspaces",
          description: "Projects that keep context, files, and chats together.",
          href: "/solutions/team-workspaces",
          icon: Users2,
        },
        {
          id: "skills-playbooks",
          label: "Skills & Playbooks",
          description: "Reusable behaviors for how the assistant approaches work.",
          href: "/solutions/skills-playbooks",
          icon: Wand2,
        },
        {
          id: "usage-insights",
          label: "Usage Insights",
          description: "Understand consumption across models and teams.",
          href: "/solutions/usage-insights",
          icon: BarChart3,
        },
        {
          id: "enterprise-security",
          label: "Enterprise Security",
          description: "Controls built for regulated environments.",
          href: "/solutions/enterprise-security",
          icon: Lock,
        },
      ],
    },
  ],
  featured: {
    eyebrow: "Featured",
    title: "Grounded answers, every time",
    description: "See how strict grounding keeps every response tied to approved sources.",
    ctaLabel: "Learn more",
    href: "/solutions/grounded-knowledge",
  },
};

export const RESOURCES_MENU: MegaMenuConfig = {
  id: "resources",
  label: "Resources",
  kind: "mega",
  columns: [
    {
      heading: "Learn",
      items: [
        {
          id: "documentation",
          label: "Documentation",
          description: "Guides for setting up projects, skills, and connectors.",
          href: "/resources/documentation",
          icon: BookOpen,
        },
        {
          id: "getting-started",
          label: "Getting Started",
          description: "A short walkthrough of your first workspace.",
          href: "/resources/getting-started",
          icon: Rocket,
        },
        {
          id: "best-practices",
          label: "Best Practices",
          description: "Patterns for grounding, skills, and team rollout.",
          href: "/resources/best-practices",
          icon: ListChecks,
        },
        {
          id: "faq",
          label: "FAQ",
          description: "Answers to common setup and usage questions.",
          href: "/resources/faq",
          icon: HelpCircle,
        },
      ],
    },
    {
      heading: "Explore",
      items: [
        {
          id: "changelog",
          label: "Changelog",
          description: "What shipped recently, in order.",
          href: "/resources/changelog",
          icon: History,
        },
        {
          id: "release-notes",
          label: "Release Notes",
          description: "Details behind each release.",
          href: "/resources/release-notes",
          icon: FileText,
        },
        {
          id: "roadmap",
          label: "Roadmap",
          description: "What's being explored next.",
          href: "/resources/roadmap",
          icon: Map,
        },
        {
          id: "api-reference",
          label: "API Reference",
          description: "Technical reference for integrations.",
          href: "/resources/api-reference",
          icon: Sparkles,
        },
      ],
    },
  ],
  featured: {
    eyebrow: "Featured resource",
    title: "Inside the grounding model",
    description: "A closer look at how citations and provenance stay visible in every answer.",
    ctaLabel: "Read more",
    href: "/resources/grounding-model",
    image: { alt: "Preview of the grounding model resource" },
  },
};

/** Community has no dropdown — it's a single top-level link. */
export const COMMUNITY_LINK: NavLinkItem = {
  id: "community",
  label: "Community",
  href: "/community",
};

export const LANDING_NAV_MENUS: NavMenuConfig[] = [SOLUTIONS_MENU, RESOURCES_MENU];
