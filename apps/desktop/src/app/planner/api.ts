import type { HermesGateway } from '@/hermes'

interface PluginCommandResponse {
  output?: string
  type?: string
}

export interface PlannerItem {
  ack_required?: boolean | number
  acknowledged_at?: null | string
  actionable_at?: null | string
  due_at?: null | string
  event_at?: null | string
  id: string
  importance: string
  raw_content?: string
  status: string
  timezone: string
  title: string
}

export interface PlannerGoal {
  area: string
  id: string
  priority: string
  status: string
  target_date?: null | string
  title: string
  weekly_target_minutes: number
}

export interface PlannerTask {
  completed_minutes: number
  due_at?: null | string
  earliest_start?: null | string
  energy: string
  estimated_minutes: number
  goal_id?: null | string
  id: string
  min_block_minutes: number
  notes?: null | string
  priority: string
  remaining_minutes: number
  splittable: boolean | number
  status: string
  title: string
}

export interface PlannerAvailability {
  enabled: boolean | number
  end_time: string
  energy: string
  id: string
  label?: null | string
  start_time: string
  weekday: number
  weekday_name: string
}

export interface PlannerProfileFact {
  category: string
  currently_valid: boolean
  fact_key: string
  fact_value: string
  id: string
  privacy: string
  status: string
}

export interface PlannerProfileInput {
  category: string
  factKey: string
  factValue: string
}

export interface PlannerMonitorProposal {
  cadence?: string
  evidence?: Array<{ id: string; kind: string; label: string }>
  id: string
  network_scan_active: boolean
  priority: string
  query_terms?: string[]
  rationale?: string
  source_kinds?: string[]
  status: string
  title: string
}

export interface PlannerSource {
  enabled: boolean | number
  id: string
  importance: string
  last_error?: null | string
  last_scanned_at?: null | string
  name: string
  query: string
}

export interface PlannerBlock {
  availability_label?: null | string
  checkin?: null | { actual_minutes?: number; outcome?: string }
  calendar_links?: Array<{ html_link?: null | string; status: string }>
  energy?: string
  ends_at: string
  goal_id?: null | string
  id: string
  minutes: number
  priority_score?: number
  starts_at: string
  status?: string
  task_id: string
  title: string
}

export interface PlannerAutomationJob {
  enabled: boolean
  error?: string
  installed: boolean
  job_id?: string
  last_error?: null | string
  last_run_at?: null | string
  last_status?: null | string
  name: string
  next_run_at?: null | string
  schedule?: unknown
  state: string
}

export interface PlannerAutomationStatus {
  enabled: boolean
  enabled_count: number
  error?: string
  jobs: Record<string, PlannerAutomationJob>
}

export interface PlannerScheduleCalendarEvent {
  block_id: string
  end: string
  start: string
  summary: string
  sync_status: string
}

export interface PlannerScheduleCalendarPreview {
  calendar_id: string
  events: PlannerScheduleCalendarEvent[]
  plan_id: string
  requires_explicit_confirmation: boolean
}

export interface PlannerSchedule {
  blocks: PlannerBlock[]
  created_at: string
  id: string
  is_replan?: boolean | number
  replaces_plan_ids?: string[]
  status: string
  summary?: Record<string, unknown>
  timezone?: string
  window_end?: string
  window_start?: string
}

export type PlannerDomain = 'career' | 'communication' | 'research'

export interface PlannerDailyMilestone {
  date: string
  definition_of_done: string[]
  domain: PlannerDomain
  explanation: string[]
  id: string
  reminder_anchor: {
    at?: string
    commitment_id?: string
    kind: string
    offset_minutes?: number
    recommended_at?: null | string
  }
  source_step_ids?: string[]
  status: 'blocked' | 'cancelled' | 'done' | 'draft' | 'in_progress' | 'missed' | 'partial' | 'planned' | 'superseded'
  target_minutes: number
  title: string
  window_end?: null | string
  work_package_id?: null | string
}

export interface PlannerMilestoneDiff {
  added: PlannerDailyMilestone[]
  changed: Array<{ after: PlannerDailyMilestone; before: PlannerDailyMilestone }>
  removed: PlannerDailyMilestone[]
}

export interface PlannerMilestonePlan {
  created_at: string
  id: string
  milestones: PlannerDailyMilestone[]
  status: 'cancelled' | 'confirmed' | 'draft' | 'rejected' | 'superseded'
  summary: {
    deadline_risks?: Array<Record<string, unknown>>
    diff?: PlannerMilestoneDiff
    p50_remaining_minutes: number
    p90_remaining_minutes: number
    tradeoff_options?: Array<Record<string, unknown>>
    unallocated_p50_minutes?: number
    unallocated_p90_minutes?: number
    unscheduled?: Array<Record<string, unknown>>
  }
}

export interface PlannerWorkPackage {
  domain: PlannerDomain
  effort: {
    confidence: number
    p50_minutes: number
    p90_minutes: number
  }
  hard_deadline: string
  id: string
  objective: string
  recommended_internal_deadline?: null | string
  status: string
}

export interface PlannerAgentRun {
  approval_status: string
  created_at: string
  id: string
  outcome: string
  reasoning_summary: string
  selected_experts: string[]
  trigger_event_id?: null | string
}

export interface PlannerPendingEvent {
  event_type: string
  id: string
  occurred_at: string
  source: string
  status: 'failed' | 'pending' | 'processed'
}

export interface PlannerRemainingWork {
  p50_minutes: number
  p90_minutes: number
}

export type PlannerMilestoneCheckinOutcome = 'blocked' | 'done' | 'not_started' | 'partial' | 'skipped'

export interface PlannerMilestoneCheckinInput {
  actualMinutes?: number
  completionFraction?: number
  note?: string
  outcome: PlannerMilestoneCheckinOutcome
}

export interface PlannerOverview {
  active_schedule: null | PlannerSchedule
  automation: PlannerAutomationStatus
  availability: PlannerAvailability[]
  draft_schedule: null | PlannerSchedule
  generated_at: string
  goals: PlannerGoal[]
  items: PlannerItem[]
  milestone_draft: null | PlannerMilestonePlan
  milestone_plan: null | PlannerMilestonePlan
  monitor_proposals: PlannerMonitorProposal[]
  pending_events: PlannerPendingEvent[]
  profile_facts: PlannerProfileFact[]
  recent_agent_runs: PlannerAgentRun[]
  recent_schedules: Array<Pick<PlannerSchedule, 'created_at' | 'id' | 'status'>>
  remaining: PlannerRemainingWork
  schema_version: number
  sources: PlannerSource[]
  status: {
    counts: Record<string, number>
    database: string
    timezone: string
  }
  tasks: PlannerTask[]
  today: PlannerItem[]
  today_milestones: PlannerDailyMilestone[]
  work_packages: PlannerWorkPackage[]
}

export interface PlannerTaskInput {
  due_at?: null | string
  earliest_start?: null | string
  energy: string
  estimated_minutes: number
  goal_id?: null | string
  min_block_minutes: number
  notes?: null | string
  priority: string
  splittable: boolean
  title: string
}

export interface PlannerGoalInput {
  area: string
  outcome?: null | string
  priority: string
  target_date?: null | string
  title: string
  weekly_target_minutes: number
}

export interface PlannerSourceInput {
  importance: string
  max_results: number
  name: string
  query: string
}

export function quotePlannerArg(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`
}

export function milestoneCheckinArg(milestoneId: string, input: PlannerMilestoneCheckinInput): string {
  if (input.outcome === 'partial') {
    const minutes = Math.max(0, Math.round(input.actualMinutes ?? 0))
    const fraction = Math.min(1, Math.max(0, input.completionFraction ?? 0))

    return `milestone-checkin ${milestoneId} partial ${minutes} ${fraction} ${quotePlannerArg(input.note?.trim() ?? '')}`
  }

  if (input.outcome === 'blocked') {
    return `milestone-checkin ${milestoneId} blocked ${quotePlannerArg(input.note?.trim() ?? '')}`
  }

  return `milestone-checkin ${milestoneId} ${input.outcome}`
}

export function milestoneSnoozeArg(milestoneId: string, minutes: number): string {
  return `milestone-snooze ${milestoneId} ${Math.max(1, Math.round(minutes))}`
}

export function milestoneDraftDecisionArg(planId: string, decision: 'confirm' | 'reject'): string {
  return decision === 'confirm' ? `milestone-confirm ${planId} confirm` : `milestone-reject ${planId}`
}

export async function runPlannerCommand(gateway: HermesGateway, arg: string): Promise<string> {
  const response = await gateway.request<PluginCommandResponse>('command.dispatch', {
    arg,
    name: 'plan'
  })

  if (response.type !== 'plugin') {
    throw new Error('Personal Planner is not available in this Hermes profile.')
  }

  return response.output ?? ''
}

export async function saveConfirmedProfileFacts(
  gateway: HermesGateway,
  facts: PlannerProfileInput[]
): Promise<string[]> {
  const savedIds: string[] = []

  for (const fact of facts) {
    const value = fact.factValue.trim()

    if (!value) {
      continue
    }

    const output = await runPlannerCommand(
      gateway,
      `profile-add ${quotePlannerArg(fact.category)} ${quotePlannerArg(fact.factKey)} ${quotePlannerArg(value)}`
    )

    const factId = output.match(/\[(PF-[A-Z0-9]+)\]/i)?.[1]?.toUpperCase()

    if (!factId) {
      throw new Error(`Personal Planner did not return a profile fact ID for ${fact.category}.${fact.factKey}.`)
    }

    await runPlannerCommand(gateway, `profile-confirm ${factId}`)
    savedIds.push(factId)
  }

  return savedIds
}

export async function savePlannerTask(gateway: HermesGateway, input: PlannerTaskInput, taskId?: string): Promise<void> {
  const payload = quotePlannerArg(JSON.stringify(input))

  await runPlannerCommand(gateway, taskId ? `task-update ${taskId} ${payload}` : `task-create ${payload}`)
}

export async function savePlannerGoal(gateway: HermesGateway, input: PlannerGoalInput): Promise<void> {
  await runPlannerCommand(gateway, `goal-create ${quotePlannerArg(JSON.stringify(input))}`)
}

export async function savePlannerSource(gateway: HermesGateway, input: PlannerSourceInput): Promise<void> {
  await runPlannerCommand(gateway, `source-create ${quotePlannerArg(JSON.stringify(input))}`)
}

export async function loadScheduleCalendarPreview(
  gateway: HermesGateway,
  scheduleId: string
): Promise<PlannerScheduleCalendarPreview> {
  const output = await runPlannerCommand(gateway, `schedule-calendar-preview ${scheduleId}`)

  return JSON.parse(output) as PlannerScheduleCalendarPreview
}

export async function syncScheduleCalendar(gateway: HermesGateway, scheduleId: string): Promise<void> {
  await runPlannerCommand(gateway, `schedule-calendar-sync ${scheduleId} confirm`)
}

export async function loadPlannerOverview(gateway: HermesGateway): Promise<PlannerOverview> {
  const output = await runPlannerCommand(gateway, 'overview')
  let parsed: unknown

  try {
    parsed = JSON.parse(output)
  } catch {
    throw new Error('Personal Planner returned an invalid overview response.')
  }

  if (!parsed || typeof parsed !== 'object') {
    throw new Error('Personal Planner overview is unavailable or incompatible.')
  }

  if ((parsed as { schema_version?: unknown }).schema_version !== 2) {
    throw new Error(
      'Desktop Planner requires Personal Planner overview schema version 2. Update the backend and retry.'
    )
  }

  return parsed as PlannerOverview
}
