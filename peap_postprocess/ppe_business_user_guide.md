# PPE Business Operator Guide

> Audience: operations / data-governance users who configure rules and mapping tables (no code edits).

## 1. Files You Maintain

1. Main config (choose one):
- `peap_postprocess/ppe_config/postprocess_external_template.json` (recommended default; outputs stay under `PEAP_DATA`)
- `peap_postprocess/ppe_config/postprocess.json`
- `peap_postprocess/ppe_config/postprocess.yaml`

2. Mapping templates:
- `peap_postprocess/ppe_config/transferor_group_mapping.template.csv`
- `peap_postprocess/ppe_config/transferor_type_mapping.template.csv`
- `peap_postprocess/ppe_config/group_group_mapping.template.csv`
- `peap_postprocess/ppe_config/group_type_mapping.template.csv`

## 2. Standard Run Flow

Routine operator workflow should now go through the desktop app. The commands below are for engine-level debugging only.

1. Set `mode=plan` first.
2. Run plan:
```bash
python -m peap_postprocess.postprocess_engine.cli run --mode plan
```
3. Review audit workbook: `<PEAP_DATA_ROOT>/outputs/postprocess_audit/audit_<run_id>.xlsx`.
4. Adjust tables/params and run `plan` again until findings are acceptable.
5. Run apply:
```bash
python -m peap_postprocess.postprocess_engine.cli run --mode apply
```

## 2.1 Input File Scope (Configurable)

Use these config keys in `postprocess_external_template.json` or a compatible copied config:

1. `input_targets` (recommended when you want a fixed file list):
- If set, PPE only processes files matched by this list.
- Each entry supports:
  - filename under `input_dir` (example: `挂牌_股权转让.xlsx`)
  - relative path pattern (example: `daily/*.xlsx`)
  - absolute path pattern

2. `scan_recursive`:
- Used only when `input_targets` is empty.
- `true`: recursive scan under `input_dir`.
- `false`: scan only first-level files under `input_dir`.

3. `include_globs`:
- Used only when `input_targets` is empty.
- Typical: `["*.xlsx", "*.xls", "*.csv"]`.

Current default config is fixed to 4 root files via `input_targets`.

## 3. Rule Config Structure

Each rule uses:
```json
"R005_normalize_source_type": {
  "enabled": true,
  "priority": 50,
  "params": {
    "group_type_mapping_file": "../ppe_config/group_type_mapping.template.csv"
  }
}
```

- `enabled`: on/off.
- `priority`: smaller number runs earlier.
- `params`: rule parameters.

## 4. R005 Model (Important)

R005 consumes 4 mapping tables:
- `transferor_group_mapping` (transferor -> group)
- `transferor_type_mapping` (transferor -> source_type)
- `group_group_mapping` (group -> parent_group)
- `group_type_mapping` (group -> source_type)

Allowed source types in this project are four fixed categories.
Use exactly the same category labels as your parser/output standard.
If your sheets use Chinese labels, the common values are:
- `\u592e\u4f01`
- `\u90e8\u59d4`
- `\u5e02\u5c5e`
- `\u6c11\u8425`

Common params:
1. `transferor_group_mapping_file`
2. `transferor_type_mapping_file`
3. `group_group_mapping_file`
4. `group_type_mapping_file`
5. `conflict_strategy` (`keep_original_and_flag` / `prefer_mapping`)
6. `emit_group_no_match`
7. `emit_ministry_no_match`
8. `emit_ministry_missing`
9. `write_ministry_field`
10. `ministry_field_name` (default ministry-column label in your sheets)

Priority order:
`transferor_type_mapping` > `group_type_mapping` > `keyword_infer`

## 5. Mapping Table Filling

## 5.1 transferor_group_mapping.template.csv

Header: `transferor_name,group_name`

Used by R001/R002 for transferor -> group mapping.

Example:
```csv
transferor_name,group_name
Transferor A,Group A
Transferor B,Group A
```

## 5.2 transferor_type_mapping.template.csv

Header: `transferor_name,source_type,notes`

- `transferor_name`: transferor name.
- `source_type`: one of the project's 4 allowed categories.
- `notes`: optional.

Example:
```csv
transferor_name,source_type,notes
Transferor A,\u592e\u4f01,
Transferor B,\u5e02\u5c5e,
```

## 5.3 group_group_mapping.template.csv

Header: `group_name,parent_group_name,notes`

Used to normalize group names to parent groups.

## 5.4 group_type_mapping.template.csv

Header: `group_name,source_type,notes`

## 6. R010 Scrap Physical Asset Filter

Rule id: `R010_filter_scrap_physical_asset`

Purpose:
- Filter out rows that are physical-asset projects and clearly indicate scrap/disposal assets.

How to enable:
1. Set `enabled=true`.
2. Set `params.active=true`.
3. Run `plan` first and review findings before `apply`.

Recommended config example:
```json
"R010_filter_scrap_physical_asset": {
  "enabled": true,
  "priority": 5,
  "params": {
    "active": true,
    "severity": "info",
    "search_all_fields": true,
    "scrap_keywords": ["\u62a5\u5e9f", "\u5e9f\u65e7", "\u62a5\u635f", "\u6dd8\u6c70", "\u62c6\u9664", "\u62c6\u89e3", "\u6b8b\u503c"],
    "negative_keywords": ["\u975e\u62a5\u5e9f", "\u4e0d\u5c5e\u4e8e\u62a5\u5e9f", "\u4e0d\u662f\u62a5\u5e9f"]
  }
}
```

Key params:
1. `active`: hard switch inside rule. Must be `true` to run.
2. `priority`: suggest early priority (for example `5`) so filtered rows skip later rules.
3. `scrap_keywords`: custom inclusion keywords (optional; defaults are built in).
4. `negative_keywords`: exclusion keywords, higher priority than inclusion keywords.
5. `physical_asset_markers`: optional markers to identify physical-asset rows (default includes `\u5b9e\u7269\u8d44\u4ea7`).
6. `search_all_fields`: if true, all non-empty columns are scanned when key columns are missing.

Audit behavior:
1. Finding type: `scrap_physical_asset_filtered`.
2. In `apply` mode, row is removed via internal `filter_out_row` patch.
3. In `plan` mode, no data is deleted, only finding is produced.

## 7. Audit Workbook Reading

File: `<PEAP_DATA_ROOT>/outputs/postprocess_audit/audit_<run_id>.xlsx`

Focus tabs:
1. `summary`
2. `changes`
3. `findings_all` (full findings, primary tab)
4. `conflicts` / `no_match` / `ambiguous` / `errors`

## 8. FAQ

1. Why is a finding not in `conflicts`?
- Check `findings_all`; that is the complete finding list.

2. Can I run `apply` directly?
- Not recommended. Always run `plan` first.

3. How do we rollback?
- Keep `overwrite=false` and keep parser `full` profile for emergency rollback.

## 9. Command Quick Reference

```bash
python -m peap_postprocess.postprocess_engine.cli run --mode plan
python -m peap_postprocess.postprocess_engine.cli run --mode apply
python -m peap_postprocess.postprocess_engine.cli run --config peap_postprocess/ppe_config/postprocess_external_template.json --mode plan
```
