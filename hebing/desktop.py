# -*- coding: utf-8 -*-
"""MutiToONE 桌面窗口版：工作台跑在原生窗口里，不再跳浏览器。

用法：
    python -m hebing.desktop --home "D:\\...\\应用程序"

窗口结构：
    * 主窗口    —— 工作台（扫描 / 生成 / 下载）
    * 组件窗口  —— 右上角「组件」点开的小窗（LinkSwift 版本与更新）
"""
import os
import sys
import threading
import time

# ---------------------------------------------------------------- 先定主目录
# 必须在导入 serve / paths 之前设置，否则 APP_ROOT 会被算成项目根目录。
def _take_opt(name):
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
    return None


_HOME = _take_opt("--home")
if _HOME:
    os.environ["MUTITOO_HOME"] = os.path.abspath(_HOME)


def _ensure_stdio():
    """pythonw 下没有 stdout/stderr，pywebview 往里写日志会直接崩，
    所以把它们重定向到一个日志文件（顺便方便排错）。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    home = os.environ.get("MUTITOO_HOME") or os.path.expanduser("~")
    try:
        path = os.path.join(home, "启动日志.txt")
        log = open(path, "a", encoding="utf-8", buffering=1)
    except Exception:
        log = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


_ensure_stdio()

import webview  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

from . import serve as app_serve  # noqa: E402

MAIN_TITLE = "MutiToONE · 真题合订本"
COMP_TITLE = "组件"
UPDATE_TITLE = "发现新版本"


class Api(object):
    """暴露给页面 JS 的接口：window.pywebview.api.xxx()。

    注意：窗口对象必须挂在「下划线」属性上。pywebview 会把 js_api 的
    公开属性递归序列化给前端，Window 对象一旦被公开，就会一路递归进
    .native（AccessibilityObject…），轻则刷错误日志，重则把 UI 线程拖死。
    """

    def __init__(self):
        self._main = None
        self._comp = None
        self._upd = None
        self._httpd = None

    # ---- 组件小窗
    def show_components(self):
        if self._comp is None:
            return False
        try:
            self._comp.show()
            self._comp.on_top = True
            threading.Thread(target=self._drop_top, daemon=True).start()
        except Exception:
            return False
        return True

    def _drop_top(self):
        time.sleep(0.6)
        try:
            self._comp.on_top = False
        except Exception:
            pass

    def hide_components(self):
        if self._comp is not None:
            try:
                self._comp.hide()
            except Exception:
                pass
        return True

    # ---- 发现新版本（更新卡片）
    def show_update(self):
        if self._upd is None:
            return False
        try:
            self._upd.show()
            self._upd.on_top = True
            threading.Thread(target=self._drop_top_update, daemon=True).start()
        except Exception:
            return False
        return True

    def _drop_top_update(self):
        time.sleep(0.6)
        try:
            self._upd.on_top = False
        except Exception:
            pass

    def hide_update(self):
        if self._upd is not None:
            try:
                self._upd.hide()
            except Exception:
                pass
        return True

    # ---- 退出
    def quit_app(self):
        threading.Thread(target=self._quit, daemon=True).start()
        return True

    def _quit(self):
        time.sleep(0.15)
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass

    # ---- 杂项
    def open_url(self, url):
        try:
            os.startfile(url)
        except Exception:
            import webbrowser
            webbrowser.open(url)
        return True

    def app_home(self):
        return app_serve.ROOT


def _start_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app_serve.Handler)
    port = httpd.server_address[1]
    app_serve._SERVER[0] = httpd
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _auto_update():
    """启动后在后台检查自身更新：有新版本就发通知，配置了自动更新则直接替换。"""
    app_serve.start_update_watcher(interval=1800)


def _dpi_aware():
    """让窗口尺寸按物理像素算，避免系统缩放后窗口超出屏幕。"""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------------------------------------------------------------- 窗口记忆
DEFAULT_W, DEFAULT_H = 1260, 810         # 参考 SpringNote 主窗口比例
MIN_W, MIN_H = 980, 640


def _conf_path():
    return os.path.join(app_serve.ROOT, "窗口设置.json")


def _screen_bounds():
    """虚拟屏幕范围（多显示器也能覆盖）。"""
    try:
        import ctypes
        u = ctypes.windll.user32
        return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
                u.GetSystemMetrics(78), u.GetSystemMetrics(79))
    except Exception:
        return (0, 0, 1920, 1080)


def _pos_visible(x, y):
    """保存的位置是否落在可见屏幕内。

    窗口最小化时 Windows 会把它挪到 -16000 之类的坐标，这种值不能记下来，
    否则下次启动窗口就跑到屏幕外，看起来像「打不开」。
    """
    if not isinstance(x, int) or not isinstance(y, int):
        return False
    vx, vy, vw, vh = _screen_bounds()
    return (vx - 50) <= x <= (vx + vw - 100) and (vy - 50) <= y <= (vy + vh - 100)


def _load_conf():
    import json
    try:
        with open(_conf_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_conf(data):
    import json
    try:
        with open(_conf_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def main():
    _dpi_aware()
    os.makedirs(app_serve.OUTDIR, exist_ok=True)
    httpd, port = _start_server()
    base = "http://127.0.0.1:%d" % port
    threading.Thread(target=_auto_update, daemon=True).start()
    api = Api()
    api._httpd = httpd

    conf = _load_conf()
    w = max(MIN_W, int(conf.get("w") or DEFAULT_W))
    h = max(MIN_H, int(conf.get("h") or DEFAULT_H))
    pos = {}
    if _pos_visible(conf.get("x"), conf.get("y")):
        pos = {"x": conf["x"], "y": conf["y"]}

    comp_visible = os.environ.get("MUTITOO_SHOW_COMP") == "1"
    upd_visible = os.environ.get("MUTITOO_SHOW_UPDATE") == "1"
    main_win = webview.create_window(
        MAIN_TITLE, base + "/",
        width=w, height=h,
        min_size=(MIN_W, MIN_H),
        js_api=api,
        **pos
    )
    comp_win = webview.create_window(
        COMP_TITLE, base + "/components",
        width=540, height=580, min_size=(470, 420),
        resizable=False, hidden=not comp_visible, js_api=api,
    )
    upd_win = webview.create_window(
        UPDATE_TITLE, base + "/update",
        width=720, height=470, min_size=(620, 400),
        resizable=True, hidden=not upd_visible, js_api=api,
    )
    api._main, api._comp, api._upd = main_win, comp_win, upd_win

    def _remember():
        """记住用户调整后的窗口大小与位置（跳过最小化/屏幕外的异常坐标）。"""
        try:
            w, h = int(main_win.width), int(main_win.height)
            x, y = int(main_win.x), int(main_win.y)
        except Exception:
            return
        if w >= MIN_W and h >= MIN_H:
            conf["w"], conf["h"] = w, h
        if _pos_visible(x, y):
            conf["x"], conf["y"] = x, y
        _save_conf(conf)

    try:
        main_win.events.resized += lambda *a: _remember()
        main_win.events.moved += lambda *a: _remember()
        main_win.events.closed += lambda *a: _remember()
    except Exception:
        pass


    def _on_main_closed():
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass

    main_win.events.closed += _on_main_closed

    print("MutiToONE 桌面版")
    print("  主目录：%s" % app_serve.ROOT)
    print("  输出：%s" % app_serve.OUTDIR)
    print("  工作台：%s" % base)
    sys.stdout.flush()

    def _after_start():
        # pywebview 在部分 WebView2 版本里会把 hidden=True 的窗口也显示出来，
        # 这里启动后再确认一次：不该露面的窗口直接藏掉。
        for need, win in ((comp_visible, comp_win), (upd_visible, upd_win)):
            if not need and win is not None:
                try:
                    win.hide()
                except Exception:
                    pass
        print("[ok] GUI 已启动，窗口数：%d" % len(webview.windows))
        sys.stdout.flush()

    try:
        webview.start(debug=bool(_take_opt("--debug")), func=_after_start)
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        print("[exit] GUI 循环结束")
        sys.stdout.flush()
        try:
            httpd.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
