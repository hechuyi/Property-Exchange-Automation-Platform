"""Helpers for saving rendered pages with companion asset folders."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import ssl
import urllib.parse
import urllib.request
from typing import Dict, Optional

from bs4 import BeautifulSoup

TAG_ASSET_ATTRS = (
    ("link", "href"),
    ("img", "src"),
    ("img", "data-original"),
    ("img", "data-src"),
    ("source", "src"),
    ("video", "poster"),
)


def snapshot_assets_dir(html_path: str) -> str:
    return f"{os.path.splitext(html_path)[0]}_files"


def is_snapshot_complete(html_path: str) -> bool:
    return os.path.isfile(html_path) and os.path.isdir(snapshot_assets_dir(html_path))


def remove_snapshot(html_path: str) -> None:
    assets_dir = snapshot_assets_dir(html_path)
    if os.path.isfile(html_path):
        os.remove(html_path)
    if os.path.isdir(assets_dir):
        shutil.rmtree(assets_dir, ignore_errors=True)


def _is_skip_asset_url(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        not lowered
        or lowered.startswith("#")
        or lowered.startswith("data:")
        or lowered.startswith("javascript:")
        or lowered.startswith("mailto:")
        or lowered.startswith("tel:")
        or lowered.startswith("blob:")
    )


def _guess_ext_from_content_type(content_type: str) -> str:
    text = (content_type or "").lower()
    if "javascript" in text:
        return ".js"
    if "css" in text:
        return ".css"
    if "png" in text:
        return ".png"
    if "jpeg" in text or "jpg" in text:
        return ".jpg"
    if "svg" in text:
        return ".svg"
    if "gif" in text:
        return ".gif"
    if "webp" in text:
        return ".webp"
    if "woff2" in text:
        return ".woff2"
    if "woff" in text:
        return ".woff"
    if "ttf" in text:
        return ".ttf"
    if "eot" in text:
        return ".eot"
    return ""


class SnapshotSaver:
    """Persist rendered HTML with linked static assets for offline inspection."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: int,
        ssl_context: Optional[ssl.SSLContext] = None,
    ):
        self.user_agent = str(user_agent or "Mozilla/5.0")
        self.timeout = max(5, int(timeout))
        self.ssl_context = ssl_context

    def save_complete_page(self, *, rendered_html: str, page_url: str, html_path: str) -> None:
        base_name = os.path.splitext(os.path.basename(html_path))[0]
        final_assets_dir = snapshot_assets_dir(html_path)
        temp_assets_dir = f"{final_assets_dir}.part"
        temp_html_path = f"{html_path}.part"

        if os.path.isdir(temp_assets_dir):
            shutil.rmtree(temp_assets_dir, ignore_errors=True)
        if os.path.isfile(temp_html_path):
            os.remove(temp_html_path)

        try:
            os.makedirs(temp_assets_dir, exist_ok=True)

            soup = BeautifulSoup(rendered_html, "html.parser")
            for script in soup.find_all("script"):
                script.decompose()
            for link in soup.find_all("link"):
                rel = [str(item).lower() for item in (link.get("rel") or [])]
                if any(item in {"prefetch", "preload", "modulepreload"} for item in rel):
                    link.decompose()

            downloaded_by_url: Dict[str, str] = {}
            source_url_by_local: Dict[str, str] = {}

            for tag_name, attr_name in TAG_ASSET_ATTRS:
                for node in soup.find_all(tag_name):
                    raw_value = node.get(attr_name)
                    if not raw_value:
                        continue
                    local_name = self._download_asset(
                        raw_url=str(raw_value),
                        base_url=page_url,
                        assets_dir=temp_assets_dir,
                        downloaded_by_url=downloaded_by_url,
                        source_url_by_local=source_url_by_local,
                    )
                    if not local_name:
                        continue
                    local_ref = f"{base_name}_files/{local_name}"
                    node[attr_name] = local_ref
                    if tag_name == "img" and attr_name != "src" and not node.get("src"):
                        node["src"] = local_ref

            for local_name, source_url in list(source_url_by_local.items()):
                if not local_name.lower().endswith(".css"):
                    continue
                css_path = os.path.join(temp_assets_dir, local_name)
                self._rewrite_css_assets(
                    css_path=css_path,
                    css_source_url=source_url,
                    assets_dir=temp_assets_dir,
                    downloaded_by_url=downloaded_by_url,
                    source_url_by_local=source_url_by_local,
                )

            with open(temp_html_path, "w", encoding="utf-8") as handle:
                handle.write(str(soup))

            remove_snapshot(html_path)
            os.replace(temp_assets_dir, final_assets_dir)
            os.replace(temp_html_path, html_path)
        except Exception:
            if os.path.isdir(temp_assets_dir):
                shutil.rmtree(temp_assets_dir, ignore_errors=True)
            if os.path.isfile(temp_html_path):
                try:
                    os.remove(temp_html_path)
                except OSError:
                    pass
            raise

    def _urlopen(self, request: urllib.request.Request):
        return urllib.request.urlopen(
            request,
            timeout=self.timeout,
            context=self.ssl_context,
        )

    def _download_asset(
        self,
        *,
        raw_url: str,
        base_url: str,
        assets_dir: str,
        downloaded_by_url: Dict[str, str],
        source_url_by_local: Dict[str, str],
    ) -> Optional[str]:
        value = str(raw_url or "").strip().strip("'\"")
        if _is_skip_asset_url(value):
            return None

        absolute_url = urllib.parse.urljoin(base_url, value)
        parsed = urllib.parse.urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            return None
        if absolute_url in downloaded_by_url:
            return downloaded_by_url[absolute_url]

        request = urllib.request.Request(
            absolute_url,
            headers={"User-Agent": self.user_agent, "Referer": base_url},
        )
        try:
            with self._urlopen(request) as response:
                content = response.read()
                content_type = response.headers.get("Content-Type", "")
        except Exception:
            return None

        basename = os.path.basename(parsed.path)
        basename = re.sub(r"[\\/:*?\"<>|]+", "_", basename)
        if not basename:
            digest = hashlib.md5(absolute_url.encode("utf-8")).hexdigest()[:12]
            basename = f"asset_{digest}"

        if not os.path.splitext(basename)[1]:
            guessed = _guess_ext_from_content_type(content_type)
            if guessed:
                basename = f"{basename}{guessed}"

        final_name = basename
        counter = 1
        while os.path.exists(os.path.join(assets_dir, final_name)):
            root, ext = os.path.splitext(basename)
            final_name = f"{root}__{counter}{ext}"
            counter += 1

        with open(os.path.join(assets_dir, final_name), "wb") as handle:
            handle.write(content)

        downloaded_by_url[absolute_url] = final_name
        source_url_by_local[final_name] = absolute_url
        return final_name

    def _rewrite_css_assets(
        self,
        *,
        css_path: str,
        css_source_url: str,
        assets_dir: str,
        downloaded_by_url: Dict[str, str],
        source_url_by_local: Dict[str, str],
    ) -> None:
        try:
            raw = open(css_path, "rb").read()
        except OSError:
            return

        try:
            text = raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            try:
                text = raw.decode("gb18030")
                encoding = "gb18030"
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
                encoding = "latin-1"

        def replace(match: re.Match[str]) -> str:
            inside = match.group(1).strip().strip("'\"")
            if _is_skip_asset_url(inside):
                return match.group(0)

            local_name = self._download_asset(
                raw_url=inside,
                base_url=css_source_url,
                assets_dir=assets_dir,
                downloaded_by_url=downloaded_by_url,
                source_url_by_local=source_url_by_local,
            )
            if not local_name:
                return match.group(0)
            return f"url('{local_name}')"

        updated = re.sub(r"url\(([^)]+)\)", replace, text)
        if updated != text:
            with open(css_path, "w", encoding=encoding, errors="ignore") as handle:
                handle.write(updated)
