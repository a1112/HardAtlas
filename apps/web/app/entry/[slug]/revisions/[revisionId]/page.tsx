import { notFound } from "next/navigation";
import { EntityView } from "../../../../../components/entity-view";
import { RevisionHistory } from "../../../../../components/revision-history";
import { SaveToCollection } from "../../../../../components/save-to-collection";
import { SiteHeader } from "../../../../../components/site-header";
import { localize } from "../../../../../lib/catalog";
import {
  getEntityRelationships,
  getEntityRevision,
  getEntityRevisionHistory,
  getTaxonomyNodes,
  getViewForEntity,
} from "../../../../../lib/data-source";
import { getPreferredLocale } from "../../../../../lib/locale";

export const dynamic = "force-dynamic";

export default async function HistoricalEntryPage({
  params,
}: {
  params: Promise<{ slug: string; revisionId: string }>;
}) {
  const { slug, revisionId } = await params;
  const entity = await getEntityRevision(slug, revisionId);
  if (!entity) notFound();
  const [view, taxonomyNodes, relationships, revisions, locale] =
    await Promise.all([
      getViewForEntity(entity),
      getTaxonomyNodes(),
      getEntityRelationships(entity, true),
      getEntityRevisionHistory(entity.ref.slug),
      getPreferredLocale(),
    ]);
  if (!view) notFound();
  const taxonomy = taxonomyNodes.find((node) =>
    entity.taxonomyNodeIds.includes(node.id),
  );

  return (
    <main>
      <SiteHeader />
      <article className="entry-page">
        <nav className="breadcrumbs" aria-label="面包屑">
          <a href="/">首页</a>
          <span>/</span>
          <a href={`/entry/${encodeURIComponent(entity.ref.slug)}`}>
            {localize(entity.names, locale)}
          </a>
          <span>/</span>
          <span>{taxonomy?.pathKeys.join(" / ")}</span>
        </nav>
        <aside className="historical-version-notice">
          <div>
            <strong>正在查看历史修订</strong>
            <span>
              {entity.revision.dataVersion} · {entity.revision.revisionId}
            </span>
          </div>
          <a href={`/entry/${encodeURIComponent(entity.ref.slug)}`}>
            返回当前版本 →
          </a>
        </aside>
        <EntityView
          entity={entity}
          locale={locale}
          relationships={relationships}
          view={view}
        />
        <SaveToCollection
          dataVersion={entity.revision.dataVersion}
          entityRef={entity.ref}
          revisionId={entity.revision.revisionId}
        />
        <RevisionHistory
          revisions={revisions}
          selectedRevisionId={entity.revision.revisionId}
          slug={entity.ref.slug}
        />
        <footer className="entry-footer">
          <span>不可变修订 {entity.revision.revisionId}</span>
          <span>Schema {entity.revision.schemaVersion}</span>
          <span>策略 {entity.revision.policyVersion}</span>
        </footer>
      </article>
    </main>
  );
}
