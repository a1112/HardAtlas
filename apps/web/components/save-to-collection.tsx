"use client";

import type {
  EntityRef,
  SavedCollection,
  SavedCollectionItem,
} from "@hardatlas/contracts";
import { useEffect, useState } from "react";

interface Props {
  entityRef: EntityRef;
  dataVersion: string;
  revisionId: string;
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
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
      "detail" in (payload as object) &&
        typeof (payload as { detail?: unknown }).detail === "string"
        ? (payload as { detail: string }).detail
        : `请求失败 ${response.status}`,
    );
  }
  return payload as T;
}

export function SaveToCollection({
  entityRef,
  dataVersion,
  revisionId,
}: Props) {
  const [collections, setCollections] = useState<SavedCollection[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("保存时会固定当前条目版本");

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const session = await jsonRequest<{ authenticated: boolean }>(
          "/api/auth/session",
        );
        if (!session.authenticated || !active) return;
        const result = await jsonRequest<SavedCollection[]>(
          "/api/backend/api/v1/collections",
        );
        if (!active) return;
        setCollections(result);
        const first = result[0];
        if (!first) return;
        setSelectedId(first.id);
        const items = await jsonRequest<SavedCollectionItem[]>(
          `/api/backend/api/v1/collections/${encodeURIComponent(first.id)}/items`,
        );
        if (active) {
          setSaved(
            items.some(
              (item) =>
                item.entityId === entityRef.id &&
                item.entityRevisionId === revisionId,
            ),
          );
        }
      } catch {
        if (active) setNotice("登录后可保存到个人空间");
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [entityRef.id, revisionId]);

  async function save() {
    setBusy(true);
    try {
      const session = await jsonRequest<{ authenticated: boolean }>(
        "/api/auth/session",
      );
      if (!session.authenticated) {
        window.location.href = `/api/auth/login?returnTo=${encodeURIComponent(
          window.location.pathname,
        )}`;
        return;
      }
      let collectionId = selectedId;
      if (!collectionId) {
        const created = await jsonRequest<SavedCollection>(
          "/api/backend/api/v1/collections",
          {
            method: "POST",
            body: JSON.stringify({
              name: "我的收藏",
              description: "从 Atlas 条目页保存的版本化知识条目",
            }),
          },
        );
        collectionId = created.id;
        setCollections([created]);
        setSelectedId(created.id);
      }
      await jsonRequest<SavedCollectionItem>(
        `/api/backend/api/v1/collections/${encodeURIComponent(collectionId)}/items`,
        {
          method: "POST",
          body: JSON.stringify({
            entityId: entityRef.id,
            entityRevisionId: revisionId,
            note: "",
            tags: [],
          }),
        },
      );
      setSaved(true);
      setNotice(`已保存 ${dataVersion} 版本`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="save-control" aria-label="个人收藏">
      {collections.length > 1 ? (
        <select
          aria-label="目标收藏夹"
          value={selectedId}
          onChange={(event) => {
            setSelectedId(event.target.value);
            setSaved(false);
          }}
        >
          {collections.map((collection) => (
            <option key={collection.id} value={collection.id}>
              {collection.name}
            </option>
          ))}
        </select>
      ) : null}
      <button disabled={busy || saved} onClick={save}>
        {busy ? "正在保存…" : saved ? "✓ 已收藏当前版本" : "＋ 收藏条目"}
      </button>
      <span>{notice}</span>
    </section>
  );
}
