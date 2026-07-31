import type {
  AgentProposal,
  AuditEvent,
  CompatibilityReport,
  DraftValidation,
  EntityDraft,
  EntityType,
  KnowledgeEntity,
  KnowledgeSpace,
  ProductRef,
  Principal,
  SavedCollection,
  SavedCollectionItem,
  SchemaRegistry,
  SourceDefinition,
  SourceRegistryRecord,
  SourceSnapshot,
  TaxonomyNode,
  ViewDefinition,
} from "@hardatlas/contracts";

export interface HardAtlasClientOptions {
  accessToken?: () => string | undefined;
  headers?: () => HeadersInit;
}

export class HardAtlasClient {
  constructor(
    private readonly baseUrl: string,
    private readonly options: HardAtlasClientOptions = {},
  ) {}

  async health(): Promise<{ status: string; service: string }> {
    return this.get("/api/v1/health");
  }

  async me(): Promise<Principal> {
    return this.get("/api/v1/me");
  }

  async collections(): Promise<SavedCollection[]> {
    return this.get("/api/v1/collections");
  }

  async createCollection(input: {
    name: string;
    description?: string;
  }): Promise<SavedCollection> {
    return this.post("/api/v1/collections", input);
  }

  async removeCollection(collectionId: string): Promise<{ removed: boolean }> {
    return this.delete(
      `/api/v1/collections/${encodeURIComponent(collectionId)}`,
    );
  }

  async collectionItems(collectionId: string): Promise<SavedCollectionItem[]> {
    return this.get(
      `/api/v1/collections/${encodeURIComponent(collectionId)}/items`,
    );
  }

  async saveCollectionItem(
    collectionId: string,
    input: { entityId: string; note?: string; tags?: string[] },
  ): Promise<SavedCollectionItem> {
    return this.post(
      `/api/v1/collections/${encodeURIComponent(collectionId)}/items`,
      input,
    );
  }

  async removeCollectionItem(
    collectionId: string,
    entityId: string,
  ): Promise<{ removed: boolean }> {
    return this.delete(
      `/api/v1/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(entityId)}`,
    );
  }

  async auditEvents(limit = 100): Promise<AuditEvent[]> {
    return this.get(
      `/api/v1/audit-events?${new URLSearchParams({
        limit: String(limit),
      }).toString()}`,
    );
  }

  async spaces(): Promise<KnowledgeSpace[]> {
    return this.get("/api/v1/spaces");
  }

  async taxonomy(spaceId?: string): Promise<TaxonomyNode[]> {
    const query = spaceId
      ? `?${new URLSearchParams({ spaceId }).toString()}`
      : "";
    return this.get(`/api/v1/taxonomy${query}`);
  }

  async entities(typeId?: string): Promise<KnowledgeEntity[]> {
    const query = typeId
      ? `?${new URLSearchParams({ typeId }).toString()}`
      : "";
    return this.get(`/api/v1/entities${query}`);
  }

  async entityTypes(): Promise<EntityType[]> {
    return this.get("/api/v1/entity-types");
  }

  async schemaRegistry(): Promise<SchemaRegistry> {
    return this.get("/api/v1/schema-registry");
  }

  async sources(): Promise<SourceRegistryRecord[]> {
    return this.get("/api/v1/sources");
  }

  async registerSource(
    source: SourceDefinition,
  ): Promise<SourceRegistryRecord> {
    return this.post("/api/v1/sources", source);
  }

  async sourceSnapshots(sourceId: string): Promise<SourceSnapshot[]> {
    return this.get(
      `/api/v1/sources/${encodeURIComponent(sourceId)}/snapshots`,
    );
  }

  async validateEntityDraft(draft: EntityDraft): Promise<DraftValidation> {
    return this.post("/api/v1/entity-drafts/validate", draft);
  }

  async proposeEntityDraft(input: {
    proposalId: string;
    agentRunId: string;
    confidence: number;
    risk: "low" | "medium" | "high" | "critical";
    draft: EntityDraft;
  }): Promise<{ proposal: AgentProposal }> {
    return this.post("/api/v1/entity-drafts/proposals", input);
  }

  async entity(slug: string): Promise<KnowledgeEntity> {
    return this.get(`/api/v1/entities/${encodeURIComponent(slug)}`);
  }

  async viewDefinition(viewId: string): Promise<ViewDefinition> {
    return this.get(`/api/v1/view-definitions/${encodeURIComponent(viewId)}`);
  }

  async checkCompatibility(
    subjects: ProductRef[],
    facts: Record<string, unknown>,
  ): Promise<CompatibilityReport> {
    const response = await fetch(
      `${this.baseUrl}/api/v1/extensions/hardware/compatibility-checks`,
      {
        method: "POST",
        headers: this.requestHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({ subjects, facts }),
      },
    );
    if (!response.ok) {
      throw new Error(`Compatibility request failed: ${response.status}`);
    }
    return response.json() as Promise<CompatibilityReport>;
  }

  private async get<T>(path: string): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      headers: this.requestHeaders(),
    });
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${path}`);
    }
    return response.json() as Promise<T>;
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: this.requestHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${path}`);
    }
    return response.json() as Promise<T>;
  }

  private async delete<T>(path: string): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "DELETE",
      headers: this.requestHeaders(),
    });
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${path}`);
    }
    return response.json() as Promise<T>;
  }

  private requestHeaders(initial?: HeadersInit): Headers {
    const headers = new Headers(this.options.headers?.());
    new Headers(initial).forEach((value, key) => headers.set(key, value));
    headers.set("accept", "application/json");
    const accessToken = this.options.accessToken?.();
    if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);
    return headers;
  }
}

export type { paths as OpenApiPaths } from "./generated";
