#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Prepare submission files using exact project-code matching."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from html import unescape
from time import monotonic
from typing import Dict, Iterable, List, Optional, Tuple


def _add_project_root_to_syspath() -> str:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    return project_root


_PROJECT_ROOT = _add_project_root_to_syspath()

from peap_core.runtime import load_runtime_config, resolve_path


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.propagate = False
logger.handlers.clear()

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(console_handler)

TARGET_EXTENSIONS = {".html", ".mhtml"}
SAFE_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]*$")
PROJECT_CODE_COL_TOKEN = "项目编号"
PROJECT_NAME_COL_TOKEN = "项目名称"
LISTING_START_DATE_COL_TOKENS = ("挂牌开始日期", "预披露开始日期", "披露开始日期", "信息披露起始日期")
PROGRESS_LOG_EVERY = 10


@dataclass(frozen=True)
class SubmissionConfig:
    data_root: str
    raw_base_dir: str
    output_dir: str
    output_excel_dir: str
    submission_dir: str
    log_dir: str
    resume: bool
    prefer_auto: bool
    mapping_source: str
    filename_max_bytes: int

    def validate(self) -> bool:
        if not os.path.isdir(self.raw_base_dir):
            logger.error(f"raw 目录不存在：{self.raw_base_dir}")
            return False
        if not os.path.isdir(self.output_excel_dir) and self.mapping_source == "excel_only":
            logger.error(f"Excel 目录不存在：{self.output_excel_dir}")
            return False
        if self.mapping_source not in {"excel_only", "excel_then_metadata"}:
            logger.error(f"mapping_source 无效：{self.mapping_source}")
            return False
        if self.filename_max_bytes < 80:
            logger.error(f"filename_max_bytes 过小：{self.filename_max_bytes}")
            return False

        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.submission_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)
        return True


@dataclass(frozen=True)
class SourcePage:
    exact_code: str
    source_path: str
    rel_path: str
    source_kind: str
    source_ext: str
    metadata_name: Optional[str]
    mtime: float


@dataclass(frozen=True)
class ProjectMapping:
    project_name: str
    listing_start_date: Optional[str]


@dataclass(frozen=True)
class ManifestRecord:
    filename: str
    target_rel_path: str
    project_code: str
    project_name: str
    listing_start_date: Optional[str]
    size_bytes: int
    mtime: float
    has_assets_dir: bool
    source_file: str
    source_rel_path: str
    source_kind: str

def _parse_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(int(value))
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    return default


def _safe_filename(name: str, *, max_bytes: int) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", str(name or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned)
    if len(cleaned.encode("utf-8")) <= max_bytes:
        return cleaned or "unnamed"
    trimmed = cleaned.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return trimmed or "unnamed"


def setup_file_logging(log_dir: str) -> str:
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"submission_prepare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    logger.info(f"日志文件：{log_file}")
    return log_file


def load_config_from_runtime() -> SubmissionConfig:
    project_root = _PROJECT_ROOT
    runtime_config_file, payload = load_runtime_config(project_root)

    paths = dict(payload.get("paths") or {})
    data_root = resolve_path(str(paths.get("data_root") or ""), base_dir=project_root)
    output_dir = os.path.join(data_root, "outputs")
    submission_defaults = dict(payload.get("submission_defaults") or {})

    mapping_source = str(submission_defaults.get("mapping_source") or "excel_only").strip().lower()
    if mapping_source not in {"excel_only", "excel_then_metadata"}:
        logger.warning(f"未知 mapping_source={mapping_source}，回退为 excel_only")
        mapping_source = "excel_only"

    filename_max_bytes = submission_defaults.get("filename_max_bytes", 200)
    try:
        filename_max_bytes = int(filename_max_bytes)
    except Exception:
        logger.warning(f"filename_max_bytes 无法解析（{filename_max_bytes}），回退默认值 200")
        filename_max_bytes = 200
    if filename_max_bytes < 80:
        logger.warning(f"filename_max_bytes 过小（{filename_max_bytes}），提升到 80")
        filename_max_bytes = 80

    return SubmissionConfig(
        data_root=data_root,
        raw_base_dir=os.path.join(data_root, "raw"),
        output_dir=output_dir,
        output_excel_dir=resolve_path(str(paths.get("output_excel_dir") or "outputs/excel"), base_dir=data_root),
        submission_dir=os.path.join(output_dir, "submission"),
        log_dir=os.path.join(data_root, "logs"),
        resume=_parse_bool(submission_defaults.get("resume", True), True),
        prefer_auto=_parse_bool(submission_defaults.get("prefer_auto", True), True),
        mapping_source=mapping_source,
        filename_max_bytes=filename_max_bytes,
    )


def select_excel_files(output_excel_dir: str) -> List[str]:
    if not os.path.isdir(output_excel_dir):
        logger.warning(f"Excel 目录不存在：{output_excel_dir}")
        return []

    excel_files: List[Tuple[str, float, int, str]] = []
    for file_name in os.listdir(output_excel_dir):
        if not file_name.lower().endswith(".xlsx"):
            continue
        if file_name.startswith("~$"):
            continue
        file_path = os.path.join(output_excel_dir, file_name)
        try:
            excel_files.append((file_path, os.path.getmtime(file_path), os.path.getsize(file_path), file_name))
        except Exception as exc:
            logger.warning(f"无法读取 Excel 文件属性：{file_name} ({exc})")

    excel_files.sort(key=lambda item: (item[1], item[2], item[3]), reverse=True)
    logger.info(f"Excel 映射文件数：{len(excel_files)}")
    return [item[0] for item in excel_files]


def _find_mapping_columns(frame) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    project_code_col = None
    project_name_col = None
    listing_start_date_col = None
    for col in frame.columns:
        col_text = str(col).strip()
        if project_code_col is None and PROJECT_CODE_COL_TOKEN in col_text:
            project_code_col = col
        if project_name_col is None and PROJECT_NAME_COL_TOKEN in col_text:
            project_name_col = col
        if listing_start_date_col is None and any(token in col_text for token in LISTING_START_DATE_COL_TOKENS):
            listing_start_date_col = col
    return project_code_col, project_name_col, listing_start_date_col


def _normalize_listing_start_date(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return None

    normalized = text.replace(".", "/").replace("-", "/")
    normalized = re.sub(r"\s+", " ", normalized)
    for fmt in ("%Y/%m/%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(normalized, fmt).strftime("%Y/%m/%d")
        except ValueError:
            continue

    match = re.search(r"(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})", normalized)
    if not match:
        return None

    try:
        return datetime(
            year=int(match.group("year")),
            month=int(match.group("month")),
            day=int(match.group("day")),
        ).strftime("%Y/%m/%d")
    except ValueError:
        return None


def _clean_html_text(value: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", str(value or ""))
    cleaned = unescape(cleaned).replace("\xa0", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _read_html_text(source_path: str) -> Optional[str]:
    try:
        with open(source_path, "r", encoding="utf-8-sig") as handle:
            return handle.read()
    except Exception as exc:
        logger.debug(f"读取 HTML 失败：{source_path} ({exc})")
        return None


def _extract_html_project_name(html_text: str) -> Optional[str]:
    patterns = (
        r'<p[^>]+class="bd_detail_name"[^>]*>(.*?)</p>',
        r"<th>\s*项目名称\s*</th>\s*<td[^>]*>(.*?)</td>",
        r"<title>(.*?)</title>",
    )
    for pattern in patterns:
        match = re.search(pattern, html_text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        candidate = _clean_html_text(match.group(1))
        if not candidate:
            continue
        if pattern == r"<title>(.*?)</title>":
            candidate = re.sub(r"^(北交互联[-_]|北京产权交易所[-_])", "", candidate)
            candidate = re.sub(r"[-_]?(北交互联|北京产权交易所)$", "", candidate).strip()
        candidate = re.sub(r"\s*在线路演$", "", candidate).strip()
        if candidate:
            return candidate
    return None


def _extract_html_listing_start_date(html_text: str) -> Optional[str]:
    patterns = (
        r"信息披露起始日期[:：]\s*([0-9./:\- ]+)",
        r"挂牌开始日期[:：]\s*([0-9./:\- ]+)",
        r"预披露开始日期[:：]\s*([0-9./:\- ]+)",
        r"披露开始日期[:：]\s*([0-9./:\- ]+)",
        r"自由报价开始时间[:：]\s*([0-9./:\- ]+)",
        r"报名开始时间[:：]\s*([0-9./:\- ]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, html_text, re.IGNORECASE)
        if not match:
            continue
        normalized = _normalize_listing_start_date(match.group(1))
        if normalized:
            return normalized
    return None


def _extract_mapping_from_source_html(source_path: str) -> Optional[ProjectMapping]:
    if os.path.splitext(source_path)[1].lower() != ".html":
        return None

    html_text = _read_html_text(source_path)
    if not html_text:
        return None

    project_name = _extract_html_project_name(html_text)
    listing_start_date = _extract_html_listing_start_date(html_text)
    if not project_name and not listing_start_date:
        return None

    return ProjectMapping(project_name=project_name or "", listing_start_date=listing_start_date)


def load_mapping_from_excel_files(excel_paths: Iterable[str]) -> Dict[str, ProjectMapping]:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("缺少 pandas/openpyxl，无法读取 Excel 映射") from exc

    mapping: Dict[str, ProjectMapping] = {}
    duplicate_same = 0
    duplicate_conflict = 0

    for excel_path in excel_paths:
        excel_name = os.path.basename(excel_path)
        logger.info(f"读取 Excel：{excel_name}")
        try:
            workbook = pd.read_excel(excel_path, sheet_name=None, dtype=str, keep_default_na=False)
        except Exception as exc:
            logger.error(f"读取 Excel 失败：{excel_name} ({exc})")
            continue

        if not isinstance(workbook, dict):
            workbook = {"Sheet1": workbook}

        for sheet_name, frame in workbook.items():
            if frame is None or frame.empty:
                continue
            project_code_col, project_name_col, listing_start_date_col = _find_mapping_columns(frame)
            if not project_code_col or not project_name_col:
                continue

            for _, row in frame.iterrows():
                project_code = str(row.get(project_code_col, "") or "").strip().upper()
                project_name = str(row.get(project_name_col, "") or "").strip()
                listing_start_date = _normalize_listing_start_date(row.get(listing_start_date_col, "")) if listing_start_date_col else None
                if not project_code or not project_name:
                    continue

                candidate = ProjectMapping(project_name=project_name, listing_start_date=listing_start_date)
                existing = mapping.get(project_code)
                if existing is None:
                    mapping[project_code] = candidate
                    continue
                if existing == candidate:
                    duplicate_same += 1
                    continue

                duplicate_conflict += 1
                logger.warning(
                    "项目编号重复且映射冲突："
                    f"{project_code}（保留=({existing.project_name}, {existing.listing_start_date})，"
                    f"忽略=({candidate.project_name}, {candidate.listing_start_date})，"
                    f"来源={excel_name}#{sheet_name}）"
                )

    logger.info(f"Excel 精确映射条数：{len(mapping)}")
    if duplicate_same:
        logger.info(f"Excel 重复同名条数：{duplicate_same}")
    if duplicate_conflict:
        logger.warning(f"Excel 重复冲突条数：{duplicate_conflict}")
    return mapping


def load_mapping_from_metadata(raw_base_dir: str) -> Dict[str, ProjectMapping]:
    mapping: Dict[str, ProjectMapping] = {}
    for root, _, files in os.walk(raw_base_dir):
        for file_name in files:
            if not file_name.endswith("_metadata.json"):
                continue
            file_path = os.path.join(root, file_name)
            try:
                with open(file_path, "r", encoding="utf-8-sig") as handle:
                    payload = json.load(handle)
            except Exception as exc:
                logger.debug(f"读取 metadata 失败：{file_path} ({exc})")
                continue

            project_code = str(payload.get("code") or payload.get("project_code") or "").strip().upper()
            project_name = str(payload.get("name") or payload.get("project_name") or "").strip()
            if project_code and project_name:
                mapping[project_code] = ProjectMapping(project_name=project_name, listing_start_date=None)

    logger.info(f"metadata 精确映射条数：{len(mapping)}")
    return mapping


def build_mapping(config: SubmissionConfig) -> Dict[str, ProjectMapping]:
    excel_paths = select_excel_files(config.output_excel_dir)
    excel_mapping = load_mapping_from_excel_files(excel_paths) if excel_paths else {}

    if config.mapping_source == "excel_only":
        return excel_mapping

    metadata_mapping = load_mapping_from_metadata(config.raw_base_dir)
    if not excel_mapping:
        return metadata_mapping

    merged = dict(excel_mapping)
    supplemented = 0
    conflicts = 0
    for code, name in metadata_mapping.items():
        existing = merged.get(code)
        if existing is None:
            merged[code] = name
            supplemented += 1
            continue
        if existing != name:
            conflicts += 1

    if supplemented:
        logger.info(f"metadata 补充映射条数：{supplemented}")
    if conflicts:
        logger.warning(f"metadata 与 Excel 冲突条数：{conflicts}（已优先采用 Excel）")
    return merged


def _build_submission_rel_dir(listing_start_date: str) -> Optional[str]:
    normalized = _normalize_listing_start_date(listing_start_date)
    if not normalized:
        return None

    dt = datetime.strptime(normalized, "%Y/%m/%d")
    return os.path.join(f"{dt.year}年挂牌项目", f"{dt.month}月")


def _load_sidecar_metadata(source_path: str) -> Tuple[Optional[str], Optional[str]]:
    metadata_path = f"{os.path.splitext(source_path)[0]}_metadata.json"
    if not os.path.isfile(metadata_path):
        return None, None

    try:
        with open(metadata_path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except Exception as exc:
        logger.debug(f"读取 sidecar metadata 失败：{metadata_path} ({exc})")
        return None, None

    metadata_code = str(payload.get("code") or payload.get("project_code") or "").strip().upper() or None
    metadata_name = str(payload.get("name") or payload.get("project_name") or "").strip() or None
    return metadata_code, metadata_name


def _is_code_like(value: str) -> bool:
    text = str(value or "").strip().upper()
    return bool(text) and bool(SAFE_CODE_RE.fullmatch(text)) and any(ch.isdigit() for ch in text)


def _resolve_exact_code(source_path: str) -> Tuple[Optional[str], Optional[str]]:
    stem = os.path.splitext(os.path.basename(source_path))[0].strip()
    metadata_code, metadata_name = _load_sidecar_metadata(source_path)

    if _is_code_like(stem):
        exact_code = stem.upper()
        if metadata_code and metadata_code != exact_code:
            logger.warning(
                f"文件名编号与 metadata 编号不一致：{os.path.basename(source_path)} "
                f"filename={exact_code} metadata={metadata_code}"
            )
        return exact_code, metadata_name

    if _is_code_like(metadata_code or ""):
        return str(metadata_code).upper(), metadata_name

    return None, metadata_name


def scan_source_pages(raw_base_dir: str) -> Tuple[List[SourcePage], List[str]]:
    pages: List[SourcePage] = []
    unresolved: List[str] = []

    all_paths: List[str] = []
    for root, _, files in os.walk(raw_base_dir):
        for file_name in files:
            ext = os.path.splitext(file_name)[1].lower()
            if ext in TARGET_EXTENSIONS:
                all_paths.append(os.path.join(root, file_name))

    all_paths.sort(key=lambda path: os.path.relpath(path, raw_base_dir).replace("\\", "/").lower())

    for source_path in all_paths:
        exact_code, metadata_name = _resolve_exact_code(source_path)
        if not exact_code:
            unresolved.append(source_path)
            continue

        rel_path = os.path.relpath(source_path, raw_base_dir).replace("\\", "/")
        source_kind = rel_path.split("/", 1)[0].lower() if "/" in rel_path else "other"
        source_ext = os.path.splitext(source_path)[1].lower()
        try:
            mtime = os.path.getmtime(source_path)
        except Exception:
            mtime = 0.0

        pages.append(
            SourcePage(
                exact_code=exact_code,
                source_path=source_path,
                rel_path=rel_path,
                source_kind=source_kind,
                source_ext=source_ext,
                metadata_name=metadata_name,
                mtime=mtime,
            )
        )

    return pages, unresolved


def _source_priority(page: SourcePage, *, prefer_auto: bool) -> Tuple[int, float, str]:
    if page.source_kind == "auto":
        source_rank = 0 if prefer_auto else 1
    elif page.source_kind == "manual":
        source_rank = 1 if prefer_auto else 0
    else:
        source_rank = 2
    return source_rank, -page.mtime, page.rel_path.lower()


def choose_source_page(pages: List[SourcePage], *, prefer_auto: bool) -> SourcePage:
    selected = min(pages, key=lambda item: _source_priority(item, prefer_auto=prefer_auto))
    if len(pages) > 1:
        logger.warning(
            f"项目 {selected.exact_code} 存在 {len(pages)} 个同编号源文件，"
            f"已选择：{selected.rel_path}"
        )
        for alt in sorted(pages, key=lambda item: _source_priority(item, prefer_auto=prefer_auto)):
            if alt.source_path == selected.source_path:
                continue
            logger.warning(f"    其他候选：{alt.rel_path}")
    return selected


def _target_assets_dir(target_path: str) -> str:
    return f"{os.path.splitext(target_path)[0]}_files"


def _source_assets_dir(source_path: str) -> str:
    return f"{os.path.splitext(source_path)[0]}_files"


def _rewrite_html_asset_references(source_path: str, target_path: str) -> None:
    source_assets_name = os.path.basename(_source_assets_dir(source_path))
    target_assets_name = os.path.basename(_target_assets_dir(target_path))
    if source_assets_name == target_assets_name:
        return

    with open(target_path, "rb") as handle:
        content = handle.read()

    updated = content.replace(source_assets_name.encode("utf-8"), target_assets_name.encode("utf-8"))
    if updated == content:
        return

    with open(target_path, "wb") as handle:
        handle.write(updated)


def _is_resume_hit(page: SourcePage, target_path: str) -> bool:
    if not os.path.isfile(target_path):
        return False

    try:
        if os.path.getsize(page.source_path) != os.path.getsize(target_path):
            return False
        if int(os.path.getmtime(page.source_path)) != int(os.path.getmtime(target_path)):
            return False
    except Exception:
        return False

    if page.source_ext == ".html":
        return os.path.isdir(_source_assets_dir(page.source_path)) == os.path.isdir(_target_assets_dir(target_path))
    return True


def _copy_with_assets(page: SourcePage, target_path: str) -> None:
    parent = os.path.dirname(target_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    shutil.copy2(page.source_path, target_path)

    target_assets_dir = _target_assets_dir(target_path)
    source_assets_dir = _source_assets_dir(page.source_path)

    if page.source_ext != ".html":
        if os.path.isdir(target_assets_dir):
            shutil.rmtree(target_assets_dir)
        return

    if os.path.isdir(source_assets_dir):
        if os.path.isdir(target_assets_dir):
            shutil.rmtree(target_assets_dir)
        shutil.copytree(source_assets_dir, target_assets_dir)
        _rewrite_html_asset_references(page.source_path, target_path)
    elif os.path.isdir(target_assets_dir):
        shutil.rmtree(target_assets_dir)


def _build_manifest_record(
    page: SourcePage,
    project: ProjectMapping,
    target_path: str,
    submission_dir: str,
) -> ManifestRecord:
    return ManifestRecord(
        filename=os.path.basename(target_path),
        target_rel_path=os.path.relpath(target_path, submission_dir).replace("\\", "/"),
        project_code=page.exact_code,
        project_name=project.project_name,
        listing_start_date=project.listing_start_date,
        size_bytes=os.path.getsize(target_path),
        mtime=os.path.getmtime(target_path),
        has_assets_dir=os.path.isdir(_target_assets_dir(target_path)),
        source_file=page.source_path,
        source_rel_path=page.rel_path,
        source_kind=page.source_kind,
    )


def write_manifest(submission_dir: str, records: Iterable[ManifestRecord]) -> str:
    manifest_path = os.path.join(submission_dir, "_manifest.json")
    sorted_records = sorted(records, key=lambda item: item.target_rel_path)
    payload = {
        "timestamp": datetime.now().isoformat(),
        "submission_dir": submission_dir,
        "total_files": len(sorted_records),
        "files": [asdict(record) for record in sorted_records],
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    logger.info(f"已生成提交清单：{manifest_path}")
    return manifest_path


def _log_progress(processed: int, total: int, copied: int, skipped: int, failed: int) -> None:
    if total <= 0:
        return
    percent = processed / total * 100
    logger.info(
        f"处理进度：{processed}/{total} ({percent:.1f}%)，"
        f"复制/更新 {copied}，跳过 {skipped}，失败/未命中 {failed}"
    )


def run_submission_prepare(config: SubmissionConfig) -> Tuple[int, int, int, List[str], List[ManifestRecord]]:
    mapping = build_mapping(config)
    if not mapping:
        logger.error("未能加载任何项目映射")
        return 0, 0, 1, ["未能加载任何项目映射"], []

    source_pages, unresolved_sources = scan_source_pages(config.raw_base_dir)
    logger.info(f"扫描到源网页：{len(source_pages)} 个")
    if unresolved_sources:
        logger.warning(f"无法识别精确项目编号的源文件：{len(unresolved_sources)} 个")
        for item in unresolved_sources[:10]:
            logger.warning(f"    {item}")
        if len(unresolved_sources) > 10:
            logger.warning(f"    ... 其余 {len(unresolved_sources) - 10} 个已省略")

    grouped: Dict[str, List[SourcePage]] = {}
    for page in source_pages:
        grouped.setdefault(page.exact_code, []).append(page)

    total_projects = len(grouped)
    logger.info(f"识别出精确项目编号：{total_projects} 个")
    logger.info("开始准备提交文件，终端将定期输出汇总进度")

    copied = 0
    skipped = 0
    failed = len(unresolved_sources)
    failed_items = list(unresolved_sources)
    manifest_records: List[ManifestRecord] = []
    expected_targets = set()
    last_progress_log_at = monotonic()

    for index, exact_code in enumerate(sorted(grouped), start=1):
        page = choose_source_page(grouped[exact_code], prefer_auto=config.prefer_auto)
        try:
            project = mapping.get(exact_code)
            html_fallback = _extract_mapping_from_source_html(page.source_path)
            if project is None and html_fallback and html_fallback.project_name:
                project = html_fallback
                logger.info(f"使用 HTML 回退映射：{exact_code} ({page.rel_path})")
            elif project is not None and html_fallback:
                if not project.project_name and html_fallback.project_name:
                    project = ProjectMapping(
                        project_name=html_fallback.project_name,
                        listing_start_date=project.listing_start_date,
                    )
                if not project.listing_start_date and html_fallback.listing_start_date:
                    project = ProjectMapping(
                        project_name=project.project_name,
                        listing_start_date=html_fallback.listing_start_date,
                    )
            if not project:
                failed += 1
                failed_items.append(f"缺失精确映射：{exact_code} -> {page.rel_path}")
                logger.warning(f"缺失精确映射：{exact_code} ({page.rel_path})")
                continue
            if not project.listing_start_date:
                failed += 1
                failed_items.append(f"缺失挂牌开始日期：{exact_code} -> {page.rel_path}")
                logger.warning(f"缺失挂牌开始日期：{exact_code} ({page.rel_path})")
                continue

            target_rel_dir = _build_submission_rel_dir(project.listing_start_date)
            if not target_rel_dir:
                failed += 1
                failed_items.append(f"挂牌开始日期无法解析：{exact_code} -> {project.listing_start_date}")
                logger.warning(f"挂牌开始日期无法解析：{exact_code} ({project.listing_start_date})")
                continue

            safe_name = _safe_filename(project.project_name, max_bytes=config.filename_max_bytes)
            target_filename = f"{exact_code}-{safe_name}{page.source_ext}"
            if target_rel_dir and target_rel_dir != ".":
                target_path = os.path.join(config.submission_dir, target_rel_dir, target_filename)
                expected_targets.add(os.path.join(target_rel_dir, target_filename).replace("\\", "/"))
            else:
                target_path = os.path.join(config.submission_dir, target_filename)
                expected_targets.add(target_filename)

            if config.resume and _is_resume_hit(page, target_path):
                if page.source_ext == ".html" and os.path.isdir(_target_assets_dir(target_path)):
                    _rewrite_html_asset_references(page.source_path, target_path)
                skipped += 1
                manifest_records.append(_build_manifest_record(page, project, target_path, config.submission_dir))
                continue

            _copy_with_assets(page, target_path)
            copied += 1
            manifest_records.append(_build_manifest_record(page, project, target_path, config.submission_dir))
        except Exception as exc:
            failed += 1
            failed_items.append(f"复制失败：{exact_code} ({exc})")
            logger.error(f"复制失败：{exact_code} ({exc})")
        finally:
            now = monotonic()
            if index == total_projects or (now - last_progress_log_at) >= PROGRESS_LOG_EVERY:
                _log_progress(index, total_projects, copied, skipped, failed)
                last_progress_log_at = now

    stale_targets = []
    for root, _, files in os.walk(config.submission_dir):
        for file_name in files:
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in TARGET_EXTENSIONS:
                continue
            full_path = os.path.join(root, file_name)
            rel_path = os.path.relpath(full_path, config.submission_dir).replace("\\", "/")
            if rel_path not in expected_targets:
                stale_targets.append(rel_path)
    if stale_targets:
        logger.warning(f"提交目录中存在未在本轮映射中的旧文件：{len(stale_targets)} 个")
        for file_name in stale_targets[:10]:
            logger.warning(f"    {file_name}")
        if len(stale_targets) > 10:
            logger.warning(f"    ... 其余 {len(stale_targets) - 10} 个已省略")

    return copied, skipped, failed, failed_items, manifest_records


def main() -> int:
    logger.info("=" * 70)
    logger.info("Property Exchange Automation Platform - 提交文件准备脚本")
    logger.info("=" * 70)

    try:
        config = load_config_from_runtime()
    except Exception as exc:
        logger.error(str(exc))
        return 2

    logger.info(f"数据根目录：{config.data_root}")
    logger.info(f"raw 目录：{config.raw_base_dir}")
    logger.info(f"Excel 目录：{config.output_excel_dir}")
    logger.info(f"提交目标目录：{config.submission_dir}")
    logger.info(
        "策略："
        f"resume={config.resume}, prefer_auto={config.prefer_auto}, "
        f"mapping_source={config.mapping_source}, filename_max_bytes={config.filename_max_bytes}"
    )

    if not config.validate():
        logger.error("配置验证失败")
        return 2

    setup_file_logging(config.log_dir)

    copied, skipped, failed, failed_items, manifest_records = run_submission_prepare(config)
    if not manifest_records and failed > 0:
        logger.error("没有生成任何提交文件")
        return 1

    manifest_path = write_manifest(config.submission_dir, manifest_records)

    logger.info("=" * 70)
    logger.info("提交准备完成")
    logger.info("=" * 70)
    logger.info(f"本次复制/更新：{copied} 个文件")
    logger.info(f"跳过未变化：{skipped} 个文件")
    logger.info(f"失败/未命中：{failed} 个")
    logger.info(f"提交目录：{config.submission_dir}")
    logger.info(f"清单文件：{manifest_path}")
    if failed_items:
        for item in failed_items[:20]:
            logger.warning(f"    {item}")
        if len(failed_items) > 20:
            logger.warning(f"    ... 其余 {len(failed_items) - 20} 个已省略")
    logger.info("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("用户中断执行")
        sys.exit(130)
    except Exception as exc:
        logger.exception(f"未捕获异常：{exc}")
        sys.exit(1)
