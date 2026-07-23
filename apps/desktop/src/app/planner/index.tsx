import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
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
import type { HermesGateway } from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertCircle, Bell, CheckCircle2, RefreshCw } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'

import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import { PageSearchShell } from '../page-search-shell'
import type { SetStatusbarItemGroup } from '../shell/statusbar-controls'

import {
  loadPlannerOverview,
  loadScheduleCalendarPreview,
  milestoneCheckinArg,
  milestoneDraftDecisionArg,
  milestoneSnoozeArg,
  type PlannerGoal,
  type PlannerGoalInput,
  type PlannerItem,
  type PlannerMonitorProposal,
  type PlannerOverview,
  type PlannerProfileFact,
  type PlannerProfileInput,
  type PlannerSchedule,
  type PlannerScheduleCalendarPreview,
  type PlannerSource,
  type PlannerSourceInput,
  type PlannerTask,
  type PlannerTaskInput,
  quotePlannerArg,
  runPlannerCommand,
  saveConfirmedProfileFacts,
  savePlannerGoal,
  savePlannerSource,
  savePlannerTask,
  syncScheduleCalendar
} from './api'
import { plannerCopy, type PlannerCopy } from './copy'
import {
  AvailabilityEditorDialog,
  GoalEditorDialog,
  type PlannerAvailabilityInput,
  ScheduleCalendarDialog,
  SourceEditorDialog,
  TaskEditorDialog
} from './editor-dialogs'
import { MilestonePlanner } from './milestone-planner'
import { WeeklyPlanner } from './weekly-planner'

const PLANNER_TABS = ['overview', 'inbox', 'goals', 'radar'] as const
type PlannerTab = (typeof PLANNER_TABS)[number]

const PROFILE_FIELDS = [
  { category: 'education', factKey: 'school', key: 'school' },
  { category: 'education', factKey: 'field', key: 'field' },
  { category: 'education', factKey: 'degree', key: 'degree' },
  { category: 'education', factKey: 'graduation_year', key: 'graduationYear' },
  { category: 'research', factKey: 'direction', key: 'researchDirection' },
  { category: 'career', factKey: 'focus', key: 'careerFocus' }
] as const

type ProfileFieldKey = (typeof PROFILE_FIELDS)[number]['key']
type ProfileFormValue = Record<ProfileFieldKey, string>

const EMPTY_PROFILE_FORM: ProfileFormValue = {
  careerFocus: '',
  degree: '',
  field: '',
  graduationYear: '',
  researchDirection: '',
  school: ''
}

interface PlannerViewProps extends React.ComponentProps<'section'> {
  gateway: HermesGateway | null
  setStatusbarItemGroup?: SetStatusbarItemGroup
}

interface PendingPlannerAction {
  arg: string
  confirmLabel: string
  description: string
  title: string
}

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

function formatMoment(value: null | string | undefined, locale: string): string {
  if (!value) {
    return '—'
  }

  const date = new Date(value)

  if (Number.isNaN(date.valueOf())) {
    return value
  }

  return new Intl.DateTimeFormat(localeTag(locale), {
    dateStyle: 'medium',
    timeStyle: 'short'
  }).format(date)
}

function formatDate(value: null | string | undefined, locale: string): string {
  if (!value) {
    return '—'
  }

  const date = new Date(value)

  if (Number.isNaN(date.valueOf())) {
    return value
  }

  return new Intl.DateTimeFormat(localeTag(locale), { dateStyle: 'medium' }).format(date)
}

function formatMinutes(minutes: number, locale: string): string {
  const safe = Math.max(0, Math.round(minutes || 0))

  if (safe < 60) {
    return locale.startsWith('zh') ? `${safe} 分钟` : `${safe}m`
  }

  const hours = safe / 60

  return locale.startsWith('zh') ? `${hours.toFixed(hours % 1 ? 1 : 0)} 小时` : `${hours.toFixed(hours % 1 ? 1 : 0)}h`
}

function itemMoment(item: PlannerItem): null | string {
  return item.due_at ?? item.event_at ?? item.actionable_at ?? null
}

function scheduleConfirmationArg(schedule: PlannerSchedule): string {
  const replaces = Boolean(schedule.is_replan) || Boolean(schedule.replaces_plan_ids?.length)

  return `schedule-confirm ${schedule.id} confirm${replaces ? ' replace' : ''}`
}

function dayKey(value: string, timeZone: string): string {
  try {
    return new Intl.DateTimeFormat('en-CA', {
      day: '2-digit',
      month: '2-digit',
      timeZone,
      year: 'numeric'
    }).format(new Date(value))
  } catch {
    return new Date(value).toISOString().slice(0, 10)
  }
}

function importanceTone(value: string): 'destructive' | 'muted' | 'warn' | 'default' {
  if (value === 'critical') {
    return 'destructive'
  }

  if (value === 'high') {
    return 'warn'
  }

  if (value === 'low') {
    return 'muted'
  }

  return 'default'
}

function includesQuery(query: string, ...values: Array<null | string | undefined>): boolean {
  const needle = query.trim().toLocaleLowerCase()

  return !needle || values.some(value => value?.toLocaleLowerCase().includes(needle))
}

function EmptyState({ children, icon = 'circle-large-outline' }: { children: ReactNode; icon?: string }) {
  return (
    <div className="grid min-h-28 place-items-center rounded-lg border border-dashed border-(--ui-stroke-tertiary) px-5 text-center">
      <div className="space-y-2 text-xs text-muted-foreground/70">
        <Codicon className="mx-auto text-muted-foreground/40" name={icon} size="1.1rem" />
        <p>{children}</p>
      </div>
    </div>
  )
}

function SectionCard({
  children,
  className,
  title,
  trailing
}: {
  children: ReactNode
  className?: string
  title: string
  trailing?: ReactNode
}) {
  return (
    <section
      className={cn(
        'overflow-hidden rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background)',
        className
      )}
    >
      <header className="flex min-h-10 items-center gap-3 border-b border-(--ui-stroke-tertiary) px-3.5 py-2">
        <h2 className="min-w-0 flex-1 truncate text-[0.72rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
          {title}
        </h2>
        {trailing}
      </header>
      {children}
    </section>
  )
}

function MetricCard({ accent, icon, label, value }: { accent: string; icon: string; label: string; value: number }) {
  return (
    <div className="group relative overflow-hidden rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background) p-3.5 transition-colors hover:border-(--ui-stroke-secondary)">
      <span className={cn('absolute inset-y-0 left-0 w-0.5 opacity-80', accent)} />
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-2xl font-semibold tabular-nums tracking-[-0.04em] text-foreground">{value}</div>
          <div className="mt-1 text-[0.68rem] text-muted-foreground/70">{label}</div>
        </div>
        <span className="grid size-7 place-items-center rounded-md bg-(--ui-bg-quinary) text-muted-foreground/55 transition-colors group-hover:text-foreground/70">
          <Codicon name={icon} size="0.9rem" />
        </span>
      </div>
    </div>
  )
}

interface TimelineEntry {
  id: string
  kind: 'block' | 'item'
  status: string
  time: string
  title: string
}

function timelineEntries(data: PlannerOverview): TimelineEntry[] {
  const todayKey = dayKey(data.generated_at, data.status.timezone)

  const entries: TimelineEntry[] = data.today.map(item => ({
    id: item.id,
    kind: 'item',
    status: item.importance,
    time: itemMoment(item) ?? data.generated_at,
    title: item.title
  }))

  for (const block of data.active_schedule?.blocks ?? []) {
    if (!block.checkin && dayKey(block.starts_at, data.status.timezone) === todayKey) {
      entries.push({ id: block.id, kind: 'block', status: 'planned', time: block.starts_at, title: block.title })
    }
  }

  return entries.sort((a, b) => Date.parse(a.time) - Date.parse(b.time)).slice(0, 8)
}

function OverviewTab({ copy, data, locale, onAction, onConfirmAction, running }: PlannerTabProps) {
  const drafts = data.items.filter(item => item.status === 'draft')
  const openTasks = data.tasks.filter(task => task.status === 'backlog' || task.status === 'planned')
  const entries = timelineEntries(data)
  const pendingMonitors = data.monitor_proposals.filter(proposal => proposal.status === 'proposed')
  const radarActive = Boolean(data.automation.jobs.radar?.enabled)

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-4 sm:px-5 sm:py-5">
      <section className="relative overflow-hidden rounded-2xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background) px-5 py-5 sm:px-6">
        <div className="pointer-events-none absolute -right-20 -top-28 size-72 rounded-full bg-[radial-gradient(circle,color-mix(in_srgb,var(--primary)_16%,transparent),transparent_68%)]" />
        <div className="relative max-w-2xl">
          <div className="text-[0.62rem] font-semibold tracking-[0.16em] text-primary/75">{copy.hero.eyebrow}</div>
          <h1 className="mt-2 text-[clamp(1.45rem,3vw,2.25rem)] font-semibold leading-[1.08] tracking-[-0.045em] text-foreground">
            {copy.hero.title}
          </h1>
          <p className="mt-2 max-w-xl text-xs leading-5 text-muted-foreground/80">{copy.hero.subtitle}</p>
          <div className="mt-4 flex flex-wrap gap-1.5">
            <Badge variant="muted">
              <CheckCircle2 />
              {copy.hero.local}
            </Badge>
            <Badge variant="warn">
              <AlertCircle />
              {radarActive ? copy.labels.radarActive : copy.labels.radarInactive}
            </Badge>
            <Badge variant="outline">{data.status.timezone}</Badge>
          </div>
        </div>
      </section>

      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
        <MetricCard accent="bg-sky-500" icon="calendar" label={copy.labels.today} value={data.today.length} />
        <MetricCard accent="bg-amber-500" icon="inbox" label={copy.labels.drafts} value={drafts.length} />
        <MetricCard accent="bg-emerald-500" icon="checklist" label={copy.labels.openTasks} value={openTasks.length} />
        <MetricCard
          accent="bg-rose-500"
          icon="target"
          label={copy.labels.activeGoals}
          value={data.goals.filter(goal => goal.status === 'active').length}
        />
      </div>

      <SectionCard
        title={copy.automation.title}
        trailing={
          <Badge variant={data.automation.enabled ? 'default' : 'muted'}>
            {data.automation.enabled ? copy.automation.active : copy.automation.inactive}
          </Badge>
        }
      >
        <div className="flex flex-wrap items-center gap-3 p-3.5">
          <p className="min-w-0 flex-1 text-xs leading-5 text-muted-foreground/75">{copy.automation.description}</p>
          {data.automation.enabled ? (
            <Button
              disabled={running !== null}
              onClick={() =>
                onConfirmAction({
                  arg: 'automation-pause',
                  confirmLabel: copy.actions.pauseAutomation,
                  description: copy.automation.paused,
                  title: copy.actions.pauseAutomation
                })
              }
              size="xs"
              variant="outline"
            >
              {copy.actions.pauseAutomation}
            </Button>
          ) : (
            <Button disabled={running !== null} onClick={() => onAction('automation-enable')} size="xs">
              {copy.actions.enableAutomation}
            </Button>
          )}
        </div>
      </SectionCard>

      {data.draft_schedule && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-500/20 bg-amber-500/6 px-4 py-3">
          <span className="grid size-8 place-items-center rounded-lg bg-amber-500/10 text-amber-500">
            <Codicon name="calendar" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-xs font-medium text-foreground">{copy.labels.scheduleDraft}</div>
            <div className="mt-0.5 text-[0.66rem] text-muted-foreground">{data.draft_schedule.id}</div>
          </div>
          <Button
            disabled={running !== null}
            onClick={() => onAction(scheduleConfirmationArg(data.draft_schedule!))}
            size="xs"
          >
            {copy.actions.confirmPlan}
          </Button>
          <Button
            disabled={running !== null}
            onClick={() =>
              onConfirmAction({
                arg: `schedule-cancel ${data.draft_schedule!.id}`,
                confirmLabel: copy.actions.cancelPlan,
                description: copy.confirmations.cancelPlan,
                title: copy.actions.cancelPlan
              })
            }
            size="xs"
            variant="outline"
          >
            {copy.actions.cancelPlan}
          </Button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
        <SectionCard title={copy.labels.todayTimeline}>
          {entries.length === 0 ? (
            <div className="p-3">
              <EmptyState icon="calendar">{copy.empty.schedule}</EmptyState>
            </div>
          ) : (
            <div className="divide-y divide-(--ui-stroke-tertiary)">
              {entries.map((entry, index) => (
                <div className="group flex min-h-12 items-center gap-3 px-3.5 py-2" key={`${entry.kind}-${entry.id}`}>
                  <div className="relative grid size-7 shrink-0 place-items-center rounded-md bg-(--ui-bg-quinary) text-muted-foreground/65">
                    <Codicon name={entry.kind === 'block' ? 'watch' : 'bell'} size="0.85rem" />
                    {index < entries.length - 1 && (
                      <span className="absolute left-1/2 top-7 h-6 w-px -translate-x-1/2 bg-(--ui-stroke-tertiary)" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-foreground/90">{entry.title}</div>
                    <div className="mt-0.5 text-[0.64rem] text-muted-foreground">
                      {formatMoment(entry.time, locale)}
                    </div>
                  </div>
                  <Badge variant={entry.kind === 'item' ? importanceTone(entry.status) : 'muted'}>{entry.status}</Badge>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard title={copy.labels.goals}>
          {data.goals.length === 0 ? (
            <div className="p-3">
              <EmptyState icon="target">{copy.empty.goals}</EmptyState>
            </div>
          ) : (
            <div className="space-y-1 p-2">
              {data.goals.slice(0, 6).map(goal => (
                <GoalRow copy={copy} goal={goal} key={goal.id} locale={locale} tasks={data.tasks} />
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <SectionCard
          title={copy.labels.monitoring}
          trailing={
            pendingMonitors.length > 0 ? (
              <Badge variant="warn">
                {pendingMonitors.length} {copy.labels.pending}
              </Badge>
            ) : undefined
          }
        >
          <div className="p-3">
            <p className="text-xs leading-5 text-muted-foreground/75">{copy.labels.radarBoundary}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                disabled={running !== null}
                onClick={() => onAction('radar-propose')}
                size="xs"
                variant="secondary"
              >
                {copy.actions.generateRadar}
              </Button>
              <Button disabled={running !== null} onClick={() => onAction('source-scan')} size="xs" variant="outline">
                {copy.actions.scan}
              </Button>
            </div>
          </div>
        </SectionCard>
        <SectionCard title={copy.labels.focusPlan}>
          <div className="flex flex-wrap gap-2 p-3">
            <Button disabled={running !== null} onClick={() => onAction('schedule calendar')} size="xs">
              {copy.actions.generatePlan}
            </Button>
            <Button disabled={running !== null} onClick={() => onAction('review replan')} size="xs" variant="outline">
              {copy.actions.review}
            </Button>
          </div>
        </SectionCard>
      </div>
    </div>
  )
}

interface PlannerTabProps {
  copy: PlannerCopy
  data: PlannerOverview
  locale: string
  onAction: (arg: string) => void
  onConfirmAction: (action: PendingPlannerAction) => void
  query: string
  running: null | string
}

function InboxTab({ copy, data, locale, onAction, onConfirmAction, query, running }: PlannerTabProps) {
  const items = data.items.filter(item =>
    includesQuery(query, item.title, item.raw_content, item.status, item.importance)
  )

  return (
    <div className="mx-auto w-full max-w-5xl space-y-3 px-4 py-4 sm:px-5">
      {items.length === 0 ? (
        <EmptyState icon="inbox">{copy.empty.inbox}</EmptyState>
      ) : (
        items.map(item => (
          <article
            className="group rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background) p-3.5 transition-colors hover:border-(--ui-stroke-secondary)"
            key={item.id}
          >
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  'mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg',
                  item.status === 'draft'
                    ? 'bg-amber-500/10 text-amber-500'
                    : item.status === 'confirmed'
                      ? 'bg-emerald-500/10 text-emerald-500'
                      : 'bg-(--ui-bg-quinary) text-muted-foreground'
                )}
              >
                {item.status === 'confirmed' ? <CheckCircle2 className="size-4" /> : <Bell className="size-4" />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <h2 className="min-w-0 truncate text-sm font-medium text-foreground">{item.title}</h2>
                  <Badge variant={item.status === 'draft' ? 'warn' : 'muted'}>{item.status}</Badge>
                  <Badge variant={importanceTone(item.importance)}>{item.importance}</Badge>
                </div>
                <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-muted-foreground/75">{item.raw_content}</p>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.65rem] text-muted-foreground/60">
                  <span className="font-mono">{item.id}</span>
                  {item.actionable_at && (
                    <span>
                      {copy.labels.next}: {formatMoment(item.actionable_at, locale)}
                    </span>
                  )}
                  {item.due_at && (
                    <span>
                      {copy.labels.due}: {formatMoment(item.due_at, locale)}
                    </span>
                  )}
                  {item.event_at && (
                    <span>
                      {copy.labels.event}: {formatMoment(item.event_at, locale)}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 gap-1.5">
                {item.status === 'draft' && (
                  <Button disabled={running !== null} onClick={() => onAction(`confirm ${item.id}`)} size="xs">
                    {copy.actions.confirm}
                  </Button>
                )}
                {item.status === 'confirmed' && (
                  <>
                    {Boolean(item.ack_required) && !item.acknowledged_at && (
                      <Button disabled={running !== null} onClick={() => onAction(`ack ${item.id}`)} size="xs">
                        {copy.actions.acknowledge}
                      </Button>
                    )}
                    <Button
                      disabled={running !== null}
                      onClick={() => onAction(`done ${item.id}`)}
                      size="xs"
                      variant="outline"
                    >
                      {copy.actions.done}
                    </Button>
                  </>
                )}
                {!['cancelled', 'done'].includes(item.status) && (
                  <Button
                    disabled={running !== null}
                    onClick={() =>
                      onConfirmAction({
                        arg: `cancel ${item.id}`,
                        confirmLabel: copy.actions.cancelItem,
                        description: copy.confirmations.cancelItem,
                        title: copy.actions.cancelItem
                      })
                    }
                    size="xs"
                    variant="text"
                  >
                    {copy.actions.cancelItem}
                  </Button>
                )}
              </div>
            </div>
          </article>
        ))
      )}
    </div>
  )
}

function goalProgress(goal: PlannerGoal, tasks: PlannerTask[]): { done: number; total: number } {
  const linked = tasks.filter(task => task.goal_id === goal.id)

  return linked.reduce(
    (sum, task) => ({ done: sum.done + task.completed_minutes, total: sum.total + task.estimated_minutes }),
    { done: 0, total: 0 }
  )
}

function GoalRow({
  copy,
  goal,
  locale,
  onAction,
  onConfirmAction,
  running,
  tasks
}: {
  copy: PlannerCopy
  goal: PlannerGoal
  locale: string
  onAction?: (arg: string) => void
  onConfirmAction?: (action: PendingPlannerAction) => void
  running?: null | string
  tasks: PlannerTask[]
}) {
  const progress = goalProgress(goal, tasks)
  const percent = progress.total ? Math.min(100, Math.round((progress.done / progress.total) * 100)) : 0

  return (
    <div className="rounded-lg px-2 py-2.5 transition-colors hover:bg-(--ui-control-hover-background)">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 size-2 shrink-0 rounded-full bg-primary/70" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium text-foreground/90">{goal.title}</div>
          <div className="mt-1 flex flex-wrap gap-x-2 text-[0.62rem] text-muted-foreground/60">
            <span>{goal.area}</span>
            {goal.target_date && <span>{formatDate(goal.target_date, locale)}</span>}
            {goal.weekly_target_minutes > 0 && (
              <span>
                {formatMinutes(goal.weekly_target_minutes, locale)} {copy.labels.weekly}
              </span>
            )}
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-(--ui-bg-quinary)">
            <div
              className="h-full rounded-full bg-primary/70 transition-[width] duration-500"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <span className="text-[0.62rem] tabular-nums text-muted-foreground/55">{percent}%</span>
          {onAction && (
            <Button
              disabled={running !== null}
              onClick={() => onAction(`goal-status ${goal.id} ${goal.status === 'paused' ? 'active' : 'paused'}`)}
              size="xs"
              variant="text"
            >
              {goal.status === 'paused' ? copy.actions.resume : copy.actions.pause}
            </Button>
          )}
          {onConfirmAction && (
            <Button
              disabled={running !== null}
              onClick={() =>
                onConfirmAction({
                  arg: `goal-status ${goal.id} cancelled`,
                  confirmLabel: copy.actions.cancelGoal,
                  description: copy.confirmations.cancelGoal,
                  title: copy.actions.cancelGoal
                })
              }
              size="xs"
              variant="text"
            >
              {copy.actions.cancelGoal}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

function GoalsTab({
  copy,
  data,
  locale,
  onAction,
  onAddAvailability,
  onAddGoal,
  onCalendarPreview,
  onConfirmAction,
  onEditTask,
  query,
  running
}: PlannerTabProps & {
  onAddAvailability: () => void
  onAddGoal: () => void
  onCalendarPreview: (scheduleId: string) => void
  onEditTask: (task?: PlannerTask) => void
}) {
  const goals = data.goals.filter(goal => includesQuery(query, goal.title, goal.area, goal.priority))

  const tasks = data.tasks.filter(
    task => ['backlog', 'planned'].includes(task.status) && includesQuery(query, task.title, task.energy, task.priority)
  )

  return (
    <div className="mx-auto w-full max-w-[90rem] space-y-4 px-4 py-4 sm:px-5">
      <MilestonePlanner
        activePlan={data.milestone_plan}
        agentRuns={data.recent_agent_runs}
        copy={copy}
        draftPlan={data.milestone_draft}
        locale={locale}
        onCheckin={(milestoneId, input) => onAction(milestoneCheckinArg(milestoneId, input))}
        onConfirmDraft={plan => onAction(milestoneDraftDecisionArg(plan.id, 'confirm'))}
        onRejectDraft={plan => onAction(milestoneDraftDecisionArg(plan.id, 'reject'))}
        onSnooze={(milestoneId, minutes) => onAction(milestoneSnoozeArg(milestoneId, minutes))}
        pendingEventCount={data.pending_events.filter(event => event.status === 'pending').length}
        remaining={data.remaining}
        running={running !== null}
        today={dayKey(data.generated_at, data.status.timezone)}
      />
      <div className="space-y-2">
        <div className="px-1">
          <h2 className="text-[0.72rem] font-semibold uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
            {copy.milestonePlanner.timeGrid}
          </h2>
          <p className="mt-1 text-xs text-muted-foreground/65">{copy.milestonePlanner.timeGridDescription}</p>
        </div>
        <WeeklyPlanner
          activeSchedule={data.active_schedule}
          availability={data.availability}
          copy={copy}
          draftSchedule={data.draft_schedule}
          generatedAt={data.generated_at}
          goals={data.goals}
          locale={locale}
          onAddAvailability={onAddAvailability}
          onBlockAction={(block, action) =>
            onAction(`block-${action} ${block.id}${action === 'done' ? '' : ' replan'}`)
          }
          onCancelSchedule={schedule =>
            onConfirmAction({
              arg: `schedule-cancel ${schedule.id}`,
              confirmLabel: copy.actions.cancelPlan,
              description: copy.confirmations.cancelPlan,
              title: copy.actions.cancelPlan
            })
          }
          onConfirmDraft={schedule => onAction(scheduleConfirmationArg(schedule))}
          onGeneratePlan={() => onAction('schedule calendar')}
          onRemoveAvailability={window =>
            onConfirmAction({
              arg: `availability-clear ${window.id}`,
              confirmLabel: copy.actions.remove,
              description: copy.confirmations.removeAvailability,
              title: copy.actions.remove
            })
          }
          onSyncCalendar={schedule => onCalendarPreview(schedule.id)}
          running={running !== null}
          tasks={data.tasks}
          timeZone={data.status.timezone}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
        <div className="space-y-4">
          <SectionCard
            title={copy.labels.goals}
            trailing={
              <Button disabled={running !== null} onClick={onAddGoal} size="xs" variant="textStrong">
                <Codicon name="add" />
                {copy.actions.addGoal}
              </Button>
            }
          >
            {goals.length === 0 ? (
              <div className="p-3">
                <EmptyState icon="target">{copy.empty.goals}</EmptyState>
              </div>
            ) : (
              <div className="space-y-1 p-2">
                {goals.map(goal => (
                  <GoalRow
                    copy={copy}
                    goal={goal}
                    key={goal.id}
                    locale={locale}
                    onAction={onAction}
                    onConfirmAction={onConfirmAction}
                    running={running}
                    tasks={data.tasks}
                  />
                ))}
              </div>
            )}
          </SectionCard>
        </div>
        <SectionCard
          title={copy.labels.tasks}
          trailing={
            <Button disabled={running !== null} onClick={() => onEditTask()} size="xs" variant="textStrong">
              <Codicon name="add" />
              {copy.actions.addTask}
            </Button>
          }
        >
          {tasks.length === 0 ? (
            <div className="p-3">
              <EmptyState icon="checklist">{copy.empty.tasks}</EmptyState>
            </div>
          ) : (
            <div className="divide-y divide-(--ui-stroke-tertiary)">
              {tasks.map(task => {
                const progress = task.estimated_minutes
                  ? Math.round((task.completed_minutes / task.estimated_minutes) * 100)
                  : 0

                return (
                  <div className="flex items-center gap-3 px-3.5 py-3" key={task.id}>
                    <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-(--ui-bg-quinary) text-[0.62rem] font-semibold tabular-nums text-muted-foreground">
                      {progress}%
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-medium text-foreground/90">{task.title}</div>
                      <div className="mt-1 flex flex-wrap gap-x-2 text-[0.62rem] text-muted-foreground/60">
                        <span>{formatMinutes(task.remaining_minutes, locale)}</span>
                        <span>{task.energy}</span>
                        {task.due_at && <span>{formatDate(task.due_at, locale)}</span>}
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      {task.status === 'backlog' && (
                        <Button disabled={running !== null} onClick={() => onEditTask(task)} size="xs" variant="text">
                          {copy.actions.edit}
                        </Button>
                      )}
                      <Button
                        disabled={running !== null}
                        onClick={() => onAction(`task-done ${task.id}`)}
                        size="xs"
                        variant="outline"
                      >
                        {copy.actions.markDone}
                      </Button>
                      {task.status === 'backlog' && (
                        <Button
                          disabled={running !== null}
                          onClick={() =>
                            onConfirmAction({
                              arg: `task-cancel ${task.id}`,
                              confirmLabel: copy.actions.cancelTask,
                              description: copy.confirmations.cancelTask,
                              title: copy.actions.cancelTask
                            })
                          }
                          size="xs"
                          variant="text"
                        >
                          {copy.actions.cancelTask}
                        </Button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  )
}

function profileFormValue(facts: PlannerProfileFact[]): ProfileFormValue {
  const result = { ...EMPTY_PROFILE_FORM }

  for (const field of PROFILE_FIELDS) {
    const fact = facts.find(
      candidate =>
        candidate.category === field.category &&
        candidate.fact_key === field.factKey &&
        candidate.status === 'confirmed' &&
        candidate.currently_valid
    )

    result[field.key] = fact?.fact_value ?? ''
  }

  return result
}

function RadarTab({
  copy,
  data,
  onAddSource,
  onAction,
  onConfirmAction,
  onConfirmAll,
  onEditProfile,
  query,
  running
}: PlannerTabProps & { onAddSource: () => void; onConfirmAll: () => void; onEditProfile: () => void }) {
  const facts = data.profile_facts.filter(fact =>
    includesQuery(query, fact.category, fact.fact_key, fact.fact_value, fact.status)
  )

  const monitors = data.monitor_proposals.filter(proposal =>
    includesQuery(query, proposal.title, proposal.rationale, proposal.priority, proposal.status)
  )

  const sources = data.sources.filter(source => includesQuery(query, source.name, source.query, source.importance))
  const pendingFacts = facts.filter(fact => fact.status === 'proposed')

  return (
    <div className="mx-auto grid w-full max-w-6xl gap-4 px-4 py-4 sm:px-5 lg:grid-cols-2">
      <div className="space-y-4">
        <SectionCard
          title={copy.labels.profile}
          trailing={
            <div className="flex items-center gap-1">
              {pendingFacts.length > 0 && (
                <Button disabled={running !== null} onClick={onConfirmAll} size="xs" variant="textStrong">
                  {copy.actions.confirmAll}
                </Button>
              )}
              <Button disabled={running !== null} onClick={onEditProfile} size="xs">
                <Codicon name="person-add" />
                {copy.actions.editProfile}
              </Button>
            </div>
          }
        >
          {facts.length === 0 ? (
            <div className="p-3">
              <EmptyState icon="account">{copy.empty.profile}</EmptyState>
            </div>
          ) : (
            <div className="divide-y divide-(--ui-stroke-tertiary)">
              {facts.map(fact => (
                <FactRow
                  copy={copy}
                  fact={fact}
                  key={fact.id}
                  onAction={onAction}
                  onConfirmAction={onConfirmAction}
                  running={running}
                />
              ))}
            </div>
          )}
        </SectionCard>
        <SectionCard
          title={copy.labels.sources}
          trailing={
            <div className="flex items-center gap-1">
              <Button disabled={running !== null} onClick={onAddSource} size="xs" variant="textStrong">
                <Codicon name="add" />
                {copy.actions.addSource}
              </Button>
              <Button
                disabled={running !== null || sources.length === 0}
                onClick={() => onAction('source-scan')}
                size="xs"
                variant="textStrong"
              >
                {copy.actions.scan}
              </Button>
            </div>
          }
        >
          {sources.length === 0 ? (
            <div className="p-3">
              <EmptyState icon="mail">{copy.empty.sources}</EmptyState>
            </div>
          ) : (
            <div className="divide-y divide-(--ui-stroke-tertiary)">
              {sources.map(source => (
                <SourceRow copy={copy} key={source.id} onAction={onAction} running={running} source={source} />
              ))}
            </div>
          )}
        </SectionCard>
      </div>
      <SectionCard
        title={copy.labels.monitoring}
        trailing={
          <Button disabled={running !== null} onClick={() => onAction('radar-propose')} size="xs" variant="textStrong">
            {copy.actions.generateRadar}
          </Button>
        }
      >
        <div className="border-b border-(--ui-stroke-tertiary) bg-amber-500/5 px-3.5 py-2.5 text-[0.68rem] leading-5 text-amber-700 dark:text-amber-300/85">
          {copy.labels.radarBoundary}
        </div>
        {monitors.length === 0 ? (
          <div className="p-3">
            <EmptyState icon="radio-tower">{copy.empty.monitors}</EmptyState>
          </div>
        ) : (
          <div className="divide-y divide-(--ui-stroke-tertiary)">
            {monitors.map(proposal => (
              <MonitorRow
                copy={copy}
                key={proposal.id}
                onAction={onAction}
                onConfirmAction={onConfirmAction}
                proposal={proposal}
                running={running}
              />
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  )
}

function FactRow({
  copy,
  fact,
  onAction,
  onConfirmAction,
  running
}: {
  copy: PlannerCopy
  fact: PlannerProfileFact
  onAction: (arg: string) => void
  onConfirmAction: (action: PendingPlannerAction) => void
  running: null | string
}) {
  return (
    <div className="flex items-center gap-3 px-3.5 py-2.5">
      <span
        className={cn(
          'size-1.5 shrink-0 rounded-full',
          fact.status === 'confirmed' ? 'bg-emerald-500' : 'bg-amber-500'
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium">{fact.fact_value}</div>
        <div className="mt-0.5 text-[0.62rem] text-muted-foreground/60">
          {fact.category} · {fact.fact_key}
        </div>
      </div>
      <Badge variant={fact.status === 'confirmed' ? 'muted' : 'warn'}>{fact.status}</Badge>
      {fact.status === 'proposed' && (
        <div className="flex shrink-0 gap-1">
          <Button disabled={running !== null} onClick={() => onAction(`profile-confirm ${fact.id}`)} size="xs">
            {copy.actions.confirm}
          </Button>
          <Button
            disabled={running !== null}
            onClick={() =>
              onConfirmAction({
                arg: `profile-reject ${fact.id}`,
                confirmLabel: copy.actions.reject,
                description: copy.confirmations.rejectProfile,
                title: copy.actions.reject
              })
            }
            size="xs"
            variant="text"
          >
            {copy.actions.reject}
          </Button>
        </div>
      )}
      {fact.status === 'confirmed' && (
        <Button
          disabled={running !== null}
          onClick={() =>
            onConfirmAction({
              arg: `profile-revoke ${fact.id}`,
              confirmLabel: copy.actions.revoke,
              description: copy.confirmations.revokeProfile,
              title: copy.actions.revoke
            })
          }
          size="xs"
          variant="text"
        >
          {copy.actions.revoke}
        </Button>
      )}
    </div>
  )
}

function MonitorRow({
  copy,
  onAction,
  onConfirmAction,
  proposal,
  running
}: {
  copy: PlannerCopy
  onAction: (arg: string) => void
  onConfirmAction: (action: PendingPlannerAction) => void
  proposal: PlannerMonitorProposal
  running: null | string
}) {
  return (
    <div className="px-3.5 py-3">
      <div className="flex items-start gap-3">
        <span
          className={cn(
            'mt-1.5 size-1.5 shrink-0 rounded-full',
            proposal.status === 'approved' ? 'bg-emerald-500' : 'bg-amber-500'
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-foreground/90">{proposal.title}</div>
          {proposal.rationale && (
            <p className="mt-1 line-clamp-2 text-[0.68rem] leading-5 text-muted-foreground/70">{proposal.rationale}</p>
          )}
          <div className="mt-1.5 flex flex-wrap gap-1">
            <Badge variant={importanceTone(proposal.priority)}>{proposal.priority}</Badge>
            <Badge variant="outline">{proposal.status}</Badge>
            {proposal.network_scan_active && <Badge variant="default">{copy.labels.radarActive}</Badge>}
          </div>
          {(proposal.query_terms?.length || proposal.source_kinds?.length) && (
            <div className="mt-2 space-y-1 text-[0.62rem] text-muted-foreground/60">
              {proposal.query_terms?.length ? <div>{proposal.query_terms.join(' · ')}</div> : null}
              {proposal.source_kinds?.length ? <div>{proposal.source_kinds.join(' · ')}</div> : null}
            </div>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          {proposal.status !== 'approved' && (
            <Button disabled={running !== null} onClick={() => onAction(`monitor-approve ${proposal.id}`)} size="xs">
              {copy.actions.approve}
            </Button>
          )}
          {proposal.status !== 'rejected' && (
            <Button
              disabled={running !== null}
              onClick={() =>
                onConfirmAction({
                  arg: `monitor-reject ${proposal.id}`,
                  confirmLabel: proposal.status === 'approved' ? copy.actions.pause : copy.actions.reject,
                  description: copy.confirmations.stopMonitor,
                  title: proposal.status === 'approved' ? copy.actions.pause : copy.actions.reject
                })
              }
              size="xs"
              variant="text"
            >
              {proposal.status === 'approved' ? copy.actions.pause : copy.actions.reject}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

function SourceRow({
  copy,
  onAction,
  running,
  source
}: {
  copy: PlannerCopy
  onAction: (arg: string) => void
  running: null | string
  source: PlannerSource
}) {
  return (
    <div className="flex items-start gap-3 px-3.5 py-2.5">
      <span
        className={cn(
          'mt-1 size-2 shrink-0 rounded-full',
          source.enabled ? 'bg-emerald-500' : 'bg-muted-foreground/30'
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium">{source.name}</div>
        <div className="mt-0.5 truncate font-mono text-[0.6rem] text-muted-foreground/55">{source.query}</div>
      </div>
      {source.last_error && <Badge variant="destructive">error</Badge>}
      <Button
        disabled={running !== null}
        onClick={() => onAction(`source-${source.enabled ? 'disable' : 'enable'} ${source.id}`)}
        size="xs"
        variant="text"
      >
        {source.enabled ? copy.actions.pause : copy.actions.approve}
      </Button>
    </div>
  )
}

function CaptureDialog({
  copy,
  onCapture,
  onOpenChange,
  open,
  running
}: {
  copy: PlannerCopy
  onCapture: (text: string) => void
  onOpenChange: (open: boolean) => void
  open: boolean
  running: boolean
}) {
  const [text, setText] = useState('')

  return (
    <Dialog
      onOpenChange={next => {
        onOpenChange(next)

        if (!next) {
          setText('')
        }
      }}
      open={open}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{copy.capture.title}</DialogTitle>
          <DialogDescription>{copy.capture.description}</DialogDescription>
        </DialogHeader>
        <Textarea
          autoFocus
          className="min-h-36 resize-y"
          onChange={event => setText(event.target.value)}
          placeholder={copy.capture.placeholder}
          value={text}
        />
        <p className="text-[0.68rem] leading-5 text-muted-foreground/70">{copy.messages.capturedHint}</p>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="text">
            {copy.capture.cancel}
          </Button>
          <Button disabled={!text.trim() || running} onClick={() => onCapture(text.trim())}>
            {copy.capture.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ProfileDialog({
  copy,
  initialValue,
  onOpenChange,
  onSave,
  open,
  running
}: {
  copy: PlannerCopy
  initialValue: ProfileFormValue
  onOpenChange: (open: boolean) => void
  onSave: (value: ProfileFormValue) => void
  open: boolean
  running: boolean
}) {
  const [value, setValue] = useState<ProfileFormValue>(initialValue)

  useEffect(() => {
    if (open) {
      setValue(initialValue)
    }
  }, [initialValue, open])

  const hasValue = PROFILE_FIELDS.some(field => value[field.key].trim())

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <div className="mb-1 flex size-9 items-center justify-center rounded-xl border border-primary/15 bg-primary/8 text-primary">
            <Codicon name="account" size="1.05rem" />
          </div>
          <DialogTitle>{copy.profileEditor.title}</DialogTitle>
          <DialogDescription>{copy.profileEditor.description}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-x-3 gap-y-3 sm:grid-cols-2">
          {PROFILE_FIELDS.map((field, index) => (
            <label className="grid gap-1.5" key={field.key}>
              <span className="text-[0.68rem] font-medium text-foreground/75">
                {copy.profileEditor.fields[field.key]}
              </span>
              <Input
                autoFocus={index === 0}
                inputMode={field.key === 'graduationYear' ? 'numeric' : undefined}
                onChange={event => setValue(current => ({ ...current, [field.key]: event.target.value }))}
                placeholder={copy.profileEditor.placeholders[field.key]}
                spellCheck={field.key !== 'graduationYear'}
                value={value[field.key]}
              />
            </label>
          ))}
        </div>
        <DialogFooter>
          <Button disabled={running} onClick={() => onOpenChange(false)} variant="text">
            {copy.profileEditor.cancel}
          </Button>
          <Button disabled={!hasValue || running} onClick={() => onSave(value)}>
            <CheckCircle2 className="size-3.5" />
            {copy.profileEditor.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function PlannerView({ gateway, setStatusbarItemGroup: _setStatusbarItemGroup, ...props }: PlannerViewProps) {
  const { locale } = useI18n()
  const copy = plannerCopy(locale)
  const [tab, setTab] = useRouteEnumParam<PlannerTab>('tab', PLANNER_TABS, 'overview')
  const [query, setQuery] = useState('')
  const [availabilityOpen, setAvailabilityOpen] = useState(false)
  const [calendarOpen, setCalendarOpen] = useState(false)
  const [calendarPreview, setCalendarPreview] = useState<null | PlannerScheduleCalendarPreview>(null)
  const [captureOpen, setCaptureOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<null | PlannerTask>(null)
  const [goalOpen, setGoalOpen] = useState(false)
  const [pendingAction, setPendingAction] = useState<null | PendingPlannerAction>(null)
  const [profileOpen, setProfileOpen] = useState(false)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [taskOpen, setTaskOpen] = useState(false)
  const [running, setRunning] = useState<null | string>(null)

  const overview = useQuery({
    enabled: Boolean(gateway),
    queryFn: () => loadPlannerOverview(gateway!),
    queryKey: ['personal-planner-overview'],
    refetchOnWindowFocus: true,
    staleTime: 15_000
  })

  const execute = useCallback(
    async (arg: string, message = copy.messages.actionComplete) => {
      if (!gateway || running) {
        return false
      }

      setRunning(arg)

      try {
        await runPlannerCommand(gateway, arg)
        await overview.refetch()
        notify({ kind: 'success', message: '', title: message })

        return true
      } catch (error) {
        notifyError(error, copy.error.title)

        return false
      } finally {
        setRunning(null)
      }
    },
    [copy.error.title, copy.messages.actionComplete, gateway, overview, running]
  )

  const capture = useCallback(
    async (text: string) => {
      const saved = await execute(`capture ${quotePlannerArg(text)}`, copy.capture.saved)

      if (saved) {
        setCaptureOpen(false)
      }
    },
    [copy.capture.saved, execute]
  )

  const profileValue = useMemo(() => profileFormValue(overview.data?.profile_facts ?? []), [overview.data])

  const saveProfile = useCallback(
    async (value: ProfileFormValue) => {
      if (!gateway || running) {
        return
      }

      const facts: PlannerProfileInput[] = PROFILE_FIELDS.filter(
        field => value[field.key].trim() && value[field.key].trim() !== profileValue[field.key].trim()
      ).map(field => ({
        category: field.category,
        factKey: field.factKey,
        factValue: value[field.key]
      }))

      if (facts.length === 0) {
        setProfileOpen(false)

        return
      }

      setRunning('profile-save')

      try {
        await saveConfirmedProfileFacts(gateway, facts)
        await runPlannerCommand(gateway, 'radar-propose')
        await overview.refetch()
        setProfileOpen(false)
        notify({ kind: 'success', message: '', title: copy.profileEditor.saved })
      } catch (error) {
        notifyError(error, copy.error.title)
      } finally {
        setRunning(null)
      }
    },
    [copy.error.title, copy.profileEditor.saved, gateway, overview, profileValue, running]
  )

  const confirmAllProfileFacts = useCallback(async () => {
    if (!gateway || running) {
      return
    }

    const pendingIds = overview.data?.profile_facts.filter(fact => fact.status === 'proposed').map(fact => fact.id)

    if (!pendingIds?.length) {
      return
    }

    setRunning('profile-confirm-all')

    try {
      for (const factId of pendingIds) {
        await runPlannerCommand(gateway, `profile-confirm ${factId}`)
      }

      await runPlannerCommand(gateway, 'radar-propose')
      await overview.refetch()
      notify({ kind: 'success', message: '', title: copy.profileEditor.saved })
    } catch (error) {
      notifyError(error, copy.error.title)
    } finally {
      setRunning(null)
    }
  }, [copy.error.title, copy.profileEditor.saved, gateway, overview, running])

  const editTask = useCallback((task?: PlannerTask) => {
    setEditingTask(task ?? null)
    setTaskOpen(true)
  }, [])

  const saveTask = useCallback(
    async (value: PlannerTaskInput) => {
      if (!gateway || running) {
        return
      }

      setRunning(editingTask ? `task-update ${editingTask.id}` : 'task-create')

      try {
        await savePlannerTask(gateway, value, editingTask?.id)
        await overview.refetch()
        setTaskOpen(false)
        setEditingTask(null)
        notify({ kind: 'success', message: '', title: copy.messages.actionComplete })
      } catch (error) {
        notifyError(error, copy.error.title)
      } finally {
        setRunning(null)
      }
    },
    [copy.error.title, copy.messages.actionComplete, editingTask, gateway, overview, running]
  )

  const saveGoal = useCallback(
    async (value: PlannerGoalInput) => {
      if (!gateway || running) {
        return
      }

      setRunning('goal-create')

      try {
        await savePlannerGoal(gateway, value)
        await overview.refetch()
        setGoalOpen(false)
        notify({ kind: 'success', message: '', title: copy.messages.actionComplete })
      } catch (error) {
        notifyError(error, copy.error.title)
      } finally {
        setRunning(null)
      }
    },
    [copy.error.title, copy.messages.actionComplete, gateway, overview, running]
  )

  const saveSource = useCallback(
    async (value: PlannerSourceInput) => {
      if (!gateway || running) {
        return
      }

      setRunning('source-create')

      try {
        await savePlannerSource(gateway, value)
        await overview.refetch()
        setSourceOpen(false)
        notify({ kind: 'success', message: '', title: copy.messages.actionComplete })
      } catch (error) {
        notifyError(error, copy.error.title)
      } finally {
        setRunning(null)
      }
    },
    [copy.error.title, copy.messages.actionComplete, gateway, overview, running]
  )

  const saveAvailability = useCallback(
    async (value: PlannerAvailabilityInput) => {
      const label = value.label.trim() ? ` ${quotePlannerArg(value.label.trim())}` : ''

      const saved = await execute(
        `availability-add ${value.weekday} ${value.startTime} ${value.endTime} ${value.energy}${label}`
      )

      if (saved) {
        setAvailabilityOpen(false)
      }
    },
    [execute]
  )

  const previewScheduleCalendar = useCallback(
    async (scheduleId: string) => {
      if (!gateway || running) {
        return
      }

      setRunning(`schedule-calendar-preview ${scheduleId}`)

      try {
        const preview = await loadScheduleCalendarPreview(gateway, scheduleId)
        setCalendarPreview(preview)
        setCalendarOpen(true)
      } catch (error) {
        notifyError(error, copy.error.title)
      } finally {
        setRunning(null)
      }
    },
    [copy.error.title, gateway, running]
  )

  const syncCalendar = useCallback(async () => {
    if (!calendarPreview || !gateway || running) {
      return
    }

    setRunning(`schedule-calendar-sync ${calendarPreview.plan_id}`)

    try {
      await syncScheduleCalendar(gateway, calendarPreview.plan_id)
      await overview.refetch()
      setCalendarOpen(false)
      setCalendarPreview(null)
      notify({ kind: 'success', message: '', title: copy.messages.actionComplete })
    } catch (error) {
      notifyError(error, copy.error.title)
    } finally {
      setRunning(null)
    }
  }, [calendarPreview, copy.error.title, copy.messages.actionComplete, gateway, overview, running])

  const confirmPendingAction = useCallback(async () => {
    if (!pendingAction) {
      return
    }

    const completed = await execute(pendingAction.arg)

    if (!completed) {
      throw new Error(copy.error.title)
    }
  }, [copy.error.title, execute, pendingAction])

  const counts = useMemo(() => {
    const data = overview.data

    return {
      goals: data?.goals.length ?? null,
      inbox: data?.items.filter(item => item.status !== 'done').length ?? null,
      radar: data?.monitor_proposals.length ?? null
    }
  }, [overview.data])

  const trailing = (
    <div className="flex items-center gap-1">
      <Button
        aria-label={copy.actions.refresh}
        disabled={overview.isFetching}
        onClick={() => void overview.refetch()}
        size="icon-titlebar"
        title={copy.actions.refresh}
        variant="ghost"
      >
        <RefreshCw className={cn(overview.isFetching && 'animate-spin')} />
      </Button>
      <Button onClick={() => setCaptureOpen(true)} size="xs">
        <Codicon name="add" />
        {copy.actions.capture}
      </Button>
    </div>
  )

  return (
    <PageSearchShell
      {...props}
      activeTab={tab}
      className="planner-surface"
      onSearchChange={setQuery}
      onTabChange={id => setTab(id as PlannerTab)}
      searchHidden={tab === 'overview'}
      searchPlaceholder={
        tab === 'inbox' ? copy.labels.inbox : tab === 'goals' ? copy.labels.tasks : copy.labels.monitoring
      }
      searchTrailingAction={trailing}
      searchValue={query}
      tabs={[
        { id: 'overview', label: copy.tabs.overview },
        { id: 'inbox', label: copy.tabs.inbox, meta: counts.inbox },
        { id: 'goals', label: copy.tabs.goals, meta: counts.goals },
        { id: 'radar', label: copy.tabs.radar, meta: counts.radar }
      ]}
    >
      {!gateway || overview.isLoading ? (
        <PageLoader label={copy.actions.refresh} />
      ) : overview.isError || !overview.data ? (
        <div className="grid h-full place-items-center px-5">
          <div className="max-w-md rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background) p-5 text-center">
            <AlertCircle className="mx-auto size-6 text-amber-500" />
            <h1 className="mt-3 text-sm font-semibold">{copy.error.title}</h1>
            <p className="mt-1.5 text-xs leading-5 text-muted-foreground/75">{copy.error.description}</p>
            <Button className="mt-4" onClick={() => void overview.refetch()} size="sm" variant="outline">
              {copy.actions.retry}
            </Button>
          </div>
        </div>
      ) : (
        <div className="h-full overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
          {tab === 'overview' && (
            <OverviewTab
              copy={copy}
              data={overview.data}
              locale={locale}
              onAction={arg => void execute(arg)}
              onConfirmAction={setPendingAction}
              query={query}
              running={running}
            />
          )}
          {tab === 'inbox' && (
            <InboxTab
              copy={copy}
              data={overview.data}
              locale={locale}
              onAction={arg => void execute(arg)}
              onConfirmAction={setPendingAction}
              query={query}
              running={running}
            />
          )}
          {tab === 'goals' && (
            <GoalsTab
              copy={copy}
              data={overview.data}
              locale={locale}
              onAction={arg => void execute(arg)}
              onAddAvailability={() => setAvailabilityOpen(true)}
              onAddGoal={() => setGoalOpen(true)}
              onCalendarPreview={scheduleId => void previewScheduleCalendar(scheduleId)}
              onConfirmAction={setPendingAction}
              onEditTask={editTask}
              query={query}
              running={running}
            />
          )}
          {tab === 'radar' && (
            <RadarTab
              copy={copy}
              data={overview.data}
              locale={locale}
              onAction={arg => void execute(arg)}
              onAddSource={() => setSourceOpen(true)}
              onConfirmAction={setPendingAction}
              onConfirmAll={() => void confirmAllProfileFacts()}
              onEditProfile={() => setProfileOpen(true)}
              query={query}
              running={running}
            />
          )}
        </div>
      )}
      <CaptureDialog
        copy={copy}
        onCapture={text => void capture(text)}
        onOpenChange={setCaptureOpen}
        open={captureOpen}
        running={running !== null}
      />
      <AvailabilityEditorDialog
        copy={copy}
        onOpenChange={setAvailabilityOpen}
        onSave={value => void saveAvailability(value)}
        open={availabilityOpen}
        running={running !== null}
      />
      <ScheduleCalendarDialog
        copy={copy}
        onConfirm={() => void syncCalendar()}
        onOpenChange={open => {
          setCalendarOpen(open)

          if (!open) {
            setCalendarPreview(null)
          }
        }}
        open={calendarOpen}
        preview={calendarPreview}
        running={running !== null}
      />
      <ProfileDialog
        copy={copy}
        initialValue={profileValue}
        onOpenChange={setProfileOpen}
        onSave={value => void saveProfile(value)}
        open={profileOpen}
        running={running !== null}
      />
      <GoalEditorDialog
        copy={copy}
        onOpenChange={setGoalOpen}
        onSave={value => void saveGoal(value)}
        open={goalOpen}
        running={running !== null}
      />
      <TaskEditorDialog
        copy={copy}
        goals={overview.data?.goals.filter(goal => goal.status === 'active') ?? []}
        initialTask={editingTask}
        onOpenChange={open => {
          setTaskOpen(open)

          if (!open) {
            setEditingTask(null)
          }
        }}
        onSave={value => void saveTask(value)}
        open={taskOpen}
        running={running !== null}
      />
      <SourceEditorDialog
        copy={copy}
        onOpenChange={setSourceOpen}
        onSave={value => void saveSource(value)}
        open={sourceOpen}
        running={running !== null}
      />
      <ConfirmDialog
        confirmLabel={pendingAction?.confirmLabel}
        description={pendingAction?.description}
        destructive
        onClose={() => setPendingAction(null)}
        onConfirm={confirmPendingAction}
        open={pendingAction !== null}
        title={pendingAction?.title ?? ''}
      />
    </PageSearchShell>
  )
}
