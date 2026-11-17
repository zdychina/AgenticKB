import type { DomainInfo } from '@/types'
import type { DomainCapability, SiteRole } from '@/types/auth'

function hasDomainCapability(
  siteRole: SiteRole,
  domain: DomainInfo | undefined,
  capability: DomainCapability,
): boolean {
  if (siteRole === 'admin') return true
  if (!domain?.enabled || domain.domain_role !== 'admin') return false
  return domain.capabilities?.includes(capability) ?? false
}

export function canManageDomainUsers(
  siteRole: SiteRole,
  domain: DomainInfo | undefined,
): boolean {
  return hasDomainCapability(siteRole, domain, 'domain.users.manage')
}

export function canManageDomainKnowledgeBases(
  siteRole: SiteRole,
  domain: DomainInfo | undefined,
): boolean {
  return hasDomainCapability(siteRole, domain, 'domain.kbs.manage')
}

/**
 * 67号本体管理写权限（新建/编辑/删除库与库内一切写）。
 * 对齐后端 can_manage_domain 语义：站点管理员或该域域管理员——后端没有
 * capabilities 细分（库内读=域成员），故不走 hasDomainCapability。
 */
export function canManageDomainOntology(
  siteRole: SiteRole,
  domain: DomainInfo | undefined,
): boolean {
  if (siteRole === 'admin') return true
  return !!domain?.enabled && domain.domain_role === 'admin'
}
