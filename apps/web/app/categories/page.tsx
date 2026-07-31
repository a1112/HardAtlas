import type { TaxonomyNode } from "@hardatlas/contracts";
import { SiteHeader } from "../../components/site-header";
import { localize } from "../../lib/catalog";
import {
  getSpaces,
  getTaxonomyNodes,
  searchDiscovery,
} from "../../lib/data-source";
import { getPreferredLocale } from "../../lib/locale";

export const dynamic = "force-dynamic";

function positiveInteger(value: string | undefined, fallback: number) {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function TaxonomyBranch({
  node,
  nodes,
  selectedId,
  locale,
  depth = 0,
}: {
  node: TaxonomyNode;
  nodes: TaxonomyNode[];
  selectedId: string | undefined;
  locale: string;
  depth?: number;
}) {
  const children = nodes.filter((candidate) =>
    candidate.parentIds.includes(node.id),
  );
  return (
    <div style={{ paddingInlineStart: `${depth * 12}px` }}>
      <a
        aria-current={selectedId === node.id ? "page" : undefined}
        href={`/categories?space=${encodeURIComponent(
          node.spaceId,
        )}&taxonomy=${encodeURIComponent(node.id)}`}
      >
        <span>{localize(node.name, locale)}</span>
        <b>{node.entityCount.toLocaleString()}</b>
      </a>
      {children.map((child) => (
        <TaxonomyBranch
          depth={depth + 1}
          key={child.id}
          node={child}
          nodes={nodes}
          selectedId={selectedId}
          locale={locale}
        />
      ))}
    </div>
  );
}

export default async function CategoriesPage({
  searchParams,
}: {
  searchParams: Promise<{
    page?: string;
    space?: string;
    taxonomy?: string;
  }>;
}) {
  const params = await searchParams;
  const entryPage = positiveInteger(params.page, 1);
  const entryPageSize = 12;
  const taxonomyId =
    params.taxonomy && params.taxonomy.trim() ? params.taxonomy : undefined;
  const spaceId =
    params.space && params.space.trim() ? params.space : undefined;
  const [spaces, taxonomyNodes, locale] = await Promise.all([
    getSpaces(),
    getTaxonomyNodes(),
    getPreferredLocale(),
  ]);
  const matchedTaxonomyById = taxonomyNodes.find(
    (node) => node.id === taxonomyId,
  );
  const matchedSpaceByTaxonomy = matchedTaxonomyById
    ? spaces.find((space) => space.id === matchedTaxonomyById.spaceId)
    : undefined;
  const selectedSpace =
    spaces.find((space) => space.id === spaceId) ??
    matchedSpaceByTaxonomy ??
    spaces[0];
  const resolvedTaxonomyId =
    taxonomyId &&
    taxonomyNodes.find(
      (node) =>
        node.id === taxonomyId &&
        (!selectedSpace || node.spaceId === selectedSpace.id),
    )?.id;
  const discovery = await searchDiscovery({
    query: "",
    spaceId: selectedSpace?.id ?? spaceId,
    taxonomyNodeId: resolvedTaxonomyId,
    limit: entryPageSize,
    offset: (entryPage - 1) * entryPageSize,
  });
  const selectedNode = selectedSpace?.id
    ? (taxonomyNodes.find(
        (node) =>
          node.id === resolvedTaxonomyId && node.spaceId === selectedSpace.id,
      ) ??
      taxonomyNodes.find(
        (node) =>
          node.spaceId === selectedSpace.id &&
          (node.parentIds.length === 0 ||
            selectedSpace?.rootTaxonomyNodeIds.includes(node.id)),
      ) ??
      taxonomyNodes.find((node) => node.spaceId === selectedSpace.id) ??
      taxonomyNodes[0])
    : undefined;

  const selectedSpaceNodes = taxonomyNodes.filter(
    (node) => node.spaceId === selectedSpace?.id,
  );
  const roots = selectedSpaceNodes.filter(
    (node) =>
      selectedSpace?.rootTaxonomyNodeIds.includes(node.id) ||
      node.parentIds.length === 0,
  );
  const children = selectedNode
    ? selectedSpaceNodes.filter((node) =>
        node.parentIds.includes(selectedNode.id),
      )
    : roots;
  const parents = selectedNode
    ? selectedNode.parentIds
        .map((parentId) =>
          selectedSpaceNodes.find((node) => node.id === parentId),
        )
        .filter((node): node is TaxonomyNode => Boolean(node))
    : [];
  const relatedCount = discovery.total;
  const relatedEntities = discovery.items.map((item) => item.entity);
  const totalPages = Math.max(1, Math.ceil(relatedCount / entryPageSize));
  const rangeStart = relatedCount ? (entryPage - 1) * entryPageSize + 1 : 0;
  const rangeEnd = Math.min(entryPage * entryPageSize, relatedCount);

  const hasNextPage = entryPage < totalPages;
  const hasPrevPage = entryPage > 1;
  const pageHref = (page: number) => {
    const query = new URLSearchParams();
    if (spaceId) query.set("space", spaceId);
    if (taxonomyId) query.set("taxonomy", taxonomyId);
    if (page > 1) query.set("page", String(page));
    return `/categories?${query.toString()}`;
  };
  const searchHref = selectedNode
    ? `/search?space=${encodeURIComponent(selectedNode.spaceId)}&taxonomy=${encodeURIComponent(selectedNode.id)}`
    : "/search";

  return (
    <main>
      <SiteHeader />
      <section className="categories-page">
        <header>
          <p className="eyebrow">DYNAMIC TAXONOMY</p>
          <h1>分类体系</h1>
          <p>分类由版本化领域包动态扩展，可拥有多个父分类和等价路径。</p>
          <nav aria-label="知识领域" className="space-tabs">
            {spaces.map((space) => (
              <a
                aria-current={
                  selectedSpace?.id === space.id ? "page" : undefined
                }
                href={`/categories?space=${encodeURIComponent(space.id)}`}
                key={space.id}
              >
                <span>{localize(space.name, locale)}</span>
                <small>
                  {
                    taxonomyNodes.filter((node) => node.spaceId === space.id)
                      .length
                  }{" "}
                  个分类节点
                </small>
              </a>
            ))}
          </nav>
        </header>
        <div className="taxonomy-layout">
          <aside className="taxonomy-tree">
            <strong>{localize(selectedSpace?.name ?? [], locale)}</strong>
            <p>{localize(selectedSpace?.description ?? [], locale)}</p>
            {roots.length ? (
              roots.map((root) => (
                <TaxonomyBranch
                  key={root.id}
                  node={root}
                  nodes={selectedSpaceNodes}
                  selectedId={selectedNode?.id}
                  locale={locale}
                />
              ))
            ) : (
              <span>该领域尚未发布分类节点。</span>
            )}
          </aside>
          <section className="taxonomy-focus">
            {selectedNode ? (
              <>
                <div className="taxonomy-breadcrumb">
                  {selectedNode.pathKeys.map((path, index) => (
                    <span key={`${path}-${index}`}>
                      {index ? " / " : ""}
                      {path}
                    </span>
                  ))}
                </div>
                <header>
                  <div>
                    <p className="eyebrow">SELECTED TAXONOMY NODE</p>
                    <h2>{localize(selectedNode.name, locale)}</h2>
                    <span>
                      {selectedNode.id} · Schema 数据驱动 ·{" "}
                      {selectedNode.parentIds.length > 1
                        ? `${selectedNode.parentIds.length} 个父分类`
                        : "单一路径"}
                    </span>
                  </div>
                  <b>{selectedNode.entityCount.toLocaleString()} 个条目</b>
                </header>
                <div className="taxonomy-meta-grid">
                  <article>
                    <span>直接子分类</span>
                    <strong>{selectedNode.childCount.toLocaleString()}</strong>
                  </article>
                  <article>
                    <span>当前已加载实体</span>
                    <strong>{relatedCount.toLocaleString()}</strong>
                  </article>
                  <article>
                    <span>所属知识领域</span>
                    <strong>
                      {localize(selectedSpace?.name ?? [], locale)}
                    </strong>
                  </article>
                </div>
                {parents.length ? (
                  <div className="taxonomy-relations">
                    <strong>父分类</strong>
                    {parents.map((parent) => (
                      <a
                        href={`/categories?space=${encodeURIComponent(
                          parent.spaceId,
                        )}&taxonomy=${encodeURIComponent(parent.id)}`}
                        key={parent.id}
                      >
                        {localize(parent.name, locale)}
                      </a>
                    ))}
                  </div>
                ) : null}
                <section className="taxonomy-children">
                  <div>
                    <h3>下级分类</h3>
                    <span>由领域包和受治理 Schema 提案持续扩展</span>
                  </div>
                  {children.length ? (
                    <div className="taxonomy-card-grid">
                      {children.map((child) => (
                        <a
                          href={`/categories?space=${encodeURIComponent(
                            child.spaceId,
                          )}&taxonomy=${encodeURIComponent(child.id)}`}
                          key={child.id}
                        >
                          <strong>{localize(child.name, locale)}</strong>
                          <span>
                            {child.childCount} 个子分类 ·{" "}
                            {child.entityCount.toLocaleString()} 个条目
                          </span>
                        </a>
                      ))}
                    </div>
                  ) : (
                    <p>当前节点没有直接子分类，可从条目关系继续探索。</p>
                  )}
                </section>
                <section className="taxonomy-entries">
                  <div>
                    <h3>代表条目</h3>
                    <a href={searchHref}>查看该分类全部条目 →</a>
                  </div>
                  {relatedEntities.length ? (
                    <div className="taxonomy-entry-grid">
                      {relatedEntities.map((entity) => (
                        <a
                          href={`/entry/${entity.ref.slug}`}
                          key={entity.ref.id}
                        >
                          <b>{entity.ref.canonicalName.slice(0, 1)}</b>
                          <span>
                            <strong>{localize(entity.names, locale)}</strong>
                            <small>
                              {entity.aliases[0]?.value ?? entity.ref.typeId}
                            </small>
                          </span>
                        </a>
                      ))}
                    </div>
                  ) : (
                    <div className="taxonomy-empty">
                      该节点的框架已经发布，内容将由来源采集与 Agent
                      治理流程逐步补充。
                    </div>
                  )}
                  {relatedCount > entryPageSize ? (
                    <nav
                      aria-label="分类条目分页"
                      className="search-pagination"
                    >
                      {hasPrevPage ? (
                        <a href={pageHref(entryPage - 1)}>← 上一页</a>
                      ) : (
                        <span />
                      )}
                      <span>
                        {entryPage} / {totalPages} · 当前{" "}
                        {rangeStart.toLocaleString()}-
                        {rangeEnd.toLocaleString()} /{" "}
                        {relatedCount.toLocaleString()} 条
                      </span>
                      {hasNextPage ? (
                        <a href={pageHref(entryPage + 1)}>下一页 →</a>
                      ) : (
                        <span />
                      )}
                    </nav>
                  ) : null}
                </section>
              </>
            ) : (
              <div className="taxonomy-empty">
                该知识领域尚未发布分类节点，可在 Schema 中心新增领域包。
              </div>
            )}
          </section>
        </div>
      </section>
    </main>
  );
}
