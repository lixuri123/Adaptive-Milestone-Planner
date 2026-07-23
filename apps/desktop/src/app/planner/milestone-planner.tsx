import { useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Activity, AlertTriangle, Brain, Clock, GitBranch } from '@/lib/icons'
import { cn } from '@/lib/utils'

import type {
  PlannerAgentRun,
  PlannerDailyMilestone,
  PlannerDomain,
  PlannerMilestoneCheckinInput,
  PlannerMilestoneCheckinOutcome,
  PlannerMilestonePlan,
  PlannerRemainingWork
} from './api'
import type { PlannerCopy } from './copy'

export type MilestoneCheckinOutcome = PlannerMilestoneCheckinOutcome
export type MilestoneCheckinInput = PlannerMilestoneCheckinInput

interface MilestonePlannerProps extends React.ComponentProps<'section'> {
  activePlan: null | PlannerMilestonePlan
  agentRuns: PlannerAgentRun[]
  copy: PlannerCopy
  draftPlan: null | PlannerMilestonePlan
  locale: string
  onCheckin: (milestoneId: string, input: MilestoneCheckinInput) => Promise<void> | void
  onConfirmDraft: (plan: PlannerMilestonePlan) => Promise<void> | void
  onRejectDraft: (plan: PlannerMilestonePlan) => Promise<void> | void
  onSnooze: (milestoneId: string, minutes: number) => Promise<void> | void
  pendingEventCount: number
  remaining: PlannerRemainingWork
  running: boolean
  today: string
}

interface CheckinDialogState {
  milestone: PlannerDailyMilestone
  mode: 'blocked' | 'partial' | 'snooze'
}

const DOMAIN_PRESENTATION: Record<PlannerDomain, { accent: string; badge: string }> = {
  career: { accent: 'bg-sky-500', badge: 'bg-sky-500/10 text-sky-600 dark:text-sky-300' },
  communication: {
    accent: 'bg-amber-500',
    badge: 'bg-amber-500/10 text-amber-600 dark:text-amber-300'
  },
  research: {
    accent: 'bg-emerald-500',
    badge: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300'
  }
}

const TERMINAL_STATUSES = new Set(['cancelled', 'done', 'superseded'])

function localeTag(locale: string): string {
  if (locale === 'zh') {
    return 'zh-CN'
  }

  if (locale === 'zh-hant') {
    return 'zh-TW'
  }

  if (locale === 'ja') {
    return 'ja-JP'
  }

  return 'en-US'
}

function formatDate(value: string, locale: string): string {
  const date = new Date(`${value}T12:00:00`)

  if (Number.isNaN(date.valueOf())) {
    return value
  }

  return new Intl.DateTimeFormat(localeTag(locale), { day: 'numeric', month: 'short' }).format(date)
}

function formatMoment(value: string, locale: string): string {
  const date = new Date(value)

  if (Number.isNaN(date.valueOf())) {
    return value
  }

  return new Intl.DateTimeFormat(localeTag(locale), {
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    month: 'short'
  }).format(date)
}

function formatMinutes(minutes: number): string {
  const safeMinutes = Math.max(0, Math.round(minutes || 0))

  if (safeMinutes < 120) {
    return `${safeMinutes}m`
  }

  const hours = safeMinutes / 60

  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)}h`
}

function statusVariant(status: string): 'default' | 'destructive' | 'muted' | 'outline' | 'warn' {
  if (status === 'blocked' || status === 'missed') {
    return 'destructive'
  }

  if (status === 'partial' || status === 'draft') {
    return 'warn'
  }

  if (status === 'done') {
    return 'default'
  }

  return 'muted'
}

function riskLabel(risk: Record<string, unknown>, fallback: string): string {
  for (const key of ['message', 'reason', 'title', 'work_package_id']) {
    const value = risk[key]

    if (typeof value === 'string' && value.trim()) {
      return value
    }
  }

  return fallback
}

function DomainBadge({ copy, domain }: { copy: PlannerCopy; domain: PlannerDomain }) {
  const presentation = DOMAIN_PRESENTATION[domain]

  return <Badge className={presentation.badge}>{copy.milestonePlanner.domainLabels[domain]}</Badge>
}

function WorkloadSummary({ copy, remaining }: { copy: PlannerCopy; remaining: PlannerRemainingWork }) {
  return (
    <aside className="rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) p-3.5">
      <div className="flex items-center gap-2 text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
        <Clock className="size-3.5" />
        {copy.milestonePlanner.remaining}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <div className="rounded-lg bg-(--ui-chat-bubble-background) px-3 py-2.5">
          <div className="text-lg font-semibold tabular-nums tracking-[-0.03em]">
            {copy.milestonePlanner.p50} {formatMinutes(remaining.p50_minutes)}
          </div>
          <div className="mt-0.5 text-[0.62rem] text-muted-foreground/65">{copy.milestonePlanner.likelyEffort}</div>
        </div>
        <div className="rounded-lg bg-(--ui-chat-bubble-background) px-3 py-2.5">
          <div className="text-lg font-semibold tabular-nums tracking-[-0.03em] text-amber-600 dark:text-amber-300">
            {copy.milestonePlanner.p90} {formatMinutes(remaining.p90_minutes)}
          </div>
          <div className="mt-0.5 text-[0.62rem] text-muted-foreground/65">
            {copy.milestonePlanner.protectedEstimate}
          </div>
        </div>
      </div>
    </aside>
  )
}

function TodayCard({
  copy,
  milestone,
  onCheckin,
  onOpenDialog,
  running
}: {
  copy: PlannerCopy
  milestone: PlannerDailyMilestone
  onCheckin: MilestonePlannerProps['onCheckin']
  onOpenDialog: (mode: CheckinDialogState['mode'], milestone: PlannerDailyMilestone) => void
  running: boolean
}) {
  const actionable = !TERMINAL_STATUSES.has(milestone.status)
  const domain = DOMAIN_PRESENTATION[milestone.domain]

  return (
    <article className="relative overflow-hidden rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background) p-3.5">
      <span className={cn('absolute inset-y-0 left-0 w-0.5', domain.accent)} />
      <div className="flex flex-wrap items-start gap-2 pl-1">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <DomainBadge copy={copy} domain={milestone.domain} />
            <Badge variant={statusVariant(milestone.status)}>{milestone.status}</Badge>
          </div>
          <h3 className="mt-2 text-sm font-semibold leading-5 text-foreground">{milestone.title}</h3>
        </div>
        <Badge className="text-[0.7rem] tabular-nums" variant="outline">
          {formatMinutes(milestone.target_minutes)}
        </Badge>
      </div>
      {milestone.definition_of_done.length > 0 && (
        <div className="mt-3 pl-1">
          <div className="text-[0.62rem] font-medium uppercase tracking-[0.08em] text-muted-foreground/60">
            {copy.milestonePlanner.definitionOfDone}
          </div>
          <ul className="mt-1.5 space-y-1">
            {milestone.definition_of_done.map(item => (
              <li className="flex gap-2 text-xs leading-4 text-muted-foreground/80" key={item}>
                <span className="mt-1.5 size-1 shrink-0 rounded-full bg-emerald-500/80" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}
      {actionable && (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-(--ui-stroke-tertiary) pt-2.5 pl-1">
          <Button disabled={running} onClick={() => void onCheckin(milestone.id, { outcome: 'done' })} size="xs">
            {copy.milestonePlanner.done}
          </Button>
          <Button disabled={running} onClick={() => onOpenDialog('partial', milestone)} size="xs" variant="outline">
            {copy.milestonePlanner.partial}
          </Button>
          <Button
            disabled={running}
            onClick={() => void onCheckin(milestone.id, { outcome: 'not_started' })}
            size="xs"
            variant="text"
          >
            {copy.milestonePlanner.notStarted}
          </Button>
          <Button disabled={running} onClick={() => onOpenDialog('blocked', milestone)} size="xs" variant="text">
            {copy.milestonePlanner.blocked}
          </Button>
          <Button
            disabled={running}
            onClick={() => void onCheckin(milestone.id, { outcome: 'skipped' })}
            size="xs"
            variant="text"
          >
            {copy.milestonePlanner.skipped}
          </Button>
          <Button disabled={running} onClick={() => onOpenDialog('snooze', milestone)} size="xs" variant="text">
            {copy.milestonePlanner.snooze}
          </Button>
        </div>
      )}
    </article>
  )
}

function DraftReview({
  copy,
  locale,
  onConfirm,
  onReject,
  plan,
  running
}: {
  copy: PlannerCopy
  locale: string
  onConfirm: () => void
  onReject: () => void
  plan: PlannerMilestonePlan
  running: boolean
}) {
  const diff = plan.summary.diff
  const hasChanges = Boolean(diff && (diff.added.length || diff.changed.length || diff.removed.length))

  return (
    <section className="overflow-hidden rounded-xl border border-amber-500/25 bg-amber-500/5">
      <header className="flex flex-wrap items-start gap-3 border-b border-amber-500/15 px-4 py-3.5">
        <span className="grid size-8 place-items-center rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-300">
          <GitBranch className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">{copy.milestonePlanner.draftTitle}</h2>
          <p className="mt-1 text-xs leading-4 text-muted-foreground/75">{copy.milestonePlanner.draftDescription}</p>
        </div>
        <Badge variant="warn">{plan.id}</Badge>
      </header>
      <div className="space-y-2 p-3.5">
        {!hasChanges && <p className="text-xs text-muted-foreground/70">{copy.milestonePlanner.noDraftChanges}</p>}
        {diff?.changed.map(change => (
          <div
            className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background) p-3"
            key={change.after.id}
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="warn">{copy.milestonePlanner.changed}</Badge>
              <span className="min-w-0 flex-1 text-xs font-medium">{change.after.title}</span>
              <span className="text-xs tabular-nums text-muted-foreground">
                {formatDate(change.before.date, locale)} → {formatDate(change.after.date, locale)}
              </span>
            </div>
            {change.before.target_minutes !== change.after.target_minutes && (
              <div className="mt-1.5 text-[0.65rem] text-muted-foreground/70">
                {copy.milestonePlanner.workload}: {formatMinutes(change.before.target_minutes)} →{' '}
                {formatMinutes(change.after.target_minutes)}
              </div>
            )}
          </div>
        ))}
        {diff?.added.map(milestone => (
          <div
            className="flex items-center gap-2 rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background) p-3"
            key={milestone.id}
          >
            <Badge>{copy.milestonePlanner.added}</Badge>
            <span className="min-w-0 flex-1 text-xs font-medium">{milestone.title}</span>
            <span className="text-xs text-muted-foreground">{formatDate(milestone.date, locale)}</span>
          </div>
        ))}
        {diff?.removed.map(milestone => (
          <div
            className="flex items-center gap-2 rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background) p-3"
            key={milestone.id}
          >
            <Badge variant="destructive">{copy.milestonePlanner.removed}</Badge>
            <span className="min-w-0 flex-1 text-xs font-medium line-through opacity-70">{milestone.title}</span>
            <span className="text-xs text-muted-foreground">{formatDate(milestone.date, locale)}</span>
          </div>
        ))}
      </div>
      <footer className="flex justify-end gap-2 border-t border-amber-500/15 px-3.5 py-3">
        <Button disabled={running} onClick={onReject} size="xs" variant="text">
          {copy.milestonePlanner.rejectUpdate}
        </Button>
        <Button disabled={running} onClick={onConfirm} size="xs">
          {copy.milestonePlanner.confirmUpdate}
        </Button>
      </footer>
    </section>
  )
}

function CheckinDialog({
  copy,
  onCheckin,
  onOpenChange,
  onSnooze,
  openState,
  running
}: {
  copy: PlannerCopy
  onCheckin: MilestonePlannerProps['onCheckin']
  onOpenChange: (state: null | CheckinDialogState) => void
  onSnooze: MilestonePlannerProps['onSnooze']
  openState: null | CheckinDialogState
  running: boolean
}) {
  const [minutes, setMinutes] = useState('')
  const [percent, setPercent] = useState('')
  const [note, setNote] = useState('')
  const [snoozeMinutes, setSnoozeMinutes] = useState('60')
  const mode = openState?.mode

  const close = () => {
    onOpenChange(null)
    setMinutes('')
    setPercent('')
    setNote('')
    setSnoozeMinutes('60')
  }

  const submit = () => {
    if (!openState) {
      return
    }

    if (mode === 'snooze') {
      void onSnooze(openState.milestone.id, Math.max(1, Number(snoozeMinutes) || 60))
    } else if (mode === 'partial') {
      void onCheckin(openState.milestone.id, {
        actualMinutes: Math.max(0, Number(minutes) || 0),
        completionFraction: Math.min(1, Math.max(0, (Number(percent) || 0) / 100)),
        note: note.trim() || undefined,
        outcome: 'partial'
      })
    } else {
      void onCheckin(openState.milestone.id, { note: note.trim() || undefined, outcome: 'blocked' })
    }

    close()
  }

  return (
    <Dialog onOpenChange={open => !open && close()} open={Boolean(openState)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {mode === 'snooze' ? copy.milestonePlanner.snooze : copy.milestonePlanner.saveCheckin}
          </DialogTitle>
          <DialogDescription>{openState?.milestone.title}</DialogDescription>
        </DialogHeader>
        {mode === 'partial' && (
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="grid gap-1.5 text-xs">
              <span>{copy.milestonePlanner.minutesCompleted}</span>
              <Input
                aria-label={copy.milestonePlanner.minutesCompleted}
                min="0"
                onChange={event => setMinutes(event.target.value)}
                type="number"
                value={minutes}
              />
            </label>
            <label className="grid gap-1.5 text-xs">
              <span>{copy.milestonePlanner.completionPercent}</span>
              <Input
                aria-label={copy.milestonePlanner.completionPercent}
                max="100"
                min="0"
                onChange={event => setPercent(event.target.value)}
                type="number"
                value={percent}
              />
            </label>
          </div>
        )}
        {mode === 'snooze' ? (
          <label className="grid gap-1.5 text-xs">
            <span>{copy.milestonePlanner.snoozeMinutes}</span>
            <Input
              aria-label={copy.milestonePlanner.snoozeMinutes}
              min="1"
              onChange={event => setSnoozeMinutes(event.target.value)}
              type="number"
              value={snoozeMinutes}
            />
          </label>
        ) : (
          <label className="grid gap-1.5 text-xs">
            <span>{copy.milestonePlanner.note}</span>
            <Textarea
              aria-label={copy.milestonePlanner.note}
              onChange={event => setNote(event.target.value)}
              value={note}
            />
          </label>
        )}
        <DialogFooter>
          <Button disabled={running} onClick={close} variant="text">
            {copy.milestonePlanner.cancel}
          </Button>
          <Button disabled={running} onClick={submit}>
            {mode === 'snooze' ? copy.milestonePlanner.snooze : copy.milestonePlanner.saveCheckin}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function MilestonePlanner({
  activePlan,
  agentRuns,
  className,
  copy,
  draftPlan,
  locale,
  onCheckin,
  onConfirmDraft,
  onRejectDraft,
  onSnooze,
  pendingEventCount,
  remaining,
  running,
  today,
  ...props
}: MilestonePlannerProps) {
  const [dialogState, setDialogState] = useState<null | CheckinDialogState>(null)
  const todayMilestones = activePlan?.milestones.filter(milestone => milestone.date === today) ?? []

  const timeline = useMemo(() => {
    const grouped = new Map<string, PlannerDailyMilestone[]>()

    for (const milestone of activePlan?.milestones ?? []) {
      const values = grouped.get(milestone.date) ?? []
      values.push(milestone)
      grouped.set(milestone.date, values)
    }

    return [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right))
  }, [activePlan])

  const risks = activePlan?.summary.deadline_risks ?? []

  return (
    <section className={cn('space-y-4', className)} {...props}>
      <section className="overflow-hidden rounded-2xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background)">
        <header className="flex flex-wrap items-start gap-3 border-b border-(--ui-stroke-tertiary) bg-[linear-gradient(110deg,color-mix(in_srgb,var(--primary)_8%,transparent),transparent_55%)] px-4 py-4 sm:px-5">
          <span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
            <Brain className="size-4.5" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-foreground">{copy.milestonePlanner.title}</h2>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground/75">
              {copy.milestonePlanner.description}
            </p>
          </div>
          {pendingEventCount > 0 && (
            <Badge variant="warn">
              {pendingEventCount} {copy.milestonePlanner.pendingEvents}
            </Badge>
          )}
        </header>

        <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(16rem,0.65fr)] sm:p-5">
          <section>
            <div className="mb-2.5 flex items-center justify-between gap-3">
              <h2 className="text-[0.72rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
                {copy.milestonePlanner.todayTargets}
              </h2>
              <span className="text-[0.65rem] text-muted-foreground">{formatDate(today, locale)}</span>
            </div>
            {todayMilestones.length === 0 ? (
              <div className="grid min-h-32 place-items-center rounded-xl border border-dashed border-(--ui-stroke-tertiary) px-4 text-center text-xs text-muted-foreground/70">
                {copy.milestonePlanner.emptyToday}
              </div>
            ) : (
              <div className="space-y-2.5">
                {todayMilestones.map(milestone => (
                  <TodayCard
                    copy={copy}
                    key={milestone.id}
                    milestone={milestone}
                    onCheckin={onCheckin}
                    onOpenDialog={(mode, selected) => setDialogState({ milestone: selected, mode })}
                    running={running}
                  />
                ))}
              </div>
            )}
          </section>

          <div className="space-y-3">
            <WorkloadSummary copy={copy} remaining={remaining} />
            {risks.length > 0 && (
              <aside className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-3.5">
                <div className="flex items-center gap-2 text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-amber-600 dark:text-amber-300">
                  <AlertTriangle className="size-3.5" />
                  {copy.milestonePlanner.risk}
                </div>
                <ul className="mt-2 space-y-1.5 text-xs leading-4 text-muted-foreground/80">
                  {risks.slice(0, 3).map((risk, index) => (
                    <li className="flex gap-2" key={`${riskLabel(risk, copy.milestonePlanner.fallbackRisk)}-${index}`}>
                      <span className="mt-1.5 size-1 shrink-0 rounded-full bg-amber-500" />
                      {riskLabel(risk, copy.milestonePlanner.fallbackRisk)}
                    </li>
                  ))}
                </ul>
              </aside>
            )}
          </div>
        </div>
      </section>

      {draftPlan && (
        <DraftReview
          copy={copy}
          locale={locale}
          onConfirm={() => void onConfirmDraft(draftPlan)}
          onReject={() => void onRejectDraft(draftPlan)}
          plan={draftPlan}
          running={running}
        />
      )}

      <div className="grid gap-4 lg:grid-cols-[1.25fr_0.75fr]">
        <section className="overflow-hidden rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background)">
          <header className="flex min-h-10 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-3.5 py-2">
            <Clock className="size-3.5 text-muted-foreground/60" />
            <h2 className="text-[0.72rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
              {copy.milestonePlanner.timeline}
            </h2>
          </header>
          {timeline.length === 0 ? (
            <div className="grid min-h-32 place-items-center px-5 text-center text-xs text-muted-foreground/70">
              {copy.milestonePlanner.emptyTimeline}
            </div>
          ) : (
            <div className="divide-y divide-(--ui-stroke-tertiary)">
              {timeline.map(([date, milestones]) => (
                <div className="grid gap-2 px-3.5 py-3 sm:grid-cols-[5rem_1fr]" key={date}>
                  <div className="text-xs font-semibold tabular-nums text-foreground/75">
                    {formatDate(date, locale)}
                  </div>
                  <div className="space-y-2">
                    {milestones.map(milestone => (
                      <div className="flex items-start gap-2" key={milestone.id}>
                        <span
                          className={cn(
                            'mt-1.5 size-1.5 shrink-0 rounded-full',
                            DOMAIN_PRESENTATION[milestone.domain].accent
                          )}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="text-xs font-medium text-foreground/90">{milestone.title}</span>
                            <Badge variant={statusVariant(milestone.status)}>{milestone.status}</Badge>
                          </div>
                          <div className="mt-0.5 text-[0.64rem] text-muted-foreground/65">
                            {copy.milestonePlanner.workload} {formatMinutes(milestone.target_minutes)}
                            {milestone.explanation[0] ? ` · ${milestone.explanation[0]}` : ''}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="overflow-hidden rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background)">
          <header className="flex min-h-10 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-3.5 py-2">
            <Activity className="size-3.5 text-muted-foreground/60" />
            <h2 className="text-[0.72rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
              {copy.milestonePlanner.activity}
            </h2>
          </header>
          {agentRuns.length === 0 ? (
            <div className="grid min-h-32 place-items-center px-5 text-center text-xs text-muted-foreground/70">
              {copy.milestonePlanner.activityEmpty}
            </div>
          ) : (
            <div className="divide-y divide-(--ui-stroke-tertiary)">
              {agentRuns.slice(0, 5).map(run => (
                <article className="p-3.5" key={run.id}>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant={run.approval_status === 'review_required' ? 'warn' : 'muted'}>{run.outcome}</Badge>
                    {run.selected_experts.map(expert => (
                      <Badge key={expert} variant="outline">
                        {expert}
                      </Badge>
                    ))}
                  </div>
                  <p className="mt-2 text-xs leading-4 text-muted-foreground/80">{run.reasoning_summary}</p>
                  <div className="mt-1.5 text-[0.62rem] text-muted-foreground/55">
                    {formatMoment(run.created_at, locale)}
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>

      <CheckinDialog
        copy={copy}
        onCheckin={onCheckin}
        onOpenChange={setDialogState}
        onSnooze={onSnooze}
        openState={dialogState}
        running={running}
      />
    </section>
  )
}
