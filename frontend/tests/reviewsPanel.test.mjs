import test from "node:test";
import assert from "node:assert/strict";

import { createReviewsPanel } from "../src/panels/reviews.js";

function createHarness(data, { loading = false, error = "", filters = { problem_kind: "all", state: "all", keyword: "", page: 1 } } = {}) {
  const controls = new Map();
  const panelEl = {
    innerHTML: "",
  };
  const $ = (selector) => {
    if (selector === "#panel-reviews") return panelEl;
    if (!controls.has(selector)) {
      controls.set(selector, {
        value: "",
        listeners: {},
        addEventListener(eventName, handler) {
          this.listeners[eventName] = handler;
        },
      });
    }
    return controls.get(selector);
  };
  const changes = [];
  const panel = createReviewsPanel({
    $,
    escapeHtml: (value) => String(value ?? ""),
    display: (value) => String(value || "—"),
    formatJobTime: (value) => String(value),
    getReviewProblems: () => data,
    getLoading: () => loading,
    getError: () => error,
    getFilters: () => filters,
    onFilterChange: (key, value) => { changes.push([key, value]); },
  });
  panel.render();
  return { html: panelEl.innerHTML, controls, changes };
}

test("reviews panel renders readonly review explanations without action buttons", () => {
  const { html } = createHarness({
    summary: {
      total_count: 1,
      project_type_unresolved_count: 1,
      business_family_unresolved_count: 0,
      deal_data_incomplete_count: 0,
      source_artifact_unavailable_count: 0,
      export_fields_missing_count: 0,
      manual_review_unclassified_count: 0,
    },
    rows: [
      {
        problem_label: "项目类型待确认",
        status_label: "待人工复核",
        project_code: "G1",
        project_name: "项目一",
        record_family_label: "挂牌业务",
        raw_business_label: "未知",
        exchange_label: "上交所",
        source_file: "",
        archive_path: "",
        has_local_artifact: true,
        local_artifact_name: "source.html",
        evidence_verdict: {
          status: "verified",
          inspection_openable_path: "/managed/source.html",
        },
        business_explanation: "原始项目类型为“未知”。系统目录中没有可用匹配。",
        business_impact: "该记录暂不能进入导出。",
        suggested_review: "请查看来源页面项目类型。",
        updated_at: "2026-05-17T10:00:00+08:00",
        evidence: { raw_business_label: "未知", message: "待人工复核历史证据" },
      },
    ],
    total_count: 1,
  });

  assert.match(html, /待复核问题/);
  assert.match(html, /source.html/);
  assert.match(html, /原因/);
  assert.match(html, /处理方向/);
  assert.doesNotMatch(html, /<button/);
  assert.doesNotMatch(html, /关联记录信息|维护信息|供维护人员排查使用|本页不修改记录/);
  assert.equal((html.match(/项目编号/g) || []).length, 1);
  assert.equal((html.match(/项目名称/g) || []).length, 1);
  assert.equal((html.match(/原始项目类型/g) || []).length, 1);
  assert.doesNotMatch(html, /业务归属重评估|btn-mappings-section-business_resolution|带入推荐规则|直接裁决/);
  const visibleText = html.replace(/<[^>]+>/g, " ");
  assert.doesNotMatch(visibleText, /problem_kind|reason_code|record_id|pending_review/);
});

test("reviews panel separates source artifact unavailable from missing export fields", () => {
  const { html } = createHarness({
    summary: {
      total_count: 1,
      project_type_unresolved_count: 0,
      business_family_unresolved_count: 0,
      deal_data_incomplete_count: 0,
      source_artifact_unavailable_count: 1,
      export_fields_missing_count: 0,
      manual_review_unclassified_count: 0,
    },
    rows: [
      {
        problem_kind: "source_artifact_unavailable",
        problem_label: "原网页不可用",
        reason_code: "source_artifact_invalid",
        status_label: "字段缺失",
        project_code: "G2",
        project_name: "项目二",
        archive_path: "/old/archive/source.html",
        has_local_artifact: false,
        artifact_status: "unresolved",
        artifact_missing_reason: "source_artifact_invalid",
        business_explanation: "本地文件不是完整原网页。",
        business_impact: "该记录暂不能进入导出。",
        suggested_review: "请重新下载真实渲染页。",
      },
    ],
    total_count: 1,
  });

  assert.match(html, /原网页不可用/);
  assert.match(html, /原网页不可用：不是完整原网页/);
  assert.doesNotMatch(html, /缺少必填字段/);
  assert.match(html, /导出必填字段缺失[\s\S]*<div class="stat-value" style="font-size:20px">0<\/div>/);
  assert.doesNotMatch(html, /来源文件或可定位文件名[^]*\/old\/archive\/source\.html/);
  assert.doesNotMatch(html, /系统原始记录[^]*\/old\/archive\/source\.html/);
});

test("reviews panel labels missing source artifacts with user-facing wording", () => {
  const { html } = createHarness({
    summary: {
      total_count: 1,
      source_artifact_unavailable_count: 1,
      export_fields_missing_count: 0,
    },
    rows: [
      {
        problem_kind: "source_artifact_unavailable",
        problem_label: "原网页不可用",
        reason_code: "source_artifact_missing",
        status_label: "字段缺失",
        project_code: "G3",
        project_name: "项目三",
        has_local_artifact: false,
        artifact_status: "unresolved",
        artifact_missing_reason: "artifact_path_unresolved",
        business_explanation: "数据库记录指向的原网页文件不存在或不可读取。",
        business_impact: "该记录暂不能进入导出。",
        suggested_review: "请重新下载该记录。",
      },
    ],
    total_count: 1,
  });

  assert.match(html, /原网页不可用：原网页文件缺失/);
  assert.doesNotMatch(html, /原路径不可访问|缺少必填字段/);
});

test("reviews panel does not render raw business label evidence", () => {
  const { html } = createHarness({
    summary: { total_count: 1, project_type_unresolved_count: 1 },
    rows: [
      {
        problem_label: "项目类型待确认",
        status_label: "待人工复核",
        project_code: "G4",
        record_family_label: "挂牌业务",
        raw_business_label: "UNTRUSTED_EXTERNAL_TEXT",
        business_label: "",
        business_explanation: "项目类型未识别。系统目录中没有可用匹配，当前不能判断应使用哪套业务规则和导出表头。",
        business_impact: "该记录暂不能进入导出。",
        suggested_review: "请查看业务目录配置或项目类型映射模板。",
        evidence: { raw_business_label: "UNTRUSTED_EXTERNAL_TEXT" },
      },
    ],
    total_count: 1,
  });

  assert.match(html, /项目类型未识别/);
  assert.match(html, /系统目录中没有可用匹配/);
  assert.doesNotMatch(html, /UNTRUSTED_EXTERNAL_TEXT/);
  assert.doesNotMatch(html, /原始项目类型/);
});

test("reviews panel does not display legacy local artifact name when evidence verdict is stale", () => {
  const { html } = createHarness({
    summary: { total_count: 1, field_missing_count: 1 },
    rows: [
      {
        problem_label: "字段缺失",
        status_label: "字段缺失",
        project_code: "G3",
        has_local_artifact: true,
        local_artifact_name: "legacy-source.html",
        artifact_status: "available",
        artifact_missing_reason: "authoritative_artifact_missing",
        evidence_verdict: {
          status: "stale_reference",
          reason_code: "authoritative_artifact_missing",
          authoritative_path: "/missing/archive.html",
          inspection_openable_path: "/managed/provenance.html",
        },
        business_explanation: "缺少导出字段。",
        business_impact: "该记录暂不能进入导出。",
        suggested_review: "请核对来源文件。",
      },
    ],
    total_count: 1,
  });

  assert.match(html, /文件未定位：缺少可打开的本地文件/);
  assert.doesNotMatch(html, /legacy-source\.html/);
});

test("reviews panel renders loading error and empty states", () => {
  assert.match(createHarness({ rows: [], summary: {} }, { loading: true }).html, /正在加载待复核问题…/);
  assert.match(createHarness({ rows: [], summary: {} }, { error: "failed" }).html, /待复核问题加载失败/);
  assert.match(createHarness({ rows: [], summary: {} }).html, /当前无待人工复核问题。/);
});

test("reviews panel exposes pagination for truncated result sets", () => {
  const { html, controls, changes } = createHarness(
    {
      summary: { total_count: 75, project_type_unresolved_count: 75 },
      rows: [{ problem_label: "项目类型待确认", status_label: "待人工复核", project_code: "G1" }],
      returned_count: 50,
      total_count: 75,
      truncated: true,
    },
    { filters: { problem_kind: "all", state: "all", keyword: "", page: 1 } },
  );

  assert.match(html, /第 1 页/);
  assert.match(html, /共 75 条/);
  assert.match(html, /下一页/);
  controls.get("#review-next-page").listeners.click();
  assert.deepEqual(changes.at(-1), ["page", 2]);
});
