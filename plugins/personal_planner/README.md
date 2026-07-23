# Personal Planner

An isolated Hermes plugin for turning early notifications into durable future
commitments with distinct actionable, deadline, and event times.

Version 1.0 makes adaptive day-level milestones the primary planning mode.
Normal conversations receive a bounded live planning snapshot; sparse-routed
Research, Career, and Communication analysis produces validated work packages;
and one deterministic allocator turns them into reviewable daily outcomes with
P50/P90 workload, deadline risk, check-ins, and rolling replans. Exact time
blocks remain available as an optional compatibility view when a commitment
really needs a clock-time reservation.

## Isolation contract

- Owns only `<HERMES_HOME>/planner/planner.db` and generated scripts under
  `<HERMES_HOME>/scripts/`.
- Uses Hermes' public plugin registration and Cron APIs.
- Reuses the existing `google-workspace` skill helper instead of owning another
  OAuth client or credential store.
- Does not modify SessionDB, Todo, Goal, Kanban, `jobs.json`, system prompts, or
  core toolsets.
- Disabling the plugin removes its commands and model tool without changing
  normal Hermes behavior. The Desktop route remains as an enablement prompt.

## Configuration

```yaml
plugins:
  enabled:
    - personal_planner
  entries:
    personal_planner:
      timezone: Asia/Shanghai
      dispatch_schedule: every 10m
      delivery: telegram
      reminder_offsets_hours: [72, 24, 3, 0]
      calendar_id: primary
      calendar_kinds: [actionable, deadline, event]
      event_duration_minutes: 60
      planning_horizon_days: 14
      planning_buffer_percent: 20
      default_task_minutes: 60
      default_min_block_minutes: 30
      max_block_minutes: 120
      calendar_busy_read: true
      calendar_busy_ids: [primary]
      calendar_busy_cache_minutes: 30
      schedule_draft_ttl_minutes: 30
      review_lookahead_hours: 48
      replan_overdue_minutes: 30
      ingest_schedule: every 30m
      radar_schedule: every 6h
      agent_runtime_enabled: true
      agent_mode: drafting
      agent_context_max_chars: 7000
      agent_weekly_schedule: "0 20 * * 0"
      planning_granularity: daily_milestone
      reminder_mode: recommended_window
      daily_buffer_percent: 25
      max_daily_domains: 2
      protect_hard_deadline_day: true
      reminder_recovery_minutes: 30
      reminder_merge_minutes: 60
      estimate_learning_enabled: true
      morning_preview_schedule: "0 8 * * *"
      end_of_day_checkin_schedule: "30 21 * * *"
```

`morning_preview_schedule` and `end_of_day_checkin_schedule` control the
existing morning-review and daily-review Cron jobs. Existing installations
that only set `agent_daily_schedule` or `review_schedule` remain compatible;
the milestone names take precedence when both are present.

## First run

```bash
hermes plan setup --delivery telegram
hermes plan capture "CET-6 registration opens September 10" \
  --title "CET-6 registration" \
  --actionable-at 2026-09-10T09:00:00+08:00 \
  --due-at 2026-09-18T17:00:00+08:00 \
  --event-at 2026-12-14T09:00:00+08:00 \
  --importance high
hermes plan inbox
hermes plan confirm P-XXXXXXXX
hermes plan calendar-preview P-XXXXXXXX
hermes plan calendar-sync P-XXXXXXXX --confirm
```

`capture` always creates a draft. Confirmation creates the reminder chain.
`ack` acknowledges messages but leaves later milestones active; `done` cancels
all remaining reminders.

## Active agent runtime

Planner state participates in ordinary Agent conversations; `/plan` is no
longer required to activate planning behavior. For example:

```text
导师让我月底前完成三组实验，但这周还要投两家实习，帮我安排一下。
```

Before the model answers, the plugin adds a compact, ephemeral snapshot of
confirmed profile facts, pending decisions, commitments, goals, open tasks,
weekly capacity, upcoming blocks, and current execution risks to that turn.
The model can then read or update Planner through the existing
`personal_planner` tool in the same Agent run.

The default `drafting` mode permits reversible actions such as creating a
commitment draft, proposed profile fact, backlog task, or schedule preview.
It never permits automatic confirmation, monitor approval, completion,
cancellation, or external calendar writes/deletes. Set `agent_mode: advisory`
to require approval before any Planner write. Set `agent_runtime_enabled:
false` to disable turn-context injection without disabling Planner commands,
Desktop, reminders, or Cron jobs.

Raw email and web-notice bodies are excluded from the runtime snapshot. The
context contains only bounded planning fields and is appended ephemerally to
the current user turn, so it does not mutate the system prompt or stored
conversation history.

## Hermes Desktop

After enabling the plugin, restart the Desktop backend and open **Planner** in
the left sidebar. The page provides four native views:

- Overview: today's commitments, automation state, capacity counts, active
  goals, and reviewable plan drafts.
- Inbox: notification drafts, acknowledgement, completion, and cancellation.
- Goals & plan: review today's milestone targets, P50/P90 remaining workload,
  deadline risks, milestone timeline, proposed-plan diff, and recent expert
  decisions; record done/partial/not-started/blocked/skipped progress or snooze
  a reminder. Goals, tasks, availability, and the optional exact-time grid stay
  on the same page below the milestone workspace.
- Radar: confirm/reject/revoke profile facts, approval-gated public-web monitor
  proposals, and enable/disable read-only Gmail sources.

Use **Enable automation** on Overview to install or resume the six
Planner-owned Cron jobs: deterministic reminder dispatch, deterministic daily
review, Gmail intake, approved web radar, agent daily planning, and agent
weekly strategy. **Pause automation** disables those jobs without deleting
their history. While Desktop is open it also claims due Planner reminders and
shows both an in-app alert and an OS notification, so the page itself does not
need to stay open.

Desktop reuses the standard plugin `command.dispatch` path. It does not add a
Planner-specific gateway RPC or duplicate Planner state. The compact,
versioned snapshot used by the page is also available in a conversation as:

```text
/plan overview
```

## Adaptive daily milestones (primary)

In a normal Hermes conversation, describe the outcome, deadline, and fixed
commitments. Planner routes only the relevant domain experts, validates their
structured WorkPackages, then uses one deterministic decision policy to create
daily targets. The model proposes domain estimates and decomposition; it does
not choose arbitrary clock-time blocks or silently accept the plan.

For the mixed-domain example used by the offline evaluation fixture:

```text
Complete three experiments before July 31 and submit two internship
applications before July 25. July 21 has many classes, July 22 has an important
meeting, Saturday morning is reserved for sport, and every evening includes
exercise.
```

Fixture assumptions are explicit: the request is received on July 20, all
dates are in `Asia/Shanghai`, applications use July 24 as the recommended
internal deadline, experiments use July 30, and the supplied class, meeting,
sport, and exercise periods are fixed commitments. The fixture is not a claim
that every user's work has the same duration. Its measured invariants are
stored in
`docs/superpowers/evaluations/personal-planner-v1.0.json`.

The expected behavior is relational rather than a frozen schedule:

- only Research and Career are selected for the direct request;
- July 21 and 22 remain deliberately light;
- risky experiment work begins before all application work is finished;
- each package's hard-deadline day is protected from normal core work;
- P50 work that cannot fit and P90 buffer that cannot fit are reported
  separately;
- no external calendar write occurs during analysis or draft generation.

Inspect, accept, or reject the deterministic draft from the CLI:

```bash
hermes plan milestone-preview --days 14 --calendar
hermes plan milestone-show MP-XXXXXXXX
hermes plan milestone-confirm MP-XXXXXXXX --confirm
# or
hermes plan milestone-reject MP-XXXXXXXX
```

When a confirmed plan is being replaced, the reviewed base version must be
named explicitly:

```bash
hermes plan milestone-confirm MP-NEWXXXXX --confirm --replace MP-OLDXXXXX
```

The same operations are available in conversation as `/plan
milestone-preview`, `/plan milestone-show`, `/plan milestone-confirm`, and
`/plan milestone-reject`. Desktop exposes Confirm and Reject buttons on the
proposed diff; rejection leaves the accepted plan unchanged.

### Progress and reminder actions

Daily reminders show today's target, its target minutes, total P50/P90
remaining workload, deadline distance, and visible risks. Reminder evaluation
is deterministic and unchanged reminders do not call an LLM. Nearby reminders
may be merged into one delivery batch.

```bash
hermes plan milestone-checkin DM-XXXXXXXX --outcome done
hermes plan milestone-checkin DM-XXXXXXXX --outcome partial \
  --minutes 35 --fraction 0.5 --note "Baseline ran; metrics need review"
hermes plan milestone-checkin DM-XXXXXXXX --outcome not_started
hermes plan milestone-checkin DM-XXXXXXXX --outcome blocked \
  --note "Waiting for GPU quota"
hermes plan milestone-checkin DM-XXXXXXXX --outcome skipped
hermes plan milestone-snooze DM-XXXXXXXX --minutes 90
```

`partial` requires positive actual minutes and a completion fraction strictly
between 0 and 1. `blocked` requires a note. A check-in records estimate
observations and may create a replacement draft; it never replaces the active
plan without explicit confirmation. Snooze changes only that unfinished
milestone's reminder anchor.

### Estimation and approval boundaries

Research, Career, and Communication estimates are probabilistic. P50 is the
likely core workload; P90 is the protected estimate used to expose uncertainty
and buffer risk. Domain pace calibration is disabled until at least three
eligible check-ins exist, and the learned multiplier is bounded to `0.75–1.5`.
Set `estimate_learning_enabled: false` to keep fixture/model estimates
unchanged.

The following always require user approval: confirming or replacing a
milestone plan, choosing a scope/deadline/capacity trade-off, confirming a
captured notice, approving an information-radar monitor, and writing external
calendar events. Analysis, capacity recomputation, reminders, and draft
generation cannot perform those mutations.

## Automatic notification intake from Gmail

Create a bounded, read-only Gmail source using normal Gmail search syntax:

```bash
hermes plan source-add "Graduate school notices" \
  --query "from:grad-school.example newer_than:30d" \
  --importance high \
  --max-results 50

hermes plan source-scan
hermes plan sources
```

The scan reads matching message metadata and bodies through Hermes' existing
`google-workspace` credentials. It does not mark messages read, move them,
label them, reply, or send mail.

Every new message receives an ingestion receipt. Rescanning is idempotent.
Messages in the same Gmail thread revise the same Planner commitment, so an
updated school notice returns the item to `draft` for review instead of
silently changing an already-confirmed reminder.

Important safety boundary:

- scans only create or update Planner drafts;
- dates are not guessed from the email;
- reminders and calendar events are not created;
- each draft must be reviewed, given explicit actionable/deadline/event times,
  and confirmed by the user.

Useful source controls:

```bash
hermes plan source-show F-XXXXXXXX
hermes plan source-disable F-XXXXXXXX
hermes plan source-enable F-XXXXXXXX
hermes plan source-scan F-XXXXXXXX
```

Install the optional no-agent scan job:

```bash
hermes plan ingest-setup --schedule "every 30m" --delivery telegram
```

The Cron job only sends a message when it captured drafts or encountered an
error. Use bounded Gmail queries such as `newer_than:30d`; each source returns
at most 100 messages per scan.

## Profile-driven information radar

Profile facts are structured and reviewable rather than being hidden in an
opaque prompt. Adding a fact creates a proposal first:

```bash
hermes plan profile-add education school "北京邮电大学"
hermes plan profile-add education degree "硕士研究生"
hermes plan profile-add education field "计算机"
hermes plan profile-add education graduation_year "2028"

hermes plan profile
hermes plan profile-confirm PF-XXXXXXXX
```

Confirm each fact that should influence recommendations. A newly confirmed fact
with the same category and key supersedes the old value. Facts can also be
rejected before confirmation or revoked later:

```bash
hermes plan profile-reject PF-XXXXXXXX
hermes plan profile-revoke PF-XXXXXXXX
```

Facts marked `sensitive` remain local and are excluded from automatic proposal
generation:

```bash
hermes plan profile-add personal identity-detail "..." --privacy sensitive
```

Generate monitoring proposals from confirmed, currently valid, standard-profile
facts and active Planner goals:

```bash
hermes plan radar-propose
hermes plan monitors
hermes plan monitor-show MP-XXXXXXXX
hermes plan monitor-approve MP-XXXXXXXX
```

For a computer-science graduate student with internship, publication, and CET
goals, proposals can include:

- university and graduate-school administrative notices;
- graduate training, scholarship, thesis, and degree milestones;
- computing lectures, competitions, and research projects;
- internships, campus recruiting, and career events;
- calls for papers and research-funding deadlines;
- CET registration and exam notices.

Every proposal includes:

- the confirmed profile facts or active goals that caused it;
- a human-readable rationale;
- suggested official source categories;
- suggested search terms;
- a priority and review cadence.

`monitor-approve` approves only that exact proposal and ensures the shared,
agent-backed radar Cron job is available. Each run loads only approved
proposals, uses their exact query terms and source kinds as an allow-list,
prefers first-party official sources, and stores new or changed results as
Planner drafts with their source URI. It never invents missing dates, confirms
a notice, or writes to a calendar. Rejecting an approved proposal removes it
from future scans; if no monitoring is desired, pause all Planner automation.

## Notification deduplication and updates

- Equivalent content is returned as the existing item.
- When `--source-uri` is present, the normalized URI is the durable identity.
- A changed notice from the same URI updates the existing item rather than
  creating another commitment.
- Changed fields are stored in `planner_item_revisions`.
- An updated confirmed notice returns to `draft`, cancels pending nudges, and
  must be reviewed and confirmed again.
- Omitted structured dates keep their existing values during an update; this
  prevents a shortened follow-up notice from silently erasing its deadline.

Inspect history with:

```bash
hermes plan revisions P-XXXXXXXX
```

## Calendar safety

Calendar writes are always two-step:

```bash
hermes plan calendar-preview P-XXXXXXXX
hermes plan calendar-sync P-XXXXXXXX --confirm
```

The preview performs no external write. The sync command calls the existing
Google Workspace calendar helper and records each returned event ID so retries
do not create the same Planner event again.

If a source notification changes after an event was linked, the link becomes
`stale`. Planner will not overwrite or delete that external event. Reconcile it
in Google Calendar, then explicitly forget the local link before resyncing:

```bash
hermes plan calendar-forget P-XXXXXXXX --confirm-external-cleanup
hermes plan calendar-preview P-XXXXXXXX
hermes plan calendar-sync P-XXXXXXXX --confirm
```

`calendar-forget` only removes Planner's local link. It does not delete an
external calendar event.

## Long-term goals and optional exact-block plans

Create goals for the work streams that compete for your time:

```bash
hermes plan goal-add "Submit first paper" \
  --area research \
  --target-date 2027-03-01 \
  --weekly-minutes 600 \
  --priority high

hermes plan goal-add "Autumn internship search" \
  --area career \
  --target-date 2026-10-01 \
  --weekly-minutes 360 \
  --priority high
```

Break goals or confirmed notifications into work that can be scheduled:

```bash
hermes plan task-add "Read and annotate five related papers" \
  --goal G-XXXXXXXX \
  --estimate 240 \
  --min-block 60 \
  --energy high \
  --due 2026-07-25T20:00:00+08:00

hermes plan task-add --item P-XXXXXXXX --estimate 90 --energy medium
```

Define time that is genuinely available. Weekdays use `mon` through `sun`
or `0` through `6`:

```bash
hermes plan availability-add mon 19:00 22:00 --energy high --label "Deep work"
hermes plan availability-add wed 19:00 22:00 --energy high --label "Deep work"
hermes plan availability-add sat 09:00 12:00 --energy high --label "Research"
hermes plan availability-add sat 14:00 17:00 --energy medium --label "Career/admin"
```

Use this compatibility path only when work genuinely needs an exact time
reservation. Generate a local draft, inspect its blocks and capacity warnings,
then confirm:

```bash
hermes plan schedule-preview --days 14 --calendar-busy
hermes plan schedule-show S-XXXXXXXX
hermes plan schedule-confirm S-XXXXXXXX --confirm
```

The scheduler:

- subtracts confirmed Planner event times, already-confirmed plan blocks, and
  configured Google Calendar events when calendar busy reads are enabled;
- keeps the configured capacity buffer instead of filling every free minute;
- prioritizes deadlines, task priority, goal priority, and unmet weekly goal
  effort;
- places high-energy tasks only in compatible windows;
- exposes unscheduled work and deadline risks in the draft summary.

Google Calendar busy-time reads are read-only. They reuse the existing
`google-workspace` helper and its OAuth token. No additional credential store
is created. Inspect the current result directly with:

```bash
hermes plan calendar-busy
```

Successful reads are cached in the Planner database, including successful
empty calendars. If a later read fails, Planner marks the result as
`cached`, `stale`, or `unavailable` instead of silently pretending the calendar
was checked.

Calendar-backed drafts are short-lived. At confirmation Planner re-reads every
configured calendar and compares an event/time fingerprint with the preview.
The confirmation is rejected when:

- an event was added, removed, moved, or cancelled after preview;
- a calendar cannot be read live;
- the draft is older than `schedule_draft_ttl_minutes`.

Generate a fresh preview after any rejection.

If circumstances change, generate a replacement draft. The old confirmed plan
is kept until the replacement is explicitly approved:

```bash
hermes plan schedule-preview --replan
hermes plan schedule-confirm S-XXXXXXXX --confirm --replace
```

Replanning only supersedes local Planner blocks. It does not mutate Google
Calendar events. Recording actual progress keeps future replans honest:

```bash
hermes plan task-progress T-XXXXXXXX 60
hermes plan task-done T-XXXXXXXX
```

Progress entries may include an audit note:

```bash
hermes plan task-progress T-XXXXXXXX 45 --note "Finished experiment analysis"
```

## Time-block check-ins

Confirmed schedule blocks can be checked in without pretending every planned
minute was completed:

```bash
hermes plan block-show B-XXXXXXXX
hermes plan block-done B-XXXXXXXX --actual-minutes 45 \
  --note "Finished the experiment analysis"
hermes plan block-skip B-XXXXXXXX --note "Advisor meeting ran long"
hermes plan block-defer B-XXXXXXXX --propose-replan
```

- `block-done` records actual effort and credits at most the task's remaining
  estimate.
- `block-skip` and `block-defer` record no progress and release the local
  reserved time.
- Repeating the same check-in is idempotent; changing an already-recorded
  outcome is rejected.
- A proposed replan is still only a draft. The current confirmed schedule is
  kept until the replacement is explicitly confirmed.

## Confirmed plan blocks in Google Calendar

Plan-block calendar writes use their own preview and confirmation flow:

```bash
hermes plan schedule-calendar-preview S-XXXXXXXX
hermes plan schedule-calendar-sync S-XXXXXXXX --confirm
```

Only blocks in the currently confirmed schedule are created. The preview stores
a local snapshot, and sync refuses to proceed if no preview exists or the plan
changed afterward. Each external event ID is stored locally, so a successful
retry does not duplicate events.
If a linked block is completed early, skipped, deferred, completed indirectly
through its task, or superseded by a replan, its link becomes `stale`.

Planner deliberately does not update or delete that Google Calendar event.
After reviewing the external events yourself, remove only the local links and
preview again:

```bash
hermes plan schedule-calendar-forget S-XXXXXXXX \
  --confirm-external-cleanup
```

Failed or interrupted writes are not retried automatically because the
external event may already exist even when Planner could not finish recording
the result.

## Daily review

Build a review of recorded progress, missed or unaccounted plan blocks,
near-term deadline capacity gaps, and new calendar conflicts:

```bash
hermes plan review --calendar-busy
```

To create a replacement draft only when the review finds a concrete trigger:

```bash
hermes plan review --calendar-busy --propose-replan
```

The old confirmed plan remains active until the replacement is reviewed and
explicitly accepted with:

```bash
hermes plan schedule-confirm S-XXXXXXXX --confirm --replace
```

An optional no-agent Cron job can deliver a compact daily review:

```bash
hermes plan review-setup --schedule "0 21 * * *" --delivery telegram
```

The Cron review reports facts and recommendations only. It never confirms a
replan or writes Google Calendar events.
