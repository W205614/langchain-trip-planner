import type { AssistantProposal, ProposalActivity } from '@/types'

export function proposalChanges(diff: AssistantProposal['diff']): string[] {
  const describe = (value: ProposalActivity) => value.title || value.name || value.activity_id || value.id || '活动安排变化'
  const values: string[] = []
  for (const [key, label] of Object.entries({ added: '新增', removed: '移除', moved: '移动', changed: '调整', preserved: '保留' }))
    for (const item of diff[key as 'added' | 'removed' | 'moved' | 'changed' | 'preserved'] || []) values.push(`${label}：${describe(item)}`)
  for (const { before, after } of diff.modified || []) {
    const details: string[] = []
    if (before.name !== after.name) details.push(`${before.name || '原活动'} → ${after.name || '新活动'}`)
    if (before.visit_duration !== after.visit_duration) details.push(`游览 ${before.visit_duration ?? '未知'} → ${after.visit_duration ?? '未知'} 分钟`)
    if (before.start_at !== after.start_at) details.push(`开始时间 ${before.start_at || '待定'} → ${after.start_at || '待定'}`)
    values.push(`调整：${describe(after)}${details.length ? `（${details.join('；')}）` : '（内容或顺序变化）'}`)
  }
  for (const day of diff.days || []) values.push(`第 ${day.day_index + 1} 天：${day.summary || '安排已调整'}`)
  return values
}
