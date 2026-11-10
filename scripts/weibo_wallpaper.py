#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博原图壁纸抓取器 · Skill 统一引擎
=====================================
双通道，按 Cookie 自动选择：

  A 访客态（无 Cookie）
      m.weibo.cn/api/container/getIndex，容器：
        107603<uid>              正文微博
        230283<uid>              精选
        107803<uid>              相册分组（沿 scheme 下钻一层）
        107803<uid>_-_photoall   相册「全部图片」流（card_type=47）
      限制：每人最新约 12 条 + 相册第一页；page>=2 服务端硬拒 ok=-100。

  B PC 登录态（环境变量 Cookie 含 XSRF-TOKEN / WBPSESS）
      weibo.com/ajax/statuses/mymblog?uid=<uid>&page=<n> + X-XSRF-TOKEN
      可翻 --pages 页历史（实测单号 16 页 / 300+ 张图，访客态仅 10 张）；
      并补一次相册「全部图片」流第一页。
      注意：m.weibo.cn/api/config 会返回 login:false，这不代表 Cookie 失效，
            判据应看 weibo.com/ajax/profile/info 的 ok 与 mymblog page=2 的 ok。

原图原理（「查看原图很大、下载只有几百 KB」的根因）：
  微博图床按 URL 路径里的尺寸前缀动态重采样：
      https://wx{n}.sinaimg.cn/<前缀>/<pid>.jpg
  mw690 / woriginal / orj360 都是 CDN 重采样压缩版；未重采样的源站文件是 /large/。
  且 *.sinaimg.cn 有防盗链：不带微博域 Referer 直接 403（只回 238 字节错误页）。
  所以脚本对每条 URL 做两件事：① 尺寸前缀统一改写成 /large/；② 强制带 Referer。
  命名坑：PC 接口 pic_infos.original 实际是 orj1080 缩水版（296KB），
          largest 才是 /large/ 源站最优（2697KB）。勿用 original 字段。

凭证安全（硬约束）：
  Cookie 只从环境变量读取，绝不写入配置 / manifest / 报告 / 画廊 / 任何产物，
  也绝不打印其值（只打印字段名）。用完请在微博退出登录使其失效。

用法：
  python3 weibo_wallpaper.py --verify-only
  python3 weibo_wallpaper.py --config cfg.json --out ./output
  WB_COOKIE='XSRF-TOKEN=...; SUB=...; SUBP=...' \
      python3 weibo_wallpaper.py --config cfg.json --out ./output --pages 16
  python3 weibo_wallpaper.py --dry-run

依赖：pip install playwright requests pillow && playwright install chromium
"""

import argparse
import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import requests
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True
except ImportError as e:
    sys.exit(f"缺少依赖: {e}\n请执行: pip install requests pillow")

HERE = Path(__file__).resolve().parent
UA_MOBILE = ("Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36")
UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
IMG_HEADERS = {"User-Agent": UA_PC, "Referer": "https://weibo.com/",
               "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 原图基础函数
def to_large(url):
    """把任意微博图片 URL 的尺寸前缀重写为源站 /large/，去掉查询参数。"""
    if not url:
        return url
    url = url.split("?")[0].split("#")[0]
    m = re.match(r"^(https?://[^/]+)/([^/]+)/([^/]+)$", url)
    if not m:
        return url
    host, _size, fname = m.groups()
    return f"{host}/large/{fname}"


def clean_text(s):
    return re.sub(r"<[^>]+>", "", s or "").replace("&nbsp;", " ").strip()


def safe_name(s):
    return re.sub(r"[^\w\u4e00-\u9fff.-]", "_", s or "")[:24]


def head_size(url):
    """HEAD 拿 content-length 预筛体积；/large/ 不通时依次试 mw2000 / original。"""
    for candidate in [url, url.replace("/large/", "/mw2000/"),
                      url.replace("/large/", "/original/")]:
        try:
            r = requests.head(candidate, headers=IMG_HEADERS, timeout=15,
                              allow_redirects=True)
            if r.status_code == 200 and r.headers.get("content-length"):
                n = int(r.headers["content-length"])
                if n > 1024:
                    return n, candidate
        except Exception:
            continue
    return 0, url


def parse_cookie(env_name):
    """只从环境变量读 Cookie，返回 (kv, mode, xsrf)。绝不落盘。"""
    raw = os.environ.get(env_name, "")
    if not raw:
        return {}, "guest", ""
    kv = {}
    for seg in raw.split(";"):
        seg = seg.strip()
        if "=" in seg:
            k, v = seg.split("=", 1)
            kv[k.strip()] = v.strip()
    xsrf = kv.get("XSRF-TOKEN", "")
    mode = "pc" if (xsrf or kv.get("WBPSESS")) else "mobile"
    return kv, mode, xsrf


# ---------------------------------------------------------------- 浏览器会话
class WbRequestError(RuntimeError):
    """接口层面的失败（HTTP 非 200 / 空 body / 非 JSON），与「查无此人」区分开。

    旧版 get_json 用 `except: return {}` 把所有异常吞成空字典，
    导致「被 WAF 拦了」和「这个人搜不到」在日志里长得一模一样。
    """


class WeiboSession:
    """真实 Chromium 建立会话（绕过 WAF 432），复用该会话调接口。"""

    def __init__(self, cookie_kv, mode):
        self.cookie_kv = cookie_kv
        self.mode = mode
        self.pw = None
        self.browser = None
        self.ctx = None
        self.page = None
        self.last_error = ""

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            sys.exit("缺少 playwright。请执行: pip install playwright && "
                     "playwright install chromium")
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        pc = self.mode == "pc"
        opts = {"user_agent": UA_PC if pc else UA_MOBILE,
                "viewport": {"width": 1440, "height": 900} if pc
                           else {"width": 390, "height": 844}}
        if not pc:
            opts.update(is_mobile=True, has_touch=True)
        self.ctx = self.browser.new_context(**opts)
        if self.cookie_kv:
            domains = [".weibo.com", ".weibo.cn"] if pc else [".weibo.cn", ".weibo.com"]
            for dom in domains:
                for k, v in self.cookie_kv.items():
                    try:
                        self.ctx.add_cookies([{"name": k, "value": v,
                                              "domain": dom, "path": "/"}])
                    except Exception:
                        pass
        page = self.ctx.new_page()
        page.goto("https://weibo.com/" if pc else "https://m.weibo.cn/",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        self.page = page
        return self

    def __exit__(self, *a):
        try:
            self.browser.close()
            self.pw.stop()
        except Exception:
            pass

    def page_json(self, url):
        """在页面上下文里 fetch，带真实浏览器指纹。

        APIRequestContext（self.ctx.request）是裸请求，境外 runner 上会被 WAF 打成 432；
        页面内 fetch 走完整浏览器上下文，同样的 URL 通常能正常返回。
        """
        js = """async (u) => {
            try {
                const r = await fetch(u, {
                    credentials: 'include',
                    headers: {'Accept': 'application/json, text/plain, */*',
                              'X-Requested-With': 'XMLHttpRequest'},
                });
                return {status: r.status, body: (await r.text()).slice(0, 400000)};
            } catch (e) { return {status: 0, body: String(e)}; }
        }"""
        try:
            res = self.page.evaluate(js, url)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None
        status, body = res.get("status") or 0, res.get("body") or ""
        if status != 200:
            self.last_error = f"HTTP {status} (body {len(body)}B)"
            return None
        try:
            return json.loads(body)
        except Exception:
            self.last_error = f"非 JSON 响应 ({len(body)}B)"
            return None

    def get_json(self, url, referer=None, extra=None, retries=2):
        """先试裸请求，失败自动降级到页面内 fetch；两种都失败时 last_error 有真实原因。"""
        h = {"Referer": referer or "https://m.weibo.cn/",
             "X-Requested-With": "XMLHttpRequest",
             "Accept": "application/json, text/plain, */*"}
        if extra:
            h.update(extra)
        self.last_error = ""
        for i in range(retries + 1):
            try:
                r = self.ctx.request.get(url, headers=h, timeout=25000)
                if r.status != 200:
                    self.last_error = f"HTTP {r.status}"
                else:
                    try:
                        return r.json()
                    except Exception:
                        self.last_error = f"非 JSON 响应 (HTTP {r.status})"
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
            if i < retries:
                time.sleep(1.0)
        # 裸请求走不通，改用页面上下文再要一次
        if self.page is not None:
            d = self.page_json(url)
            if d is not None:
                self.last_error = ""
                return d
        return {}

    def resolve_uid(self, term, expect=None):
        """昵称 -> uid。接口被风控时抛 WbRequestError，与「查无此人」区分开。"""
        cid = urllib.parse.quote("100103type=3&q=" + term, safe="")
        url = ("https://m.weibo.cn/api/container/getIndex?containerid="
               + cid + "&page_type=searchall")
        d = self.get_json(url)
        if not d:
            raise WbRequestError(f"搜索接口不可用（{self.last_error or '空响应'}）")
        hits = []
        for c in d.get("data", {}).get("cards", []):
            grp = [c] if c.get("card_type") != 11 else c.get("card_group", [])
            for cc in grp:
                u = cc.get("user") or {}
                if not (u.get("screen_name") and u.get("id")):
                    # 综合页里大量卡片是 card_type 9（微博卡片），用户挂在 mblog.user 上
                    u = (cc.get("mblog") or {}).get("user") or {}
                if u.get("screen_name") and u.get("id"):
                    hits.append((u["screen_name"], str(u["id"]),
                                 u.get("verified_reason") or cc.get("desc1") or ""))
        if expect:
            for h in hits:
                if expect.lower() in (h[0] + h[2]).lower():
                    return h
        return hits[0] if hits else None

    def profile(self, uid):
        """取主页信息，用于复核「这个 uid 到底是谁」。拿不到返回 None。"""
        d = self.get_json(f"https://weibo.com/ajax/profile/info?uid={uid}",
                          referer=f"https://weibo.com/u/{uid}")
        data = d.get("data")
        u = (data.get("user") or {}) if isinstance(data, dict) else {}
        if not u:
            d2 = self.get_json(
                f"https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}",
                referer=f"https://m.weibo.cn/u/{uid}")
            d2data = d2.get("data")
            u = (d2data.get("userInfo") or {}) if isinstance(d2data, dict) else {}
        if not u:
            return None
        return {"uid": str(u.get("id") or uid),
                "nick": u.get("screen_name") or "",
                "verified": u.get("verified_reason") or "",
                "followers": u.get("followers_count") or 0,
                "desc": (u.get("description") or "")[:80]}


# ---------------------------------------------------------------- 图片记录提取
def new_rec(owner, acc, large, thumb, post_url, text, created, page=0):
    pid = large.rsplit("/", 1)[-1].split(".")[0]
    return {"pid": pid, "owner": owner, "uid": acc["uid"],
            "group": acc.get("group", ""), "large_url": large,
            "thumb_url": thumb or large.replace("/large/", "/orj480/"),
            "post_url": post_url, "text": clean_text(text)[:100],
            "created": created, "page": page, "w": 0, "h": 0}


def extract_mblog(mblog, owner, acc):
    """正文流/相册流里的 mblog：pics[] 或 pic_ids / original_pic 兜底。"""
    out = []
    mid = mblog.get("id") or mblog.get("mid") or ""
    post_url = f"https://m.weibo.cn/detail/{mid}" if mid else ""
    meta_text = clean_text(mblog.get("text_raw") or mblog.get("text") or "")
    created = mblog.get("created_at", "")
    for target in [mblog, mblog.get("retweeted_status") or {}]:
        pics = target.get("pics") or []
        if isinstance(pics, dict):
            pics = list(pics.values())
        for pic in pics:
            raw = (pic.get("large") or {}).get("url") or pic.get("url")
            pidv = pic.get("pid")
            if not raw and pidv:
                raw = f"https://wx1.sinaimg.cn/large/{pidv}.jpg"
            if not raw:
                continue
            large = to_large(raw)
            geo = (pic.get("large") or {}).get("geo") or pic.get("geo") or {}
            try:
                w, h = int(geo.get("width", 0)), int(geo.get("height", 0))
            except (TypeError, ValueError):
                w = h = 0
            r = new_rec(owner, acc, large, pic.get("url"), post_url,
                        meta_text, created)
            r["w"], r["h"] = w, h
            out.append(r)
        if not pics:
            for pidv in (target.get("pic_ids") or [])[:9]:
                out.append(new_rec(owner, acc,
                                   f"https://wx1.sinaimg.cn/large/{pidv}.jpg",
                                   None, post_url, meta_text, created))
            for raw in [target.get("original_pic"), target.get("bmiddle_pic")]:
                if raw and "sinaimg" in raw:
                    out.append(new_rec(owner, acc, to_large(raw), None,
                                       post_url, meta_text, created))
    return out


def extract_photostream(card, owner, acc):
    """相册「全部图片」流 card 的 pics[]（pic_mw2000 / pic_big）。"""
    out = []
    pics = list(card.get("pics") or [])
    for g in card.get("card_group", []) or []:
        pics.extend(g.get("pics") or [])
    for pic in pics:
        raw = (pic.get("pic_mw2000") or pic.get("pic_big")
               or pic.get("pic_middle") or pic.get("pic_small"))
        if not raw or "sinaimg" not in raw:
            continue
        mb = pic.get("mblog") or {}
        mid = mb.get("id") or ""
        out.append(new_rec(owner, acc, to_large(raw),
                           pic.get("pic_middle") or pic.get("pic_small"),
                           f"https://m.weibo.cn/detail/{mid}" if mid else "",
                           mb.get("text", ""), mb.get("created_at", "")))
    return out


# ---------------------------------------------------------------- 通道 A：访客态
def harvest_guest(sess, acc):
    uid, owner = acc["uid"], acc["label"]
    containers = [f"107603{uid}", f"230283{uid}", f"107803{uid}",
                  f"107803{uid}_-_photoall"]
    out, seen = [], set()
    for cid in containers:
        url = ("https://m.weibo.cn/api/container/getIndex?containerid="
               + urllib.parse.quote(cid, safe="")
               + f"&type=uid&value={uid}")
        d = sess.get_json(url, referer=f"https://m.weibo.cn/u/{uid}")
        cards = d.get("data", {}).get("cards", [])
        # 相册分组：沿 scheme 里的完整 containerid 下钻一层
        if cid == f"107803{uid}":
            subs = []
            for c in cards:
                for g in c.get("card_group", [c]):
                    m = re.search(r"containerid=([^&\s]+)", g.get("scheme") or "")
                    if m and m.group(1) not in subs:
                        subs.append(m.group(1))
            for sub in subs[:4]:
                d2 = sess.get_json(
                    "https://m.weibo.cn/api/container/getIndex?containerid="
                    + urllib.parse.quote(sub, safe="")
                    + f"&type=uid&value={uid}",
                    referer=f"https://m.weibo.cn/u/{uid}")
                cards.extend(d2.get("data", {}).get("cards", []))
        if "_-_photoall" in cid:
            for c in cards:
                out.extend(extract_photostream(c, owner, acc))
        for c in cards:
            for g in c.get("card_group", [c]):
                m = g.get("mblog")
                if not m or m.get("id") in seen:
                    continue
                seen.add(m.get("id"))
                out.extend(extract_mblog(m, owner, acc))
    return out, {"owner": owner, "uid": uid, "pages": 1, "posts": len(seen),
                 "pics": len(out)}


# ---------------------------------------------------------------- 通道 B：PC 登录态
def pc_headers(sess, uid, xsrf):
    return {"Referer": f"https://weibo.com/u/{uid}",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "X-XSRF-TOKEN": xsrf,
            "Client-Version": "v2.47.50",
            "Server-Version": "v2025.01.01"}


def harvest_pc(sess, acc, pages, xsrf):
    uid, owner = acc["uid"], acc["label"]
    H = pc_headers(sess, uid, xsrf)
    out, n_pages, n_posts = [], 0, 0
    for pg in range(1, pages + 1):
        u = (f"https://weibo.com/ajax/statuses/mymblog?uid={uid}"
             f"&page={pg}&feature=0")
        d = sess.get_json(u, referer=f"https://weibo.com/u/{uid}", extra=H)
        if d.get("ok") != 1:
            break
        lst = d.get("data", {}).get("list", []) if isinstance(d.get("data"), dict) else []
        if not lst:
            break
        for mb in lst:
            n_posts += 1
            for pidv, pic in (mb.get("pic_infos") or {}).items():
                r = _pc_pic(pidv, pic, mb, owner, acc, pg)
                if r:
                    out.append(r)
            rt = mb.get("retweeted_status") or {}
            for pidv, pic in (rt.get("pic_infos") or {}).items():
                r = _pc_pic(pidv, pic, rt, owner, acc, pg)
                if r:
                    out.append(r)
        n_pages += 1
        time.sleep(0.75)
    # 补一次相册「全部图片」流第一页（PC 正文流不含相册独占图）
    try:
        cid = f"107803{uid}_-_photoall"
        d = sess.get_json(
            "https://m.weibo.cn/api/container/getIndex?containerid="
            + urllib.parse.quote(cid, safe="") + f"&type=uid&value={uid}",
            referer=f"https://m.weibo.cn/u/{uid}")
        for c in d.get("data", {}).get("cards", []):
            out.extend(extract_photostream(c, owner, acc))
        time.sleep(0.5)
    except Exception:
        pass
    return out, {"owner": owner, "uid": uid, "pages": n_pages,
                 "posts": n_posts, "pics": len(out)}


def _pc_pic(pidv, pic, mb, owner, acc, pg):
    node = pic.get("largest") or pic.get("mw2000") or pic.get("large") or {}
    raw = node.get("url") if isinstance(node, dict) else None
    if not raw:
        raw = f"https://wx1.sinaimg.cn/large/{pidv}.jpg"
    large = to_large(raw)
    thumb = ((pic.get("bmiddle") or {}).get("url")
             or (pic.get("thumbnail") or {}).get("url") or None)
    mid = mb.get("id") or mb.get("mid") or ""
    post_url = (f"https://weibo.com/{acc['uid']}/{mb.get('mblogid') or mid}"
                if mid else "")
    return new_rec(owner, acc, large, thumb, post_url,
                   mb.get("text_raw") or mb.get("text") or "",
                   mb.get("created_at", ""), pg)


# ---------------------------------------------------------------- 最小验证
def verify_only(sess, cfg, mode, xsrf):
    uid = cfg["accounts"][0]["uid"]
    log("== 最小验证（不下载任何图片）==")
    log(f"   凭证字段（仅字段名，值不打印）: {sorted(sess.cookie_kv.keys()) or '无'}")
    log(f"   通道判定: {mode}")
    if mode == "guest":
        d = sess.get_json(
            f"https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}"
            f"&containerid=107603{uid}",
            referer=f"https://m.weibo.cn/u/{uid}")
        ok = d.get("ok")
        log(f"   访客态 getIndex 第 1 页: ok={ok}")
        log("   预期 page=2 返回 ok=-100（访客态登录墙），属正常。")
        log("   提示：设置 WB_COOKIE 后可验证 PC 登录态翻页。")
        return ok == 1
    a = sess.get_json("https://m.weibo.cn/api/config",
                      referer="https://m.weibo.cn/")
    login = a.get("data", {}).get("login")
    b = sess.get_json(f"https://weibo.com/ajax/profile/info?uid={uid}",
                      referer="https://weibo.com/")
    H = pc_headers(sess, uid, xsrf)
    rows = []
    ok_all = True
    for pg in (1, 2):
        d = sess.get_json(
            f"https://weibo.com/ajax/statuses/mymblog?uid={uid}&page={pg}&feature=0",
            referer=f"https://weibo.com/u/{uid}", extra=H)
        lst = d.get("data", {}).get("list", []) if isinstance(d.get("data"), dict) else []
        rows.append((pg, d.get("ok"), len(lst),
                     sum(len(m.get("pic_infos", {}) or {}) for m in lst)))
    # 匿名对照
    anon = sess.browser.new_context(user_agent=UA_PC)
    try:
        r = anon.request.get(
            f"https://weibo.com/ajax/statuses/mymblog?uid={uid}&page=1&feature=0",
            headers=H, timeout=25000)
        anon_ok = r.json().get("ok")
    except Exception:
        anon_ok = "ERR"
    anon.close()
    log("   | 判据 | 结果 |")
    log("   | --- | --- |")
    log(f"   | m.weibo.cn/api/config login | {login} （false 属正常，移动端不认 PC Cookie）|")
    log(f"   | weibo.com/ajax/profile/info ok | {b.get('ok')} |")
    for pg, o, n, npic in rows:
        mark = " ← 关键判据" if pg == 2 else ""
        log(f"   | mymblog page={pg} | ok={o} / {n} 条微博 / {npic} 张图{mark} |")
    log(f"   | 匿名 mymblog page=1 | ok={anon_ok} （-100 证明是登录态解锁）|")
    ok_all = (b.get("ok") == 1 and rows[1][1] == 1)
    log(f"== 结论: {'登录态有效，可以跑全量' if ok_all else '登录态未生效，停止，不跑全量'}")
    return ok_all


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="微博源站原图壁纸抓取器")
    ap.add_argument("--config", default=None, help="配置 JSON；缺省用当前目录 config.json 或内置模板")
    ap.add_argument("--out", default=None, help="输出目录，缺省 ./weibo-wallpaper-output")
    ap.add_argument("--cookie-env", default="WB_COOKIE", help="存放 Cookie 的环境变量名")
    ap.add_argument("--pages", type=int, default=16, help="PC 登录态每号最多翻多少页")
    ap.add_argument("--max-orig", type=int, default=45, help="每号最多下载横图原图数")
    ap.add_argument("--top-vertical", type=int, default=2, help="每号保留竖图原图数")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--compare", default=None, help="旧 manifest，用于标记新增；缺省用输出目录里已有的")
    ap.add_argument("--verify-only", action="store_true", help="只验证登录态/连通性，不下载")
    ap.add_argument("--dry-run", action="store_true", help="只统计不下载图片")
    args = ap.parse_args()

    cfg_path = (Path(args.config).resolve() if args.config else
                (Path.cwd() / "config.json" if (Path.cwd() / "config.json").exists()
                 else HERE / "config.example.json"))
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    out_dir = Path(args.out).resolve() if args.out else Path.cwd() / "weibo-wallpaper-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir, thumb_dir = out_dir / "原图", out_dir / "缩略图"
    img_dir.mkdir(exist_ok=True)
    thumb_dir.mkdir(exist_ok=True)

    RATIO = cfg.get("ratio", 16 / 9)
    TOL = cfg.get("ratio_tolerance", 0.06)
    MINB = cfg.get("min_bytes", 2 * 1024 * 1024)
    MINW = cfg.get("min_width", 1920)
    accounts = cfg["accounts"]

    kv, mode, xsrf = parse_cookie(args.cookie_env)
    log(f"== 配置 {cfg_path.name} | {len(accounts)} 个账号 | 通道 {mode} "
        f"| 口径 {RATIO:.4f}±{TOL} / ≥{MINB/1024/1024:.0f}MB / 宽≥{MINW}px")

    with WeiboSession(kv, mode) as sess:
        # 补齐配置里没写 uid 的账号
        for acc in accounts:
            if not acc.get("uid"):
                hit = sess.resolve_uid(acc["nick"], acc.get("expect"))
                if hit:
                    acc["uid"] = hit[1]
                    log(f"   解析 {acc['nick']} -> {hit[0]} ({hit[1]})")
                else:
                    log(f"   [跳过] 找不到账号: {acc['nick']}")
        accounts = [a for a in accounts if a.get("uid")]

        if args.verify_only:
            ok = verify_only(sess, {"accounts": accounts}, mode, xsrf)
            sys.exit(0 if ok else 2)

        recs, acct_stat = {}, []
        for acc in accounts:
            if mode == "pc":
                rows, st = harvest_pc(sess, acc, args.pages, xsrf)
            else:
                rows, st = harvest_guest(sess, acc)
            for r in rows:
                recs.setdefault(r["pid"], r)
            acct_stat.append(st)
            log(f"   {acc['label']:14s} 翻 {st['pages']:2d} 页 / "
                f"{st['posts']:3d} 条微博 / 取到 {st['pics']:3d} 张")
            time.sleep(0.5)
    log(f"== 合计 {len(recs)} 张唯一图片")

    # 标记「新增」
    cmp_path = Path(args.compare).resolve() if args.compare else out_dir / "manifest.json"
    old = set()
    if cmp_path.exists() and cmp_path != (out_dir / "manifest.json").resolve():
        try:
            old = {i["pid"] for i in json.loads(
                cmp_path.read_text(encoding="utf-8"))["items"]}
        except Exception:
            pass
    elif cmp_path.exists():
        try:
            old = {i["pid"] for i in json.loads(
                cmp_path.read_text(encoding="utf-8"))["items"]}
        except Exception:
            pass
    for r in recs.values():
        r["is_new"] = bool(old) and r["pid"] not in old

    # 并发探测真实分辨率（Range 读头）+ 体积（HEAD）
    def probe(item):
        pid, r = item
        o = {"w": 0, "h": 0, "bytes": 0}
        try:
            hh = {**IMG_HEADERS, "Range": "bytes=0-131071"}
            resp = requests.get(r["large_url"], headers=hh, timeout=20)
            if resp.status_code in (200, 206) and resp.content:
                try:
                    o["w"], o["h"] = Image.open(io.BytesIO(resp.content)).size
                except Exception:
                    pass
        except Exception:
            pass
        try:
            o["bytes"] = head_size(r["large_url"])[0]
        except Exception:
            pass
        return pid, o

    log("== 探测真实分辨率 / 体积 ...")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f in as_completed([ex.submit(probe, x) for x in recs.items()]):
            try:
                pid, o = f.result()
                recs[pid].update(o)
            except Exception:
                pass
    # 探测失败的窄图视为无效（缩略图/错误页）
    for pid in [r["pid"] for r in recs.values() if r["w"] and r["w"] < 600]:
        recs.pop(pid, None)
    log(f"== 有效 {len(recs)} 张 | 探测成功 "
        f"{sum(1 for r in recs.values() if r['w'] and r['h'])} 张")

    def is169(r):
        return r["w"] > r["h"] > 0 and abs(r["w"] / r["h"] - RATIO) <= TOL

    # 下载选择：16:9 候选无条件全收 —— 这类海报 HEAD 体积常偏小，
    # 若按体积排序取头部会被 3:2 相机原片挤出，导致体积记 0 误判「不足 2MB」。
    want = set()
    for acc in accounts:
        g = [r for r in recs.values() if r["owner"] == acc["label"]]
        want.update(r["pid"] for r in g if is169(r))
        land = sorted([r for r in g if r["w"] > r["h"]],
                      key=lambda x: -x["bytes"])[:args.max_orig]
        want.update(r["pid"] for r in land)
        vert = sorted([r for r in g if r["w"] <= r["h"]],
                      key=lambda x: -x["bytes"])[:args.top_vertical]
        want.update(r["pid"] for r in vert)
    log(f"== 待下载原图 {len(want)} 张 | 缩略图 {len(recs)} 张"
        + ("（dry-run，实际不下载）" if args.dry_run else ""))

    def fetch(kind, item):
        pid, r = item
        url = r["large_url"] if kind == "orig" else r["thumb_url"]
        dest = (img_dir if kind == "orig" else thumb_dir) / \
               f"{safe_name(r['owner'])}_{pid}.jpg"
        if dest.exists() and dest.stat().st_size > 800:
            return pid, kind, dest.name, dest.stat().st_size
        try:
            resp = requests.get(url, headers=IMG_HEADERS, timeout=60)
            if resp.status_code == 200 and len(resp.content) > 800:
                dest.write_bytes(resp.content)
                return pid, kind, dest.name, len(resp.content)
        except Exception:
            pass
        return pid, kind, "", 0

    if not args.dry_run:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            jobs = [ex.submit(fetch, "orig", (pid, recs[pid])) for pid in want]
            jobs += [ex.submit(fetch, "thumb", x) for x in recs.items()]
            for f in as_completed(jobs):
                try:
                    pid, kind, fn, _ = f.result()
                    if fn:
                        recs[pid]["file" if kind == "orig" else "thumb"] = fn
                except Exception:
                    pass

    # 按磁盘真实字节重新定级（不信任 HEAD 预筛值）
    for r in recs.values():
        fp = img_dir / r["file"] if r.get("file") else None
        if fp and fp.exists():
            r["real_bytes"] = fp.stat().st_size
            try:
                r["w"], r["h"] = Image.open(fp).size
            except Exception:
                pass
        else:
            r["real_bytes"] = 0
            r["file"] = ""
        r["head_mb"] = round(r["bytes"] / 1024 / 1024, 2)
        r["ratio_real"] = round(r["w"] / r["h"], 4) if r["w"] and r["h"] else 0
        r["size_mb"] = round(r["real_bytes"] / 1024 / 1024, 2)
        r["orient"] = ("横" if r["w"] > r["h"] else
                       "方" if r["h"] and abs(r["w"] - r["h"]) /
                       max(r["w"], r["h"]) < 0.08 else "竖")
        r["is169"] = is169(r)
        r["isbig"] = r["real_bytes"] >= MINB
        r["grade"] = ("S" if r["is169"] and r["isbig"] else
                      "A" if r["is169"] else
                      "B" if r["w"] > r["h"] and r["isbig"] else
                      "C" if r["w"] > r["h"] else "V")

    order = {"S": 5, "A": 4, "B": 3, "C": 2, "V": 1}
    items = sorted(recs.values(),
                   key=lambda r: (-order[r["grade"]], -r["real_bytes"]))
    stats = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "PC登录态" if mode == "pc" else "访客态",
        "accounts": len(accounts), "max_pages": args.pages if mode == "pc" else 1,
        "unique_images": len(items),
        "new_vs_prev": sum(1 for r in items if r["is_new"]),
        "strict": sum(1 for r in items if r["grade"] == "S"),
        "big_landscape": sum(1 for r in items if r["grade"] in ("S", "B")),
        "landscape": sum(1 for r in items if r["w"] > r["h"]),
        "downloaded": sum(1 for r in items if r["file"]),
        "total_mb": round(sum(r["real_bytes"] for r in items) / 1024 / 1024, 1),
        "acct_stat": acct_stat,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps({"stats": stats,
                    "criteria": {"ratio": RATIO, "tolerance": TOL,
                                 "min_bytes": MINB, "min_width": MINW},
                    "items": items}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    write_gallery(out_dir, items, stats)
    write_report(out_dir, items, stats, cfg, mode, args.cookie_env)
    log(f"== 完成 | 唯一 {stats['unique_images']} | S档 {stats['strict']} "
        f"| 下载 {stats['downloaded']} 张 / {stats['total_mb']}MB")
    log(f"== 产物: {out_dir}")


# ---------------------------------------------------------------- 画廊
def write_gallery(out_dir, items, stats):
    owners = sorted({r["owner"] for r in items})
    nS = sum(1 for r in items if r["grade"] == "S")
    n169 = sum(1 for r in items if r["is169"])
    nbig = sum(1 for r in items if r["isbig"])
    ndl = sum(1 for r in items if r["file"])
    nnew = sum(1 for r in items if r["is_new"])
    badge = {"S": ("S·16:9达标", "ok"), "A": ("A·16:9", "ok"),
             "B": ("B·大横图", "warm"), "C": ("C·横图", "mute"),
             "V": ("V·竖图", "mute")}
    cards = []
    for i, r in enumerate(items, 1):
        th = f"缩略图/{r['thumb']}" if r.get("thumb") else ""
        orig = f"原图/{r['file']}" if r.get("file") else r["large_url"]
        gtxt, gcls = badge[r["grade"]]
        newtag = '<span class="newtag">NEW</span>' if r["is_new"] else ""
        txt = html.escape((r["text"] or "（无文案）").replace("\n", " "))
        link = html.escape(r["post_url"] or "#")
        cards.append(f"""
    <div class="card" data-g="{r['grade']}" data-o="{html.escape(r['owner'])}"
         data-new="{1 if r['is_new'] else 0}" data-dl="{1 if r['file'] else 0}"
         data-big="{1 if r['isbig'] else 0}" data-169="{1 if r['is169'] else 0}">
      <a class="shot" href="{html.escape(orig)}" target="_blank"><img loading="lazy" src="{html.escape(th)}" alt=""></a>
      <div class="meta">
        <div class="row1"><span class="no">{i:04d}</span><span class="badge {gcls}">{gtxt}</span>{newtag}
          <span class="sz {'big' if r['isbig'] else ''}">{r['size_mb']}MB</span></div>
        <div class="dim">{r['w']}×{r['h']} <span class="ratio">· {r['ratio_real']}</span> <span class="who">{html.escape(r['owner'])}</span></div>
        <div class="txt">{txt}</div>
        <a class="wb" href="{link}" target="_blank">查看原微博 ↗</a>
      </div>
    </div>""")
    opts = "".join(f'<option value="{html.escape(o)}">{html.escape(o)}</option>'
                   for o in owners)
    doc = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>微博原图壁纸 · 筛选结果</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:radial-gradient(1200px 700px at 12% -10%,#2a2140 0%,transparent 60%),radial-gradient(1000px 600px at 92% 0%,#152c3a 0%,transparent 55%),#0b0c10;color:#e8eaf0;font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh;padding:34px 28px 70px}}
.wrap{{max-width:1360px;margin:0 auto}}
h1{{font-size:27px;font-weight:650;letter-spacing:.5px;background:linear-gradient(90deg,#fff,#b9a7ff 60%,#7fd7ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.sub{{color:#8b90a0;font-size:13px;margin-top:6px}}
.stat{{margin:20px 0 12px;padding:13px 18px;border-radius:14px;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.09);backdrop-filter:blur(18px);color:#c9cddb;font-size:13px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.stat b{{color:#fff;font-weight:600}}
.bar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:20px}}
.btn{{font-size:12px;padding:8px 14px;border-radius:20px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.04);color:#c9cddb;cursor:pointer;transition:.2s;backdrop-filter:blur(10px)}}
.btn:hover{{border-color:rgba(160,190,255,.5);color:#fff}}
.btn.on{{background:rgba(140,175,255,.18);border-color:rgba(150,190,255,.55);color:#fff}}
select{{margin-left:auto;font-size:12px;padding:8px 12px;border-radius:20px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.04);color:#c9cddb;outline:none}}
select option{{background:#171922;color:#e8eaf0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:16px}}
.card{{border-radius:16px;overflow:hidden;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);backdrop-filter:blur(16px);transition:transform .2s,border-color .2s;display:flex;flex-direction:column}}
.card:hover{{transform:translateY(-3px);border-color:rgba(180,160,255,.45)}}
.shot{{display:block;aspect-ratio:4/3;overflow:hidden;background:#15161c}}
.shot img{{width:100%;height:100%;object-fit:cover;display:block}}
.meta{{padding:11px 13px 13px;display:flex;flex-direction:column;gap:6px;flex:1}}
.row1{{display:flex;align-items:center;gap:7px}}
.no{{font:600 11px ui-monospace,monospace;color:#7b8095}}
.badge{{font-size:10.5px;padding:1px 7px;border-radius:20px;border:1px solid rgba(255,255,255,.14);color:#aeb3c4;white-space:nowrap}}
.badge.ok{{background:rgba(90,220,150,.14);border-color:rgba(90,220,150,.4);color:#8ee6b4}}
.badge.warm{{background:rgba(255,180,110,.14);border-color:rgba(255,180,110,.4);color:#ffc793}}
.newtag{{font-size:10px;padding:1px 6px;border-radius:20px;background:rgba(90,220,150,.16);color:#8ee6b4}}
.sz{{margin-left:auto;font:600 11.5px ui-monospace,monospace;color:#9aa0b4;white-space:nowrap}}
.sz.big{{color:#ff9e7d}}
.dim{{font:12px ui-monospace,monospace;color:#d6d9e6}}
.ratio{{color:#7f8599}}
.who{{float:right;color:#8fb8ff;font-family:-apple-system,"PingFang SC",sans-serif;font-size:11px}}
.txt{{font-size:12px;color:#9499ab;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:34px}}
.wb{{margin-top:auto;font-size:11.5px;color:#8fb8ff;text-decoration:none}}
.wb:hover{{text-decoration:underline}}
.ft{{margin-top:30px;color:#5f6474;font-size:11.5px;text-align:center}}
</style></head><body><div class="wrap">
<h1>微博原图壁纸 · 筛选结果</h1>
<div class="sub">{stats['mode']} · {stats['accounts']} 个账号 · 源站 /large/ 真原图 · 点击图片打开原图</div>
<div class="stat">📷 <span>共 <b>{len(items)}</b> 张唯一图 · 16:9 <b>{n169}</b> 张 · 严格达标 S <b>{nS}</b> 张 · ≥2MB <b>{nbig}</b> 张 · 已下原图 <b>{ndl}</b> 张 · 新增 <b>{nnew}</b> 张 · {stats['total_mb']} MB</span></div>
<div class="bar">
<button class="btn on" data-f="all">全部 {len(items)}</button>
<button class="btn" data-f="S">S 严格 {nS}</button>
<button class="btn" data-f="A">A 16:9</button>
<button class="btn" data-f="B">B 大横图</button>
<button class="btn" data-f="169">16:9 {n169}</button>
<button class="btn" data-f="big">≥2MB {nbig}</button>
<button class="btn" data-f="new">仅新增 {nnew}</button>
<button class="btn" data-f="dl">已下原图 {ndl}</button>
<select id="own"><option value="all">全部账号 {len(owners)}</option>{opts}</select>
</div>
<div class="grid" id="g">{''.join(cards)}</div>
<div class="ft">仅供个人做壁纸使用 · 版权归原作者及所属团体 · 生成于 {stats['generated']}</div>
</div>
<script>
let curF='all';
function apply(){{
  const own=document.getElementById('own').value;
  document.querySelectorAll('.card').forEach(c=>{{
    const f=curF, g=c.dataset.g;
    const okF=(f==='all'||(f==='S'&&g==='S')||(f==='A'&&g==='A')||(f==='B'&&g==='B')||
      (f==='169'&&c.dataset['169']==='1')||(f==='big'&&c.dataset.big==='1')||
      (f==='new'&&c.dataset.new==='1')||(f==='dl'&&c.dataset.dl==='1'));
    const okO=(own==='all'||c.dataset.o===own);
    c.style.display=(okF&&okO)?'flex':'none';
  }});
}}
document.querySelectorAll('.btn').forEach(b=>b.onclick=()=>{{
  document.querySelectorAll('.btn').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); curF=b.dataset.f; apply();
}});
document.getElementById('own').onchange=apply;
</script>
</body></html>"""
    (out_dir / "画廊.html").write_text(doc, encoding="utf-8")


# ---------------------------------------------------------------- 报告
def write_report(out_dir, items, stats, cfg, mode, cookie_env):
    S = [r for r in items if r["grade"] == "S"]
    srows = "\n".join(
        f"| {'新' if r['is_new'] else '旧'} | {r['owner']} | {r['w']}×{r['h']} | "
        f"{r['ratio_real']} | {r['size_mb']} MB | [原微博]({r['post_url']}) |"
        for r in S[:30])
    arows = "\n".join(
        f"| {r['owner']} | {r['w']}×{r['h']} | {r['size_mb']} MB |"
        for r in items if r["grade"] == "A")
    acct = "\n".join(
        f"| {x['owner']} | {x['uid']} | {x['pages']} | {x['posts']} | {x['pics']} |"
        for x in stats["acct_stat"])
    md = f"""# 微博原图壁纸 · 抓取报告

生成时间：{stats['generated']}　|　通道：{stats['mode']}　|　账号数：{stats['accounts']}

## 一、为什么「查看原图很大，下载却只有几百 KB」

微博图床按 URL 路径里的**尺寸前缀**动态重采样：

```
https://wx{{n}}.sinaimg.cn/<尺寸前缀>/<pid>.jpg
```

实测同一张图（pid `4e56d113gy3gquw3heol7j216o1kwhdv`）：

| 前缀 | 分辨率 | 体积 | 说明 |
| --- | --- | --- | --- |
| `thumb150` | 150×150 | 8 KB | 列表缩略图 |
| `mw690` | 690×920 | 221 KB | 页面常规展示图 ← **几百 KB 的主要来源** |
| `mw1024` | 1024×1365 | 471 KB | 中等 |
| `woriginal` | 1536×2048 | 283 KB | 「查看原图」常给的版本，带水印 + 二次压缩 |
| `mw2000` / `large` | 1536×2048 | **1,041 KB** | 源站未重采样原始二进制 ← 真原图 |
| PC `largest` | 2332×3498 | **2,697 KB** | 源站最优，与 `/large/` 等价 |
| PC `original` | — | 296 KB | **命名坑**：实为 `orj1080` 缩水版，勿用 |

结论：接口里叫 `large` 的字段，实际 URL 常仍是 `mw2000`；页面保存又多半是 `orj360`。
**把路径前缀统一改写成 `/large/` 并强制带 `Referer: https://weibo.com/`** 才能拿到真原图
（不带该 Referer 直接 403，只返回 238 字节错误页）。这两步已固化进 `to_large()` 与 `IMG_HEADERS`。
`oslarge` / `osj1080` 是去水印前缀，实测反而更小，不采用。

## 二、筛选口径与结果

- 目标比例 {cfg.get('ratio',16/9):.4f}（容差 ±{cfg.get('ratio_tolerance',0.06)}）
- 文件 ≥ {cfg.get('min_bytes',2097152)/1024/1024:.0f} MB，宽度参考 ≥ {cfg.get('min_width',1920)} px

| 项 | 值 |
| --- | --- |
| 唯一图片 | {stats['unique_images']} 张 |
| 相对上一轮新增 | {stats['new_vs_prev']} 张 |
| 严格达标 16:9 + ≥2MB（S 档） | **{stats['strict']} 张** |
| ≥2MB 横图（S+B） | {stats['big_landscape']} 张 |
| 横图总数 | {stats['landscape']} 张 |
| 抓回源站原图 | {stats['downloaded']} 张 / {stats['total_mb']} MB |

分级：**S** 16:9 且 ≥2MB ｜ **A** 16:9 但 <2MB ｜ **B** ≥2MB 横图但非 16:9 ｜
**C** 其余横图 ｜ **V** 竖图。体积一律以**下载后磁盘真实字节**为准，不信任 HEAD 预筛值。

### S 档（可直接做壁纸）

| 新旧 | 归属 | 分辨率 | 比例 | 体积 | 来源 |
| --- | --- | --- | --- | --- | --- |
{srows or '| — | — | — | — | — | — |'}

### A 档（16:9 真原图，体积不足 2MB）

| 归属 | 分辨率 | 体积 |
| --- | --- | --- |
{arows or '| — | — | — |'}

## 三、两个已修掉的真实缺陷

1. **16:9 候选必须无条件全下载**。曾按 HEAD 体积排序取每号前 45 张横图，
   结果 41 张 16:9 海报（体积偏小，被 3:2 相机原片挤出）没下原图、体积记 0，
   被误判成「不足 2MB」。改为 16:9 全收后 S 档从 4 张修正为 9 张。
2. **相册「全部图片」子容器 id 不能只匹配数字**。`107803<uid>_-_photoall` 含连字符，
   旧正则 `(\\d+)` 会截断，导致相册上千张的账号被误判为 0 张。

## 四、各账号明细

| 账号 | UID | 翻页 | 微博条数 | 取到图片 |
| --- | --- | --- | --- | --- |
{acct}

## 五、限制与扩展

- **访客态**：`m.weibo.cn/api/container/getIndex` 对 `page>=2` 硬拒 `ok=-100`，
  **与登录态无关**；只能拿每人最新约 12 条 + 相册第一页。
- **PC 登录态**：Cookie 含 `XSRF-TOKEN` / `WBPSESS` 时走
  `weibo.com/ajax/statuses/mymblog?uid=<uid>&page=<n>` + `X-XSRF-TOKEN`，可翻 `--pages` 页。
  判据看 `weibo.com/ajax/profile/info` 的 `ok=1` 与 `mymblog page=2` 的 `ok=1`；
  `m.weibo.cn/api/config` 返回 `login:false` 属正常，不要据此判断 Cookie 失效。
- **「2MB 以上」只是画质代理指标**：微博对上传图做有损压缩，源站 `large` 普遍 0.8–1.5MB，
  16:9 在偶像自拍里本就稀少，两个条件叠加命中率天然低。建议改用「宽 ≥1920px」作门槛。
- **B 档 3:2 相机原片**（5000×3333 ~ 9212×5182，单张 10–25MB）画质比 S 档还猛，
  中心裁成 16:9 不丢像素即可当壁纸；本轮**未擅自裁切**，原图保持完整。
- 图片版权归原作者及所属团体，**仅供个人做壁纸**，请勿二次分发或商用。

## 六、凭证安全

Cookie 只从环境变量 `{cookie_env}` 读取，
未写入本目录任何脚本、配置、manifest、报告、画廊或交付物；日志只打印字段名，不打印值。
**用完请在微博退出登录使其失效**（这是完整登录凭证）。

完整清单见 `manifest.json`，可视化挑图见 `画廊.html`，原图在 `原图/` 目录。
"""
    (out_dir / "报告.md").write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
