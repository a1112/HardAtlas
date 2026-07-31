import { notFound } from "next/navigation";
import { EntityView } from "../../../components/entity-view";
import { SaveToCollection } from "../../../components/save-to-collection";
import { RevisionHistory } from "../../../components/revision-history";
import { SiteHeader } from "../../../components/site-header";
import {
  getEntity,
  getEntityRelationships,
  getEntityRevisionHistory,
  getTaxonomyNodes,
  getViewForEntity,
} from "../../../lib/data-source";
import { getPreferredLocale } from "../../../lib/locale";

export const dynamic = "force-dynamic";

export default async function EntryPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const entity = await getEntity(slug);
  if (!entity) notFound();
  const [view, taxonomyNodes, relationships, revisions, locale] =
    await Promise.all([
      getViewForEntity(entity),
      getTaxonomyNodes(),
      getEntityRelationships(entity),
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
          <a href="/categories">分类</a>
          <span>/</span>
          <span>{taxonomy?.pathKeys.join(" / ")}</span>
        </nav>
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
          <span>修订 {entity.revision.revisionId}</span>
          <span>Schema {entity.revision.schemaVersion}</span>
          <span>策略 {entity.revision.policyVersion}</span>
        </footer>
      </article>
    </main>
  );
}
