import { describe, expect, it } from "vitest";
import type {
  AgentRole,
  AttributeDataType,
  DraftValidation,
  ViewBlockType,
} from "../src";

describe("universal encyclopedia contracts", () => {
  it("keeps the dynamic schema vocabulary explicit and render-safe", () => {
    const dataTypes: AttributeDataType[] = [
      "text",
      "rich-text",
      "integer",
      "decimal",
      "boolean",
      "date",
      "date-range",
      "measurement",
      "enum",
      "entity-ref",
      "media-ref",
      "geo",
      "json",
    ];
    const blocks: ViewBlockType[] = [
      "hero",
      "summary",
      "classification",
      "attribute-table",
      "relationship-list",
      "timeline",
      "map",
      "media-gallery",
      "citations",
    ];
    expect(dataTypes).toHaveLength(13);
    expect(blocks).toHaveLength(9);
  });

  it("models the maintenance pipeline as typed agent roles", () => {
    const roles: AgentRole[] = [
      "source-monitor",
      "acquisition",
      "extraction",
      "entity-resolution",
      "normalization",
      "translation",
      "evidence-verification",
      "conflict-analysis",
      "quality-assurance",
    ];
    expect(new Set(roles).size).toBe(9);
  });

  it("makes server-side authoring validation machine readable", () => {
    const validation: DraftValidation = {
      valid: false,
      entityTypeId: "type-animal",
      schemaVersion: "animals-1.0.0",
      issues: [
        {
          severity: "error",
          code: "required",
          path: "/attributeValues/attr-scientific-name",
          message: "缺少必填字段",
        },
      ],
    };
    expect(validation.issues[0]?.path).toContain("attributeValues");
  });
});
