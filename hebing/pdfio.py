# -*- coding: utf-8 -*-
"""PDF 读取：按阅读顺序输出带坐标的行，并提供图形区域裁剪。"""
import os
import re
import sys


def _resource_bases():
    """可能存放 pymupdf 数据目录的几个位置。

    * 源码运行         -> 包目录的上一层
    * Nuitka --standalone -> exe 所在目录
    * Nuitka --onefile    -> 运行时解包出来的临时目录（__file__ 指向那里）
    * PyInstaller         -> sys._MEIPASS
    """
    bases = []
    if getattr(sys, "frozen", False):
        bases.append(os.path.dirname(sys.executable))
    bases.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(meipass)
    if sys.argv and sys.argv[0]:
        bases.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    return bases


def _preload_pymupdf_dlls():
    """编译版把 pymupdf 作为数据目录随包分发时，它自带的
    mupdfcpp64.dll 需要显式加入 DLL 搜索路径，否则 import fitz 会报
    "DLL load failed while importing _extra"。
    """
    if not hasattr(os, "add_dll_directory"):
        return
    for base in _resource_bases():
        for sub in ("pymupdf", "fitz"):
            d = os.path.join(base, sub)
            try:
                if os.path.isdir(d):
                    os.add_dll_directory(d)
            except Exception:
                pass


_preload_pymupdf_dlls()

import fitz  # noqa: E402

# 页码/水印等噪声行，抽取时直接丢弃
NOISE = [
    re.compile(r"^（认准淘宝店铺.*$"),
    re.compile(r"^【认准淘宝.*$"),
    re.compile(r"^第\s*\d+\s*页\s*[，,]?\s*共\s*\d+\s*页\s*$"),
    re.compile(r"^\d+\s*/\s*\d+\s*$"),
    re.compile(r"^[-—]\s*\d+\s*[-—]\s*$"),
    re.compile(r"^第\s*\d+\s*页\s*共\s*\d+\s*页\s*$"),
    re.compile(r"^\d{4}\s*年江苏省.*(?:答案|解析).*$"),
    re.compile(r"^.{0,4}江苏省公务员录用考试《行测》.*(?:答案|解析).*$"),
]

# 水印/店铺广告，可能被拼进正文，按内容片段清除
WATERMARK = [
    re.compile(r"【认准淘宝店铺[^】]*】[^。\n]{0,60}?更新"),
    re.compile(r"（认准淘宝店铺[^）]*）[^。\n]{0,60}?更新"),
    re.compile(r"【认准淘宝店铺[^】]*】"),
]


def is_noise(text):
    t = text.strip()
    if not t:
        return True
    for rx in NOISE:
        if rx.match(t):
            return True
    return False


def strip_watermark(text):
    t = text
    for rx in WATERMARK:
        t = rx.sub("", t)
    return t.strip()


def _reading_key(width):
    """阅读顺序：先按纵向行（2pt 容差），同一行内按横坐标。

    这样能修正「题号被单独定位在页面左边距、内容流却排在题干之后」的 PDF。
    """
    def key(l):
        return (int(round(l.y0 / 2.0)), l.x0)
    return key


class Line(object):
    __slots__ = ("text", "page", "x0", "y0", "x1", "y1")

    def __init__(self, text, page, x0, y0, x1, y1):
        self.text = text
        self.page = page
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1

    def __repr__(self):
        return "Line(p%d y%.0f %r)" % (self.page + 1, self.y0, self.text[:40])


class Pdf(object):
    """一次性把 PDF 读成行序列 + 保留 doc 以便裁剪图形。"""

    def __init__(self, path):
        self.path = path
        self.doc = fitz.open(path)
        self.page_count = self.doc.page_count
        self.lines = []
        for pno in range(self.page_count):
            page = self.doc[pno]
            width = page.rect.width
            d = page.get_text("dict")
            page_lines = []
            for block in d.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for ln in block.get("lines", []):
                    spans = ln.get("spans", [])
                    if not spans:
                        continue
                    text = "".join(s.get("text", "") for s in spans)
                    text = strip_watermark(text)
                    if not text or is_noise(text):
                        continue
                    x0, y0, x1, y1 = ln.get("bbox", (0, 0, 0, 0))
                    page_lines.append(Line(text.strip(), pno, x0, y0, x1, y1))
            # 按视觉位置重排：同一行内从左到右，行间从上到下
            page_lines.sort(key=_reading_key(width))
            self.lines.extend(page_lines)

    # ---- 图形 ----
    def figure_boxes(self, pno, y_from=None, y_to=None, min_area=4000):
        """返回该页上属于图形的裁剪框。

        排除整页底图、页面缩略图水印与细小装饰。
        """
        page = self.doc[pno]
        pr = page.rect
        page_area = pr.width * pr.height
        boxes = []
        for img in page.get_images(full=True):
            try:
                rects = page.get_image_rects(img[0])
            except Exception:
                continue
            for r in rects:
                # 整页底图、半页缩略图水印不是题图
                if r.width * r.height > page_area * 0.8:
                    continue
                if r.width > pr.width * 0.75 and r.height > pr.height * 0.55:
                    continue
                boxes.append(fitz.Rect(r))
        for dr in page.get_drawings():
            r = dr.get("rect")
            if r is None:
                continue
            if r.width * r.height < min_area:
                continue
            if r.width * r.height > page_area * 0.8:
                continue
            boxes.append(fitz.Rect(r))
        out = []
        for b in boxes:
            # 页眉/页脚区不裁图（多为水印条、页码装饰）
            if b.y1 < 62 or b.y0 > pr.height - 72:
                continue
            if y_from is not None and b.y1 < y_from - 2:
                continue
            if y_to is not None and b.y0 > y_to + 2:
                continue
            out.append(b)
        # 合并相交/相邻框
        out.sort(key=lambda r: (round(r.y0, 1), r.x0))
        merged = []
        for r in out:
            placed = False
            for m in merged:
                if r.intersects(m + fitz.Rect(-6, -6, 6, 6)):
                    m |= r
                    placed = True
                    break
            if not placed:
                merged.append(fitz.Rect(r))
        return merged

    def crop_png(self, pno, rect, dpi=120):
        """裁剪图形，输出灰度 PNG（体积小，线稿清晰）。"""
        import io as _io
        from PIL import Image

        page = self.doc[pno]
        clip = fitz.Rect(rect) & page.rect
        if clip.is_empty:
            return None
        pix = page.get_pixmap(clip=clip, dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        # 限制最大宽度，避免过大的图撑爆文档
        if img.width > 900:
            h = int(img.height * 900.0 / img.width)
            img = img.resize((900, h), Image.LANCZOS)
        buf = _io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def page_image(self, pno, dpi=110, quality=72):
        """整页渲染成 JPEG 字节（用于扫描件的图片附录）。"""
        import io as _io
        from PIL import Image

        page = self.doc[pno]
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        if img.width > 900:
            h = int(img.height * 900.0 / img.width)
            img = img.resize((900, h), Image.LANCZOS)
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def close(self):
        try:
            self.doc.close()
        except Exception:
            pass
