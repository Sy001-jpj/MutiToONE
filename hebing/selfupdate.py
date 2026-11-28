# -*- coding: utf-8 -*-
"""MutiToONE 自身的版本检查与自动更新。

更新源是 GitHub 仓库，配置写在 `应用程序\更新设置.json`：

    {
      "repo": "yourname/MutiToONE",   // 空字符串 = 未配置，检查时会提示
      "auto": true                     // 检测到新版本是否自动下载并替换
    }

更新方式：下载仓库的 zip 包（某一 tag），解压后覆盖程序文件，
**不动** 输出 / 组件 / 窗口设置.json / 更新设置.json 这些用户数据。
"""
import io
import json
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile

from . import __version__
from .paths import APP_ROOT

UA = {"User-Agent": "MutiToONE-updater"}
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROTECTED = {"输出", "组件", "窗口设置.json", "更新设置.json", "启动日志.txt",
             "_internal", "MutiToONE_旧版浏览器模式.exe"}


def _conf_path():
    return os.path.join(APP_ROOT, "更新设置.json")


def load_conf():
    try:
        with open(_conf_path(), "r", encoding="utf-8") as f:
            conf = json.load(f)
    except Exception:
        conf = {}
    conf.setdefault("repo", "")
    conf.setdefault("auto", True)
    return conf


def save_conf(conf):
    with open(_conf_path(), "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)


def version_tuple(v):
    parts = str(v or "0").lstrip("vV").split(".")
    out = []
    for p in parts:
        digits = "".join(ch for ch in p if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _api_candidates(repo):
    """GitHub 接口的可用地址（国内直连常常不通，给几个反代）。"""
    return [
        "https://api.github.com/repos/%s/releases/latest" % repo,
        "https://gh-proxy.com/https://api.github.com/repos/%s/releases/latest" % repo,
    ]


def _download_candidates(data):
    """从 release 数据里挑出所有可用的下载地址，按优先级排。

    优先用我们上传的 zip 资源（结构可控），实在没有再退回源码 zipball。
    每个地址都补一条 gh-proxy 反代，直连不通时自动走备用。
    """
    urls = []
    for asset in (data.get("assets") or []):
        name = (asset.get("name") or "").lower()
        if not name.endswith(".zip"):
            continue
        raw = asset.get("browser_download_url") or ""
        if raw:
            urls.append(raw)
            urls.append("https://gh-proxy.com/" + raw)
    ball = data.get("zipball_url")
    if ball:
        urls.append(ball)
        urls.append("https://gh-proxy.com/" + ball)
    return urls


def check():
    """返回最新版本信息；未配置仓库时给出明确提示。"""
    conf = load_conf()
    repo = (conf.get("repo") or "").strip()
    if not repo:
        return {
            "configured": False,
            "current": __version__,
            "message": "未配置更新源：需要在 更新设置.json 里填 GitHub 仓库（owner/repo）",
        }

    errors = []
    for url in _api_candidates(repo):
        try:
            data = json.loads(_get(url).decode("utf-8", "ignore"))
            tag = data.get("tag_name") or data.get("name") or ""
            latest = tag.lstrip("vV")
            urls = _download_candidates(data)
            return {
                "configured": True,
                "current": __version__,
                "latest": latest or None,
                "tag": tag,
                "has_update": version_tuple(latest) > version_tuple(__version__),
                "zip_url": urls[0] if urls else None,
                "zip_urls": urls,
                "html_url": data.get("html_url"),
                "published_at": data.get("published_at") or "",
                "body": (data.get("body") or "").strip(),
                "repo": repo,
                "source": url.split("/")[2],
            }
        except Exception as exc:
            errors.append("%s: %s" % (url.split("/")[2], exc))
    return {
        "configured": True,
        "current": __version__,
        "error": "拿不到最新版本（网络或镜像不可用）",
        "detail": errors,
    }


def _safe_extract(zip_path, dst):
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            target = os.path.join(dst, name)
            if not os.path.abspath(target).startswith(os.path.abspath(dst) + os.sep):
                continue
            z.extract(name, dst)


def _find_package_dir(root):
    """在解压结果里找到 hebing 包（兼容两种结构）。

    * 我们上传的资源包：  hebing\\__init__.py
    * GitHub 源码 zipball： <repo>-<tag>\\hebing\\__init__.py
    """
    if os.path.isfile(os.path.join(root, "hebing", "__init__.py")):
        return os.path.join(root, "hebing")
    for dirpath, _dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath).lower() == "hebing" and "__init__.py" in filenames:
            return dirpath
    return None


def _fetch_any(urls, dst_path, timeout=180):
    """按顺序尝试下载，成功返回实际使用的地址。"""
    last = None
    for url in urls:
        try:
            with open(dst_path, "wb") as f:
                f.write(_get(url, timeout=timeout))
            if os.path.getsize(dst_path) > 1024:
                return url
        except Exception as exc:
            last = exc
    raise RuntimeError("下载失败：%s" % (last,))


def apply_update(zip_url=None, zip_urls=None):
    """下载并覆盖程序文件。返回被替换的文件数。"""
    info = {} if (zip_url or zip_urls) else check()
    urls = list(zip_urls or [])
    if zip_url:
        urls.insert(0, zip_url)
    if not urls:
        urls = list(info.get("zip_urls") or [])
    if not urls and info.get("zip_url"):
        urls = [info["zip_url"]]
    if not urls:
        raise RuntimeError(info.get("error") or info.get("message") or "没有可用的下载地址")

    tmp = tempfile.mkdtemp(prefix="mutitoo_upd_")
    try:
        zip_path = os.path.join(tmp, "update.zip")
        used = _fetch_any(urls, zip_path)

        extract_dir = os.path.join(tmp, "x")
        os.makedirs(extract_dir, exist_ok=True)
        _safe_extract(zip_path, extract_dir)

        pkg_src = _find_package_dir(extract_dir)
        if not pkg_src:
            raise RuntimeError("下载包里没有找到 hebing 目录，更新中止")

        # 只替换程序文件，用户数据（输出/日志/组件/设置）一概不碰
        replaced = 0
        for name in os.listdir(pkg_src):
            if name == "__pycache__":
                continue
            s = os.path.join(pkg_src, name)
            d = os.path.join(PACKAGE_DIR, name)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
            replaced += 1

        src_root = os.path.dirname(pkg_src)
        for name in ("hebing_gui.py", "desktop_main.py", "requirements.txt",
                     "启动工作台.bat", "README.md"):
            s = os.path.join(src_root, name)
            if os.path.isfile(s):
                try:
                    shutil.copy2(s, os.path.join(os.path.dirname(PACKAGE_DIR), name))
                    replaced += 1
                except Exception:
                    pass
        return replaced
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
