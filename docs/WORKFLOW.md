# Workflow — the three-phase milestone loop

## The loop

DESIGN writes `docs/plans/mN.md` and no code. BUILD reads that plan
as its only brief and writes code on branch `mN-name`. REVIEW is a cold read
of the diff against the plan, producing `docs/reviews/mN.md`. Context is
never carried between phases; the plan file is the handoff. Clear the
session between phases.

## Model policy

Opus for DESIGN and REVIEW on milestones involving a
long-lived interface or an open problem (M2, M3, M5). Sonnet everywhere
else. Extended thinking on for DESIGN and REVIEW only, since subagents
inherit the session's thinking setting.

## Prompt templates

Copy these verbatim, substituting `<N>` and `<name>`.

### DESIGN template (run with `claude --model opus`, in plan mode)

```
Read CLAUDE.md and docs/WORKFLOW.md. Design milestone M<N>: <one line>.
Produce docs/plans/m<N>.md following docs/plans/TEMPLATE.md. Write the
plan only, no implementation code. The Interface section must be precise
enough to build from without reading this conversation.
```

### BUILD template (fresh session, Sonnet, thinking off)

```
Read CLAUDE.md, docs/WORKFLOW.md and docs/plans/m<N>.md. That plan file
is your complete brief. Implement it exactly. Branch m<N>-<name>. Do not
push, do not touch main. Delegate searching to Explore and test runs to
test-runner. Do not expand scope. If the plan is wrong or incomplete,
stop and say so rather than improvising. Stop when the plan's "Done when"
condition holds.
```

### REVIEW template

```
@reviewer review this branch against docs/plans/m<N>.md and write the
findings to docs/reviews/m<N>.md.
```
