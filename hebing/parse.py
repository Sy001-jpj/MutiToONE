# -*- coding: utf-8 -*-
"""解析核心：题号驱动切块、选项拆分、答案抽取。"""
import re

# ---------------------------------------------------------------- 题号候选
# 1) 顿号题号 "12、"（其后可能紧跟年份数字，不能加 (?!\d)）
_CAND_DUN = re.compile(r"(?<![\dA-Za-z])(\d{1,3})\s*、")
# 2) 全角点题号 "12．"（全角句点不会当小数点）
_CAND_FWDOT = re.compile(r"(?<![\dA-Za-z])(\d{1,3})\s*．")
# 3) 半角点题号 "12."（需排除 3.5 这类小数）
_CAND_DOT = re.compile(r"(?<![\dA-Za-z])(\d{1,3})\s*\.(?!\d)")
# 4) 2024 体例："【解析1—正确答案D"
_CAND_JIEXI = re.compile(r"【\s*解析\s*(\d{1,3})\s*[—\-－]")
# 5) 2025 体例："题目1 解析"
_CAND_TIMU = re.compile(r"题目\s*(\d{1,3})")
CAND_PATTERNS = [_CAND_DUN, _CAND_FWDOT, _CAND_DOT, _CAND_JIEXI, _CAND_TIMU]

# ---------------------------------------------------------------- 部分标题
SECTION_NAMES = [
    "政治理论", "常识判断", "言语理解与表达", "言语理解", "阅读理解",
    "数量关系", "数字推理", "数学运算", "判断推理", "图形推理",
    "定义判断", "类比推理", "逻辑判断", "文章阅读", "片段阅读",
    "选词填空", "语句表达", "资料分析",
]
HEAD_PREFIX_RE = re.compile(
    r"^(第[一二三四五六七八九十]+部分|[一二三四五六七八九十]+\s*[、.]|（[一二三四五六七八九十]+）)")

# ---------------------------------------------------------------- 答案标记
LETTER_PATTERNS = [
    re.compile(r"【正确答案】\s*[：:]?\s*([A-D])"),
    re.compile(r"答案】\s*[：:]?\s*([A-D])"),
    re.compile(r"正确答案为\s*([A-D])"),
    re.compile(r"正确答案是\s*([A-D])"),
    re.compile(r"答案[：:]\s*([A-D])"),
    re.compile(r"选择\s*([A-D])\s*选项"),
    re.compile(r"因此[，,]?\s*选择\s*([A-D])"),
    re.compile(r"故选\s*([A-D])"),
]

TRAILING_ANS = re.compile(
    r"[。．.]?\s*(?:故正确答案为|因此[，,]?\s*选择|所以选择|故选)\s*[A-D]\s*(?:项|选项)?\s*[。．.]?\s*$")

# 选项标记：A. / A． / A、 / （A）
OPT_MARK_RE = re.compile(r"(?<![A-Za-z0-9])[（(]?([A-D])[）)]?\s*[．.、]\s*")

STRIP_PREFIX = [
    re.compile(r"^[（(]\s*(?:单选|多选)题\s*[）)]\s*"),
]

# 资料分析/文章阅读的公共材料起始标记（这些内容属于下一组题，不能粘在上一题末尾）
PREAMBLE_RE = [
    re.compile(r"^[（(][一二三四五六七八九十]+[）)]"),
    re.compile(r"^根据(?:以下|下列|上述|所给)资料"),
    re.compile(r"^阅读(?:下面|下列|以下)文章"),
    re.compile(r"^请根据(?:以下|下列|上述)"),
]


def split_preamble(text):
    """把块尾的“下一组材料”切出来。返回 (body, preamble)。"""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        t = ln.strip()
        if not t:
            continue
        for rx in PREAMBLE_RE:
            if rx.search(t):
                # 材料至少要有一定长度，避免误伤题干里的“（一）”
                if len("\n".join(lines[i:])) > 80:
                    return "\n".join(lines[:i]), "\n".join(lines[i:])
                break
    return text, ""


class Block(object):
    __slots__ = ("no", "section", "text", "page_from", "y_from", "page_to", "y_to")

    def __init__(self, no, section, text, page_from, y_from, page_to, y_to):
        self.no = no
        self.section = section
        self.text = text
        self.page_from = page_from
        self.y_from = y_from
        self.page_to = page_to
        self.y_to = y_to

    def __repr__(self):
        return "Block(%d, %s, %d chars)" % (self.no, self.section, len(self.text))


def find_candidates(lines):
    """强锚点：常规题号、标签题号。返回 [(line_idx, start, end, no), ...]。"""
    cands = []
    for i, ln in enumerate(lines):
        hits = []
        for rx in CAND_PATTERNS:
            for m in rx.finditer(ln.text):
                hits.append((m.start(), m.end(), int(m.group(1))))
        hits.sort()
        for s, e, n in hits:
            cands.append((i, s, e, n))
    return cands


def find_weak_candidates(lines):
    """弱锚点：整行只有一个数字（题号与顿号被拆到不同行）。"""
    out = {}
    for i, ln in enumerate(lines):
        t = ln.text.strip()
        if re.fullmatch(r"\d{1,3}", t) and 40 < ln.y0 < 760:
            out.setdefault(int(t), []).append((i, 0, len(ln.text)))
    return out


def section_map(lines):
    labels = []
    cur = None
    for ln in lines:
        t = ln.text.strip()
        name = None
        if len(t) <= 40 and HEAD_PREFIX_RE.match(t):
            for s in SECTION_NAMES:
                if s in t:
                    name = s
                    break
        elif len(t) <= 6 and t in SECTION_NAMES:
            name = t
        if name:
            cur = name
        labels.append(cur)
    return labels


def split_blocks(lines, max_no=200, max_jump=12):
    """按题号顺序切块。

    强锚点构成主链（允许小幅跳号）；缺失题号再用弱锚点在相邻区间内补位。
    返回 (blocks, missing)。
    """
    secs = section_map(lines)
    cands = find_candidates(lines)
    anchors = []
    expect = 1
    for idx, (i, s, e, n) in enumerate(cands):
        if n == expect:
            anchors.append((n, i, s, e))
            expect = n + 1
        elif (expect < n <= expect + max_jump
              and s <= 3                      # 跳号必须落在行首附近
              and _lookahead(cands, idx, n)):  # 且后续题号能接上
            anchors.append((n, i, s, e))
            expect = n + 1
    if not anchors:
        return [], []

    anchors = _repair_weak(lines, anchors)
    top = max(a[0] for a in anchors)

    blocks = []
    for k, (n, i, s, e) in enumerate(anchors):
        has_next = k + 1 < len(anchors)
        if has_next:
            ni, npos = anchors[k + 1][1], anchors[k + 1][2]
        else:
            ni, npos = len(lines) - 1, None

        parts = []
        if i == ni:
            tail = lines[i].text[e:npos if npos is not None else len(lines[i].text)]
            parts.append(tail)
        else:
            parts.append(lines[i].text[e:])
            for j in range(i + 1, ni):
                parts.append(lines[j].text)
            parts.append(lines[ni].text[:npos] if npos is not None else lines[ni].text)
        text = "\n".join(p for p in parts if p.strip())

        last = lines[ni] if has_next else lines[-1]
        # 有下一题时，本题的下边界取下一题题号行的上沿，避免把下一题的图算进来
        end_y = last.y0 if has_next else last.y1
        blocks.append(Block(n, secs[i], text,
                            lines[i].page, lines[i].y0, last.page, end_y))

    found = set(b.no for b in blocks)
    missing = [x for x in range(1, top + 1) if x not in found]
    return blocks, missing


def _lookahead(cands, idx, n, window=10):
    """跳号确认：后面紧接着应能出现 n+1 或 n+2。"""
    for c in cands[idx + 1: idx + 1 + window]:
        if c[3] in (n + 1, n + 2):
            return True
    return False


def _repair_weak(lines, anchors):
    """用弱锚点补齐主链中缺失的题号（只在其相邻两题之间寻找）。"""
    have = set(a[0] for a in anchors)
    top = max(have)
    missing = [n for n in range(1, top + 1) if n not in have]
    if not missing:
        return anchors
    weak = find_weak_candidates(lines)
    by_n = {a[0]: a for a in anchors}
    for m in missing:
        opts = weak.get(m)
        if not opts:
            continue
        prev_i = max([a[1] for a in anchors if a[0] < m] or [-1])
        next_i = min([a[1] for a in anchors if a[0] > m] or [len(lines)])
        cand = [w for w in opts if prev_i < w[0] < next_i]
        if cand:
            i, s, e = cand[0]
            by_n[m] = (m, i, s, e)
    return sorted(by_n.values(), key=lambda a: a[0])


# ---------------------------------------------------------------- 字段抽取
def extract_letter(text):
    for rx in LETTER_PATTERNS:
        m = rx.search(text)
        if m:
            return m.group(1)
    return None


def split_stem_options(text):
    """返回 (stem, [(letter, option_text), ...])"""
    marks = [(m.start(), m.end(), m.group(1)) for m in OPT_MARK_RE.finditer(text)]
    start = None
    for idx in range(len(marks) - 3):
        if [marks[idx + d][2] for d in range(4)] == ["A", "B", "C", "D"]:
            start = idx
            break
    if start is None:
        return text, []
    stem = text[:marks[start][0]]
    opts = []
    for d in range(4):
        s0, e0, L = marks[start + d]
        nxt = marks[start + d + 1][0] if d < 3 else len(text)
        opts.append((L, text[e0:nxt]))
    return stem, opts


# 解析起点标记：答案文件既可能是纯解析，也可能是「试卷+解析」合体
ANALYSIS_ANCHORS = [
    re.compile(r"【\s*正确答案\s*】"),
    re.compile(r"【\s*答案\s*】"),
    re.compile(r"【\s*解析\s*】"),
    re.compile(r"正确答案[:：]?\s*[A-D]?"),
    re.compile(r"答案[：:]\s*[A-D]"),
]

_LEAD_JUNK = [
    re.compile(r"^\s*[A-D]\s*[）)]?\s*"),
    re.compile(r"^\s*[（(][^）)]{0,40}[）)]\s*"),
    re.compile(r"^\s*】\s*"),
    re.compile(r"^\s*[，,。、；;：:]\s*"),
    re.compile(r"^\s*全站正确率[^。\n]*。?\s*"),
    re.compile(r"^\s*易错项[：:]?[^。\n]*。?\s*"),
    re.compile(r"^\s*【\s*解析\s*】\s*"),
    re.compile(r"^\s*解析\s*"),
]


def extract_analysis(text):
    """从答案块中取出纯解析部分。

    合体文件里题干与选项会重复出现，所以在首个答案/解析标记处切开，
    只保留标记之后的内容。
    """
    best = None
    for rx in ANALYSIS_ANCHORS:
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m)
    if best is not None and best[0] < len(text) * 0.6:
        t = text[best[1].end():]
    else:
        t = text
    for _ in range(3):
        before = t
        for rx in _LEAD_JUNK:
            t = rx.sub("", t)
        if t == before:
            break
    t = TRAILING_ANS.sub("", t)
    return t


def strip_answer_noise(text):
    return extract_analysis(text)


def _is_cjk(ch):
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF) or (0x3000 <= o <= 0x303F) or (0xFF00 <= o <= 0xFFEF)


NEW_PARA_RE = re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫]")


def tidy(text):
    """把 PDF 硬换行合并成自然段；①②③ 这类列举项单独成段。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = []
    prev_item = False
    for l in lines:
        if not out:
            out.append(l)
            prev_item = bool(NEW_PARA_RE.match(l))
            continue
        if NEW_PARA_RE.match(l):
            out.append(l)
            prev_item = True
            continue
        prev = out[-1]
        # 短问句单独成段：上一段已收尾（句号）或本身就是 ①② 列举项
        if len(l) <= 30 and l.endswith(("：", ":")) and (prev_item or prev.endswith(("。", "？", "?", "”"))):
            out.append(l)
            prev_item = False
            continue
        if _is_cjk(prev[-1]) or _is_cjk(l[0]):
            out[-1] = prev + l
        else:
            out[-1] = prev + " " + l
    return out


def clean_stem(text):
    t = text.strip()
    for rx in STRIP_PREFIX:
        t = rx.sub("", t)
    return t.strip()


STEP_RE = re.compile(r"(?<=[。；;”])\s*(?=第[一二三四五六]步)")
CIRCLE_RE = re.compile(r"(?<=[。；;”])\s*(?=[①②③④⑤⑥⑦⑧⑨⑩])")


def split_paragraphs(text):
    """把超长解析按 ①②③ 与 第X步 切成自然段，便于阅读。"""
    t = STEP_RE.sub("\n", text)
    t = CIRCLE_RE.sub("\n", t)
    return [p.strip() for p in t.split("\n") if p.strip()]
