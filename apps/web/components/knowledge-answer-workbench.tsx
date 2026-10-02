"use client";

import type { KnowledgeAnswer } from "@hardatlas/contracts";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

const modeLabels: Record<KnowledgeAnswer["mode"], string> = {
  "retrieval-synthesis": "证据检索摘要",
  "model-proxy": "经批准模型代理",
  "retrieval-fallback": "代理失败后证据降级",
};

export function KnowledgeAnswerWorkbench({
  initialQuestion,
  locale,
}: {
  initialQuestion: string;
  locale: string;
}) {
  const [question, setQuestion] = useState(initialQuestion);
  const [answer, setAnswer] = useState<KnowledgeAnswer>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const initialRequestSent = useRef(false);

  const ask = useCallback(
    async (value: string) => {
      const normalized = value.trim();
      if (normalized.length < 2) return;
      setLoading(true);
      setError("");
      setAnswer(undefined);
      try {
        const response = await fetch("/apps/hardatlas/api/backend/answers", {
          method: "POST",
          headers: {
            accept: "application/json",
            "content-type": "application/json",
          },
          body: JSON.stringify({
            question: normalized,
            locale,
            mode: "auto",
          }),
        });
        if (!response.ok) {
          throw new Error(`知识问答请求失败（${response.status}）`);
        }
        setAnswer((await response.json()) as KnowledgeAnswer);
      } catch (cause) {
        setError(
          cause instanceof Error
            ? cause.message
            : "知识问答暂时不可用，请稍后重试。",
        );
      } finally {
        setLoading(false);
      }
    },
    [locale],
  );

  useEffect(() => {
    if (!initialQuestion || initialRequestSent.current) return;
    initialRequestSent.current = true;
    void ask(initialQuestion);
  }, [ask, initialQuestion]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask(question);
  }

  const citationById = new Map(
    answer?.citations.map((citation) => [citation.id, citation]) ?? [],
  );

  return (
    <section className="ask-workbench">
      <header>
        <p className="eyebrow">EVIDENCE-FIRST ANSWERING</p>
        <h1>向 Atlas 提出一个问题</h1>
        <p>
          回答只使用当前已发布条目及其固定修订证据。模型是可选的表达层，不是事实来源。
        </p>
      </header>

      <form className="ask-form" onSubmit={submit}>
        <label className="sr-only" htmlFor="atlas-question">
          输入百科问题
        </label>
        <span aria-hidden="true">?</span>
        <textarea
          id="atlas-question"
          maxLength={1000}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="例如：雪豹生活在什么环境？"
          rows={2}
          value={question}
        />
        <button disabled={loading || question.trim().length < 2} type="submit">
          {loading ? "检索证据中…" : "生成证据回答"}
        </button>
      </form>

      <div className="ask-policy">
        <span>✓ 固定修订</span>
        <span>✓ 引用白名单</span>
        <span>✓ 浏览器同源 BFF</span>
        <span>✓ 模型仅经服务端代理</span>
      </div>

      {error ? (
        <div className="ask-error" role="alert">
          <strong>无法生成回答</strong>
          <span>{error}</span>
        </div>
      ) : null}

      {loading ? (
        <div aria-live="polite" className="answer-loading">
          <i />
          <div>
            <strong>正在定位条目与可引用证据</strong>
            <span>不会把开放网络内容或模型记忆直接作为答案。</span>
          </div>
        </div>
      ) : null}

      {answer ? (
        <div className="answer-layout">
          <article className="answer-card">
            <header>
              <div>
                <p className="eyebrow">GROUNDED ANSWER</p>
                <h2>{answer.question}</h2>
              </div>
              <span data-status={answer.status}>
                {answer.status === "answered" ? "证据充分" : "证据不足"}
              </span>
            </header>
            <p className="answer-text">{answer.answer}</p>
            <div className="answer-provenance">
              <div>
                <small>生成方式</small>
                <strong>{modeLabels[answer.mode]}</strong>
              </div>
              <div>
                <small>数据版本</small>
                <strong>
                  {answer.dataVersions.join(" · ") || "无匹配版本"}
                </strong>
              </div>
              <div>
                <small>回答记录</small>
                <code>{answer.id}</code>
              </div>
            </div>
            {answer.modelProvenance ? (
              <details className="model-provenance">
                <summary>模型代理调用记录</summary>
                <dl>
                  <div>
                    <dt>Gateway</dt>
                    <dd>{answer.modelProvenance.gatewayId}</dd>
                  </div>
                  <div>
                    <dt>Model alias</dt>
                    <dd>{answer.modelProvenance.model}</dd>
                  </div>
                  <div>
                    <dt>Request ID</dt>
                    <dd>{answer.modelProvenance.requestId}</dd>
                  </div>
                  <div>
                    <dt>状态</dt>
                    <dd>{answer.modelProvenance.status}</dd>
                  </div>
                </dl>
              </details>
            ) : null}
          </article>

          <aside className="answer-sources" aria-label="回答证据">
            <header>
              <div>
                <p className="eyebrow">EVIDENCE LEDGER</p>
                <h2>引用证据</h2>
              </div>
              <span>{answer.evidence.length} 条</span>
            </header>
            {answer.evidence.length ? (
              <ol>
                {answer.evidence.map((evidence, index) => (
                  <li key={evidence.id}>
                    <div className="evidence-number">{index + 1}</div>
                    <div>
                      <a href={`/entry/${evidence.entity.slug}`}>
                        {evidence.entity.canonicalName} ↗
                      </a>
                      <strong>{evidence.label}</strong>
                      <p>{evidence.text}</p>
                      <small>
                        修订 {evidence.revisionId} · 可信度{" "}
                        {(evidence.confidence * 100).toFixed(0)}%
                      </small>
                      {evidence.citationIds.map((citationId) => {
                        const citation = citationById.get(citationId);
                        return citation ? (
                          <div className="answer-citation" key={citation.id}>
                            <b>{citation.sourceTitle}</b>
                            <span>
                              {citation.sourceTier} ·{" "}
                              {citation.retrievedAt.slice(0, 10)}
                            </span>
                            {citation.locator ? (
                              <code>{citation.locator}</code>
                            ) : null}
                          </div>
                        ) : null;
                      })}
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <div className="no-answer-evidence">
                <strong>没有可引用的已发布证据</strong>
                <span>请改用明确的条目名、别名、学名或型号重新提问。</span>
              </div>
            )}
          </aside>
        </div>
      ) : null}
    </section>
  );
}
