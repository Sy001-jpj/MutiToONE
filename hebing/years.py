# -*- coding: utf-8 -*-
"""年份与文件配对。"""
import os
import re
import glob


def date_of(path):
    """从文件名里取出 (年, 月)，没有月份时返回 (年, None)。

    例：
        2008年0309江苏公务员考试…pdf   -> (2008, 3)
        2011年0424江苏公务员考试…pdf   -> (2011, 4)
        2024年12月江苏省考…pdf         -> (2024, 12)
        2017年江苏公务员考试…pdf       -> (2017, None)
    """
    name = os.path.basename(path)
    m = re.match(r"^(\d{4})\s*年\s*(\d{2})(\d{2})", name)   # 年+MMDD
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return y, mo
    m = re.match(r"^(\d{4})\s*年\s*(\d{1,2})\s*月", name)   # 年+月
    if m:
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            return int(m.group(1)), mo
    y = year_of(name)
    return y, None


def year_of(path):
    name = os.path.basename(path)
    m = re.match(r"^(20\d\d)", name)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d{2})(?!\d)", name)
    if m:
        y = 2000 + int(m.group(1))
        if 2008 <= y <= 2030:
            return y
    m = re.search(r"(20\d\d)", name)
    if m:
        return int(m.group(1))
    return None


def discover(base):
    """返回 {year: {'q': path, 'a': path}}"""
    out = {}
    for sub, key in (("题目", "q"), ("答案", "a")):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(glob.glob(os.path.join(d, "*.pdf"))):
            y = year_of(f)
            if y is None:
                continue
            slot = out.setdefault(y, {})
            prev = slot.get(key)
            # 同一年多个文件时，优先取名字更“整卷”的那个
            if prev is None or _score(f) > _score(prev):
                slot[key] = f
    return out


def _score(path):
    name = os.path.basename(path)
    s = len(name)
    if "资料分析" in name:
        s -= 30
    if "答案" in name and "解析" in name:
        s += 5
    return s


def years_in_range(base, start=None, end=None):
    data = discover(base)
    ys = sorted(data)
    if start:
        ys = [y for y in ys if y >= start]
    if end:
        ys = [y for y in ys if y <= end]
    return [(y, data[y].get("q"), data[y].get("a")) for y in ys]
