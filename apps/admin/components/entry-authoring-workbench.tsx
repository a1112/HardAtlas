"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

interface LocalizedText {
  locale: string;
  value: string;
}

interface Citation {
  id: string;
  sourceId: string;
  sourceTitle: string;
  sourceUrl?: string;
  sourceTier: "primary" | "authoritative" | "secondary" | "community";
  retrievedAt: string;
}

interface DraftSection {
  key: string;
  heading: LocalizedText[];
  body: LocalizedText[];
  citationIds: string[];
}

interface EntityRef {
  id: string;
  slug: string;
  typeId: string;
  canonicalName: string;
}

interface Relationship {
  id: string;
  typeId: string;
  source: EntityRef;
  target: EntityRef;
  qualifiers: Record<string, unknown>;
  confidence: number;
  citationIds: string[];
  revisionId: string;
}

interface EntityDraft {
  id: string;
  slug: string;
  typeId: string;
  names: LocalizedText[];
  aliases: LocalizedText[];
  description: LocalizedText[];
  taxonomyNodeIds: string[];
  attributeValues: Record<string, unknown>;
  sections: DraftSection[];
  relationships: Relationship[];
  citations: Citation[];
}

interface AttributeDefinition {
  id: string;
  name: LocalizedText[];
  dataType: string;
  cardinality: "one" | "many";
  required: boolean;
  enumValues?: string[];
  schemaVersion: string;
}

interface EntityType {
  id: string;
  spaceId: string;
  name: LocalizedText[];
  allowedTaxonomyNodeIds: string[];
  attributeDefinitionIds: string[];
  allowedRelationshipTypeIds: string[];
  defaultViewDefinitionId: string;
  schemaVersion: string;
}

interface RelationshipType {
  id: string;
  name: LocalizedText[];
  inverseName: LocalizedText[];
  targetEntityTypeIds: string[];
  sourceCardinality: "one" | "many";
  targetCardinality: "one" | "many";
  qualifierSchema: Record<
    string,
    { type?: string; enum?: Array<string | number | boolean> }
  >;
  evidenceRequired: boolean;
  schemaVersion: string;
}

interface ViewDefinition {
  id: string;
  blocks: Array<{ id: string; type: string }>;
}

interface Registry {
  entityTypes: EntityType[];
  attributeDefinitions: AttributeDefinition[];
  relationshipTypes: RelationshipType[];
  viewDefinitions: ViewDefinition[];
}

interface TaxonomyNode {
  id: string;
  spaceId: string;
  name: LocalizedText[];
  pathKeys: string[];
}

interface EntitySummary {
  ref: EntityRef;
  revision: { revisionId: string };
}

interface DraftIssue {
  severity: "error" | "warning";
  code: string;
  path: string;
  message: string;
}

interface AuthoringDraftRecord {
  id: string;
  mode: "create" | "revise";
  draft: EntityDraft;
  baseRevisionId?: string;
  status: "editing" | "submitted" | "abandoned";
  proposalId?: string;
  version: number;
  updatedAt: string;
}

const apiUrl = "/api/backend";

function label(values: LocalizedText[]) {
  return (
    values.find((item) => item.locale === "zh-CN")?.value ??
    values[0]?.value ??
    ""
  );
}

function replacePrimary(
  values: LocalizedText[],
  value: string,
): LocalizedText[] {
  const current = values[0];
  if (!current) return [{ locale: "zh-CN", value }];
  return [{ locale: current.locale, value }, ...values.slice(1)];
}

function formatAttribute(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
}

function errorDetail(value: unknown, fallback: string): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") return JSON.stringify(value);
  return fallback;
}

function parseAttribute(
  definition: AttributeDefinition,
  value: string,
): unknown {
  if (!value.trim()) return undefined;
  if (definition.cardinality === "many") {
    if (value.trim().startsWith("[")) return JSON.parse(value);
    return value
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
  }
  if (definition.dataType === "integer") return Number.parseInt(value, 10);
  if (definition.dataType === "decimal") return Number.parseFloat(value);
  if (definition.dataType === "boolean") return value === "true";
  if (
    ["measurement", "date-range", "geo", "entity-ref", "media-ref"].includes(
      definition.dataType,
    )
  ) {
    return JSON.parse(value);
  }
  return value;
}

function parseQualifier(value: string, expectedType?: string): unknown {
  if (expectedType === "number") return Number.parseFloat(value);
  if (expectedType === "integer") return Number.parseInt(value, 10);
  if (expectedType === "boolean") return value === "true";
  if (expectedType === "object" || expectedType === "array") {
    return JSON.parse(value);
  }
  return value;
}

function emptyDraft(
  entityType: EntityType,
  taxonomy: TaxonomyNode[],
): EntityDraft {
  const allowed = taxonomy.filter(
    (node) =>
      node.spaceId === entityType.spaceId &&
      (!entityType.allowedTaxonomyNodeIds.length ||
        entityType.allowedTaxonomyNodeIds.includes(node.id)),
  );
  return {
    id: "entity-new-entry",
    slug: "new-entry",
    typeId: entityType.id,
    names: [{ locale: "zh-CN", value: "" }],
    aliases: [],
    description: [{ locale: "zh-CN", value: "" }],
    taxonomyNodeIds: allowed[0] ? [allowed[0].id] : [],
    attributeValues: {},
    sections: [
      {
        key: "overview",
        heading: [{ locale: "zh-CN", value: "概述" }],
        body: [{ locale: "zh-CN", value: "" }],
        citationIds: ["cite-new-entry-001"],
      },
    ],
    relationships: [],
    citations: [
      {
        id: "cite-new-entry-001",
        sourceId: "",
        sourceTitle: "",
        sourceTier: "authoritative",
        retrievedAt: new Date().toISOString(),
      },
    ],
  };
}

export function EntryAuthoringWorkbench() {
  const [registry, setRegistry] = useState<Registry>();
  const [taxonomy, setTaxonomy] = useState<TaxonomyNode[]>([]);
  const [entities, setEntities] = useState<EntitySummary[]>([]);
  const [mode, setMode] = useState<"create" | "revise">("create");
  const [selectedEntityId, setSelectedEntityId] = useState("");
  const [draft, setDraft] = useState<EntityDraft>();
  const [baseRevisionId, setBaseRevisionId] = useState<string>();
  const [savedDrafts, setSavedDrafts] = useState<AuthoringDraftRecord[]>([]);
  const [savedDraftId, setSavedDraftId] = useState("");
  const [persistedDraft, setPersistedDraft] = useState<AuthoringDraftRecord>();
  const [attributeInputs, setAttributeInputs] = useState<
    Record<string, string>
  >({});
  const [issues, setIssues] = useState<DraftIssue[]>([]);
  const [notice, setNotice] = useState("正在加载条目与 Schema…");
  const [busy, setBusy] = useState(false);
  const [proposalId, setProposalId] = useState<string>();

  useEffect(() => {
    void Promise.all([
      adminFetch(`${apiUrl}/api/v1/schema-registry`, { cache: "no-store" }),
      adminFetch(`${apiUrl}/api/v1/taxonomy`, { cache: "no-store" }),
      adminFetch(`${apiUrl}/api/v1/entities`, { cache: "no-store" }),
      adminFetch(`${apiUrl}/api/v1/authoring-drafts?status=editing`, {
        cache: "no-store",
      }),
    ])
      .then(
        async ([
          registryResponse,
          taxonomyResponse,
          entitiesResponse,
          draftsResponse,
        ]) => {
          if (
            !registryResponse.ok ||
            !taxonomyResponse.ok ||
            !entitiesResponse.ok ||
            !draftsResponse.ok
          ) {
            throw new Error("条目创作 API 返回异常");
          }
          const nextRegistry = (await registryResponse.json()) as Registry;
          const nextTaxonomy =
            (await taxonomyResponse.json()) as TaxonomyNode[];
          const nextEntities =
            (await entitiesResponse.json()) as EntitySummary[];
          const nextDrafts =
            (await draftsResponse.json()) as AuthoringDraftRecord[];
          setRegistry(nextRegistry);
          setTaxonomy(nextTaxonomy);
          setEntities(nextEntities);
          setSavedDrafts(nextDrafts);
          setSavedDraftId(nextDrafts[0]?.id ?? "");
          const firstType = nextRegistry.entityTypes[0];
          if (firstType) setDraft(emptyDraft(firstType, nextTaxonomy));
          setSelectedEntityId(nextEntities[0]?.ref.id ?? "");
          setNotice("条目、Schema 与分类注册表已同步");
        },
      )
      .catch((error: unknown) =>
        setNotice(error instanceof Error ? error.message : "创作 API 不可用"),
      );
  }, []);

  const entityType = registry?.entityTypes.find(
    (item) => item.id === draft?.typeId,
  );
  const attributes = useMemo(
    () =>
      entityType?.attributeDefinitionIds
        .map((id) =>
          registry?.attributeDefinitions.find((item) => item.id === id),
        )
        .filter((item): item is AttributeDefinition => Boolean(item)) ?? [],
    [entityType, registry],
  );
  const relationshipTypes = useMemo(
    () =>
      entityType?.allowedRelationshipTypeIds
        .map((id) => registry?.relationshipTypes.find((item) => item.id === id))
        .filter((item): item is RelationshipType => Boolean(item)) ?? [],
    [entityType, registry],
  );
  const compatibleTaxonomy = taxonomy.filter(
    (node) =>
      node.spaceId === entityType?.spaceId &&
      (!entityType?.allowedTaxonomyNodeIds.length ||
        entityType.allowedTaxonomyNodeIds.includes(node.id)),
  );
  const view = registry?.viewDefinitions.find(
    (item) => item.id === entityType?.defaultViewDefinitionId,
  );

  const resetAttributeInputs = useCallback(
    (nextDraft: EntityDraft, definitions: AttributeDefinition[]) => {
      setAttributeInputs(
        Object.fromEntries(
          definitions.map((definition) => [
            definition.id,
            formatAttribute(nextDraft.attributeValues[definition.id]),
          ]),
        ),
      );
    },
    [],
  );

  const upsertSavedDraft = useCallback((record: AuthoringDraftRecord) => {
    setSavedDrafts((current) => [
      record,
      ...current.filter((item) => item.id !== record.id),
    ]);
    setSavedDraftId(record.id);
  }, []);

  function clearPersistedContext() {
    setPersistedDraft(undefined);
    setIssues([]);
    setProposalId(undefined);
  }

  useEffect(() => {
    if (draft && attributes.length) resetAttributeInputs(draft, attributes);
  }, [attributes, draft?.id, resetAttributeInputs]);

  async function loadRevision() {
    if (!selectedEntityId) return;
    setBusy(true);
    setNotice("正在钉住当前不可变修订…");
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/entities/${selectedEntityId}/authoring-draft`,
        { cache: "no-store" },
      );
      const payload = (await response.json()) as {
        draft?: EntityDraft;
        baseRevisionId?: string;
        attributeDefinitions?: AttributeDefinition[];
        detail?: string;
      };
      if (!response.ok || !payload.draft || !payload.baseRevisionId) {
        throw new Error(payload.detail ?? "读取修订草稿失败");
      }
      setDraft(payload.draft);
      setBaseRevisionId(payload.baseRevisionId);
      setPersistedDraft(undefined);
      resetAttributeInputs(payload.draft, payload.attributeDefinitions ?? []);
      setIssues([]);
      setProposalId(undefined);
      setNotice(`已钉住基础修订 ${payload.baseRevisionId}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "读取修订草稿失败");
    } finally {
      setBusy(false);
    }
  }

  function switchType(typeId: string) {
    if (!registry) return;
    const nextType = registry.entityTypes.find((item) => item.id === typeId);
    if (!nextType) return;
    const nextDraft = emptyDraft(nextType, taxonomy);
    setDraft(nextDraft);
    resetAttributeInputs(
      nextDraft,
      nextType.attributeDefinitionIds
        .map((id) =>
          registry.attributeDefinitions.find((item) => item.id === id),
        )
        .filter((item): item is AttributeDefinition => Boolean(item)),
    );
    setBaseRevisionId(undefined);
    clearPersistedContext();
  }

  function materializeDraft(): EntityDraft {
    if (!draft) throw new Error("草稿尚未初始化");
    const values = { ...draft.attributeValues };
    for (const definition of attributes) {
      const parsed = parseAttribute(
        definition,
        attributeInputs[definition.id] ?? "",
      );
      if (parsed === undefined) delete values[definition.id];
      else values[definition.id] = parsed;
    }
    return { ...draft, attributeValues: values };
  }

  function relationshipTargets(typeId: string): EntitySummary[] {
    const definition = relationshipTypes.find((item) => item.id === typeId);
    if (!definition) return [];
    return entities.filter(
      (entity) =>
        !definition.targetEntityTypeIds.length ||
        definition.targetEntityTypeIds.includes(entity.ref.typeId),
    );
  }

  function addRelationship() {
    if (!draft) return;
    const definition = relationshipTypes[0];
    if (!definition) {
      setNotice("当前实体类型没有声明可用关系");
      return;
    }
    const target = relationshipTargets(definition.id)[0];
    if (!target) {
      setNotice("当前关系类型没有可选目标条目");
      return;
    }
    const qualifiers = Object.fromEntries(
      Object.entries(definition.qualifierSchema).map(([key, schema]) => [
        key,
        schema.enum?.[0] ?? (schema.type === "boolean" ? false : ""),
      ]),
    );
    setDraft({
      ...draft,
      relationships: [
        ...draft.relationships,
        {
          id: `relation-${draft.id}-${Date.now()}`,
          typeId: definition.id,
          source: {
            id: draft.id,
            slug: draft.slug,
            typeId: draft.typeId,
            canonicalName: draft.names[0]?.value ?? draft.id,
          },
          target: target.ref,
          qualifiers,
          confidence: 1,
          citationIds: draft.citations[0] ? [draft.citations[0].id] : [],
          revisionId: baseRevisionId ?? "draft",
        },
      ],
    });
  }

  async function persistCandidate(
    candidate: EntityDraft,
  ): Promise<AuthoringDraftRecord> {
    if (mode === "revise" && !baseRevisionId) {
      throw new Error("修订模式必须先钉住基础修订");
    }
    const response = persistedDraft
      ? await adminFetch(
          `${apiUrl}/api/v1/authoring-drafts/${persistedDraft.id}`,
          {
            method: "PUT",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              expectedVersion: persistedDraft.version,
              draft: candidate,
            }),
          },
        )
      : await adminFetch(`${apiUrl}/api/v1/authoring-drafts`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            id: `draft-${mode}-${candidate.id}-${Date.now()}`,
            mode,
            draft: candidate,
            ...(baseRevisionId ? { baseRevisionId } : {}),
          }),
        });
    const payload = (await response.json()) as
      AuthoringDraftRecord | { detail?: unknown };
    if (!response.ok || !("id" in payload)) {
      throw new Error(
        errorDetail(
          "detail" in payload ? payload.detail : undefined,
          "保存草稿失败",
        ),
      );
    }
    setPersistedDraft(payload);
    upsertSavedDraft(payload);
    return payload;
  }

  async function saveDraft() {
    setBusy(true);
    try {
      const candidate = materializeDraft();
      setDraft(candidate);
      setNotice("正在保存工作区私有草稿…");
      const saved = await persistCandidate(candidate);
      setNotice(`草稿已保存 · v${saved.version} · 仅当前工作区可见`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存草稿失败");
    } finally {
      setBusy(false);
    }
  }

  async function restoreDraft() {
    if (!savedDraftId || !registry) return;
    setBusy(true);
    setNotice("正在恢复工作区草稿…");
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/authoring-drafts/${savedDraftId}`,
        { cache: "no-store" },
      );
      const payload = (await response.json()) as
        AuthoringDraftRecord | { detail?: unknown };
      if (!response.ok || !("id" in payload)) {
        throw new Error(
          errorDetail(
            "detail" in payload ? payload.detail : undefined,
            "恢复草稿失败",
          ),
        );
      }
      const nextType = registry.entityTypes.find(
        (item) => item.id === payload.draft.typeId,
      );
      const definitions =
        nextType?.attributeDefinitionIds
          .map((id) =>
            registry.attributeDefinitions.find((item) => item.id === id),
          )
          .filter((item): item is AttributeDefinition => Boolean(item)) ?? [];
      setMode(payload.mode);
      setDraft(payload.draft);
      setBaseRevisionId(payload.baseRevisionId);
      setSelectedEntityId(payload.draft.id);
      setPersistedDraft(payload);
      resetAttributeInputs(payload.draft, definitions);
      setIssues([]);
      setProposalId(undefined);
      setNotice(`已恢复草稿 ${payload.id} · v${payload.version}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "恢复草稿失败");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft) return;
    setBusy(true);
    setProposalId(undefined);
    try {
      const candidate = materializeDraft();
      setDraft(candidate);
      setNotice("正在执行服务端 Schema 与证据校验…");
      const validationResponse = await adminFetch(
        `${apiUrl}/api/v1/entity-drafts/validate`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(candidate),
        },
      );
      const validation = (await validationResponse.json()) as {
        valid: boolean;
        issues: DraftIssue[];
      };
      if (!validationResponse.ok) throw new Error("草稿校验请求失败");
      setIssues(validation.issues);
      if (!validation.valid) {
        setNotice("草稿未通过校验，没有产生治理提案");
        return;
      }
      setNotice("校验通过，正在保存草稿并提交治理提案…");
      const saved = await persistCandidate(candidate);
      const timestamp = Date.now();
      const proposalResponse = await adminFetch(
        `${apiUrl}/api/v1/authoring-drafts/${saved.id}/submit`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            expectedVersion: saved.version,
            proposalId: `proposal-${mode}-${candidate.slug}-${timestamp}`,
            agentRunId: `human-authoring-${timestamp}`,
            confidence: 0.98,
            risk: "medium",
          }),
        },
      );
      const submission = (await proposalResponse.json()) as {
        draft?: AuthoringDraftRecord;
        proposal?: {
          proposal?: { id: string; operations: unknown[] };
        };
        detail?: unknown;
      };
      const proposal = submission.proposal?.proposal;
      if (!proposalResponse.ok || !submission.draft || !proposal) {
        throw new Error(errorDetail(submission.detail, "创建治理提案失败"));
      }
      setPersistedDraft(submission.draft);
      setSavedDrafts((current) =>
        current.filter((item) => item.id !== submission.draft?.id),
      );
      setSavedDraftId("");
      setProposalId(proposal.id);
      setNotice(
        `草稿 v${submission.draft.version} 已提交，创建 ${proposal.operations.length} 项差异操作；公开知识尚未改变`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }

  if (!draft) {
    return (
      <main className="entry-authoring-page">
        <p>{notice}</p>
      </main>
    );
  }

  return (
    <main className="entry-authoring-page">
      <header className="entry-authoring-header">
        <div>
          <a href="/">← 返回运行中心</a>
          <p>SCHEMA-DRIVEN EDITORIAL WORKBENCH</p>
          <h1>通用条目工作室</h1>
          <span>人工与 Agent 共用同一草稿、证据、提案和不可变修订链路</span>
        </div>
        <div>
          <small>{notice}</small>
          {persistedDraft ? (
            <code>
              {persistedDraft.status} · v{persistedDraft.version} ·{" "}
              {persistedDraft.id}
            </code>
          ) : null}
          {proposalId ? (
            <a href={`/proposals/${proposalId}`}>审核提案 {proposalId} →</a>
          ) : null}
        </div>
      </header>

      <section className="authoring-mode-bar">
        <div role="group" aria-label="创作模式">
          <button
            data-active={mode === "create"}
            onClick={() => {
              setMode("create");
              if (registry?.entityTypes[0]) {
                switchType(registry.entityTypes[0].id);
              }
            }}
            type="button"
          >
            新建条目
          </button>
          <button
            data-active={mode === "revise"}
            onClick={() => {
              setMode("revise");
              setBaseRevisionId(undefined);
              clearPersistedContext();
            }}
            type="button"
          >
            修订现有条目
          </button>
        </div>
        {mode === "revise" ? (
          <div className="revision-picker">
            <label>
              现有条目
              <select
                onChange={(event) => setSelectedEntityId(event.target.value)}
                value={selectedEntityId}
              >
                {entities.map((entity) => (
                  <option key={entity.ref.id} value={entity.ref.id}>
                    {entity.ref.canonicalName} · {entity.revision.revisionId}
                  </option>
                ))}
              </select>
            </label>
            <button disabled={busy} onClick={() => void loadRevision()}>
              载入并钉住修订
            </button>
          </div>
        ) : (
          <label>
            实体类型
            <select
              onChange={(event) => switchType(event.target.value)}
              value={draft.typeId}
            >
              {registry?.entityTypes.map((item) => (
                <option key={item.id} value={item.id}>
                  {label(item.name)} · {item.schemaVersion}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>

      <section className="draft-resume-bar" aria-label="可恢复草稿">
        <div>
          <p>WORKSPACE DRAFTS</p>
          <strong>可恢复草稿</strong>
          <span>草稿隔离在当前工作区，并以乐观版本锁防止覆盖。</span>
        </div>
        {savedDrafts.length ? (
          <div>
            <label>
              编辑中的草稿
              <select
                onChange={(event) => setSavedDraftId(event.target.value)}
                value={savedDraftId}
              >
                {savedDrafts.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.draft.names[0]?.value || item.draft.id} ·{" "}
                    {item.mode === "create" ? "新建" : "修订"} · v{item.version}
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={busy || !savedDraftId}
              onClick={() => void restoreDraft()}
              type="button"
            >
              恢复草稿
            </button>
          </div>
        ) : (
          <small>当前工作区没有编辑中的草稿。</small>
        )}
      </section>

      <div className="entry-authoring-layout">
        <form className="entry-authoring-form" onSubmit={submit}>
          <div className="authoring-section-title">
            <div>
              <p>{mode === "create" ? "NEW ENTITY" : "REVISION DRAFT"}</p>
              <h2>{label(entityType?.name ?? [])}条目</h2>
            </div>
            <code>{baseRevisionId ?? entityType?.schemaVersion}</code>
          </div>
          <div className="authoring-grid">
            <label>
              实体 ID
              <input
                disabled={mode === "revise"}
                onChange={(event) =>
                  setDraft({ ...draft, id: event.target.value })
                }
                required
                value={draft.id}
              />
            </label>
            <label>
              稳定 URL slug
              <input
                disabled={mode === "revise"}
                onChange={(event) =>
                  setDraft({ ...draft, slug: event.target.value })
                }
                required
                value={draft.slug}
              />
            </label>
            <label>
              规范名称
              <input
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    names: replacePrimary(draft.names, event.target.value),
                  })
                }
                required
                value={draft.names[0]?.value ?? ""}
              />
            </label>
            <label>
              分类节点
              <select
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    taxonomyNodeIds: [event.target.value],
                  })
                }
                value={draft.taxonomyNodeIds[0] ?? ""}
              >
                {compatibleTaxonomy.map((node) => (
                  <option key={node.id} value={node.id}>
                    {node.pathKeys.join(" / ")} · {label(node.name)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label>
            别名（每行一个）
            <textarea
              onChange={(event) =>
                setDraft({
                  ...draft,
                  aliases: event.target.value
                    .split("\n")
                    .map((value) => value.trim())
                    .filter(Boolean)
                    .map((value) => ({ locale: "zh-CN", value })),
                })
              }
              value={draft.aliases.map((item) => item.value).join("\n")}
            />
          </label>
          <label>
            条目摘要
            <textarea
              onChange={(event) =>
                setDraft({
                  ...draft,
                  description: replacePrimary(
                    draft.description,
                    event.target.value,
                  ),
                })
              }
              required
              value={draft.description[0]?.value ?? ""}
            />
          </label>

          <fieldset className="authoring-attributes">
            <legend>Schema 动态属性</legend>
            {attributes.map((definition) => (
              <label key={definition.id}>
                <span>
                  {label(definition.name)}
                  <code>{definition.dataType}</code>
                </span>
                {definition.enumValues ? (
                  <select
                    onChange={(event) =>
                      setAttributeInputs({
                        ...attributeInputs,
                        [definition.id]: event.target.value,
                      })
                    }
                    required={definition.required}
                    value={attributeInputs[definition.id] ?? ""}
                  >
                    <option value="">请选择</option>
                    {definition.enumValues.map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                ) : (
                  <textarea
                    aria-label={label(definition.name)}
                    onChange={(event) =>
                      setAttributeInputs({
                        ...attributeInputs,
                        [definition.id]: event.target.value,
                      })
                    }
                    placeholder={
                      definition.cardinality === "many"
                        ? "每行一个值，复杂值可输入 JSON"
                        : definition.dataType
                    }
                    required={definition.required}
                    value={attributeInputs[definition.id] ?? ""}
                  />
                )}
              </label>
            ))}
          </fieldset>

          <fieldset className="authoring-sections">
            <legend>结构化章节</legend>
            {draft.sections.map((section, index) => (
              <article key={`${section.key}-${index}`}>
                <label>
                  章节标题
                  <input
                    onChange={(event) => {
                      const sections = [...draft.sections];
                      sections[index] = {
                        ...section,
                        heading: replacePrimary(
                          section.heading,
                          event.target.value,
                        ),
                      };
                      setDraft({ ...draft, sections });
                    }}
                    value={section.heading[0]?.value ?? ""}
                  />
                </label>
                <label>
                  章节正文
                  <textarea
                    onChange={(event) => {
                      const sections = [...draft.sections];
                      sections[index] = {
                        ...section,
                        body: replacePrimary(section.body, event.target.value),
                      };
                      setDraft({ ...draft, sections });
                    }}
                    required
                    value={section.body[0]?.value ?? ""}
                  />
                </label>
                <label>
                  引用 ID（逗号分隔）
                  <input
                    onChange={(event) => {
                      const sections = [...draft.sections];
                      sections[index] = {
                        ...section,
                        citationIds: event.target.value
                          .split(",")
                          .map((item) => item.trim())
                          .filter(Boolean),
                      };
                      setDraft({ ...draft, sections });
                    }}
                    value={section.citationIds.join(", ")}
                  />
                </label>
              </article>
            ))}
          </fieldset>

          <fieldset className="authoring-relationships">
            <legend>Schema 动态关系</legend>
            {draft.relationships.length ? (
              draft.relationships.map((relationship, index) => {
                const definition = relationshipTypes.find(
                  (item) => item.id === relationship.typeId,
                );
                const targets = relationshipTargets(relationship.typeId);
                return (
                  <article key={relationship.id}>
                    <div className="relationship-editor-head">
                      <strong>
                        {definition
                          ? label(definition.name)
                          : relationship.typeId}
                      </strong>
                      <code>
                        {definition?.sourceCardinality ?? "?"}:
                        {definition?.targetCardinality ?? "?"} ·{" "}
                        {definition?.schemaVersion}
                      </code>
                      <button
                        className="danger"
                        onClick={() =>
                          setDraft({
                            ...draft,
                            relationships: draft.relationships.filter(
                              (_, itemIndex) => itemIndex !== index,
                            ),
                          })
                        }
                        type="button"
                      >
                        移除
                      </button>
                    </div>
                    <div className="relationship-editor-grid">
                      <label>
                        关系类型
                        <select
                          onChange={(event) => {
                            const nextDefinition = relationshipTypes.find(
                              (item) => item.id === event.target.value,
                            );
                            if (!nextDefinition) return;
                            const nextTarget = relationshipTargets(
                              nextDefinition.id,
                            )[0];
                            if (!nextTarget) return;
                            const relationships = [...draft.relationships];
                            relationships[index] = {
                              ...relationship,
                              typeId: nextDefinition.id,
                              target: nextTarget.ref,
                              qualifiers: Object.fromEntries(
                                Object.entries(
                                  nextDefinition.qualifierSchema,
                                ).map(([key, schema]) => [
                                  key,
                                  schema.enum?.[0] ??
                                    (schema.type === "boolean" ? false : ""),
                                ]),
                              ),
                            };
                            setDraft({ ...draft, relationships });
                          }}
                          value={relationship.typeId}
                        >
                          {relationshipTypes.map((item) => (
                            <option key={item.id} value={item.id}>
                              {label(item.name)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        目标条目
                        <select
                          onChange={(event) => {
                            const target = entities.find(
                              (item) => item.ref.id === event.target.value,
                            );
                            if (!target) return;
                            const relationships = [...draft.relationships];
                            relationships[index] = {
                              ...relationship,
                              target: target.ref,
                            };
                            setDraft({ ...draft, relationships });
                          }}
                          value={relationship.target.id}
                        >
                          {targets.map((target) => (
                            <option key={target.ref.id} value={target.ref.id}>
                              {target.ref.canonicalName} · {target.ref.typeId}
                            </option>
                          ))}
                        </select>
                      </label>
                      {Object.entries(definition?.qualifierSchema ?? {}).map(
                        ([key, schema]) => (
                          <label key={key}>
                            限定字段 · {key}
                            {schema.enum ? (
                              <select
                                onChange={(event) => {
                                  const relationships = [
                                    ...draft.relationships,
                                  ];
                                  relationships[index] = {
                                    ...relationship,
                                    qualifiers: {
                                      ...relationship.qualifiers,
                                      [key]: parseQualifier(
                                        event.target.value,
                                        schema.type,
                                      ),
                                    },
                                  };
                                  setDraft({ ...draft, relationships });
                                }}
                                value={String(
                                  relationship.qualifiers[key] ?? "",
                                )}
                              >
                                {schema.enum.map((value) => (
                                  <option key={String(value)}>
                                    {String(value)}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <input
                                onChange={(event) => {
                                  try {
                                    const relationships = [
                                      ...draft.relationships,
                                    ];
                                    relationships[index] = {
                                      ...relationship,
                                      qualifiers: {
                                        ...relationship.qualifiers,
                                        [key]: parseQualifier(
                                          event.target.value,
                                          schema.type,
                                        ),
                                      },
                                    };
                                    setDraft({ ...draft, relationships });
                                  } catch {
                                    setNotice(`限定字段 ${key} 需要有效 JSON`);
                                  }
                                }}
                                value={formatAttribute(
                                  relationship.qualifiers[key],
                                )}
                              />
                            )}
                          </label>
                        ),
                      )}
                      <label>
                        引用 ID（逗号分隔）
                        <input
                          onChange={(event) => {
                            const relationships = [...draft.relationships];
                            relationships[index] = {
                              ...relationship,
                              citationIds: event.target.value
                                .split(",")
                                .map((item) => item.trim())
                                .filter(Boolean),
                            };
                            setDraft({ ...draft, relationships });
                          }}
                          required={definition?.evidenceRequired}
                          value={relationship.citationIds.join(", ")}
                        />
                      </label>
                      <label>
                        置信度
                        <input
                          max="1"
                          min="0"
                          onChange={(event) => {
                            const relationships = [...draft.relationships];
                            relationships[index] = {
                              ...relationship,
                              confidence: Number.parseFloat(event.target.value),
                            };
                            setDraft({ ...draft, relationships });
                          }}
                          step="0.01"
                          type="number"
                          value={relationship.confidence}
                        />
                      </label>
                    </div>
                  </article>
                );
              })
            ) : (
              <small>
                当前草稿没有关系。关系类型、目标约束、限定字段和证据要求均来自
                Domain Pack。
              </small>
            )}
            <button
              className="secondary"
              disabled={!relationshipTypes.length}
              onClick={addRelationship}
              type="button"
            >
              添加关系
            </button>
          </fieldset>

          <fieldset className="authoring-citations">
            <legend>来源证据</legend>
            {draft.citations.map((citation, index) => (
              <article key={`${citation.id}-${index}`}>
                <label>
                  引用 ID
                  <input
                    onChange={(event) => {
                      const citations = [...draft.citations];
                      citations[index] = {
                        ...citation,
                        id: event.target.value,
                      };
                      setDraft({ ...draft, citations });
                    }}
                    required
                    value={citation.id}
                  />
                </label>
                <label>
                  来源 ID
                  <input
                    onChange={(event) => {
                      const citations = [...draft.citations];
                      citations[index] = {
                        ...citation,
                        sourceId: event.target.value,
                      };
                      setDraft({ ...draft, citations });
                    }}
                    required
                    value={citation.sourceId}
                  />
                </label>
                <label>
                  来源标题
                  <input
                    onChange={(event) => {
                      const citations = [...draft.citations];
                      citations[index] = {
                        ...citation,
                        sourceTitle: event.target.value,
                      };
                      setDraft({ ...draft, citations });
                    }}
                    required
                    value={citation.sourceTitle}
                  />
                </label>
                <label>
                  来源等级
                  <select
                    onChange={(event) => {
                      const citations = [...draft.citations];
                      citations[index] = {
                        ...citation,
                        sourceTier: event.target
                          .value as Citation["sourceTier"],
                      };
                      setDraft({ ...draft, citations });
                    }}
                    value={citation.sourceTier}
                  >
                    <option value="primary">原始来源</option>
                    <option value="authoritative">权威来源</option>
                    <option value="secondary">二手来源</option>
                    <option value="community">社区来源</option>
                  </select>
                </label>
              </article>
            ))}
            <button
              className="secondary"
              onClick={() =>
                setDraft({
                  ...draft,
                  citations: [
                    ...draft.citations,
                    {
                      id: `cite-${draft.slug}-${draft.citations.length + 1}`,
                      sourceId: "",
                      sourceTitle: "",
                      sourceTier: "authoritative",
                      retrievedAt: new Date().toISOString(),
                    },
                  ],
                })
              }
              type="button"
            >
              添加来源
            </button>
          </fieldset>

          <div className="authoring-form-actions">
            <button
              className="secondary"
              disabled={busy || !entityType}
              onClick={() => void saveDraft()}
              type="button"
            >
              保存私有草稿
            </button>
            <button disabled={busy || !entityType} type="submit">
              校验并提交治理提案
            </button>
          </div>
        </form>

        <aside className="entry-live-preview">
          <p>SAFE VIEW PREVIEW</p>
          <h2>{draft.names[0]?.value || "未命名条目"}</h2>
          <code>{draft.slug}</code>
          <span>{draft.description[0]?.value || "条目摘要将在此预览"}</span>
          <dl>
            {attributes.map((definition) => (
              <div key={definition.id}>
                <dt>{label(definition.name)}</dt>
                <dd>{attributeInputs[definition.id] || "未知"}</dd>
              </div>
            ))}
          </dl>
          <div className="entry-preview-sections">
            {draft.sections.map((section, index) => (
              <article key={`${section.key}-${index}`}>
                <h3>{section.heading[0]?.value}</h3>
                <p>{section.body[0]?.value || "待补充内容"}</p>
                <small>{section.citationIds.length} 条引用</small>
              </article>
            ))}
          </div>
          <div className="entry-preview-relations">
            <h3>关系预览</h3>
            {draft.relationships.length ? (
              draft.relationships.map((relationship) => (
                <span key={relationship.id}>
                  {label(
                    relationshipTypes.find(
                      (item) => item.id === relationship.typeId,
                    )?.name ?? [],
                  ) || relationship.typeId}{" "}
                  → {relationship.target.canonicalName}
                </span>
              ))
            ) : (
              <small>暂无关系</small>
            )}
          </div>
          <div className="entry-preview-contract">
            <h3>公开页面安全块</h3>
            {view?.blocks.map((block, index) => (
              <span key={block.id}>
                {index + 1}. {block.type}
              </span>
            ))}
          </div>
          <div className="entry-preview-issues">
            <h3>服务端校验</h3>
            {issues.length ? (
              issues.map((issue) => (
                <article
                  data-severity={issue.severity}
                  key={`${issue.path}-${issue.code}`}
                >
                  <b>{issue.severity === "error" ? "错误" : "提示"}</b>
                  <span>{issue.message}</span>
                  <code>{issue.path}</code>
                </article>
              ))
            ) : (
              <small>尚未执行校验；预览不会写入公开知识库。</small>
            )}
          </div>
        </aside>
      </div>
    </main>
  );
}
