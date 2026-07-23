import { useEffect, useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Clock, Zap } from '@/lib/icons'
import { cn } from '@/lib/utils'

import type { PlannerAvailability, PlannerBlock, PlannerGoal, PlannerSchedule, PlannerTask } from './api'
import type { PlannerCopy } from './copy'

const HOUR_HEIGHT = 48
const FOCUSED_START_MINUTES = 7 * 60
const FOCUSED_END_MINUTES = 23 * 60
const MINUTES_PER_DAY = 24 * 60
const DAY_MS = 24 * 60 * 60 * 1000

type BlockAction = 'defer' | 'done' | 'skip'
type ScheduleMode = 'current' | 'draft'
type WeekOffset = 'current' | 'next'

interface WeekDay {
  date: Date
  dateLabel: string
  isToday: boolean
  key: string
  weekdayLabel: string
}

interface TimedPlacement {
  height: number
  top: number
}

interface GoalTone {
  block: string
  dot: string
}

const GOAL_TONES: GoalTone[] = [
  {
    block: 'border-sky-500/35 bg-sky-500/14 text-sky-950 hover:bg-sky-500/20 dark:text-sky-100',
    dot: 'bg-sky-500'
  },
  {
    block: 'border-emerald-500/35 bg-emerald-500/14 text-emerald-950 hover:bg-emerald-500/20 dark:text-emerald-100',
    dot: 'bg-emerald-500'
  },
  {
    block: 'border-amber-500/40 bg-amber-500/15 text-amber-950 hover:bg-amber-500/22 dark:text-amber-100',
    dot: 'bg-amber-500'
  },
  {
    block: 'border-rose-500/35 bg-rose-500/13 text-rose-950 hover:bg-rose-500/20 dark:text-rose-100',
    dot: 'bg-rose-500'
  },
  {
    block: 'border-cyan-500/35 bg-cyan-500/13 text-cyan-950 hover:bg-cyan-500/20 dark:text-cyan-100',
    dot: 'bg-cyan-500'
  }
]

const UNLINKED_TONE: GoalTone = {
  block: 'border-slate-500/30 bg-slate-500/12 text-slate-950 hover:bg-slate-500/18 dark:text-slate-100',
  dot: 'bg-slate-500'
}

const AVAILABILITY_TONES: Record<string, string> = {
  high: 'border-emerald-500/30 bg-emerald-500/7 text-emerald-800 dark:text-emerald-200/80',
  low: 'border-slate-500/25 bg-slate-500/5 text-slate-700 dark:text-slate-200/70',
  medium: 'border-sky-500/25 bg-sky-500/6 text-sky-800 dark:text-sky-200/80'
}

export interface WeeklyPlannerProps {
  activeSchedule: null | PlannerSchedule
  availability: PlannerAvailability[]
  copy: PlannerCopy
  draftSchedule: null | PlannerSchedule
  generatedAt: string
  goals: PlannerGoal[]
  locale: string
  onAddAvailability: () => void
  onBlockAction: (block: PlannerBlock, action: BlockAction) => void
  onCancelSchedule: (schedule: PlannerSchedule) => void
  onConfirmDraft: (schedule: PlannerSchedule) => void
  onGeneratePlan: () => void
  onRemoveAvailability: (window: PlannerAvailability) => void
  onSyncCalendar: (schedule: PlannerSchedule) => void
  running: boolean
  tasks: PlannerTask[]
  timeZone: string
}

export function WeeklyPlanner({
  activeSchedule,
  availability,
  copy,
  draftSchedule,
  generatedAt,
  goals,
  locale,
  onAddAvailability,
  onBlockAction,
  onCancelSchedule,
  onConfirmDraft,
  onGeneratePlan,
  onRemoveAvailability,
  onSyncCalendar,
  running,
  tasks,
  timeZone
}: WeeklyPlannerProps) {
  const draftId = draftSchedule?.id ?? null
  const [fullDay, setFullDay] = useState(false)
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>(draftSchedule ? 'draft' : 'current')
  const [weekOffset, setWeekOffset] = useState<WeekOffset>('current')

  useEffect(() => {
    setScheduleMode(draftId ? 'draft' : 'current')
  }, [draftId])

  const visibleStart = fullDay ? 0 : FOCUSED_START_MINUTES
  const visibleEnd = fullDay ? MINUTES_PER_DAY : FOCUSED_END_MINUTES

  const days = useMemo(
    () => buildWeekDays(generatedAt, timeZone, weekOffset === 'next' ? 1 : 0, locale),
    [generatedAt, locale, timeZone, weekOffset]
  )

  const selectedSchedule = scheduleMode === 'draft' ? draftSchedule : activeSchedule
  const blocks = selectedSchedule?.blocks ?? []

  const visibleBlockCount = blocks.filter(
    block =>
      days.some(day => day.key === zonedDateKey(block.starts_at, timeZone)) &&
      getBlockPlacement(block, timeZone, visibleStart, visibleEnd)
  ).length

  const tasksById = useMemo(() => new Map(tasks.map(task => [task.id, task])), [tasks])
  const goalsById = useMemo(() => new Map(goals.map(goal => [goal.id, goal])), [goals])
  const rangeLabel = formatWeekRange(days, locale)
  const hourMarkers = buildHourMarkers(visibleStart, visibleEnd)
  const gridHeight = ((visibleEnd - visibleStart) / 60) * HOUR_HEIGHT
  const nowPlacement = getCurrentTimePlacement(generatedAt, timeZone, days, visibleStart, visibleEnd)

  const scheduleOptions = [
    { id: 'current' as const, label: copy.weekPlanner.currentPlan },
    { id: 'draft' as const, label: copy.weekPlanner.draftPlan }
  ]

  const weekOptions = [
    { id: 'current' as const, label: copy.weekPlanner.thisWeek },
    { id: 'next' as const, label: copy.weekPlanner.nextWeek }
  ]

  return (
    <section className="overflow-hidden rounded-2xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background)">
      <header className="border-b border-(--ui-stroke-tertiary) bg-[linear-gradient(100deg,color-mix(in_srgb,var(--ui-bg-elevated)_96%,transparent),color-mix(in_srgb,var(--primary)_5%,var(--ui-bg-elevated)))] px-4 py-3.5 sm:px-5">
        <div className="flex flex-wrap items-start gap-x-5 gap-y-3">
          <div className="min-w-56 flex-1">
            <div className="flex items-center gap-2">
              <span className="grid size-7 place-items-center rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) text-primary">
                <Codicon name="calendar" size="0.9rem" />
              </span>
              <div>
                <h2 className="text-sm font-semibold tracking-[-0.02em] text-foreground">{copy.weekPlanner.title}</h2>
                <p className="mt-0.5 text-[0.65rem] text-muted-foreground/70">{rangeLabel}</p>
              </div>
            </div>
            <p className="mt-2 max-w-2xl text-[0.68rem] leading-5 text-muted-foreground/70">
              {copy.weekPlanner.description}
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <SegmentedControl onChange={setWeekOffset} options={weekOptions} value={weekOffset} />
            {draftSchedule && (
              <SegmentedControl onChange={setScheduleMode} options={scheduleOptions} value={scheduleMode} />
            )}
            <Button onClick={() => setFullDay(value => !value)} size="xs" variant="outline">
              <Clock />
              {fullDay ? copy.weekPlanner.focusHours : copy.weekPlanner.fullDay}
            </Button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-(--ui-stroke-tertiary) pt-3">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3 text-[0.62rem] text-muted-foreground/70">
            <LegendSwatch className="border-dashed border-sky-500/30 bg-sky-500/7" label={copy.weekPlanner.available} />
            <LegendSwatch className="border-sky-500/35 bg-sky-500/18" label={copy.weekPlanner.confirmedBlock} />
            <LegendSwatch
              className="border-dashed border-amber-500/45 bg-amber-500/10"
              label={copy.weekPlanner.draftBlock}
            />
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Button disabled={running} onClick={onAddAvailability} size="xs" variant="outline">
              <Codicon name="add" />
              {copy.actions.addAvailability}
            </Button>
            {scheduleMode === 'current' && activeSchedule && (
              <>
                <Button disabled={running} onClick={() => onSyncCalendar(activeSchedule)} size="xs" variant="outline">
                  {copy.actions.syncCalendar}
                </Button>
                <Button disabled={running} onClick={() => onCancelSchedule(activeSchedule)} size="xs" variant="text">
                  {copy.actions.cancelPlan}
                </Button>
              </>
            )}
            {scheduleMode === 'draft' && draftSchedule && (
              <>
                <Button disabled={running} onClick={() => onConfirmDraft(draftSchedule)} size="xs">
                  {copy.actions.confirmPlan}
                </Button>
                <Button disabled={running} onClick={() => onCancelSchedule(draftSchedule)} size="xs" variant="text">
                  {copy.actions.cancelPlan}
                </Button>
              </>
            )}
            <Button disabled={running} onClick={onGeneratePlan} size="xs">
              {copy.actions.generatePlan}
            </Button>
          </div>
        </div>
      </header>

      <div className="max-h-[min(68vh,46rem)] overflow-auto overscroll-contain [scrollbar-gutter:stable]">
        <div className="min-w-[61rem]">
          <div className="sticky top-0 z-30 grid grid-cols-[4.25rem_repeat(7,minmax(7.75rem,1fr))] border-b border-(--ui-stroke-secondary) bg-[color-mix(in_srgb,var(--ui-bg-elevated)_96%,transparent)] backdrop-blur-md">
            <div className="sticky left-0 z-40 border-r border-(--ui-stroke-secondary) bg-inherit px-2 py-2 text-center text-[0.58rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground/55">
              {copy.weekPlanner.time}
            </div>
            {days.map(day => (
              <div
                className={cn(
                  'border-r border-(--ui-stroke-tertiary) px-2 py-2 text-center last:border-r-0',
                  day.isToday && 'bg-primary/5'
                )}
                key={day.key}
              >
                <div
                  className={cn('text-[0.63rem] font-semibold text-muted-foreground', day.isToday && 'text-primary')}
                >
                  {day.weekdayLabel}
                </div>
                <div className="mt-0.5 text-[0.6rem] tabular-nums text-muted-foreground/55">{day.dateLabel}</div>
              </div>
            ))}
          </div>

          <div
            className="relative grid grid-cols-[4.25rem_repeat(7,minmax(7.75rem,1fr))]"
            style={{ height: gridHeight }}
          >
            <div
              className="sticky left-0 z-20 border-r border-(--ui-stroke-secondary) bg-[color-mix(in_srgb,var(--ui-chat-bubble-background)_97%,transparent)]"
              style={{ height: gridHeight }}
            >
              {hourMarkers.map(marker => (
                <div
                  className="absolute right-2 -translate-y-1/2 text-[0.58rem] tabular-nums text-muted-foreground/50"
                  key={marker.minutes}
                  style={{ top: marker.top }}
                >
                  {marker.label}
                </div>
              ))}
            </div>

            {days.map((day, dayIndex) => {
              const dayBlocks = blocks.filter(block => zonedDateKey(block.starts_at, timeZone) === day.key)

              const dayAvailability = availability.filter(
                window => Number(window.weekday) === dayIndex && window.enabled
              )

              return (
                <div
                  className={cn(
                    'relative border-r border-(--ui-stroke-tertiary) last:border-r-0',
                    day.isToday && 'bg-primary/[0.018]'
                  )}
                  data-testid={`planner-day-${day.key}`}
                  key={day.key}
                  style={{
                    backgroundImage:
                      'repeating-linear-gradient(to bottom, transparent 0, transparent 23px, color-mix(in srgb, var(--ui-stroke-tertiary) 48%, transparent) 23px, color-mix(in srgb, var(--ui-stroke-tertiary) 48%, transparent) 24px)',
                    height: gridHeight
                  }}
                >
                  {dayAvailability.map(window => {
                    const placement = getAvailabilityPlacement(window, visibleStart, visibleEnd)

                    if (!placement) {
                      return null
                    }

                    return (
                      <AvailabilityWindow
                        copy={copy}
                        key={window.id}
                        onRemove={() => onRemoveAvailability(window)}
                        placement={placement}
                        running={running}
                        window={window}
                      />
                    )
                  })}

                  {dayBlocks.map(block => {
                    const placement = getBlockPlacement(block, timeZone, visibleStart, visibleEnd)

                    if (!placement) {
                      return null
                    }

                    const task = tasksById.get(block.task_id)
                    const goal = task?.goal_id ? goalsById.get(task.goal_id) : undefined

                    return (
                      <ScheduleBlock
                        block={block}
                        copy={copy}
                        goal={goal}
                        isDraft={scheduleMode === 'draft'}
                        key={block.id}
                        locale={locale}
                        onAction={action => onBlockAction(block, action)}
                        placement={placement}
                        running={running}
                        task={task}
                        timeZone={timeZone}
                      />
                    )
                  })}

                  {nowPlacement?.dayKey === day.key && (
                    <div
                      aria-label={copy.weekPlanner.now}
                      className="pointer-events-none absolute inset-x-0 z-20 border-t border-rose-500/80"
                      style={{ top: nowPlacement.top }}
                    >
                      <span className="absolute -left-1 -top-1 size-2 rounded-full bg-rose-500 shadow-[0_0_0_3px_color-mix(in_srgb,var(--ui-chat-bubble-background)_80%,transparent)]" />
                    </div>
                  )}
                </div>
              )
            })}

            {visibleBlockCount === 0 && (
              <div className="pointer-events-none absolute left-[4.25rem] right-0 top-4 z-10 flex justify-center">
                <span className="rounded-full border border-(--ui-stroke-tertiary) bg-[color-mix(in_srgb,var(--ui-bg-elevated)_90%,transparent)] px-3 py-1 text-[0.62rem] text-muted-foreground/65 backdrop-blur-sm">
                  {scheduleMode === 'draft' ? copy.weekPlanner.noDraftBlocks : copy.weekPlanner.noConfirmedBlocks}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}

function LegendSwatch({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn('size-3 rounded-[3px] border', className)} />
      {label}
    </span>
  )
}

function AvailabilityWindow({
  copy,
  onRemove,
  placement,
  running,
  window
}: {
  copy: PlannerCopy
  onRemove: () => void
  placement: TimedPlacement
  running: boolean
  window: PlannerAvailability
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          aria-label={`${copy.weekPlanner.available}: ${window.start_time}-${window.end_time}`}
          className={cn(
            'absolute inset-x-1 z-0 overflow-hidden rounded-md border border-dashed px-1.5 py-1 text-left transition-colors hover:brightness-110',
            AVAILABILITY_TONES[window.energy] ?? AVAILABILITY_TONES.medium
          )}
          data-testid={`availability-window-${window.id}`}
          style={{ height: placement.height, top: placement.top }}
          type="button"
        >
          {placement.height >= 30 && (
            <span className="block truncate text-[0.55rem] font-medium opacity-75">
              {window.label || copy.weekPlanner.available}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-3">
        <div className="text-xs font-semibold text-foreground">{window.label || copy.weekPlanner.available}</div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <Badge variant="outline">
            {window.start_time}–{window.end_time}
          </Badge>
          <Badge variant="muted">{window.energy}</Badge>
        </div>
        <p className="mt-2 text-[0.65rem] leading-5 text-muted-foreground/70">{copy.weekPlanner.availabilityHint}</p>
        <Button className="mt-2" disabled={running} onClick={onRemove} size="xs" variant="text">
          {copy.actions.remove}
        </Button>
      </PopoverContent>
    </Popover>
  )
}

function ScheduleBlock({
  block,
  copy,
  goal,
  isDraft,
  locale,
  onAction,
  placement,
  running,
  task,
  timeZone
}: {
  block: PlannerBlock
  copy: PlannerCopy
  goal?: PlannerGoal
  isDraft: boolean
  locale: string
  onAction: (action: BlockAction) => void
  placement: TimedPlacement
  running: boolean
  task?: PlannerTask
  timeZone: string
}) {
  const tone = goalTone(goal)
  const checkedIn = Boolean(block.checkin?.outcome)

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          aria-label={`${block.title}, ${formatTimeRange(block.starts_at, block.ends_at, locale, timeZone)}`}
          className={cn(
            'absolute inset-x-1.5 z-10 overflow-hidden rounded-md border px-1.5 py-1 text-left shadow-[0_1px_0_color-mix(in_srgb,var(--ui-stroke-secondary)_35%,transparent)] transition-[filter,transform] hover:z-20 hover:-translate-y-px hover:brightness-105 focus-visible:z-20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
            tone.block,
            isDraft && 'border-dashed bg-transparent backdrop-blur-[1px]',
            checkedIn && 'opacity-50 saturate-50'
          )}
          data-testid={`schedule-block-${block.id}`}
          style={{ height: placement.height, top: placement.top }}
          type="button"
        >
          <span className="block truncate text-[0.62rem] font-semibold leading-3.5">{block.title}</span>
          {placement.height >= 38 && (
            <span className="mt-0.5 block truncate text-[0.54rem] tabular-nums opacity-65">
              {formatTimeRange(block.starts_at, block.ends_at, locale, timeZone)}
            </span>
          )}
          {checkedIn && placement.height >= 56 && (
            <span className="mt-1 inline-block rounded-sm bg-black/8 px-1 text-[0.5rem] font-semibold uppercase dark:bg-white/10">
              {block.checkin?.outcome}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 p-3">
        <div className="flex items-start gap-2">
          <span className={cn('mt-1 size-2 shrink-0 rounded-full', tone.dot)} />
          <div className="min-w-0 flex-1">
            <div className="text-xs font-semibold leading-5 text-foreground">{block.title}</div>
            <div className="mt-0.5 text-[0.64rem] tabular-nums text-muted-foreground/65">
              {formatTimeRange(block.starts_at, block.ends_at, locale, timeZone)} ·{' '}
              {formatDuration(block.minutes, locale)}
            </div>
          </div>
          <Badge variant={isDraft ? 'warn' : 'outline'}>
            {isDraft ? copy.weekPlanner.draft : copy.weekPlanner.confirmed}
          </Badge>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) p-2 text-[0.62rem]">
          <BlockFact label={copy.weekPlanner.goal} value={goal?.title || copy.weekPlanner.noGoal} />
          <BlockFact label={copy.weekPlanner.energy} value={task?.energy || '—'} />
          <BlockFact label={copy.weekPlanner.priority} value={task?.priority || '—'} />
          <BlockFact
            label={copy.weekPlanner.taskProgress}
            value={task ? `${task.completed_minutes}/${task.estimated_minutes}m` : '—'}
          />
        </div>
        <div className="mt-2 rounded-md border border-(--ui-stroke-tertiary) px-2.5 py-2">
          <div className="flex items-center gap-1.5 text-[0.62rem] font-semibold text-foreground/75">
            <Zap className="size-3 text-amber-500" />
            {copy.weekPlanner.whyThisTime}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2 text-[0.6rem]">
            <BlockFact
              label={copy.weekPlanner.availabilityReason}
              value={block.availability_label || copy.weekPlanner.available}
            />
            <BlockFact label={copy.weekPlanner.energyReason} value={block.energy || task?.energy || '—'} />
            <BlockFact label={copy.weekPlanner.priorityReason} value={task?.priority || '—'} />
            <BlockFact label={copy.weekPlanner.deadlineReason} value={formatTaskDue(task, locale, timeZone)} />
            <BlockFact
              label={copy.weekPlanner.goalTargetReason}
              value={goal ? formatDuration(goal.weekly_target_minutes, locale) : '—'}
            />
            <BlockFact label={copy.weekPlanner.schedulerScore} value={String(block.priority_score ?? '—')} />
          </div>
        </div>
        {isDraft ? (
          <p className="mt-2 text-[0.65rem] leading-5 text-amber-700 dark:text-amber-300/80">
            {copy.weekPlanner.draftHint}
          </p>
        ) : checkedIn ? (
          <p className="mt-2 text-[0.65rem] text-muted-foreground/70">
            {copy.weekPlanner.checkinRecorded}: {block.checkin?.outcome}
          </p>
        ) : (
          <div className="mt-3 flex flex-wrap gap-1.5">
            <Button disabled={running} onClick={() => onAction('done')} size="xs">
              {copy.actions.done}
            </Button>
            <Button disabled={running} onClick={() => onAction('defer')} size="xs" variant="outline">
              {copy.actions.defer}
            </Button>
            <Button disabled={running} onClick={() => onAction('skip')} size="xs" variant="text">
              {copy.actions.skip}
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

function BlockFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground/55">{label}</div>
      <div className="mt-0.5 truncate font-medium text-foreground/80">{value}</div>
    </div>
  )
}

export function buildWeekDays(generatedAt: string, timeZone: string, weekOffset: number, locale: string): WeekDay[] {
  const generated = zonedParts(generatedAt, timeZone)
  const generatedOrdinal = Date.UTC(generated.year, generated.month - 1, generated.day)
  const mondayOffset = (new Date(generatedOrdinal).getUTCDay() + 6) % 7
  const monday = generatedOrdinal - mondayOffset * DAY_MS + weekOffset * 7 * DAY_MS
  const todayKey = dateKey(generated.year, generated.month, generated.day)

  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date(monday + index * DAY_MS)
    const key = dateKey(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate())

    return {
      date,
      dateLabel: new Intl.DateTimeFormat(localeTag(locale), {
        day: 'numeric',
        month: 'short',
        timeZone: 'UTC'
      }).format(date),
      isToday: key === todayKey,
      key,
      weekdayLabel: new Intl.DateTimeFormat(localeTag(locale), { timeZone: 'UTC', weekday: 'short' }).format(date)
    }
  })
}

export function getTimedPlacement(
  startMinutes: number,
  endMinutes: number,
  visibleStart: number,
  visibleEnd: number
): null | TimedPlacement {
  const clippedStart = Math.max(startMinutes, visibleStart)
  const clippedEnd = Math.min(endMinutes, visibleEnd)

  if (clippedEnd <= clippedStart) {
    return null
  }

  return {
    height: Math.max(18, ((clippedEnd - clippedStart) / 60) * HOUR_HEIGHT),
    top: ((clippedStart - visibleStart) / 60) * HOUR_HEIGHT
  }
}

function getAvailabilityPlacement(
  window: PlannerAvailability,
  visibleStart: number,
  visibleEnd: number
): null | TimedPlacement {
  return getTimedPlacement(parseClock(window.start_time), parseClock(window.end_time), visibleStart, visibleEnd)
}

function getBlockPlacement(
  block: PlannerBlock,
  timeZone: string,
  visibleStart: number,
  visibleEnd: number
): null | TimedPlacement {
  const start = zonedParts(block.starts_at, timeZone)
  const end = zonedParts(block.ends_at, timeZone)
  const startMinutes = start.hour * 60 + start.minute
  const sameDay = dateKey(start.year, start.month, start.day) === dateKey(end.year, end.month, end.day)
  const endMinutes = sameDay ? end.hour * 60 + end.minute : MINUTES_PER_DAY

  return getTimedPlacement(startMinutes, endMinutes, visibleStart, visibleEnd)
}

function getCurrentTimePlacement(
  generatedAt: string,
  timeZone: string,
  days: WeekDay[],
  visibleStart: number,
  visibleEnd: number
): null | { dayKey: string; top: number } {
  const current = zonedParts(generatedAt, timeZone)
  const key = dateKey(current.year, current.month, current.day)
  const minutes = current.hour * 60 + current.minute

  if (!days.some(day => day.key === key) || minutes < visibleStart || minutes > visibleEnd) {
    return null
  }

  return {
    dayKey: key,
    top: ((minutes - visibleStart) / 60) * HOUR_HEIGHT
  }
}

function buildHourMarkers(start: number, end: number): Array<{ label: string; minutes: number; top: number }> {
  const firstHour = Math.ceil(start / 60)
  const lastHour = Math.floor(end / 60)

  return Array.from({ length: lastHour - firstHour + 1 }, (_, index) => {
    const hour = firstHour + index
    const minutes = hour * 60

    return {
      label: `${String(hour).padStart(2, '0')}:00`,
      minutes,
      top: ((minutes - start) / 60) * HOUR_HEIGHT
    }
  })
}

function zonedParts(value: string, timeZone: string) {
  const parts = new Intl.DateTimeFormat('en-US', {
    day: '2-digit',
    hour: '2-digit',
    hourCycle: 'h23',
    minute: '2-digit',
    month: '2-digit',
    timeZone,
    year: 'numeric'
  }).formatToParts(new Date(value))

  const part = (name: Intl.DateTimeFormatPartTypes) => Number(parts.find(valuePart => valuePart.type === name)?.value)

  return {
    day: part('day'),
    hour: part('hour'),
    minute: part('minute'),
    month: part('month'),
    year: part('year')
  }
}

function zonedDateKey(value: string, timeZone: string): string {
  const parts = zonedParts(value, timeZone)

  return dateKey(parts.year, parts.month, parts.day)
}

function dateKey(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

function parseClock(value: string): number {
  const [hour = 0, minute = 0] = value.split(':').map(Number)

  return hour * 60 + minute
}

function formatWeekRange(days: WeekDay[], locale: string): string {
  if (days.length === 0) {
    return ''
  }

  const formatter = new Intl.DateTimeFormat(localeTag(locale), {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
    year: 'numeric'
  })

  return `${formatter.format(days[0].date)} – ${formatter.format(days[days.length - 1].date)}`
}

function formatTimeRange(start: string, end: string, locale: string, timeZone: string): string {
  const formatter = new Intl.DateTimeFormat(localeTag(locale), {
    hour: '2-digit',
    hourCycle: 'h23',
    minute: '2-digit',
    timeZone
  })

  return `${formatter.format(new Date(start))}–${formatter.format(new Date(end))}`
}

function formatDuration(minutes: number, locale: string): string {
  if (locale.startsWith('zh')) {
    return minutes < 60 ? `${minutes} 分钟` : `${Number((minutes / 60).toFixed(1))} 小时`
  }

  return minutes < 60 ? `${minutes}m` : `${Number((minutes / 60).toFixed(1))}h`
}

function formatTaskDue(task: PlannerTask | undefined, locale: string, timeZone: string): string {
  if (!task?.due_at) {
    return '—'
  }

  return new Intl.DateTimeFormat(localeTag(locale), {
    dateStyle: 'medium',
    timeZone
  }).format(new Date(task.due_at))
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

function goalTone(goal?: PlannerGoal): GoalTone {
  if (!goal) {
    return UNLINKED_TONE
  }

  const material = `${goal.id}:${goal.area}`
  let hash = 0

  for (const character of material) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0
  }

  return GOAL_TONES[hash % GOAL_TONES.length]
}
