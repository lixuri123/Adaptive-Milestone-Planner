import { useEffect } from 'react'

import { useI18n } from '@/i18n'
import { dispatchNativeNotification } from '@/store/native-notifications'
import { notify } from '@/store/notifications'

import { plannerCopy } from './copy'

interface PluginCommandResponse {
  output?: string
  type?: string
}

interface PlannerReminderBridgeProps {
  enabled: boolean
  requestGateway: <T>(
    method: string,
    params?: Record<string, unknown>,
    timeoutMs?: number,
    signal?: AbortSignal
  ) => Promise<T>
}

const POLL_INTERVAL_MS = 60_000

/**
 * Claims Planner reminders while Desktop is running. The claim is atomic with
 * the Cron dispatcher, so enabling Cron later cannot produce a second copy.
 */
export function PlannerReminderBridge({ enabled, requestGateway }: PlannerReminderBridgeProps) {
  const { locale } = useI18n()
  const copy = plannerCopy(locale)

  useEffect(() => {
    if (!enabled) {
      return
    }

    let disposed = false
    let polling = false

    async function poll() {
      if (disposed || polling) {
        return
      }

      polling = true

      try {
        const response = await requestGateway<PluginCommandResponse>('command.dispatch', {
          arg: 'dispatch',
          name: 'plan'
        })

        const message = response.type === 'plugin' ? response.output?.trim() : ''

        if (!disposed && message) {
          notify({
            icon: 'bell',
            kind: 'warning',
            message,
            title: copy.messages.reminderTitle
          })
          dispatchNativeNotification({
            body: message.slice(0, 480),
            global: true,
            kind: 'backgroundDone',
            title: copy.messages.reminderTitle
          })
        }
      } catch {
        // The plugin is optional. Connection recovery is owned by requestGateway;
        // a missing/disabled Planner must not disturb the rest of Desktop.
      } finally {
        polling = false
      }
    }

    void poll()
    const interval = window.setInterval(() => void poll(), POLL_INTERVAL_MS)

    return () => {
      disposed = true
      window.clearInterval(interval)
    }
  }, [copy.messages.reminderTitle, enabled, requestGateway])

  return null
}
