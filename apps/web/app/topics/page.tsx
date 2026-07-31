import { SiteHeader } from "../../components/site-header";
import { localize } from "../../lib/catalog";
import { getBuildInfo, getSpaces, getTaxonomyNodes, searchDiscovery } from "../../lib/data-source";
import { getPreferredLocale } from "../../lib/locale";

export const dynamic = "force-dynamic";

export default async function TopicsPage() {
  const locale = await getPreferredLocale();
  const [build, spaces, taxonomyNodes, allDiscovery] = await Promise.all([
    getBuildInfo(),
    getSpaces(),
    getTaxonomyNodes(),
    searchDiscovery({ query: "", limit: 6 }),
  ]);

  const featured = allDiscovery.items
    .slice(0, 6)
    .map(({ entity }) => ({
      id: entity.ref.id,
      name: localize(entity.names, locale),
      slug: entity.ref.slug,
    }));

  const bySpace = await Promise.all(
    spaces
      .filter((space) => space.status === "published")
      .map(async (space) => {
        const discovery = await searchDiscovery({
          query: "",
          spaceId: space.id,
          limit: 6,
          offset: 0,
        });
        const nodes = taxonomyNodes.filter((node) => node.spaceId === space.id);
        const topRoots = nodes
          .filter((node) => node.parentIds.length === 0)
          .slice(0, 4)
          .map((node) => localize(node.name, locale));
        return {
          space,
          totalEntities: discovery.total,
          featuredEntries: discovery.items.slice(0, 3).map(({ entity }) => ({
            id: entity.ref.id,
            name: localize(entity.names, locale),
            slug: entity.ref.slug,
          })),
          topRoots,
        };
      }),
  );

  return (
    <main className="topics-page">
      <SiteHeader />
      <section className="topics-hero">
        <p className="eyebrow">THEMATIC KNOWLEDGE</p>
        <h1>专题入口</h1>
        <p>
          Atlas 以“领域-分类-条目”三层结构承载百科内容，支持通过领域包持续扩展新的专题，不改前端代码即可添加。
        </p>
      </section>

      <section className="topic-grid">
        {bySpace.map((item) => (
          <article className="topic-card" key={item.space.id}>
            <header>
              <p className="eyebrow">专题域</p>
              <h2>{localize(item.space.name, locale)}</h2>
              <span>{localize(item.space.description, locale)}</span>
            </header>
            <div>
              <strong>已公开内容</strong>
              <b>{item.totalEntities.toLocaleString()}</b>
            </div>
            <div>
              <strong>核心分类</strong>
              <span>{item.topRoots.join(" · ") || "待补充分类"}</span>
            </div>
            <div>
              <strong>进入入口</strong>
              <a href={`/categories?space=${encodeURIComponent(item.space.id)}`}>
                浏览 {localize(item.space.name, locale)} 全景 →
              </a>
            </div>
            {item.featuredEntries.length ? (
              <ul>
                {item.featuredEntries.map((entry) => (
                  <li key={entry.id}>
                    <a href={`/entry/${entry.slug}`}>{entry.name}</a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="topic-empty">该专题尚在持续收录中</p>
            )}
          </article>
        ))}
      </section>

      <section className="topics-insight">
        <h2>当前版本快照</h2>
        <p>运行期数据：{build.schemaVersion}</p>
        <p>
          本页展示了“由发布数据驱动的专题导航”。样例条目：
          {featured.map((entry) => (
            <a href={`/entry/${entry.slug}`} key={entry.id}>
              {entry.name}
            </a>
          ))}
        </p>
      </section>
    </main>
  );
}
