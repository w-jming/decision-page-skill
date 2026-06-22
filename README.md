# decision-page-skill

> Agent skill（Claude Code / Codex 等通用）：本地交互决策页 —— 把需要人工拍板的事项做成浏览器里的决策卡片，选完直接落盘；页面内还能和会话中值守的智能体实时问答，决策内容随问随改、即时热更新。

纯 Python 标准库 + 单文件 HTML，**零第三方依赖**，只绑定 `127.0.0.1`。

## 为什么

让 AI 干活时，真正的瓶颈往往是「等人拍板」。把决策罗列在聊天里有三个问题：选项多了看不过来、回复格式随意难以解析、记录散落在会话里无法跨会话追溯。decision-page 把这件事产品化：

- **决策卡片**：每项决策带背景、2~4 个互斥选项（含推荐标注）、自定义方案和备注；
- **直接落盘**：点「保存决策」追加写入 `decisions-log.md`，值守的智能体自动收到 `SAVED` 事件并回填执行，不需要复制粘贴回对话；
- **页面内问答**：右侧聊天面板直连会话中值守的智能体——可以追问取舍、要求加选项、改背景，`decisions.json` 一变页面立即热更新；
- **跨会话契约**：决策日志入库，后续任何会话都能读取、回填、执行。

## 快速体验

```bash
git clone https://github.com/w-jming/decision-page-skill.git
cd decision-page-skill
uv run templates/decide.py --dir examples/demo   # 或 python3 templates/decide.py --dir examples/demo
```

浏览器自动打开 `http://127.0.0.1:8765`。选择并保存后，结果写入 `examples/demo/decisions-log.md`（演示产物已 gitignore；`examples/demo/decisions.json` 的状态变更用 `git checkout -- examples/demo/decisions.json` 还原）。

> 没有智能体值守时，聊天消息会留在 `examples/demo/chat.jsonl` 中无人应答——问答能力来自值守的 agent 会话（见下）。

## 通用型 skill（跨 agent）

本 skill 与具体 agent 无关：服务 `decide.py` 是零依赖纯 stdlib，值守通过它的 `watch`（流式）或 `poll`（一次性、可周期调用）事件流进行——任何能执行命令并读取其输出的 agent 都能用。

- **Claude Code**：用 Monitor 工具跑 `decide.py watch` 值守。
- **Codex / 其它 agent**：用各自的后台/流式能力跑 `decide.py watch`，或在干活间隙周期性 `decide.py poll`。

## 安装

```bash
git clone https://github.com/w-jming/decision-page-skill.git ~/workplace/skills/decision-page-skill
ln -s ~/workplace/skills/decision-page-skill ~/.claude/skills/decision-page-skill   # Claude Code
ln -s ~/workplace/skills/decision-page-skill ~/.codex/skills/decision-page-skill     # Codex
```

之后当出现多项需要拍板的决策时，agent 会按 [SKILL.md](SKILL.md) 的流程：复制模板 → 生成 `decisions.json` → 后台启动服务 → 用 `watch`/`poll` 值守聊天与日志 → 实时应答/热更新 → 决策保存后自动回填执行。也可以把值守交给后台子任务，主会话继续干别的活。

## 工作原理

```
浏览器（decisions.html SPA）
   │  POST /api/chat            提问
   │  POST /api/save            保存决策
   │  GET  /api/state /events   数据 + SSE 实时推送
   ▼
decide.py（零依赖本地服务，127.0.0.1）
   │  追加 chat.jsonl（user 行）          追加 decisions-log.md
   ▼                                      ▼
值守的 agent 会话（watch/poll 消费归一化事件流：QUESTION / SAVED）
   │  decide.py reply "…"  →  chat.jsonl（assistant 行）→ SSE → 聊天气泡
   │  编辑 decisions.json  →  SSE → 决策卡片热更新
   └─ 读 decisions-log.md  →  回填决策表、按结论执行
```

页面与智能体之间没有网络直连，全部通过数据目录里的三个文件中转——这正是它能跨 agent、跨会话、可审计、零依赖的原因。

## 文件契约

| 文件 | 谁写 | 作用 |
|---|---|---|
| `decisions.json` | 智能体维护 | 决策数据；修改即热更新页面 |
| `chat.jsonl` | 页面写 user 行；`decide.py reply` 写 assistant 行 | 浏览器 ↔ 智能体消息通道 |
| `decisions-log.md` | 页面「保存决策」追加 | 持久决策日志，跨会话契约，应当入库 |

## `decide.py` 用法

```bash
python3 decide.py                     # 启动服务（默认端口 8765，自动开浏览器）
python3 decide.py --port 8888 --no-browser
python3 decide.py --dir docs/decisions    # 数据目录不在脚本旁时指定（位置任意）
python3 decide.py --idle-timeout 7200     # 空闲自动退出秒数；页面开着不计时，关掉页面后开始计时
                                          # 默认 3600（一小时），0 = 一直运行
python3 decide.py reply "简短回答"         # 向聊天面板追加一条智能体回复
python3 decide.py reply - <<'EOF'          # 多行/含特殊字符的回复走 stdin
支持 **加粗**、`代码`、列表与代码块
EOF
python3 decide.py watch                    # 持续输出新事件（QUESTION/SAVED），供值守
python3 decide.py poll                     # 打印自上次以来的新事件后退出（任何 agent 可周期调用）
```

## License

[MIT](LICENSE)
