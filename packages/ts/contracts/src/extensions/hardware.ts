/** Optional computer-hardware extension contracts. */
export type CompatibilityStatus =
  "compatible" | "conditional" | "incompatible" | "unknown";

export interface ProductRef {
  modelId: string;
  skuId?: string;
  revisionId?: string;
}

export interface VersionContext {
  dataVersion: string;
  ruleVersion: string;
}

export interface EvidenceRef {
  id: string;
  title: string;
  sourceLevel: "A" | "B" | "C" | "D";
  url?: string;
  retrievedAt: string;
  appliesTo: ProductRef;
}

export interface SpecificationValue {
  definitionId: string;
  originalValue: string;
  normalizedValue?: number | string | boolean;
  displayValue: string;
  unit?: string;
  appliesTo: ProductRef;
  evidence: EvidenceRef[];
  confidence: number;
}

export interface ScanComponent {
  localId: string;
  reportedName: string;
  mappedProduct?: ProductRef;
  confidence: number;
  state: "confirmed" | "candidate" | "unknown" | "risk";
  signals: string[];
}

export interface ScanSnapshot {
  id: string;
  capturedAt: string;
  platform: "windows" | "macos" | "linux";
  components: ScanComponent[];
  approvedFields: string[];
}

export interface CompatibilityIssue {
  code: string;
  status: Exclude<CompatibilityStatus, "compatible">;
  title: string;
  explanation: string;
  requiredActions: string[];
  evidence: EvidenceRef[];
}

export interface CompatibilityReport {
  id: string;
  status: CompatibilityStatus;
  subject: ProductRef[];
  issues: CompatibilityIssue[];
  versions: VersionContext;
  createdAt: string;
}
