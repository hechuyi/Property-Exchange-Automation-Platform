# Electron Smoke Report

> Update (2026-03-28 11:11 UTC): the latest strict real Electron smoke is green again at `/tmp/peap-desktop-smoke-main-1774696290.md`. The intermediate `export` generic script failure, force-stop disabled state, and restart-read race were all fixed explicitly rather than silently tolerated. Older failure snapshots remain historical context only.

- started_at: 2026-03-28T11:11:31.142Z
- finished_at: 2026-03-28T11:11:33.370Z
- ok: true

## Steps

- [x] renderer_ready
  - detail: `"renderer-ready"`
- [x] manual_import
  - detail: `{"job_id":"e1a8dd064fe44e73a718b0d70b471e37","job_type":"manual_import","status":"success","downloaded_count":1,"persisted_count":1,"exception_count":0,"metadata":{"discovered_count":1,"input_dir":"/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app/smoke_fixtures/manual_import_equity_transfer"},"summary":{"failed_count":0,"imported_count":1,"pending_mapping_count":0,"skipped_count":0}}`
- [x] export
  - detail: `{"job_id":"69dafd60a33941ce87ad821b477d79d8","job_type":"export_excel","status":"success","downloaded_count":37,"persisted_count":2,"exception_count":0,"metadata":{"business_types":["股权转让","实物资产","增资扩股","预披露"],"date_from":"","date_to":"","output_dir":"/Users/rtoc/Documents/PEAP/exports","record_family":"listing","scope":{"date_from":"","date_to":"","keyword":"","page":1,"page_size":50,"project_type":"all","record_family":"listing","state":"all"}},"summary":{"artifacts":["/Users/rtoc/Documents/PEAP/exports/挂牌_实物资产_新增_20260328_191131_979147_bc25a468.xlsx","/Users/rtoc/Documents/PEAP/exports/挂牌_预披露_新增_20260328_191131_979147_bc25a468.xlsx"],"changed_records":0,"cursor_key":"export:1f0700eeb448","export_id":"20260328_191131_979147_bc25a468","job_id":"69dafd60a33941ce87ad821b477d79d8","job_type":"export_excel","message":"导出完成，共生成 2 个文件","new_records":37,"scope":{"date_from":"","date_to":"","keyword":"","page":1,"page_size":50,"project_type":"all","record_family":"listing","state":"all"},"status":"completed"}}`
- [x] interrupt_restart
  - detail: `{"job_id":"d90633f8ae554dda8b271480f3e00b46","job_type":"manual_import","status":"interrupted","downloaded_count":0,"persisted_count":0,"exception_count":0,"metadata":{"discovered_count":1,"input_dir":"/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app/smoke_fixtures/manual_import_smoke_delay_equity_transfer"},"summary":{"interrupted_at":"2026-03-28 19:11:32","message":"desktop backend restarted before task completed","status":"interrupted"}}`

## Notes

- 这次 strict real smoke 已闭环通过，报告源文件为 `/tmp/peap-desktop-smoke-main-1774696290.md`
- `interrupt_restart` 现在验证到字面意义上的 `interrupted` 终态，不再退化为 `completed_before_interrupt`
- `mapping_refresh_1` 的真实 Electron 证据仍来自更早一轮 fresh pending-mapping 运行：`/tmp/peap-desktop-smoke-1774691518.md`，其中 `mapping_refresh_1` 为 pass
- Electron 退出阶段仍可能在 stderr 打出 Playwright pipe 的 `EPIPE`，但只要 markdown report 已完整写出且 `ok: true`，该噪音不影响本报告结论
