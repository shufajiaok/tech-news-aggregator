#!/usr/bin/env python3
"""云端增量抓取：只产出元数据 data.json，不落地任何图片文件。

与本地 build_site.py 的分工
    本地版：下载原图 -> 重采样 720px 缩略图 -> 提交 thumbs/ + data.json
    云端版：只探测分辨率与体积 -> 只提交 data.json
           缩略图与原图都由服务端的 /i/<档位>/<key> 向微博 CDN 实时代理

所以本仓库里零图片，Action 单次产物几十 KB，可以每天跑。

用法
    python tools/cloud_build.py                      # 访客态全量一轮
    python tools/cloud_build.py --uids 7969451525    # 只跑指定 uid，便于验证
    python tools/cloud_build.py --dry-run            # 只打印统计，不写文件
"""
import argparse
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "scripts"))
import weibo_wallpaper as wb  # noqa: E402

RATIO = 1.7778
TOL = 0.06
MIN_BYTES = 2097152   # 2MB
MIN_WIDTH = 1920
THUMB_SIZE = "wap720"  # 微博 CDN 现成档位，约 90KB，做首屏缩略图刚好


def log(*a):
    print(*a, flush=True)


def parse_created(s):
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return ""


def sina_key(url):
    """取微博图片 key。四个 wx 主机对同一 key 返回字节一致，故只存 key。"""
    cand = (url or "").split("?")[0].rsplit("/", 1)[-1]
    return cand if wb.re.match(r"^[0-9a-zA-Z]{6,}\.jpg$", cand) else ""


def probe(r):
    """Range 读前 128KB 解码出分辨率，HEAD 拿体积。不下载整图。"""
    o = {"w": 0, "h": 0, "bytes": 0}
    try:
        hh = {**wb.IMG_HEADERS, "Range": "bytes=0-131071"}
        resp = requests.get(r["large_url"], headers=hh, timeout=25)
        if resp.status_code in (200, 206) and resp.content:
            try:
                o["w"], o["h"] = Image.open(io.BytesIO(resp.content)).size
            except Exception:
                pass
    except Exception:
        pass
    try:
        o["bytes"] = wb.head_size(r["large_url"])[0]
    except Exception:
        pass
    return r["pid"], o


def load_old(path):
    if not path.exists():
        return {}, [], {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, [], {}
    items = d.get("items") or []
    return {i["key"]: i for i in items if i.get("key")}, items, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(REPO / "scripts" / "weibo_config.json"))
    ap.add_argument("--data", default=str(REPO / "frontend" / "wallpaper" / "data.json"))
    ap.add_argument("--feed", default=str(REPO / "frontend" / "wallpaper" / "feed.json"))
    ap.add_argument("--feed-max", type=int, default=2000, help="动态流最多保留多少条")
    ap.add_argument("--set-key", default="main")
    ap.add_argument("--label", default="主轮")
    ap.add_argument("--uids", default="", help="逗号分隔，只跑这些 uid")
    ap.add_argument("--names", default="", help="逗号分隔，按昵称搜索 uid 后抓")
    ap.add_argument("--pages", type=int, default=6, help="登录态下每号翻几页")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-new", type=int, default=0, help="本次最多追加多少条，0 不限")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    accounts = cfg["accounts"]
    if args.names:
        accounts = []  # 指定名字时只抓这些，名单只用来快速命中 nick
    elif args.uids:
        want = {u.strip() for u in args.uids.split(",") if u.strip()}
        accounts = [a for a in accounts if str(a["uid"]) in want]
        if not accounts:
            sys.exit(f"没有匹配的 uid: {args.uids}")

    data_path = Path(args.data)
    old_by_key, old_items, old_meta = load_old(data_path)
    log(f"== 账号 {len(accounts)} 个 | 既有条目 {len(old_items)} | 输出 {data_path}")

    cookie_kv, mode, xsrf = wb.parse_cookie("WB_COOKIE")
    if cookie_kv:
        log(f"== 登录态: {sorted(cookie_kv.keys())} | 通道 {mode} | 每号 {args.pages} 页")
    else:
        log("== 无 WB_COOKIE，走访客态（每号第 1 页 + 相册流；部分账号仅相册可见）")

    t0 = time.time()
    recs = {}
    with wb.WeiboSession(cookie_kv, mode) as sess:
        if args.names:
            known = {a["nick"]: a for a in cfg["accounts"] if a.get("nick")}
            for raw in [n.strip() for n in args.names.split(",") if n.strip()]:
                if raw in known:
                    accounts.append(known[raw])
                    log(f"   名单命中 {raw} -> uid {known[raw]['uid']}")
                    continue
                if raw.isdigit():
                    accounts.append({"label": raw, "group": "临时", "nick": raw, "uid": raw})
                    continue
                hit = sess.resolve_uid(raw)
                if hit:
                    log(f"   搜索 {raw} -> {hit[0]} ({hit[1]})")
                    accounts.append({"label": hit[0], "group": "临时",
                                     "nick": hit[0], "uid": hit[1]})
                else:
                    log(f"   搜索 {raw} 无结果，跳过")
            if not accounts:
                sys.exit(f"名字都没解析出 uid: {args.names}")
            log(f"== 本次只抓 {len(accounts)} 个账号")

        for acc in accounts:
            try:
                if mode == "pc":
                    rows, st = wb.harvest_pc(sess, acc, args.pages, xsrf)
                else:
                    rows, st = wb.harvest_guest(sess, acc)
            except Exception as e:
                log(f"   {acc['label']:16s} 失败: {type(e).__name__}: {e}")
                continue
            for r in rows:
                recs.setdefault(r["pid"], r)
            log(f"   {acc['label']:16s} 微博 {st['posts']:3d} 条 / 图 {st['pics']:3d} 张")
            time.sleep(0.4)
    log(f"== 合计 {len(recs)} 张唯一图片 | {time.time() - t0:.0f}s")
    if not recs:
        sys.exit("一个账号都没抓到图：会话被 WAF 拦或境外 IP 被限流，"
                 "本次不写文件（保留旧索引）")

    # 已有 key 直接跳过探测，省掉绝大部分请求
    todo = [r for pid, r in recs.items() if sina_key(r["large_url"]) not in old_by_key]
    log(f"== 待探测 {len(todo)} 张（已收录的跳过）")
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f in as_completed([ex.submit(probe, r) for r in todo]):
            try:
                pid, o = f.result()
                recs[pid].update(o)
                if not o["w"]:
                    failed += 1
            except Exception as e:
                failed += 1
                log(f"   probe 异常: {type(e).__name__}: {e}")
    log(f"== 探测完成，失败 {failed} 张")

    fresh = []
    # ---- 动态流：所有探测成功的图，不筛 16:9 / 体积，独立于壁纸页
    feed_path = Path(args.feed)
    old_feed = []
    if feed_path.exists():
        try:
            old_feed = json.loads(feed_path.read_text(encoding="utf-8")).get("items") or []
        except Exception:
            old_feed = []
    if not old_feed:
        # 首次生成：用壁纸索引当种子，动态流一上线就有历史
        old_feed = [{
            "key": i["key"], "owner": i.get("owner", ""), "group": i.get("group", ""),
            "w": i.get("w", 0), "h": i.get("h", 0), "mb": i.get("mb", 0),
            "created": i.get("created", ""), "post": i.get("post", ""),
            "text": i.get("text", ""), "set_key": i.get("set_key"),
        } for i in old_items if i.get("key")]
        log(f"== 动态流种子 {len(old_feed)} 条（取自壁纸索引）")
    feed_seen = {i["key"] for i in old_feed}
    feed_added = 0

    drop = {"已收录": 0, "无 key": 0, "窄图": 0, "非16:9": 0, "不足2MB": 0, "宽度<1920": 0}
    for pid, r in recs.items():
        key = sina_key(r["large_url"])
        w, h, n = r.get("w", 0), r.get("h", 0), r.get("bytes", 0)
        if not key:
            drop["无 key"] += 1
            continue
        if w >= 600 and h > 0 and key not in feed_seen:
            old_feed.append({
                "key": key, "owner": r["owner"], "group": r.get("group", ""),
                "w": w, "h": h, "mb": round(n / 1048576, 2),
                "created": parse_created(r.get("created") or ""),
                "post": r.get("post_url") or "",
                "text": (r.get("text") or "")[:160],
                "set_key": args.set_key,
            })
            feed_seen.add(key)
            feed_added += 1
        if key in old_by_key:
            drop["已收录"] += 1
            continue
        if w < 600 or h <= 0:
            drop["窄图"] += 1
            continue  # 探测失败或错误页
        if abs(w / h - RATIO) > TOL:
            drop["非16:9"] += 1
            continue
        if w < MIN_WIDTH:
            drop["宽度<1920"] += 1
            continue
        if n < MIN_BYTES:
            drop["不足2MB"] += 1
            continue
        fname = f"{wb.safe_name(r['owner'])}_{pid}.jpg"
        fresh.append({
            "set": args.label,
            "set_key": args.set_key,
            "file": fname,
            # 缩略图不随包，交给服务端实时向微博 CDN 取 wap720 档
            "thumb": f"/i/{THUMB_SIZE}/{key}",
            "key": key,
            "owner": r["owner"],
            "group": r.get("group", ""),
            "grade": "S",
            "w": w, "h": h,
            "ratio": round(w / h, 4),
            "mb": round(n / 1048576, 2),
            "bytes": n,
            "created": parse_created(r.get("created") or ""),
            "post": r.get("post_url") or "",
            "text": (r.get("text") or "")[:160],
        })

    fresh.sort(key=lambda x: (-x["bytes"]))
    if args.max_new:
        fresh = fresh[:args.max_new]
    log("== 淘汰: " + " | ".join(f"{k} {v}" for k, v in drop.items() if v))
    log(f"== 新增合格 {len(fresh)} 张（16:9 / >=2MB / 宽>=1920）")
    for it in fresh[:15]:
        log(f"   + {it['owner']:16s} {it['w']}x{it['h']} {it['mb']:5.2f}MB "
            f"{it['created']} {it['key'][:22]}")
    if len(fresh) > 15:
        log(f"   ... 其余 {len(fresh) - 15} 张")

    if args.dry_run:
        log(f"== dry-run，不写文件（壁纸 +{len(fresh)}，动态 +{feed_added}）")
        return

    # 动态流滚动窗口：时间倒序，只保留最近 --feed-max 条
    old_feed.sort(key=lambda x: x.get("created") or "", reverse=True)
    if args.feed_max and len(old_feed) > args.feed_max:
        old_feed = old_feed[:args.feed_max]
    if feed_added or not feed_path.exists():
        feed_meta = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count": len(old_feed),
            "items": old_feed,
        }
        feed_path.parent.mkdir(parents=True, exist_ok=True)
        tmpf = feed_path.with_suffix(".json.tmp")
        tmpf.write_text(json.dumps(feed_meta, ensure_ascii=False,
                                   separators=(",", ":")), encoding="utf-8")
        tmpf.replace(feed_path)
        log(f"== 写入 {feed_path} | 共 {len(old_feed)} 条（+{feed_added}）"
            f" | {feed_path.stat().st_size / 1024:.0f}KB")

    if not fresh:
        log("== 壁纸无新增，data.json 保持不变")
        return

    items = old_items + fresh
    sets = {}
    for it in items:
        sk = it.get("set_key")
        if sk:
            sets.setdefault(sk, it["set"])
    labels = dict(sets)
    try:
        prev = json.loads(data_path.read_text(encoding="utf-8")).get("set_labels") or {}
        labels = {**prev, **labels}
    except Exception:
        pass

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(items),
        "set_labels": labels,
        # 新条目的缩略图走服务端代理，不占仓库体积；旧条目仍是随包缩略图
        "thumbs_bytes": old_meta.get("thumbs_bytes") or 0,
        "items": items,
    }
    data_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = data_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(data_path)
    log(f"== 写入 {data_path} | 共 {len(items)} 条（+{len(fresh)}）"
        f" | {data_path.stat().st_size / 1024:.0f}KB | {time.time() - t0:.0f}s")

    summary = Path(__import__("os").environ.get("GITHUB_STEP_SUMMARY", "/dev/null"))
    try:
        if summary.parent.exists():
            with summary.open("a", encoding="utf-8") as f:
                f.write(f"### 壁纸增量\n\n- 账号 {len(accounts)} 个，探测图片 {len(todo)} 张\n"
                        f"- 壁纸新增 {len(fresh)} 张，累计 {len(items)} 张\n"
                        f"- 动态新增 {feed_added} 张，累计 {len(old_feed)} 条\n"
                        f"- 耗时 {time.time() - t0:.0f}s\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
