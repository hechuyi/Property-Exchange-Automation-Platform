import { describe, expect, it } from "vitest";
import { normalizeSavedMappingEntries } from "./entries";
import { MAPPING_RULE_CONFIG } from "./model";

describe("normalizeSavedMappingEntries", () => {
  it("marks unsupported schema entries explicitly and never falls back to index identity", () => {
    const normalized = normalizeSavedMappingEntries(
      [
        {
          match_field: "unknown_match",
          target_field: "unknown_target",
          source_name: "示例来源",
          target_value: "示例目标",
        },
      ],
      MAPPING_RULE_CONFIG,
    ) as Array<Record<string, any>>;

    expect(normalized).toHaveLength(1);
    expect(normalized[0].status).toBe("abnormal");
    expect(normalized[0].issueCodes).toContain("unsupported_rule_fields");
    expect(normalized[0].issueCodes).toContain("missing_identity");
    expect(normalized[0].key).toMatch(/^abnormal:/);
    expect(normalized[0].key).not.toBe("0");
  });

  it("keeps valid entries editable only when schema and identity are complete", () => {
    const normalized = normalizeSavedMappingEntries(
      [
        {
          entry_id: "entry-1",
          company_name: "华润集团",
          source_type: "央企",
          metadata: {
            match_field: "group",
            target_field: "source_type",
            notes: "备注",
          },
        },
      ],
      MAPPING_RULE_CONFIG,
    ) as Array<Record<string, any>>;

    expect(normalized).toHaveLength(1);
    expect(normalized[0].status).toBe("valid");
    expect(normalized[0].isEditable).toBe(true);
    expect(normalized[0].ruleKind).toBe("group_type");
    expect(normalized[0].key).toBe("entry-1");
  });
});
