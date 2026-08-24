import test from "node:test";
import assert from "node:assert/strict";

import { API } from "../api.js";

function createJsonResponse({ ok, status, payload, contentType = "application/json; charset=utf-8" }) {
  return {
    ok,
    status,
    headers: {
      get(name) {
        return String(name || "").toLowerCase() === "content-type" ? contentType : null;
      },
    },
    async json() {
      return payload;
    },
  };
}

function withFetchMock(t, callback) {
  const previousFetch = globalThis.fetch;
  t.after(() => {
    if (previousFetch === undefined) {
      delete globalThis.fetch;
      return;
    }
    globalThis.fetch = previousFetch;
  });
  globalThis.fetch = callback;
}

test("API mapping client keeps mapping-refresh and business re-evaluation launch paths distinct", { concurrency: false }, async (t) => {
  const requests = [];
  withFetchMock(t, async (url, options) => {
    requests.push({ url, options });
    const path = String(url);
    if (path === "/api/mappings/preview") {
      return createJsonResponse({
        ok: true,
        status: 200,
        payload: {
          ok: true,
          data: {
            conflict: true,
            mode: "overwrite",
            existing_entry: {
              entry_id: "entry-1",
              rule_title: "集团 -> 类型",
              source_name: "中铁集团",
              target_value: "央企",
            },
            affected_count: 3,
            affected_pending_count: 2,
            match_field: "group_name",
            target_field: "source_type",
            target_value: "央企",
            source_name: "中铁集团",
            rule_kind: "group_source_type",
            rule_title: "集团 -> 类型",
            source_label: "集团名称",
            target_label: "主体类型",
            scope_miss: false,
            scope_miss_message: "",
          },
        },
      });
    }
    if (path === "/api/mappings") {
      return createJsonResponse({
        ok: true,
        status: 200,
        payload: {
          ok: true,
          data: {
            entry_id: "entry-2",
            job_id: "job-2",
            job_type: "mapping_refresh",
            affected_count: 4,
            conflict: false,
            mode: "create",
            existing_entry: {},
            affected_pending_count: 1,
            match_field: "transferor",
            target_field: "group_name",
            target_value: "中铁集团",
            source_name: "中铁",
            rule_kind: "transferor_group",
            rule_title: "转让方 -> 集团",
            source_label: "转让方名称",
            target_label: "集团名称",
            scope_miss: true,
            scope_miss_message: "当前记录范围内未命中",
          },
        },
      });
    }
    if (path === "/api/mappings/entry-1") {
      if (options?.method === "PUT") {
        return createJsonResponse({
          ok: true,
          status: 200,
          payload: {
            ok: true,
            data: {
              entry_id: "entry-9",
              job_id: "job-9",
              job_type: "mapping_refresh",
              affected_count: 2,
              conflict: false,
              mode: "update",
              existing_entry: {
                entry_id: "entry-1",
                rule_title: "转让方 -> 集团",
                source_name: "中铁",
                target_value: "中铁集团",
              },
              affected_pending_count: 1,
              match_field: "transferor",
              target_field: "group_name",
              target_value: "华润集团",
              source_name: "华润置地",
              rule_kind: "transferor_group",
              rule_title: "转让方 -> 集团",
              source_label: "转让方名称",
              target_label: "集团名称",
              scope_miss: false,
              scope_miss_message: "",
            },
          },
        });
      }
      if (options?.method === "DELETE") {
        return createJsonResponse({
          ok: true,
          status: 200,
          payload: {
            ok: true,
            data: {
              entry_id: "entry-1",
              deleted: true,
              job_id: "job-10",
              job_type: "mapping_refresh",
              affected_count: 1,
            },
          },
        });
      }
    }
    if (path === "/api/mappings/resolve-conflict") {
      return createJsonResponse({
        ok: true,
        status: 200,
        payload: {
          ok: true,
          data: {
            job_id: "job-3",
            job_type: "mapping_refresh",
            affected_count: 2,
            record_id: "rec-3",
            resolution_mode: "rule_saved_and_refresh_started",
            blocker_kind: "mapping_resolution",
            queue_section: "mapping_resolution",
            resolution: {
              field: "source_type",
              rule_kind: "group_source_type",
              source_name: "中铁集团",
              target_value: "央企",
            },
          },
        },
      });
    }
    if (path === "/api/mappings/reprocess-pending") {
      return createJsonResponse({
        ok: true,
        status: 200,
        payload: {
          ok: true,
          data: {
            job_id: "job-4",
            job_type: "mapping_refresh",
            db_path: "/tmp/db.sqlite3",
            input_dir: "/tmp/mappings",
            discovered_count: 7,
            affected_count: 5,
          },
        },
      });
    }
    if (path === "/api/mappings/undo") {
      return createJsonResponse({
        ok: true,
        status: 200,
        payload: {
          ok: true,
          data: {
            undone: true,
            undo_kind: "update",
            entry_id: "entry-1",
          },
        },
      });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  const preview = await API.previewMapping({
    rule_kind: "group_source_type",
    source_name: "中铁集团",
    target_value: "央企",
    notes: " 预览 ",
    confirm_overwrite: true,
    match_field: "group_name",
    target_field: "source_type",
    extra_field: "legacy",
  });
  assert.deepEqual(preview, {
    conflict: true,
    mode: "overwrite",
    existing_entry: {
      entry_id: "entry-1",
      rule_title: "集团 -> 类型",
      source_name: "中铁集团",
      target_value: "央企",
    },
    affected_count: 3,
    affected_pending_count: 2,
    match_field: "group_name",
    target_field: "source_type",
    target_value: "央企",
    source_name: "中铁集团",
    rule_kind: "group_source_type",
    rule_title: "集团 -> 类型",
    source_label: "集团名称",
    target_label: "主体类型",
    scope_miss: false,
    scope_miss_message: "",
  });

  const saved = await API.saveMapping({
    rule_kind: "transferor_group",
    source_name: "中铁",
    target_value: "中铁集团",
    notes: " new note ",
    confirm_overwrite: false,
    match_field: "transferor",
    target_field: "group_name",
  });
  assert.deepEqual(saved, {
    entry_id: "entry-2",
    job_id: "job-2",
    job_type: "mapping_refresh",
    affected_count: 4,
    conflict: false,
    mode: "create",
    existing_entry: {
      entry_id: "",
      rule_title: "",
      source_name: "",
      target_value: "",
    },
    affected_pending_count: 1,
    match_field: "transferor",
    target_field: "group_name",
    target_value: "中铁集团",
    source_name: "中铁",
    rule_kind: "transferor_group",
    rule_title: "转让方 -> 集团",
    source_label: "转让方名称",
    target_label: "集团名称",
    scope_miss: true,
    scope_miss_message: "当前记录范围内未命中",
  });

  const updated = await API.updateMapping("entry-1", {
    entry_id: "entry-1",
    rule_kind: "transferor_group",
    source_name: "华润置地",
    target_value: "华润集团",
    notes: " refresh ",
    confirm_overwrite: false,
  });
  assert.deepEqual(updated, {
    entry_id: "entry-9",
    job_id: "job-9",
    job_type: "mapping_refresh",
    affected_count: 2,
    conflict: false,
    mode: "update",
    existing_entry: {
      entry_id: "entry-1",
      rule_title: "转让方 -> 集团",
      source_name: "中铁",
      target_value: "中铁集团",
    },
    affected_pending_count: 1,
    match_field: "transferor",
    target_field: "group_name",
    target_value: "华润集团",
    source_name: "华润置地",
    rule_kind: "transferor_group",
    rule_title: "转让方 -> 集团",
    source_label: "转让方名称",
    target_label: "集团名称",
    scope_miss: false,
    scope_miss_message: "",
  });

  const deleted = await API.deleteMapping("entry-1");
  assert.deepEqual(deleted, {
    entry_id: "entry-1",
    deleted: true,
    job_id: "job-10",
    job_type: "mapping_refresh",
    affected_count: 1,
  });

  const conflict = await API.resolveMappingConflict({
    record_id: "rec-3",
    notes: "人工裁决",
    confirm_overwrite: false,
    selected_resolution: {
      field: "source_type",
      label: "央企",
      title: "集团 -> 类型",
      rule_kind: "group_source_type",
      source_name: "中铁集团",
      target_value: "央企",
    },
  });
  assert.deepEqual(conflict, {
    job_id: "job-3",
    job_type: "mapping_refresh",
    affected_count: 2,
    record_id: "rec-3",
    resolution_mode: "rule_saved_and_refresh_started",
    blocker_kind: "mapping_resolution",
    queue_section: "mapping_resolution",
    resolution: {
      field: "source_type",
      rule_kind: "group_source_type",
      source_name: "中铁集团",
      target_value: "央企",
    },
  });

  const relaunch = await API.reprocessPendingMappings();
  assert.deepEqual(relaunch, {
    job_id: "job-4",
    job_type: "mapping_refresh",
    db_path: "/tmp/db.sqlite3",
    input_dir: "/tmp/mappings",
    discovered_count: 7,
    affected_count: 5,
  });

  const undone = await API.undoMapping("startup-session-a");
  assert.deepEqual(undone, {
    undone: true,
    undo_kind: "update",
    entry_id: "entry-1",
  });

  assert.equal(Object.hasOwn(API, "reEvaluateBusinessResolution"), false);

  const previewRequest = requests.find((request) => request.url === "/api/mappings/preview");
  const saveRequest = requests.find((request) => request.url === "/api/mappings" && request.options.method === "POST");
  const updateRequest = requests.find((request) => request.url === "/api/mappings/entry-1" && request.options.method === "PUT");
  const deleteRequest = requests.find((request) => request.url === "/api/mappings/entry-1" && request.options.method === "DELETE");
  const conflictRequest = requests.find((request) => request.url === "/api/mappings/resolve-conflict");
  const reprocessRequest = requests.find((request) => request.url === "/api/mappings/reprocess-pending");
  const undoRequest = requests.find((request) => request.url === "/api/mappings/undo");

  assert.ok(previewRequest);
  assert.ok(saveRequest);
  assert.ok(updateRequest);
  assert.ok(deleteRequest);
  assert.ok(conflictRequest);
  assert.ok(reprocessRequest);
  assert.ok(undoRequest);
  assert.deepEqual(JSON.parse(undoRequest.options.body), {
    startup_session_id: "startup-session-a",
  });
  assert.deepEqual(JSON.parse(updateRequest.options.body), {
    entry_id: "entry-1",
    rule_kind: "transferor_group",
    source_name: "华润置地",
    target_value: "华润集团",
    notes: "refresh",
    confirm_overwrite: false,
  });
});
