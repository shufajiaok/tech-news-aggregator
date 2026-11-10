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
import re
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
sys.path.insert(0, str(HERE))
import weibo_wallpaper as wb  # noqa: E402
import llm_plan  # noqa: E402

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


def find_account(raw, accounts):
    """昵称模糊命中：全名优先，再试 label，最后试包含关系。

    用户和模型都会说简称（「白泠」而不是「白泠Mio-StarHoney」），
    先在本地名单里消掉，能省掉微博搜索（境外调用不稳定）。
    """
    def norm(s):
        return re.sub(r"[\s\-_·・]+", "", s or "").lower()

    n = norm(raw)
    if not n:
        return None
    for a in accounts:
        if any(norm(a.get(k)) == n for k in ("nick", "label", "uid")):
            return a
    for a in accounts:  # 包含：白泠 -> 白泠Mio-StarHoney
        if any(n and n in norm(a.get(k)) for k in ("nick", "label")):
            return a
    return None


def uid_from_text(raw):
    """纯数字 / 主页链接 -> uid；抽不到返回空串。"""
    s = (raw or "").strip()
    if s.isdigit() and len(s) >= 6:
        return s
    m = re.search(r"weibo\.(?:com|cn)/(?:u/|profile/)?(\d{6,})", s)
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(\d{9,10})(?!\d)", s)
    return m.group(1) if m else ""


def verify_and_add(sess, uid, want, accounts, log, hint="", trust=False):
    """复核 uid 到底是谁，通过才加入 accounts。

    模型联网找 uid 有编造风险，所以一律走 profile/info 对照昵称与认证信息；
    对不上就拒绝并打印真实昵称，让人改用主页链接。
    """
    info = sess.profile(uid)
    if not info:
        if trust:
            accounts.append({"label": want or uid, "group": "临时",
                             "nick": want or uid, "uid": uid})
            log(f"   ✓ 直接使用 uid {uid}（复核接口不可用，按输入信任）")
            return True
        log(f"   ! {want}: uid {uid} 复核拿不到主页（cookie 失效或被风控），跳过")
        return False
    nick, ver = info["nick"], info["verified"]
    if not nick:
        log(f"   ! {want or uid}: uid {uid} 主页读不到昵称，跳过")
        return False
    w = (want or "").strip().lower()
    if w and w not in (nick + " " + ver).lower():
        log(f"   ! {want}: uid {uid} 实际是「{nick}」"
            f"{('（' + ver + '）') if ver else ''}，与所指不符，跳过。"
            f"若确是这个号，请直接粘主页链接")
        return False
    accounts.append({"label": nick, "group": "临时", "nick": nick, "uid": uid})
    log(f"   ✓ {want or nick} -> {nick} (uid {uid})"
        f"{(' | ' + ver) if ver else ''}{hint}")
    return True


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
    ap.add_argument("--q", default="", help="自然语言请求，由大模型解析成 names/uids/pages")
    ap.add_argument("--pages", type=int, default=6, help="登录态下每号翻几页")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-new", type=int, default=0, help="本次最多追加多少条，0 不限")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    accounts = cfg["accounts"]

    # 自然语言优先：解析成功就用它的 names/uids/pages，失败才退回结构化参数
    if args.q.strip():
        log(f"== 自然语言请求: {args.q}")
        plan = llm_plan.resolve(
            args.q, [a.get("nick") for a in cfg["accounts"] if a.get("nick")],
            log=log)
        if plan:
            # 前端已经拆出 uid（粘了主页链接）时要保留，不能被模型解析结果覆盖
            preset = {u.strip() for u in (args.uids or "").split(",") if u.strip()}
            merged = sorted(preset | set(plan["uids"]))
            args.names = ",".join(plan["names"])
            args.uids = ",".join(merged)
            args.pages = plan["pages"]
            if not plan["names"] and not merged:
                log("   [llm] 判定为全量，跑完整名单")
        else:
            log("   [llm] 解析失败，原句按逗号当昵称处理")
            args.names = args.q

    explicit = []
    if args.uids:
        want = {u.strip() for u in args.uids.split(",") if u.strip()}
        for a in accounts:
            if str(a["uid"]) in want:
                explicit.append(a)
                want.discard(str(a["uid"]))
        for u in sorted(want):
            explicit.append({"label": u, "group": "临时", "nick": u, "uid": u})
    if args.names or args.uids:
        accounts = explicit  # 指定了目标就只抓这些；names 在会话内解析后追加

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
            for raw in [n.strip() for n in args.names.split(",") if n.strip()]:
                hit = find_account(raw, cfg["accounts"])
                if hit:
                    accounts.append(hit)
                    log(f"   名单命中 {raw} -> {hit['label']} (uid {hit['uid']})")
                    continue

                # ① 用户直接给了 uid 或主页链接：最可靠，不经过任何搜索
                direct = uid_from_text(raw)
                if direct:
                    verify_and_add(sess, direct, "" if raw.isdigit() else raw,
                                   accounts, log, hint=" [直接输入]", trust=True)
                    continue

                # ② 模型联网确认「微博上的显示名」（简称/外号 -> 全名，提高搜索命中）
                #    注意顺序：完整昵称必须排在原始词前面。
                #    用户输入常常是两三个字的中文简称（「琴子」），拿它直接搜会命中
                #    「岛谷琴子」「XX琴子」这类同名路人；微博的用户搜索又是按完整昵称匹配的，
                #    只有带团名后缀的全名才可能搜到本人。
                variants = []
                try:
                    variants += llm_plan.find_names(raw, log=log)
                except Exception as e:
                    log(f"   [llm-find] 异常: {type(e).__name__}: {e}")
                variants.append(raw)

                # ③ 拿显示名回微博实查 uid（get_json 已带页面内 fetch 降级，绕 WAF 432）
                hit, search_err = None, None
                for name in variants:
                    try:
                        hit = sess.resolve_uid(name, expect=raw)
                    except wb.WbRequestError as e:
                        search_err = e
                        log(f"   搜索 {name} 失败：{e}")
                        break
                    if hit:
                        log(f"   搜索命中 {name} -> {hit[0]} ({hit[1]})")
                        break
                if hit:
                    accounts.append({"label": hit[0], "group": "临时",
                                     "nick": hit[0], "uid": hit[1]})
                    continue
                if search_err:
                    log(f"   ✗ {raw} 搜索通道不可用，跳过（可改用主页链接）")
                else:
                    log(f"   ✗ {raw} 搜不到，跳过（可改用主页链接）")

            if not accounts:
                sys.exit("没有任何账号解析成功，本次不写文件（保留旧索引）。"
                         "请确认 WB_COOKIE 有效，或直接粘主页链接 / 纯数字 uid")

        # 粘链接 / 直填 uid 时 label 是数字，取主页昵称替换，
        # 否则分类 chips 会显示成一串数字
        for a in accounts:
            lab = str(a.get("label") or "")
            if lab.isdigit() or lab == str(a.get("uid")):
                info = sess.profile(str(a["uid"]))
                if info and info.get("nick"):
                    a["label"] = a["nick"] = info["nick"]
                    log(f"   uid {a['uid']} -> {info['nick']}"
                        f"{('（' + info['verified'] + '）') if info.get('verified') else ''}")
                elif lab.isdigit():
                    log(f"   提示：uid {a['uid']} 取不到昵称，分类会显示成数字")
        seen_uid, uniq = set(), []
        for a in accounts:
            if str(a["uid"]) in seen_uid:
                continue
            seen_uid.add(str(a["uid"]))
            uniq.append(a)
        accounts = uniq
        log("== 目标: " + ", ".join(f"{a['label']}({a['uid']})" for a in accounts))

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
