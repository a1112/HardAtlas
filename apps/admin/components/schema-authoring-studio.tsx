"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

interface LocalizedText {
  locale: string;
  value: string;
}

interface AttributeDefinition {
  id: string;
  key: string;
  name: LocalizedText[];
  dataType: string;
  cardinality: "one" | "many";
  required: boolean;
  unitFamily?: string;
  enumValues?: string[];
  schemaVersion: string;
}

interface EntityType {
  id: string;
  spaceId: string;
  key: string;
  name: LocalizedText[];
  description: LocalizedText[];
  allowedTaxonomyNodeIds: string[];
  attributeDefinitionIds: string[];
  allowedRelationshipTypeIds: string[];
  defaultViewDefinitionId: string;
  schemaVersion: string;
}

interface RelationshipType {
  id: string;
  key: string;
  name: LocalizedText[];
  inverseName: LocalizedText[];
  directed: boolean;
  sourceCardinality: "one" | "many";
  targetCardinality: "one" | "many";
  evidenceRequired: boolean;
  schemaVersion: string;
}

interface ViewDefinition {
  id: string;
  entityTypeId: string;
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

interface DraftIssue {
  severity: "error" | "warning";
  code: string;
  path: string;
  message: string;
}

const apiUrl = "/api/backend";

function label(text: LocalizedText[]) {
  return text.find((item) => item.locale === "zh-CN")?.value ?? text[0]?.value;
}

function parseAttributeValue(
  definition: AttributeDefinition,
  raw: string,
): unknown {
  if (definition.cardinality === "many") {
    return raw
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
  }
  if (definition.dataType === "integer") return Number.parseInt(raw, 10);
  if (definition.dataType === "decimal") return Number.parseFloat(raw);
  if (definition.dataType === "boolean") return raw === "true";
  if (definition.dataType === "measurement") {
    const [value, unit] = raw.trim().split(/\s+/, 2);
    return { value: Number(value), unit: unit ?? "" };
  }
  return raw;
}

export function SchemaAuthoringStudio() {
  const [registry, setRegistry] = useState<Registry>();
  const [taxonomy, setTaxonomy] = useState<TaxonomyNode[]>([]);
  const [typeId, setTypeId] = useState("");
  const [issues, setIssues] = useState<DraftIssue[]>([]);
  const [notice, setNotice] = useState("正在加载 Schema Registry…");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void Promise.all([
      adminFetch(`${apiUrl}/api/v1/schema-registry`, { cache: "no-store" }),
      adminFetch(`${apiUrl}/api/v1/taxonomy`, { cache: "no-store" }),
    ])
      .then(async ([registryResponse, taxonomyResponse]) => {
        if (!registryResponse.ok || !taxonomyResponse.ok) {
          throw new Error("Schema API 返回异常");
        }
        const nextRegistry = (await registryResponse.json()) as Registry;
        setRegistry(nextRegistry);
        setTaxonomy((await taxonomyResponse.json()) as TaxonomyNode[]);
        setTypeId(nextRegistry.entityTypes[0]?.id ?? "");
        setNotice("Schema Registry 已同步");
      })
      .catch((error: unknown) =>
        setNotice(error instanceof Error ? error.message : "Schema API 不可用"),
      );
  }, []);

  const entityType = registry?.entityTypes.find((item) => item.id === typeId);
  const attributes = useMemo(
    () =>
      entityType?.attributeDefinitionIds
        .map((id) =>
          registry?.attributeDefinitions.find((item) => item.id === id),
        )
        .filter((item): item is AttributeDefinition => Boolean(item)) ?? [],
    [entityType, registry],
  );
  const view = registry?.viewDefinitions.find(
    (item) => item.id === entityType?.defaultViewDefinitionId,
  );
  const relationshipTypes =
    entityType?.allowedRelationshipTypeIds
      .map((id) => registry?.relationshipTypes.find((item) => item.id === id))
      .filter((item): item is RelationshipType => Boolean(item)) ?? [];
  const compatibleTaxonomy = taxonomy.filter(
    (node) =>
      node.spaceId === entityType?.spaceId &&
      (!entityType.allowedTaxonomyNodeIds.length ||
        entityType.allowedTaxonomyNodeIds.includes(node.id)),
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!entityType) return;
    const form = new FormData(event.currentTarget);
    const slug = String(form.get("slug") ?? "");
    const citationId = `cite-${slug}-001`;
    const attributeValues = Object.fromEntries(
      attributes
        .map((definition) => {
          const raw = String(form.get(`attribute:${definition.id}`) ?? "");
          return raw
            ? [definition.id, parseAttributeValue(definition, raw)]
            : null;
        })
        .filter((item): item is [string, unknown] => item !== null),
    );
    const draft = {
      id: String(form.get("entityId")),
      slug,
      typeId: entityType.id,
      names: [{ locale: "zh-CN", value: String(form.get("name")) }],
      aliases: String(form.get("aliases") ?? "")
        .split("\n")
        .map((value) => value.trim())
        .filter(Boolean)
        .map((value) => ({ locale: "zh-CN", value })),
      description: [
        { locale: "zh-CN", value: String(form.get("description")) },
      ],
      taxonomyNodeIds: [String(form.get("taxonomyNodeId"))],
      attributeValues,
      sections: [
        {
          key: "overview",
          heading: [{ locale: "zh-CN", value: "概述" }],
          body: [{ locale: "zh-CN", value: String(form.get("overview")) }],
          citationIds: [citationId],
        },
      ],
      citations: [
        {
          id: citationId,
          sourceId: String(form.get("sourceId")),
          sourceTitle: String(form.get("sourceTitle")),
          sourceUrl: String(form.get("sourceUrl")) || undefined,
          sourceTier: form.get("sourceTier"),
          retrievedAt: new Date().toISOString(),
        },
      ],
    };

    setBusy(true);
    setNotice("正在按动态 Schema 校验草稿…");
    try {
      const validationResponse = await adminFetch(
        `${apiUrl}/api/v1/entity-drafts/validate`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(draft),
        },
      );
      const validation = (await validationResponse.json()) as {
        valid: boolean;
        issues: DraftIssue[];
      };
      if (!validationResponse.ok) throw new Error("草稿校验请求失败");
      setIssues(validation.issues);
      if (!validation.valid) {
        setNotice("草稿未通过校验，尚未创建提案");
        return;
      }

      const proposalResponse = await adminFetch(
        `${apiUrl}/api/v1/entity-drafts/proposals`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            proposalId: `proposal-create-${slug}`,
            agentRunId: `authoring-studio-${Date.now()}`,
            confidence: 0.95,
            risk: "medium",
            draft,
          }),
        },
      );
      const proposal = (await proposalResponse.json()) as {
        proposal?: { id: string };
        detail?: string;
      };
      if (!proposalResponse.ok) {
        throw new Error(
          typeof proposal.detail === "string"
            ? proposal.detail
            : "创建提案失败",
        );
      }
      setNotice(`已创建受治理提案 ${proposal.proposal?.id}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="schema-studio">
      <header className="schema-studio-header">
        <div>
          <a href="/">← 返回运行中心</a>
          <p>DYNAMIC KNOWLEDGE AUTHORING</p>
          <h1>Schema 与条目工作室</h1>
          <span>类型定义自动生成字段、校验规则和公开页面结构</span>
        </div>
        <div className="schema-header-actions">
          <small>{notice}</small>
          <a href="/schema/domain-packs">Domain Pack 构造器 →</a>
          <a href="/schema/migrations">Schema 影响分析 →</a>
        </div>
      </header>

      <div className="schema-studio-layout">
        <aside className="schema-catalog">
          <p>ENTITY TYPES</p>
          <h2>实体类型</h2>
          {registry?.entityTypes.map((item) => (
            <button
              data-active={item.id === typeId}
              key={item.id}
              onClick={() => {
                setTypeId(item.id);
                setIssues([]);
              }}
              type="button"
            >
              <strong>{label(item.name)}</strong>
              <span>{item.key}</span>
              <small>{item.schemaVersion}</small>
            </button>
          ))}
        </aside>

        <form className="schema-entry-form" onSubmit={submit}>
          <div className="schema-section-title">
            <div>
              <p>GENERATED FORM</p>
              <h2>
                {entityType ? `${label(entityType.name)}条目草稿` : "加载中"}
              </h2>
            </div>
            <code>{entityType?.id}</code>
          </div>
          <div className="schema-form-grid">
            <label>
              实体 ID
              <input defaultValue="entity-new-entry" name="entityId" required />
            </label>
            <label>
              URL slug
              <input defaultValue="new-entry" name="slug" required />
            </label>
            <label>
              中文名称
              <input name="name" placeholder="规范名称" required />
            </label>
            <label>
              分类节点
              <select name="taxonomyNodeId">
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
            <textarea name="aliases" placeholder="别名、旧称、跨语言名称" />
          </label>
          <label>
            条目摘要
            <textarea name="description" required />
          </label>
          <fieldset className="dynamic-attributes">
            <legend>Schema 动态字段</legend>
            {attributes.map((definition) => (
              <label key={definition.id}>
                <span>
                  {label(definition.name)}
                  {definition.required ? <b>必填</b> : null}
                </span>
                {definition.enumValues ? (
                  <select
                    name={`attribute:${definition.id}`}
                    required={definition.required}
                  >
                    <option value="">请选择</option>
                    {definition.enumValues.map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    name={`attribute:${definition.id}`}
                    placeholder={
                      definition.dataType === "measurement"
                        ? "数值 单位，例如 5 V"
                        : definition.dataType
                    }
                    required={definition.required}
                  />
                )}
                <small>
                  {definition.id} · {definition.dataType} ·{" "}
                  {definition.cardinality}
                </small>
              </label>
            ))}
          </fieldset>
          <fieldset className="dynamic-relationships">
            <legend>允许的关系类型</legend>
            {relationshipTypes.length ? (
              relationshipTypes.map((relationshipType) => (
                <article key={relationshipType.id}>
                  <div>
                    <strong>{label(relationshipType.name)}</strong>
                    <span>反向：{label(relationshipType.inverseName)}</span>
                  </div>
                  <code>{relationshipType.key}</code>
                  <small>
                    {relationshipType.directed ? "有向" : "无向"} ·{" "}
                    {relationshipType.sourceCardinality}:
                    {relationshipType.targetCardinality} ·{" "}
                    {relationshipType.evidenceRequired
                      ? "必须证据"
                      : "证据可选"}
                  </small>
                  <em>{relationshipType.schemaVersion}</em>
                </article>
              ))
            ) : (
              <small>该实体类型未开放关系边。</small>
            )}
          </fieldset>
          <label>
            概述正文
            <textarea name="overview" required />
          </label>
          <fieldset className="source-fields">
            <legend>来源证据</legend>
            <label>
              来源 ID
              <input
                defaultValue="source-authoring-001"
                name="sourceId"
                required
              />
            </label>
            <label>
              来源标题
              <input name="sourceTitle" required />
            </label>
            <label>
              来源 URL
              <input name="sourceUrl" type="url" />
            </label>
            <label>
              来源等级
              <select defaultValue="authoritative" name="sourceTier">
                <option value="primary">原始来源</option>
                <option value="authoritative">权威来源</option>
                <option value="secondary">二手来源</option>
                <option value="community">社区来源</option>
              </select>
            </label>
          </fieldset>
          <button disabled={busy || !entityType} type="submit">
            校验并创建提案
          </button>
        </form>

        <aside className="schema-preview">
          <p>RENDER CONTRACT</p>
          <h2>页面结构预览</h2>
          <span>{entityType && label(entityType.description)}</span>
          <div className="view-blocks">
            {view?.blocks.map((block, index) => (
              <article key={block.id}>
                <b>{index + 1}</b>
                <div>
                  <strong>{block.type}</strong>
                  <small>{block.id}</small>
                </div>
              </article>
            ))}
          </div>
          <div className="schema-issues">
            <h3>校验结果</h3>
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
              <small>
                提交时将在服务端执行类型、必填、枚举、分类与证据校验。
              </small>
            )}
          </div>
        </aside>
      </div>
    </main>
  );
}
