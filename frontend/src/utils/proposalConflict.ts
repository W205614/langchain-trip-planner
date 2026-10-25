const reasons: Record<string, string> = {
  FACTS_EXPIRED: '方案依赖的事实已过期，请返回原行程重新核验，再生成提案。',
  PLANNING_INPUT_CONFLICT: '规划约束或预订事项已变化，请重新加载原行程并生成提案。',
  VERSION_CONFLICT: '方案或原行程版本已变化，请重新加载后检查差异并生成提案。',
  REVISION_SCOPE_VIOLATION: '方案修改了授权范围外的内容，请检查改排范围后重新生成提案。',
  PROTECTED_ACTIVITY_CHANGED: '锁定、已完成或预约安排发生变化，请检查受保护安排后重新生成提案。',
  COMMITMENT_TIME_VIOLATION: '方案未保留当前已确认的预约时间，请检查预约后重新生成提案。',
  PROPOSAL_NOT_PENDING: '方案已确认或已放弃，请重新加载行程查看最新结果。',
  PARENT_MISSING: '原行程已不存在或无法访问，请返回我的行程重新选择。',
}

export function proposalConflictMessage(code?: string, fallback?: string): string {
  const codes = (code || '').split(',').map(value => value.trim()).filter(Boolean)
  const messages = [...new Set(codes.map(value => reasons[value]).filter(Boolean))]
  if (messages.length) {
    if (codes.some(value => !reasons[value])) messages.push('另有确认条件已变化，请刷新提案依赖并检查原行程。')
    return messages.join(' ')
  }
  return fallback && !/^[A-Z][A-Z0-9_,: -]*$/.test(fallback)
    ? fallback
    : '方案暂时无法确认，请刷新提案依赖并检查原行程；原行程未改变。'
}
