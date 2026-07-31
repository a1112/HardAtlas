import type { EntityRevisionSummary } from "../lib/data-source";

export function RevisionHistory({
  slug,
  revisions,
  selectedRevisionId,
}: {
  slug: string;
  revisions: EntityRevisionSummary[];
  selectedRevisionId: string;
}) {
  return (
    <section
      className="revision-history"
      aria-labelledby="revision-history-title"
    >
      <header>
        <div>
          <p className="eyebrow">VERSION HISTORY</p>
          <h2 id="revision-history-title">版本历史</h2>
        </div>
        <span>{revisions.length} 个不可变修订</span>
      </header>
      <div>
        {revisions.map(({ revision, current }) => {
          const selected = revision.revisionId === selectedRevisionId;
          return (
            <a
              aria-current={selected ? "page" : undefined}
              href={
                current
                  ? `/entry/${encodeURIComponent(slug)}`
                  : `/entry/${encodeURIComponent(slug)}/revisions/${encodeURIComponent(
                      revision.revisionId,
                    )}`
              }
              key={revision.revisionId}
            >
              <strong>
                {revision.dataVersion}
                {current ? " · 当前" : ""}
              </strong>
              <span>{revision.revisionId}</span>
              <time dateTime={revision.createdAt}>
                {revision.createdAt.slice(0, 10)}
              </time>
            </a>
          );
        })}
      </div>
    </section>
  );
}
