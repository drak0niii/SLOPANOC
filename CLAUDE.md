CLAUDE.md

Project Purpose

This repository is the starting point for the UI/UX prototype of an enterprise AI assistant.

The product should feel as immediate, calm, and intuitive as a modern conversational AI workspace, while remaining visually and structurally its own product.

This phase is intentionally limited to UI/UX prototyping.

Non-Negotiable Scope

Build now

Application shell

Left navigation/sidebar

Empty chat landing state

Prompt composer

Mock conversation states

Project UI

Settings modal

Usage UI

Connectors management UI

Skills management UI

Responsive desktop behavior

Interaction states, overlays, menus, hover states, loading states, and empty states

Mock data where needed

Do NOT build yet

AI APIs

LLM integrations

RAG

embeddings

vector databases

authentication

authorization backends

document ingestion

real connectors

email integrations

Teams integrations

databases

backend services

production telemetry

billing integrations

real usage metering

real file storage

real action execution

If a UI interaction requires one of the above, simulate it with mock data and local state.

Product Principle

The default experience must be:

Open application → type prompt → send → receive response

The user must NOT need to click "New Chat" before entering the first prompt.

The prompt composer is already present and usable when the application opens.

The first submitted prompt automatically creates the conversation and causes it to appear in chat history.

Design Direction

The interface should be:

calm

minimal

premium

highly polished

desktop-first

spacious

low-noise

fast to understand

content-first

conversational rather than dashboard-like

familiar without being a pixel-for-pixel clone of ChatGPT

Prefer:

whitespace

subtle hierarchy

progressive disclosure

restrained use of borders

contextual controls

menus and overlays instead of permanent clutter

soft visual transitions

strong alignment and spacing discipline

Avoid:

enterprise dashboard aesthetics

excessive cards

dense toolbars

permanent panels for secondary actions

unnecessary labels

duplicated controls

decorative complexity

gradients used only for visual effect

oversized hero content

onboarding dashboards

landing-page marketing patterns inside the product

"Get Started" screens before the prompt

Core Information Architecture

Global application

Contains:

global approved baseline knowledge

global connector catalogue

global skill catalogue

settings

usage

account/help/logout controls

cross-project chat history

Global approved baseline knowledge

Global approved baseline knowledge exists but is system/admin-managed and is not managed by the end user in this UI prototype.

The baseline knowledge will later be supplied from an external approved source such as a Google Cloud Storage bucket.

For this UI/UX phase:

do not build a global knowledge-management screen

do not build bucket configuration

do not build ingestion

do not build approval workflows

use mock source/citation data only where the UX needs to represent grounded answers

Project

A Project is a workspace/container.

A project can contain:

project instructions

project approved baseline documents

selected global connectors

project chats

project files

New chats started inside a project inherit the project context.

Skills are NOT configured at project level.

Chat

A chat:

belongs either to the general workspace or a project

maintains conversation history

can receive temporary files/folders from the prompt composer

can have zero or one active skill

is automatically created after the first user prompt is submitted

Skills apply per chat only.

Knowledge Behavior to Represent in the UX

The intended future assistant behavior is strict grounding.

For knowledge-grounded questions:

use approved baseline documents

do not silently supplement missing knowledge from general model knowledge

if the answer is not available in approved knowledge, say so clearly

make source provenance visible in the response experience

The UI prototype should anticipate citations/source references, but no real retrieval system is needed yet.

The global approved baseline is system/admin-managed and will later come from an approved external source such as Google Cloud Storage.

Connector Model

Connectors are managed globally.

Examples may include:

Email

Microsoft Teams

SharePoint

ticketing systems

monitoring systems

file repositories

APIs

Projects select which globally available connectors are enabled for that workspace.

Future connectors can support:

read/retrieve/search

action/write/update/send

For the future product:

read-only actions may happen directly

external state-changing actions should require explicit confirmation

In this UI phase:

use mock connector data

simulate connected/disconnected/error states

simulate an approval confirmation pattern for write actions

do not perform real actions

Skill Model

Skills are managed globally.

A skill is a reusable behavioral/task instruction set that changes how the assistant approaches a request.

Examples:

Incident Investigator

Executive Communication

NOC Troubleshooting

Concise Writing

Rules:

skills apply to chats, not projects

only ONE skill may be active in a chat at a time

a chat may have no active skill

selecting a new skill for a chat replaces the previously active skill in that chat

project instructions and skill instructions are separate concepts

project instructions describe the workspace/context

the skill describes how the assistant should perform the task within the current chat

The UI must not imply that a skill is inherited from, stored on, or configured for a project.

The UI must not imply that multiple skills can be active simultaneously.

Main Navigation

The left sidebar should include:

Top

New Chat

Projects

expandable/collapsible projects

project chats accessible below the project

contextual project actions where appropriate

Chat History

recent chats

pinning

rename

contextual options

project association where relevant

Bottom

Settings

Help

Account / Logout

The sidebar should feel lightweight and navigational, not like a management console.

Main Chat Area

The main area has two primary states.

Empty chat state

When the app opens:

prompt composer is immediately visible

composer is the visual focus

no dashboard

no onboarding cards

no forced project selection

no extra step before typing

keep the empty canvas calm

Active conversation state

After sending the first prompt:

user prompt appears in the conversation

mock assistant response appears

chat is now represented in history

composer remains available

source/citation affordances can be mocked

conversation content becomes the visual focus

Prompt Composer

The composer should support the following UI affordances:

"+" button

text input

thinking effort selector

skill selector

microphone

send button

The "+" menu should expose:

Add files

Add folders

Add connector

Important:

this is UI only

use mocked attachment behavior

use mocked connector selection

do not implement real uploads or integrations unless they are purely local prototype behavior

The composer should remain visually simple.

Secondary controls should not overpower the text input.

The skill selector applies only to the current chat.

Settings

Settings should open as a modal/overlay rather than forcing navigation to a standalone dashboard.

Initial sections:

Usage

Connectors

Skills

The structure should be extensible to future account/preferences/security sections, but those are not required now.

Do not add a global Knowledge section in the user-facing Settings UI during this phase.

Global approved baseline knowledge is system/admin-managed outside this prototype.

Usage UI

Usage is mock UI only.

It may show:

usage summary

model/request consumption

time-range breakdown

limits/allowance

connector-related usage where useful

Do not invent complex enterprise billing flows.

The goal is clarity, not analytics density.

Connectors UI

The connectors section should allow the user to understand:

which connectors exist

whether each is connected

connection health/state

what it is for

whether it supports read-only or read/write behavior

an obvious connect/disconnect/manage action

Use realistic mock states:

Connected

Not connected

Permission required

Error

Do not build real OAuth or API integrations.

Skills UI

The skills section should support:

browse skills

create a skill

edit a skill

view skill name

view short description

view/edit instruction text

Skills are defined and managed globally, but activation happens per chat.

Do not create a global "active skill" state in Settings.

Only one skill may be active in any given chat at a time.

Project UI

A project should expose project-specific configuration without becoming a dashboard.

Project configuration may include:

Instructions

Knowledge

Connectors

Files

Chats

Do NOT include Skill as a project configuration section.

Skills are selected per chat only.

Prefer a calm, focused layout.

Global management belongs in Settings.

Project screens only select/configure what the project uses.

UX Principles

Immediate entry

The user should never be blocked from typing a prompt when the application first opens.

Progressive disclosure

Show secondary options only when needed.

Context clarity

The user should be able to understand:

whether they are in a general chat or project

which project is active

which skill is active in the current chat

which connectors are available

whether sources were used

Do this subtly.

Low cognitive load

Do not present every available feature at once.

Familiar interaction patterns

Use conventions users already understand:

sidebar navigation

contextual menus

modal settings

inline attachments

composer actions

hover states

tooltips where needed

Explicit action confirmation

Prototype a clear confirmation step before mocked state-changing external actions.

Implementation Discipline

Before writing implementation code:

Inspect the repository and read:

CLAUDE.md

docs/PRODUCT.md

docs/UX_SPEC.md

Propose the component architecture.

Propose the page/state architecture.

Identify reusable components.

Identify the minimum state model required for the prototype.

Present an implementation plan.

Stop and wait for approval before implementation if explicitly requested by the user.

Do not create a large number of screens at once.

Do not install packages, initialize a framework, scaffold an application, or choose the final technology stack until the architecture proposal has been approved.

The preferred build order is:

App shell

Empty chat landing state

Prompt composer

Sidebar

Active conversation state

Project workspace

Settings modal shell

Usage

Connectors

Skills

Responsive and interaction polish

The first major quality gate is the opening experience.

Do not move on merely because the opening screen is functional.

The shell, proportions, spacing, typography, composer, sidebar, hover states, and visual hierarchy should feel production-quality before expanding the prototype.

Component Philosophy

Prefer reusable, composable components.

Likely conceptual components include:

AppShell

Sidebar

SidebarSection

ProjectTree

ChatHistory

ChatRow

MainConversation

EmptyChat

MessageList

Message

PromptComposer

ComposerPlusMenu

ThinkingEffortSelector

SkillSelector

AttachmentChip

SourceCitation

SettingsModal

SettingsNavigation

UsagePanel

ConnectorsPanel

ConnectorRow/Card

SkillsPanel

SkillEditor

ProjectSettings

ConfirmationDialog

These are conceptual suggestions, not mandatory implementation names.

Claude should propose the final architecture before implementation.

Prototype Data

Use clearly separated mock data for:

projects

chats

connectors

skills

usage

messages

files

citations

Do not hard-code large amounts of mock content directly into presentation components when avoidable.

Mock skill assignment belongs to chat data, not project data.

Accessibility

The prototype should observe good accessibility fundamentals:

keyboard-accessible controls

visible focus states

semantic buttons

appropriate labels

sufficient contrast

dialogs that can be closed predictably

menus that support keyboard navigation where practical

Responsive Behavior

Desktop is the priority.

The UI should nevertheless degrade gracefully when width is reduced.

At smaller widths:

sidebar may collapse

secondary metadata may hide

composer must remain usable

modals should remain accessible

conversation content should maintain readable line lengths

Do not spend disproportionate effort on mobile during the first UI milestone.

Visual Fidelity Rule

Do not copy ChatGPT pixel-for-pixel.

The goal is to borrow the successful interaction model:

immediate prompt

conversation-first workspace

restrained navigation

contextual controls

progressive disclosure

Develop an original visual system through:

typography

spacing

surface treatment

radius

icon choices

menu treatment

interaction motion

hierarchy

Decision Rule

When uncertain between:

showing more information

hiding it until needed

prefer hiding it until needed, unless hiding it makes the user lose important context.

When uncertain between:

adding another screen

using a modal/popover/contextual interaction

prefer the simpler interaction if it remains clear.

When uncertain between:

visually impressive

calm and obvious

prefer calm and obvious.