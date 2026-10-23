# 知行读书（终极方案）

输入一本书的书名（或直接粘贴参考文献），AI 导师带你走完
**收集资料 → 结构化梗概 → 自由问答 → 主干自测纠偏 → 总结与思维导图**，
所有产出（梗概、资料库、术语表、反馈卡片、查漏表、思维导图）都以
Markdown / JSON 落盘，随时回看。

本应用是 **OpenCode 的一个前端**：真正干活的是项目里的
`book-tutor` 技能（`.opencode/skills/book-tutor/SKILL.md`），
由 OpenCode 加载并调用你配置的模型来完成。

## 工作原理

```
浏览器  static/v2.html
   │
Flask  app.py  ── 只做：页面托管 + 数据读写 + 调 OpenCode CLI
   │
OpenCode 本地服务（opencode api）
   │
book-tutor 技能（本项目 .opencode/skills/ 下）
   │
模型（OpenCode 里配置的 provider，带自动回退链）
```

- 前端只有**一个页面**：左侧书架，右侧「梗概窗口（可折叠）+ 对话区」。
- 建书任务**逐本排队**生成，失败自动换模型重试；问题**可排队**，不打断正在生成的回答。
- 问答区选中文字可：**讲解 / 复制 / 标记 / 建立词条**；术语在正文里点击可看解释（大白话 + 例子 + 为什么需要）。
- 点「结束并总结」生成**思维导图**：全书概念地图，你追问过的问题和答案要点挂在对应概念下，可导出 SVG。
- 支持语音输入（Chrome/Edge 的语音识别）。

## 前置条件

1. **Python 3.10+**
2. **OpenCode**（桌面版或 CLI），并且**保持运行**——应用通过 `opencode` CLI 调用它的本地服务。
   - Windows 桌面版自带 CLI，`app.py` 会自动找到它；
   - CLI 版需保证 `opencode` 在 PATH 里（也可用环境变量 `OPENCODE_CLI` 指定完整路径）。
3. **模型额度**：登录 OpenCode（免费模型有额度），或在 OpenCode 里配置自己的
   provider（如火山方舟 Ark Coding Plan、DashScope 等）。
4. **联网搜索（可选，推荐）**：在 OpenCode 的 Integrations 里连接 **Tavily**
   （免费档每月 1000 次，够用）。不连也能用，只是遇到要查资料的问题时只能靠模型知识。

> 不需要任何 DeepSeek / OpenAI API Key。旧版的 DeepSeek 流程已弃用。

## 安装运行

```bash
git clone https://github.com/winwindy511-ctrl/windystudy.git
cd windystudy

python -m venv .venv
.venv\Scripts\activate          # Windows（mac/Linux 用 source .venv/bin/activate）
pip install -r requirements.txt

python app.py
```

浏览器打开 **http://127.0.0.1:5000/v2.html**

## 模型与配置

- 项目根目录的 `opencode.json` 已设置默认模型（当前是 OpenCode 免费档），
  可按需改成自己的 provider，例如：
  ```json
  { "model": "volcengine-coding-plan/deepseek-v4-flash" }
  ```
- **`config.json`（可选）**：复制 `config.json.example` 为 `config.json` 后修改，
  不用动代码：
  ```json
  {
    "models": ["provider/模型id", "..."],   ← 回退链，按顺序尝试
    "port": 5000,                            ← 网页端口
    "cli": "C:\\...\\opencode-cli.exe"       ← OpenCode CLI 路径（CLI 版需要）
  }
  ```
  不创建也能运行（内置默认值）。
- **自动回退链**：问答或建书遇到「额度用尽 / 限流」时，会按候选顺序自动切换模型
  并重发你的问题，成功后清空记录，额度恢复后回到首选模型。
- **环境体检**：侧栏底部的「环境体检」按钮一键检查 OpenCode 连接、
  book-tutor 技能、默认模型、联网搜索、建书队列，出问题会给出修正提示。

## 数据存在哪

| 位置 | 内容 |
|---|---|
| `reviews/<书名>.md` | 梗概 + 自测反馈卡片 + 总结（可直接用编辑器看，网页里可编辑） |
| `data/sources/<书名>.md` | 建书时联网收集的摘要资料（简介、章节骨架、书评争议） |
| `data/glossary/<书名>.json` | 术语表（大白话解释 + 例子 + 应用场景），可手动增改 |
| `data/mindmap/<书名>.json` | 思维导图 JSON |
| `data/qa/` | ——（预留） |

> 仓库里现有的 `data/`、`reviews/` 是作者自己的学习记录，可作输出格式示例，自行清理即可。

## 常见问题

- **浏览器拒绝连接 / 502**：说明 `python app.py` 没在跑（或启动即报错退出，通常是没装依赖）。
  先 `pip install -r requirements.txt`，再 `python app.py`，看到
  `终极方案 已启动: http://127.0.0.1:5000` 后保持窗口开着。
- **回答报「额度用尽」**：免费档按 5 小时窗口限额，用完等恢复或换 provider；
  应用会自动尝试回退链上的其它模型。
- **问「具体怎么做」没联网**：确认 OpenCode 里已连接搜索提供方（Tavily 等）。
- **书架出现「失效」且删不掉的幽灵条目**：刷新页面即可（会话已删除，列表是旧的）。
- **语音按钮不可用**：语音输入依赖 Chrome/Edge 的在线语音识别，其他浏览器不支持。
