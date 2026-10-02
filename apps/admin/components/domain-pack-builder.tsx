"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

type PackStatus = "draft" | "published" | "archived" | "rolled-back";
type AttributeDataType =
  | "text"
  | "rich-text"
  | "integer"
  | "decimal"
  | "boolean"
  | "date"
  | "date-range"
  | "measurement"
  | "enum"
  | "entity-ref"
  | "media-ref"
  | "geo"
  | "json";

interface AttributeRow {
  key: string;
  name: string;
  dataType: AttributeDataType;
  required: boolean;
}

interface RelationshipRow {
  key: string;
  name: string;
  inverseName: string;
  directed: boolean;
  targetEntityTypeIds: string[];
  sourceCardinality: "one" | "many";
  targetCardinality: "one" | "many";
  evidenceRequired: boolean;
  qualifierKey: string;
  qualifierType: "string" | "number" | "integer" | "boolean";
}

interface LocalizedText {
  locale: string;
  value: string;
}

interface RegistryEntityType {
  id: string;
  spaceId: string;
  key: string;
  name: LocalizedText[];
  schemaVersion: string;
}

interface SchemaRegistry {
  entityTypes: RegistryEntityType[];
}

interface DomainPack {
  id: string;
  name: string;
  version: string;
  status: PackStatus;
  proposalId: string;
  createdBy: string;
  createdAt: string;
  publishedAt?: string;
  rolledBackAt?: string;
  rollbackToVersion?: string;
  spaces: unknown[];
  taxonomyNodes: unknown[];
  entityTypes: unknown[];
  attributes: unknown[];
  relationshipTypes: unknown[];
  views: unknown[];
  qualityProfiles: unknown[];
}

interface DomainPackDiffEntry {
  collection:
    | "spaces"
    | "taxonomyNodes"
    | "entityTypes"
    | "attributes"
    | "relationshipTypes"
    | "views"
    | "qualityProfiles";
  documentId: string;
  change: "added" | "removed" | "modified";
}

interface DomainPackDiff {
  packId: string;
  fromVersion?: string;
  toVersion: string;
  entries: DomainPackDiffEntry[];
  counts: Record<"added" | "removed" | "modified" | "unchanged", number>;
}

interface DomainPackRollbackAnalysis {
  packId: string;
  version: string;
  restoreVersion?: string;
  safe: boolean;
  blockers: Array<{
    code: string;
    resourceId: string;
    dependentIds: string[];
    message: string;
  }>;
  manifestDiff: DomainPackDiff;
}

interface ValidationIssue {
  severity: "error" | "warning";
  code: string;
  path: string;
  message: string;
}

interface Validation {
  valid: boolean;
  issues: ValidationIssue[];
  counts: Record<string, number>;
}

interface GovernedProposal {
  proposal: {
    id: string;
    status: string;
  };
}

const apiUrl = "/apps/hardatlas-admin/api/backend";
const currentTypeTarget = "$current-pack-type";

function keyOf(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function displayName(values: LocalizedText[], fallback: string) {
  return (
    values.find((item) => item.locale === "zh-CN")?.value ??
    values[0]?.value ??
    fallback
  );
}

export function DomainPackBuilder() {
  const [packId, setPackId] = useState("astronomy");
  const [packName, setPackName] = useState("天文学扩展包");
  const [version, setVersion] = useState("1.0.0");
  const [citationIds, setCitationIds] = useState(
    "citation-domain-pack-ontology",
  );
  const [spaceKey, setSpaceKey] = useState("astronomy");
  const [spaceName, setSpaceName] = useState("天文与空间");
  const [spaceDescription, setSpaceDescription] =
    useState("天体、观测与空间科学");
  const [categoryKey, setCategoryKey] = useState("celestial-objects");
  const [categoryName, setCategoryName] = useState("天体");
  const [typeKey, setTypeKey] = useState("celestial-object");
  const [typeName, setTypeName] = useState("天体");
  const [typeDescription, setTypeDescription] =
    useState("可独立描述的天文对象");
  const [attributes, setAttributes] = useState<AttributeRow[]>([
    {
      key: "discovery-year",
      name: "发现年份",
      dataType: "integer",
      required: false,
    },
  ]);
  const [relationships, setRelationships] = useState<RelationshipRow[]>([
    {
      key: "orbits",
      name: "轨道环绕",
      inverseName: "被环绕",
      directed: true,
      targetEntityTypeIds: [currentTypeTarget],
      sourceCardinality: "many",
      targetCardinality: "one",
      evidenceRequired: true,
      qualifierKey: "orbit-class",
      qualifierType: "string",
    },
  ]);
  const [packs, setPacks] = useState<DomainPack[]>([]);
  const [registryTypes, setRegistryTypes] = useState<RegistryEntityType[]>([]);
  const [proposalStatuses, setProposalStatuses] = useState<
    Record<string, string>
  >({});
  const [validation, setValidation] = useState<Validation>();
  const [inspectedPack, setInspectedPack] = useState<DomainPack>();
  const [versionDiff, setVersionDiff] = useState<DomainPackDiff>();
  const [rollbackAnalysis, setRollbackAnalysis] =
    useState<DomainPackRollbackAnalysis>();
  const [rollbackComment, setRollbackComment] = useState("");
  const [notice, setNotice] = useState("正在加载 Domain Pack 注册表…");
  const [busy, setBusy] = useState(false);

  const normalized = useMemo(
    () => ({
      pack: keyOf(packId),
      space: keyOf(spaceKey),
      category: keyOf(categoryKey),
      type: keyOf(typeKey),
    }),
    [categoryKey, packId, spaceKey, typeKey],
  );

  const payload = useMemo(() => {
    const schemaVersion = `${normalized.pack}-${version}`;
    const spaceId = `space-${normalized.space}`;
    const taxonomyId = `tax-${normalized.category}`;
    const entityTypeId = `type-${normalized.type}`;
    const viewId = `view-${normalized.type}`;
    const attributeDefinitions = attributes
      .filter((item) => keyOf(item.key) && item.name.trim())
      .map((item) => ({
        id: `attr-${normalized.pack}-${keyOf(item.key)}`,
        key: keyOf(item.key),
        name: [{ locale: "zh-CN", value: item.name.trim() }],
        dataType: item.dataType,
        cardinality: "one",
        required: item.required,
        schemaVersion,
      }));
    const relationshipDefinitions = relationships
      .filter((item) => keyOf(item.key) && item.name.trim())
      .map((item) => {
        const selectedTargets = item.targetEntityTypeIds.length
          ? item.targetEntityTypeIds
          : [currentTypeTarget];
        return {
          id: `rel-${normalized.pack}-${keyOf(item.key)}`,
          key: keyOf(item.key),
          name: [{ locale: "zh-CN", value: item.name.trim() }],
          inverseName: [
            {
              locale: "zh-CN",
              value: item.inverseName.trim() || `反向${item.name.trim()}`,
            },
          ],
          directed: item.directed,
          sourceEntityTypeIds: [entityTypeId],
          targetEntityTypeIds: Array.from(
            new Set(
              selectedTargets.map((targetId) =>
                targetId === currentTypeTarget ? entityTypeId : targetId,
              ),
            ),
          ),
          sourceCardinality: item.sourceCardinality,
          targetCardinality: item.targetCardinality,
          qualifierSchema: keyOf(item.qualifierKey)
            ? {
                [keyOf(item.qualifierKey)]: {
                  type: item.qualifierType,
                },
              }
            : {},
          evidenceRequired: item.evidenceRequired,
          schemaVersion,
        };
      });
    return {
      id: normalized.pack,
      name: packName.trim(),
      version: version.trim(),
      kernelVersion: "2.0",
      locales: ["zh-CN", "en"],
      citationIds: citationIds
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      spaces: [
        {
          id: spaceId,
          slug: normalized.space,
          name: [{ locale: "zh-CN", value: spaceName.trim() }],
          description: [{ locale: "zh-CN", value: spaceDescription.trim() }],
          rootTaxonomyNodeIds: [taxonomyId],
          iconKey: "orbit",
          status: "published",
        },
      ],
      taxonomyNodes: [
        {
          id: taxonomyId,
          spaceId,
          slug: normalized.category,
          name: [{ locale: "zh-CN", value: categoryName.trim() }],
          parentIds: [],
          childCount: 0,
          entityCount: 0,
          pathKeys: [normalized.space, normalized.category],
        },
      ],
      entityTypes: [
        {
          id: entityTypeId,
          spaceId,
          key: normalized.type,
          name: [{ locale: "zh-CN", value: typeName.trim() }],
          description: [{ locale: "zh-CN", value: typeDescription.trim() }],
          allowedTaxonomyNodeIds: [taxonomyId],
          attributeDefinitionIds: attributeDefinitions.map((item) => item.id),
          allowedRelationshipTypeIds: relationshipDefinitions.map(
            (item) => item.id,
          ),
          defaultViewDefinitionId: viewId,
          schemaVersion,
        },
      ],
      attributes: attributeDefinitions,
      relationshipTypes: relationshipDefinitions,
      views: [
        {
          id: viewId,
          entityTypeId,
          schemaVersion,
          blocks: [
            { id: "hero", type: "hero", config: { showAliases: true } },
            { id: "summary", type: "summary", config: {} },
            { id: "classification", type: "classification", config: {} },
            { id: "attributes", type: "attribute-table", config: {} },
            { id: "relations", type: "relationship-list", config: {} },
            { id: "citations", type: "citations", config: {} },
          ],
        },
      ],
      qualityProfiles: [
        {
          id: `quality-${normalized.type}`,
          entityTypeId,
          schemaVersion,
          requiredLocales: ["zh-CN"],
          requiredAttributeIds: attributeDefinitions
            .filter((item) => item.required)
            .map((item) => item.id),
          minimumCitations: 2,
          minimumSections: 2,
          freshnessDays: 365,
          healthyScore: 90,
          criticalScore: 50,
        },
      ],
    };
  }, [
    attributes,
    categoryName,
    citationIds,
    normalized,
    packName,
    relationships,
    spaceDescription,
    spaceName,
    typeDescription,
    typeName,
    version,
  ]);

  const refresh = useCallback(async () => {
    const [packResponse, proposalResponse, registryResponse] =
      await Promise.all([
        adminFetch(`${apiUrl}/api/v1/domain-packs`, { cache: "no-store" }),
        adminFetch(`${apiUrl}/api/v1/proposals`, { cache: "no-store" }),
        adminFetch(`${apiUrl}/api/v1/schema-registry`, { cache: "no-store" }),
      ]);
    if (!packResponse.ok || !proposalResponse.ok || !registryResponse.ok) {
      throw new Error("Domain Pack API 返回异常");
    }
    setPacks((await packResponse.json()) as DomainPack[]);
    const registry = (await registryResponse.json()) as SchemaRegistry;
    setRegistryTypes(
      registry.entityTypes
        .slice()
        .sort((left, right) =>
          displayName(left.name, left.id).localeCompare(
            displayName(right.name, right.id),
            "zh-CN",
          ),
        ),
    );
    const proposals = (await proposalResponse.json()) as GovernedProposal[];
    setProposalStatuses(
      Object.fromEntries(
        proposals.map((item) => [item.proposal.id, item.proposal.status]),
      ),
    );
  }, []);

  useEffect(() => {
    void refresh()
      .then(() => setNotice("Domain Pack 注册表已同步"))
      .catch((error: unknown) =>
        setNotice(error instanceof Error ? error.message : "注册表不可用"),
      );
  }, [refresh]);

  async function validate() {
    setBusy(true);
    setNotice("正在检查引用、冲突与分类环…");
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/domain-packs/validate`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      const result = (await response.json()) as Validation;
      if (!response.ok) throw new Error("Domain Pack 校验请求失败");
      setValidation(result);
      setNotice(
        result.valid ? "扩展包校验通过，可以保存草稿" : "扩展包存在阻断项",
      );
      return result;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "校验失败");
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const result = await validate();
    if (!result?.valid) return;
    setBusy(true);
    setNotice("正在保存不可变版本草稿…");
    try {
      const response = await adminFetch(`${apiUrl}/api/v1/domain-packs`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = (await response.json()) as DomainPack & {
        detail?: string | { message?: string };
      };
      if (!response.ok) {
        throw new Error(
          typeof body.detail === "string"
            ? body.detail
            : (body.detail?.message ?? "保存草稿失败"),
        );
      }
      await refresh();
      setNotice(`已保存 ${body.id}@${body.version} 草稿`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function publish(pack: DomainPack) {
    setBusy(true);
    setNotice(`正在原子发布 ${pack.id}@${pack.version}…`);
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/domain-packs/${encodeURIComponent(pack.id)}/${encodeURIComponent(pack.version)}/publish`,
        { method: "POST" },
      );
      const body = (await response.json()) as DomainPack & { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "发布失败");
      await refresh();
      setNotice("发布完成：空间、分类、Schema 与 Outbox 已在同一事务提交");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "发布失败");
    } finally {
      setBusy(false);
    }
  }

  async function inspectDiff(pack: DomainPack) {
    setBusy(true);
    setNotice(`正在比较 ${pack.id}@${pack.version} 与前一版本…`);
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/domain-packs/${encodeURIComponent(pack.id)}/${encodeURIComponent(pack.version)}/diff`,
        { cache: "no-store" },
      );
      const body = (await response.json()) as DomainPackDiff & {
        detail?: string;
      };
      if (!response.ok) throw new Error(body.detail ?? "版本差异读取失败");
      setInspectedPack(pack);
      setVersionDiff(body);
      setRollbackAnalysis(undefined);
      setNotice(`已生成 ${pack.id}@${pack.version} 的确定性清单差异`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "版本差异读取失败");
    } finally {
      setBusy(false);
    }
  }

  async function inspectRollback(pack: DomainPack) {
    setBusy(true);
    setNotice(`正在分析 ${pack.id}@${pack.version} 的运行时依赖…`);
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/domain-packs/${encodeURIComponent(pack.id)}/${encodeURIComponent(pack.version)}/rollback-analysis`,
        { cache: "no-store" },
      );
      const body = (await response.json()) as DomainPackRollbackAnalysis & {
        detail?: string;
      };
      if (!response.ok) throw new Error(body.detail ?? "回滚分析读取失败");
      setInspectedPack(pack);
      setVersionDiff(body.manifestDiff);
      setRollbackAnalysis(body);
      setRollbackComment("");
      setNotice(
        body.safe
          ? "回滚分析通过：没有活动内容或 Schema 依赖"
          : `回滚被 ${body.blockers.length} 个运行时依赖阻断`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "回滚分析读取失败");
    } finally {
      setBusy(false);
    }
  }

  async function rollback() {
    if (
      !inspectedPack?.publishedAt ||
      !rollbackAnalysis?.safe ||
      !rollbackComment.trim()
    ) {
      return;
    }
    setBusy(true);
    setNotice(
      `正在按发布前快照回滚 ${inspectedPack.id}@${inspectedPack.version}…`,
    );
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/domain-packs/${encodeURIComponent(inspectedPack.id)}/${encodeURIComponent(inspectedPack.version)}/rollback`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            expectedPublishedAt: inspectedPack.publishedAt,
            comment: rollbackComment.trim(),
          }),
        },
      );
      const body = (await response.json()) as {
        rolledBack?: DomainPack;
        restored?: DomainPack;
        detail?: string | { code?: string };
      };
      if (!response.ok) {
        throw new Error(
          typeof body.detail === "string"
            ? body.detail
            : (body.detail?.code ?? "Domain Pack 回滚失败"),
        );
      }
      await refresh();
      setInspectedPack(body.rolledBack);
      setRollbackAnalysis(undefined);
      setRollbackComment("");
      setNotice(
        body.restored
          ? `回滚完成，已恢复 ${body.restored.id}@${body.restored.version}`
          : "回滚完成，已恢复发布前的 Schema、分类与空间快照",
      );
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Domain Pack 回滚失败",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="domain-pack-page">
      <header className="domain-pack-header">
        <div>
          <a href="/schema">← 返回 Schema 中心</a>
          <p>DYNAMIC DOMAIN CONSTRUCTOR</p>
          <h1>Domain Pack 构造器</h1>
          <span>
            声明知识空间、分类、实体类型、属性与页面结构，无需修改应用代码
          </span>
        </div>
        <small>{notice}</small>
      </header>

      <div className="domain-pack-layout">
        <form className="domain-pack-form" onSubmit={save}>
          <div className="domain-pack-title">
            <div>
              <p>PACK DEFINITION</p>
              <h2>创建领域扩展包</h2>
            </div>
            <code>
              {normalized.pack || "invalid"}@{version}
            </code>
          </div>

          <fieldset>
            <legend>扩展包身份</legend>
            <label>
              Pack ID
              <input
                value={packId}
                onChange={(event) => setPackId(event.target.value)}
                required
              />
            </label>
            <label>
              显示名称
              <input
                value={packName}
                onChange={(event) => setPackName(event.target.value)}
                required
              />
            </label>
            <label>
              不可变版本
              <input
                value={version}
                onChange={(event) => setVersion(event.target.value)}
                required
              />
            </label>
            <label className="wide">
              治理依据 ID（逗号分隔）
              <input
                value={citationIds}
                onChange={(event) => setCitationIds(event.target.value)}
                required
              />
            </label>
          </fieldset>

          <fieldset>
            <legend>知识空间与根分类</legend>
            <label>
              空间 key
              <input
                value={spaceKey}
                onChange={(event) => setSpaceKey(event.target.value)}
                required
              />
            </label>
            <label>
              空间名称
              <input
                value={spaceName}
                onChange={(event) => setSpaceName(event.target.value)}
                required
              />
            </label>
            <label className="wide">
              空间说明
              <input
                value={spaceDescription}
                onChange={(event) => setSpaceDescription(event.target.value)}
                required
              />
            </label>
            <label>
              根分类 key
              <input
                value={categoryKey}
                onChange={(event) => setCategoryKey(event.target.value)}
                required
              />
            </label>
            <label>
              根分类名称
              <input
                value={categoryName}
                onChange={(event) => setCategoryName(event.target.value)}
                required
              />
            </label>
          </fieldset>

          <fieldset>
            <legend>实体类型</legend>
            <label>
              类型 key
              <input
                value={typeKey}
                onChange={(event) => setTypeKey(event.target.value)}
                required
              />
            </label>
            <label>
              类型名称
              <input
                value={typeName}
                onChange={(event) => setTypeName(event.target.value)}
                required
              />
            </label>
            <label className="wide">
              类型说明
              <input
                value={typeDescription}
                onChange={(event) => setTypeDescription(event.target.value)}
                required
              />
            </label>
          </fieldset>

          <fieldset className="attribute-builder">
            <legend>动态属性</legend>
            {attributes.map((attribute, index) => (
              <div key={`${index}-${attribute.key}`}>
                <input
                  aria-label={`属性 ${index + 1} key`}
                  value={attribute.key}
                  onChange={(event) =>
                    setAttributes((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, key: event.target.value }
                          : item,
                      ),
                    )
                  }
                />
                <input
                  aria-label={`属性 ${index + 1} 名称`}
                  value={attribute.name}
                  onChange={(event) =>
                    setAttributes((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, name: event.target.value }
                          : item,
                      ),
                    )
                  }
                />
                <select
                  aria-label={`属性 ${index + 1} 类型`}
                  value={attribute.dataType}
                  onChange={(event) =>
                    setAttributes((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? {
                              ...item,
                              dataType: event.target.value as AttributeDataType,
                            }
                          : item,
                      ),
                    )
                  }
                >
                  <option value="text">文本</option>
                  <option value="integer">整数</option>
                  <option value="decimal">小数</option>
                  <option value="boolean">布尔</option>
                  <option value="date">日期</option>
                  <option value="measurement">测量值</option>
                  <option value="enum">枚举</option>
                  <option value="entity-ref">实体引用</option>
                  <option value="geo">地理位置</option>
                  <option value="json">结构化 JSON</option>
                </select>
                <label>
                  <input
                    checked={attribute.required}
                    onChange={(event) =>
                      setAttributes((current) =>
                        current.map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, required: event.target.checked }
                            : item,
                        ),
                      )
                    }
                    type="checkbox"
                  />
                  必填
                </label>
                <button
                  disabled={attributes.length === 1}
                  onClick={() =>
                    setAttributes((current) =>
                      current.filter((_, itemIndex) => itemIndex !== index),
                    )
                  }
                  type="button"
                >
                  移除
                </button>
              </div>
            ))}
            <button
              className="secondary"
              onClick={() =>
                setAttributes((current) => [
                  ...current,
                  {
                    key: "new-field",
                    name: "新字段",
                    dataType: "text",
                    required: false,
                  },
                ])
              }
              type="button"
            >
              + 添加属性
            </button>
          </fieldset>

          <fieldset className="relationship-builder">
            <legend>动态关系类型</legend>
            {relationships.map((relationship, index) => (
              <div key={`${index}-${relationship.key}`}>
                <input
                  aria-label={`关系 ${index + 1} key`}
                  onChange={(event) =>
                    setRelationships((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, key: event.target.value }
                          : item,
                      ),
                    )
                  }
                  value={relationship.key}
                />
                <input
                  aria-label={`关系 ${index + 1} 名称`}
                  onChange={(event) =>
                    setRelationships((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, name: event.target.value }
                          : item,
                      ),
                    )
                  }
                  value={relationship.name}
                />
                <input
                  aria-label={`关系 ${index + 1} 反向名称`}
                  onChange={(event) =>
                    setRelationships((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, inverseName: event.target.value }
                          : item,
                      ),
                    )
                  }
                  value={relationship.inverseName}
                />
                <label className="relationship-target-types">
                  目标实体类型（可多选）
                  <select
                    aria-label={`关系 ${index + 1} 目标实体类型`}
                    multiple
                    onChange={(event) => {
                      const selected = Array.from(
                        event.target.selectedOptions,
                        (option) => option.value,
                      );
                      setRelationships((current) =>
                        current.map((item, itemIndex) =>
                          itemIndex === index
                            ? {
                                ...item,
                                targetEntityTypeIds: selected,
                              }
                            : item,
                        ),
                      );
                    }}
                    size={4}
                    value={relationship.targetEntityTypeIds}
                  >
                    <option value={currentTypeTarget}>
                      当前包 · {typeName || normalized.type || "新实体类型"}
                    </option>
                    <optgroup label="已发布 Schema Registry">
                      {registryTypes.map((entityType) => (
                        <option key={entityType.id} value={entityType.id}>
                          {displayName(entityType.name, entityType.id)} ·{" "}
                          {entityType.id}
                        </option>
                      ))}
                    </optgroup>
                  </select>
                  <small>⌘/Ctrl 可多选；关系源固定为当前包的新实体类型</small>
                </label>
                <select
                  aria-label={`关系 ${index + 1} 源基数`}
                  onChange={(event) =>
                    setRelationships((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? {
                              ...item,
                              sourceCardinality: event.target.value as
                                "one" | "many",
                            }
                          : item,
                      ),
                    )
                  }
                  value={relationship.sourceCardinality}
                >
                  <option value="one">源端唯一</option>
                  <option value="many">源端多个</option>
                </select>
                <select
                  aria-label={`关系 ${index + 1} 目标基数`}
                  onChange={(event) =>
                    setRelationships((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? {
                              ...item,
                              targetCardinality: event.target.value as
                                "one" | "many",
                            }
                          : item,
                      ),
                    )
                  }
                  value={relationship.targetCardinality}
                >
                  <option value="one">目标端唯一</option>
                  <option value="many">目标端多个</option>
                </select>
                <input
                  aria-label={`关系 ${index + 1} 限定字段`}
                  onChange={(event) =>
                    setRelationships((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, qualifierKey: event.target.value }
                          : item,
                      ),
                    )
                  }
                  placeholder="限定字段 key（可空）"
                  value={relationship.qualifierKey}
                />
                <select
                  aria-label={`关系 ${index + 1} 限定字段类型`}
                  onChange={(event) =>
                    setRelationships((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? {
                              ...item,
                              qualifierType: event.target
                                .value as RelationshipRow["qualifierType"],
                            }
                          : item,
                      ),
                    )
                  }
                  value={relationship.qualifierType}
                >
                  <option value="string">文本</option>
                  <option value="number">数值</option>
                  <option value="integer">整数</option>
                  <option value="boolean">布尔</option>
                </select>
                <label>
                  <input
                    checked={relationship.directed}
                    onChange={(event) =>
                      setRelationships((current) =>
                        current.map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, directed: event.target.checked }
                            : item,
                        ),
                      )
                    }
                    type="checkbox"
                  />
                  有向
                </label>
                <label>
                  <input
                    checked={relationship.evidenceRequired}
                    onChange={(event) =>
                      setRelationships((current) =>
                        current.map((item, itemIndex) =>
                          itemIndex === index
                            ? {
                                ...item,
                                evidenceRequired: event.target.checked,
                              }
                            : item,
                        ),
                      )
                    }
                    type="checkbox"
                  />
                  必须证据
                </label>
                <button
                  onClick={() =>
                    setRelationships((current) =>
                      current.filter((_, itemIndex) => itemIndex !== index),
                    )
                  }
                  type="button"
                >
                  移除
                </button>
              </div>
            ))}
            <button
              className="secondary"
              onClick={() =>
                setRelationships((current) => [
                  ...current,
                  {
                    key: "new-relation",
                    name: "新关系",
                    inverseName: "反向关系",
                    directed: true,
                    targetEntityTypeIds: [currentTypeTarget],
                    sourceCardinality: "many",
                    targetCardinality: "many",
                    evidenceRequired: true,
                    qualifierKey: "",
                    qualifierType: "string",
                  },
                ])
              }
              type="button"
            >
              + 添加关系类型
            </button>
          </fieldset>

          <div className="domain-pack-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void validate()}
              type="button"
            >
              仅做影响分析
            </button>
            <button disabled={busy} type="submit">
              保存版本草稿
            </button>
          </div>
        </form>

        <aside className="domain-pack-preview">
          <p>GENERATED MANIFEST</p>
          <h2>运行时扩展预览</h2>
          <dl>
            <div>
              <dt>知识空间</dt>
              <dd>{spaceName}</dd>
            </div>
            <div>
              <dt>根分类</dt>
              <dd>{categoryName}</dd>
            </div>
            <div>
              <dt>实体类型</dt>
              <dd>{typeName}</dd>
            </div>
            <div>
              <dt>动态属性</dt>
              <dd>{attributes.length}</dd>
            </div>
            <div>
              <dt>关系类型</dt>
              <dd>{relationships.length}</dd>
            </div>
            <div>
              <dt>默认视图块</dt>
              <dd>6</dd>
            </div>
            <div>
              <dt>Schema 版本</dt>
              <dd>
                {normalized.pack}-{version}
              </dd>
            </div>
          </dl>
          <pre aria-label="生成的 Domain Pack 清单" tabIndex={0}>
            {JSON.stringify(payload, null, 2)}
          </pre>
          <section className="domain-pack-validation">
            <h3>影响与引用检查</h3>
            {!validation && (
              <small>运行分析后显示分类环、未解析引用和 Schema 冲突。</small>
            )}
            {validation?.issues.length === 0 && (
              <b>✓ 所有引用闭合，可安全新增</b>
            )}
            {validation?.issues.map((item) => (
              <article
                data-severity={item.severity}
                key={`${item.code}-${item.path}`}
              >
                <strong>{item.code}</strong>
                <span>{item.message}</span>
                <code>{item.path}</code>
              </article>
            ))}
          </section>
        </aside>
      </div>

      <section className="domain-pack-registry">
        <div>
          <p>VERSION REGISTRY</p>
          <h2>已保存的扩展包版本</h2>
        </div>
        <div className="domain-pack-cards">
          {packs.length === 0 && <small>尚无运行时创建的 Domain Pack。</small>}
          {packs.map((pack) => (
            <article
              data-status={pack.status}
              key={`${pack.id}-${pack.version}`}
            >
              <header>
                <div>
                  <code>
                    {pack.id}@{pack.version}
                  </code>
                  <h3>{pack.name}</h3>
                </div>
                <b>{pack.status}</b>
              </header>
              <span>
                {pack.spaces.length} 空间 · {pack.taxonomyNodes.length} 分类 ·{" "}
                {pack.entityTypes.length} 类型 · {pack.attributes.length} 属性
                {" · "}
                {pack.relationshipTypes.length} 关系 ·{" "}
                {pack.qualityProfiles.length} 质量档案
              </span>
              <small>创建者 {pack.createdBy}</small>
              <a href={`/proposals/${encodeURIComponent(pack.proposalId)}`}>
                提案 {pack.proposalId} ·{" "}
                {proposalStatuses[pack.proposalId] ?? "同步中"}
              </a>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void inspectDiff(pack)}
                type="button"
              >
                查看版本差异
              </button>
              {pack.status === "draft" && (
                <button
                  disabled={
                    busy || proposalStatuses[pack.proposalId] !== "accepted"
                  }
                  onClick={() => void publish(pack)}
                >
                  {proposalStatuses[pack.proposalId] === "accepted"
                    ? "原子发布"
                    : "等待双人审核"}
                </button>
              )}
              {pack.status === "published" && (
                <>
                  <em>✓ 已进入公开 Schema Registry</em>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() => void inspectRollback(pack)}
                    type="button"
                  >
                    回滚影响分析
                  </button>
                </>
              )}
              {pack.status === "rolled-back" && (
                <em>
                  已回滚
                  {pack.rollbackToVersion
                    ? `，恢复到 ${pack.rollbackToVersion}`
                    : "，恢复到发布前快照"}
                </em>
              )}
            </article>
          ))}
        </div>
        {inspectedPack && versionDiff && (
          <section
            aria-labelledby="domain-pack-inspection-title"
            className="domain-pack-inspection"
          >
            <header>
              <div>
                <p>VERSION SAFETY INSPECTOR</p>
                <h3 id="domain-pack-inspection-title">
                  {inspectedPack.id}@{inspectedPack.version}
                </h3>
              </div>
              <button
                aria-label="关闭版本检查器"
                className="secondary"
                onClick={() => {
                  setInspectedPack(undefined);
                  setVersionDiff(undefined);
                  setRollbackAnalysis(undefined);
                }}
                type="button"
              >
                关闭
              </button>
            </header>
            <div className="domain-pack-diff-summary">
              <span>基线 {versionDiff.fromVersion ?? "空注册表"}</span>
              <b data-change="added">+{versionDiff.counts.added} 新增</b>
              <b data-change="modified">~{versionDiff.counts.modified} 修改</b>
              <b data-change="removed">−{versionDiff.counts.removed} 移除</b>
              <span>{versionDiff.counts.unchanged} 未变化</span>
            </div>
            <div className="domain-pack-diff-entries">
              {versionDiff.entries.length === 0 && (
                <small>该版本与比较基线没有清单差异。</small>
              )}
              {versionDiff.entries.map((entry) => (
                <code
                  data-change={entry.change}
                  key={`${entry.collection}-${entry.documentId}`}
                >
                  {entry.change === "added"
                    ? "+"
                    : entry.change === "removed"
                      ? "−"
                      : "~"}{" "}
                  {entry.collection}/{entry.documentId}
                </code>
              ))}
            </div>
            {rollbackAnalysis && (
              <div
                className="domain-pack-rollback"
                data-safe={rollbackAnalysis.safe}
              >
                <div>
                  <strong>
                    {rollbackAnalysis.safe
                      ? "✓ 可以安全回滚"
                      : "⚠ 当前禁止回滚"}
                  </strong>
                  <span>
                    {rollbackAnalysis.restoreVersion
                      ? `将恢复版本 ${rollbackAnalysis.restoreVersion}`
                      : "将精确恢复发布前快照"}
                  </span>
                </div>
                {rollbackAnalysis.blockers.map((blocker) => (
                  <article key={`${blocker.code}-${blocker.resourceId}`}>
                    <b>{blocker.code}</b>
                    <span>{blocker.message}</span>
                    <code>{blocker.resourceId}</code>
                    {blocker.dependentIds.length > 0 && (
                      <small>依赖：{blocker.dependentIds.join("、")}</small>
                    )}
                  </article>
                ))}
                {rollbackAnalysis.safe && (
                  <label>
                    回滚审计说明
                    <textarea
                      onChange={(event) =>
                        setRollbackComment(event.target.value)
                      }
                      placeholder="说明回滚原因、变更单或事故编号"
                      rows={3}
                      value={rollbackComment}
                    />
                  </label>
                )}
                <button
                  disabled={
                    busy || !rollbackAnalysis.safe || !rollbackComment.trim()
                  }
                  onClick={() => void rollback()}
                  type="button"
                >
                  执行安全回滚
                </button>
              </div>
            )}
          </section>
        )}
      </section>
    </main>
  );
}
