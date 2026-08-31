UX_SPEC.md

Enterprise AI Assistant — UI/UX Specification

1. UX Objective

Create an interface that makes a governed enterprise AI assistant feel as easy to use as a consumer conversational AI product.

The user should not feel that they are operating:

a knowledge management platform

an analytics dashboard

an integration console

a workflow orchestration tool

Those capabilities exist beneath a simple conversational surface.

2. Primary Navigation Model

The application uses a persistent left sidebar and a central workspace.

Conceptual desktop layout:

┌──────────────────────┬───────────────────────────────────────────────┐
│ Left Sidebar         │ Main Workspace                                │
│                      │                                               │
│ + New Chat           │                                               │
│                      │                                               │
│ Projects             │               Empty / Chat                    │
│  ▸ Project Alpha     │                                               │
│  ▸ Project Beta      │                                               │
│                      │                                               │
│ Chats                │                                               │
│  Chat title          │                                               │
│  Chat title          │                                               │
│                      │                                               │
│                      │                                               │
│ Settings             │                                               │
│ Help                 │                                               │
│ Account              │                                               │
└──────────────────────┴───────────────────────────────────────────────┘

The proportions should be refined during implementation.

Do not treat this ASCII layout as a pixel specification.

3. Default Application State

3.1 On launch

The user opens the application.

The application should immediately show:

left sidebar

empty main conversation area

active prompt composer

The user does not select a mode first.

The user does not click "New Chat" first.

The user does not see an onboarding dashboard first.

The prompt input should be ready for interaction.

3.2 Visual priority

Visual priority should be:

Prompt composer

Calm empty space

Navigation

Secondary controls

The page should not compete for attention.

4. Empty Chat State

The empty state should feel intentional rather than unfinished.

Possible elements:

a small neutral product mark

optional short greeting

prompt composer

Avoid:

large hero headline

feature grid

suggested prompts covering the page

multiple cards

recent activity dashboard

excessive explanatory copy

If a greeting is used, keep it short.

The composer remains the focal point.

5. Prompt Composer

5.1 Structure

The composer includes:

+ button

multiline text input

thinking effort selector

skill selector

microphone

send button

The exact arrangement can be proposed by Claude, but it should avoid looking like a dense toolbar.

5.2 Text input

Behavior:

placeholder is concise

Enter submits

Shift+Enter creates a new line

multiline input expands within sensible limits

composer remains anchored and stable

after send, the composer remains available

5.3 "+" menu

Clicking + opens a compact contextual menu.

Items:

Add files

Add folders

Add connector

Prototype behavior:

Add files

Mock:

file picker behavior may be simulated

selected files can appear as attachment chips

Add folders

Mock:

folder selection may be simulated

folder appears as a compact attachment/context chip

Add connector

Open a compact selector containing globally available mock connectors.

Do not make this a full settings management experience.

Connector management belongs in Settings.

5.4 Thinking effort

A compact selector.

Example conceptual values:

Standard

Deeper

Claude may propose different naming, but:

keep the control understated

do not create a visually heavy "AI configuration" surface

current selection should be understandable

This is UI only.

5.5 Skill selector

The composer exposes the skill for the current chat.

Rules:

skills are globally defined but activated per chat

zero or one skill may be active in the current chat

never multiple active skills

control should open a selector/popover

selecting a new skill replaces the previous skill in the current chat

"No skill" should be possible

skill choice does not change project configuration

skill choice does not automatically affect other chats

A Project does not have or inherit a Skill.

If the user starts a new chat inside a project, that chat begins with no active skill unless the user selects one.

5.6 Microphone

Microphone is a visible action.

In the prototype:

no real transcription required

clicking can trigger a mock recording state if useful

recording state should be visually clear and easy to cancel

5.7 Send

Send should:

be visually obvious without dominating the composer

transition disabled/enabled based on input state

submit the prompt

6. First Prompt Behavior

When the user submits the first prompt:

User message enters conversation.

Empty state transforms into active conversation state.

A mock assistant thinking/loading state appears.

Mock grounded response renders.

Chat receives an automatic title or mock title.

Chat appears in Chat History.

Composer remains usable.

The experience should feel continuous.

Avoid a page reload or visible route jump.

7. Active Conversation State

7.1 Message layout

Conversation content should prioritize readability.

Do not over-constrain messages into chat bubbles if that reduces readability.

The user and assistant should be visually distinguishable through spacing, alignment, surface treatment, or typography.

Avoid excessive borders.

7.2 Assistant response

The mock assistant response may include:

prose

bullets

headings

citations

attachment references

action confirmation prompts

Responses should support comfortable reading width.

7.3 Citation behavior

Mock citations should be interactive.

Possible interaction:

inline citation chip

click opens source popover/drawer

source preview shows:

document title

version

section

relevant excerpt

The citation UI should reinforce trust without making every answer look academic.

Global approved baseline knowledge is system/admin-managed and will later be supplied from an external approved source such as a Google Cloud Storage bucket.

Do not expose global baseline administration in the end-user UI.

8. Left Sidebar

8.1 General behavior

Sidebar should:

remain stable

support collapse

use subtle section hierarchy

avoid excessive separators

use contextual actions on hover where practical

8.2 New Chat

New Chat is a primary navigation action.

Behavior:

clears the active conversation view

returns user to empty prompt state

does not require confirmation unless current draft content would be lost

does not create an empty history item

resets chat-specific skill selection to no active skill

A chat is created only after a prompt is actually sent.

8.3 Projects

Projects are shown as a section.

Each project:

can expand/collapse

may expose recent project chats underneath

can be opened into the project workspace

may expose a contextual action menu

Avoid showing every project configuration control directly in the sidebar.

8.4 Chat history

Chat history:

chronological

compact

scannable

titles truncate elegantly

Per-chat contextual actions:

Pin / Unpin

Rename

Move to project

Delete

Potential future actions may be added later.

Pinned chats can appear above recent history or in a small pinned subsection.

8.5 Bottom actions

Bottom section:

Settings

Help

Account / Logout

Keep this visually quieter than primary navigation.

9. Project Experience

9.1 Project entry

Opening a project changes the current workspace context.

The user should understand:

project name

that new chats will inherit project instructions, project approved knowledge, and enabled project connectors

which connectors are enabled

This context should be visible but subtle.

Do not create a large status dashboard.

Skills are not project configuration.

9.2 Project structure

A project contains:

Instructions

Knowledge

Connectors

Files

Chats

These may be represented through:

tabs

segmented navigation

side panel

contextual project settings

Claude should propose the least cluttered approach.

Do not add a Skill section to the Project.

9.3 Project Instructions

Project Instructions UI should support:

viewing instructions

editing instructions in the prototype

clear indication that they apply to all project chats

Avoid turning this into a developer system-prompt editor.

Use understandable product language.

9.4 Project Knowledge

Project Knowledge UI shows approved project documents.

Mock document row can include:

title

type

version

status

updated date

source/owner where helpful

The key semantic state is approval.

Approved documents are considered authoritative.

Avoid building document governance workflow in this UI phase.

Global approved baseline knowledge is separate and system/admin-managed outside the end-user prototype.

9.5 Project Connectors

Projects select from globally configured connectors.

Project connector UI should allow:

enable/disable for project

understand connector state

understand whether it is read-only or read/write in concept

Do not configure credentials here.

Credentials/global connection management belongs in Settings.

9.6 Project Files

Project Files are workspace files.

This is separate from approved baseline Knowledge.

The UI should not imply every file is authoritative.

Possible states:

File

Approved knowledge

Temporary/general project file

Keep the distinction simple.

10. Settings Modal

10.1 Opening

Clicking Settings opens an overlay/modal.

The user remains conceptually inside the current workspace.

The modal may be large enough for structured navigation but should not feel like leaving the product.

10.2 Settings navigation

Initial settings:

Usage

Connectors

Skills

Do not add global Knowledge management to Settings.

Global approved baseline knowledge is system/admin-managed and outside the scope of this user-facing UI prototype.

Use simple left-side or top-level navigation depending on modal proportions.

11. Usage UX

Usage should be understandable at a glance.

Possible summary:

current period usage

remaining allowance

requests/messages

model consumption

connector use

Potential simple visualization may be used.

Avoid:

dense tables

admin reporting aesthetic

unnecessary financial complexity

This is a personal/product usage view, not an enterprise BI screen.

12. Connectors UX

12.1 Connector catalogue

Each connector entry should communicate:

icon

name

purpose

status

capability

action

Example:

Microsoft Outlook
Email search and actions
Connected
Read + Write
[Manage]

Use better visual treatment than this conceptual example.

12.2 States

Required mock states:

Connected

Not connected

Permission required

Error

Optional:

Disabled

Reconnect needed

12.3 Connector detail

Manage action may open a detail modal/panel.

Possible information:

connection state

permissions

available actions

account

reconnect

disconnect

No real connector behavior required.

13. Skills UX

13.1 Skill catalogue

Skills should feel like reusable working modes, not plugins.

Each skill may show:

name

short description

edit action

select/use affordance when invoked from a chat context

Settings manages the skill library.

Settings should not show a single globally active skill.

13.2 Create Skill

Create Skill flow can ask for:

name

description

instruction text

Keep it simple.

No complex builder is required.

13.3 Edit Skill

Skill editor should provide a clean instruction field.

Do not overload it with:

prompt engineering terminology

temperature

token settings

model internals

advanced parameters

The user is defining behavior, not configuring a model.

13.4 Skill exclusivity

Only one skill can be active in a chat at a time.

If a user selects another skill in the current chat:

new skill becomes active for that chat

previous skill becomes inactive for that chat

No multi-select UI.

Changing a skill in one chat does not change:

another chat

a project

the global skill library

14. Mock External Action Confirmation

The prototype should demonstrate future connector actions.

Example:

User:

Send this summary to John.

Assistant mock response:

presents proposed action

shows relevant details

requires explicit confirmation

Conceptual dialog/card:

Send email?

To: John
Subject: Incident Summary

[Cancel] [Approve & Send]

This is a UX prototype only.

Nothing is actually sent.

15. Menus and Popovers

Use menus/popovers for:

chat contextual actions

project contextual actions

+ composer actions

skill selection

thinking effort

account menu

Guidelines:

concise

aligned with trigger

keyboard accessible

no oversized floating panels for simple decisions

16. Modal Behavior

Modals should:

have clear close affordance

close on Escape where appropriate

trap focus appropriately

avoid nested modal stacks when possible

preserve current workspace beneath

Settings is the primary large modal.

Smaller tasks may use dialog or popover.

17. Hover and Focus States

Every interactive control should have:

hover feedback

focus feedback

pressed/selected state where relevant

Avoid overly animated hover effects.

Micro-interactions should reinforce responsiveness, not attract attention for their own sake.

18. Motion

Motion should be restrained.

Good uses:

sidebar collapse

menu open/close

modal appearance

message arrival

loading transition

attachment addition

selection transition

Avoid:

dramatic page transitions

bouncing controls

background animations

continuous decorative motion

19. Empty / Loading / Error States

Prototype should anticipate:

Empty

no projects

no chats

no connectors

no skills

no usage history

Loading

assistant generating

connectors loading

source preview loading

Error

connector unavailable

failed mock action

missing approved answer

Keep error language clear and calm.

20. Strict Knowledge "No Answer" State

A future assistant must be able to say:

The approved knowledge available to this workspace does not contain enough information to answer this.

The UX should treat this as a valid outcome, not as a failure.

Possible response structure:

concise explanation

sources searched or knowledge scope

optional suggestion to provide an approved source

Do not make the assistant appear broken.

The global baseline itself is not managed by the user in this UI prototype.

21. Context Indicators

The interface may need to communicate:

active project

active skill for the current chat

temporary attachments

selected/available connectors

grounded source usage

These indicators must remain subtle.

Do not create a permanent "context control center" above every chat.

Prefer:

compact labels

chips

composer-level indicators

contextual popovers

Skill indicators must clearly belong to the current chat, not the Project.

22. Visual Hierarchy

Primary:

conversation

composer

Secondary:

navigation

active project context

Tertiary:

configuration and metadata

This hierarchy should remain consistent across the application.

23. Typography and Spacing

Typography should:

prioritize readability

support long AI responses

create subtle hierarchy

avoid overly decorative styles

Spacing should:

be generous

follow a consistent scale

reduce visual crowding

allow controls to breathe

Do not rely on borders to solve spacing problems.

24. Color

No specific palette is mandated in this document.

Claude should propose a restrained system.

Requirements:

accessible contrast

neutral surfaces

accent color used intentionally

semantic colors for success/warning/error

dark mode should be structurally possible

Avoid overusing brand color.

25. Icons

Use a coherent icon family.

Icons should:

support recognition

not replace necessary text where ambiguity exists

remain visually quiet

use consistent size/stroke weight

26. Desktop First

Primary target:

desktop web application

Important widths:

large desktop

common laptop

narrower desktop/tablet landscape

At narrower widths:

sidebar may collapse

labels may simplify

composer remains full-featured

settings modal adapts

Mobile is not a first-milestone priority.

27. Accessibility

Minimum UX expectations:

keyboard navigation

visible focus

semantic interactions

tooltips for ambiguous icons

Escape closes dialogs

screen-reader labels for icon-only buttons

reasonable target sizes

contrast compliance

28. First Build Milestone

The first implementation milestone should contain only enough to perfect the opening interaction.

Required:

app shell

sidebar

empty chat

prompt composer

+ menu

thinking selector

chat-level skill selector

microphone mock state

send behavior

mock first assistant response

automatic creation of chat history item

sidebar collapse

core hover/focus states

light/dark-ready visual foundations

Do not rush into Settings/Projects before this shell is visually and behaviorally strong.

29. Quality Gate for Milestone 1

Before continuing:

Opening experience

Does the user immediately know they can type?

Is the prompt visually dominant without being oversized?

Is there unnecessary content competing with it?

Sidebar

Does it feel lightweight?

Can users understand New Chat, Projects, and History?

Is collapse behavior polished?

Composer

Are secondary controls discoverable but quiet?

Does the composer remain usable as text grows?

Does it feel coherent rather than assembled from separate buttons?

Is it clear that the selected Skill applies only to the current chat?

Conversation transition

Does the empty state transform smoothly into chat?

Does chat history update naturally?

Does the user remain oriented?

Overall

Does it feel like a product rather than an AI-generated dashboard?

Is spacing consistent?

Is typography intentional?

Is the visual system restrained?

Only after this passes should implementation expand to the rest of the product.

30. Later UI Milestones

After Milestone 1:

Milestone 2

fuller chat history behavior

pinning

rename

move chat

contextual menus

Milestone 3

project workspace

instructions

project knowledge

project connectors

project files

Milestone 4

Settings shell

Usage

Connectors

Skills library

Milestone 5

action confirmation

citation/source preview

error/empty states

responsive polish

accessibility refinement

31. Final UX Rule

Whenever adding a feature, ask:

Does the user need to see this all the time?

If the answer is no, prefer:

popover

menu

modal

contextual state

progressive disclosure

The product should become more capable without looking more complicated.