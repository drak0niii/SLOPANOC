PRODUCT.md

Enterprise AI Assistant — Product Definition

1. Product Summary

This product is an enterprise conversational AI workspace.

Its defining experience is simple:

Open the application and ask a question immediately.

The interface should feel familiar to users of modern AI assistants, but the product is designed around governed enterprise knowledge, projects, connectors, and reusable behavioral skills.

The initial phase of this product was UI/UX only. A working backend
(Microsoft Teams integration, agents, persistence, approval) has since been
implemented for the Teams domain described here — see README.md and
docs/AGENT_CONTRACT.md for the current architecture. The product objects
and UI surfaces below (Projects, global Knowledge, Connectors beyond Teams,
Skills) remain product/UX intent, not yet implemented — see README.md's
"Current capabilities" and "Roadmap" for what exists today.

2. Product Goal

Create a conversational workspace that combines:

immediate AI interaction

approved enterprise knowledge

project-specific context

globally managed connectors

globally managed skills

conversation history

future read/action capabilities

The product must remain simple even as capability grows.

3. Core User Promise

The product should answer the following user expectation:

"I open it, ask what I need, and it understands the approved context I am allowed to use."

The user should not need to understand retrieval systems, knowledge indexes, connector architecture, or AI orchestration.

4. Primary User Journey

Start a chat

User opens the app.

User immediately sees the prompt composer.

User types a prompt.

User sends the prompt.

The assistant returns a grounded response.

The chat is automatically created and appears in history.

There is no "create chat" step before the first prompt.

New Chat remains available as a way to leave the current conversation and return to the clean empty prompt state.

5. Product Objects

5.1 Global Knowledge

The product has an organisation-level approved baseline knowledge set.

This content is considered authoritative for grounded enterprise answers.

Global approved baseline knowledge is system/admin-managed and is not managed by the end user in this UI prototype.

The approved baseline will later be supplied from an external approved source, expected to be a Google Cloud Storage bucket or equivalent controlled source.

The UI prototype should not include:

global knowledge administration

bucket configuration

document ingestion

document approval workflows

The UI may represent grounded source provenance using mock citations.

5.2 Project

A Project is a workspace/container, not simply a folder.

A project can contain:

Project instructions

Project approved baseline documents

Selected global connectors

Project chats

Project files

New chats inside a project inherit the project's configuration.

Skills are not configured at project level.

5.3 Chat

A chat is a conversation.

It contains:

conversation messages

current conversation context

temporary user attachments

references to the active project when applicable

zero or one active skill

A chat is created automatically after the user's first submitted prompt.

Skill selection is specific to the chat.

A skill selected in one chat does not become a project setting and does not automatically apply to other chats.

5.4 Connector

A Connector gives the assistant access to an external system.

Examples:

Email

Microsoft Teams

SharePoint

ticketing platforms

monitoring systems

file stores

enterprise APIs

Connectors are configured and managed globally.

Projects can select which available connectors they use.

Future connectors may support:

Read/Search/Retrieve

Write/Send/Update/Act

External state-changing actions should require explicit confirmation.

5.5 Skill

A Skill is a reusable instruction set that changes how the assistant behaves or performs a task.

Examples:

Incident Investigator

NOC Troubleshooting

Executive Communication

Concise Writing

Skills are created and managed globally.

Skills are activated per chat only.

Rules:

a chat may have no active skill

a chat may have one active skill

a chat may never have more than one active skill

selecting another skill replaces the currently active skill for that chat

projects do not define, store, or inherit an active skill

A Skill is different from Project Instructions:

Project Instructions describe context, constraints, terminology, or workspace rules.

A Skill describes the behavior/method the assistant should apply in the current chat.

5.6 Project Instructions

Project Instructions are persistent context for a project.

Examples:

terminology

scope

customer context

operational rules

time zone assumptions

local escalation logic

project-specific constraints

Project instructions are always active for chats in the project.

6. Knowledge Policy

For questions that depend on governed enterprise knowledge, the future assistant should operate in strict grounding mode:

Use approved baseline documents.

Prefer the approved global and project baseline.

Do not invent missing enterprise facts.

Do not silently fill a missing answer from generic model knowledge.

If the approved sources do not contain the answer, say that the information is not available in the approved knowledge.

The user should be able to see the source basis for grounded answers.

7. Source Provenance

The response experience should anticipate citations.

A future grounded response may reference:

document title

document ID

version

section

relevant excerpt

Example concept:

[MOP-CORE-014 · v2.1 · §4.3]

Clicking a citation may eventually open a source preview.

The UI prototype should represent this concept using mock sources.

8. Information Architecture

Global

New Chat

Projects

Chat History

system/admin-managed Global baseline knowledge

Settings

Usage

Connectors

Skills

Help

Account / Logout

Global baseline knowledge exists in the product architecture but is not managed by the end user through this UI prototype.

Project

Project overview/context

Instructions

Knowledge

Connectors

Files

Chats

Chat

Conversation

Prompt composer

temporary attachments

current active skill indicator when relevant

project context indicator when relevant

source citations when relevant

9. UI Surfaces in Scope

The UI prototype should eventually cover:

Main application shell

Empty chat state

Active conversation state

Prompt composer

Sidebar

Project workspace

Project configuration

Settings modal

Usage section

Connectors section

Skills section

Menus/popovers/dialogs

Mock confirmation for state-changing actions

A global Knowledge management screen is not in scope.

10. Prompt Composer

The prompt composer is a central product surface.

It includes:

text input

+

thinking effort selector

skill selector

microphone

send

The + menu eventually supports:

Add files

Add folders

Add connector

In the prototype these may be mocked.

The composer should feel powerful without becoming visually dense.

Skill selection from the composer applies to the current chat only.

11. Sidebar

The left sidebar should support:

Top

New Chat

Projects

project list

expandable project hierarchy

project chat access

Chat History

chronological chat list

pinning

rename

contextual actions

Bottom

Settings

Help

Account / Logout

The sidebar should support a compact/collapsed state.

12. Settings

Settings opens as a modal/overlay.

Initial sections:

Usage

A clear usage summary.

Connectors

Manage globally available integrations.

Skills

Manage reusable behavior/instruction sets.

Do not add a user-facing global Knowledge management section.

Global approved baseline knowledge is system/admin-managed and will later be supplied from an approved external source such as Google Cloud Storage.

Future settings sections may be added without redesigning the modal shell.

13. Usage

The prototype usage experience is illustrative only.

Potential information:

current usage

recent usage

request/model usage

limits

allowance

connector-related consumption if useful

Avoid turning Usage into a complex analytics dashboard.

14. Connector Management

The connector catalogue should make it easy to understand:

connector name

connector type

connection state

permissions/capability summary

read vs read/write intent

manage/connect/disconnect action

Example states:

Connected

Not connected

Permission required

Error

No real integration is needed in this phase.

15. Skill Management

The skill catalogue should support:

viewing skills

creating a skill

editing a skill

viewing skill instructions

selecting a skill from within an individual chat

Skills are globally defined but activated per chat.

Settings should not imply that one skill is globally active.

Projects should not expose a Skill configuration section.

Only one skill may be active in a chat at once.

16. External Actions

The long-term product can execute actions through connectors.

Examples:

send email

post message

update incident

create ticket

modify external object

Design assumption:

read/search/retrieve may occur without confirmation

writes/state changes require explicit user confirmation

The prototype should demonstrate the confirmation interaction using mock data only.

17. UX Philosophy

The interface must prioritize:

Simplicity

A user should understand what to do without instruction.

Immediacy

The prompt is available when the app opens.

Calm

The product should avoid visual noise.

Progressive disclosure

Secondary features appear when requested.

Context awareness

Users should understand which project, chat skill, and connectors are currently relevant.

Trust

Grounded answers should expose source provenance.

Restraint

Do not display every capability permanently.

18. Empty State Philosophy

The empty state is not a dashboard.

It should not contain:

performance cards

large help panels

marketing copy

complex quick actions

project metrics

large onboarding sequences

Its main purpose is:

invite the user to type.

19. Non-Goals for UI Prototype

The current prototype does not aim to prove:

model quality

retrieval quality

connector reliability

backend architecture

document governance engine

security implementation

production readiness

action automation

It aims to prove:

interaction model

navigation

hierarchy

product simplicity

visual direction

workflow clarity

20. Success Criteria for the First UI Milestone

The first milestone is successful when:

opening the app immediately makes sense

the prompt composer is the obvious next action

the sidebar feels familiar but original

the screen does not feel like a dashboard

the interface feels calm and premium

the user can understand how to start a chat

New Chat correctly resets to an empty conversation

the first sent prompt creates a visible chat in history

the skill selector clearly applies to the current chat only

the app shell feels sufficiently polished to build the remaining product on top of it

The goal is not maximum feature coverage.

The goal is a strong interaction foundation.