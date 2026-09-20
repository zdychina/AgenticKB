/**
 * 知识制品 API —— 与知识库同样经 main_control_service 代理转发到 mining。
 *
 * 制品不跨知识域（52号 D6），所以走 createProxyClient('mining') 的按域 baseURL
 * 正合适：切域即换制品集合。
 */
import { createProxyClient, extractItems, extractOne } from '@/api/proxyClient'
import type {
  CreationInstance, KnowledgeProduct, ObjectEdit, ProductCreateBody, ProductObjectRow,
  PublishGate, ReviewDecision, ReviewRecord, RevisionDiff, StartedInstance,
  SubmissionRecord, TrialRecord, TrialVerdict,
} from '@/types/knowledgeProduct'

const BASE = '/api/knowledge-products'

export function useKnowledgeProductApi() {
  const client = createProxyClient('mining')

  return {
    // ── 制品 ──
    async list(): Promise<KnowledgeProduct[]> {
      const { data } = await client.get(BASE)
      return extractItems<KnowledgeProduct>(data)
    },

    async get(productId: string): Promise<KnowledgeProduct> {
      const { data } = await client.get(`${BASE}/${encodeURIComponent(productId)}`)
      return extractOne<KnowledgeProduct>(data)
    },

    async create(body: ProductCreateBody): Promise<KnowledgeProduct> {
      const { data } = await client.post(BASE, body)
      return extractOne<KnowledgeProduct>(data)
    },

    // ── 内容 ──
    async listObjects(productId: string, revision?: number): Promise<ProductObjectRow[]> {
      const { data } = await client.get(`${BASE}/${encodeURIComponent(productId)}/objects`, {
        params: revision == null ? {} : { revision },
      })
      return extractItems<ProductObjectRow>(data)
    },

    async getObjectMd(
      productId: string, objectId: string, revision?: number,
    ): Promise<string> {
      const { data } = await client.get(
        `${BASE}/${encodeURIComponent(productId)}/objects/${encodeURIComponent(objectId)}/md`,
        { params: revision == null ? {} : { revision } },
      )
      return (extractOne<{ md: string }>(data)).md
    },

    /** 人工改一个对象。留痕后写新修订，对象随即标为「人工确认」。 */
    async editObject(
      productId: string, objectId: string,
      payload: { raw_md: string; reason?: string; basis?: string },
    ): Promise<{ revision_no: number; diff: RevisionDiff }> {
      const { data } = await client.patch(
        `${BASE}/${encodeURIComponent(productId)}/objects/${encodeURIComponent(objectId)}`,
        payload,
      )
      return extractOne(data)
    },

    async listObjectEdits(productId: string, objectId: string): Promise<ObjectEdit[]> {
      const { data } = await client.get(
        `${BASE}/${encodeURIComponent(productId)}/objects/${encodeURIComponent(objectId)}/edits`,
      )
      return extractItems<ObjectEdit>(data)
    },

    async diff(productId: string, from: number, to: number): Promise<RevisionDiff> {
      const { data } = await client.get(`${BASE}/${encodeURIComponent(productId)}/diff`, {
        params: { from, to },
      })
      return extractOne<RevisionDiff>(data)
    },

    async diffObject(
      productId: string, objectId: string, from: number, to: number,
    ): Promise<string> {
      const { data } = await client.get(`${BASE}/${encodeURIComponent(productId)}/diff`, {
        params: { from, to, object_id: objectId },
      })
      return (extractOne<{ unified_diff: string }>(data)).unified_diff
    },

    // ── 人审 ──
    async review(
      productId: string,
      decisions: Record<string, ReviewDecision>,
      notes?: string,
    ): Promise<ReviewRecord> {
      const { data } = await client.post(
        `${BASE}/${encodeURIComponent(productId)}/reviews`, { decisions, notes },
      )
      return extractOne<ReviewRecord>(data)
    },

    async listReviews(productId: string, revision?: number): Promise<ReviewRecord[]> {
      const { data } = await client.get(`${BASE}/${encodeURIComponent(productId)}/reviews`, {
        params: revision == null ? {} : { revision },
      })
      return extractItems<ReviewRecord>(data)
    },

    // ── 试用与发布 ──
    async recordTrial(
      productId: string,
      payload: {
        question: string; verdict: TrialVerdict
        answer?: string; expected_points?: string; comment?: string; revision?: number
      },
    ): Promise<TrialRecord> {
      const { data } = await client.post(
        `${BASE}/${encodeURIComponent(productId)}/trials`, payload,
      )
      return extractOne<TrialRecord>(data)
    },

    async listTrials(productId: string, revision?: number): Promise<TrialRecord[]> {
      const { data } = await client.get(`${BASE}/${encodeURIComponent(productId)}/trials`, {
        params: revision == null ? {} : { revision },
      })
      return extractItems<TrialRecord>(data)
    },

    async publishGate(productId: string, revision?: number): Promise<PublishGate> {
      const { data } = await client.get(
        `${BASE}/${encodeURIComponent(productId)}/publish-gate`,
        { params: revision == null ? {} : { revision } },
      )
      return extractOne<PublishGate>(data)
    },

    async publish(
      productId: string, opts: { revision?: number; force?: boolean } = {},
    ): Promise<{ released_revision: number; gate: PublishGate; forced: boolean }> {
      const { data } = await client.post(
        `${BASE}/${encodeURIComponent(productId)}/publish`, opts,
      )
      return extractOne(data)
    },

    // ── 制作 ──
    /** 起制作实例。响应里的票据明文只此一次——不要存、不要回显。 */
    async startInstance(productId: string, ttlSeconds?: number): Promise<StartedInstance> {
      const { data } = await client.post(
        `${BASE}/${encodeURIComponent(productId)}/creation-instances`,
        ttlSeconds == null ? {} : { ttl_seconds: ttlSeconds },
      )
      return extractOne<StartedInstance>(data)
    },

    async listInstances(productId: string): Promise<CreationInstance[]> {
      const { data } = await client.get(
        `${BASE}/${encodeURIComponent(productId)}/creation-instances`,
      )
      return extractItems<CreationInstance>(data)
    },

    async cancelInstance(
      productId: string, instanceId: string, reason: string,
    ): Promise<void> {
      await client.post(
        `${BASE}/${encodeURIComponent(productId)}/creation-instances/${encodeURIComponent(instanceId)}/cancel`,
        { reason },
      )
    },

    async revokeTickets(productId: string, reason: string): Promise<{ revoked: number }> {
      const { data } = await client.post(
        `${BASE}/${encodeURIComponent(productId)}/tickets/revoke`, { reason },
      )
      return extractOne(data)
    },

    async listSubmissions(productId: string): Promise<SubmissionRecord[]> {
      const { data } = await client.get(`${BASE}/${encodeURIComponent(productId)}/submissions`)
      return extractItems<SubmissionRecord>(data)
    },
  }
}
