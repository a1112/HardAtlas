"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

type SchemaKind =
  | "attribute-definition"
  | "entity-type"
  | "relationship-type"
  | "view-definition";

interface SchemaDocument {
  id: string;
  schemaVersion: string;
  [key: string]: unknown;
}

interface Registry {
  entityTypes: SchemaDocument[];
  attributeDefinitions: SchemaDocument[];
  relationshipTypes: SchemaDocument[];
  viewDefinitions: SchemaDocument[];
}

interface Analysis {
  schemaKind: SchemaKind;
  schemaId: string;
  fromVersion: string;
  toVersion: string;
  classification: "additive" | "compatible" | "migratory" | "breaking";
  changes: Array<{
    field: string;
    severity: string;
    reason: string;
  }>;
  affectedEntityIds: string[];
  affectedRelationshipCount: number;
  requiresMigration: boolean;
  reversible: boolean;
  blockers: string[];
}

interface MigrationManifest {
  id: string;
  schemaId: string;
  fromVersion: string;
  toVersion: string;
  proposalId: string;
  status: "planned" | "applied" | "rolled-back" | "failed";
  frozenRevisionIds: Record<string, string>;
  appliedRevisionIds: Record<string, string>;
  error: string | null;
}

const collections: Record<SchemaKind, keyof Registry> = {
  "attribute-definition": "attributeDefinitions",
  "entity-type": "entityTypes",
  "relationship-type": "relationshipTypes",
  "view-definition": "viewDefinitions",
};

function nextVersion(document: SchemaDocument) {
  return {
    ...document,
    schemaVersion: `${document.schemaVersion}-candidate`,
  };
}

export function SchemaMigrationAnalyzer() {
  const [registry, setRegistry] = useState<Registry>();
  const [kind, setKind] = useState<SchemaKind>("attribute-definition");
  const [schemaId, setSchemaId] = useState("");
  const [document, setDocument] = useState("");
  const [analysis, setAnalysis] = useState<Analysis>();
  const [operations, setOperations] = useState("[]");
  const [migrations, setMigrations] = useState<MigrationManifest[]>([]);
  const [notice, setNotice] = useState("正在加载当前 Schema…");
  const [busy, setBusy] = useState(false);

  const documents = useMemo(
    () => registry?.[collections[kind]] ?? [],
    [kind, registry],
  );

  useEffect(() => {
    void Promise.all([
      adminFetch("/api/backend/api/v1/schema-registry", {
        cache: "no-store",
      }),
      adminFetch("/api/backend/api/v1/schema-migrations", {
        cache: "no-store",
      }),
    ])
      .then(async ([registryResponse, migrationsResponse]) => {
        if (!registryResponse.ok || !migrationsResponse.ok) {
          throw new Error("Schema Registry 返回异常");
        }
        const next = (await registryResponse.json()) as Registry;
        setRegistry(next);
        setMigrations((await migrationsResponse.json()) as MigrationManifest[]);
        const first = next.attributeDefinitions[0];
        setSchemaId(first?.id ?? "");
        setDocument(first ? JSON.stringify(nextVersion(first), null, 2) : "");
        setNotice("当前不可变 Schema 已载入");
      })
      .catch((error: unknown) =>
        setNotice(error instanceof Error ? error.message : "Schema API 不可用"),
      );
  }, []);

  function chooseDocument(nextKind: SchemaKind, nextId?: string) {
    const candidates = registry?.[collections[nextKind]] ?? [];
    const selected =
      candidates.find((item) => item.id === nextId) ?? candidates[0];
    setKind(nextKind);
    setSchemaId(selected?.id ?? "");
    setDocument(selected ? JSON.stringify(nextVersion(selected), null, 2) : "");
    setAnalysis(undefined);
    setOperations("[]");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setNotice("正在计算实体、关系与回滚影响…");
    try {
      const proposedDocument = JSON.parse(document) as Record<string, unknown>;
      const response = await adminFetch(
        "/api/backend/api/v1/schema-changes/analyze",
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            schemaKind: kind,
            schemaId,
            proposedDocument,
          }),
        },
      );
      const payload = (await response.json()) as Analysis | { detail?: string };
      if (!response.ok) {
        throw new Error(
          "detail" in payload && payload.detail
            ? payload.detail
            : "影响分析失败",
        );
      }
      setAnalysis(payload as Analysis);
      const parsed = proposedDocument as {
        dataType?: "text" | "integer" | "decimal" | "boolean";
      };
      if (
        kind === "attribute-definition" &&
        parsed.dataType &&
        (payload as Analysis).requiresMigration
      ) {
        setOperations(
          JSON.stringify(
            [
              {
                operation: "cast-claim",
                attributeId: schemaId,
                targetDataType: parsed.dataType,
              },
            ],
            null,
            2,
          ),
        );
      }
      setNotice("影响分析完成；尚未写入或迁移任何数据");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "候选 JSON 无效");
    } finally {
      setBusy(false);
    }
  }

  async function createMigration() {
    if (!analysis) return;
    setBusy(true);
    setNotice("正在冻结影响集并创建高风险 Schema 提案…");
    try {
      const suffix = Date.now();
      const response = await adminFetch(
        "/api/backend/api/v1/schema-migrations",
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            id: `schema-migration-${suffix}`,
            proposalId: `proposal-schema-migration-${suffix}`,
            schemaKind: kind,
            schemaId,
            proposedDocument: JSON.parse(document),
            operations: JSON.parse(operations),
            citationIds: [`schema-policy-${schemaId}`],
            confidence: 0.99,
            dataVersion: `atlas-schema-${suffix}`,
          }),
        },
      );
      const payload = (await response.json()) as
        MigrationManifest | { detail?: string };
      if (!response.ok) {
        throw new Error(
          "detail" in payload && payload.detail
            ? payload.detail
            : "迁移计划创建失败",
        );
      }
      setMigrations((current) => [payload as MigrationManifest, ...current]);
      setNotice("迁移影响集已冻结；必须完成双人审核后才能执行");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "迁移计划创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function transitionMigration(
    manifest: MigrationManifest,
    action: "apply" | "rollback",
  ) {
    setBusy(true);
    setNotice(action === "apply" ? "正在原子执行迁移…" : "正在反向回放…");
    try {
      const response = await adminFetch(
        `/api/backend/api/v1/schema-migrations/${manifest.id}/${action}`,
        { method: "POST" },
      );
      const payload = (await response.json()) as
        MigrationManifest | { detail?: string };
      if (!response.ok) {
        throw new Error(
          "detail" in payload && payload.detail
            ? payload.detail
            : "迁移状态切换失败",
        );
      }
      const updated = payload as MigrationManifest;
      setMigrations((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setNotice(
        action === "apply"
          ? "Schema 与实体修订已原子切换"
          : "Schema 激活版本与实体修订已恢复",
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "迁移状态切换失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="migration-analyzer">
      <header>
        <div>
          <a href="/schema">← 返回 Schema 中心</a>
          <p>SCHEMA IMPACT ANALYSIS</p>
          <h1>Schema 变更与迁移分析</h1>
          <span>先计算影响与可逆性，再允许创建高风险治理提案</span>
        </div>
        <small>{notice}</small>
      </header>
      <div className="migration-layout">
        <form onSubmit={submit}>
          <label>
            Schema 类型
            <select
              onChange={(event) =>
                chooseDocument(event.target.value as SchemaKind)
              }
              value={kind}
            >
              <option value="attribute-definition">字段定义</option>
              <option value="entity-type">实体类型</option>
              <option value="relationship-type">关系类型</option>
              <option value="view-definition">页面视图</option>
            </select>
          </label>
          <label>
            当前文档
            <select
              onChange={(event) => chooseDocument(kind, event.target.value)}
              value={schemaId}
            >
              {documents.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id} · {item.schemaVersion}
                </option>
              ))}
            </select>
          </label>
          <label>
            候选版本 JSON
            <textarea
              onChange={(event) => setDocument(event.target.value)}
              spellCheck={false}
              value={document}
            />
          </label>
          <button disabled={busy} type="submit">
            {busy ? "分析中…" : "运行影响分析"}
          </button>
        </form>
        <section className="migration-result">
          {analysis ? (
            <>
              <div className="migration-result-title">
                <div>
                  <p>ANALYSIS RESULT</p>
                  <h2>{analysis.schemaId}</h2>
                </div>
                <b data-level={analysis.classification}>
                  {analysis.classification}
                </b>
              </div>
              <div className="migration-metrics">
                <article>
                  <span>受影响实体</span>
                  <strong>{analysis.affectedEntityIds.length}</strong>
                </article>
                <article>
                  <span>受影响关系</span>
                  <strong>{analysis.affectedRelationshipCount}</strong>
                </article>
                <article>
                  <span>需要迁移</span>
                  <strong>{analysis.requiresMigration ? "是" : "否"}</strong>
                </article>
                <article>
                  <span>自动可逆</span>
                  <strong>{analysis.reversible ? "是" : "否"}</strong>
                </article>
              </div>
              <div className="migration-changes">
                {analysis.changes.map((change) => (
                  <article key={change.field}>
                    <code>{change.field}</code>
                    <b data-level={change.severity}>{change.severity}</b>
                    <span>{change.reason}</span>
                  </article>
                ))}
              </div>
              <label className="migration-operations">
                声明式迁移操作
                <textarea
                  onChange={(event) => setOperations(event.target.value)}
                  spellCheck={false}
                  value={operations}
                />
              </label>
              {analysis.blockers.map((blocker) => (
                <p className="migration-blocker" key={blocker}>
                  ! {blocker}
                </p>
              ))}
              <button
                disabled={busy}
                onClick={() => void createMigration()}
                type="button"
              >
                冻结影响集并创建高风险提案
              </button>
            </>
          ) : (
            <div className="migration-empty">
              <strong>尚未运行分析</strong>
              <span>
                修改候选版本号或约束后执行。该操作只读，不会注册 Schema
                或修改条目。
              </span>
            </div>
          )}
        </section>
      </div>
      <section className="migration-history">
        <div>
          <p>MIGRATION MANIFESTS</p>
          <h2>迁移清单与回放状态</h2>
        </div>
        {migrations.length ? (
          migrations.map((manifest) => (
            <article key={manifest.id}>
              <div>
                <code>{manifest.id}</code>
                <strong>{manifest.schemaId}</strong>
                <span>
                  {manifest.fromVersion} → {manifest.toVersion}
                </span>
              </div>
              <b data-state={manifest.status}>{manifest.status}</b>
              <small>
                冻结 {Object.keys(manifest.frozenRevisionIds).length} 个修订
              </small>
              <a href={`/proposals/${manifest.proposalId}`}>治理提案 →</a>
              {manifest.status === "planned" ? (
                <button
                  disabled={busy}
                  onClick={() => void transitionMigration(manifest, "apply")}
                  type="button"
                >
                  执行
                </button>
              ) : null}
              {manifest.status === "applied" ? (
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() => void transitionMigration(manifest, "rollback")}
                  type="button"
                >
                  回滚
                </button>
              ) : null}
            </article>
          ))
        ) : (
          <small>暂无迁移清单。</small>
        )}
      </section>
    </main>
  );
}
