import { SiteHeader } from "../components/site-header";
import { localize } from "../lib/catalog";
import {
  getBuildInfo,
  getSpaces,
  getTaxonomyNodes,
  searchDiscovery,
} from "../lib/data-source";
import { getPreferredLocale } from "../lib/locale";

export const dynamic = "force-dynamic";

const accents = ["green", "blue", "amber", "violet", "cyan", "rose"];
const icons: Record<string, string> = {
  life: "◌",
  earth: "⌁",
  engineering: "◇",
  humanities: "文",
  mathematics: "∑",
  medicine: "🩺",
  geography: "⛰",
  geology: "🪨",
  language: "文",
  history: "◉",
  ocean: "🌊",
  astronomy: "🌌",
};

export default async function HomePage() {
  const [build, spaces, taxonomy, locale, discovery] = await Promise.all([
    getBuildInfo(),
    getSpaces(),
    getTaxonomyNodes(),
    getPreferredLocale(),
    searchDiscovery({
      query: "",
      limit: 4,
    }),
  ]);

  const publishedSpaces = spaces.filter(
    (space) => space.status === "published",
  );
  const publishedSpaceIds = new Set(publishedSpaces.map((space) => space.id));
  const publishedTaxonomyNodes = taxonomy.filter((node) =>
    publishedSpaceIds.has(node.spaceId),
  ).length;

  const spaceCounts = new Map(
    discovery.facets.spaces.map((item) => [item.value, item.count]),
  );
  const sampleTotalCitations = discovery.items.reduce(
    (sum, item) => sum + item.entity.citations.length,
    0,
  );
  const sampleAverageCitations = discovery.items.length
    ? (sampleTotalCitations / discovery.items.length).toFixed(1)
    : "0";

  return (
    <main>
      <SiteHeader />

      <section className="hero" id="explore">
        <p className="kicker">A LIVING KNOWLEDGE SYSTEM</p>
        <h1>探索世界的每一种知识</h1>
        <p className="hero-copy">
          一个持续生长、来源透明、由专家与智能 Agent 共同维护的专业百科全书。
        </p>
        <form action="/search" className="global-search">
          <span aria-hidden="true">⌕</span>
          <input
            aria-label="搜索百科"
            name="q"
            placeholder="搜索条目、别名、学名、型号或提出一个问题"
          />
          <kbd>⌘ K</kbd>
          <button type="submit">搜索</button>
          <button className="ask-submit" formAction="/ask" type="submit">
            提问
          </button>
        </form>
        <div className="search-hints">
          <span>试试：</span>
          <a href="/ask?q=雪豹生活在什么环境">雪豹的栖息地</a>
          <a href="/ask?q=银杏为什么被称为活化石">银杏为什么被称为活化石</a>
          <a href="/ask?q=海洋为什么会有分层？">海洋为什么会有分层？</a>
        </div>
      </section>

      <section className="spaces" id="taxonomy">
        <div className="section-heading">
          <div>
            <p className="eyebrow">KNOWLEDGE DOMAINS</p>
            <h2>按知识领域探索</h2>
          </div>
          <a className="all-link" href="/categories">
            查看完整分类体系 →
          </a>
        </div>
        <div className="space-grid">
          {spaces.map((space, index) => {
            const count = spaceCounts.get(space.id) ?? 0;
            return (
              <a
                className={`space-card ${accents[index % accents.length]}`}
                href={`/categories?space=${encodeURIComponent(space.id)}`}
                key={space.id}
              >
                <div className="space-icon" aria-hidden="true">
                  {icons[space.iconKey] ?? "□"}
                </div>
                <div>
                  <h3>{localize(space.name, locale)}</h3>
                  <p>{localize(space.description, locale)}</p>
                  <span>{count.toLocaleString()} 条目</span>
                </div>
                <b aria-hidden="true">→</b>
              </a>
            );
          })}
        </div>
      </section>

      <section className="knowledge-pulse">
        <div className="section-heading">
          <div>
            <p className="eyebrow">ENCYCLOPEDIA OPERATIONS</p>
            <h2>知识运营健康度</h2>
          </div>
          <a className="all-link" href="/categories">
            继续扩展分类 →
          </a>
        </div>
        <div className="pulse-grid">
          <article>
            <b>{build.dataVersion}</b>
            <span>当前发布版本</span>
          </article>
          <article>
            <b>{build.schemaVersion}</b>
            <span>Schema 版本</span>
          </article>
          <article>
            <b>{discovery.total.toLocaleString()}</b>
            <span>可检索条目</span>
          </article>
          <article>
            <b>{sampleAverageCitations}</b>
            <span>示例条目平均证据数</span>
          </article>
          <article>
            <b>
              {publishedSpaces.length} / {spaces.length}
            </b>
            <span>已发布知识领域</span>
          </article>
          <article>
            <b>
              {publishedTaxonomyNodes} / {taxonomy.length}
            </b>
            <span>有效分类节点</span>
          </article>
        </div>
        <div className="domain-pulse-grid">
          {publishedSpaces.map((space) => {
            const count = spaceCounts.get(space.id) ?? 0;
            return (
              <a
                href={`/categories?space=${encodeURIComponent(space.id)}`}
                key={space.id}
              >
                <strong>{localize(space.name, locale)}</strong>
                <span>{count} 条目</span>
              </a>
            );
          })}
        </div>
      </section>

      <section className="featured">
        <div className="section-heading">
          <div>
            <p className="eyebrow">CURATED ENTRIES</p>
            <h2>今日精选条目</h2>
          </div>
          <p className="release-note">
            <i aria-hidden="true" /> 数据版本 {build.dataVersion} ·
            实时读取搜索索引
          </p>
        </div>
        <div className="entry-list">
          {discovery.items.slice(0, 3).map(({ entity }, index) => (
            <a
              className="featured-entry"
              href={`/entry/${entity.ref.slug}`}
              key={entity.ref.id}
            >
              <div className={`entry-index entry-index-${index + 1}`}>
                0{index + 1}
              </div>
              <div className="entry-title">
                <strong>{localize(entity.names, locale)}</strong>
                <em>{entity.aliases[0]?.value ?? "暂无别名"}</em>
              </div>
              <span>{entity.ref.typeId}</span>
              <span className="source-count">
                ✓ {entity.citations.length} 个可靠来源
              </span>
              <b aria-hidden="true">↗</b>
            </a>
          ))}
          {discovery.total === 0 ? (
            <p className="empty-results">尚未有公开条目可展示。</p>
          ) : null}
        </div>
      </section>

      <footer>
        <div>
          <strong>知识正在持续更新</strong>
          <span>
            当前发布集 {discovery.total.toLocaleString()} 个条目 · 样例条目共
            {sampleTotalCitations} 条来源记录
          </span>
        </div>
        <p>
          内容由结构化证据、版本策略与可审计 Agent
          流程共同维护，支持持续扩展到更多领域。
        </p>
      </footer>
    </main>
  );
}
