# -*- coding: utf-8 -*-
"""
知行读书 —— 最小可跑的 AI 深度导读应用

三步流程：结构化梗概 -> 自由问答 -> 主干自测 + 卡片式纠偏
核心理念：
  1. 不接 PDF：靠大模型预训练知识 + DuckDuckGo 联网搜索核对（压幻觉）
  2. 梗概是地图，自测是"指着地图复述"，问题从主干骨架出发，不拆碎
  3. 每一步的反馈卡片落盘到 data/ 目录，可随时回顾
"""

import os
import json
import re
import time
import inspect
import asyncio
import difflib
import shutil
import subprocess
import threading
import io
import uuid
import zipfile
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, send_file

# 可选依赖：只有旧版界面（static/index.html）的 DeepSeek 流程需要。
# 新版流程（static/v2.html + OpenCode skill）不使用这些包，缺失时不影响启动。
try:
    from dotenv import load_dotenv
    load_dotenv()
    from openai import OpenAI
    HAS_LEGACY_DEPS = True
except ImportError:
    HAS_LEGACY_DEPS = False

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
REVIEWS_DIR = BASE_DIR / "reviews"
REVIEWS_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="")

# ------------------------------------------------ 配置 ------------------------------------------------
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()


# ------------------------------------------------ LLM / 搜索 ------------------------------------------------
def get_client():
    if not HAS_LEGACY_DEPS:
        raise RuntimeError("旧版界面缺少依赖：请运行 pip install openai python-dotenv duckduckgo-search（新流程不需要）")
    if not DEEPSEEK_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY（.env）")
    return OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_BASE)


def llm_chat(messages, json_mode=False, temperature=0.6):
    """调用 DeepSeek，返回文本。json_mode 时尽力返回 JSON 字符串。"""
    client = get_client()
    kwargs = dict(model=DEEPSEEK_MODEL, messages=messages, temperature=temperature)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content


def web_search(query, max_results=5):
    """DuckDuckGo 搜索，返回 [{title, snippet}]，失败返回空列表（不阻断流程）。"""
    try:
        from duckduckgo_search import DDGS
        ddgs = DDGS()
        res = ddgs.text(query, max_results=max_results, safesearch="moderate")
        if inspect.iscoroutine(res):
            res = asyncio.run(res)
        out = []
        for r in res:
            out.append({
                "title": (r.get("title") or "").strip(),
                "snippet": (r.get("body") or r.get("snippet") or "").strip(),
            })
        return [x for x in out if x["snippet"] or x["title"]]
    except Exception:
        return []


def search_book(title, author=""):
    """对书名/作者/目录做一次搜索核对，合并多条查询结果。"""
    queries = [f"{title} 内容简介 核心观点"]
    if author:
        queries.append(f"{title} {author} 书籍 目录")
    else:
        queries.append(f"{title} 书籍 目录 章节目录")
    seen, merged = set(), []
    for q in queries:
        for r in web_search(q, max_results=4):
            key = (r["title"] + r["snippet"])[:80]
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)
        if len(merged) >= 10:
            break
    return merged[:10]


# ------------------------------------------------ 数据存储 ------------------------------------------------
def new_session_id(title):
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title.strip()).strip("-") or "book"
    return f"{slug[:30]}-{int(time.time())}"


def session_path(sid):
    return DATA_DIR / f"{sid}.json"


def load_session(sid):
    p = session_path(sid)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_session(s):
    p = session_path(s["id"])
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
    # 同步导出回顾 markdown
    write_review_md(s)


def load_all_sessions():
    items = []
    for p in sorted(DATA_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(p, "r", encoding="utf-8") as f:
                s = json.load(f)
            items.append({
                "id": s.get("id"),
                "title": s.get("title"),
                "author": s.get("author", ""),
                "stage": s.get("stage"),
                "created_at": s.get("created_at"),
            })
        except Exception:
            continue
    return items


def _safe_name(title):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "-", title.strip()).strip("-") or "book"


def write_review_md(s):
    """把梗概 + 反馈卡片导出成一份 markdown 作为外部备份（卡片数据本体始终以 JSON 存于 data/）。"""
    title = s.get("title", "")
    fname = _safe_name(title)
    lines = []
    lines.append(f"# {title}  ·  学习回顾\n")
    lines.append(f"- 学习时间：{s.get('created_at', '')}")
    if s.get("author"):
        lines.append(f"- 作者：{s['author']}")
    lines.append("")
    if s.get("overview"):
        lines.append("## 结构化梗概\n")
        lines.append(s["overview"].strip())
        lines.append("")
    cards = s.get("quiz_cards", [])
    if cards:
        lines.append("## 反馈卡片\n")
        for c in cards:
            lines.append(f"### 卡片 {c.get('qid')}：{c.get('title', '')}\n")
            if c.get("verdict"):
                lines.append(f"> {c['verdict']}\n")
            for e in c.get("entries", []):
                stat = e.get("status")
                icon = {"done": "✅", "partial": "🟡", "missed": "❌", "wrong": "⚠️"}.get(stat, "•")
                lines.append(f"- {icon} {e.get('text', '')}")
            lines.append("")
    if s.get("summary"):
        lines.append("## 总结评估\n")
        lines.append(s["summary"].strip())
        lines.append("")
    try:
        (REVIEWS_DIR / f"{fname}.md").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


# ------------------------------------------------ 提取 JSON ------------------------------------------------
def extract_json(text):
    """尽力从模型输出里把 JSON 对象抠出来。"""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


# ------------------------------------------------ 核心 Prompt ------------------------------------------------
def _background_note(background):
    return f"\n\n用户的使用背景/目标：{background}" if background else ""


OVERVIEW_SYS = (
    "你是一位擅长深度导读的导师，帮助用户快速吃透一本书的核心思想。"
    "你只输出高质量的结构化梗概，语言精炼，绝不堆砌原文，也不写目录翻译。"
)


def build_overview_prompt(title, author, background, search_results):
    snippet_block = ""
    if search_results:
        items = []
        for r in search_results:
            items.append(f"-【{r['title']}】{r['snippet']}")
        snippet_block = "\n".join(items[:10])
    else:
        snippet_block = "（本次未能联网拿到资料，请以你的预训练知识为准，并对不确定处谨慎说明）"

    user = f"""用户想快速吃透这本书：

书名：《{title}》{('，作者：' + author) if author else ''}
{_background_note(background)}

先联网核对到的资料如下（仅用于校准你的记忆，不能照抄，也不需要全文）：
{snippet_block}

请综合你的预训练知识 + 上述核对资料，输出一份「结构化梗概」。
梗概不是内容摘要，必须逐层回答以下五个层次：

1. 这本书在跟谁吵架：作者在反对什么主流观点/方法论/范式？（理解一本书的"位置"比内容更重要）
2. 前因后果：为什么写这本书？当时学科处于什么状态？什么事件/矛盾触发了写作？（建立因果链）
3. 核心概念及关系：关键概念有哪些，它们之间的逻辑关系是什么？（A 产生 B，B 需要 C 才能操作，C 遇到 D 会受阻——用箭头/因果表述，不要并列罗列）
4. 与其他人/书的对话：这本书与同领域其他重要著作是什么关系？（支持/反对/补充/不同角度）
5. 论述题答题骨架：如果考试问"你怎么看这本书"，能用它论证什么立场？核心论点怎么组织？

格式要求：
- 概念关系用「箭头 / 因果」表述，不用列表平铺
- 结尾必须单独列一节「如果考试遇到这本书相关的题，这几个点必须踩到」
- 用 Markdown 输出
"""
    return [{"role": "system", "content": OVERVIEW_SYS}, {"role": "user", "content": user}]


QA_SYS = (
    "你是一位正在带用户精读一本书的导师。用户已经拿到这本书的结构化梗概，现在在自由提问。"
    "讲解时始终回到梗概的框架里，并在回答末尾标注：「这个点属于主干」或「这个点属于分支」，"
    "帮用户建立层次感。用户可能问细枝末节，也可能问延伸发散，都接住。"
)

QUIZ_SYS = (
    "你是一位严格的导师，负责从一本书的主干骨架上出「自测题」。"
    "题目必须切得准：答出来说明真懂了，答不出来说明这个模块没吃透。"
)


def build_quiz_gen_prompt(session):
    title = session["title"]
    author = session.get("author", "")
    overview = session.get("overview", "")
    user = f"""这是用户正在学习的书：《{title}》{('，作者：' + author) if author else ''}

下面是已经核对生成的「结构化梗概」：
---
{overview[:6000]}
---

请从梗概的「主干骨架」出发，出 4~6 道核心模块自测题（不要拆碎成零散知识点，也不要偏枝节）。

出题标准：
1. 每道题对应书的一个核心模块（不是零散知识点）
2. 题目应能启发用户调用梗概中的逻辑线去组织答案，而不是凭空回忆
3. 每道题内部附一份「要点清单」，3~6 个要点（这是你内部持有的评分标准，不会展示给用户）

严格只输出如下 JSON（不要 markdown 代码块，不要多余文字）：
{{
  "questions": [
    {{
      "qid": 1,
      "title": "模块标题",
      "question": "问题正文",
      "points": ["要点1", "要点2", "要点3"]
    }}
  ]
}}"""
    return [{"role": "system", "content": QUIZ_SYS}, {"role": "user", "content": user}]


def build_grade_prompt(session, question, points, answer):
    title = session["title"]
    user = f"""你是《{title}》的自测批改导师。现在有一道题、它的内部要点清单、以及用户的回答。

【题目】{question['title']}
【问题】{question['question']}

【要点清单】（每项都要评）
{json.dumps(points, ensure_ascii=False, indent=2)}

【用户回答】
{answer}

请按以下固定格式，对这一题输出反馈卡片。每个要点逐条判断，output 为 JSON：

判断规则（严格执行）：
- 用户完整答到：status="done"，text 只写该要点，绝不画蛇添足补充
- 用户答了但漏了关键细节：status="partial"，text 写"要点 + 补一个：<只补缺口>"
- 用户完全没答到：status="missed"，text 写"未答到。关键词：<关键词/短句>"
- 用户答错了：status="wrong"，text 写"注意：<用户错误内容>是错的，正确的是<正确内容>"

不要长篇大论，不要反过来把补充的要点又变成新问题问用户。

严格只输出如下 JSON（不要 markdown 代码块，不要多余文字）：
{{
  "entries": [
    {{"status": "done", "text": "..."}},
    {{"status": "partial", "text": "..."}},
    {{"status": "missed", "text": "..."}},
    {{"status": "wrong", "text": "..."}}
  ],
  "verdict": "一句话总评"
}}

要点数量必须与要点清单一致，逐条对应。"""
    return [{"role": "system", "content": QUIZ_SYS}, {"role": "user", "content": user}]


SUMMARY_SYS = "你是一位导师，负责在学习结束时给出总结性评估。"


def build_summary_prompt(session):
    title = session["title"]
    qa = session.get("qa", [])
    cards = session.get("quiz_cards", [])
    done_count = sum(1 for c in cards for e in c.get("entries", []) if e.get("status") in ("done", "partial"))
    total_count = sum(len(c.get("entries", [])) for c in cards)
    user = f"""《{title}》的自测已全部完成。请输出一份简短的总结评估：
- 用户掌握了哪些模块
- 哪些模块仍需回顾
- 建议下一步做什么

参考数据：反馈卡片共 {len(cards)} 题，{done_count}/{total_count} 个要点被掌握。

用 Markdown 输出，控制在 200 字以内。"""
    return [{"role": "system", "content": SUMMARY_SYS}, {"role": "user", "content": user}]


# ------------------------------------------------ 路由 ------------------------------------------------
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/config")
def api_config():
    return jsonify({"ok": True, "has_key": bool(DEEPSEEK_KEY), "model": DEEPSEEK_MODEL})


@app.route("/api/studies")
def api_studies():
    return jsonify({"ok": True, "items": load_all_sessions()})


@app.route("/api/study/<sid>")
def api_get_study(sid):
    s = load_session(sid)
    if not s:
        return jsonify({"ok": False, "error": "会话不存在"}), 404
    return jsonify({"ok": True, "session": s})


@app.route("/api/study", methods=["POST"])
def api_new_study():
    data = request.get_json(force=True, silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "请填写书名"}), 400
    author = (data.get("author") or "").strip()
    background = (data.get("background") or "").strip()

    sid = new_session_id(title)
    s = {
        "id": sid,
        "title": title,
        "author": author,
        "background": background,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": "overview",
        "overview": "",
        "qa": [],
        "quiz_questions": [],
        "quiz_index": 0,
        "quiz_cards": [],
        "summary": "",
    }
    save_session(s)

    # 先搜索核对，再生成梗概
    try:
        search_results = search_book(title, author)
        messages = build_overview_prompt(title, author, background, search_results)
        overview = llm_chat(messages, temperature=0.5)
        s["overview"] = overview
        s["stage"] = "qa"
        save_session(s)
        return jsonify({"ok": True, "session": s, "search_count": len(search_results)})
    except Exception as e:
        s["stage"] = "overview"
        save_session(s)
        return jsonify({"ok": False, "error": f"生成梗概失败：{e}"}), 500


@app.route("/api/study/<sid>/ask", methods=["POST"])
def api_ask(sid):
    s = load_session(sid)
    if not s:
        return jsonify({"ok": False, "error": "会话不存在"}), 404
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "请输入问题"}), 400

    overview = s.get("overview", "")
    messages = [
        {"role": "system", "content": QA_SYS},
        {"role": "user", "content": f"这本书《{s['title']}》的梗概如下：\n---\n{overview[:5000]}\n---\n\n用户提问：{question}\n请讲解，并在末尾标注这是主干还是分支。"},
    ]
    try:
        answer = llm_chat(messages, temperature=0.6)
        s["qa"].append({"q": question, "a": answer, "ts": time.strftime("%H:%M:%S")})
        save_session(s)
        return jsonify({"ok": True, "answer": answer})
    except Exception as e:
        return jsonify({"ok": False, "error": f"回答失败：{e}"}), 500


@app.route("/api/study/<sid>/quiz", methods=["POST"])
def api_quiz(sid):
    s = load_session(sid)
    if not s:
        return jsonify({"ok": False, "error": "会话不存在"}), 404
    if not s.get("overview"):
        return jsonify({"ok": False, "error": "请先生成梗概"}), 400

    try:
        messages = build_quiz_gen_prompt(s)
        raw = llm_chat(messages, json_mode=True, temperature=0.6)
        parsed = extract_json(raw)
        questions = parsed.get("questions", []) if parsed else []
        if not questions:
            return jsonify({"ok": False, "error": "出题解析失败，请重试"}), 500
        s["quiz_questions"] = questions
        s["quiz_index"] = 0
        s["stage"] = "quiz"
        save_session(s)
        return jsonify({"ok": True, "questions": questions})
    except Exception as e:
        return jsonify({"ok": False, "error": f"出题失败：{e}"}), 500


@app.route("/api/study/<sid>/answer", methods=["POST"])
def api_answer(sid):
    s = load_session(sid)
    if not s:
        return jsonify({"ok": False, "error": "会话不存在"}), 404
    questions = s.get("quiz_questions", [])
    idx = s.get("quiz_index", 0)
    if idx >= len(questions):
        return jsonify({"ok": False, "error": "已经答完所有题目"}), 400
    data = request.get_json(force=True, silent=True) or {}
    answer = (data.get("answer") or "").strip()
    if not answer:
        return jsonify({"ok": False, "error": "请先写下你的回答"}), 400

    question = questions[idx]
    try:
        messages = build_grade_prompt(s, question, question.get("points", []), answer)
        raw = llm_chat(messages, json_mode=True, temperature=0.3)
        parsed = extract_json(raw)
        entries = parsed.get("entries", []) if parsed else []
        verdict = (parsed or {}).get("verdict", "")
        card = {"qid": question.get("qid", idx + 1), "title": question.get("title"), "entries": entries, "verdict": verdict}
        s["quiz_cards"].append(card)
        s["quiz_index"] = idx + 1
        finished = s["quiz_index"] >= len(questions)
        if finished:
            s["stage"] = "done"
            try:
                s["summary"] = llm_chat(build_summary_prompt(s), temperature=0.5)
            except Exception:
                s["summary"] = ""
        save_session(s)
        return jsonify({"ok": True, "card": card, "next_index": s["quiz_index"], "finished": finished, "summary": s.get("summary", "")})
    except Exception as e:
        return jsonify({"ok": False, "error": f"批改失败：{e}"}), 500


# ------------------------------------------------ 用户配置 config.json ------------------------------------------------
# 可选：复制 config.json.example 为 config.json 后修改，无需动代码。
TUTOR_CONFIG_FILE = BASE_DIR / "config.json"


def _load_user_config():
    if TUTOR_CONFIG_FILE.exists():
        try:
            data = json.loads(TUTOR_CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


USER_CONFIG = _load_user_config()

# ------------------------------------------------ OpenCode book-tutor 代理 ------------------------------------------------
# 通过 OpenCode 桌面版自带的 CLI（opencode api）调用本机服务，由 skill 完成导读流程。
OPENCODE_CLI = (
    USER_CONFIG.get("cli")
    or os.getenv("OPENCODE_CLI", "")
    or os.path.join(os.getenv("LOCALAPPDATA", ""), "Programs", "@opencodedesktop", "resources", "opencode-cli.exe")
)
OPENCODE_CLI = str(OPENCODE_CLI).strip()
if not os.path.exists(OPENCODE_CLI):
    OPENCODE_CLI = "opencode"

# ---- 可用模型自动发现：每次启动识别“真正登录过”的运营商，剔除用不了的（如 alibaba）----
# opencode auth list 里，credential 型连接 = 用户主动保存过凭据（可靠可用）；
# env 型连接（如 alibaba 的 DASHSCOPE_API_KEY）多半是残留旧 key，不可靠，不计入“可用”。
_KNOWN_AUTHED = {"opencode", "deepseek", "volcengine", "volcengine-coding-plan"}  # 检测失败时的兜底
AUTHED_PROVIDERS = set()   # 已认证（有保存凭据）的 provider
USABLE_MODELS = []         # 可用模型 [{provider,id,name,free,input_cost}]
_MODELS_LOCK = threading.Lock()


def _authed_providers():
    authed = set()
    ok = False
    try:
        p = subprocess.run([OPENCODE_CLI, "auth", "list", "--format", "json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if p.returncode == 0 and p.stdout.strip():
            ok = True
            for prov in json.loads(p.stdout):
                pid = (prov.get("id") or "").strip()
                if pid and any(c.get("type") == "credential" for c in (prov.get("connections") or [])):
                    authed.add(pid)
    except Exception as e:
        print(f"[models] 读取认证 provider 失败: {e}")
    return authed if ok else _KNOWN_AUTHED


def _fetch_usable_models(authed):
    items = []
    try:
        cmd = [OPENCODE_CLI, "api", "get", f"/api/model?location[directory]={BASE_DIR}"]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if p.returncode != 0:
            raise RuntimeError((p.stdout + p.stderr).strip() or "读取模型列表失败")
        resp = json.loads(p.stdout) or {}
        for m in resp.get("data", []):
            if m.get("status") and m.get("status") != "active":
                continue
            provider = (m.get("providerID") or "").strip()
            mid = (m.get("modelID") or "").strip()
            if not provider or not mid or provider not in authed:
                continue
            if (m.get("capabilities") or {}).get("tools") is False:
                continue   # 导师流程需要调用工具（写文件/搜索），不支持工具的模型跳过
            cost = m.get("cost") or []
            c0 = cost[0] if cost else {}
            try:
                ic, oc = float(c0.get("input") or 0), float(c0.get("output") or 0)
            except Exception:
                ic = oc = 0.0
            items.append({"provider": provider, "id": mid,
                          "name": (m.get("name") or mid).strip(),
                          "free": ic == 0 and oc == 0, "input_cost": ic})
    except Exception as e:
        print(f"[models] 拉取模型列表失败: {e}")
    return items


def _model_sort_key(m):
    return (not m["free"], m["input_cost"], m["provider"], m["name"].lower())


def refresh_models():
    """重新检测可用 provider 并拉取对应模型，返回 (authed_providers, usable_models)。"""
    global AUTHED_PROVIDERS, USABLE_MODELS
    with _MODELS_LOCK:
        authed = _authed_providers()
        models = _fetch_usable_models(authed)
        AUTHED_PROVIDERS = authed
        USABLE_MODELS = models
        return authed, models


refresh_models()

TUTOR_TITLE_PREFIX = "[知行读书] "
# 导师模型回退链：按顺序使用，遇到额度/限流错误就自动切到下一个并重试
# 优先级：config.json 的 "models"（"provider/model" 字符串）> 环境变量 > 内置默认
TUTOR_MODEL_CHAIN = []
_cfg_models = USER_CONFIG.get("models")
if isinstance(_cfg_models, list):
    for _m in _cfg_models:
        if isinstance(_m, str) and "/" in _m:
            _p, _i = _m.split("/", 1)
            _pair = (_p.strip(), _i.strip())
        elif isinstance(_m, (list, tuple)) and len(_m) == 2:
            _pair = (str(_m[0]).strip(), str(_m[1]).strip())
        else:
            continue
        if _pair[0] and _pair[1] and _pair not in TUTOR_MODEL_CHAIN:
            TUTOR_MODEL_CHAIN.append(_pair)
for _pair in [
    (os.getenv("TUTOR_MODEL_PROVIDER", "opencode").strip(), os.getenv("TUTOR_MODEL_ID", "mimo-v2.6-flash-free").strip()),
    ("opencode", "flint-40b-free"),
    ("opencode", "beam-free"),
    ("opencode", "nemotron-3.5-super-vl-free"),
    ("opencode", "deepseek-v4.1-flash-free"),
    ("volcengine-coding-plan", "deepseek-v4-flash"),
    ("volcengine-coding-plan", "doubao-seed-evolving"),
]:
    if _pair[0] and _pair[1] and _pair not in TUTOR_MODEL_CHAIN:
        TUTOR_MODEL_CHAIN.append(_pair)
# 自动补上：把当前“能用了的运营商”里还没进链的模型追加进去（免费优先，再按价格从低到高）
_seen = set(TUTOR_MODEL_CHAIN)
for _m in sorted(USABLE_MODELS, key=_model_sort_key):
    _pair = (_m["provider"], _m["id"])
    if _pair not in _seen:
        TUTOR_MODEL_CHAIN.append(_pair)
        _seen.add(_pair)
if not TUTOR_MODEL_CHAIN:
    TUTOR_MODEL_CHAIN.append(("opencode", "mimo-v2.6-flash-free"))
TUTOR_MODEL_PROVIDER, TUTOR_MODEL_ID = TUTOR_MODEL_CHAIN[0]
TUTOR_MODEL_STATE = {}          # sid -> {"idx": 下一个候选序号, "tries": 已重试次数}
QUOTA_ERRORS = ("quota", "free-limit", "free usage limit", "usage limit", "rate limit",
                "insufficient", "overdue", "not activated", "429", "402")
# 判断会话状态时只看这几类消息；model-switched / agent-switched 等事件不算一轮对话
TURN_TYPES = ("user", "assistant", "idle")


def _parse_model_string(s):
    """把 'providerID/modelID' 解析成 (providerID, modelID)；格式不对返回 None。"""
    s = (s or "").strip()
    if not s or "/" not in s:
        return None
    p, i = s.split("/", 1)
    p, i = p.strip(), i.strip()
    if not p or not i or len(p) > 60 or len(i) > 100:
        return None
    return (p, i)


def oc_request(method, path, body=None):
    """通过 opencode api 调用 OpenCode 服务，所有请求都限定在本项目目录下。"""
    sep = "&" if "?" in path else "?"
    cmd = [OPENCODE_CLI, "api", method, f"{path}{sep}location[directory]={BASE_DIR}"]
    if body is not None:
        cmd += ["--data", json.dumps(body, ensure_ascii=False)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if p.returncode != 0:
        raise RuntimeError((p.stdout + p.stderr).strip() or f"OpenCode 调用失败：{path}")
    out = p.stdout.strip()
    return json.loads(out) if out else None


def tutor_view(raw_messages):
    """只保留用户消息和导师的文字回复，隐藏 skill 加载、工具调用等过程信息。

    设计要点（修复：模型故障时不再丢失用户的问题）：
    - 用户消息一律保留。若一轮以失败告终（额度/限流、模型不可用等）且没有导师回复，
      给该条用户消息打上 failed 标记，前端据此显示「未收到回复」，而不是删除它。
    - 若回退重发后又出现一条相同文本的用户消息，用新的替换旧的（避免重复显示两条）。
    """
    out = []
    pending_idx = None      # out 中「还没有回复的用户消息」的下标
    for m in raw_messages:
        t = m.get("type")
        if t == "user":
            text = m.get("text", "")
            # 回退重发会产生一条相同文本的新用户消息：替换旧的，避免重复
            if out and out[-1].get("role") == "user" and out[-1].get("text") == text:
                out[-1]["id"] = m.get("id")
                out[-1].pop("failed", None)
            else:
                out.append({"id": m.get("id"), "role": "user", "text": text})
            pending_idx = len(out) - 1
        elif t == "assistant":
            text = "".join(p.get("text", "") for p in m.get("content", []) if p.get("type") == "text")
            if text.strip():
                out.append({"id": m.get("id"), "role": "assistant", "text": text})
                pending_idx = None
        elif t == "idle":
            if pending_idx is not None and out[pending_idx].get("role") == "user":
                ok = m.get("outcome") in (None, "succeeded")
                if ok:
                    out[pending_idx].pop("failed", None)
                else:
                    out[pending_idx]["failed"] = True
            pending_idx = None
    return out


def _is_quota_error(err_text):
    t = (err_text or "").lower()
    return any(k in t for k in QUOTA_ERRORS)


def _is_retryable_error(err_text):
    """模型暂时不可用 / 路由 / 区域限制 / 认证失败等 provider 层面错误，都值得换模型重发。

    provider.auth（如「This model is not available in your country」）、provider.overloaded、
    provider.timeout 等都属于「这个模型/运营商当前用不了」，换下一个候选模型是正确的处理。
    只有真正的内容问题（模型已正常回复但内容不合规）才不应重发。
    """
    t = (err_text or "").lower()
    if "provider." in t:
        return True
    return any(k in t for k in ("no-route", "model unavailable", "model not found", "route",
                                "connection", "timeout", "timed out", "network", "503", "500",
                                "not available", "unavailable", "country", "region"))


def _session_turns(sid, limit=10):
    """取会话最近的对话轮次消息（倒序）。失败返回空列表。"""
    try:
        raw = oc_request("get", f"/api/session/{sid}/message?order=desc&limit={limit}") or {}
    except Exception:
        return []
    return [m for m in (raw.get("data") or []) if m.get("type") in TURN_TYPES]


def _session_hung_tool(sid, limit=6):
    """最新几轮里是否挂着 running 状态的工具调用（question 这类等待答复的调用会永远 running）。"""
    try:
        raw = oc_request("get", f"/api/session/{sid}/message?order=desc&limit={limit}") or {}
    except Exception:
        return False
    for m in (raw.get("data") or []):
        if m.get("type") != "assistant":
            continue
        for part in m.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "tool" \
                    and (part.get("state") or {}).get("status") == "running":
                return True
    return False


def _session_idle(sid):
    """会话是否空闲：还没有对话轮次，或最新轮次是 idle 事件。"""
    turns = _session_turns(sid)
    return (not turns) or turns[0].get("type") == "idle"


def _ensure_idle(sid, timeout=45):
    """等会话回到空闲；若发现有挂死的工具调用（running 超过 20 秒）就中断它。"""
    deadline = time.time() + timeout
    hung_since = None
    while time.time() < deadline:
        if _session_idle(sid):
            return True
        if _session_hung_tool(sid):
            if hung_since is None:
                hung_since = time.time()
            elif time.time() - hung_since > 20:
                try:
                    oc_request("post", f"/api/session/{sid}/interrupt")
                except Exception:
                    pass
                hung_since = None
        time.sleep(2)
    return _session_idle(sid)


def _prompt_until_delivered(sid, text, tries=3):
    """发送 prompt 并核实它真的写入了会话（OpenCode 在会话忙时会静默吞掉 prompt）。
    没查到就等空闲后重发。返回是否确认送达。"""
    want = text.strip()
    for _ in range(tries):
        try:
            oc_request("post", f"/api/session/{sid}/prompt", {"text": text})
        except Exception:
            pass
        deadline = time.time() + 10
        while time.time() < deadline:
            for m in _session_turns(sid, limit=10):
                if m.get("type") == "user" and (m.get("text") or "").strip() == want:
                    return True
            time.sleep(2)
        _ensure_idle(sid, timeout=15)
    return False


def _tutor_recover(sid, msgs=None):
    """会话因额度/限流或模型不可用失败时：换下一个候选模型并重发最后一条问题。返回是否已重发。

    若整条回退链都试过仍失败，进入冷却（give_up_until），避免每次轮询都把整条链
    重新扫一遍、反复重发同一个问题。
    """
    st = TUTOR_MODEL_STATE.setdefault(sid, {"idx": 0, "tries": 0})
    if time.time() < st.get("give_up_until", 0):
        return False
    if msgs is None:
        raw = oc_request("get", f"/api/session/{sid}/message?order=desc&limit=6") or {}
        msgs = raw.get("data", [])
    # 只看对话轮次消息，忽略 model-switched 等事件
    turns = [m for m in msgs if m.get("type") in TURN_TYPES]
    if not turns or turns[0].get("type") != "idle":
        return False
    if turns[0].get("outcome") == "succeeded":
        st["tries"] = 0
        st["failed"] = []   # 恢复后清空失败记录，等额度恢复后还能回到首选模型
        st["give_up_until"] = 0
        return False
    err = str(turns[0].get("error", ""))
    if not (_is_quota_error(err) or _is_retryable_error(err)):
        return False
    if st["tries"] >= len(TUTOR_MODEL_CHAIN):
        st["tries"] = 0
        st["give_up_until"] = time.time() + 60   # 整条链都试过了，冷却一分钟再重试
        return False
    last_user = next((m for m in msgs if m.get("type") == "user"), None)
    if not last_user or not (last_user.get("text") or "").strip():
        return False
    # 记录刚刚失败的那个模型（取该轮 assistant 消息上的模型信息）
    failed_now = None
    for m in msgs:
        if m.get("type") == "assistant" and m.get("model"):
            md = m["model"]
            failed_now = (md.get("providerID", ""), md.get("id", ""))
            break
    if failed_now:
        st.setdefault("failed", []).append(failed_now)
    # 在链上挑候选模型。
    # 关键：从「刚失败的模型在链中的位置之后」开始挑，而不是永远从链首挑。
    # 否则会话一旦失败，会从链首(免费模型)重新开始，把用户手动选好的模型（如 ark coding plan）拉回免费链。
    free_pairs = {(m["provider"], m["id"]) for m in USABLE_MODELS if m.get("free")}
    used = set(st.get("failed", []))
    skip_free = _is_quota_error(err)
    # 当前失败模型在链中的位置；找不到就从头开始
    start = 0
    if failed_now and failed_now in TUTOR_MODEL_CHAIN:
        start = TUTOR_MODEL_CHAIN.index(failed_now) + 1
    choice = None
    n = len(TUTOR_MODEL_CHAIN)
    for k in range(n):
        cand = TUTOR_MODEL_CHAIN[(start + k) % n]
        if cand in used:
            continue
        if skip_free and cand in free_pairs:
            continue
        choice = cand
        break
    if not choice and skip_free:
        # 没有非免费候选时退回原逻辑（允许免费模型）
        for k in range(n):
            cand = TUTOR_MODEL_CHAIN[(start + k) % n]
            if cand not in used:
                choice = cand
                break
    if not choice:
        st["tries"] = 0
        st["failed"] = []
        st["give_up_until"] = time.time() + 60
        return False
    prov, mod = choice
    st["idx"] = TUTOR_MODEL_CHAIN.index(choice)
    st["tries"] += 1
    st["give_up_until"] = 0
    try:
        oc_request("post", f"/api/session/{sid}/model", {"model": {"providerID": prov, "id": mod}})
        oc_request("post", f"/api/session/{sid}/prompt", {"text": last_user["text"]})
        print(f"[tutor] 会话 {sid} 当前模型不可用，已切换到 {prov}/{mod} 并重发问题")
        return True
    except Exception as e:
        print(f"[tutor] 会话 {sid} 切换模型失败: {e}")
        return False


# ------------------------------------------------ 导读队列：逐本生成 ------------------------------------------------
TUTOR_STATE_FILE = DATA_DIR / "tutor_queue.json"
TUTOR_LOCK = threading.Lock()
TUTOR_RETRY_LIMIT = 2
TUTOR_CONTINUE_PROMPT = "上一步因为模型服务中断而没有完成。请接着完成第一步：检查资料文件和梗概是否已经落盘，缺什么补什么，最后把梗概写入 reviews/ 并生成术语表，并把梗概正文完整输出在对话里。"


def _tutor_load():
    if TUTOR_STATE_FILE.exists():
        try:
            return json.loads(TUTOR_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"current": None, "queue": [], "failed": []}


def _tutor_save():
    TUTOR_STATE_FILE.write_text(json.dumps(TUTOR_STATE, ensure_ascii=False, indent=2), encoding="utf-8")


TUTOR_STATE = _tutor_load()


def _tutor_status(sid):
    """返回 (状态, 前面还有几本书)。"""
    with TUTOR_LOCK:
        cur = TUTOR_STATE["current"]
        if cur and cur["sid"] == sid:
            return "running", 0
        ahead = 1 if cur else 0
        for i, it in enumerate(TUTOR_STATE["queue"]):
            if it["sid"] == sid:
                return "queued", ahead + i
        if any(f["sid"] == sid for f in TUTOR_STATE["failed"]):
            return "failed", 0
    return "", 0


def _tutor_finish(ok):
    cur = TUTOR_STATE["current"]
    if not ok:
        TUTOR_STATE["failed"].append({"sid": cur["sid"], "title": cur["title"]})
    TUTOR_STATE["current"] = None
    _tutor_save()


def _tutor_tick():
    with TUTOR_LOCK:
        cur = TUTOR_STATE["current"]
        if cur is None:
            if not TUTOR_STATE["queue"]:
                return
            cur = TUTOR_STATE["queue"].pop(0)
            cur.update({"started_at": time.time(), "retries": 0})
            TUTOR_STATE["current"] = cur
            _tutor_save()
            try:
                oc_request("post", f"/api/experimental/session/{cur['sid']}/skill", {"id": "book-tutor"})
                # 技能注入可能触发一次"无书名"的空跑并挂死在提问工具上，先等它结束/掐掉，
                # 再发建书提示词，并核实提示词真的入账（OpenCode 会静默吞掉忙时的 prompt）。
                _ensure_idle(cur["sid"], timeout=45)
                if not _prompt_until_delivered(cur["sid"], cur["prompt"]):
                    raise RuntimeError("建书提示词未能送达会话")
            except Exception:
                _tutor_finish(False)
            return
        if time.time() - cur["started_at"] < 15:
            return
        raw = oc_request("get", f"/api/session/{cur['sid']}/message?order=desc&limit=20") or {}
        msgs = raw.get("data", [])
        turns = [m for m in msgs if m.get("type") in TURN_TYPES]
        if not turns or turns[0].get("type") != "idle":
            # 看门狗：assistant 轮次的工具调用挂起超过 6 分钟（如 question 等待答复），
            # 说明这一轮已经卡死，中断它让会话落回 idle，交给下面的重试逻辑续跑。
            if turns and turns[0].get("type") == "assistant" and _session_hung_tool(cur["sid"]):
                created = ((turns[0].get("time") or {}).get("created") or 0) / 1000.0
                if created and time.time() - created > 360:
                    try:
                        oc_request("post", f"/api/session/{cur['sid']}/interrupt")
                    except Exception:
                        pass
            return
        if any(p.stat().st_mtime >= cur["started_at"] for p in REVIEWS_DIR.glob("*.md")):
            _tutor_finish(True)
        elif cur["retries"] < TUTOR_RETRY_LIMIT:
            cur["retries"] += 1
            _tutor_save()
            if not _tutor_recover(cur["sid"]):
                oc_request("post", f"/api/session/{cur['sid']}/prompt",
                           {"text": TUTOR_CONTINUE_PROMPT + "\n（书目：《" + cur["title"] + "》）"})
        else:
            _tutor_finish(False)


def _tutor_worker():
    while True:
        try:
            _tutor_tick()
        except Exception:
            pass
        time.sleep(3)


@app.route("/api/tutor/books")
def api_tutor_books():
    try:
        resp = oc_request("get", "/api/session?limit=200&order=desc")
    except Exception as e:
        return jsonify({"ok": False, "error": f"连接 OpenCode 失败：{e}"}), 500
    items = []
    for s in (resp or {}).get("data", []):
        title = s.get("title") or ""
        if title.startswith(TUTOR_TITLE_PREFIX):
            st, ahead = _tutor_status(s.get("id"))
            items.append({"id": s.get("id"), "title": title[len(TUTOR_TITLE_PREFIX):], "created": (s.get("time") or {}).get("created"), "status": st, "ahead": ahead})
    return jsonify({"ok": True, "items": items})


@app.route("/api/models")
def api_models():
    """列出可用模型，供问答窗口的下拉框选择。

    只显示真正登录过（有保存凭据）的运营商的模型，免费模型排前面。
    每次请求都重新检测一次，用户新登录了某个运营商（如 deepseek）后刷新即可看到。
    """
    try:
        _authed, models = refresh_models()
    except Exception as e:
        return jsonify({"ok": False, "error": f"读取模型列表失败：{e}"}), 500
    items = [{"provider": m["provider"], "id": m["id"], "name": m["name"], "free": m["free"]}
             for m in sorted(models, key=_model_sort_key)]
    # 默认值优先取回退链链首；若链首已不在可用列表里，退到第一个免费模型
    default = f"{TUTOR_MODEL_PROVIDER}/{TUTOR_MODEL_ID}"
    if not any(i["provider"] + "/" + i["id"] == default for i in items):
        first_free = next((i for i in items if i["free"]), items[0] if items else None)
        default = (first_free["provider"] + "/" + first_free["id"]) if first_free else ""
    return jsonify({"ok": True, "items": items, "default": default})


@app.route("/api/tutor/<sid>/model", methods=["POST"])
def api_tutor_model(sid):
    """问答窗口里手动切换当前会话使用的模型。"""
    data = request.get_json(force=True, silent=True) or {}
    pair = _parse_model_string(data.get("model"))
    if not pair:
        return jsonify({"ok": False, "error": "模型格式应为 providerID/modelID"}), 400
    prov, mid = pair
    try:
        oc_request("post", f"/api/session/{sid}/model", {"model": {"providerID": prov, "id": mid}})
    except Exception as e:
        return jsonify({"ok": False, "error": f"切换模型失败：{e}"}), 500
    # 手动切换后清掉该会话的自动回退记录，之后额度出问题再从干净状态回退
    TUTOR_MODEL_STATE.pop(sid, None)
    return jsonify({"ok": True, "model": f"{prov}/{mid}"})


CITATION_TYPE = re.compile(r"\[[A-Z]{1,2}(?:/[A-Z]{1,2})?\]")   # [M] [J] [C] [D] [EB/OL] 等类型标识


def parse_citation(text):
    """识别 GB/T 7714 参考文献格式，提取书名和作者。
    返回 (title, author, is_citation)；不是引用格式时原样返回。"""
    s = (text or "").strip()
    if not s:
        return "", "", False
    s2 = re.sub(r"^\[\d+\]\s*", "", s)                 # 去掉开头的 [5]
    m = CITATION_TYPE.search(s2)
    if not m:
        return s, "", False
    head = s2[:m.start()].strip()
    if not head:
        return s2, "", True
    # 优先用「点号后紧跟中文」的位置分隔 作者.题名（兼容 John G. Proakis 这类带缩写点的作者）
    dot = None
    for i, ch in enumerate(head):
        if ch in ".．。" and re.match(r"[\u4e00-\u9fff]", head[i + 1:i + 2] or ""):
            dot = i
            break
    if dot is None:
        dots = [i for i, ch in enumerate(head) if ch in ".．"]
        dot = dots[-1] if dots else None
    if dot is None:
        title, author = head, ""
    else:
        author, title = head[:dot].strip(), head[dot + 1:].strip()
    title = title.strip("．。. ")
    if not title:
        return s2, author, True
    return title, author, True


@app.route("/api/tutor/start", methods=["POST"])
def api_tutor_start():
    data = request.get_json(force=True, silent=True) or {}
    raw_title = (data.get("title") or "").strip()
    if not raw_title:
        return jsonify({"ok": False, "error": "请填写书名"}), 400
    author = (data.get("author") or "").strip()
    background = (data.get("background") or "").strip()
    # 允许前端指定模型（"providerID/modelID"），不指定就用回退链首选
    model_pair = _parse_model_string(data.get("model"))
    prov, mid = model_pair if model_pair else (TUTOR_MODEL_PROVIDER, TUTOR_MODEL_ID)

    # 支持直接粘贴参考文献：自动解析出书名和作者
    title, parsed_author, is_citation = parse_citation(raw_title)
    if is_citation and parsed_author and not author:
        author = parsed_author
    if not title:
        return jsonify({"ok": False, "error": "无法从输入里识别出书名"}), 400
    if len(title) > 60:
        return jsonify({"ok": False, "error": f"识别出的书名太长（{len(title)} 字），请检查输入"}), 400

    prompt = f"请使用 book-tutor 技能帮我导读这本书。\n书名：《{title}》"
    if author:
        prompt += f"\n作者：{author}"
    if background:
        prompt += f"\n学习目标/背景：{background}"
    prompt += "\n\n现在开始第一步：若 data/sources/ 下还没有本书的资料文件，先按「资料库」流程收集摘要资料并落盘；然后生成结构化梗概，写入 reviews/ 目录；最后把梗概正文完整输出在对话里，供我直接阅读，执行报告只写一两行。"
    try:
        s = oc_request("post", "/api/session", {
            "title": TUTOR_TITLE_PREFIX + title,
            "location": {"directory": str(BASE_DIR)},
            "model": {"providerID": prov, "id": mid},
        })
        sid = s["data"]["id"]
    except Exception as e:
        return jsonify({"ok": False, "error": f"启动导读失败：{e}"}), 500
    with TUTOR_LOCK:
        TUTOR_STATE["queue"].append({"sid": sid, "title": title, "prompt": prompt})
        _tutor_save()
    _, ahead = _tutor_status(sid)
    return jsonify({"ok": True, "sid": sid, "title": title, "ahead": ahead})


@app.route("/api/tutor/<sid>/messages")
def api_tutor_messages(sid):
    try:
        resp = oc_request("get", f"/api/session/{sid}/message?order=desc&limit=200")
    except Exception as e:
        return jsonify({"ok": False, "error": f"读取消息失败：{e}"}), 500
    newest_first = (resp or {}).get("data", [])
    # 额度/限流失败时自动换模型重试
    try:
        _tutor_recover(sid, newest_first)
    except Exception:
        pass
    raw = list(reversed(newest_first))
    status, ahead = _tutor_status(sid)
    # 取最近一轮对话的状态（忽略 model-switched 这类事件消息）
    newest_turn = next((m for m in newest_first if m.get("type") in TURN_TYPES), None)
    busy = status in ("queued", "running") or (newest_turn is not None and newest_turn.get("type") != "idle")
    # 会话当前实际使用的模型（打开书时前端用它来对齐模型下拉框）
    cur_model = ""
    try:
        sinfo = oc_request("get", f"/api/session/{sid}") or {}
        sm = (sinfo.get("data") or {}).get("model") or {}
        if sm.get("providerID") and sm.get("id"):
            cur_model = f"{sm['providerID']}/{sm['id']}"
    except Exception:
        pass
    return jsonify({"ok": True, "messages": tutor_view(raw), "busy": busy, "status": status, "ahead": ahead, "model": cur_model})


@app.route("/api/tutor/<sid>/send", methods=["POST"])
def api_tutor_send(sid):
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "请输入内容"}), 400
    try:
        oc_request("post", f"/api/session/{sid}/prompt", {"text": text})
    except Exception as e:
        return jsonify({"ok": False, "error": f"发送失败：{e}"}), 500
    return jsonify({"ok": True})


@app.route("/api/reviews")
def api_reviews():
    """回顾列表：按「主题（书）」分组返回。一个主题 = 梗概/总结文件 + 可选的查漏表文件。"""
    topics = {}
    for p in REVIEWS_DIR.glob("*.md"):
        stem = p.stem
        if stem.startswith("查漏表-"):
            topic, kind = stem[len("查漏表-"):], "checklist"
        else:
            topic, kind = stem, "main"
        t = topics.setdefault(topic, {"name": topic, "mtime": 0.0, "files": []})
        t["files"].append({"name": p.name, "kind": kind, "mtime": p.stat().st_mtime})
        t["mtime"] = max(t["mtime"], p.stat().st_mtime)
    items = sorted(topics.values(), key=lambda t: t["mtime"], reverse=True)
    return jsonify({"ok": True, "items": items})


@app.route("/api/reviews/<name>", methods=["GET", "PUT", "DELETE"])
def api_review_file(name):
    p = (REVIEWS_DIR / name).resolve()
    if p.parent != REVIEWS_DIR.resolve() or p.suffix != ".md" or not p.is_file():
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    if request.method == "PUT":
        data = request.get_json(force=True, silent=True) or {}
        content = data.get("content")
        if not isinstance(content, str):
            return jsonify({"ok": False, "error": "内容格式错误"}), 400
        p.write_text(content, encoding="utf-8")
        return jsonify({"ok": True, "name": p.name})
    if request.method == "DELETE":
        p.unlink()
        return jsonify({"ok": True, "name": p.name})
    return jsonify({"ok": True, "name": p.name, "content": p.read_text(encoding="utf-8")})


@app.route("/api/reviews/notes", methods=["POST"])
def api_review_notes():
    """把选中文字追加进该书的梗概文件「## 我的笔记」区块（没有则创建）。"""
    data = request.get_json(force=True, silent=True) or {}
    title = (data.get("title") or "").strip()
    text = (data.get("text") or "").strip()
    if not title or not text:
        return jsonify({"ok": False, "error": "缺少书名或笔记内容"}), 400
    p = find_review_file(title)
    if not p:
        return jsonify({"ok": False, "error": "找不到该书的梗概文件，请先建书"}), 404
    content = p.read_text(encoding="utf-8")
    stamp = time.strftime("%Y-%m-%d %H:%M")
    note_line = f"- **{stamp}** · {text}"
    if "## 我的笔记" in content:
        idx = content.index("## 我的笔记")
        tail = content[idx:].rstrip()
        content = content[:idx] + tail + "\n" + note_line + "\n"
    else:
        content = content.rstrip() + "\n\n## 我的笔记\n\n" + note_line + "\n"
    p.write_text(content, encoding="utf-8")
    return jsonify({"ok": True, "name": p.name, "content": content})


@app.route("/api/health")
def api_health():
    """一键自检：OpenCode 连接、技能、默认模型、搜索提供方、队列状态。"""
    checks = []

    def add(name, ok, detail, hint=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail or "", "hint": hint})

    # 1) CLI 可执行文件
    cli_path = OPENCODE_CLI if OPENCODE_CLI != "opencode" else shutil.which("opencode") or ""
    if cli_path:
        add("OpenCode CLI", True, cli_path)
    else:
        add("OpenCode CLI", False, "未找到 opencode 命令",
            "安装 OpenCode（桌面版或 CLI）；CLI 版需在 PATH，或在 config.json 的 cli 填完整路径")

    # 2) 服务 + 技能 + 模型 + 搜索（都依赖服务可达）
    try:
        info = oc_request("get", "/api/info") or {}
        add("OpenCode 服务", True, "已连接（v%s）" % info.get("version", "?"))
        try:
            sk = oc_request("get", "/api/skill") or {}
            ids = [x.get("id") for x in sk.get("data", [])]
            if "book-tutor" in ids:
                add("book-tutor 技能", True, "已在 .opencode/skills/ 中被发现")
            else:
                add("book-tutor 技能", False, "未发现",
                    "确认 .opencode/skills/book-tutor/SKILL.md 存在，且本程序从项目目录启动")
        except Exception as e:
            add("book-tutor 技能", False, str(e)[:90])
        try:
            d = oc_request("get", "/api/model/default") or {}
            dd = d.get("data", d)
            mm = (dd.get("providerID") + "/" + dd.get("id")) if isinstance(dd, dict) else "?"
            add("默认模型", True, mm, "额度用尽时会按回退链自动切换")
        except Exception as e:
            add("默认模型", False, str(e)[:90])
        try:
            integ = oc_request("get", "/api/integration") or {}
            search_ids = ("tavily", "exa", "firecrawl", "parallel", "tinyfish")
            names = [i.get("name") for i in integ.get("data", [])
                     if i.get("id") in search_ids and i.get("connections")]
            if names:
                add("联网搜索", True, "已连接: " + ", ".join(names))
            else:
                add("联网搜索", False, "未连接任何搜索提供方",
                    "「具体怎么做」类问题需要联网：在 OpenCode 的 Integrations 里连接 Tavily（免费档够用）")
        except Exception as e:
            add("联网搜索", False, str(e)[:90])
    except Exception as e:
        add("OpenCode 服务", False, str(e)[:100], "确认 OpenCode 已启动（桌面版开着，或运行 opencode）")

    # 3) 建书队列
    with TUTOR_LOCK:
        qn = len(TUTOR_STATE["queue"])
        fn = len(TUTOR_STATE["failed"])
        cn = 1 if TUTOR_STATE["current"] else 0
    add("建书队列", True, "进行中 %d / 排队 %d / 失败 %d" % (cn, qn, fn),
        "有失败时可在左侧删除该书后重新添加")

    return jsonify({"ok": True, "checks": checks})


@app.route("/api/tutor/<sid>", methods=["DELETE"])
def api_tutor_delete(sid):
    with TUTOR_LOCK:
        TUTOR_STATE["queue"] = [x for x in TUTOR_STATE["queue"] if x["sid"] != sid]
        TUTOR_STATE["failed"] = [x for x in TUTOR_STATE["failed"] if x["sid"] != sid]
        if TUTOR_STATE["current"] and TUTOR_STATE["current"]["sid"] == sid:
            TUTOR_STATE["current"] = None
        _tutor_save()
    try:
        oc_request("delete", f"/api/session/{sid}")
    except Exception as e:
        # 会话已经不存在时视为删除成功（幂等），避免「删不掉」的死循环
        if "SessionNotFound" not in str(e) and "not found" not in str(e).lower():
            return jsonify({"ok": False, "error": f"删除失败：{e}"}), 500
    return jsonify({"ok": True})


OVERVIEW_CUT = re.compile(r"^#{1,4}\s*(附[：:]|主干自测|自测记录|反馈卡片|问答记录|总结评估)", re.M)


def _find_fuzzy(directory, title, suffix):
    """在 directory 里按书名找 <名><suffix>：先精确，再模糊（容忍大小写与拼写小差异）。"""
    directory = Path(directory)
    for p in (directory / f"{title}{suffix}", directory / f"{_safe_name(title)}{suffix}"):
        if p.is_file():
            return p
    key = _safe_name(title).replace("-", "").lower()
    if not key:
        return None
    best, best_score = None, 0.0
    for p in directory.glob(f"*{suffix}"):
        stem = _safe_name(p.stem).replace("-", "").lower()
        if not stem:
            continue
        if stem in key or key in stem:
            score = 1.0
        else:
            score = difflib.SequenceMatcher(None, stem, key).ratio()
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 0.8 else None


def find_review_file(title):
    """按书名找梗概文件；文件名由导师生成（大小写/拼写可能与输入略有出入），做宽松匹配。"""
    p = _find_fuzzy(REVIEWS_DIR, title, ".md")
    if p and p.stem.startswith("查漏表"):
        return None
    return p


@app.route("/api/overview")
def api_overview():
    title = (request.args.get("title") or "").strip()
    p = find_review_file(title) if title else None
    if not p:
        return jsonify({"ok": True, "content": ""})
    text = p.read_text(encoding="utf-8")
    m = OVERVIEW_CUT.search(text)
    if m:
        text = text[:m.start()]
    return jsonify({"ok": True, "content": text.strip(), "name": p.name})


@app.route("/api/mindmap")
def api_mindmap():
    title = (request.args.get("title") or "").strip()
    p = _find_fuzzy(DATA_DIR / "mindmap", title, ".json") if title else None
    if not p or not p.is_file():
        return jsonify({"ok": True, "tree": None})
    try:
        tree = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        tree = None
    return jsonify({"ok": True, "tree": tree if isinstance(tree, dict) else None})


def _xmind_topic(node):
    """把思维导图树节点转成 XMind 的 topic（递归）。"""
    topic = {
        "id": "t-" + uuid.uuid4().hex[:12],
        "class": "topic",
        "title": (node.get("text") or node.get("title") or "").strip(),
        "children": {"attached": []},
    }
    kids = node.get("children") or []
    if kids:
        topic["children"]["attached"] = [_xmind_topic(k) for k in kids]
    return topic


@app.route("/api/mindmap/xmind", methods=["POST"])
def api_mindmap_xmind():
    """把前端传来的思维导图树打包成 .xmind（XMind Zen 格式：zip 里放 content/metadata/manifest.json）。"""
    data = request.get_json(force=True, silent=True) or {}
    tree = data if isinstance(data, dict) else {}
    title = (tree.get("title") or "思维导图").strip()
    root = _xmind_topic({"text": title, "children": tree.get("children") or []})
    content = [{
        "id": "sheet-" + uuid.uuid4().hex[:8],
        "class": "sheet",
        "title": "Sheet 1",
        "rootTopic": root,
    }]
    metadata = {"creator": {"name": "xmind", "version": "1.0.0"}}
    manifest = {"file-entries": {"content.json": {}, "metadata.json": {}}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.json", json.dumps(content, ensure_ascii=False).encode("utf-8"))
        z.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
    buf.seek(0)
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title).strip("-") or "思维导图"
    return send_file(buf, as_attachment=True, download_name=f"{safe}.xmind", mimetype="application/octet-stream")


@app.route("/api/glossary")
def api_glossary():
    title = (request.args.get("title") or "").strip()
    p = _find_fuzzy(DATA_DIR / "glossary", title, ".json") if title else None
    if not p or not p.is_file():
        return jsonify({"ok": True, "items": []})
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        items = []
    return jsonify({"ok": True, "items": items if isinstance(items, list) else []})


if __name__ == "__main__":
    APP_PORT = int(USER_CONFIG.get("port", 5000))
    print(f"终极方案 已启动: http://127.0.0.1:{APP_PORT}  (模型: {DEEPSEEK_MODEL})")
    threading.Thread(target=_tutor_worker, daemon=True).start()
    app.run(host="127.0.0.1", port=APP_PORT, debug=False)