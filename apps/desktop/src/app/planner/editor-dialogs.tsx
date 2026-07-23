import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

import type {
  PlannerGoal,
  PlannerGoalInput,
  PlannerScheduleCalendarPreview,
  PlannerSourceInput,
  PlannerTask,
  PlannerTaskInput
} from './api'
import type { PlannerCopy } from './copy'

export interface PlannerAvailabilityInput {
  endTime: string
  energy: string
  label: string
  startTime: string
  weekday: string
}

interface TaskEditorDialogProps {
  copy: PlannerCopy
  goals: PlannerGoal[]
  initialTask: null | PlannerTask
  onOpenChange: (open: boolean) => void
  onSave: (value: PlannerTaskInput) => void
  open: boolean
  running: boolean
}

function localDateTimeValue(value?: null | string): string {
  if (!value) {
    return ''
  }

  const date = new Date(value)

  if (Number.isNaN(date.valueOf())) {
    return ''
  }

  return new Date(date.valueOf() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function isoDateTimeValue(value: string): null | string {
  if (!value) {
    return null
  }

  const date = new Date(value)

  return Number.isNaN(date.valueOf()) ? null : date.toISOString()
}

function initialTaskValue(task: null | PlannerTask): PlannerTaskInput {
  return {
    due_at: task?.due_at ?? null,
    earliest_start: task?.earliest_start ?? null,
    energy: task?.energy ?? 'medium',
    estimated_minutes: task?.estimated_minutes ?? 60,
    goal_id: task?.goal_id ?? null,
    min_block_minutes: task?.min_block_minutes ?? 30,
    notes: task?.notes ?? '',
    priority: task?.priority ?? 'normal',
    splittable: task ? Boolean(task.splittable) : true,
    title: task?.title ?? ''
  }
}

export function TaskEditorDialog({
  copy,
  goals,
  initialTask,
  onOpenChange,
  onSave,
  open,
  running
}: TaskEditorDialogProps) {
  const [value, setValue] = useState<PlannerTaskInput>(() => initialTaskValue(initialTask))

  useEffect(() => {
    if (open) {
      setValue(initialTaskValue(initialTask))
    }
  }, [initialTask, open])

  const valid =
    Boolean(value.title.trim()) &&
    value.estimated_minutes >= 5 &&
    value.min_block_minutes >= 5 &&
    value.min_block_minutes <= value.estimated_minutes

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{copy.taskEditor.title}</DialogTitle>
          <DialogDescription>{copy.taskEditor.description}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.taskEditor.titleField}</span>
            <Input
              autoFocus
              onChange={event => setValue(current => ({ ...current, title: event.target.value }))}
              value={value.title}
            />
          </label>
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.taskEditor.goal}</span>
            <Select
              onValueChange={goalId =>
                setValue(current => ({ ...current, goal_id: goalId === 'none' ? null : goalId }))
              }
              value={value.goal_id ?? 'none'}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">{copy.taskEditor.noGoal}</SelectItem>
                {goals.map(goal => (
                  <SelectItem key={goal.id} value={goal.id}>
                    {goal.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.taskEditor.estimate}</span>
            <Input
              inputMode="numeric"
              min={5}
              onChange={event =>
                setValue(current => ({ ...current, estimated_minutes: Number(event.target.value) || 0 }))
              }
              type="number"
              value={value.estimated_minutes}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.taskEditor.minBlock}</span>
            <Input
              inputMode="numeric"
              min={5}
              onChange={event =>
                setValue(current => ({ ...current, min_block_minutes: Number(event.target.value) || 0 }))
              }
              type="number"
              value={value.min_block_minutes}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.taskEditor.energy}</span>
            <Select onValueChange={energy => setValue(current => ({ ...current, energy }))} value={value.energy}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="low">low</SelectItem>
                <SelectItem value="medium">medium</SelectItem>
                <SelectItem value="high">high</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.taskEditor.priority}</span>
            <Select onValueChange={priority => setValue(current => ({ ...current, priority }))} value={value.priority}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="low">low</SelectItem>
                <SelectItem value="normal">normal</SelectItem>
                <SelectItem value="high">high</SelectItem>
                <SelectItem value="critical">critical</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.taskEditor.earliest}</span>
            <Input
              onChange={event =>
                setValue(current => ({ ...current, earliest_start: isoDateTimeValue(event.target.value) }))
              }
              type="datetime-local"
              value={localDateTimeValue(value.earliest_start)}
            />
          </label>
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.taskEditor.due}</span>
            <Input
              onChange={event => setValue(current => ({ ...current, due_at: isoDateTimeValue(event.target.value) }))}
              type="datetime-local"
              value={localDateTimeValue(value.due_at)}
            />
          </label>
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.taskEditor.notes}</span>
            <Textarea
              className="min-h-20 resize-y"
              onChange={event => setValue(current => ({ ...current, notes: event.target.value }))}
              placeholder={copy.taskEditor.notesPlaceholder}
              value={value.notes ?? ''}
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-foreground/75 sm:col-span-2">
            <Checkbox
              checked={value.splittable}
              onCheckedChange={checked => setValue(current => ({ ...current, splittable: checked === true }))}
            />
            {copy.taskEditor.splittable}
          </label>
        </div>
        <DialogFooter>
          <Button disabled={running} onClick={() => onOpenChange(false)} variant="text">
            {copy.taskEditor.cancel}
          </Button>
          <Button disabled={!valid || running} onClick={() => onSave(value)}>
            {copy.taskEditor.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface GoalEditorDialogProps {
  copy: PlannerCopy
  onOpenChange: (open: boolean) => void
  onSave: (value: PlannerGoalInput) => void
  open: boolean
  running: boolean
}

const EMPTY_GOAL: PlannerGoalInput = {
  area: 'personal',
  outcome: '',
  priority: 'normal',
  target_date: null,
  title: '',
  weekly_target_minutes: 180
}

export function GoalEditorDialog({ copy, onOpenChange, onSave, open, running }: GoalEditorDialogProps) {
  const [value, setValue] = useState<PlannerGoalInput>(EMPTY_GOAL)

  useEffect(() => {
    if (open) {
      setValue(EMPTY_GOAL)
    }
  }, [open])

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{copy.goalEditor.title}</DialogTitle>
          <DialogDescription>{copy.goalEditor.description}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.goalEditor.titleField}</span>
            <Input
              autoFocus
              onChange={event => setValue(current => ({ ...current, title: event.target.value }))}
              value={value.title}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.goalEditor.area}</span>
            <Input
              onChange={event => setValue(current => ({ ...current, area: event.target.value }))}
              placeholder={copy.goalEditor.areaPlaceholder}
              value={value.area}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.goalEditor.priority}</span>
            <Select onValueChange={priority => setValue(current => ({ ...current, priority }))} value={value.priority}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="low">low</SelectItem>
                <SelectItem value="normal">normal</SelectItem>
                <SelectItem value="high">high</SelectItem>
                <SelectItem value="critical">critical</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.goalEditor.targetDate}</span>
            <Input
              onChange={event => setValue(current => ({ ...current, target_date: event.target.value || null }))}
              type="date"
              value={value.target_date ?? ''}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.goalEditor.weeklyMinutes}</span>
            <Input
              inputMode="numeric"
              min={0}
              onChange={event =>
                setValue(current => ({ ...current, weekly_target_minutes: Number(event.target.value) || 0 }))
              }
              type="number"
              value={value.weekly_target_minutes}
            />
          </label>
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.goalEditor.outcome}</span>
            <Textarea
              className="min-h-20 resize-y"
              onChange={event => setValue(current => ({ ...current, outcome: event.target.value }))}
              placeholder={copy.goalEditor.outcomePlaceholder}
              value={value.outcome ?? ''}
            />
          </label>
        </div>
        <DialogFooter>
          <Button disabled={running} onClick={() => onOpenChange(false)} variant="text">
            {copy.goalEditor.cancel}
          </Button>
          <Button disabled={!value.title.trim() || running} onClick={() => onSave(value)}>
            {copy.goalEditor.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface ScheduleCalendarDialogProps {
  copy: PlannerCopy
  onConfirm: () => void
  onOpenChange: (open: boolean) => void
  open: boolean
  preview: null | PlannerScheduleCalendarPreview
  running: boolean
}

export function ScheduleCalendarDialog({
  copy,
  onConfirm,
  onOpenChange,
  open,
  preview,
  running
}: ScheduleCalendarDialogProps) {
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{copy.calendarPreview.title}</DialogTitle>
          <DialogDescription>{copy.calendarPreview.description}</DialogDescription>
        </DialogHeader>
        <div className="max-h-[50vh] overflow-y-auto rounded-lg border border-(--ui-stroke-tertiary)">
          {!preview?.events.length ? (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">{copy.calendarPreview.empty}</p>
          ) : (
            <div className="divide-y divide-(--ui-stroke-tertiary)">
              {preview.events.map(event => (
                <div className="flex items-center gap-3 px-3.5 py-2.5" key={event.block_id}>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-foreground/90">{event.summary}</div>
                    <div className="mt-0.5 text-[0.62rem] text-muted-foreground/60">
                      {new Date(event.start).toLocaleString()} – {new Date(event.end).toLocaleTimeString()}
                    </div>
                  </div>
                  <span className="text-[0.62rem] text-muted-foreground">{event.sync_status}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button disabled={running} onClick={() => onOpenChange(false)} variant="text">
            {copy.calendarPreview.cancel}
          </Button>
          <Button
            disabled={running || !preview?.events.some(event => event.sync_status === 'ready')}
            onClick={onConfirm}
          >
            {copy.calendarPreview.confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface SourceEditorDialogProps {
  copy: PlannerCopy
  onOpenChange: (open: boolean) => void
  onSave: (value: PlannerSourceInput) => void
  open: boolean
  running: boolean
}

const EMPTY_SOURCE: PlannerSourceInput = {
  importance: 'normal',
  max_results: 25,
  name: '',
  query: 'newer_than:30d'
}

export function SourceEditorDialog({ copy, onOpenChange, onSave, open, running }: SourceEditorDialogProps) {
  const [value, setValue] = useState<PlannerSourceInput>(EMPTY_SOURCE)

  useEffect(() => {
    if (open) {
      setValue(EMPTY_SOURCE)
    }
  }, [open])

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{copy.sourceEditor.title}</DialogTitle>
          <DialogDescription>{copy.sourceEditor.description}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.sourceEditor.name}</span>
            <Input
              autoFocus
              onChange={event => setValue(current => ({ ...current, name: event.target.value }))}
              placeholder={copy.sourceEditor.namePlaceholder}
              value={value.name}
            />
          </label>
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.sourceEditor.query}</span>
            <Input
              onChange={event => setValue(current => ({ ...current, query: event.target.value }))}
              placeholder={copy.sourceEditor.queryPlaceholder}
              value={value.query}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.sourceEditor.importance}</span>
            <Select
              onValueChange={importance => setValue(current => ({ ...current, importance }))}
              value={value.importance}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="low">low</SelectItem>
                <SelectItem value="normal">normal</SelectItem>
                <SelectItem value="high">high</SelectItem>
                <SelectItem value="critical">critical</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.sourceEditor.maxResults}</span>
            <Input
              inputMode="numeric"
              max={100}
              min={1}
              onChange={event => setValue(current => ({ ...current, max_results: Number(event.target.value) || 0 }))}
              type="number"
              value={value.max_results}
            />
          </label>
        </div>
        <DialogFooter>
          <Button disabled={running} onClick={() => onOpenChange(false)} variant="text">
            {copy.sourceEditor.cancel}
          </Button>
          <Button
            disabled={!value.name.trim() || !value.query.trim() || value.max_results < 1 || running}
            onClick={() => onSave(value)}
          >
            {copy.sourceEditor.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface AvailabilityEditorDialogProps {
  copy: PlannerCopy
  onOpenChange: (open: boolean) => void
  onSave: (value: PlannerAvailabilityInput) => void
  open: boolean
  running: boolean
}

const EMPTY_AVAILABILITY: PlannerAvailabilityInput = {
  endTime: '21:00',
  energy: 'medium',
  label: '',
  startTime: '19:00',
  weekday: '0'
}

export function AvailabilityEditorDialog({ copy, onOpenChange, onSave, open, running }: AvailabilityEditorDialogProps) {
  const [value, setValue] = useState<PlannerAvailabilityInput>(EMPTY_AVAILABILITY)

  useEffect(() => {
    if (open) {
      setValue(EMPTY_AVAILABILITY)
    }
  }, [open])

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{copy.availabilityEditor.title}</DialogTitle>
          <DialogDescription>{copy.availabilityEditor.description}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1.5 sm:col-span-2">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.availabilityEditor.weekday}</span>
            <Select onValueChange={weekday => setValue(current => ({ ...current, weekday }))} value={value.weekday}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {copy.weekdays.map((weekday, index) => (
                  <SelectItem key={weekday} value={String(index)}>
                    {weekday}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.availabilityEditor.start}</span>
            <Input
              onChange={event => setValue(current => ({ ...current, startTime: event.target.value }))}
              type="time"
              value={value.startTime}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.availabilityEditor.end}</span>
            <Input
              onChange={event => setValue(current => ({ ...current, endTime: event.target.value }))}
              type="time"
              value={value.endTime}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.availabilityEditor.energy}</span>
            <Select onValueChange={energy => setValue(current => ({ ...current, energy }))} value={value.energy}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="low">low</SelectItem>
                <SelectItem value="medium">medium</SelectItem>
                <SelectItem value="high">high</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="grid gap-1.5">
            <span className="text-[0.68rem] font-medium text-foreground/75">{copy.availabilityEditor.label}</span>
            <Input
              onChange={event => setValue(current => ({ ...current, label: event.target.value }))}
              placeholder={copy.availabilityEditor.labelPlaceholder}
              value={value.label}
            />
          </label>
        </div>
        <DialogFooter>
          <Button disabled={running} onClick={() => onOpenChange(false)} variant="text">
            {copy.availabilityEditor.cancel}
          </Button>
          <Button disabled={running || value.startTime >= value.endTime} onClick={() => onSave(value)}>
            {copy.availabilityEditor.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
