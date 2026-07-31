import { notFound } from "next/navigation";
import type { RelationshipTraversalItem } from "@hardatlas/contracts";
import { SiteHeader } from "../../../components/site-header";
import { localize } from "../../../lib/catalog";
import { getEntity, getEntityRelationships } from "../../../lib/data-source";
import { getPreferredLocale } from "../../../lib/locale";

export const dynamic = "force-dynamic";

function localizeRelation(item: RelationshipTraversalItem, locale: string) {
  const labels =
    item.direction === "outgoing"
      ? item.relationshipType.name
      : item.relationshipType.inverseName;
  return localize(labels, locale) || item.relationshipType.key;
}

export default async function RelationshipExplorerPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const entity = await getEntity(slug);
  if (!entity) notFound();
  const [relationships, locale] = await Promise.all([
    getEntityRelationships(entity),
    getPreferredLocale(),
  ]);
  const entityName = localize(entity.names, locale);

  return (
    <main>
      <SiteHeader />
      <article className="relation-explorer">
        <nav className="breadcrumbs" aria-label="面包屑">
          <a href="/">首页</a>
          <span>/</span>
          <a href={`/entry/${entity.ref.slug}`}>{entityName}</a>
          <span>/</span>
          <span>关系浏览器</span>
        </nav>
        <header>
          <div>
            <p>RELATIONSHIP EXPLORER</p>
            <h1>{entityName}的关系网络</h1>
            <span>
              当前修订中的受治理关系边；正向与反向名称由关系 Schema 定义
            </span>
          </div>
          <code>{entity.revision.dataVersion}</code>
        </header>

        <section className="relation-canvas">
          <article className="relation-center-node">
            <small>{entity.ref.typeId}</small>
            <strong>{entityName}</strong>
            <span>{entity.ref.slug}</span>
          </article>
          <div className="relation-neighbors">
            {relationships.length ? (
              relationships.map((item) => (
                <article
                  data-direction={item.direction}
                  key={item.relationship.id}
                >
                  <div className="relation-edge-label">
                    <b>{localizeRelation(item, locale)}</b>
                    <small>
                      {item.direction === "outgoing" ? "→" : "←"}{" "}
                      {(item.relationship.confidence * 100).toFixed(0)}%
                    </small>
                  </div>
                  <a href={`/entry/${item.neighbor.slug}`}>
                    <strong>{item.neighbor.canonicalName}</strong>
                    <span>{item.neighbor.typeId}</span>
                  </a>
                  <code>{item.relationshipType.schemaVersion}</code>
                </article>
              ))
            ) : (
              <div className="relation-empty">
                <strong>暂无已发布关系</strong>
                <span>
                  Agent
                  或编辑者提交的关系提案经证据校验、策略门和发布批次后会出现在这里。
                </span>
              </div>
            )}
          </div>
        </section>

        <footer>
          <span>节点 1 + {relationships.length}</span>
          <span>边 {relationships.length}</span>
          <span>关系数据来自当前不可变实体修订</span>
        </footer>
      </article>
    </main>
  );
}
