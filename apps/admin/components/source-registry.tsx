"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

interface SourceRecord {
  source: {
    id: string;
    version: string;
    name: string;
    kind: string;
    baseUrl: string;
    allowedHosts: string[];
    trustTier: string;
    licenseId: string;
    licenseStatus: string;
    robotsPolicy: string;
    robotsStatus: string;
    modelProcessingPolicy: string;
    allowedMediaTypes: string[];
    parserId?: string;
    parserVersion?: string;
    maxBytes: number;
    locales: string[];
    entityTypeIds: string[];
    taxonomyNodeIds: string[];
    schedule?: string;
    status: string;
  };
  policy: {
    allowed: boolean;
    blockers: string[];
    warnings: string[];
  };
}

interface SourceActivity {
  acquisitions: Array<{
    id: string;
    status: string;
    snapshotId?: string;
    error?: string;
    createdAt: string;
  }>;
  snapshots: Array<{
    id: string;
    contentSha256: string;
    mediaType: string;
    byteSize: number;
    retrievedAt: string;
  }>;
  batches: Array<{
    id: string;
    parserId: string;
    parserVersion: string;
    status: string;
    candidateCount: number;
    createdAt: string;
  }>;
  candidates: Array<{
    id: string;
    entityId?: string;
    labels: Array<{ locale: string; value: string }>;
    status: string;
    scheduleIds: string[];
    resolutionMatches: Array<{
      entityId: string;
      canonicalName: string;
      matchBasis: string;
      score: number;
    }>;
  }>;
  pipeline: {
    sourceId: string;
    pendingEventCount: number;
    stalledCount: number;
    items: Array<{
      stage:
        | "acquisition"
        | "snapshot"
        | "extraction"
        | "candidate"
        | "agent-schedule";
      id: string;
      parentId?: string;
      status: string;
      replayable: boolean;
      error?: string;
      graphId?: string;
      updatedAt: string;
    }>;
  };
}

interface SourceParser {
  id: string;
  version: string;
  format: string;
  mediaTypes: string[];
  entityTypeId: string;
}

const apiUrl = "/apps/hardatlas-admin/api/backend";

export function SourceRegistry() {
  const [records, setRecords] = useState<SourceRecord[]>([]);
  const [parsers, setParsers] = useState<SourceParser[]>([]);
  const [notice, setNotice] = useState("正在同步来源策略…");
  const [busy, setBusy] = useState(false);
  const [activities, setActivities] = useState<Record<string, SourceActivity>>(
    {},
  );

  const refresh = useCallback(async () => {
    try {
      const [sourcesResponse, parsersResponse] = await Promise.all([
        adminFetch(`${apiUrl}/api/v1/sources`, {
          cache: "no-store",
        }),
        adminFetch(`${apiUrl}/api/v1/source-parsers`, {
          cache: "no-store",
        }),
      ]);
      if (!sourcesResponse.ok || !parsersResponse.ok) {
        throw new Error("来源 API 返回异常");
      }
      setRecords((await sourcesResponse.json()) as SourceRecord[]);
      setParsers((await parsersResponse.json()) as SourceParser[]);
      setNotice("来源与解析器注册表已同步");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "来源 API 不可用");
    }
  }, []);

  const loadActivity = useCallback(async (sourceId: string) => {
    try {
      const [
        acquisitionsResponse,
        snapshotsResponse,
        batchesResponse,
        pipelineResponse,
      ] = await Promise.all([
        adminFetch(`${apiUrl}/api/v1/sources/${sourceId}/acquisitions`, {
          cache: "no-store",
        }),
        adminFetch(`${apiUrl}/api/v1/sources/${sourceId}/snapshots`, {
          cache: "no-store",
        }),
        adminFetch(`${apiUrl}/api/v1/sources/${sourceId}/extractions`, {
          cache: "no-store",
        }),
        adminFetch(`${apiUrl}/api/v1/sources/${sourceId}/pipeline`, {
          cache: "no-store",
        }),
      ]);
      if (
        !acquisitionsResponse.ok ||
        !snapshotsResponse.ok ||
        !batchesResponse.ok ||
        !pipelineResponse.ok
      ) {
        throw new Error("来源活动 API 返回异常");
      }
      const acquisitions =
        (await acquisitionsResponse.json()) as SourceActivity["acquisitions"];
      const snapshots =
        (await snapshotsResponse.json()) as SourceActivity["snapshots"];
      const batches =
        (await batchesResponse.json()) as SourceActivity["batches"];
      const pipeline =
        (await pipelineResponse.json()) as SourceActivity["pipeline"];
      const candidateResponses = await Promise.all(
        batches
          .slice(0, 3)
          .map((batch) =>
            adminFetch(
              `${apiUrl}/api/v1/extractions/${batch.id}/candidates?limit=100`,
              { cache: "no-store" },
            ),
          ),
      );
      if (candidateResponses.some((response) => !response.ok)) {
        throw new Error("解析候选 API 返回异常");
      }
      const candidates = (
        await Promise.all(
          candidateResponses.map(
            async (response) =>
              (await response.json()) as SourceActivity["candidates"],
          ),
        )
      ).flat();
      const activity = {
        acquisitions,
        snapshots,
        batches,
        candidates,
        pipeline,
      };
      setActivities((current) => ({ ...current, [sourceId]: activity }));
      setNotice(
        `${sourceId}：${activity.snapshots.length} 个快照，${activity.batches.length} 个解析批次`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "读取来源活动失败");
    }
  }, []);

  const requestAcquisition = useCallback(
    async (sourceId: string) => {
      const timestamp = Date.now();
      setBusy(true);
      try {
        const response = await adminFetch(
          `${apiUrl}/api/v1/sources/${sourceId}/acquisitions`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              id: `acquisition-${sourceId}-${timestamp}`,
              idempotencyKey: `admin:${sourceId}:${timestamp}`,
            }),
          },
        );
        const payload = (await response.json()) as {
          id?: string;
          detail?: string;
        };
        if (!response.ok) {
          throw new Error(payload.detail ?? "请求采集失败");
        }
        setNotice(`采集任务 ${payload.id} 已进入可靠队列`);
        await loadActivity(sourceId);
      } catch (error) {
        setNotice(error instanceof Error ? error.message : "请求采集失败");
      } finally {
        setBusy(false);
      }
    },
    [loadActivity],
  );

  const replayPipelineItem = useCallback(
    async (
      sourceId: string,
      item: SourceActivity["pipeline"]["items"][number],
    ) => {
      const endpoint =
        item.stage === "acquisition"
          ? `/api/v1/source-acquisitions/${item.id}/replay`
          : item.stage === "snapshot"
            ? `/api/v1/source-snapshots/${item.id}/replay-extraction`
            : item.stage === "extraction"
              ? `/api/v1/extractions/${item.id}/replay-finalization`
              : item.stage === "agent-schedule" && item.graphId
                ? `/api/v1/agent-graphs/${item.graphId}/schedules/${item.id}/replay`
                : "";
      if (!endpoint) {
        setNotice("该阶段没有可执行的安全重放动作");
        return;
      }
      setBusy(true);
      try {
        const response = await adminFetch(`${apiUrl}${endpoint}`, {
          method: "POST",
        });
        const payload = (await response.json()) as {
          resourceId?: string;
          detail?: string;
        };
        if (!response.ok) {
          throw new Error(payload.detail ?? "重放请求失败");
        }
        setNotice(`${payload.resourceId ?? item.id} 已重新进入可靠队列`);
        await loadActivity(sourceId);
      } catch (error) {
        setNotice(error instanceof Error ? error.message : "重放请求失败");
      } finally {
        setBusy(false);
      }
    },
    [loadActivity],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const baseUrl = String(form.get("baseUrl"));
    const host = new URL(baseUrl).hostname;
    setBusy(true);
    try {
      const response = await adminFetch(`${apiUrl}/api/v1/sources`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id: form.get("id"),
          version: form.get("version"),
          name: form.get("name"),
          kind: form.get("kind"),
          baseUrl,
          allowedHosts: [host],
          trustTier: form.get("trustTier"),
          licenseId: form.get("licenseId"),
          licenseStatus: form.get("licenseStatus"),
          robotsPolicy: form.get("robotsPolicy"),
          robotsStatus: form.get("robotsStatus"),
          modelProcessingPolicy: form.get("modelProcessingPolicy"),
          allowedMediaTypes: String(form.get("mediaTypes"))
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          parserId: String(form.get("parserId")) || undefined,
          parserVersion: String(form.get("parserVersion")) || undefined,
          maxBytes: Number(form.get("maxBytes")),
          locales: String(form.get("locales"))
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          entityTypeIds: String(form.get("entityTypeIds"))
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          taxonomyNodeIds: String(form.get("taxonomyNodeIds"))
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          schedule: String(form.get("schedule")) || undefined,
          status: form.get("status"),
        }),
      });
      const payload = (await response.json()) as {
        source?: { id: string };
        detail?: string;
      };
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string" ? payload.detail : "注册来源失败",
        );
      }
      setNotice(`已注册 ${payload.source?.id}，采集前仍需通过策略检查`);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "注册来源失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="source-registry-page">
      <header className="source-registry-header">
        <div>
          <a href="/">← 返回运行中心</a>
          <p>GOVERNED SOURCE ACQUISITION</p>
          <h1>来源与许可中心</h1>
          <span>许可、robots、域名、媒体类型和快照哈希共同约束 Agent 采集</span>
        </div>
        <small>{notice}</small>
      </header>
      <div className="source-registry-layout">
        <form className="source-registration-form" onSubmit={register}>
          <p>REGISTER SOURCE</p>
          <h2>注册版本化来源</h2>
          <div>
            <label>
              来源 ID
              <input defaultValue="source-new" name="id" required />
            </label>
            <label>
              版本
              <input defaultValue="1.0.0" name="version" required />
            </label>
          </div>
          <label>
            显示名称
            <input name="name" required />
          </label>
          <label>
            基础 URL
            <input name="baseUrl" placeholder="https://…" required type="url" />
          </label>
          <div>
            <label>
              来源类型
              <select defaultValue="website" name="kind">
                <option value="website">网站</option>
                <option value="api">API</option>
                <option value="feed">Feed</option>
                <option value="dataset">数据集</option>
                <option value="file">文件</option>
              </select>
            </label>
            <label>
              信任等级
              <select defaultValue="authoritative" name="trustTier">
                <option value="primary">原始</option>
                <option value="authoritative">权威</option>
                <option value="secondary">二手</option>
                <option value="community">社区</option>
              </select>
            </label>
          </div>
          <div>
            <label>
              许可标识
              <input defaultValue="review-pending" name="licenseId" required />
            </label>
            <label>
              许可状态
              <select defaultValue="review-required" name="licenseStatus">
                <option value="review-required">待审核</option>
                <option value="allowed">允许</option>
                <option value="blocked">阻断</option>
              </select>
            </label>
          </div>
          <div>
            <label>
              robots 策略
              <select defaultValue="respect" name="robotsPolicy">
                <option value="respect">遵守 robots</option>
                <option value="explicit-api">显式 API</option>
                <option value="not-applicable">不适用</option>
              </select>
            </label>
            <label>
              robots 状态
              <select defaultValue="review-required" name="robotsStatus">
                <option value="review-required">待审核</option>
                <option value="allowed">允许</option>
                <option value="blocked">阻断</option>
              </select>
            </label>
          </div>
          <label>
            模型处理策略
            <select defaultValue="forbidden" name="modelProcessingPolicy">
              <option value="forbidden">禁止发送到模型</option>
              <option value="approved-gateway">允许经批准的模型代理处理</option>
            </select>
          </label>
          <div>
            <label>
              初始状态
              <select defaultValue="paused" name="status">
                <option value="paused">暂停</option>
                <option value="active">启用</option>
                <option value="blocked">阻断</option>
              </select>
            </label>
            <label>
              采集计划
              <input name="schedule" placeholder="cron 表达式" />
            </label>
          </div>
          <label>
            允许媒体类型（逗号分隔）
            <input defaultValue="text/html" name="mediaTypes" required />
          </label>
          <div>
            <label>
              解析器 ID
              <input
                list="source-parser-options"
                name="parserId"
                placeholder="parser-animals-html"
              />
              <datalist id="source-parser-options">
                {parsers.map((parser) => (
                  <option
                    key={`${parser.id}@${parser.version}`}
                    value={parser.id}
                  >
                    {parser.format} · {parser.entityTypeId}
                  </option>
                ))}
              </datalist>
            </label>
            <label>
              解析器版本
              <input name="parserVersion" placeholder="1.0.0" />
            </label>
          </div>
          <div>
            <label>
              最大字节数
              <input
                defaultValue="5000000"
                min="1"
                name="maxBytes"
                type="number"
              />
            </label>
            <label>
              语言
              <input defaultValue="zh-CN,en" name="locales" />
            </label>
          </div>
          <div>
            <label>
              实体类型范围
              <input
                name="entityTypeIds"
                placeholder="type-plant,type-animal"
              />
            </label>
            <label>
              分类节点范围
              <input name="taxonomyNodeIds" placeholder="taxonomy-plants" />
            </label>
          </div>
          <button disabled={busy} type="submit">
            保存来源版本
          </button>
          <small>
            默认以暂停、许可与 robots 待审核状态注册，Agent 无法采集。当前已加载{" "}
            {parsers.length} 个受限声明式解析器。
          </small>
        </form>

        <section className="source-list-panel">
          <div className="source-list-title">
            <div>
              <p>SOURCE POLICIES</p>
              <h2>已注册来源</h2>
            </div>
            <button className="secondary" onClick={() => void refresh()}>
              刷新
            </button>
          </div>
          <div className="source-cards">
            {records.length ? (
              records.map(({ source, policy }) => (
                <article data-allowed={policy.allowed} key={source.id}>
                  <header>
                    <div>
                      <code>
                        {source.id}@{source.version}
                      </code>
                      <h3>{source.name}</h3>
                      <a href={source.baseUrl}>{source.baseUrl}</a>
                    </div>
                    <b>{policy.allowed ? "可采集" : "已阻断"}</b>
                  </header>
                  <dl>
                    <div>
                      <dt>信任等级</dt>
                      <dd>{source.trustTier}</dd>
                    </div>
                    <div>
                      <dt>许可</dt>
                      <dd>
                        {source.licenseId} · {source.licenseStatus}
                      </dd>
                    </div>
                    <div>
                      <dt>robots</dt>
                      <dd>
                        {source.robotsPolicy} · {source.robotsStatus}
                      </dd>
                    </div>
                    <div>
                      <dt>模型处理</dt>
                      <dd>{source.modelProcessingPolicy}</dd>
                    </div>
                    <div>
                      <dt>媒体/上限</dt>
                      <dd>
                        {source.allowedMediaTypes.join(", ")} ·{" "}
                        {(source.maxBytes / 1_000_000).toFixed(1)} MB
                      </dd>
                    </div>
                    <div>
                      <dt>解析器</dt>
                      <dd>
                        {source.parserId
                          ? `${source.parserId}@${source.parserVersion}`
                          : "未固定（仅保存快照）"}
                      </dd>
                    </div>
                    <div>
                      <dt>维护路由范围</dt>
                      <dd>
                        类型：
                        {source.entityTypeIds.length
                          ? source.entityTypeIds.join(", ")
                          : "通用"}
                        {" · "}分类：
                        {source.taxonomyNodeIds.length
                          ? source.taxonomyNodeIds.join(", ")
                          : "通用"}
                        {" · "}语言：
                        {source.locales.length
                          ? source.locales.join(", ")
                          : "通用"}
                      </dd>
                    </div>
                  </dl>
                  {policy.blockers.length ? (
                    <ul>
                      {policy.blockers.map((blocker) => (
                        <li key={blocker}>! {blocker}</li>
                      ))}
                    </ul>
                  ) : (
                    <footer>
                      <span>✓ 域名与许可策略通过</span>
                      <small>{source.schedule ?? "仅手动采集"}</small>
                    </footer>
                  )}
                  <div className="source-activity">
                    <div className="source-activity-actions">
                      <button
                        className="secondary"
                        onClick={() => void loadActivity(source.id)}
                        type="button"
                      >
                        查看维护链路
                      </button>
                      <button
                        disabled={busy || !policy.allowed}
                        onClick={() => void requestAcquisition(source.id)}
                        type="button"
                      >
                        请求采集
                      </button>
                    </div>
                    {(() => {
                      const activity = activities[source.id];
                      return activity ? (
                        <div>
                          <span>{activity.snapshots.length} 个不可变快照</span>
                          <span>{activity.acquisitions.length} 个采集任务</span>
                          <span>
                            {activity.batches.reduce(
                              (total, batch) => total + batch.candidateCount,
                              0,
                            )}{" "}
                            个结构化候选
                          </span>
                          <span>
                            {
                              activity.candidates.filter(
                                (candidate) => candidate.status === "parsed",
                              ).length
                            }{" "}
                            个待消歧
                          </span>
                          <span>
                            {
                              activity.candidates.filter(
                                (candidate) => candidate.status === "scheduled",
                              ).length
                            }{" "}
                            个已调度
                          </span>
                          <div className="source-pipeline-summary">
                            <b>自动维护流水线</b>
                            <span>
                              {activity.pipeline.pendingEventCount}{" "}
                              个可靠事件待派发 ·{" "}
                              {activity.pipeline.stalledCount} 个阶段需处理
                            </span>
                          </div>
                          <ol className="source-pipeline-list">
                            {activity.pipeline.items.slice(-12).map((item) => (
                              <li
                                data-status={item.status}
                                key={`${item.stage}:${item.id}`}
                              >
                                <div>
                                  <b>{item.stage}</b>
                                  <code>{item.id}</code>
                                  <span>{item.status}</span>
                                  {item.error ? (
                                    <small>{item.error}</small>
                                  ) : null}
                                </div>
                                {item.replayable ? (
                                  <button
                                    className="secondary"
                                    disabled={busy}
                                    onClick={() =>
                                      void replayPipelineItem(source.id, item)
                                    }
                                    type="button"
                                  >
                                    安全重放
                                  </button>
                                ) : null}
                              </li>
                            ))}
                          </ol>
                          {activity.batches.slice(0, 3).map((batch) => (
                            <small key={batch.id}>
                              {batch.parserId}@{batch.parserVersion} ·{" "}
                              {batch.candidateCount} 候选 · {batch.status}
                            </small>
                          ))}
                          {activity.acquisitions.slice(0, 3).map((job) => (
                            <small key={job.id}>
                              {job.id} · {job.status}
                              {job.error ? ` · ${job.error}` : ""}
                            </small>
                          ))}
                          {activity.candidates.slice(0, 4).map((candidate) => (
                            <small key={candidate.id}>
                              {candidate.labels[0]?.value ?? candidate.id} ·{" "}
                              {candidate.status} ·{" "}
                              {candidate.entityId
                                ? `命中 ${candidate.entityId}`
                                : `${candidate.resolutionMatches.length} 个可能匹配`}
                            </small>
                          ))}
                        </div>
                      ) : null;
                    })()}
                  </div>
                </article>
              ))
            ) : (
              <div className="source-empty">
                <h3>尚未注册来源</h3>
                <p>先在左侧登记许可与采集边界，再允许 Agent 获取内容。</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
