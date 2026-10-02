import type {
  EntityType,
  KnowledgeEntity,
  KnowledgeSpace,
  RelationshipTraversalItem,
  TaxonomyNode,
  ViewDefinition,
} from "@hardatlas/contracts";
import {
  entities as localEntities,
  findEntity,
  localize,
  searchEntities,
  taxonomyNodes as localTaxonomy,
  viewDefinitions as localViews,
} from "./catalog";

export interface SearchFacetValue {
  value: string;
  label: string;
  count: number;
}

export interface SearchResultItem {
  entity: KnowledgeEntity;
  matchedText: string;
  matchedField: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  items: SearchResultItem[];
  total: number;
  offset: number;
  limit: number;
  facets: {
    spaces: SearchFacetValue[];
    entityTypes: SearchFacetValue[];
    taxonomyNodes: SearchFacetValue[];
    locales: SearchFacetValue[];
    sourceTiers: SearchFacetValue[];
  };
}

export interface SearchFilters {
  query?: string;
  typeId?: string | undefined;
  spaceId?: string | undefined;
  taxonomyNodeId?: string | undefined;
  locale?: string | undefined;
  displayLocale?: string | undefined;
  sourceTier?: string | undefined;
  minimumSources?: number;
  limit?: number;
  offset?: number;
}

export interface EntityRevisionSummary {
  revision: KnowledgeEntity["revision"];
  current: boolean;
}

export interface BuildInfo {
  buildSha: string;
  environment: string;
  dataVersion: string;
  schemaVersion: string;
  policyVersion: string;
}

const localSpaces: KnowledgeSpace[] = [
  {
    id: "space-life",
    slug: "life",
    name: [{ locale: "zh-CN", value: "生命科学" }],
    description: [{ locale: "zh-CN", value: "动物、植物、微生物、生态与演化" }],
    rootTaxonomyNodeIds: ["tax-animals", "tax-plants"],
    iconKey: "life",
    status: "published",
  },
  {
    id: "space-earth",
    slug: "earth",
    name: [{ locale: "zh-CN", value: "地球与环境" }],
    description: [{ locale: "zh-CN", value: "地理、气候、地质与自然现象" }],
    rootTaxonomyNodeIds: ["tax-geography", "tax-geology"],
    iconKey: "earth",
    status: "published",
  },
  {
    id: "space-engineering",
    slug: "engineering",
    name: [{ locale: "zh-CN", value: "工程技术" }],
    description: [{ locale: "zh-CN", value: "电子、材料、机械、计算与制造" }],
    rootTaxonomyNodeIds: ["tax-electronics"],
    iconKey: "engineering",
    status: "published",
  },
  {
    id: "space-humanities",
    slug: "humanities",
    name: [{ locale: "zh-CN", value: "人文与历史" }],
    description: [{ locale: "zh-CN", value: "人物、事件、文化、语言与艺术" }],
    rootTaxonomyNodeIds: ["tax-history", "tax-language"],
    iconKey: "humanities",
    status: "published",
  },
  {
    id: "space-mathematics",
    slug: "mathematics",
    name: [{ locale: "zh-CN", value: "数学与逻辑" }],
    description: [{ locale: "zh-CN", value: "数学基础、模型、统计与计算推理" }],
    rootTaxonomyNodeIds: ["tax-mathematics"],
    iconKey: "mathematics",
    status: "published",
  },
  {
    id: "space-medicine",
    slug: "medicine",
    name: [{ locale: "zh-CN", value: "医学与健康" }],
    description: [
      { locale: "zh-CN", value: "解剖、生理、疾病、治疗与公共健康" },
    ],
    rootTaxonomyNodeIds: ["tax-medicine"],
    iconKey: "medicine",
    status: "published",
  },
  {
    id: "space-oceanography",
    slug: "oceanography",
    name: [{ locale: "zh-CN", value: "海洋与水圈" }],
    description: [
      {
        locale: "zh-CN",
        value: "海洋地貌、深海地形与相关观测实体",
      },
    ],
    rootTaxonomyNodeIds: ["tax-marine-objects"],
    iconKey: "ocean",
    status: "published",
  },
  {
    id: "space-astronomy",
    slug: "astronomy",
    name: [{ locale: "zh-CN", value: "天文与空间" }],
    description: [
      {
        locale: "zh-CN",
        value: "天体、轨道、探测与宇宙探索相关知识实体。",
      },
    ],
    rootTaxonomyNodeIds: ["tax-astronomy"],
    iconKey: "astronomy",
    status: "published",
  },
];

const localEntityTypes: EntityType[] = [
  {
    id: "type-animal",
    spaceId: "space-life",
    key: "animal",
    name: [{ locale: "zh-CN", value: "动物" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-animals"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-animal",
    schemaVersion: "schema-2.1.0",
  },
  {
    id: "type-plant",
    spaceId: "space-life",
    key: "plant",
    name: [{ locale: "zh-CN", value: "植物" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-plants"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-plant",
    schemaVersion: "schema-2.0.0",
  },
  {
    id: "type-electronic-component",
    spaceId: "space-engineering",
    key: "electronic-component",
    name: [{ locale: "zh-CN", value: "电子元件" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-electronics"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-electronic-component",
    schemaVersion: "schema-2.0.0",
  },
  {
    id: "type-geography",
    spaceId: "space-earth",
    key: "geography",
    name: [{ locale: "zh-CN", value: "地理地貌" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-geography"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-geography",
    schemaVersion: "schema-2.0.0",
  },
  {
    id: "type-geology",
    spaceId: "space-earth",
    key: "geology",
    name: [{ locale: "zh-CN", value: "地质与资源" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-geology"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-geology",
    schemaVersion: "schema-2.0.0",
  },
  {
    id: "type-history",
    spaceId: "space-humanities",
    key: "history",
    name: [{ locale: "zh-CN", value: "历史与文明" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-history"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-history",
    schemaVersion: "schema-2.0.0",
  },
  {
    id: "type-language",
    spaceId: "space-humanities",
    key: "language",
    name: [{ locale: "zh-CN", value: "语言与文字" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-language"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-language",
    schemaVersion: "schema-2.0.0",
  },
  {
    id: "type-mathematics",
    spaceId: "space-mathematics",
    key: "mathematics",
    name: [{ locale: "zh-CN", value: "数学条目" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-mathematics"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-mathematics",
    schemaVersion: "schema-2.0.0",
  },
  {
    id: "type-medicine",
    spaceId: "space-medicine",
    key: "medicine",
    name: [{ locale: "zh-CN", value: "医学实体" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-medicine"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-medicine",
    schemaVersion: "schema-2.0.0",
  },
  {
    id: "type-sea-feature",
    spaceId: "space-oceanography",
    key: "sea-feature",
    name: [{ locale: "zh-CN", value: "海洋地貌" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-marine-objects", "tax-deep-sea"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-sea-feature",
    schemaVersion: "schema-2.0.0",
  },
  {
    id: "type-celestial-body",
    spaceId: "space-astronomy",
    key: "celestial-body",
    name: [{ locale: "zh-CN", value: "天体" }],
    description: [],
    allowedTaxonomyNodeIds: ["tax-astronomy", "tax-stars", "tax-planets"],
    attributeDefinitionIds: [],
    allowedRelationshipTypeIds: [],
    defaultViewDefinitionId: "view-celestial-body",
    schemaVersion: "schema-2.2.0",
  },
];

const isBrowser = typeof window !== "undefined";
const resolvedWebOrigin =
  process.env.NEXT_PUBLIC_HARDATLAS_WEB_URL ??
  process.env.NEXT_PUBLIC_WEB_ORIGIN ??
  process.env.WEB_ORIGIN ??
  process.env.NEXT_PUBLIC_HARDATLAS_WEB_HOST ??
  undefined;
// Browser callers (and same-host server contexts when web origin available) use
// the web BFF so auth/session headers and tracing stay consistent.
const apiBase = isBrowser
  ? "/apps/hardatlas/api/backend"
  : resolvedWebOrigin
    ? `${resolvedWebOrigin.replace(/\/$/, "")}/api/backend`
    : (process.env.HARDATLAS_API_URL ??
      process.env.NEXT_PUBLIC_HARDATLAS_API_URL ??
      (process.env.NODE_ENV === "development"
        ? "http://localhost:8000"
        : undefined));
const allowFixtureFallback =
  process.env.HARDATLAS_ALLOW_FIXTURE_FALLBACK === "true" ||
  process.env.NODE_ENV !== "production";

async function apiGet<T>(
  path: string,
  notFoundAsUndefined = false,
): Promise<T | undefined> {
  if (!apiBase) {
    if (allowFixtureFallback) return undefined;
    throw new Error(
      "HARDATLAS_API_URL is required when production fixture fallback is disabled",
    );
  }
  try {
    const response = await fetch(`${apiBase}${path}`, {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    if (response.status === 404 && notFoundAsUndefined) return undefined;
    if (!response.ok) {
      throw new Error(
        `Knowledge API request failed: ${response.status} ${path}`,
      );
    }
    return (await response.json()) as T;
  } catch (error) {
    if (allowFixtureFallback) return undefined;
    throw error;
  }
}

export async function getSpaces(): Promise<KnowledgeSpace[]> {
  return (await apiGet<KnowledgeSpace[]>("/api/v1/spaces")) ?? localSpaces;
}

export async function getBuildInfo(): Promise<BuildInfo> {
  return (
    (await apiGet<BuildInfo>("/api/v1/build")) ?? {
      buildSha: "offline-fixture",
      environment: "offline",
      dataVersion: localEntities[0]?.revision.dataVersion ?? "offline",
      schemaVersion: "offline",
      policyVersion: "offline",
    }
  );
}

export async function getEntityTypes(): Promise<EntityType[]> {
  return (
    (await apiGet<EntityType[]>("/api/v1/entity-types")) ?? localEntityTypes
  );
}

export async function getTaxonomyNodes(): Promise<TaxonomyNode[]> {
  return (await apiGet<TaxonomyNode[]>("/api/v1/taxonomy")) ?? localTaxonomy;
}

export async function getEntities(): Promise<KnowledgeEntity[]> {
  return (await apiGet<KnowledgeEntity[]>("/api/v1/entities")) ?? localEntities;
}

export async function getEntity(
  slug: string,
): Promise<KnowledgeEntity | undefined> {
  const remote = await apiGet<KnowledgeEntity>(
    `/api/v1/entities/${encodeURIComponent(slug)}`,
    true,
  );
  return remote ?? (allowFixtureFallback ? findEntity(slug) : undefined);
}

export async function getEntityRevision(
  slug: string,
  revisionId: string,
): Promise<KnowledgeEntity | undefined> {
  const remote = await apiGet<KnowledgeEntity>(
    `/api/v1/entities/${encodeURIComponent(slug)}/revisions/${encodeURIComponent(
      revisionId,
    )}`,
    true,
  );
  if (remote) return remote;
  if (!allowFixtureFallback) return undefined;
  const local = findEntity(slug);
  return local?.revision.revisionId === revisionId ? local : undefined;
}

export async function getEntityRevisionHistory(
  slug: string,
): Promise<EntityRevisionSummary[]> {
  const remote = await apiGet<EntityRevisionSummary[]>(
    `/api/v1/entities/${encodeURIComponent(slug)}/revisions`,
  );
  if (remote) return remote;
  const local = findEntity(slug);
  return local ? [{ revision: local.revision, current: true }] : [];
}

function relationshipsFromDocument(
  entity: KnowledgeEntity,
): RelationshipTraversalItem[] {
  return entity.relationships.map((relationship) => ({
    relationship,
    relationshipType: {
      id: relationship.typeId,
      key: relationship.typeId,
      name: [{ locale: "zh-CN", value: relationship.typeId }],
      inverseName: [{ locale: "zh-CN", value: relationship.typeId }],
      directed: true,
      sourceEntityTypeIds: [],
      targetEntityTypeIds: [],
      sourceCardinality: "many",
      targetCardinality: "many",
      qualifierSchema: {},
      evidenceRequired: true,
      schemaVersion: entity.revision.schemaVersion,
    },
    direction: "outgoing",
    neighbor: relationship.target,
  }));
}

export async function getEntityRelationships(
  entity: KnowledgeEntity,
  historical = false,
): Promise<RelationshipTraversalItem[]> {
  if (!historical) {
    const relationships = await apiGet<RelationshipTraversalItem[]>(
      `/api/v1/entities/${encodeURIComponent(entity.ref.slug)}/relationships`,
    );
    if (relationships) return relationships;
  }
  return relationshipsFromDocument(entity);
}

export async function searchKnowledge(
  query: string,
  typeId?: string,
): Promise<KnowledgeEntity[]> {
  const response = await searchDiscovery({ query, typeId });
  return response.items.map((item) => item.entity);
}

function fallbackFacet(
  values: string[],
  labels: Record<string, string> = {},
): SearchFacetValue[] {
  const counts = new Map<string, number>();
  values.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  return [...counts.entries()]
    .sort(
      ([valueA, countA], [valueB, countB]) =>
        countB - countA ||
        (labels[valueA] ?? valueA).localeCompare(labels[valueB] ?? valueB),
    )
    .map(([value, count]) => ({
      value,
      label: labels[value] ?? value,
      count,
    }));
}

export async function searchDiscovery(
  filters: SearchFilters,
): Promise<SearchResponse> {
  const parameters = new URLSearchParams({ q: filters.query ?? "" });
  if (filters.typeId) parameters.set("typeId", filters.typeId);
  if (filters.spaceId) parameters.set("spaceId", filters.spaceId);
  if (filters.taxonomyNodeId) {
    parameters.set("taxonomyNodeId", filters.taxonomyNodeId);
  }
  if (filters.locale) parameters.set("locale", filters.locale);
  if (filters.displayLocale) {
    parameters.set("displayLocale", filters.displayLocale);
  }
  if (filters.sourceTier) parameters.set("sourceTier", filters.sourceTier);
  if (filters.minimumSources) {
    parameters.set("minimumSources", String(filters.minimumSources));
  }
  parameters.set("limit", String(filters.limit ?? 20));
  parameters.set("offset", String(filters.offset ?? 0));
  const response = await apiGet<SearchResponse>(
    `/api/v1/search?${parameters.toString()}`,
  );
  if (response) return response;

  const query = filters.query?.trim() ?? "";
  const typesById = new Map(localEntityTypes.map((item) => [item.id, item]));
  const filtered = (query ? searchEntities(query) : localEntities).filter(
    (item) => {
      const type = typesById.get(item.ref.typeId);
      return (
        (!filters.typeId || item.ref.typeId === filters.typeId) &&
        (!filters.spaceId || type?.spaceId === filters.spaceId) &&
        (!filters.taxonomyNodeId ||
          item.taxonomyNodeIds.includes(filters.taxonomyNodeId)) &&
        (!filters.locale ||
          [...item.names, ...item.aliases, ...item.description].some(
            (value) => value.locale === filters.locale,
          )) &&
        (!filters.sourceTier ||
          item.citations.some(
            (citation) => citation.sourceTier === filters.sourceTier,
          )) &&
        item.citations.length >= (filters.minimumSources ?? 0)
      );
    },
  );
  const offset = filters.offset ?? 0;
  const limit = filters.limit ?? 20;
  const typeLabels = Object.fromEntries(
    localEntityTypes.map((item) => [
      item.id,
      localize(item.name, filters.displayLocale),
    ]),
  );
  const spaceLabels = Object.fromEntries(
    localSpaces.map((item) => [
      item.id,
      localize(item.name, filters.displayLocale),
    ]),
  );
  const taxonomyLabels = Object.fromEntries(
    localTaxonomy.map((item) => [
      item.id,
      localize(item.name, filters.displayLocale),
    ]),
  );
  const sourceTierLabels: Record<string, string> = {
    primary: "第一手来源",
    authoritative: "权威来源",
    secondary: "二级来源",
    community: "社区来源",
  };
  return {
    query,
    total: filtered.length,
    offset,
    limit,
    items: filtered.slice(offset, offset + limit).map((entity) => ({
      entity,
      matchedText: entity.ref.canonicalName,
      matchedField: query ? "localFallback" : "browse",
      score: 1,
    })),
    facets: {
      spaces: fallbackFacet(
        filtered
          .map((item) => typesById.get(item.ref.typeId)?.spaceId)
          .filter((value): value is string => Boolean(value)),
        spaceLabels,
      ),
      entityTypes: fallbackFacet(
        filtered.map((item) => item.ref.typeId),
        typeLabels,
      ),
      taxonomyNodes: fallbackFacet(
        filtered.flatMap((item) => item.taxonomyNodeIds),
        taxonomyLabels,
      ),
      locales: fallbackFacet(
        filtered.flatMap((item) => [
          ...new Set(
            [...item.names, ...item.aliases, ...item.description].map(
              (value) => value.locale,
            ),
          ),
        ]),
      ),
      sourceTiers: fallbackFacet(
        filtered.flatMap((item) => [
          ...new Set(item.citations.map((citation) => citation.sourceTier)),
        ]),
        sourceTierLabels,
      ),
    },
  };
}

export async function getViewForEntity(
  entity: KnowledgeEntity,
): Promise<ViewDefinition | undefined> {
  const type = await apiGet<EntityType>(
    `/api/v1/entity-types/${encodeURIComponent(entity.ref.typeId)}`,
  );
  if (type) {
    const view = await apiGet<ViewDefinition>(
      `/api/v1/view-definitions/${encodeURIComponent(
        type.defaultViewDefinitionId,
      )}`,
    );
    if (view) return view;
  }
  return localViews[entity.ref.typeId];
}
