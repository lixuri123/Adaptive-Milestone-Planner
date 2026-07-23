import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PlannerAgentRun, PlannerMilestonePlan } from './api'
import { plannerCopy } from './copy'
import { MilestonePlanner } from './milestone-planner'

const ACTIVE_PLAN: PlannerMilestonePlan = {
  created_at: '2026-07-21T08:00:00+08:00',
  id: 'MP-ACTIVE',
  milestones: [
    {
      date: '2026-07-21',
      definition_of_done: ['Run the baseline', 'Save the metrics table'],
      domain: 'research',
      explanation: ['Keeps the experiment sequence ahead of the internal deadline.'],
      id: 'DM-RESEARCH-1',
      reminder_anchor: { at: '2026-07-21T18:30:00+08:00', kind: 'fixed_time' },
      status: 'planned',
      target_minutes: 60,
      title: 'Complete interpretability baseline',
      work_package_id: 'WP-RESEARCH'
    },
    {
      date: '2026-07-23',
      definition_of_done: ['Shortlist five roles'],
      domain: 'career',
      explanation: ['Leaves a review buffer before applications are sent.'],
      id: 'DM-CAREER-1',
      reminder_anchor: { kind: 'recommended_window', recommended_at: '2026-07-23T19:00:00+08:00' },
      status: 'planned',
      target_minutes: 90,
      title: 'Build internship shortlist',
      work_package_id: 'WP-CAREER'
    }
  ],
  status: 'confirmed',
  summary: {
    deadline_risks: [{ level: 'medium', message: 'Research buffer is one day.' }],
    p50_remaining_minutes: 420,
    p90_remaining_minutes: 540
  }
}

const DRAFT_PLAN: PlannerMilestonePlan = {
  ...ACTIVE_PLAN,
  created_at: '2026-07-21T12:00:00+08:00',
  id: 'MP-DRAFT',
  milestones: [
    {
      ...ACTIVE_PLAN.milestones[1],
      date: '2026-07-24',
      id: 'DM-CAREER-2',
      target_minutes: 60
    }
  ],
  status: 'draft',
  summary: {
    diff: {
      added: [],
      changed: [
        {
          after: {
            ...ACTIVE_PLAN.milestones[1],
            date: '2026-07-24',
            id: 'DM-CAREER-2',
            target_minutes: 60
          },
          before: ACTIVE_PLAN.milestones[1]
        }
      ],
      removed: []
    },
    p50_remaining_minutes: 390,
    p90_remaining_minutes: 510
  }
}

const AGENT_RUNS: PlannerAgentRun[] = [
  {
    approval_status: 'review_required',
    created_at: '2026-07-21T12:00:00+08:00',
    id: 'AR-1',
    outcome: 'draft_created',
    reasoning_summary: 'A meeting reduced capacity, so the career milestone moved by one day.',
    selected_experts: ['research', 'career']
  }
]

function renderPlanner({ draft = null }: { draft?: null | PlannerMilestonePlan } = {}) {
  const onCheckin = vi.fn()
  const onConfirmDraft = vi.fn()
  const onRejectDraft = vi.fn()

  render(
    <MilestonePlanner
      activePlan={ACTIVE_PLAN}
      agentRuns={AGENT_RUNS}
      copy={plannerCopy('en')}
      draftPlan={draft}
      locale="en"
      onCheckin={onCheckin}
      onConfirmDraft={onConfirmDraft}
      onRejectDraft={onRejectDraft}
      onSnooze={vi.fn()}
      pendingEventCount={1}
      remaining={{ p50_minutes: 420, p90_minutes: 540 }}
      running={false}
      today="2026-07-21"
    />
  )

  return { onCheckin, onConfirmDraft, onRejectDraft }
}

afterEach(cleanup)

describe('MilestonePlanner', () => {
  it('makes today targets and remaining workload the primary hierarchy', () => {
    renderPlanner()

    expect(screen.getByRole('heading', { name: 'Today targets' })).toBeTruthy()
    expect(screen.getByRole('heading', { level: 3, name: 'Complete interpretability baseline' })).toBeTruthy()
    expect(screen.getByText('60m')).toBeTruthy()
    expect(screen.getByText('P50 7h')).toBeTruthy()
    expect(screen.getByText('P90 9h')).toBeTruthy()
    expect(screen.getByText('Build internship shortlist')).toBeTruthy()
  })

  it('collects structured progress for a partial check-in', () => {
    const { onCheckin } = renderPlanner()

    fireEvent.click(screen.getByRole('button', { name: 'Partial' }))
    fireEvent.change(screen.getByLabelText('Minutes completed'), { target: { value: '35' } })
    fireEvent.change(screen.getByLabelText('Completion percent'), { target: { value: '50' } })
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'Baseline ran; metrics need review.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save check-in' }))

    expect(onCheckin).toHaveBeenCalledWith('DM-RESEARCH-1', {
      actualMinutes: 35,
      completionFraction: 0.5,
      note: 'Baseline ran; metrics need review.',
      outcome: 'partial'
    })
  })

  it('keeps replanning as a reviewable diff with confirm and reject actions', () => {
    const { onConfirmDraft, onRejectDraft } = renderPlanner({ draft: DRAFT_PLAN })

    expect(screen.getByRole('heading', { name: 'Plan update for review' })).toBeTruthy()
    expect(screen.getByText('Jul 23 → Jul 24')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm update' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reject update' }))

    expect(onConfirmDraft).toHaveBeenCalledWith(DRAFT_PLAN)
    expect(onRejectDraft).toHaveBeenCalledWith(DRAFT_PLAN)
  })
})
