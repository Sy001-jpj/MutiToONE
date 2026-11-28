# -*- coding: utf-8 -*-
"""外部组件（目前只有 LinkSwift）的版本检查与下载。

设计取舍：
* LinkSwift 是 AGPL-3.0，**不随本程序分发**，只在用户点击时从官方仓库下载，
  这样是用户自己获取，不构成我们再分发。
* 官方默认分支是 dev（不是 main），脚本文件名带全角括号，需要 URL 编码。
"""
import json
import os
import re
import subprocess
import time
import urllib.request
import webbrowser

from .paths import APP_ROOT

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MutiToONE"}

_NAME_ENC = ("%EF%BC%88%E6%94%B9%EF%BC%89%E7%BD%91%E7%9B%98%E7%9B%B4%E9%93%BE"
             "%E4%B8%8B%E8%BD%BD%E5%8A%A9%E6%89%8B.user.js")

COMPONENTS = {
    "linkswift": {
        "name": "LinkSwift（网盘直链下载助手）",
        "filename": "LinkSwift.user.js",
        "repo": "https://github.com/hmjz100/LinkSwift",
        "note": "安装到浏览器的油猴（Tampermonkey）里，负责在网盘页面提供下载按钮。",
        # 顺序：官方直连 → 大厂 CDN → 第三方反代（仅在前两者都不通时使用）
        "mirrors": [
            "https://raw.githubusercontent.com/hmjz100/LinkSwift/dev/" + _NAME_ENC,
            "https://fastly.jsdelivr.net/gh/hmjz100/LinkSwift@dev/" + _NAME_ENC,
            "https://gcore.jsdelivr.net/gh/hmjz100/LinkSwift@dev/" + _NAME_ENC,
            "https://gh-proxy.com/https://raw.githubusercontent.com/"
            "hmjz100/LinkSwift/dev/" + _NAME_ENC,
        ],
    }
}


def component_dir():
    d = os.path.join(APP_ROOT, "组件")
    os.makedirs(d, exist_ok=True)
    return d


def _state_path():
    return os.path.join(component_dir(), "components.json")


def _load_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


VER_RE = re.compile(r"//\s*@version\s+([0-9][0-9A-Za-z.\-]*)")


def parse_version(text):
    m = VER_RE.search(text[:8000])
    return m.group(1) if m else None


def version_tuple(v):
    if not v:
        return (0,)
    parts = re.split(r"[.\-+]", v)
    out = []
    for p in parts:
        out.append(int(p) if p.isdigit() else 0)
    return tuple(out)


def local_version(cid="linkswift"):
    """本地已下载的版本号。

    优先读磁盘上那份脚本的实际 `// @version`，读不到才退回下载时记录的值，
    这样你手动替换了 组件\\LinkSwift.user.js 也能显示正确版本。
    """
    p = local_path(cid)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                ver = parse_version(f.read(8000))
            if ver:
                return ver
        except Exception:
            pass
    st = _load_state().get(cid) or {}
    return st.get("version")


def local_path(cid="linkswift"):
    fn = COMPONENTS[cid]["filename"]
    return os.path.join(component_dir(), fn)


def _fetch(url, timeout=25, head_bytes=None):
    req = urllib.request.Request(url, headers=UA)
    if head_bytes:
        req.add_header("Range", "bytes=0-%d" % (head_bytes - 1))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if head_bytes and len(data) > head_bytes:
            data = data[:head_bytes]
        return data


def check_update(cid="linkswift"):
    """去官方仓库看最新版本号。只取文件头部，省流量。"""
    errors = []
    for url in COMPONENTS[cid]["mirrors"]:
        try:
            head = _fetch(url, head_bytes=8192).decode("utf-8", "ignore")
            ver = parse_version(head)
            if ver:
                return {
                    "ok": True,
                    "remote_version": ver,
                    "local_version": local_version(cid),
                    "has_update": version_tuple(ver) > version_tuple(local_version(cid)),
                    "source": url.split("/")[2],
                }
        except Exception as exc:
            errors.append("%s: %s" % (url.split("/")[2], exc))
    return {"ok": False, "error": "拿不到最新版本（网络或镜像不可用）", "detail": errors}


def download(cid="linkswift"):
    """下载脚本全文并落盘，返回 (版本号, 路径)。"""
    errors = []
    for url in COMPONENTS[cid]["mirrors"]:
        try:
            data = _fetch(url, timeout=90)
            if len(data) < 10000:
                errors.append("%s: 内容异常（%d 字节）" % (url.split("/")[2], len(data)))
                continue
            text = data.decode("utf-8", "ignore")
            ver = parse_version(text)
            path = local_path(cid)
            with open(path, "wb") as f:
                f.write(data)
            st = _load_state()
            st[cid] = {
                "version": ver,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "bytes": len(data),
                "source": url,
            }
            _save_state(st)
            return {"ok": True, "version": ver, "path": path, "bytes": len(data)}
        except Exception as exc:
            errors.append("%s: %s" % (url.split("/")[2], exc))
    return {"ok": False, "error": "下载失败（网络或镜像不可用）", "detail": errors}


def _env_path(p):
    return os.path.expandvars(os.path.expanduser(p))


_BROWSERS = [
    {
        "name": "Chrome",
        "exe": [r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
                r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
                r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"],
        "data": r"%LOCALAPPDATA%\Google\Chrome\User Data",
    },
    {
        "name": "Edge",
        "exe": [r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
                r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"],
        "data": r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
    },
]


_EXT_ID_RE = re.compile(r'"([a-p]{32})"\s*:\s*\{')


def _find_tampermonkey():
    """在 Chrome / Edge 的用户配置里找 Tampermonkey 的扩展 ID。

    注意：Chromium 的 Secure Preferences 里不一定有 manifest.name，
    但整段配置文本里会出现 "Tampermonkey"（如 action.default_title）。
    所以做法是：搜到这个词，再往前找最近的那个 32 位扩展 ID。
    """
    for b in _BROWSERS:
        exe = None
        for cand in b["exe"]:
            p = _env_path(cand)
            if os.path.exists(p):
                exe = p
                break
        if not exe:
            continue
        data_dir = _env_path(b["data"])
        if not os.path.isdir(data_dir):
            continue
        try:
            profiles = [d for d in os.listdir(data_dir)
                        if os.path.isdir(os.path.join(data_dir, d))]
        except Exception:
            continue
        for prof in profiles:
            for fn in ("Secure Preferences", "Preferences"):
                f = os.path.join(data_dir, prof, fn)
                if not os.path.exists(f):
                    continue
                try:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                        text = fh.read()
                except Exception:
                    continue
                low = text.lower()
                pos = low.find("tampermonkey")
                if pos < 0:
                    continue
                ids = _EXT_ID_RE.findall(text[:pos])
                if ids:
                    return {"browser": b["name"], "exe": exe, "id": ids[-1],
                            "profile": prof}
    return None


STORE_URLS = {
    "Chrome": "https://chromewebstore.google.com/detail/tampermonkey/"
              "dhdgffkkebhmkfjojejmpbldmpobfkfo",
    "Edge": "https://microsoftedge.microsoft.com/addons/detail/tampermonkey/"
            "iikmkjmpaadaobahmlepeloendndfphd",
}
TM_HOME = "https://www.tampermonkey.net/"


def detect_browser():
    """找出机器上装了哪个受支持的浏览器。"""
    for b in _BROWSERS:
        for cand in b["exe"]:
            p = _env_path(cand)
            if os.path.exists(p):
                return {"name": b["name"], "exe": p}
    return None


def has_tampermonkey():
    return _find_tampermonkey() is not None


def store_url():
    b = detect_browser()
    return STORE_URLS.get(b["name"], TM_HOME) if b else TM_HOME


def open_store():
    """打开 Tampermonkey 的安装页（按浏览器跳对应扩展商店）。"""
    b = detect_browser()
    url = STORE_URLS.get(b["name"], TM_HOME) if b else TM_HOME
    try:
        if b:
            subprocess.Popen([b["exe"], url])
        else:
            webbrowser.open(url)
        return {"ok": True, "url": url, "browser": b["name"] if b else None}
    except Exception as exc:
        return {"ok": False, "error": "打开浏览器失败：%s" % exc}


def open_tampermonkey():
    """打开 Tampermonkey 的管理面板；没装就打开安装页。"""
    hit = _find_tampermonkey()
    if hit:
        url = "chrome-extension://%s/options.html#nav=dashboard" % hit["id"]
        try:
            subprocess.Popen([hit["exe"], url])
            return {"ok": True, "mode": "dashboard",
                    "browser": hit["browser"], "id": hit["id"], "url": url}
        except Exception as exc:
            return {"ok": False, "error": "启动浏览器失败：%s" % exc}

    res = open_store()
    if not res.get("ok"):
        return res
    return {"ok": True, "mode": "store", "url": res["url"],
            "browser": res.get("browser"),
            "message": "没检测到 Tampermonkey，已打开%s的安装页"
                       % (res.get("browser") or "浏览器")}


def list_components():
    tm = _find_tampermonkey()
    browser = (tm or {}).get("browser") or (detect_browser() or {}).get("name")
    out = []
    for cid, meta in COMPONENTS.items():
        p = local_path(cid)
        out.append({
            "id": cid,
            "name": meta["name"],
            "repo": meta["repo"],
            "note": meta["note"],
            "local_version": local_version(cid),
            "installed": os.path.exists(p),
            "path": p,
            "tampermonkey": bool(tm),
            "browser": browser,
            "store_url": store_url(),
        })
    return out
