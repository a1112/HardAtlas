"use client";

import { useCallback, useEffect, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

interface AuditEvent {
  id: string;
  occurredAt: string;
  actor: {
    subject: string;
    displayName: string;
    roles: string[];
    authenticationMethod: string;
  };
  action: string;
  resourceType: string;
  resourceId: string;
  outcome: "success" | "denied" | "failed";
  requestId: string;
  metadata: Record<string, unknown>;
  previousHash?: string;
  eventHash: string;
}

interface AuditStatus {
  valid: boolean;
  eventCount: number;
  latestHash?: string;
}

const apiUrl = "/apps/hardatlas-admin/api/backend";

export function AuditLog() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [status, setStatus] = useState<AuditStatus>();
  const [notice, setNotice] = useState("正在验证审计哈希链…");

  const refresh = useCallback(async () => {
    try {
      const [eventResponse, statusResponse] = await Promise.all([
        adminFetch(`${apiUrl}/api/v1/audit-events?limit=200`, {
          cache: "no-store",
        }),
        adminFetch(`${apiUrl}/api/v1/audit-events/status`, {
          cache: "no-store",
        }),
      ]);
      if (!eventResponse.ok || !statusResponse.ok) {
        throw new Error("审计 API 返回异常");
      }
      setEvents((await eventResponse.json()) as AuditEvent[]);
      const nextStatus = (await statusResponse.json()) as AuditStatus;
      setStatus(nextStatus);
      setNotice(
        nextStatus.valid
          ? "审计链完整，事件哈希验证通过"
          : "审计链校验失败，请立即停止发布",
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "审计 API 不可用");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <main className="audit-page">
      <header className="audit-header">
        <div>
          <a href="/">← 返回运行中心</a>
          <p>IDENTITY & IMMUTABLE AUDIT</p>
          <h1>身份与审计日志</h1>
          <span>OIDC 主体、角色授权、请求标识和哈希链共同记录受控操作</span>
        </div>
        <div className="audit-integrity" data-valid={status?.valid}>
          <b>{status?.valid ? "✓ 哈希链完整" : "等待校验"}</b>
          <small>{notice}</small>
        </div>
      </header>

      <section className="audit-metrics">
        <article>
          <span>审计事件</span>
          <strong>{status?.eventCount ?? "—"}</strong>
        </article>
        <article>
          <span>拒绝访问</span>
          <strong>
            {events.filter((event) => event.outcome === "denied").length}
          </strong>
        </article>
        <article>
          <span>最近事件哈希</span>
          <code>{status?.latestHash?.slice(0, 20) ?? "—"}</code>
        </article>
        <button className="secondary" onClick={() => void refresh()}>
          重新验证
        </button>
      </section>

      <section className="audit-table-panel">
        <div className="audit-table-header">
          <div>
            <p>RECENT PRIVILEGED ACTIVITY</p>
            <h2>最近受控操作</h2>
          </div>
          <span>仅显示最近 200 项，完整记录保存在数据库中</span>
        </div>
        <div className="audit-table">
          <div className="audit-row audit-columns">
            <b>时间 / 请求</b>
            <b>主体</b>
            <b>操作</b>
            <b>资源</b>
            <b>结果</b>
            <b>哈希链</b>
          </div>
          {events.map((event) => (
            <article className="audit-row" key={event.id}>
              <div>
                <time>{event.occurredAt.slice(0, 19).replace("T", " ")}</time>
                <code>{event.requestId.slice(0, 12)}</code>
              </div>
              <div>
                <strong>{event.actor.displayName}</strong>
                <small>
                  {event.actor.subject} · {event.actor.authenticationMethod}
                </small>
              </div>
              <div>
                <strong>{event.action}</strong>
                <small>{event.actor.roles.join(", ") || "无角色"}</small>
              </div>
              <div>
                <strong>{event.resourceType}</strong>
                <small>{event.resourceId}</small>
              </div>
              <b data-outcome={event.outcome}>{event.outcome}</b>
              <div>
                <code>{event.eventHash.slice(0, 14)}</code>
                <small>← {event.previousHash?.slice(0, 10) ?? "GENESIS"}</small>
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
