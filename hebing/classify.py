# -*- coding: utf-8 -*-
"""自动分类：不依赖文件夹结构，从一堆平铺的 PDF 里认出「哪一年的题目 / 答案」。

网盘抓下来的文件通常全堆在下载目录里，名字五花八门，甚至带 (1)(2) 后缀。
这里先用文件名判性质，判不出来再探内容。
"""
import os
import re

from .years import year_of, date_of

# 文件名里出现这些词，基本就是答案/解析
ANS_HINT = re.compile(r"(答案|解析|详解|参考作答|参考答案)")
# 明显是题本的词
Q_HINT = re.compile(r"(真题|试卷|题本|试题)")

# 无意义的下载后缀，用来给重复文件降权
DUP_SUFFIX = re.compile(r"[(（]\s*\d+\s*[)）]\s*$")

SKIP_DIRS = {"输出", "_scratch", "合订本输出", "__pycache__", ".git", "node_modules"}
MAX_DEPTH = 3


def scan_pdfs(root, max_depth=MAX_DEPTH):
    """递归找出目录里的 PDF。"""
    found = []
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return found
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.rstrip(os.sep).count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.lower().endswith(".pdf"):
                found.append(os.path.join(dirpath, f))
    return sorted(found)


def kind_of(path):
    """'q' 题本 / 'a' 答案 / None 认不出。"""
    name = os.path.splitext(os.path.basename(path))[0]
    name = DUP_SUFFIX.sub("", name)
    if ANS_HINT.search(name):
        return "a"
    if Q_HINT.search(name):
        return "q"
    return None


def _score(path):
    """同一年的同类文件有多份时，选最像完整版的那份。"""
    name = os.path.splitext(os.path.basename(path))[0]
    s = len(name)
    if "资料分析" in name and "题目+答案" in name:
        s -= 60          # 只是部分题，别当整卷
    if DUP_SUFFIX.search(name):
        s -= 5           # 重复下载的副本
    if "解析" in name:
        s += 3
    return s


def pair_years(paths):
    """把文件按「年（+月）」配对。

    返回 {(year, month_or_None): {"q": path, "a": path, "month": m}}。
    同一年里如果出现不同月份的多套卷子，会拆成多条，不会互相覆盖。
    """
    by_year = {}
    unknown = []
    for p in paths:
        y, m = date_of(p)
        if y is None:
            unknown.append(p)
            continue
        by_year.setdefault(y, []).append((p, m, kind_of(p) or "other"))

    pairs = {}
    for y, items in by_year.items():
        qs = [(p, m) for p, m, k in items if k == "q"]
        ans = [(p, m) for p, m, k in items if k == "a"]
        oth = [(p, m) for p, m, k in items if k == "other"]

        if len(qs) <= 1 and len(ans) <= 1:
            # 常见情况：一年一套卷
            q = qs[0] if qs else (oth[0] if oth else None)
            a = ans[0] if ans else None
            m = (q[1] if q else None) or (a[1] if a else None)
            item = {"month": m}
            if q:
                item["q"] = q[0]
            if a:
                item["a"] = a[0]
            pairs[(y, m)] = item
            continue

        # 一年多套：按月份分组各自配对
        for m in sorted({mm for _, mm in qs + ans if mm is not None}):
            gq = [p for p, mm in qs if mm == m]
            ga = [p for p, mm in ans if mm == m]
            if gq or ga:
                item = {"month": m}
                if gq:
                    gq.sort(key=_score, reverse=True)
                    item["q"] = gq[0]
                if ga:
                    ga.sort(key=_score, reverse=True)
                    item["a"] = ga[0]
                pairs[(y, m)] = item
        gq = [p for p, mm in qs if mm is None]
        ga = [p for p, mm in ans if mm is None]
        if gq or ga:
            item = {"month": None}
            if gq:
                item["q"] = sorted(gq, key=_score, reverse=True)[0]
            if ga:
                item["a"] = sorted(ga, key=_score, reverse=True)[0]
            pairs[(y, None)] = item

    return pairs, unknown


def label_of(year, month):
    return "%d年%d月" % (year, month) if month else "%d年" % year


def describe(pairs):
    """给前端用的摘要。"""
    rows = []
    for key in sorted(pairs, key=lambda k: (k[0], k[1] or 0)):
        y, m = key
        it = pairs[key]
        rows.append({
            "year": y,
            "month": m,
            "label": label_of(y, m),
            "q": os.path.basename(it["q"]) if it.get("q") else "",
            "a": os.path.basename(it["a"]) if it.get("a") else "",
            "has_q": bool(it.get("q")),
            "has_a": bool(it.get("a")),
        })
    return rows


def default_dir():
    """默认扫描目录：优先 下载，其次桌面。"""
    home = os.path.expanduser("~")
    for name in ("Downloads", "下载", "Desktop"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            return p
    return home
