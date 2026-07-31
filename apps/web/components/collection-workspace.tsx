"use client";

import type {
  SavedCollection,
  SavedCollectionItem,
} from "@hardatlas/contracts";
import { FormEvent, useEffect, useState } from "react";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      accept: "application/json",
      ...(init?.body ? { "content-type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const payload = (await response.json()) as T | { detail?: string };
  if (!response.ok) {
    throw new Error(
      (payload as { detail?: string }).detail ?? `请求失败 ${response.status}`,
    );
  }
  return payload as T;
}

export function CollectionWorkspace() {
  const [collections, setCollections] = useState<SavedCollection[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [items, setItems] = useState<SavedCollectionItem[]>([]);
  const [name, setName] = useState("");
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [notice, setNotice] = useState("正在连接个人空间…");

  async function loadCollections(preferredId?: string) {
    const result = await request<SavedCollection[]>(
      "/api/backend/api/v1/collections",
    );
    setCollections(result);
    const target =
      preferredId && result.some((item) => item.id === preferredId)
        ? preferredId
        : (result[0]?.id ?? "");
    setSelectedId(target);
    if (target) {
      const savedItems = await request<SavedCollectionItem[]>(
        `/api/backend/api/v1/collections/${encodeURIComponent(target)}/items`,
      );
      setItems(savedItems);
    } else {
      setItems([]);
    }
    setNotice(result.length ? "收藏已同步" : "创建收藏夹开始整理条目");
  }

  useEffect(() => {
    async function load() {
      try {
        const session = await request<{ authenticated: boolean }>(
          "/api/auth/session",
        );
        setAuthenticated(session.authenticated);
        if (session.authenticated) await loadCollections();
        else setNotice("登录后可访问版本化个人收藏");
      } catch (error) {
        setNotice(error instanceof Error ? error.message : "个人空间不可用");
      }
    }
    void load();
  }, []);

  async function choose(collectionId: string) {
    setSelectedId(collectionId);
    setNotice("正在读取收藏…");
    try {
      const result = await request<SavedCollectionItem[]>(
        `/api/backend/api/v1/collections/${encodeURIComponent(collectionId)}/items`,
      );
      setItems(result);
      setNotice("收藏已同步");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "读取失败");
    }
  }

  async function createCollection(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    try {
      const created = await request<SavedCollection>(
        "/api/backend/api/v1/collections",
        {
          method: "POST",
          body: JSON.stringify({ name: name.trim(), description: "" }),
        },
      );
      setName("");
      await loadCollections(created.id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建失败");
    }
  }

  async function removeItem(item: SavedCollectionItem) {
    await request<{ removed: boolean }>(
      `/api/backend/api/v1/collections/${encodeURIComponent(
        item.collectionId,
      )}/items/${encodeURIComponent(item.entityId)}`,
      { method: "DELETE" },
    );
    await choose(item.collectionId);
  }

  async function removeCollection(collectionId: string) {
    await request<{ removed: boolean }>(
      `/api/backend/api/v1/collections/${encodeURIComponent(collectionId)}`,
      { method: "DELETE" },
    );
    await loadCollections();
  }

  if (authenticated === false) {
    return (
      <section className="collection-login">
        <p>PRIVATE KNOWLEDGE SPACE</p>
        <h1>把可信条目保存为自己的知识集</h1>
        <span>
          收藏会固定数据版本与修订号，不会因公开条目更新而失去上下文。
        </span>
        <a href="/api/auth/login?returnTo=/collections">登录并进入个人空间</a>
      </section>
    );
  }

  return (
    <div className="collection-layout">
      <aside className="collection-sidebar">
        <p>PERSONAL COLLECTIONS</p>
        <h1>我的收藏</h1>
        <span>{notice}</span>
        <form onSubmit={createCollection}>
          <input
            aria-label="新收藏夹名称"
            maxLength={120}
            placeholder="新收藏夹名称"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <button disabled={!name.trim()}>创建</button>
        </form>
        <nav aria-label="收藏夹">
          {collections.map((collection) => (
            <button
              className={collection.id === selectedId ? "active" : ""}
              key={collection.id}
              onClick={() => choose(collection.id)}
            >
              <strong>{collection.name}</strong>
              <span>{collection.description || "私人知识集合"}</span>
            </button>
          ))}
        </nav>
      </aside>
      <section className="collection-content">
        <header>
          <div>
            <p>VERSION-PINNED ENTRIES</p>
            <h2>
              {collections.find((item) => item.id === selectedId)?.name ??
                "尚无收藏夹"}
            </h2>
          </div>
          {selectedId ? (
            <button
              className="collection-delete"
              onClick={() => removeCollection(selectedId)}
            >
              删除收藏夹
            </button>
          ) : null}
        </header>
        {items.length ? (
          <div className="saved-entry-grid">
            {items.map((item) => (
              <article key={item.entityId}>
                <span>{item.entityRef.typeId}</span>
                <h3>
                  <a
                    href={`/entry/${encodeURIComponent(
                      item.entityRef.slug,
                    )}/revisions/${encodeURIComponent(item.entityRevisionId)}`}
                  >
                    {item.entityRef.canonicalName}
                  </a>
                </h3>
                <dl>
                  <div>
                    <dt>数据版本</dt>
                    <dd>{item.dataVersion}</dd>
                  </div>
                  <div>
                    <dt>实体修订</dt>
                    <dd>{item.entityRevisionId}</dd>
                  </div>
                </dl>
                {item.note ? <p>{item.note}</p> : null}
                <footer>
                  <time>
                    {new Date(item.savedAt).toLocaleDateString("zh-CN")}
                  </time>
                  <button onClick={() => removeItem(item)}>移除</button>
                </footer>
              </article>
            ))}
          </div>
        ) : (
          <div className="collection-empty">
            <strong>还没有保存的条目</strong>
            <span>浏览百科条目并选择“收藏条目”，所选修订会固定在这里。</span>
            <a href="/search?q=">浏览全部条目</a>
          </div>
        )}
      </section>
    </div>
  );
}
