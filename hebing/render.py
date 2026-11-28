# -*- coding: utf-8 -*-
"""输出渲染：Markdown（校对用）与 Word（成书用）。"""
import io
import os

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BODY_FONT = "宋体"
HEAD_FONT = "微软雅黑"


# ------------------------------------------------------------------ Markdown
def render_markdown(years, path):
    buf = []
    buf.append("# 江苏省公务员录用考试《行政职业能力测验》（A类）真题 + 解析合订本\n")
    buf.append("> 每题下方紧跟该题答案与解析。共 %d 个年度、%d 道题。\n" % (
        len(years), sum(y.count for y in years)))
    for doc in years:
        buf.append("\n\n## %s\n" % doc.title)
        if doc.notes:
            for n in doc.notes:
                buf.append("> 注：%s\n" % n)
        quick = ["%d.%s" % (q.no, q.letter or "?") for q in doc.questions]
        buf.append("\n**答案速查**：" + "  ".join(quick) + "\n")
        cur = None
        for q in doc.questions:
            if q.section and q.section != cur:
                cur = q.section
                buf.append("\n### %s\n" % cur)
            if q.preamble:
                buf.append("\n**材料：**\n")
                for p in q.preamble:
                    buf.append("> %s\n" % p)
            stem_parts = (q.stem or "（题干缺失）").split("\n")
            buf.append("\n**%d．** %s  \n" % (q.no, stem_parts[0]))
            for extra in stem_parts[1:]:
                buf.append("%s  \n" % extra)
            for L, t in q.options:
                buf.append("%s．%s  \n" % (L, t))
            if q.letter:
                buf.append("\n**答案：%s**\n" % q.letter)
            else:
                buf.append("\n**答案：**（源文件缺）\n")
            if q.analysis:
                for p in q.analysis:
                    buf.append("%s\n" % p)
            for i, _ in enumerate(q.figures):
                buf.append("![图](figure_%d_%d_%d.png)\n" % (doc.year, q.no, i))
    data = "".join(buf)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return len(data)


# ------------------------------------------------------------------ Word
def _set_font(run, name=BODY_FONT, size=None, bold=None, color=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _para(doc, text="", size=10.5, bold=False, align=None, indent=0.0,
          space_before=0, space_after=2, font=BODY_FONT, color=None,
          line=1.25):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line
    if indent:
        pf.left_indent = Cm(indent)
    r = p.add_run(text)
    _set_font(r, font, size, bold, color)
    return p


def _add_page_number_footer(section):
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._element.append(fld_begin)
    run._element.append(instr)
    run._element.append(fld_end)
    _set_font(run, BODY_FONT, 9)


def _shade(paragraph, hexcolor="F2F2F2"):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hexcolor)
    pPr.append(shd)


def render_docx(years, path, title, figures_dir=None):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.left_margin = Cm(2.0)
    sec.right_margin = Cm(2.0)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(1.8)
    _add_page_number_footer(sec)

    # 封面
    for _ in range(4):
        _para(doc, "", size=12)
    _para(doc, title, size=26, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER,
          space_after=10, font=HEAD_FONT)
    _para(doc, "题目 + 答案 + 解析 · 逐题对照", size=14,
          align=WD_ALIGN_PARAGRAPH.CENTER, space_after=24, font=HEAD_FONT,
          color=RGBColor(0x55, 0x55, 0x55))
    _para(doc, "收录：%s" % "、".join(str(y.year) for y in years), size=11,
          align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
    _para(doc, "共 %d 道题" % sum(y.count for y in years), size=11,
          align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)

    # 目录（手工，不依赖 Word 更新域）
    doc.add_page_break()
    _para(doc, "目　录", size=18, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER,
          space_after=12, font=HEAD_FONT)
    for y in years:
        miss = len(y.missing_letter)
        tag = "" if miss == 0 else "（缺 %d 题解析）" % miss
        _para(doc, "%s%s" % (y.title, tag), size=11, space_after=2)

    for y in years:
        doc.add_page_break()
        _para(doc, y.title, size=17, bold=True, space_before=0, space_after=8,
              font=HEAD_FONT, color=RGBColor(0x1F, 0x3B, 0x73))
        for note in y.notes:
            _para(doc, "注：" + note, size=9.5, color=RGBColor(0xB0, 0x30, 0x30),
                  space_after=4)

        # 答案速查
        _para(doc, "答案速查", size=12, bold=True, space_before=6, space_after=3,
              font=HEAD_FONT)
        chunks = []
        for q in y.questions:
            chunks.append("%d.%s" % (q.no, q.letter or "—"))
        for i in range(0, len(chunks), 12):
            _para(doc, "　".join(chunks[i:i + 12]), size=9.5, space_after=1,
                  color=RGBColor(0x33, 0x33, 0x33))

        cur = None
        for q in y.questions:
            if q.section and q.section != cur:
                cur = q.section
                _para(doc, "【%s】" % cur, size=13, bold=True, space_before=10,
                      space_after=4, font=HEAD_FONT,
                      color=RGBColor(0x1F, 0x3B, 0x73))
            if q.preamble:
                for p in q.preamble:
                    pp = _para(doc, p, size=9.5, indent=0.3, space_before=4,
                               space_after=4, color=RGBColor(0x40, 0x40, 0x40))
                    _shade(pp, "F5F5F5")

            # 题干
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.3
            r = p.add_run("%d．" % q.no)
            _set_font(r, HEAD_FONT, 11, True)
            stem_parts = (q.stem or "（题干缺失）").split("\n")
            r2 = p.add_run(stem_parts[0])
            _set_font(r2, BODY_FONT, 11, False)
            for extra in stem_parts[1:]:
                br = p.add_run()
                br.add_break()
                r3 = p.add_run(extra)
                _set_font(r3, BODY_FONT, 11, False)

            for L, t in q.options:
                _para(doc, "%s．%s" % (L, t), size=10.5, indent=0.5, space_after=1,
                      line=1.2)

            for i, png in enumerate(q.figures):
                if figures_dir:
                    os.makedirs(figures_dir, exist_ok=True)
                    fp = os.path.join(figures_dir, "q%d_%d.png" % (q.no, i))
                    with open(fp, "wb") as f:
                        f.write(png)
                try:
                    doc.add_picture(io.BytesIO(png), width=Cm(9.0))
                    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                except Exception:
                    pass

            # 答案
            ans = q.letter or "—"
            pa = _para(doc, "【答案】%s" % ans, size=11, bold=True, space_before=4,
                       space_after=2, color=RGBColor(0xC0, 0x00, 0x00))
            _shade(pa, "FFF4E5")

            # 解析
            if q.analysis:
                _para(doc, "【解析】", size=10.5, bold=True, space_before=3,
                      space_after=1, color=RGBColor(0x1F, 0x3B, 0x73))
                for para_text in q.analysis:
                    _para(doc, para_text, size=10, indent=0.5, space_after=2,
                          line=1.3)
            else:
                _para(doc, "【解析】（源文件未提供）", size=9.5, indent=0.5,
                      space_after=2, color=RGBColor(0x99, 0x99, 0x99))
            _para(doc, "", size=4, space_after=0)

        if y.appendix:
            _para(doc, "附：参考答案与解析（源文件为扫描件，按原页呈现）", size=13,
                  bold=True, space_before=14, space_after=6, font=HEAD_FONT,
                  color=RGBColor(0x1F, 0x3B, 0x73))
            for png in y.appendix:
                try:
                    doc.add_picture(io.BytesIO(png), width=Cm(16.0))
                    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                except Exception:
                    pass

    doc.save(path)
    return path
