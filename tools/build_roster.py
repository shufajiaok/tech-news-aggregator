#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把资料库图鉴里的人物名单抽成 tools/roster.md，供大模型找人时参考。

用法：
    # 先把资料库文档导出成 md（get_doc_reviews.py 的输出即可）
    python3 tools/build_roster.py docs/图鉴A.md docs/图鉴B.md docs/图鉴C.md

为什么要这份名册：微博搜索只认完整昵称，而用户口语只说中文简称。
地偶圈成员号昵称是「中文名+罗马音_团名」（不渝uyuna_Diamond、白泠Mio-StarHoney），
把姓名 → 团体的映射喂给模型，它才能把「不渝」补成「不渝uyuna_Diamond」；
否则它只能瞎猜一个同名账号（实测会落到「我是梓渝_」这种完全无关的人身上）。

生成结果只作**提示**，任何人名仍由微博搜索 + profile 复核确认真实性。
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "tools" / "roster.md"
CFG = REPO / "scripts" / "weibo_config.json"

NAMES_SPLIT = re.compile(r"[、／/＋+,，]")
BAD_HEAD = ("机制", "时间线", "用户视角", "规律", "血统", "旧体制", "结论",
            "一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、", "九、", "十、")

# 命名后缀总表：从已解析账号里自动学、再补几个已知的
KNOWN_SUFFIX = {
    "StarHoney": "-StarHoney", "Diamond%": "_Diamond",
    "Fuwafuwa夢": "_Fuwafuwa夢", "恋时青空": "_恋时青空",
    "弥漫星雲": "-弥漫星雲", "Astoria": "-Astoria", "夏日青空": "_夏日青空",
    "Purelee": "_Purelee", "StarWinK": "-StarWinK", "天枢Polaris": "_Polaris",
    "StarCandy": "-StarCandy",
}

# 图鉴里没收录、但已人工去微博核实过的团体。
# 每项写「微博上真实存在的完整昵称」，重建名册时不会被覆盖。
EXTRA_GROUPS = {
    "StarCandy": [
        "琴子Kotoko-StarCandy",   # uid 7928592755 认证：StarCandy偶像团体成员
        "巧可Choco-StarCandy",    # uid 7312611017 认证：StarCandy偶像团体成员
        "鲨鲨Same-StarCandy",     # uid 5498817895 昵称完全一致（认证为动漫博主）
    ],
}


def clean(t):
    t = re.sub(r"<Mark[^>]*>|</Mark>", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)
    return t.strip()


def parse(md):
    lines = md.split("\n")
    cur, out = None, []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.match(r'\s*<Heading[^>]*level="[23]"[^>]*>\s*$', ln):
            i += 1
            cur = clean(lines[i] if i < len(lines) else "")
            i += 1
            continue
        if cur and ("人：" in ln or "人:" in ln or "成员" in ln):
            body = clean(ln)
            if len(body) < 200:
                toks = []
                for tok in NAMES_SPLIT.split(body):
                    tok = re.split(r"[（(]", tok)[0]
                    tok = tok.strip(" 　()（）[]【】〕")
                    if not tok or "：" in tok or ":" in tok or len(tok) > 18:
                        continue
                    toks.append(tok)
                # 同一行出现过罗马音，就说明这是成员名单，纯中文名也一并收录
                has_latin = any(re.search(r"[A-Za-z]", x) for x in toks)
                for tok in toks:
                    if re.search(r"[A-Za-z]", tok):
                        out.append((tok, cur))
                    elif has_latin and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", tok):
                        out.append((tok, cur))
        i += 1
    return out


def norm_group(g):
    g = re.sub(r"^[0-9]+[.、]\s*", "", g or "")
    return re.split(r"[（(]", g)[0].strip() or "（未标注团体）"


def main(paths):
    if not paths:
        sys.exit("用法: python3 tools/build_roster.py <图鉴.md> [更多.md ...]")
    rows = []
    for p in paths:
        rows += parse(Path(p).read_text(encoding="utf-8"))

    seen, uniq = set(), []
    for n, g in rows:
        k = n.replace(" ", "")
        if k in seen:
            continue
        seen.add(k)
        uniq.append((n, g))

    groups = {}
    for n, g in uniq:
        if any(b in (g or "") for b in BAD_HEAD):
            continue
        g = norm_group(g)
        if len(g) > 24:
            continue
        groups.setdefault(g, []).append(n)

    # 图鉴没收录、但已核实过的团体补进来（保持人在前、不重复）
    for g, members in EXTRA_GROUPS.items():
        bucket = groups.setdefault(g, [])
        for m in members:
            if m not in bucket:
                bucket.append(m)

    acc = json.loads(CFG.read_text(encoding="utf-8"))["accounts"]

    out = ["# 微博找人参考资料（由 tools/build_roster.py 生成，勿手改）", "",
           "来源：用户资料库里的地偶情报图鉴与抓取图鉴（人工核对过的档案）。", "",
           "## 一、微博昵称命名规律", "",
           "成员个人号昵称＝「**中文名 + 罗马音 + _团名**」，团名后缀用来区分同名：", "",
           "| 团体 | 后缀 | 实例 |", "|---|---|---|"]
    for g, suf in KNOWN_SUFFIX.items():
        ex = [f"{a['nick']}" for a in acc if suf in (a.get("nick") or "")][:2]
        out.append(f"| {g} | `{suf}` | {'、'.join(ex) or '-'} |")
    out += ["",
            "团体官博多为「团名Official / 团名_Official」，个别带 `-` 前后缀。",
            "结论：知道姓名 + 团体就能拼出完整昵称；反过来，搜到的昵称必须含"
            "「中文名」或「罗马音」才算命中，否则是同名路人。", "",
            "## 二、已解析并纳入抓取的账号", ""]
    for a in acc:
        out.append(f"- {a.get('label')} -> `{a.get('nick')}`"
                   f" (uid {a.get('uid')}, {a.get('group') or '-'})")

    out += ["", "## 三、人物 -> 团体名册", ""]
    for g in sorted(groups, key=lambda x: -len(groups[x])):
        out.append(f"- **{g}**：" + "、".join(groups[g]))

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"写出 {OUT}（{len(chr(10).join(out))} 字符，{len(groups)} 个团体，{len(uniq)} 个人名）")


if __name__ == "__main__":
    main(sys.argv[1:])
