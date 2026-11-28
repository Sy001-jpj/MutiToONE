# -*- coding: utf-8 -*-
"""命令行入口。"""
import argparse
import io
import os
import sys

from .years import years_in_range, discover
from .build import build_year


def _resolve_base():
    """数据目录可能在工作区里，也可能在项目根或上一级，都找一遍。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands = [
        os.path.join("题目和答案", "行测（A类）"),
        os.path.join(here, "题目和答案", "行测（A类）"),
        os.path.join(os.path.dirname(here), "题目和答案", "行测（A类）"),
    ]
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


DEFAULT_BASE = _resolve_base()


def cmd_report(args):
    rows = []
    for year, q, a in years_in_range(args.base, args.start, args.end):
        doc = build_year(year, q, a, with_figures=False)
        rows.append((year, doc, q, a))
    out = io.StringIO()
    total = 0
    for year, doc, q, a in rows:
        total += doc.count
        miss_ans = len(doc.missing_letter)
        out.write("%d  题数=%-4d 有答案=%-4d 缺答案=%-3d 缺题干=%-3d  %s\n" % (
            year, doc.count, doc.count - miss_ans, miss_ans,
            len(doc.missing_stem), os.path.basename(q or "")))
        if doc.notes:
            for n in doc.notes:
                out.write("      注：%s\n" % n)
        if doc.missing_letter and len(doc.missing_letter) <= 40:
            out.write("      缺答案题号：%s\n" % doc.missing_letter)
    out.write("合计题目=%d\n" % total)
    text = out.getvalue()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("written", args.out)
    else:
        sys.stdout.write(text)
    return 0


def cmd_sample(args):
    data = discover(args.base)
    if args.year not in data:
        print("no such year", args.year)
        return 1
    slot = data[args.year]
    doc = build_year(args.year, slot.get("q"), slot.get("a"))
    for q in doc.questions:
        if args.no and q.no != args.no:
            continue
        print("=" * 70)
        print("题号", q.no, "部分", q.section, "答案", q.letter, "图", len(q.figures))
        print("--STEM--")
        print(q.stem[:1200])
        print("--OPTIONS--")
        for L, t in q.options:
            print(" %s. %s" % (L, t[:160]))
        print("--ANALYSIS--")
        for p in q.analysis[:6]:
            print(" ", p[:300])
        if args.no:
            break
    return 0


def _build_all(args, with_figures=True):
    docs = []
    for year, q, a in years_in_range(args.base, args.start, args.end):
        docs.append(build_year(year, q, a, with_figures=with_figures))
    return docs


def cmd_md(args):
    docs = _build_all(args, with_figures=False)
    from .render import render_markdown
    n = render_markdown(docs, args.out)
    print("markdown: %s (%d chars, %d 年 %d 题)" % (
        args.out, n, len(docs), sum(d.count for d in docs)))
    return 0


def cmd_docx(args):
    docs = _build_all(args, with_figures=True)
    from .render import render_docx
    title = args.title or "江苏省公务员录用考试《行测》（A类）真题与解析合订本"
    render_docx(docs, args.out, title, figures_dir=None)
    print("docx: %s (%d 年 %d 题)" % (args.out, len(docs), sum(d.count for d in docs)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hebing")
    ap.add_argument("--base", default=DEFAULT_BASE)
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("report")
    r.add_argument("--start", type=int)
    r.add_argument("--end", type=int)
    r.add_argument("--out")

    s = sub.add_parser("sample")
    s.add_argument("--year", type=int, required=True)
    s.add_argument("--no", type=int)

    m = sub.add_parser("md")
    m.add_argument("--start", type=int)
    m.add_argument("--end", type=int)
    m.add_argument("--out", default=os.path.join("输出", "合订本.md"))

    d = sub.add_parser("docx")
    d.add_argument("--start", type=int)
    d.add_argument("--end", type=int)
    d.add_argument("--out", default=os.path.join("输出", "合订本.docx"))
    d.add_argument("--title")

    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8777)
    sv.add_argument("--dir", help="默认扫描目录")
    sv.add_argument("--no-browser", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "sample":
        return cmd_sample(args)
    if args.cmd == "md":
        return cmd_md(args)
    if args.cmd == "docx":
        return cmd_docx(args)
    if args.cmd == "serve":
        from .serve import serve
        serve(port=args.port, open_browser=not args.no_browser, directory=args.dir)
        return 0
    ap.print_help()
    return 1
