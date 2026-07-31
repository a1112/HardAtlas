import type {
  KnowledgeEntity,
  RelationshipTraversalItem,
  ViewBlock,
  ViewDefinition,
} from "@hardatlas/contracts";
import { localize } from "../lib/catalog";

function HeroBlock({
  entity,
  locale,
}: {
  entity: KnowledgeEntity;
  locale: string;
}) {
  return (
    <header className="entry-hero">
      <div>
        <p className="eyebrow">ENCYCLOPEDIA ENTRY</p>
        <h1>{localize(entity.names, locale)}</h1>
        <p className="entry-aliases">
          {entity.aliases.map((alias) => alias.value).join(" · ")}
        </p>
      </div>
      <div className="entry-state">
        <b>✓ 已审核</b>
        <span>{entity.revision.dataVersion}</span>
      </div>
    </header>
  );
}

function SummaryBlock({
  entity,
  locale,
}: {
  entity: KnowledgeEntity;
  locale: string;
}) {
  return (
    <section className="entry-section">
      {entity.sections
        .slice()
        .sort((a, b) => a.order - b.order)
        .map((section) => (
          <article key={section.id}>
            <h2>{localize(section.heading, locale)}</h2>
            <p>{localize(section.body, locale)}</p>
            <small>引用 {section.citationIds.length} 条来源</small>
          </article>
        ))}
    </section>
  );
}

function ClassificationBlock({
  block,
  locale,
}: {
  block: ViewBlock;
  locale: string;
}) {
  const rows = (block.config.rows ?? []) as string[][];
  return (
    <aside className="fact-panel">
      <h2>{block.title ? localize(block.title, locale) : "科学分类"}</h2>
      <dl>
        {rows.map(([rank, value]) => (
          <div key={`${rank}-${value}`}>
            <dt>{rank}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

function AttributeBlock({
  block,
  entity,
  locale,
}: {
  block: ViewBlock;
  entity: KnowledgeEntity;
  locale: string;
}) {
  return (
    <section className="attribute-panel">
      <h2>{block.title ? localize(block.title, locale) : "结构化属性"}</h2>
      <dl>
        {entity.claims.map((claim) => (
          <div key={claim.id}>
            <dt>{claim.attributeDefinitionId.replaceAll("_", " ")}</dt>
            <dd>{localize(claim.displayValue, locale)}</dd>
            <small>可信度 {(claim.confidence * 100).toFixed(0)}%</small>
          </div>
        ))}
      </dl>
    </section>
  );
}

function MapBlock({ block, locale }: { block: ViewBlock; locale: string }) {
  return (
    <section className="map-panel">
      <div>
        <p>亚洲中部高山生态区域</p>
        <span>喜马拉雅 · 青藏高原 · 帕米尔 · 天山</span>
      </div>
      <strong>
        {block.title ? localize(block.title, locale) : "地理分布"}
      </strong>
    </section>
  );
}

function TimelineBlock({
  block,
  locale,
}: {
  block: ViewBlock;
  locale: string;
}) {
  const events = (block.config.events ?? []) as string[][];
  return (
    <section className="timeline-panel">
      <h2>{block.title ? localize(block.title, locale) : "时间线"}</h2>
      {events.map(([date, label]) => (
        <article key={`${date}-${label}`}>
          <time>{date}</time>
          <p>{label}</p>
        </article>
      ))}
    </section>
  );
}

function CitationBlock({ entity }: { entity: KnowledgeEntity }) {
  return (
    <section className="citation-panel">
      <h2>来源与证据</h2>
      {entity.citations.map((citation, index) => (
        <article key={citation.id}>
          <b>{index + 1}</b>
          <div>
            <strong>{citation.sourceTitle}</strong>
            <span>
              {citation.sourceTier} · 获取于 {citation.retrievedAt.slice(0, 10)}
            </span>
          </div>
        </article>
      ))}
    </section>
  );
}

function relationshipLabel(item: RelationshipTraversalItem, locale: string) {
  const labels =
    item.direction === "outgoing"
      ? item.relationshipType.name
      : item.relationshipType.inverseName;
  return localize(labels, locale) || item.relationshipType.key;
}

function RelationshipBlock({
  entity,
  relationships,
  locale,
}: {
  entity: KnowledgeEntity;
  relationships: RelationshipTraversalItem[];
  locale: string;
}) {
  return (
    <section className="attribute-panel">
      <h2>关联知识</h2>
      {relationships.length ? (
        <dl>
          {relationships.map((item) => (
            <div key={item.relationship.id}>
              <dt>{relationshipLabel(item, locale)}</dt>
              <dd>
                <a href={`/entry/${item.neighbor.slug}`}>
                  {item.neighbor.canonicalName}
                </a>
              </dd>
              <small>
                {item.direction === "outgoing" ? "正向" : "反向"} · 可信度{" "}
                {(item.relationship.confidence * 100).toFixed(0)}%
              </small>
            </div>
          ))}
        </dl>
      ) : (
        <p>该版本尚未发布经过审核的关联关系。</p>
      )}
      <a
        className="relationship-explorer-link"
        href={`/relations/${entity.ref.slug}`}
      >
        打开关系浏览器 →
      </a>
    </section>
  );
}

function MediaGalleryBlock() {
  return (
    <section className="unsupported-block">
      当前条目尚无经过授权与审核的媒体资料。
    </section>
  );
}

function UnsupportedBlock({ block }: { block: ViewBlock }) {
  return (
    <section className="unsupported-block">
      当前前端尚不支持页面块：{block.type}
    </section>
  );
}

export function EntityView({
  entity,
  locale,
  relationships,
  view,
}: {
  entity: KnowledgeEntity;
  locale: string;
  relationships: RelationshipTraversalItem[];
  view: ViewDefinition;
}) {
  return (
    <>
      {view.blocks.map((block) => {
        switch (block.type) {
          case "hero":
            return <HeroBlock entity={entity} key={block.id} locale={locale} />;
          case "summary":
            return (
              <SummaryBlock entity={entity} key={block.id} locale={locale} />
            );
          case "classification":
            return (
              <ClassificationBlock
                block={block}
                key={block.id}
                locale={locale}
              />
            );
          case "attribute-table":
            return (
              <AttributeBlock
                block={block}
                entity={entity}
                key={block.id}
                locale={locale}
              />
            );
          case "map":
            return <MapBlock block={block} key={block.id} locale={locale} />;
          case "timeline":
            return (
              <TimelineBlock block={block} key={block.id} locale={locale} />
            );
          case "citations":
            return <CitationBlock entity={entity} key={block.id} />;
          case "relationship-list":
            return (
              <RelationshipBlock
                entity={entity}
                key={block.id}
                locale={locale}
                relationships={relationships}
              />
            );
          case "media-gallery":
            return <MediaGalleryBlock key={block.id} />;
          default:
            return <UnsupportedBlock block={block} key={block.id} />;
        }
      })}
    </>
  );
}
