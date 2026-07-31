import type {
  EntityType,
  KnowledgeEntity,
  KnowledgeSpace,
  TaxonomyNode,
} from "@hardatlas/contracts";
import "@hardatlas/design-tokens/tokens.css";
import { invoke } from "@tauri-apps/api/core";
import {
  FormEvent,
  StrictMode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

interface BuildInfo {
  buildSha: string;
  environment: string;
  dataVersion: string;
  schemaVersion: string;
  policyVersion: string;
}

interface SearchResponse {
  query: string;
  total: number;
  items: Array<{
    entity: KnowledgeEntity;
    matchedText: string;
    matchedField: string;
    score: number;
  }>;
}

interface CachedEntitySummary {
  id: string;
  slug: string;
  typeId: string;
  canonicalName: string;
  dataVersion: string;
  savedAt: number;
}

interface DesktopStatus {
  databasePath: string;
  cachedEntityCount: number;
  pendingSyncCount: number;
}

type ReaderMode = "explore" | "categories" | "offline";

const apiBase =
  import.meta.env.VITE_HARDATLAS_API_URL ?? "http://127.0.0.1:8000";
const browserCacheKey = "atlas.desktop.cached-entities.v1";
const showHardwareExtension =
  (import.meta.env.VITE_HARDATLAS_ENABLE_HARDWARE_EXTENSION ?? "false") ===
  "true";

function localized(
  values: Array<{ locale: string; value: string }> | undefined,
  fallback = "未命名",
) {
  return (
    values?.find((item) => item.locale === "zh-CN")?.value ??
    values?.[0]?.value ??
    fallback
  );
}

function isTauriRuntime() {
  return "__TAURI_INTERNALS__" in window;
}

function readBrowserCache(): Record<string, KnowledgeEntity> {
  try {
    return JSON.parse(localStorage.getItem(browserCacheKey) ?? "{}") as Record<
      string,
      KnowledgeEntity
    >;
  } catch {
    return {};
  }
}

function browserSummaries(): CachedEntitySummary[] {
  return Object.values(readBrowserCache())
    .map((entity) => ({
      id: entity.ref.id,
      slug: entity.ref.slug,
      typeId: entity.ref.typeId,
      canonicalName: entity.ref.canonicalName,
      dataVersion: entity.revision.dataVersion,
      savedAt: Number(
        localStorage.getItem(`${browserCacheKey}:${entity.ref.id}:savedAt`) ??
          0,
      ),
    }))
    .sort((left, right) => right.savedAt - left.savedAt);
}

async function cacheForOffline(entity: KnowledgeEntity) {
  if (isTauriRuntime()) {
    return invoke<CachedEntitySummary>("cache_entity", {
      entityJson: JSON.stringify(entity),
    });
  }
  const cache = readBrowserCache();
  cache[entity.ref.id] = entity;
  localStorage.setItem(browserCacheKey, JSON.stringify(cache));
  const savedAt = Math.floor(Date.now() / 1000);
  localStorage.setItem(
    `${browserCacheKey}:${entity.ref.id}:savedAt`,
    String(savedAt),
  );
  return {
    id: entity.ref.id,
    slug: entity.ref.slug,
    typeId: entity.ref.typeId,
    canonicalName: entity.ref.canonicalName,
    dataVersion: entity.revision.dataVersion,
    savedAt,
  };
}

async function listOfflineEntities() {
  if (isTauriRuntime()) {
    return invoke<CachedEntitySummary[]>("list_cached_entities");
  }
  return browserSummaries();
}

async function readOfflineEntity(slug: string) {
  if (isTauriRuntime()) {
    const document = await invoke<string | null>("get_cached_entity", { slug });
    return document ? (JSON.parse(document) as KnowledgeEntity) : undefined;
  }
  return Object.values(readBrowserCache()).find(
    (entity) => entity.ref.slug === slug,
  );
}

async function removeOfflineEntity(id: string) {
  if (isTauriRuntime()) {
    return invoke<boolean>("remove_cached_entity", { id });
  }
  const cache = readBrowserCache();
  const removed = Boolean(cache[id]);
  delete cache[id];
  localStorage.setItem(browserCacheKey, JSON.stringify(cache));
  localStorage.removeItem(`${browserCacheKey}:${id}:savedAt`);
  return removed;
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Atlas API ${response.status}: ${path}`);
  }
  return (await response.json()) as T;
}

function App() {
  const [mode, setMode] = useState<ReaderMode>("explore");
  const [build, setBuild] = useState<BuildInfo>();
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [taxonomy, setTaxonomy] = useState<TaxonomyNode[]>([]);
  const [entityTypes, setEntityTypes] = useState<EntityType[]>([]);
  const [selectedSpaceId, setSelectedSpaceId] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeEntity[]>([]);
  const [selected, setSelected] = useState<KnowledgeEntity>();
  const [offline, setOffline] = useState<CachedEntitySummary[]>([]);
  const [status, setStatus] = useState<DesktopStatus>();
  const [connection, setConnection] = useState<
    "connecting" | "online" | "offline"
  >("connecting");
  const [notice, setNotice] = useState("正在连接 Atlas Knowledge API…");
  const [busy, setBusy] = useState(false);
  const [extensionStatus, setExtensionStatus] = useState("未启用");

  const refreshOffline = useCallback(async () => {
    const cached = await listOfflineEntities();
    setOffline(cached);
    if (isTauriRuntime()) {
      setStatus(await invoke<DesktopStatus>("desktop_status"));
    } else {
      setStatus({
        databasePath: "浏览器预览存储",
        cachedEntityCount: cached.length,
        pendingSyncCount: 0,
      });
    }
  }, []);

  useEffect(() => {
    void Promise.all([
      apiGet<BuildInfo>("/api/v1/build"),
      apiGet<KnowledgeSpace[]>("/api/v1/spaces"),
      apiGet<TaxonomyNode[]>("/api/v1/taxonomy"),
      apiGet<EntityType[]>("/api/v1/entity-types"),
      refreshOffline(),
    ])
      .then(([nextBuild, nextSpaces, nextTaxonomy, nextTypes]) => {
        setBuild(nextBuild);
        setSpaces(nextSpaces);
        setTaxonomy(nextTaxonomy);
        setEntityTypes(nextTypes);
        setSelectedSpaceId(nextSpaces[0]?.id ?? "");
        setConnection("online");
        setNotice("已连接版本化知识库");
      })
      .catch((error: unknown) => {
        setConnection("offline");
        setNotice(
          error instanceof Error
            ? `知识 API 不可用，已切换离线阅读：${error.message}`
            : "知识 API 不可用，已切换离线阅读",
        );
        setMode("offline");
        void refreshOffline();
      });
  }, [refreshOffline]);

  const selectedSpace = spaces.find((space) => space.id === selectedSpaceId);
  const visibleTaxonomy = useMemo(
    () => taxonomy.filter((node) => node.spaceId === selectedSpaceId),
    [selectedSpaceId, taxonomy],
  );
  const typeNames = useMemo(
    () =>
      Object.fromEntries(
        entityTypes.map((type) => [type.id, localized(type.name, type.key)]),
      ),
    [entityTypes],
  );

  async function search(event?: FormEvent) {
    event?.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setNotice(`正在检索“${query.trim()}”…`);
    try {
      const response = await apiGet<SearchResponse>(
        `/api/v1/search?q=${encodeURIComponent(query.trim())}`,
      );
      setResults(response.items.map((item) => item.entity));
      setSelected(response.items[0]?.entity);
      setMode("explore");
      setConnection("online");
      setNotice(`找到 ${response.total} 个结果`);
    } catch (error) {
      const cached = await listOfflineEntities();
      const normalized = query.trim().toLocaleLowerCase();
      const matches = cached.filter((item) =>
        item.canonicalName.toLocaleLowerCase().includes(normalized),
      );
      setOffline(matches);
      setMode("offline");
      setConnection("offline");
      setNotice(
        error instanceof Error
          ? `在线检索失败，显示 ${matches.length} 个离线匹配`
          : "在线检索失败",
      );
    } finally {
      setBusy(false);
    }
  }

  async function openOffline(item: CachedEntitySummary) {
    setBusy(true);
    try {
      const entity = await readOfflineEntity(item.slug);
      if (!entity) throw new Error("离线快照不存在");
      setSelected(entity);
      setNotice(`正在阅读离线版本 ${item.dataVersion}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "离线条目读取失败");
    } finally {
      setBusy(false);
    }
  }

  async function saveSelected() {
    if (!selected) return;
    setBusy(true);
    try {
      await cacheForOffline(selected);
      await refreshOffline();
      setNotice(`已保存“${selected.ref.canonicalName}”供离线阅读`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "离线保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function removeCached(item: CachedEntitySummary) {
    await removeOfflineEntity(item.id);
    await refreshOffline();
    if (selected?.ref.id === item.id) setSelected(undefined);
    setNotice(`已移除“${item.canonicalName}”的离线快照`);
  }

  async function runHardwareExtension() {
    setExtensionStatus("正在调用可选硬件扩展…");
    try {
      setExtensionStatus(await invoke<string>("start_safe_scan"));
    } catch {
      setExtensionStatus("浏览器预览模式：本地扩展桥未连接");
    }
  }

  return (
    <main className="reader-shell">
      <aside>
        <div className="brand">
          A <span>ATLAS</span>
        </div>
        <p className="workspace-label">专业知识百科</p>
        <nav>
          <button
            className={mode === "explore" ? "active" : ""}
            onClick={() => setMode("explore")}
          >
            探索与搜索
          </button>
          <button
            className={mode === "categories" ? "active" : ""}
            onClick={() => setMode("categories")}
          >
            分类体系
          </button>
          <button
            className={mode === "offline" ? "active" : ""}
            onClick={() => {
              setMode("offline");
              void refreshOffline();
            }}
          >
            离线知识包 <b>{offline.length}</b>
          </button>
        </nav>
        <section className="extension">
          <p>可选领域扩展</p>
          {showHardwareExtension ? (
            <>
              <button onClick={runHardwareExtension}>硬件识别（可选）</button>
              <small>{extensionStatus}</small>
            </>
          ) : (
            <small>未启用：默认入口保持百科搜索、分类与离线阅读</small>
          )}
        </section>
      </aside>

      <section className="reader-content">
        <header>
          <div>
            <i data-status={connection} />
            <span>
              {connection === "online"
                ? "知识服务在线"
                : connection === "offline"
                  ? "离线模式"
                  : "正在连接"}
            </span>
          </div>
          <b>
            数据版本{" "}
            {build?.dataVersion ?? selected?.revision.dataVersion ?? "本地"}
          </b>
        </header>

        <form className="desktop-search" onSubmit={search}>
          <span>⌕</span>
          <input
            aria-label="搜索知识"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索条目、别名、学名、型号或概念"
            value={query}
          />
          <button disabled={busy || !query.trim()}>搜索</button>
        </form>
        <p className="desktop-notice">{notice}</p>

        {mode === "explore" && (
          <div className="desktop-workspace">
            <section className="result-panel">
              <div className="panel-heading">
                <div>
                  <p>KNOWLEDGE RESULTS</p>
                  <h1>{results.length ? "检索结果" : "从一个概念开始"}</h1>
                </div>
                <small>{results.length} 个条目</small>
              </div>
              {results.length === 0 ? (
                <div className="empty-state">
                  <strong>Atlas 桌面阅读器已连接动态百科内核</strong>
                  <span>
                    搜索条目后可查看结构化属性、证据和版本，并保存为离线快照。
                  </span>
                </div>
              ) : (
                <div className="desktop-results">
                  {results.map((entity) => (
                    <button
                      data-active={selected?.ref.id === entity.ref.id}
                      key={entity.ref.id}
                      onClick={() => setSelected(entity)}
                      type="button"
                    >
                      <div>
                        <strong>{entity.ref.canonicalName}</strong>
                        <span>
                          {typeNames[entity.ref.typeId] ?? entity.ref.typeId}
                        </span>
                      </div>
                      <p>{localized(entity.description, "暂无摘要")}</p>
                      <small>
                        {entity.citations.length} 个来源 ·{" "}
                        {entity.revision.dataVersion}
                      </small>
                    </button>
                  ))}
                </div>
              )}
            </section>
            <EntityReader
              busy={busy}
              entity={selected}
              offline={offline.some((item) => item.id === selected?.ref.id)}
              onCache={() => void saveSelected()}
              typeName={
                selected
                  ? (typeNames[selected.ref.typeId] ?? selected.ref.typeId)
                  : undefined
              }
            />
          </div>
        )}

        {mode === "categories" && (
          <div className="category-browser">
            <aside>
              <p>KNOWLEDGE SPACES</p>
              <h1>分类体系</h1>
              {spaces.map((space) => (
                <button
                  data-active={space.id === selectedSpaceId}
                  key={space.id}
                  onClick={() => setSelectedSpaceId(space.id)}
                >
                  <strong>{localized(space.name)}</strong>
                  <span>{localized(space.description, "")}</span>
                </button>
              ))}
            </aside>
            <section>
              <div className="panel-heading">
                <div>
                  <p>DYNAMIC TAXONOMY</p>
                  <h2>{localized(selectedSpace?.name)}</h2>
                </div>
                <code>{selectedSpace?.id}</code>
              </div>
              <div className="taxonomy-grid">
                {visibleTaxonomy.length === 0 && (
                  <div className="empty-state">
                    <strong>该空间尚未发布根分类</strong>
                    <span>新分类可由 Domain Pack 提案动态加入。</span>
                  </div>
                )}
                {visibleTaxonomy.map((node) => (
                  <article key={node.id}>
                    <small>{node.pathKeys.join(" / ")}</small>
                    <strong>{localized(node.name)}</strong>
                    <span>{node.entityCount.toLocaleString()} 个条目</span>
                    <code>{node.id}</code>
                  </article>
                ))}
              </div>
            </section>
          </div>
        )}

        {mode === "offline" && (
          <div className="offline-workspace">
            <section className="offline-list">
              <div className="panel-heading">
                <div>
                  <p>SQLITE SNAPSHOTS</p>
                  <h1>离线知识包</h1>
                </div>
                <small>
                  {status?.cachedEntityCount ?? offline.length} 个条目
                </small>
              </div>
              {offline.length === 0 ? (
                <div className="empty-state">
                  <strong>尚未保存离线条目</strong>
                  <span>在线阅读条目时选择“保存离线快照”。</span>
                </div>
              ) : (
                offline.map((item) => (
                  <article key={item.id}>
                    <button onClick={() => void openOffline(item)}>
                      <strong>{item.canonicalName}</strong>
                      <span>{typeNames[item.typeId] ?? item.typeId}</span>
                      <small>{item.dataVersion}</small>
                    </button>
                    <button
                      aria-label={`移除 ${item.canonicalName}`}
                      className="remove"
                      onClick={() => void removeCached(item)}
                    >
                      ×
                    </button>
                  </article>
                ))
              )}
              <footer>
                <span>{status?.databasePath}</span>
                <b>{status?.pendingSyncCount ?? 0} 个待同步操作</b>
              </footer>
            </section>
            <EntityReader
              busy={busy}
              entity={selected}
              offline
              onCache={() => void saveSelected()}
              typeName={
                selected
                  ? (typeNames[selected.ref.typeId] ?? selected.ref.typeId)
                  : undefined
              }
            />
          </div>
        )}
      </section>
    </main>
  );
}

function EntityReader({
  busy,
  entity,
  offline,
  onCache,
  typeName,
}: {
  busy: boolean;
  entity: KnowledgeEntity | undefined;
  offline: boolean;
  onCache: () => void;
  typeName: string | undefined;
}) {
  if (!entity) {
    return (
      <aside className="entity-reader empty">
        <strong>条目阅读区</strong>
        <span>选择一个搜索结果或离线快照。</span>
      </aside>
    );
  }
  return (
    <aside className="entity-reader">
      <header>
        <div>
          <span>{typeName}</span>
          <h2>{entity.ref.canonicalName}</h2>
          <small>
            {entity.aliases.map((alias) => alias.value).join(" · ") || "无别名"}
          </small>
        </div>
        <b>
          {entity.publicationStatus === "published"
            ? "已发布"
            : entity.publicationStatus}
        </b>
      </header>
      <p className="entity-summary">
        {localized(entity.description, "暂无摘要")}
      </p>
      <dl>
        {entity.claims.slice(0, 6).map((claim) => (
          <div key={claim.id}>
            <dt>{claim.attributeDefinitionId}</dt>
            <dd>
              {localized(claim.displayValue, String(claim.originalValue))}
            </dd>
          </div>
        ))}
      </dl>
      {entity.sections.slice(0, 2).map((section) => (
        <section key={section.id}>
          <h3>{localized(section.heading)}</h3>
          <p>{localized(section.body, "")}</p>
        </section>
      ))}
      <div className="evidence-summary">
        <strong>来源与版本</strong>
        <span>{entity.citations.length} 个来源</span>
        <span>{entity.revision.dataVersion}</span>
        <span>{entity.revision.schemaVersion}</span>
      </div>
      <button disabled={busy || offline} onClick={onCache}>
        {offline ? "✓ 已保存离线快照" : "保存离线快照"}
      </button>
    </aside>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
