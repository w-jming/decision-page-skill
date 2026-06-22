---
name: decision-page-skill
description: 当存在需要用户人工拍板的决策事项（≥2 项，或用户要求）时使用。生成本地交互决策页：零依赖 Python 服务 + HTML 决策卡片页 + 内置问答面板。用户在浏览器选择/提问，值守的智能体实时应答并可热更新页面内容；「保存决策」直接追加写入 decisions-log.md（不需要复制粘贴回对话）。后续会话读取日志、回填决策表并执行。通用型 skill：服务与 agent 无关，值守用 decide.py 的 watch/poll 事件流，任何能跑命令并读其输出的 agent 都能用。用户硬性偏好：人工决策不得只在聊天里罗列。
metadata:
  agents: [claude, codex]
---

# decision-page-skill · 交互决策页

> **通用型（跨 agent）。** 服务 `decide.py` 是零依赖纯 stdlib，与具体 agent 无关；值守通过 `decide.py watch`（流式）或 `decide.py poll`（一次性、可周期调用）消费归一化事件——任何能执行命令并读取其输出的 agent（Claude Code、Codex 等）都能值守。下文出现"智能体"即指当前 agent。

## 何时使用

- 积累了 ≥2 个需要用户拍板的决策，或用户明确要求"做成决策页"。
- 单个简单二选一仍可直接在对话里问；多项、需要背景比较的决策必须用本 skill。

## 架构一览

| 文件 | 谁写 | 作用 |
|---|---|---|
| `decide.py` | 模板复制 | 零依赖服务：页面、SSE 实时推送、聊天、保存；`reply` 应答、`watch`/`poll` 值守事件流 |
| `decisions.html` | 模板复制 | 单文件界面：决策卡片 + 问答侧栏，数据全部来自接口 |
| `decisions.json` | **智能体维护** | 决策数据；文件一改，页面立即热更新 |
| `chat.jsonl` | 页面写 user 行；`reply` 写 assistant 行 | 浏览器 ↔ 智能体的消息通道 |
| `decisions-log.md` | 页面「保存决策」追加 | 跨会话持久契约，后续回填执行的依据 |

页面与智能体之间没有直连：浏览器提问 → 服务追加 `chat.jsonl` → 值守的 `watch`/`poll` 发出 `QUESTION` 事件 → 智能体用 `reply` 写回答 / 直接改 `decisions.json` → SSE 推给页面。**不要手工编辑 `chat.jsonl` 的 user 行。**

## 流程

### 1. 放置文件

把 `templates/` 下 `decide.py`、`decisions.html` 复制到项目内同一目录：

- git 仓库项目：建议 `docs/decisions/`（或项目既有约定位置）。遵循项目 git 规范（分支 + PR）。决策日志是治理记录，应当入库。
- 非仓库场景：任意工作目录即可。

### 2. 生成 decisions.json（与模板同目录，或 `--dir` 指定的数据目录）

```json
{
  "title": "项目名",
  "subtitle": "一句话说明本批决策的背景（可选）",
  "decisions": [
    {
      "id": "D1",
      "title": "一句话标题",
      "doc": "背景文档名（可选）",
      "background": "两三句背景：现状、为什么需要决策、代价对比。支持多行（\\n）。",
      "allowCustom": true,
      "options": [
        {"key": "A", "label": "选项标题", "desc": "一句话说明取舍", "recommended": true},
        {"key": "B", "label": "另一选项", "desc": "说明"}
      ],
      "status": "open"
    }
  ]
}
```

质量要求：每项恰好一个 `recommended: true`；选项互斥且穷尽合理路径（2~4 个）；背景给出代价/收益对比，让用户不读外部文档也能选。`allowCustom` 默认开（页面会多一个"自定义"输入项）。

### 3. 启动服务并告知用户

在**后台**启动（不阻塞会话），然后把地址告诉用户：

```bash
python3 <目录>/decide.py            # 默认 http://127.0.0.1:8765，自动开浏览器
# --port N 换端口；--no-browser 不开浏览器；--dir <数据目录> 数据不在脚本旁时指定
# --idle-timeout N 空闲 N 秒自动退出（默认 3600；页面开着不计时；0 = 一直运行）
```

- 后台启动方式按 agent 而定：Claude Code 用 Bash `run_in_background: true`；通用做法 `nohup python3 <目录>/decide.py >/tmp/decide.log 2>&1 &`，或该 agent 的等价后台执行能力。
- 服务空闲一小时（无页面连接、无请求）会自动退出，不留孤儿进程；决策预计拖更久时调大或置 0。

### 4. 值守（关键）：监视提问与保存

持续消费 `decide.py` 的事件流——每个事件一行：`QUESTION #<id>: <提问摘要>`（用户提问）或 `SAVED: <条目标题>`（用户保存）。游标存在数据目录的 `.decide-watch.json`，**只认新增、不回放历史**。按 agent 选监视方式：

- **Claude Code**：用 Monitor 工具（`persistent: true`）跑流式命令——
  ```bash
  python3 <目录>/decide.py watch --dir <数据目录>
  ```
- **Codex / 其它 agent**：用各自的后台/流式执行能力跑同一条 `watch` 命令；若不便常驻，则在干活间隙**周期性**调用一次性的
  ```bash
  python3 <目录>/decide.py poll --dir <数据目录>     # 打印自上次以来的新事件后立即退出，无新事件则无输出
  ```

收到事件后按类型处理：

- **`QUESTION`（用户提问）**：读 `chat.jsonl` 看完整上下文，结合项目知识作答，用 `reply` 写回：
  ```bash
  python3 <目录>/decide.py reply - --dir <数据目录> <<'EOF'
  回答内容（支持 **加粗**、`代码`、列表、```代码块```，会渲染成气泡）
  EOF
  ```
  回答要短而具体；用户在页面上等着，先快速应答，再做耗时调查后补充第二条。
- **用户要求修改决策内容**（加选项、改背景、增删决策项）：直接编辑 `decisions.json` → 页面自动热更新 → 再 `reply` 一句"已更新，D2 多了选项 C"之类的确认（页面无需刷新，礼貌告知即可）。
- **`SAVED`（用户保存）**：进入第 6 步回填执行，并 `reply` 确认收到。

### 5. 可选：交给后台子任务值守

若主会话需继续做其它工作，可把值守交给后台子任务（Claude Code：Agent 工具 `run_in_background: true`；其它 agent：等价的子会话/后台任务能力），prompt 模板：

> 你负责值守 `<数据目录>` 的决策页。用你的后台/流式能力跑 `python3 <目录>/decide.py watch --dir <数据目录>`（或周期性 `poll`）。收到 `QUESTION` 事件时读 `chat.jsonl`，结合以下背景作答（用 `python3 <目录>/decide.py reply - --dir <数据目录>` 写回）：<决策背景摘要>。用户要求修改决策内容时直接编辑 `<数据目录>/decisions.json`。授权范围：只允许改该目录内的 `decisions.json` 和调用 `reply`，不得改动其他文件。收到 `SAVED` 事件后，向我汇报结论并结束值守。

子任务没有主会话的上下文，prompt 里必须带足决策背景摘要。简单场景优先用第 4 步的主会话值守（上下文最全、回答质量最高）。

### 6. 回填执行（保存触发或后续会话）

1. 读 `decisions-log.md` 最新条目（带 `待智能体回填` 注释的）。
2. 把结论回填到项目的决策表/待办文档，解锁对应任务。
3. 把该条目注释改为 `已回填（日期）`。
4. 按结论开始执行（遵守项目工作流，如分支 + PR）。

注意：`watch`/`poll` 的游标只认新增 `## ` 条目，第 3 步**原地修改日志不会触发重放**。（仅当你改用裸 `tail -F` 流时才会重读整文件、重放旧条目——那时核对确无新条目后忽略即可。）

### 7. 收尾

决策全部完成后：停止值守（Claude Code：TaskStop 停 Monitor / 后台 Agent；其它 agent：结束 `watch` 进程或停止 `poll` 轮询），杀掉后台 `decide.py` 进程（即使忘了，空闲超时也会让它自动退出）；确认日志条目均已标记回填。

## 注意

- `decide.py` 只绑定 127.0.0.1，零依赖；不要改成监听 0.0.0.0。
- 多行回复一律用 `reply - <<'EOF'` 的 stdin 形式，避免 shell 引号问题。
- 页面以 `file://` 直接打开会提示功能不可用；必须经 `decide.py` 访问。
- 用户保存后 `decisions.json` 中对应项会变为 `"status": "decided"` 并带 `result`；修改 `decisions.json` 时不要覆盖已有的 decided 状态。
- 所有 `decide.py` 子命令都接受 `--dir <数据目录>`（可放命令任意位置）；值守、应答、服务务必指向同一数据目录。
