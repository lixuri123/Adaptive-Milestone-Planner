import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type { PlannerSchedule } from './api'
import { plannerCopy } from './copy'
import { buildWeekDays, getTimedPlacement, WeeklyPlanner } from './weekly-planner'

const ACTIVE_SCHEDULE: PlannerSchedule = {
  blocks: [
    {
      availability_label: 'Deep work',
      checkin: null,
      energy: 'high',
      ends_at: '2026-07-15T10:30:00+08:00',
      id: 'B-ACTIVE',
      minutes: 90,
      priority_score: 145,
      starts_at: '2026-07-15T09:00:00+08:00',
      task_id: 'T-RESEARCH',
      title: 'Interpretability experiment'
    }
  ],
  created_at: '2026-07-15T08:00:00+08:00',
  id: 'S-ACTIVE',
  status: 'confirmed'
}

const DRAFT_SCHEDULE: PlannerSchedule = {
  blocks: [
    {
      checkin: null,
      ends_at: '2026-07-20T20:00:00+08:00',
      id: 'B-DRAFT',
      minutes: 60,
      starts_at: '2026-07-20T19:00:00+08:00',
      task_id: 'T-CAREER',
      title: 'Internship applications'
    }
  ],
  created_at: '2026-07-15T08:00:00+08:00',
  id: 'S-DRAFT',
  is_replan: true,
  replaces_plan_ids: ['S-ACTIVE'],
  status: 'draft'
}

const TASKS = [
  {
    completed_minutes: 60,
    energy: 'high',
    estimated_minutes: 480,
    goal_id: 'G-RESEARCH',
    id: 'T-RESEARCH',
    min_block_minutes: 60,
    priority: 'high',
    remaining_minutes: 420,
    splittable: true,
    status: 'planned',
    title: 'Interpretability experiment'
  },
  {
    completed_minutes: 0,
    energy: 'medium',
    estimated_minutes: 180,
    goal_id: 'G-CAREER',
    id: 'T-CAREER',
    min_block_minutes: 30,
    priority: 'normal',
    remaining_minutes: 180,
    splittable: true,
    status: 'backlog',
    title: 'Internship applications'
  }
]

const GOALS = [
  {
    area: 'research',
    id: 'G-RESEARCH',
    priority: 'high',
    status: 'active',
    title: 'Submit paper',
    weekly_target_minutes: 600
  },
  {
    area: 'career',
    id: 'G-CAREER',
    priority: 'high',
    status: 'active',
    title: 'Find internship',
    weekly_target_minutes: 300
  }
]

const AVAILABILITY = [
  {
    enabled: true,
    end_time: '12:00',
    energy: 'high',
    id: 'A-WED',
    label: 'Deep work',
    start_time: '09:00',
    weekday: 2,
    weekday_name: 'wednesday'
  }
]

function renderPlanner({
  active = ACTIVE_SCHEDULE,
  draft = DRAFT_SCHEDULE
}: { active?: null | PlannerSchedule; draft?: null | PlannerSchedule } = {}) {
  const onBlockAction = vi.fn()

  render(
    <WeeklyPlanner
      activeSchedule={active}
      availability={AVAILABILITY}
      copy={plannerCopy('en')}
      draftSchedule={draft}
      generatedAt="2026-07-15T08:30:00+08:00"
      goals={GOALS}
      locale="en"
      onAddAvailability={vi.fn()}
      onBlockAction={onBlockAction}
      onCancelSchedule={vi.fn()}
      onConfirmDraft={vi.fn()}
      onGeneratePlan={vi.fn()}
      onRemoveAvailability={vi.fn()}
      onSyncCalendar={vi.fn()}
      running={false}
      tasks={TASKS}
      timeZone="Asia/Shanghai"
    />
  )

  return { onBlockAction }
}

afterEach(cleanup)

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class ResizeObserver {
      disconnect() {}
      observe() {}
      unobserve() {}
    }
  )
})

describe('WeeklyPlanner', () => {
  it('builds a Monday-first timezone-aware week', () => {
    const days = buildWeekDays('2026-07-15T23:30:00Z', 'Asia/Shanghai', 0, 'en')

    expect(days.map(day => day.key)).toEqual([
      '2026-07-13',
      '2026-07-14',
      '2026-07-15',
      '2026-07-16',
      '2026-07-17',
      '2026-07-18',
      '2026-07-19'
    ])
    expect(days[3].isToday).toBe(true)
  })

  it('clips timed regions to the visible timetable range', () => {
    expect(getTimedPlacement(6 * 60, 8 * 60, 7 * 60, 23 * 60)).toEqual({ height: 48, top: 0 })
    expect(getTimedPlacement(23 * 60, 24 * 60, 7 * 60, 23 * 60)).toBeNull()
    expect(getTimedPlacement(9 * 60, 10.5 * 60, 7 * 60, 23 * 60)).toEqual({ height: 72, top: 96 })
  })

  it('layers recurring availability under current and draft schedule blocks', () => {
    renderPlanner()

    fireEvent.click(screen.getByRole('button', { name: 'Current plan' }))
    expect(screen.getByTestId('availability-window-A-WED')).toBeTruthy()
    expect(screen.getByTestId('schedule-block-B-ACTIVE')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Next week' }))
    fireEvent.click(screen.getByRole('button', { name: 'Draft preview' }))
    expect(screen.getByTestId('schedule-block-B-DRAFT')).toBeTruthy()
    expect(screen.queryByTestId('schedule-block-B-ACTIVE')).toBeNull()
  })

  it('keeps execution actions inside the confirmed block detail popover', async () => {
    const { onBlockAction } = renderPlanner({ active: ACTIVE_SCHEDULE, draft: null })

    fireEvent.click(screen.getByTestId('schedule-block-B-ACTIVE'))
    expect(await screen.findByText('Why this time')).toBeTruthy()
    expect(screen.getByText('145')).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: 'Done' }))

    expect(onBlockAction).toHaveBeenCalledWith(ACTIVE_SCHEDULE.blocks[0], 'done')
  })
})
