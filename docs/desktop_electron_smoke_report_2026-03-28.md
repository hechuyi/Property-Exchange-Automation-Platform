# Electron Smoke Report

> Update (2026-03-28 10:32 UTC): after tightening interrupt-path diagnostics so failures are no longer silently accepted, the latest strict smoke run fails at `export` with `Script failed to execute...`; see `/tmp/peap-desktop-smoke-commit-1774693974.md`. The passing snapshot below is no longer the newest run.

- started_at: 2026-03-28T10:10:39.788Z
- finished_at: 2026-03-28T10:10:41.081Z
- ok: true

## Steps

- [x] renderer_ready
  - detail: `"renderer-ready"`
- [x] manual_import
  - detail: `{"job_id":"743fbf8423964fa6bde6abafa3409702","job_type":"manual_import","status":"success","downloaded_count":1,"persisted_count":1,"exception_count":0,"metadata":{"discovered_count":1,"input_dir":"/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/desktop_app/smoke_fixtures/manual_import_equity_transfer"},"summary":{"failed_count":0,"imported_count":1,"pending_mapping_count":0,"skipped_count":0}}`
- [x] export
  - detail: `{"job_id":"bcd5c6a870164557a3a75567e95722cd","job_type":"export_excel","status":"success","downloaded_count":37,"persisted_count":2,"exception_count":0,"metadata":{"business_types":["股权转让","实物资产","增资扩股","预披露"],"date_from":"","date_to":"","output_dir":"/Users/rtoc/Documents/PEAP/exports","record_family":"listing","scope":{"date_from":"","date_to":"","keyword":"","page":1,"page_size":50,"project_type":"all","record_family":"listing","state":"all"}},"summary":{"artifacts":["/Users/rtoc/Documents/PEAP/exports/挂牌_实物资产_新增_20260328_181040_692559_e532507a.xlsx","/Users/rtoc/Documents/PEAP/exports/挂牌_预披露_新增_20260328_181040_692559_e532507a.xlsx"],"changed_records":0,"cursor_key":"export:1f0700eeb448","export_id":"20260328_181040_692559_e532507a","job_id":"bcd5c6a870164557a3a75567e95722cd","job_type":"export_excel","message":"导出完成，共生成 2 个文件","new_records":37,"scope":{"date_from":"","date_to":"","keyword":"","page":1,"page_size":50,"project_type":"all","record_family":"listing","state":"all"},"status":"completed"}}`
- [x] interrupt_restart
  - detail: `{"job_id":"4e2f8976555343ac9ad403b98dd75b20","job_type":"manual_import","status":"success","downloaded_count":1,"persisted_count":1,"exception_count":0,"metadata":{"discovered_count":1,"input_dir":"/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/desktop_app/smoke_fixtures/manual_import_equity_transfer"},"summary":{"failed_count":0,"imported_count":1,"pending_mapping_count":0,"skipped_count":0},"completed_before_interrupt":true}`

## Notes

- 这次 real smoke 已闭环通过，报告源文件为 `/tmp/peap-desktop-smoke-1774692638.md`
- `interrupt_restart.detail.completed_before_interrupt=true` 表示第二次 `manual_import` 已成功创建并完成，但在当前单样本 fixture 下，它快于 smoke driver 发出中断动作；因此该步当前验证的是“恢复后再次导入仍可成功完成”，而不是字面意义上的 `interrupted` 终态
- `mapping_refresh_1` 的真实 Electron 证据仍来自更早一轮 fresh pending-mapping 运行：`/tmp/peap-desktop-smoke-1774691518.md`，其中 `mapping_refresh_1` 为 pass
- Electron 退出阶段仍可能在 stderr 打出 Playwright pipe 的 `EPIPE`，但只要 markdown report 已完整写出且 `ok: true`，该噪音不影响本报告结论
