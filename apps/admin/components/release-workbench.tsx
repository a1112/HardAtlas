"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

interface GovernedProposal {
  proposal: {
    id: string;
    entityId?: string;
    proposalType: string;
    risk: string;
    status: string;
  };
}

interface ReleaseManifest {
  id: string;
  proposalIds: string[];
  dataVersion: string;
  schemaVersions: string[];
  policyVersion: string;
  previousReleaseId: string;
  trigger: "manual" | "automatic";
  initiatedBy?: string;
  status:
    "staged" | "publishing" | "published" | "rolling-back" | "rolled-back";
  searchIndex?: string;
  searchAlias?: string;
  rollbackSearchIndex?: string;
  entityRevisionsBefore: Record<string, string>;
  entityRevisionsAfter: Record<string, string>;
  verification: {
    status: "pending" | "running" | "passed" | "failed" | "superseded";
    attempt: number;
    checks: {
      key: string;
      status: "passed" | "failed" | "skipped";
      detail: string;
      affectedEntityIds: string[];
    }[];
    automaticRollback: boolean;
    rollbackReason?: string;
    completedAt?: string;
  };
  createdAt: string;
  publishedAt?: string;
  rolledBackAt?: string;
}

const apiUrl = "/api/backend";

export function ReleaseWorkbench() {
  const [proposals, setProposals] = useState<GovernedProposal[]>([]);
  const [releases, setReleases] = useState<ReleaseManifest[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [notice, setNotice] = useState("正在读取发布状态…");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [proposalResponse, releaseResponse] = await Promise.all([
        adminFetch(`${apiUrl}/api/v1/proposals`, { cache: "no-store" }),
        adminFetch(`${apiUrl}/api/v1/releases`, { cache: "no-store" }),
      ]);
      if (!proposalResponse.ok || !releaseResponse.ok) {
        throw new Error("发布 API 返回异常");
      }
      const nextProposals =
        (await proposalResponse.json()) as GovernedProposal[];
      const nextReleases = (await releaseResponse.json()) as ReleaseManifest[];
      setProposals(nextProposals);
      setReleases(nextReleases);
      setSelected((current) =>
        current.filter((id) =>
          nextProposals.some(
            (item) =>
              item.proposal.id === id && item.proposal.status === "accepted",
          ),
        ),
      );
      setNotice("发布状态已同步");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "发布 API 不可用");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function stage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const response = await adminFetch(`${apiUrl}/api/v1/releases`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id: form.get("releaseId"),
          proposalIds: selected,
          dataVersion: form.get("dataVersion"),
          schemaVersions: [form.get("schemaVersion")],
          previousReleaseId: form.get("previousReleaseId"),
        }),
      });
      const payload = (await response.json()) as
        ReleaseManifest | { detail?: string };
      if (!response.ok) {
        throw new Error(
          "detail" in payload ? payload.detail : "创建发布批次失败",
        );
      }
      setNotice("发布批次已暂存，尚未修改公开知识");
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建发布批次失败");
    } finally {
      setBusy(false);
    }
  }

  async function transition(releaseId: string, action: "publish" | "rollback") {
    setBusy(true);
    setNotice(
      action === "publish"
        ? "正在生成实体修订、构建索引并切换别名…"
        : "正在恢复实体修订并生成回滚索引…",
    );
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/releases/${releaseId}/${action}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
        },
      );
      const payload = (await response.json()) as
        ReleaseManifest | { detail?: string };
      if (!response.ok) {
        throw new Error(
          "detail" in payload ? payload.detail : `${action} 操作失败`,
        );
      }
      setNotice(
        action === "publish"
          ? "实体修订与搜索索引已发布"
          : "实体修订与搜索索引已回滚",
      );
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "发布操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function verify(release: ReleaseManifest) {
    setBusy(true);
    setNotice("正在核对冻结修订、引用、关系和搜索可见性…");
    try {
      const retry =
        release.verification.status === "failed" ? "?retryFailed=true" : "";
      const response = await adminFetch(
        `${apiUrl}/api/v1/releases/${release.id}/verify${retry}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
        },
      );
      const payload = (await response.json()) as
        ReleaseManifest | { detail?: string };
      if (!response.ok) {
        throw new Error("detail" in payload ? payload.detail : "发布验证失败");
      }
      const verified = payload as ReleaseManifest;
      const resultNotice =
        verified.verification.status === "passed"
          ? "发布后完整性验证已通过"
          : verified.verification.status === "superseded"
            ? "该发布已被更新版本替代，冻结修订验证完成"
            : "验证发现回归，请检查失败项并决定是否回滚";
      await refresh();
      setNotice(resultNotice);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "发布验证失败");
    } finally {
      setBusy(false);
    }
  }

  const accepted = proposals.filter(
    (item) => item.proposal.status === "accepted",
  );
  const previousRelease =
    releases.find((release) => release.status === "published")?.id ??
    "bootstrap";

  return (
    <main className="release-workbench-page">
      <header className="release-workbench-header">
        <div>
          <a href="/">← 返回运行中心</a>
          <p>ATOMIC KNOWLEDGE RELEASE</p>
          <h1>发布与回滚工作台</h1>
          <span>提案 → 不可变实体修订 → 版本化搜索索引 → 别名切换</span>
        </div>
        <small>{notice}</small>
      </header>

      <div className="release-workbench-layout">
        <form className="release-create-panel" onSubmit={stage}>
          <p>RELEASE CANDIDATE</p>
          <h2>创建发布批次</h2>
          <label>
            批次标识
            <input
              defaultValue={`release-${new Date().toISOString().slice(0, 10)}`}
              name="releaseId"
            />
          </label>
          <label>
            数据版本
            <input
              defaultValue={`atlas-${new Date().toISOString().slice(0, 10)}`}
              name="dataVersion"
            />
          </label>
          <label>
            Schema 版本
            <input defaultValue="schema-2.1.0" name="schemaVersion" />
          </label>
          <label>
            上一发布
            <input defaultValue={previousRelease} name="previousReleaseId" />
          </label>
          <fieldset>
            <legend>已接受提案</legend>
            {accepted.length ? (
              accepted.map((item) => (
                <label key={item.proposal.id}>
                  <input
                    checked={selected.includes(item.proposal.id)}
                    onChange={(event) =>
                      setSelected((current) =>
                        event.target.checked
                          ? [...current, item.proposal.id]
                          : current.filter((id) => id !== item.proposal.id),
                      )
                    }
                    type="checkbox"
                  />
                  <span>
                    <strong>{item.proposal.id}</strong>
                    {item.proposal.entityId ?? "Schema 提案"} ·{" "}
                    {item.proposal.risk}
                  </span>
                </label>
              ))
            ) : (
              <small>当前没有通过策略与人工审核的待发布提案。</small>
            )}
          </fieldset>
          <button disabled={busy || selected.length === 0} type="submit">
            暂存发布批次
          </button>
          <small>暂存不会修改公开条目，也不会切换搜索索引。</small>
        </form>

        <section className="release-history-panel">
          <div className="release-history-title">
            <div>
              <p>RELEASE MANIFESTS</p>
              <h2>发布清单</h2>
            </div>
            <button className="secondary" onClick={() => void refresh()}>
              刷新
            </button>
          </div>
          <div className="release-manifest-list">
            {releases.length ? (
              releases.map((release) => (
                <article data-state={release.status} key={release.id}>
                  <header>
                    <div>
                      <code>{release.id}</code>
                      <h3>{release.dataVersion}</h3>
                    </div>
                    <b>{release.status}</b>
                  </header>
                  <dl>
                    <div>
                      <dt>提案</dt>
                      <dd>{release.proposalIds.length}</dd>
                    </div>
                    <div>
                      <dt>实体修订</dt>
                      <dd>
                        {Object.keys(release.entityRevisionsAfter ?? {}).length}
                      </dd>
                    </div>
                    <div>
                      <dt>Schema</dt>
                      <dd>{release.schemaVersions.join(", ")}</dd>
                    </div>
                    <div>
                      <dt>策略</dt>
                      <dd>{release.policyVersion}</dd>
                    </div>
                    <div>
                      <dt>触发方式</dt>
                      <dd>
                        {release.trigger === "automatic"
                          ? "Agent 自动发布"
                          : "人工批次"}
                      </dd>
                    </div>
                    <div>
                      <dt>发起者</dt>
                      <dd>{release.initiatedBy ?? "未记录"}</dd>
                    </div>
                  </dl>
                  <div className="release-index-state">
                    <span>当前索引：{release.searchIndex ?? "尚未生成"}</span>
                    <span>读取别名：{release.searchAlias ?? "内存索引"}</span>
                    {release.rollbackSearchIndex ? (
                      <span>回滚索引：{release.rollbackSearchIndex}</span>
                    ) : null}
                  </div>
                  <section
                    className="release-verification"
                    data-verification={release.verification.status}
                  >
                    <header>
                      <strong>发布后完整性验证</strong>
                      <b>{release.verification.status}</b>
                      <small>第 {release.verification.attempt} 次</small>
                    </header>
                    {release.verification.checks.length ? (
                      <ul>
                        {release.verification.checks.map((check) => (
                          <li data-check={check.status} key={check.key}>
                            <span aria-hidden="true">
                              {check.status === "passed"
                                ? "✓"
                                : check.status === "failed"
                                  ? "!"
                                  : "–"}
                            </span>
                            <div>
                              <strong>{check.key}</strong>
                              <small>{check.detail}</small>
                              {check.affectedEntityIds.length ? (
                                <code>
                                  {check.affectedEntityIds.join(", ")}
                                </code>
                              ) : null}
                            </div>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <small>发布完成后由 Worker 自动执行七项检查。</small>
                    )}
                    {release.verification.automaticRollback ? (
                      <p>已自动回滚：{release.verification.rollbackReason}</p>
                    ) : null}
                  </section>
                  <footer>
                    {release.status === "staged" ? (
                      <button
                        disabled={busy}
                        onClick={() => void transition(release.id, "publish")}
                      >
                        发布并切换索引
                      </button>
                    ) : null}
                    {release.status === "published" ? (
                      <>
                        {release.verification.status !== "passed" &&
                        release.verification.status !== "superseded" ? (
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() => void verify(release)}
                          >
                            {release.verification.status === "failed"
                              ? "重试完整性验证"
                              : "立即验证"}
                          </button>
                        ) : null}
                        <button
                          className="danger"
                          disabled={busy}
                          onClick={() =>
                            void transition(release.id, "rollback")
                          }
                        >
                          回滚此发布
                        </button>
                      </>
                    ) : null}
                    <small>
                      创建于 {release.createdAt.slice(0, 19).replace("T", " ")}
                    </small>
                  </footer>
                </article>
              ))
            ) : (
              <div className="release-empty">
                <h3>尚无发布清单</h3>
                <p>审核并接受提案后，可在左侧创建第一个批次。</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
