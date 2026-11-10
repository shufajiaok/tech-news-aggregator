"""自然语言 -> 抓取参数。

走 OpenAI 兼容的 chat/completions（默认阿里云百炼 qwen-plus）。
Key 只从 Actions Secret 读，不进浏览器、不打日志。

输入的典型句子：
    "白泠最近的图"
    "把 Astoria官博 和 天枢官博 都抓一遍，多翻几页"
    "小卡、石榴，只要 16:9 的"（比例/体积由脚本自己筛，模型不用管）

输出统一成：
    {"names": [...], "uids": [...], "pages": 6}
"""

import json
import os

import requests

DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"

SYSTEM = """你是一个抓取任务参数解析器。把用户的中文自然语言请求，转成 JSON 参数。

只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。字段：
- names: 字符串数组，要抓的微博账号昵称。若用户说的是已知名单里的账号，必须原样使用名单中的写法。
- uids: 字符串数组，用户直接给出数字 uid 或主页链接（weibo.com/u/123456、m.weibo.cn/u/123456）时填这里，只填链接里的数字部分。
- pages: 整数，翻页页数。用户说"多翻几页""全部""历史"给 12，说"只看最新""最近的"给 2，没提就给 6。

规则：
- 不要臆造账号。用户提到但名单里没有的名字，按用户原话填进 names。
- 不要把昵称自己转换成 uid：你无法凭记忆知道 uid，转换由后续联网搜索完成。
- 用户说"全部账号""都抓"时，names 和 uids 都返回空数组（表示跑完整名单）。
- 无法判断就返回 {"names": [], "uids": [], "pages": 6}。"""

FIND_SYSTEM = """你是微博账号检索助手。请用联网搜索，确认用户说的昵称/外号对应哪个微博账号。

只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块：
{"names": ["微博显示名1", "微博显示名2"]}

规则：
- 只给「显示名」，不要给 uid、不要给任何数字 ID。后台会拿这些名字回微博实查 uid。
- 最多 3 个，按可能性从高到低排列。同名账号全部列出。
- 显示名要写成微博上真实出现的形态，例如简称「白泠」可能对应「白泠Mio-StarHoney」，
  外号「鼠条」可能对应「鼠条Kyuko-Astoria」。
- 一次检索都没命中任何账号时返回 {"names": []}。"""


def _extract_json(text):
    """模型偶尔会带 ```json 包裹或前后废话，截取最外层花括号。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(s[i:j + 1])
    except Exception:
        return None


def resolve(query, known_nicks, log=print):
    """把 query 解析成参数；拿不到有效结果时返回 None。"""
    key = (os.environ.get("WB_LLM_KEY") or "").strip()
    if not key:
        log("   [llm] 未配置 WB_LLM_KEY，跳过自然语言解析")
        return None
    base = (os.environ.get("WB_LLM_BASE_URL") or DEFAULT_BASE).strip().rstrip("/")
    model = (os.environ.get("WB_LLM_MODEL") or DEFAULT_MODEL).strip()

    user = f"已知账号名单（可作为 names 的合法取值）：\n{json.dumps(known_nicks, ensure_ascii=False)}\n\n用户请求：{query}"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": 400,
    }
    s = requests.Session()
    s.trust_env = False  # 本机有注入代理，直连才通
    try:
        r = s.post(base + "/chat/completions",
                   headers={"Authorization": "Bearer " + key,
                            "Content-Type": "application/json"},
                   json=payload, timeout=60)
    except Exception as e:
        log(f"   [llm] 请求失败: {type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        log(f"   [llm] HTTP {r.status_code}: {r.text[:160]}")
        return None
    try:
        text = r.json()["choices"][0]["message"]["content"]
    except Exception:
        log("   [llm] 响应结构异常")
        return None

    plan = _extract_json(text)
    if not isinstance(plan, dict):
        log(f"   [llm] 无法解析输出: {text[:120]}")
        return None

    names = [str(x).strip() for x in (plan.get("names") or []) if str(x).strip()]
    uids = [str(x).strip() for x in (plan.get("uids") or []) if str(x).strip().isdigit()]
    try:
        pages = int(plan.get("pages") or 6)
    except Exception:
        pages = 6
    pages = max(1, min(pages, 20))
    log(f"   [llm] {model} -> names={names} uids={uids} pages={pages}")
    return {"names": names, "uids": uids, "pages": pages, "raw": text[:200]}


def _post_chat(payload, log, tag):
    """发一次 chat/completions，返回正文文本；失败返回 None。"""
    key = (os.environ.get("WB_LLM_KEY") or "").strip()
    if not key:
        log(f"   [{tag}] 未配置 WB_LLM_KEY")
        return None
    base = (os.environ.get("WB_LLM_BASE_URL") or DEFAULT_BASE).strip().rstrip("/")
    s = requests.Session()
    s.trust_env = False  # 本机有注入代理，直连才通
    try:
        r = s.post(base + "/chat/completions",
                   headers={"Authorization": "Bearer " + key,
                            "Content-Type": "application/json"},
                   json=payload, timeout=90)
    except Exception as e:
        log(f"   [{tag}] 请求失败: {type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        log(f"   [{tag}] HTTP {r.status_code}: {r.text[:160]}")
        return None
    try:
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        log(f"   [{tag}] 响应结构异常")
        return None


def _native_search_url(base):
    """从兼容模式 base 推出 DashScope 原生 endpoint。

    实测：OpenAI 兼容模式（/compatible-mode/v1）会静默忽略 enable_search，
    响应里没有 search_info，模型退回凭记忆编造；只有原生接口真的联网。
    """
    b = (base or "").rstrip("/")
    if "/compatible-mode" in b:
        return b.split("/compatible-mode")[0] + (
            "/api/v1/services/aigc/text-generation/generation")
    return ""


def _post_native_search(payload, log, tag):
    """走 DashScope 原生接口（联网搜索只在这里生效），返回正文文本。"""
    key = (os.environ.get("WB_LLM_KEY") or "").strip()
    if not key:
        log(f"   [{tag}] 未配置 WB_LLM_KEY")
        return None
    base = (os.environ.get("WB_LLM_BASE_URL") or DEFAULT_BASE).strip().rstrip("/")
    url = _native_search_url(base)
    if not url:
        log(f"   [{tag}] 当前 WB_LLM_BASE_URL 不是 DashScope 兼容模式，无法启用联网搜索")
        return None
    s = requests.Session()
    s.trust_env = False
    try:
        r = s.post(url,
                   headers={"Authorization": "Bearer " + key,
                            "Content-Type": "application/json"},
                   json=payload, timeout=120)
    except Exception as e:
        log(f"   [{tag}] 请求失败: {type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        log(f"   [{tag}] HTTP {r.status_code}: {r.text[:200]}")
        return None
    try:
        d = r.json()
        text = d["output"]["choices"][0]["message"]["content"]
        hits = ((d.get("usage") or {}).get("plugins") or {}).get("search") or {}
        log(f"   [{tag}] 联网命中 {hits.get('count', '?')} 条（strategy={hits.get('strategy', '-')}）")
        return text
    except Exception:
        log(f"   [{tag}] 响应结构异常: {r.text[:160]}")
        return None


def find_names(term, log=print):
    """让模型联网确认「这个昵称在微博上叫什么」，返回显示名候选。

    为什么不返回 uid：实测联网搜索只回摘要文本，里面不含数字 uid，
    模型会凭空编一个（同一个名字两次调用给出不同 uid，且与真实值不符）。
    所以 uid 一律由调用方拿这些显示名回微博实查。
    """
    model = (os.environ.get("WB_LLM_MODEL") or DEFAULT_MODEL).strip()
    payload = {
        "model": model,
        "input": {"messages": [
            {"role": "system", "content": FIND_SYSTEM},
            {"role": "user", "content": f"「{term}」在微博上的显示名是什么？"},
        ]},
        "parameters": {"enable_search": True, "result_format": "message",
                       "temperature": 0, "max_tokens": 500},
    }
    text = _post_native_search(payload, log, "llm-find")
    if not text:
        return []
    plan = _extract_json(text)
    if not isinstance(plan, dict):
        log(f"   [llm-find] 无法解析输出: {text[:120]}")
        return []
    raw = plan.get("names")
    if not isinstance(raw, list):
        raw = [plan.get("nick")] if plan.get("nick") else []
    out, seen = [], {(term or "").strip().lower()}
    for n in raw[:4]:
        s = str(n or "").strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
    if out:
        log(f"   [llm-find] 「{term}」-> {out}")
    else:
        log(f"   [llm-find] 「{term}」没有给出可用显示名")
    return out[:3]
