# -*- coding: utf-8 -*-
"""把某年的题目 PDF 与答案 PDF 合成一份按题对齐的数据结构。"""
import os

from .pdfio import Pdf
from .parse import (split_blocks, split_stem_options, extract_letter,
                    strip_answer_noise, clean_stem, tidy, split_preamble,
                    split_paragraphs)


class Source(object):
    """一份 PDF 解析后的题号块集合。"""

    def __init__(self, path):
        self.path = path
        self.pdf = Pdf(path)
        self.blocks, self.missing = split_blocks(self.pdf.lines)
        self.by_no = {}
        for b in self.blocks:
            self.by_no.setdefault(b.no, b)
        n = max(1, len(self.blocks))
        stems = letters = 0
        for b in self.blocks:
            if len(split_stem_options(b.text)[1]) >= 3:
                stems += 1
            if extract_letter(b.text):
                letters += 1
        self.stem_ratio = stems / float(n)
        self.letter_ratio = letters / float(n)
        self.chars = sum(len(b.text) for b in self.blocks)

    @property
    def has_text(self):
        return self.chars > 500

    @property
    def is_paper(self):
        return self.stem_ratio > 0.5

    @property
    def is_key(self):
        return self.letter_ratio > 0.5

    def close(self):
        self.pdf.close()


class Question(object):
    __slots__ = ("no", "section", "stem", "options", "letter", "analysis",
                 "figures", "preamble")

    def __init__(self, no):
        self.no = no
        self.section = None
        self.stem = ""
        self.options = []
        self.letter = None
        self.analysis = []
        self.figures = []
        self.preamble = []


class YearDoc(object):
    def __init__(self, year, title):
        self.year = year
        self.title = title
        self.questions = []
        self.notes = []
        self.appendix = []
        # 统计
        self.missing_stem = []
        self.missing_answer = []
        self.missing_letter = []

    @property
    def count(self):
        return len(self.questions)

    def answer_map(self):
        return [(q.no, q.letter) for q in self.questions if q.letter]


def _collect_figures(src, block, limit=2):
    """把题目块范围内的图形裁成 PNG。

    只保留够大的图块，避免把单行文字/装饰条当成题图。
    """
    out = []
    if block is None:
        return out
    for pno in range(block.page_from, block.page_to + 1):
        y0 = block.y_from - 4 if pno == block.page_from else None
        y1 = block.y_to + 4 if pno == block.page_to else None
        try:
            boxes = src.pdf.figure_boxes(pno, y0, y1)
        except Exception:
            boxes = []
        for b in boxes:
            if b.width < 60 or b.height < 40:
                continue
            png = src.pdf.crop_png(pno, b)
            if png:
                out.append(png)
            if len(out) >= limit:
                return out
    return out


def build_year(year, qpath, apath, with_figures=True, month=None):
    qsrc = Source(qpath) if qpath else None
    asrc = Source(apath) if apath else None

    if asrc is not None and not asrc.has_text:
        asrc = None  # 纯扫描件，无文字层

    paper = None
    if qsrc is not None and qsrc.is_paper:
        paper = qsrc
    elif asrc is not None and asrc.is_paper:
        paper = asrc
    else:
        paper = qsrc or asrc

    # 答案源：题目文件自带答案时优先用自己（如 2026 金标尺答案），否则用答案文件
    key = None
    if paper is not None and paper.is_key:
        key = paper
    elif asrc is not None and asrc.is_key:
        key = asrc
    if key is None:
        for cand in (asrc, qsrc):
            if cand is not None and cand.is_key:
                key = cand
                break

    doc = YearDoc(year, "%s江苏省公务员录用考试《行政职业能力测验》（A类）"
                  % ("%d年%d月" % (year, month) if month else "%d年" % year))
    if paper is None:
        doc.notes.append("未能解析到试题内容")
        return doc

    top = max(b.no for b in paper.blocks) if paper.blocks else 0
    if key is not None and key.blocks:
        top = max(top, max(b.no for b in key.blocks))

    paper_is_key = (key is paper)
    pending = ""
    for n in range(1, top + 1):
        q = Question(n)
        pb = paper.by_no.get(n)
        kb = key.by_no.get(n) if key is not None else None

        if pending:
            q.preamble = tidy(pending)
            pending = ""

        if pb is not None:
            body, preamble = split_preamble(pb.text)
            stem, opts = split_stem_options(body)
            q.stem = "\n".join(tidy(clean_stem(stem)))
            q.options = [(L, t.strip()) for L, t in opts]
            q.section = pb.section
            pending = preamble
            if paper_is_key:
                q.letter = extract_letter(pb.text)
                q.analysis = _clean_analysis(tidy(strip_answer_noise(pb.text)))
            if with_figures and paper is qsrc:
                q.figures = _collect_figures(paper, pb)
        else:
            doc.missing_stem.append(n)

        if not paper_is_key:
            if kb is not None:
                q.letter = extract_letter(kb.text)
                q.analysis = _clean_analysis(tidy(strip_answer_noise(kb.text)))
                if not q.section:
                    q.section = kb.section
            else:
                doc.missing_answer.append(n)

        if not q.letter:
            doc.missing_letter.append(n)
        doc.questions.append(q)

    if asrc is None and apath:
        doc.notes.append("答案文件为扫描图片，无文字层，未能并入解析；已作为图片附录附在本年末尾")
        doc.appendix = _scanned_pages(apath)
    return doc


def _scanned_pages(path, dpi=110, limit=80):
    """把扫描件整页转成 JPEG，作为图片附录。"""
    out = []
    try:
        pdf = Pdf(path)
    except Exception:
        return out
    try:
        for pno in range(min(pdf.page_count, limit)):
            try:
                out.append(pdf.page_image(pno, dpi=dpi))
            except Exception:
                continue
    finally:
        pdf.close()
    return out


def _clean_analysis(paragraphs):
    """清理解析：去掉源文件占位符与尾部残字，并按 ①②③/第X步 细分段。"""
    out = []
    for p in paragraphs:
        for piece in split_paragraphs(p):
            t = piece.strip().rstrip("】").strip()
            if not t:
                continue
            if "暂缺" in t and len(t) < 40:
                continue
            if "全力以赴征集" in t:
                continue
            out.append(t)
    return out
