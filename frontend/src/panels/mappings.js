import { recordStateLabel } from "../constants/index.js";
import { normalizeEvidenceEntry } from "../contracts/mappings.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}
function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asText(value) {
  return String(value ?? "").trim();
}

const UNKNOWN_BUSINESS_LABEL = "未识别项目类型";
const UNTRUSTED_EXTERNAL_TEXT = "UNTRUSTED_EXTERNAL_TEXT";

function unsafeBusinessValues(item = {}) {
  return new Set([
    asText(item.raw_business_label),
    asText(item.business_label) === UNTRUSTED_EXTERNAL_TEXT ? UNTRUSTED_EXTERNAL_TEXT : "",
  ].filter(Boolean));
}

function safeDisplayText(value, fallback = "", unsafeValues = new Set()) {
  const text = asText(value);
  return text === UNTRUSTED_EXTERNAL_TEXT || unsafeValues.has(text) ? fallback : text;
}

function safeBusinessLabel(item = {}) {
  const label = safeDisplayText(item.business_label, "", unsafeBusinessValues(item));
  return label || (asText(item.raw_business_label) ? UNKNOWN_BUSINESS_LABEL : "");
}

function asCount(value) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function defaultRule(MAPPING_RULES = {}) {
  return Object.values(MAPPING_RULES)[0] || {
    sourceLabel: "来源",
    targetLabel: "目标",
    title: "映射规则",
  };
}

function resolveRule(MAPPING_RULES = {}, ruleKind = "") {
  return MAPPING_RULES[asText(ruleKind)] || defaultRule(MAPPING_RULES);
}

function sectionActionLabel(section = {}) {
  switch (asText(section.cta_kind)) {
    case "reprocess_pending":
      return "全部回刷";
    default:
      return "";
  }
}

function summaryCard(label, value) {
  return `<div class="stat-card">
    <div class="stat-label">${label}</div>
    <div class="stat-value" style="font-size:20px">${value}</div>
  </div>`;
}

const LEGACY_BUSINESS_SECTION_ID = ["business", "resolution"].join("_");
const LEGACY_REVIEW_CTA_KIND = ["re", "evaluate", "business"].join("_");

function sectionDescription(section = {}) {
  switch (asText(section.section_id)) {
    case "mapping_gap_resolution":
      return "这里保留仍存在映射缺口的记录。";
    case "mapping_conflict_resolution":
      return "这里保留需要人工裁决的候选冲突。";
    case "audit":
      return "审计只读项不会进入可执行队列。";
    default:
      return "";
  }
}

function buildGapLabel(item = {}) {
  const codes = asArray(item.gap_codes).map((code) => asText(code)).filter(Boolean);
  return codes.length ? codes.join("、") : "";
}

function isMappingConflictItem(item = {}, section = {}) {
  return asText(item.state) === "mapping_conflict"
    || asText(item.blocker_kind) === "mapping_conflict"
    || asText(item.blocking_reason_code) === "mapping_conflict"
    || asText(section.section_id) === "mapping_conflict_resolution"
    || Boolean(item.has_conflict);
}

function buildConflictLabel(item = {}) {
  const candidateCount = asArray(item.candidate_resolutions).length;
  return candidateCount > 0 ? `候选：${candidateCount} 个` : "候选：暂无，请先回刷确认最新状态";
}

function renderSectionItem(item = {}, index, section = {}, { escapeHtml, display }) {
  const statusText = recordStateLabel(item.status_label || item.state || section.title);
  const isConflict = isMappingConflictItem(item, section);
  const gapLabel = buildGapLabel(item);
  const unsafeValues = unsafeBusinessValues(item);
  const itemTitle = safeDisplayText(item.project_code, "", unsafeValues) || safeBusinessLabel(item) || safeDisplayText(item.record_id, "", unsafeValues) || "未命名记录";
  const itemSource = safeDisplayText(item.source_name, "", unsafeValues) || statusText || safeDisplayText(item.queue_section, "", unsafeValues) || safeDisplayText(section.title, "", unsafeValues);
  const recommended = asObject(item.recommended_rule).rule_kind
    ? `<button class="btn btn-sm btn-primary btn-use-rule" data-section-id="${escapeHtml(section.section_id)}" data-index="${index}" data-source="recommended">带入推荐规则</button>`
    : "";
  const candidates = asArray(item.candidate_resolutions).map((resolution, resolutionIndex) => {
    const title = display(safeDisplayText(resolution.title, "", unsafeValues) || safeDisplayText(resolution.rule_kind, "", unsafeValues));
    const targetValue = safeDisplayText(resolution.target_value, "", unsafeValues);
    const sourceName = safeDisplayText(resolution.source_name, "", unsafeValues);
    const evidenceChain = asArray(resolution.evidence_chain).length
      ? `<div class="mapping-candidate-evidence">依据：${escapeHtml(asArray(resolution.evidence_chain).map((entry) => normalizeEvidenceEntry(entry, unsafeValues) || safeDisplayText(display(entry), "", unsafeValues)).filter(Boolean).join(" -> "))}</div>`
      : "";
    const resolveButton = isConflict
      ? `<button class="btn btn-sm btn-primary btn-resolve-conflict" data-section-id="${escapeHtml(section.section_id)}" data-index="${index}" data-resolution-index="${resolutionIndex}">裁决为此项</button>`
      : "";
    return `<div class="mapping-candidate-card">
      <div class="mapping-candidate-title">${escapeHtml(title)} · ${escapeHtml(display(targetValue))}</div>
      <div class="mapping-candidate-meta">${escapeHtml(display(sourceName))} → ${escapeHtml(display(targetValue))}</div>
      ${evidenceChain}
      <div class="mapping-candidate-actions">
        <button class="btn btn-sm btn-use-rule" data-section-id="${escapeHtml(section.section_id)}" data-index="${index}" data-source="candidate" data-resolution-index="${resolutionIndex}">带入表单</button>
        ${resolveButton}
      </div>
    </div>`;
  }).join("");

  return `<div class="card" style="padding:var(--space-5);background:var(--bg)">
    <div style="display:flex;justify-content:space-between;gap:var(--space-4);align-items:flex-start;flex-wrap:wrap">
      <div>
        <div style="font-size:15px;font-weight:600;color:var(--text)">${escapeHtml(display(itemTitle))}</div>
        <div style="font-size:13px;color:var(--text-muted);margin-top:4px">${escapeHtml(display(itemSource))}</div>
      </div>
      <span class="badge pending"><span class="badge-dot"></span>${escapeHtml(display(statusText || section.title))}</span>
    </div>
    <div style="margin-top:var(--space-3);font-size:13px;color:var(--text-muted)">${escapeHtml(display(item.status_detail || ""))}</div>
    ${isConflict
      ? `<div style="margin-top:var(--space-2);font-size:12px;color:var(--text-faint)">${escapeHtml(buildConflictLabel(item))}</div>`
      : (gapLabel ? `<div style="margin-top:var(--space-2);font-size:12px;color:var(--text-faint)">缺口：${escapeHtml(gapLabel)}</div>` : "")}
    ${asObject(item.recommended_rule).rule_kind ? `<div style="margin-top:var(--space-3);font-size:13px;color:var(--text-muted)">推荐：${escapeHtml(display(safeDisplayText(item.recommended_rule.title, "", unsafeValues)))} · ${escapeHtml(display(safeDisplayText(item.recommended_rule.source_name, "", unsafeValues)))} → ${escapeHtml(display(safeDisplayText(item.recommended_rule.target_value, "", unsafeValues)))}</div>` : ""}
    ${recommended ? `<div style="display:flex;gap:var(--space-2);flex-wrap:wrap;margin-top:var(--space-3)">${recommended}</div>` : ""}
    ${candidates ? `<div class="mapping-candidate-list">${candidates}</div>` : ""}
  </div>`;
}

function renderSection(section = {}, { escapeHtml, display }) {
  const items = asArray(section.items);
  const actionLabel = items.length > 0 ? sectionActionLabel(section) : "";
  return `<section class="card animate-in delay-2">
    <div class="jobs-header">
      <span class="jobs-header-title">${escapeHtml(asText(section.title || section.section_id || "待处理"))}</span>
      <div style="display:flex;gap:var(--space-3);align-items:center">
        <span style="font-size:13px;color:var(--text-muted)">${asCount(section.count)} 条</span>
        ${actionLabel ? `<button class="btn btn-sm" id="btn-mappings-section-${escapeHtml(asText(section.section_id))}">${escapeHtml(actionLabel)}</button>` : ""}
      </div>
    </div>
    ${sectionDescription(section) ? `<div style="font-size:12px;color:var(--text-faint);margin-top:var(--space-2)">${escapeHtml(sectionDescription(section))}</div>` : ""}
    ${items.length === 0
      ? `<div style="padding:24px 0;text-align:center;color:var(--text-faint)">当前分区暂无待处理记录</div>`
      : `<div class="mapping-pending-list">
        ${items.map((item, index) => renderSectionItem(item, index, section, { escapeHtml, display })).join("")}
      </div>`}
  </section>`;
}

export function createMappingsPanel({
  $,
  $$,
  MAPPING_RULES,
  escapeHtml,
  display,
  formatJobTime,
  num,
  getMappings,
  getMappingDraft,
  getMappingPreview,
  getMappingMessage,
  buildMappingTargetControl,
  onRuleKindChange,
  onDraftInput,
  onDraftPreview,
  onDraftSave,
  onDraftReset,
  onUseSuggestion,
  onResolveConflict,
  onSectionAction,
  onEditEntry,
  onDeleteEntry,
  onUndo,
}) {
  function render() {
    const el = $("#panel-mappings");
    const mappings = asObject(getMappings?.());
    const sections = asArray(mappings.sections).filter((section) => asText(section.section_id) !== LEGACY_BUSINESS_SECTION_ID && asText(section.cta_kind) !== LEGACY_REVIEW_CTA_KIND);
    const entries = asArray(mappings.entries);
    const summary = asObject(mappings.summary);
    const mappingDraft = asObject(getMappingDraft?.());
    const preview = getMappingPreview?.() || null;
    const mappingMessage = asText(getMappingMessage?.());
    const rule = resolveRule(MAPPING_RULES, mappingDraft.rule_kind);
    const targetControl = buildMappingTargetControl(rule, mappingDraft.target_value);
    const isEditingEntry = Boolean(asText(mappingDraft.entry_id));
    const undo = asObject(mappings.undo);
    const canUndo = undo.available === true && Boolean(asText(undo.startup_session_id));

    el.innerHTML = `
      <div class="animate-in" style="display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);flex-wrap:wrap">
        <h1 class="page-title">映射</h1>
        <button class="btn" id="btn-mapping-undo" type="button" ${canUndo ? "" : "disabled"}>撤销上次规则变更</button>
      </div>
      <section class="animate-in delay-1" style="margin-bottom:var(--space-4)">
        <div class="stats-grid" style="grid-template-columns:repeat(4,1fr)">
          ${summaryCard("可执行", asCount(summary.actionable_count))}
          ${summaryCard("待映射补全", asCount(summary.mapping_gap_count))}
          ${summaryCard("待映射冲突", asCount(summary.mapping_conflict_count))}
          ${summaryCard("审计只读", asCount(summary.audit_count))}
        </div>
      </section>
      <div class="mappings-layout">
        <div class="mappings-main">
          <section class="card animate-in delay-1">
            <div class="jobs-header">
              <span class="jobs-header-title">${isEditingEntry ? "编辑映射规则" : "新增 / 修改映射规则"}</span>
            </div>
            ${isEditingEntry ? `<div style="margin-top:var(--space-2);font-size:12px;color:var(--text-faint)">正在编辑已有规则，保存后会替换原规则并回刷受影响记录。</div>` : ""}
            <div class="form-row" style="margin-top:var(--space-4)">
              <div class="form-group">
                <label>规则类型</label>
                <select id="mapping-rule-kind">
                  ${Object.entries(MAPPING_RULES).map(([ruleKind, spec]) => `<option value="${ruleKind}" ${mappingDraft.rule_kind === ruleKind ? "selected" : ""}>${escapeHtml(spec.title)}</option>`).join("")}
                </select>
              </div>
              <div class="form-group">
                <label id="mapping-source-label">${escapeHtml(rule.sourceLabel)}</label>
                <input type="text" id="mapping-source-name" value="${escapeHtml(mappingDraft.source_name || "")}" placeholder="请输入${escapeHtml(rule.sourceLabel)}名称">
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label id="mapping-target-label">${escapeHtml(rule.targetLabel)}</label>
                ${targetControl}
              </div>
              <div class="form-group">
                <label>备注</label>
                <input type="text" id="mapping-notes" value="${escapeHtml(mappingDraft.notes || "")}" placeholder="可选，记录规则来源或说明">
              </div>
            </div>
            <div style="display:flex;gap:var(--space-3);align-items:center;flex-wrap:wrap">
              <label style="display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--text-muted)">
                <input type="checkbox" id="mapping-confirm-overwrite" ${mappingDraft.confirm_overwrite ? "checked" : ""} ${preview && preview.conflict ? "" : "disabled"}>
                覆盖已有规则
              </label>
              <button class="btn" id="btn-mapping-preview">预览影响范围</button>
              <button class="btn btn-primary" id="btn-mapping-save">${isEditingEntry ? "更新规则" : "保存规则"}</button>
              <button class="btn" id="btn-mapping-reset">${isEditingEntry ? "取消编辑" : "重置"}</button>
            </div>
            ${mappingMessage ? `<div class="alert ${mappingMessage.startsWith("失败") ? "alert-danger" : "alert-success"}">${escapeHtml(mappingMessage)}</div>` : ""}
            ${preview ? `
            <div class="card" style="margin-top:var(--space-4);padding:var(--space-5);background:var(--bg)">
              <div style="display:flex;justify-content:space-between;gap:var(--space-4);align-items:flex-start;flex-wrap:wrap">
                <div>
                  <div style="font-size:15px;font-weight:600;color:var(--text)">${escapeHtml(preview.rule_title || rule.title)}</div>
                  <div style="font-size:13px;color:var(--text-muted);margin-top:4px">${escapeHtml(preview.source_label || rule.sourceLabel)}：${escapeHtml(display(preview.source_name))} → ${escapeHtml(preview.target_label || rule.targetLabel)}：${escapeHtml(display(preview.target_value))}</div>
                </div>
                <span class="badge ${preview.conflict ? "pending" : "ready"}"><span class="badge-dot"></span>${escapeHtml(preview.mode === "overwrite" ? "将覆盖" : preview.mode === "update" ? "将更新" : "将新增")}</span>
              </div>
              <div style="display:flex;gap:var(--space-4);flex-wrap:wrap;margin-top:var(--space-3);font-size:13px;color:var(--text-muted)">
                <span>影响记录：${num(preview.affected_count)}</span>
                <span>待处理记录：${num(preview.affected_pending_count)}</span>
                <span>${escapeHtml(preview.scope_miss ? display(preview.scope_miss_message) : "已命中当前记录范围")}</span>
              </div>
              ${preview.existing_entry && preview.existing_entry.entry_id ? `<div style="margin-top:var(--space-3);font-size:13px;color:var(--warning)">现有规则：${escapeHtml(display(preview.existing_entry.rule_title))} · ${escapeHtml(display(preview.existing_entry.source_name))} → ${escapeHtml(display(preview.existing_entry.target_value))}</div>` : ""}
            </div>` : ""}
          </section>

          <section class="card animate-in delay-3" style="margin-top:var(--space-4)">
            <div class="jobs-header">
              <span class="jobs-header-title">已保存规则</span>
              <span style="font-size:13px;color:var(--text-muted)">${entries.length} 条</span>
            </div>
            ${entries.length === 0
              ? `<div style="padding:24px 0;text-align:center;color:var(--text-faint)">暂无保存的映射规则</div>`
              : `<div class="table-wrap" style="margin-top:var(--space-4)">
                <table>
                  <thead><tr><th>规则类型</th><th>来源名称</th><th>目标值</th><th>更新时间</th><th>操作</th></tr></thead>
                  <tbody>
                    ${entries.map((entry) => `<tr>
                      <td>${escapeHtml(display(entry.rule_title))}</td>
                      <td>${escapeHtml(display(entry.source_name))}</td>
                      <td>${escapeHtml(display(entry.target_value))}</td>
                      <td style="white-space:nowrap">${entry.updated_at ? formatJobTime(entry.updated_at) : "—"}</td>
                      <td style="white-space:nowrap">
                        <div style="display:flex;gap:var(--space-2);flex-wrap:wrap">
                          <button class="btn btn-sm btn-edit-mapping-entry" data-entry-id="${escapeHtml(asText(entry.entry_id))}">编辑</button>
                          <button class="btn btn-sm btn-delete-mapping-entry" data-entry-id="${escapeHtml(asText(entry.entry_id))}">删除</button>
                        </div>
                      </td>
                    </tr>`).join("")}
                  </tbody>
                </table>
              </div>`}
          </section>
        </div>

        <div class="mappings-side">
          ${sections.length
            ? sections.map((section) => renderSection(section, { escapeHtml, display })).join("")
            : `<section class="card animate-in delay-2">
              <div style="padding:24px 0;text-align:center;color:var(--text-faint)">当前没有待处理的映射缺口或映射冲突。</div>
            </section>`}

          <section class="card animate-in delay-3">
            <div class="jobs-header">
              <span class="jobs-header-title">映射优先级</span>
            </div>
            <div class="mapping-priority-list">
              <p>1. 先看 转让方 → 集团；如果有明确规则，优先用它，再继续追集团链。</p>
              <p>2. 集团链会继续套用 集团 → 集团，直到收敛到最终集团。</p>
              <p>3. 类型同时参考 转让方 → 类型 和 集团 → 类型；只要出现多个不同候选，就判定为映射冲突。</p>
              <p>4. 当集团已经确定但类型仍缺失时，系统会优先推荐 集团 → 类型，因为它的覆盖面更稳定。</p>
            </div>
          </section>
        </div>
      </div>
    `;

    $("#mapping-rule-kind")?.addEventListener("change", (event) => {
      onRuleKindChange?.(event.target.value);
    });
    $("#mapping-source-name")?.addEventListener("input", (event) => {
      onDraftInput?.("source_name", event.target.value);
    });
    const mappingTarget = $("#mapping-target-value");
    const syncMappingTargetValue = (event) => {
      onDraftInput?.("target_value", event.target.value);
    };
    mappingTarget?.addEventListener("input", syncMappingTargetValue);
    mappingTarget?.addEventListener("change", syncMappingTargetValue);
    $("#mapping-notes")?.addEventListener("input", (event) => {
      onDraftInput?.("notes", event.target.value);
    });
    $("#mapping-confirm-overwrite")?.addEventListener("change", (event) => {
      onDraftInput?.("confirm_overwrite", Boolean(event.target.checked));
    });
    $("#btn-mapping-preview")?.addEventListener("click", async () => {
      await onDraftPreview?.();
    });
    $("#btn-mapping-save")?.addEventListener("click", async () => {
      await onDraftSave?.();
    });
    $("#btn-mapping-reset")?.addEventListener("click", () => {
      onDraftReset?.();
    });
    $("#btn-mapping-undo")?.addEventListener("click", async () => {
      if (!canUndo) return;
      await onUndo?.(undo);
    });

    sections.forEach((section) => {
      const actionButton = $(`#btn-mappings-section-${asText(section.section_id)}`, el);
      actionButton?.addEventListener("click", async () => {
        await onSectionAction?.(section);
      });
    });

    $$(".btn-use-rule", el).forEach((button) => {
      button.addEventListener("click", () => {
        const section = sections.find((entry) => asText(entry.section_id) === asText(button.dataset.sectionId));
        const item = asArray(section?.items)[asCount(button.dataset.index)];
        if (!item) return;
        if (button.dataset.source === "recommended") {
          onUseSuggestion?.({
            section,
            item,
            resolution: item.recommended_rule,
          });
          return;
        }
        const resolution = asArray(item.candidate_resolutions)[asCount(button.dataset.resolutionIndex)];
        if (!resolution) return;
        onUseSuggestion?.({
          section,
          item,
          resolution,
        });
      });
    });

    $$(".btn-resolve-conflict", el).forEach((button) => {
      button.addEventListener("click", async () => {
        const section = sections.find((entry) => asText(entry.section_id) === asText(button.dataset.sectionId));
        const item = asArray(section?.items)[asCount(button.dataset.index)];
        const resolution = asArray(item?.candidate_resolutions)[asCount(button.dataset.resolutionIndex)];
        if (!item || !resolution) return;
        await onResolveConflict?.({
          section,
          item,
          resolution,
        });
      });
    });

    $$(".btn-edit-mapping-entry", el).forEach((button) => {
      button.addEventListener("click", () => {
        const entry = entries.find((item) => asText(item.entry_id) === asText(button.dataset.entryId));
        if (!entry) return;
        onEditEntry?.(entry);
      });
    });

    $$(".btn-delete-mapping-entry", el).forEach((button) => {
      button.addEventListener("click", async () => {
        const entry = entries.find((item) => asText(item.entry_id) === asText(button.dataset.entryId));
        if (!entry) return;
        await onDeleteEntry?.(entry);
      });
    });
  }

  return {
    render,
  };
}
