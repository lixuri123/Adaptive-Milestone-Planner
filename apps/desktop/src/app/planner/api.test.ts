import { describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'

import {
  loadPlannerOverview,
  loadScheduleCalendarPreview,
  milestoneCheckinArg,
  milestoneDraftDecisionArg,
  milestoneSnoozeArg,
  quotePlannerArg,
  runPlannerCommand,
  saveConfirmedProfileFacts,
  savePlannerGoal,
  savePlannerSource,
  savePlannerTask,
  syncScheduleCalendar
} from './api'

function gatewayReturning(value: unknown): HermesGateway {
  return { request: vi.fn().mockResolvedValue(value) } as unknown as HermesGateway
}

describe('planner desktop api', () => {
  it('quotes notification text for the shlex-backed plugin command', () => {
    expect(quotePlannerArg("advisor's reminder")).toBe("'advisor'\"'\"'s reminder'")
  })

  it('builds the milestone commands used by Desktop check-ins and approvals', () => {
    expect(
      milestoneCheckinArg('DM-1', {
        actualMinutes: 35,
        completionFraction: 0.5,
        note: "Baseline ran; advisor's review remains.",
        outcome: 'partial'
      })
    ).toBe("milestone-checkin DM-1 partial 35 0.5 'Baseline ran; advisor'\"'\"'s review remains.'")
    expect(milestoneCheckinArg('DM-1', { note: 'Waiting for GPU', outcome: 'blocked' })).toBe(
      "milestone-checkin DM-1 blocked 'Waiting for GPU'"
    )
    expect(milestoneCheckinArg('DM-1', { outcome: 'done' })).toBe('milestone-checkin DM-1 done')
    expect(milestoneSnoozeArg('DM-1', 90)).toBe('milestone-snooze DM-1 90')
    expect(milestoneDraftDecisionArg('MP-2', 'confirm')).toBe('milestone-confirm MP-2 confirm')
    expect(milestoneDraftDecisionArg('MP-2', 'reject')).toBe('milestone-reject MP-2')
  })

  it('loads milestone overview schema version 2 through generic plugin dispatch', async () => {
    const gateway = gatewayReturning({
      type: 'plugin',
      output: JSON.stringify({
        active_schedule: null,
        automation: { enabled: false, enabled_count: 0, jobs: {} },
        availability: [],
        draft_schedule: null,
        generated_at: '2026-07-21T00:00:00Z',
        goals: [],
        items: [],
        milestone_draft: null,
        milestone_plan: null,
        monitor_proposals: [],
        pending_events: [],
        profile_facts: [],
        recent_agent_runs: [],
        recent_schedules: [],
        remaining: { p50_minutes: 0, p90_minutes: 0 },
        schema_version: 2,
        sources: [],
        status: { counts: {}, database: 'test', timezone: 'Asia/Shanghai' },
        tasks: [],
        today: [],
        today_milestones: [],
        work_packages: []
      })
    })

    await expect(loadPlannerOverview(gateway)).resolves.toMatchObject({
      schema_version: 2,
      today_milestones: []
    })
    expect(gateway.request).toHaveBeenCalledWith('command.dispatch', { arg: 'overview', name: 'plan' })
  })

  it('rejects an older planner overview with an explicit upgrade error', async () => {
    await expect(
      loadPlannerOverview(
        gatewayReturning({
          type: 'plugin',
          output: JSON.stringify({ schema_version: 1 })
        })
      )
    ).rejects.toThrow('requires Personal Planner overview schema version 2')
  })

  it('rejects missing plugins and malformed snapshots', async () => {
    await expect(runPlannerCommand(gatewayReturning({ type: 'exec', output: '' }), 'overview')).rejects.toThrow(
      'not available'
    )
    await expect(loadPlannerOverview(gatewayReturning({ type: 'plugin', output: 'not json' }))).rejects.toThrow(
      'invalid overview'
    )
  })

  it('saves profile fields through proposed then confirmed states', async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce({ type: 'plugin', output: 'Created proposed profile fact [PF-SCHOOL1].' })
      .mockResolvedValueOnce({ type: 'plugin', output: '{"status":"confirmed"}' })
      .mockResolvedValueOnce({ type: 'plugin', output: 'Created proposed profile fact [PF-FIELD2].' })
      .mockResolvedValueOnce({ type: 'plugin', output: '{"status":"confirmed"}' })

    const gateway = { request } as unknown as HermesGateway

    await expect(
      saveConfirmedProfileFacts(gateway, [
        { category: 'education', factKey: 'school', factValue: '北京邮电大学' },
        { category: 'education', factKey: 'field', factValue: '计算机科学与技术' },
        { category: 'research', factKey: 'direction', factValue: '   ' }
      ])
    ).resolves.toEqual(['PF-SCHOOL1', 'PF-FIELD2'])

    expect(request).toHaveBeenNthCalledWith(1, 'command.dispatch', {
      arg: "profile-add 'education' 'school' '北京邮电大学'",
      name: 'plan'
    })
    expect(request).toHaveBeenNthCalledWith(2, 'command.dispatch', {
      arg: 'profile-confirm PF-SCHOOL1',
      name: 'plan'
    })
    expect(request).toHaveBeenNthCalledWith(3, 'command.dispatch', {
      arg: "profile-add 'education' 'field' '计算机科学与技术'",
      name: 'plan'
    })
    expect(request).toHaveBeenNthCalledWith(4, 'command.dispatch', {
      arg: 'profile-confirm PF-FIELD2',
      name: 'plan'
    })
  })

  it('fails safely when profile-add does not return a fact ID', async () => {
    const gateway = gatewayReturning({ type: 'plugin', output: 'unexpected response' })

    await expect(
      saveConfirmedProfileFacts(gateway, [{ category: 'education', factKey: 'school', factValue: 'BUPT' }])
    ).rejects.toThrow('did not return a profile fact ID')
  })

  it('creates rich goals and linked tasks without going through chat', async () => {
    const request = vi.fn().mockResolvedValue({ type: 'plugin', output: '{}' })
    const gateway = { request } as unknown as HermesGateway

    await savePlannerGoal(gateway, {
      area: 'research',
      outcome: 'Manuscript submitted',
      priority: 'high',
      target_date: '2027-03-01',
      title: 'Submit first paper',
      weekly_target_minutes: 600
    })
    await savePlannerTask(gateway, {
      due_at: null,
      energy: 'high',
      estimated_minutes: 120,
      goal_id: 'G-RESEARCH1',
      min_block_minutes: 45,
      notes: 'Review the last two years',
      priority: 'high',
      splittable: true,
      title: 'Literature review'
    })
    await savePlannerSource(gateway, {
      importance: 'high',
      max_results: 25,
      name: 'Graduate school',
      query: 'from:grad-school.example newer_than:30d'
    })

    expect(request).toHaveBeenNthCalledWith(1, 'command.dispatch', {
      arg: expect.stringMatching(/^goal-create '\{"area":"research"/),
      name: 'plan'
    })
    expect(request).toHaveBeenNthCalledWith(2, 'command.dispatch', {
      arg: expect.stringContaining('"goal_id":"G-RESEARCH1"'),
      name: 'plan'
    })
    expect(request).toHaveBeenNthCalledWith(3, 'command.dispatch', {
      arg: expect.stringMatching(/^source-create '\{"importance":"high"/),
      name: 'plan'
    })
  })

  it('previews the exact schedule before calendar sync', async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce({
        type: 'plugin',
        output: JSON.stringify({
          calendar_id: 'primary',
          events: [],
          plan_id: 'S-1',
          requires_explicit_confirmation: true
        })
      })
      .mockResolvedValueOnce({ type: 'plugin', output: '{}' })

    const gateway = { request } as unknown as HermesGateway

    await expect(loadScheduleCalendarPreview(gateway, 'S-1')).resolves.toMatchObject({ plan_id: 'S-1' })
    await syncScheduleCalendar(gateway, 'S-1')

    expect(request).toHaveBeenNthCalledWith(1, 'command.dispatch', {
      arg: 'schedule-calendar-preview S-1',
      name: 'plan'
    })
    expect(request).toHaveBeenNthCalledWith(2, 'command.dispatch', {
      arg: 'schedule-calendar-sync S-1 confirm',
      name: 'plan'
    })
  })
})
