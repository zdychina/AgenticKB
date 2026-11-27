import { describe, expect, it } from 'vitest'
import {
  isActiveRunStatus,
  isPartialRun,
  processedDocumentCount,
  runStatusLabel,
  runStatusTagType,
} from '@/utils/runStatus'

describe('runStatus', () => {
  it('uses the database run status vocabulary', () => {
    expect(runStatusLabel('completed')).toBe('已完成')
    expect(runStatusTagType('completed')).toBe('success')
    expect(runStatusTagType('failed')).toBe('danger')
  })

  it('keeps queued runs polling', () => {
    expect(isActiveRunStatus('queued')).toBe(true)
    expect(isActiveRunStatus('running')).toBe(true)
    expect(isActiveRunStatus('completed')).toBe(false)
  })

  it('counts skipped documents as processed', () => {
    expect(processedDocumentCount({
      committed_count: 2,
      failed_count: 1,
      skipped_count: 7,
    })).toBe(10)
  })

  it('flags completed runs that have failures (BUG-07)', () => {
    // 新数据：metadata 透传的标记
    expect(isPartialRun({ status: 'completed', has_failures: true })).toBe(true)
    expect(isPartialRun({ status: 'completed', partial_success: true })).toBe(true)
    // 旧数据：标记缺失时用 failed_count 兜底
    expect(isPartialRun({ status: 'completed', failed_count: 1 })).toBe(true)
    // 全成功 / 非 completed（failed 的 failed_count>0 但状态本身就是 failed）
    expect(isPartialRun({ status: 'completed', failed_count: 0 })).toBe(false)
    expect(isPartialRun({ status: 'completed' })).toBe(false)
    expect(isPartialRun({ status: 'failed', failed_count: 2 })).toBe(false)
    expect(isPartialRun({ status: 'running', has_failures: true })).toBe(false)
  })
})
