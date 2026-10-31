import { computed, ref, type Ref } from 'vue'
import { isAxiosError } from 'axios'
import { message } from 'ant-design-vue'
import { confirmAssistantProposal, discardAssistantProposal, fetchAssistantProposal } from '../services/api'
import { proposalConflictMessage } from '../utils/proposalConflict'
import { proposalChanges } from '../utils/proposalChanges'
import type { AssistantConfirmation, AssistantDecision, AssistantProposal } from '@/types'

export function useAssistantProposal(context: {
  recordId: Ref<number>
  version: Ref<number>
  quality: Ref<{ assistant_conversation_id?: string; assistant_proposal_status?: string }>
  confirmed: (response: AssistantConfirmation) => Promise<void>
  discarded: (response: AssistantDecision) => Promise<void>
  accessError: (error: unknown) => void
  reopen: () => Promise<unknown>
}) {
  const details = ref<AssistantProposal | null>(null), detailsError = ref(''), confirmError = ref(''), busy = ref(false)
  const changes = computed(() => details.value ? proposalChanges(details.value.diff) : [])
  const errorMessage = (error: unknown, fallback: string) => isAxiosError<{ message?: string }>(error) ? error.response?.data?.message || fallback : fallback

  async function load() {
    confirmError.value = ''
    const id = context.recordId.value, conversation = context.quality.value.assistant_conversation_id
    if (!id || !conversation || context.quality.value.assistant_proposal_status !== 'pending') { details.value = null; detailsError.value = ''; return }
    try { details.value = await fetchAssistantProposal(conversation, id); detailsError.value = '' }
    catch (error) { details.value = null; detailsError.value = errorMessage(error, '提案依赖读取失败，请刷新后确认。'); context.accessError(error) }
  }
  async function confirm() {
    const id = context.recordId.value, conversation = context.quality.value.assistant_conversation_id
    if (!id || !conversation || busy.value) return
    busy.value = true; confirmError.value = ''
    try {
      const response = await confirmAssistantProposal(conversation, id, context.version.value)
      details.value = null
      await context.confirmed(response)
    } catch (error) {
      if (isAxiosError<{ code?: string; message?: string }>(error) && error.response?.status === 409) {
        const reason = proposalConflictMessage(error.response.data.code, error.response.data.message)
        await load(); confirmError.value = reason; message.error(reason)
      } else { context.accessError(error); message.error(errorMessage(error, '确认失败，请重新打开方案')) }
    } finally { busy.value = false }
  }
  async function discard() {
    const id = context.recordId.value, conversation = context.quality.value.assistant_conversation_id
    if (!id || !conversation || busy.value) return
    busy.value = true
    try { await context.discarded(await discardAssistantProposal(conversation, id, context.version.value)) }
    catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) { message.error('方案版本已变化，请重新打开行程记录'); await context.reopen() }
      else message.error(errorMessage(error, '放弃方案失败'))
    } finally { busy.value = false }
  }
  return { details, detailsError, confirmError, busy, changes, load, confirm, discard }
}
