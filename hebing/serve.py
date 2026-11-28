# -*- coding: utf-8 -*-
"""本地服务：浏览器里点一下，把目录里的 PDF 合成一本合订本。

不依赖第三方 Web 框架，只用标准库；构建在后台线程跑，前端轮询进度。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

from .classify import (scan_pdfs, pair_years, describe, default_dir, label_of)
from .build import build_year
from .render import render_docx
from . import components as comp
from . import selfupdate as upd

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))

from .paths import APP_ROOT  # noqa: E402  （打包后指向 exe 目录）

ROOT = APP_ROOT
OUTDIR = os.path.join(ROOT, "输出")
DOCX2PDF = os.path.join(HERE, "docx2pdf.ps1")
WEBUI = os.path.join(HERE, "webui.html")
COMPONENTS_HTML = os.path.join(HERE, "components.html")
UPDATE_HTML = os.path.join(HERE, "update.html")
TITLE = "江苏省公务员录用考试《行政职业能力测验》（A类）真题与解析合订本"

_LOCK = threading.Lock()
_OUTPUTS = {}          # 文件名 -> 绝对路径（支持存到任意目录）
STATE = {
    "status": "idle",     # idle | running | done | error
    "progress": 0,
    "message": "",
    "log": [],
    "word": None,
    "pdf": None,
}


def _set(**kw):
    with _LOCK:
        STATE.update(kw)


def _log(line):
    with _LOCK:
        STATE["log"] = (STATE["log"] + [line])[-300:]


def _snapshot():
    with _LOCK:
        return dict(STATE)


def upd_status():
    from . import __version__
    conf = upd.load_conf()
    return {
        "current": __version__,
        "repo": conf.get("repo", ""),
        "auto": conf.get("auto", True),
        "configured": bool((conf.get("repo") or "").strip()),
    }


def _demo_release():
    """预览开关：设置环境变量 MUTITOO_DEMO_RELEASE=1 时，返回一条假发布信息，
    方便在没有 GitHub 仓库时预览「发现新版本」卡片的样式。"""
    if os.environ.get("MUTITOO_DEMO_RELEASE") != "1":
        return None
    from . import __version__
    return {
        "configured": True,
        "current": __version__,
        "latest": "0.3.1",
        "tag": "v0.3.1",
        "has_update": True,
        "zip_url": "",
        "html_url": "",
        "published_at": "2026-11-26T09:54:31Z",
        "body": ("### 界面优化\n"
                 "- 右上角新增「说明」按钮，把功能、用法、LinkSwift 说明集中到一个弹框。\n"
                 "- 「组件」窗新增 MutiToONE 本体更新检查，发现新版本时弹出更新卡片。\n"
                 "### 修复\n"
                 "- 修正用 pythonw 启动时没有控制台、窗口不出现的问题。\n"),
        "repo": "demo/MutiToONE",
        "source": "preview",
        "demo": True,
    }


# ------------------------------------------------------------------ 通知中心
_NOTICES = []
_NOTICE_LOCK = threading.Lock()
_WATCH = {"last": 0, "busy": False}


def _add_notice(notice):
    """同 id 的通知只保留一条，不重复堆叠。"""
    with _NOTICE_LOCK:
        for i, old in enumerate(_NOTICES):
            if old.get("id") == notice.get("id"):
                old.update(notice)
                return old
        notice.setdefault("read", False)
        _NOTICES.insert(0, notice)
        del _NOTICES[40:]
        return notice


def _notices_snapshot():
    with _NOTICE_LOCK:
        items = [dict(n) for n in _NOTICES]
    return {"items": items, "unread": sum(1 for n in items if not n.get("read"))}


def _mark_all_read():
    with _NOTICE_LOCK:
        for n in _NOTICES:
            n["read"] = True


def refresh_update_notice(force=False, auto_apply=True, interval=600):
    """查一次更新，有新版本就往通知里塞一条（可选自动更新）。"""
    now = time.time()
    if not force and (now - _WATCH["last"] < interval or _WATCH["busy"]):
        return
    _WATCH["last"] = now
    _WATCH["busy"] = True
    try:
        info = _demo_release() or upd.check()
        if not info.get("configured") or not info.get("has_update"):
            return
        latest = info.get("latest")
        notice = {
            "id": "update-%s" % latest,
            "kind": "update",
            "title": "发现新版本 %s" % latest,
            "desc": "当前 %s。点击查看更新内容并立即更新。" % info.get("current"),
            "version": latest,
            "at": time.strftime("%m-%d %H:%M"),
            "action": "open-update",
        }
        conf = upd.load_conf()
        if auto_apply and conf.get("auto", True) and not info.get("demo"):
            try:
                n = upd.apply_update(info.get("zip_url"), info.get("zip_urls"))
                notice["title"] = "已更新到 %s" % latest
                notice["desc"] = "已替换 %d 个文件，重启 MutiToONE 后生效。" % n
                notice["action"] = "restart-hint"
            except Exception as exc:
                notice["desc"] = "自动更新失败：%s。点击查看详情。" % exc
        _add_notice(notice)
    except Exception:
        pass
    finally:
        _WATCH["busy"] = False


def start_update_watcher(interval=1800):
    """后台定时检查更新（桌面版启动时调用一次即可）。"""
    def loop():
        refresh_update_notice(force=True)
        while True:
            time.sleep(interval)
            refresh_update_notice(force=True)
    threading.Thread(target=loop, daemon=True).start()


# ------------------------------------------------------------------ 生成日志
LOG_CONF = os.path.join(ROOT, "日志设置.json")
LOG_DEFAULT_DIR = os.path.join(ROOT, "日志")
_LOG_LOCK = threading.Lock()


def log_dir():
    try:
        with open(LOG_CONF, "r", encoding="utf-8") as f:
            d = (json.load(f) or {}).get("dir")
        if d:
            return d
    except Exception:
        pass
    return LOG_DEFAULT_DIR


def set_log_dir(path):
    path = os.path.abspath(path)
    os.makedirs(path, exist_ok=True)
    with open(LOG_CONF, "w", encoding="utf-8") as f:
        json.dump({"dir": path}, f, ensure_ascii=False, indent=2)
    return path


def log_file():
    return os.path.join(log_dir(), "生成记录.txt")


def _fmt_record(rec):
    """把一条记录写成人类可读的文本（两行：摘要 + 明细）。"""
    ok = rec.get("status") == "ok"
    if ok:
        body = "%s · %d 题 · 缺解析 %d · %.1fs" % (
            rec.get("years", ""), rec.get("count", 0),
            rec.get("missing", 0), rec.get("seconds", 0))
        if rec.get("pdf"):
            body += " · 含 PDF"
    else:
        body = "错误：%s" % rec.get("error", "")
    out = ["%s  %s  %s" % (rec.get("time", ""), "成功" if ok else "失败", body)]
    if rec.get("src"):
        out.append("    源目录：%s" % rec["src"])
    if ok and rec.get("word"):
        out.append("    输出：%s" % os.path.join(rec.get("outdir", ""), rec["word"]))
    return "\n".join(out) + "\n"


def append_log(rec):
    """一次生成追加一段可读文本到 生成记录.txt。"""
    with _LOG_LOCK:
        os.makedirs(log_dir(), exist_ok=True)
        with open(log_file(), "a", encoding="utf-8") as f:
            f.write(_fmt_record(rec))


_HEAD = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(成功|失败)\s+(.*)$")
_DETAIL = re.compile(r"^\s+(源目录|输出)[:：](.+)$")


def read_logs(limit=200):
    """把 .txt 里的记录解析回结构化数据（给页面用）。

    用 utf-8-sig 打开：记事本另存为「UTF-8 带 BOM」时，BOM 会粘在第一行开头，
    导致首条记录匹配不上，这里一并处理掉。
    """
    try:
        with open(log_file(), "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except Exception:
        return []

    items, cur = [], None
    for raw in lines:
        raw = raw.lstrip("\ufeff")
        m = _HEAD.match(raw)
        if m:
            if cur:
                items.append(cur)
            t, st, rest = m.groups()
            rec = {"time": t, "status": "ok" if st == "成功" else "error"}
            if rec["status"] == "ok":
                rec["years"] = rest.split("·")[0].strip()
                m2 = re.search(r"(\d+)\s*题", rest)
                rec["count"] = int(m2.group(1)) if m2 else 0
                m3 = re.search(r"缺解析\s*(\d+)", rest)
                rec["missing"] = int(m3.group(1)) if m3 else 0
                m4 = re.search(r"([\d.]+)s", rest)
                rec["seconds"] = float(m4.group(1)) if m4 else 0
                rec["pdf"] = "含 PDF" in rest
            else:
                rec["error"] = rest.replace("错误：", "").strip()
            cur = rec
            continue
        d = _DETAIL.match(raw)
        if d and cur:
            k, v = d.group(1), d.group(2).strip()
            if k == "源目录":
                cur["src"] = v
            else:
                cur["outdir"] = os.path.dirname(v)
                cur["word"] = os.path.basename(v)
    if cur:
        items.append(cur)
    items.reverse()
    return items[:limit]


# ------------------------------------------------------------------ 系统对话框
_PICK_PS = r'''
Add-Type -AssemblyName System.Windows.Forms
$outFile = $args[0]
$initial = $args[1]
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Width = 1
$owner.Height = 1
$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
$dlg.Description = "选择合订本存放的文件夹"
$dlg.ShowNewFolderButton = $true
if ($initial -and (Test-Path -LiteralPath $initial)) {
    $dlg.SelectedPath = $initial
}
if ($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    [System.IO.File]::WriteAllText($outFile, $dlg.SelectedPath, [System.Text.Encoding]::UTF8)
}
$owner.Dispose()
'''

_DESKTOP_PS = r'''
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Width = 1
$owner.Height = 1
$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
$dlg.Description = "选择真题 PDF 所在目录"
$dlg.ShowNewFolderButton = $false
if ($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
    [System.IO.File]::WriteAllText($args[0], $dlg.SelectedPath, [System.Text.Encoding]::UTF8)
}
$owner.Dispose()
'''


def _run_picker(script, args):
    tmpdir = tempfile.mkdtemp(prefix="hebing_pick_")
    ps1 = os.path.join(tmpdir, "pick.ps1")
    out = os.path.join(tmpdir, "out.txt")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(script)
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
             "-File", ps1, out] + list(args),
            timeout=600, capture_output=True, stdin=subprocess.DEVNULL,
        )
        if os.path.exists(out):
            with open(out, "r", encoding="utf-8-sig") as f:
                path = f.read().strip()
            return path or None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return None


def _pick_dir(initial=None):
    """选择「真题所在目录」。"""
    return _run_picker(_DESKTOP_PS, [])


def _pick_outdir(initial=None):
    """选择「合订本存放目录」。"""
    return _run_picker(_PICK_PS, [initial or ""])


_SERVER = [None]


def _shutdown():
    time.sleep(0.2)
    srv = _SERVER[0]
    if srv is not None:
        srv.shutdown()


# ------------------------------------------------------------------ 业务
def do_scan(directory):
    directory = os.path.abspath(os.path.expanduser(directory or ""))
    if not os.path.isdir(directory):
        raise ValueError("目录不存在：%s" % directory)
    pdfs = scan_pdfs(directory)
    pairs, unknown = pair_years(pdfs)
    return {
        "dir": directory,
        "total_pdfs": len(pdfs),
        "unknown": [os.path.basename(p) for p in unknown][:20],
        "rows": describe(pairs),
        "_pairs": pairs,
    }


def _convert_pdf(src, dst):
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", DOCX2PDF, "-Src", src, "-Dst", dst],
            capture_output=True, text=True, timeout=1800,
            stdin=subprocess.DEVNULL,
        )
        return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0
    except Exception:
        return False


def _job(directory, start, end, want_pdf, outdir=None):
    import datetime
    t0 = time.time()

    def _record(status, **kw):
        rec = {
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "src": directory,
            "range": "%s — %s" % (start if start is not None else "最早",
                                  end if end is not None else "最晚"),
            "pdf": bool(want_pdf),
            "seconds": round(time.time() - t0, 1),
        }
        rec.update(kw)
        try:
            append_log(rec)
        except Exception:
            pass

    try:
        _set(status="running", progress=1, message="扫描文件…", log=[], word=None, pdf=None)
        info = do_scan(directory)
        pairs = info["_pairs"]
        keys = [k for k in sorted(pairs, key=lambda k: (k[0], k[1] or 0))
                if (start is None or k[0] >= start) and (end is None or k[0] <= end)]
        if not keys:
            raise RuntimeError("该年份区间内没有找到可用的真题文件")

        docs = []
        total = len(keys)
        for i, key in enumerate(keys):
            y, m = key
            label = label_of(y, m)
            _set(progress=3 + int(76 * i / total),
                 message="解析 %s…（%d/%d）" % (label, i + 1, total))
            doc = build_year(y, pairs[key].get("q"), pairs[key].get("a"), month=m)
            docs.append(doc)
            _log("%s：%d 题，缺解析 %d 题%s" % (
                label, doc.count, len(doc.missing_letter),
                "，答案文件为扫描件" if doc.appendix else ""))

        _set(progress=82, message="排版 Word…")
        target_dir = outdir or OUTDIR
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception:
            target_dir = OUTDIR
            os.makedirs(target_dir, exist_ok=True)
        y0, y1 = keys[0][0], keys[-1][0]
        span = "%d-%d" % (y0, y1) if y0 != y1 else str(y0)
        name = "合订本.docx"
        out = os.path.join(target_dir, name)
        try:
            render_docx(docs, out, TITLE)
        except PermissionError:
            # 同名文件正被 Word 打开时，换个名字写，避免直接失败
            name = "合订本(2).docx"
            out = os.path.join(target_dir, name)
            render_docx(docs, out, TITLE)
        _OUTPUTS[name] = out
        _set(word=name, progress=88, outdir=target_dir, out_path=out)
        _log("输出目录：%s" % target_dir)

        pdf_ok = False
        pdfname = None
        if want_pdf:
            _set(message="导出 PDF（需要 Word，可能几分钟）…")
            pdfname = name[:-5] + ".pdf"
            pdf_ok = _convert_pdf(out, os.path.join(target_dir, pdfname))
            _set(pdf=pdfname if pdf_ok else None)
            if pdf_ok:
                _OUTPUTS[pdfname] = os.path.join(target_dir, pdfname)
            _log("PDF 导出%s" % ("成功" if pdf_ok else "失败（可能没装 Word）"))

        total_q = sum(d.count for d in docs)
        missing_q = sum(len(d.missing_letter) for d in docs)
        _log("共 %d 题，已生成 %s" % (total_q, name))
        _set(status="done", progress=100, message="完成，可以下载了")
        _record("ok", years=span, count=total_q, missing=missing_q,
                outdir=target_dir, word=name,
                pdf=pdfname if pdf_ok else None)
    except Exception as exc:
        _log("失败：%s" % exc)
        _log(traceback.format_exc())
        _set(status="error", progress=100, message="失败：%s" % exc)
        _record("error", error=str(exc))


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "hebing/0.2"

    def log_message(self, *args):
        pass

    # ---- 工具
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                with open(WEBUI, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
                return
            if u.path in ("/components", "/components.html"):
                with open(COMPONENTS_HTML, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
                return
            if u.path in ("/update", "/update.html"):
                with open(UPDATE_HTML, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
                return
            if u.path == "/api/ping":
                self._json({"ok": True, "dir": _initial_dir() or default_dir(),
                            "outdir": OUTDIR})
                return
            if u.path == "/api/status":
                self._json(_snapshot())
                return
            if u.path == "/api/components":
                self._json({"items": comp.list_components()})
                return
            if u.path == "/api/update/status":
                self._json(upd_status())
                return
            if u.path == "/api/updates":
                self._json(_notices_snapshot())
                return
            if u.path == "/api/logs":
                d = log_dir()
                self._json({"dir": d, "file": log_file(), "items": read_logs()})
                return
            if u.path == "/api/scan":
                info = do_scan(q.get("dir", [""])[0])
                info.pop("_pairs", None)
                self._json(info)
                return
            if u.path == "/api/download":
                self._download(q.get("f", [""])[0])
                return
            self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def _download(self, name):
        name = os.path.basename(name)
        path = _OUTPUTS.get(name) or os.path.join(OUTDIR, name)
        if not name or not os.path.exists(path):
            self._json({"error": "文件不存在"}, 404)
            return
        with open(path, "rb") as f:
            data = f.read()
        self._send(200, data, "application/octet-stream", {
            "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name),
        })

    # ---- POST
    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/pickdir":
            try:
                self._json({"dir": _pick_dir()})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return
        if u.path == "/api/pickoutdir":
            body = self._read_json()
            try:
                self._json({"dir": _pick_outdir(body.get("initial"))})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return
        if u.path == "/api/quit":
            self._json({"ok": True})
            threading.Thread(target=_shutdown, daemon=True).start()
            return
        if u.path == "/api/updates/read":
            _mark_all_read()
            self._json({"ok": True})
            return
        if u.path == "/api/updates/refresh":
            refresh_update_notice(force=True, auto_apply=False)
            self._json(_notices_snapshot())
            return
        if u.path == "/api/update/check":
            self._json(_demo_release() or upd.check())
            return
        if u.path == "/api/reveal-output":
            try:
                os.makedirs(OUTDIR, exist_ok=True)
                subprocess.Popen(["explorer", OUTDIR])
                self._json({"ok": True})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return
        if u.path == "/api/update/apply":
            body = self._read_json()
            try:
                n = upd.apply_update(body.get("zip_url"), body.get("zip_urls"))
                self._json({"ok": True, "files": n,
                            "message": "更新已下载并覆盖，重启程序后生效"})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)})
            return
        if u.path == "/api/update/config":
            body = self._read_json()
            conf = upd.load_conf()
            if "repo" in body:
                conf["repo"] = (body.get("repo") or "").strip()
            if "auto" in body:
                conf["auto"] = bool(body.get("auto"))
            try:
                upd.save_conf(conf)
                self._json({"ok": True, "conf": conf})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)})
            return
        if u.path == "/api/logs/pick":
            body = self._read_json()
            try:
                picked = _pick_outdir(body.get("initial") or log_dir())
                if picked:
                    set_log_dir(picked)
                self._json({"dir": log_dir(), "picked": picked})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return
        if u.path == "/api/open-url":
            body = self._read_json()
            target = (body.get("url") or "").strip()
            if not target.lower().startswith(("http://", "https://")):
                self._json({"error": "只允许打开 http/https 地址"}, 400)
                return
            try:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", target],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True,
                )
                self._json({"ok": True, "url": target})
            except Exception as exc:
                self._json({"error": "打开浏览器失败：%s" % exc}, 500)
            return
        if u.path == "/api/open-folder":
            body = self._read_json()
            target = (body.get("path") or "").strip()
            if not target:
                self._json({"error": "没有指定要打开的目录"}, 400)
                return
            try:
                os.makedirs(target, exist_ok=True)
                subprocess.Popen(["explorer", target])
                self._json({"ok": True})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return
        if u.path == "/api/components/check":
            body = self._read_json()
            self._json(comp.check_update(body.get("id") or "linkswift"))
            return
        if u.path == "/api/components/install":
            body = self._read_json()
            cid = body.get("id") or "linkswift"
            res = comp.download(cid)
            if res.get("ok") and body.get("open", True):
                try:
                    os.startfile(res["path"])   # 交给浏览器/Tampermonkey 弹安装页
                    res["opened"] = True
                except Exception as exc:
                    res["opened"] = False
                    res["open_error"] = str(exc)
            self._json(res)
            return
        if u.path == "/api/components/store":
            self._json(comp.open_store())
            return
        if u.path == "/api/components/tampermonkey":
            self._json(comp.open_tampermonkey())
            return
        if u.path == "/api/components/reveal":
            body = self._read_json()
            p = comp.local_path(body.get("id") or "linkswift")
            try:
                subprocess.Popen(["explorer", "/select,", p])
                self._json({"ok": True})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return
        if u.path != "/api/build":
            self._json({"error": "not found"}, 404)
            return
        body = self._read_json()

        with _LOCK:
            busy = STATE["status"] == "running"
        if busy:
            self._json({"error": "正在生成中，请稍候"}, 409)
            return

        threading.Thread(
            target=_job,
            args=(body.get("dir") or default_dir(),
                  body.get("start"), body.get("end"), bool(body.get("pdf")),
                  body.get("outdir")),
            daemon=True,
        ).start()
        self._json({"ok": True})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


class _Server(ThreadingHTTPServer):
    """Windows 上必须关掉地址复用。

    ThreadingHTTPServer 默认 allow_reuse_address = 1，而 Windows 的
    SO_REUSEADDR 语义是「可以重复占用同一个端口」——结果第二次双击时
    绑定不会报错，但请求全被旧进程接走，用户看到的就是「打不开」。
    关掉之后重复占用会抛 OSError，交给 _bind 顺延下一个端口。
    """
    allow_reuse_address = False
    daemon_threads = True


def _bind(port, tries=12):
    """绑定端口；被占用就往后顺延，避免"双击没反应"。"""
    last = None
    for p in range(port, port + tries):
        try:
            return _Server(("127.0.0.1", p), Handler), p
        except OSError as exc:
            last = exc
            continue
    raise RuntimeError("端口 %d-%d 都被占用了（%s）" % (port, port + tries - 1, last))


def serve(port=8777, open_browser=True, directory=None):
    os.makedirs(OUTDIR, exist_ok=True)
    if directory:
        _set_initial_dir(directory)
    httpd, port = _bind(port)
    _SERVER[0] = httpd
    url = "http://127.0.0.1:%d" % port
    print("合订本工作台：%s" % url)
    print("默认扫描目录：%s" % (_initial_dir() or default_dir()))
    print("按 Ctrl+C 停止")
    sys.stdout.flush()
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


_INITIAL_DIR = [None]


def _set_initial_dir(d):
    _INITIAL_DIR[0] = os.path.abspath(os.path.expanduser(d))


def _bundled_data_dir():
    """项目自带的真题目录。

    打包后 exe 可能被放在别处（如 应用程序 文件夹），而题库在兄弟目录
    （如 `多文件合并\题目和答案`），所以逐级向上找，并在每级看一眼子目录。
    """
    rel = os.path.join("题目和答案", "行测（A类）")
    here = ROOT
    for _ in range(3):
        direct = os.path.join(here, rel)
        if os.path.isdir(direct):
            return direct
        try:
            names = sorted(os.listdir(here))
        except OSError:
            names = []
        for name in names:
            sub = os.path.join(here, name)
            if os.path.isdir(sub):
                cand = os.path.join(sub, rel)
                if os.path.isdir(cand):
                    return cand
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None


def _initial_dir():
    return _INITIAL_DIR[0] or _bundled_data_dir()
