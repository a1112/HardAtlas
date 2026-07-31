import { SiteHeader } from "../../components/site-header";
import { localize } from "../../lib/catalog";
import {
  getEntityTypes,
  getSpaces,
  getTaxonomyNodes,
  searchDiscovery,
} from "../../lib/data-source";
import { getPreferredLocale } from "../../lib/locale";

const matchFieldLabels: Record<string, string> = {
  alias: "别名匹配",
  browse: "分类浏览",
  canonicalName: "规范名称匹配",
  claim: "结构化属性匹配",
  description: "正文匹配",
  localFallback: "本地索引匹配",
  name: "名称匹配",
};

const localeLabels: Record<string, string> = {
  "zh-CN": "简体中文",
  "zh-TW": "繁体中文",
  en: "英语",
  la: "拉丁语",
  und: "未指定语言",
};

const sourceTierOptions = [
  ["", "全部来源等级"],
  ["primary", "第一手来源"],
  ["authoritative", "权威来源"],
  ["secondary", "二级来源"],
  ["community", "社区来源"],
] as const;

interface SearchParameters {
  q?: string | undefined;
  type?: string | undefined;
  space?: string | undefined;
  taxonomy?: string | undefined;
  locale?: string | undefined;
  sourceTier?: string | undefined;
  minimumSources?: string | undefined;
  page?: string | undefined;
}

function positiveInteger(value: string | undefined, fallback: number) {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function hrefForPage(params: SearchParameters, page: number) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value && key !== "page") query.set(key, value);
  });
  if (page > 1) query.set("page", String(page));
  return `/search?${query.toString()}`;
}

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<SearchParameters>;
}) {
  const params = await searchParams;
  const query = params.q?.trim() ?? "";
  const page = positiveInteger(params.page, 1);
  const pageSize = 20;
  const minimumSources = Math.min(
    positiveInteger(params.minimumSources, 0),
    100,
  );
  const preferredLocale = await getPreferredLocale();
  const [results, taxonomyNodes, entityTypes, spaces] = await Promise.all([
    searchDiscovery({
      query,
      typeId: params.type,
      spaceId: params.space,
      taxonomyNodeId: params.taxonomy,
      locale: params.locale,
      displayLocale: preferredLocale,
      sourceTier: params.sourceTier,
      minimumSources,
      limit: pageSize,
      offset: (page - 1) * pageSize,
    }),
    getTaxonomyNodes(),
    getEntityTypes(),
    getSpaces(),
  ]);
  const totalPages = Math.max(1, Math.ceil(results.total / pageSize));
  const availableLocales = [
    ...new Set([
      ...results.facets.locales.map((item) => item.value),
      ...(params.locale ? [params.locale] : []),
      "zh-CN",
      "en",
      "la",
    ]),
  ];

  return (
    <main>
      <SiteHeader />
      <section className="search-page">
        <form action="/search" className="results-search">
          <span aria-hidden="true">⌕</span>
          <input
            aria-label="搜索百科"
            defaultValue={query}
            name="q"
            placeholder="搜索条目、别名、学名或型号"
          />
          {params.type ? (
            <input name="type" type="hidden" value={params.type} />
          ) : null}
          {params.space ? (
            <input name="space" type="hidden" value={params.space} />
          ) : null}
          {params.taxonomy ? (
            <input name="taxonomy" type="hidden" value={params.taxonomy} />
          ) : null}
          <button type="submit">搜索</button>
        </form>
        <div className="results-layout">
          <form action="/search" className="filter-panel">
            <input name="q" type="hidden" value={query} />
            <p className="eyebrow">DISCOVERY FILTERS</p>
            <h2>筛选结果</h2>
            <label>
              知识领域
              <select defaultValue={params.space ?? ""} name="space">
                <option value="">全部领域</option>
                {spaces.map((space) => (
                  <option key={space.id} value={space.id}>
                    {localize(space.name, preferredLocale)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              条目类型
              <select defaultValue={params.type ?? ""} name="type">
                <option value="">全部类型</option>
                {entityTypes
                  .filter(
                    (entityType) =>
                      !params.space || entityType.spaceId === params.space,
                  )
                  .map((entityType) => (
                    <option key={entityType.id} value={entityType.id}>
                      {localize(entityType.name, preferredLocale)}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              分类节点
              <select defaultValue={params.taxonomy ?? ""} name="taxonomy">
                <option value="">全部分类</option>
                {taxonomyNodes
                  .filter(
                    (node) => !params.space || node.spaceId === params.space,
                  )
                  .map((node) => (
                    <option key={node.id} value={node.id}>
                      {localize(node.name, preferredLocale)}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              内容语言
              <select defaultValue={params.locale ?? ""} name="locale">
                <option value="">全部语言</option>
                {availableLocales.map((locale) => (
                  <option key={locale} value={locale}>
                    {localeLabels[locale] ?? locale}
                  </option>
                ))}
              </select>
            </label>
            <label>
              来源等级
              <select defaultValue={params.sourceTier ?? ""} name="sourceTier">
                {sourceTierOptions.map(([value, label]) => (
                  <option key={value || "all"} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              最少来源数
              <select
                defaultValue={minimumSources ? String(minimumSources) : "0"}
                name="minimumSources"
              >
                <option value="0">不限</option>
                <option value="1">至少 1 条</option>
                <option value="2">至少 2 条</option>
                <option value="3">至少 3 条</option>
              </select>
            </label>
            <button type="submit">应用筛选</button>
            <a
              className="filter-reset"
              href={`/search?q=${encodeURIComponent(query)}`}
            >
              清除筛选
            </a>
            <div className="published-only-note">
              <b>✓ 公开索引</b>
              <span>仅包含已发布、具有固定修订版本的条目。</span>
            </div>
            <div className="filter-taxonomy">
              <strong>当前结果分布</strong>
              {results.facets.taxonomyNodes.length ? (
                results.facets.taxonomyNodes.slice(0, 8).map((facet) => (
                  <a
                    href={hrefForPage(
                      {
                        ...params,
                        taxonomy: facet.value,
                        page: undefined,
                      },
                      1,
                    )}
                    key={facet.value}
                  >
                    {facet.label} · {facet.count.toLocaleString()}
                  </a>
                ))
              ) : (
                <span>当前筛选没有分类分布。</span>
              )}
            </div>
          </form>
          <section className="results-panel">
            <header>
              <div>
                <p className="eyebrow">SEARCH RESULTS</p>
                <h1>{query ? `“${query}”` : "全部条目"}</h1>
              </div>
              <span>
                找到 {results.total.toLocaleString()} 个匹配条目 · 第 {page} 页
              </span>
            </header>
            {results.items.length === 0 ? (
              <div className="empty-results">
                <h2>没有找到可靠匹配</h2>
                <p>尝试使用别名、学名、型号，或移除筛选条件。</p>
                <a href="/categories">转到分类体系 →</a>
              </div>
            ) : (
              <div className="result-list">
                {results.items.map(({ entity, matchedField, matchedText }) => (
                  <a href={`/entry/${entity.ref.slug}`} key={entity.ref.id}>
                    <div className="result-mark">
                      {entity.ref.canonicalName.slice(0, 1)}
                    </div>
                    <div>
                      <h2>{localize(entity.names, preferredLocale)}</h2>
                      <em>
                        {entity.aliases.map((item) => item.value).join(" · ")}
                      </em>
                      <p>{localize(entity.description, preferredLocale)}</p>
                      <span>
                        {entity.citations.length} 条可靠来源 · 最近审核{" "}
                        {entity.revision.createdAt.slice(0, 10)}
                      </span>
                      <small className="match-reason">
                        {matchFieldLabels[matchedField] ?? matchedField}
                        {matchedText ? ` · ${matchedText}` : ""}
                      </small>
                    </div>
                    <b>查看条目 →</b>
                  </a>
                ))}
              </div>
            )}
            {results.total > pageSize ? (
              <nav aria-label="搜索结果分页" className="search-pagination">
                {page > 1 ? (
                  <a href={hrefForPage(params, page - 1)}>← 上一页</a>
                ) : (
                  <span />
                )}
                <span>
                  {page} / {totalPages}
                </span>
                {page < totalPages ? (
                  <a href={hrefForPage(params, page + 1)}>下一页 →</a>
                ) : (
                  <span />
                )}
              </nav>
            ) : null}
          </section>
        </div>
      </section>
    </main>
  );
}
