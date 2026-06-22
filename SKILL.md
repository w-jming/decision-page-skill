---
name: decision-page-skill
description: 当存在需要用户人工拍板的决策事项（≥2 项，或用户要求）时使用。生成本地交互决策页：零依赖 Python 服务 + HTML 决策卡片页 + 内置 Claude 问答面板。用户在浏览器选择/提问，Claude 通过 Monitor 值守实时应答并可热更新页面内容；「保存决策」直接追加写入 decisions-log.md（不需要复制粘贴回对话）。后续会话读取日志、回填决策表并执行。用户硬性偏好：人工决策不得只在聊天里罗列。
---

# decision-page-skill · 交互决策页

## 何时使用

- 积累了 ≥2 个需要用户拍板的决策，或用户明确要求"做成决策页"。
- 单个简单二选一仍可直接在对话里问（AskUserQuestion）；多项、需要背景比较的决策必须用本 skill。

## 架构一览

| 文件 | 谁写 | 作用 |
|---|---|---|
| `decide.py` | 模板复制 | 零依赖服务：页面、SSE 实时推送、聊天、保存；`reply` 子命令供 Claude 应答 |
| `decisions.html` | 模板复制 | 单文件界面：决策卡片 + Claude 聊天侧栏，数据全部来自接口 |
| `decisions.json` | **Claude 维护** | 决策数据；Claude 一改文件，页面立即热更新 |
| `chat.jsonl` | 页面写 user 行；`reply` 写 assistant 行 | 浏览器 ↔ Claude 的消息通道 |
| `decisions-log.md` | 页面「保存决策」追加 | 跨会话持久契约，后续回填执行的依据 |

页面与 Claude 之间没有直连：浏览器提问 → 服务追加 `chat.jsonl` → Claude 的 Monitor 被触发 → Claude 用 `reply` 写回答 / 直接改 `decisions.json` → SSE 推给页面。**不要手工编辑 `chat.jsonl` 的 user 行。**

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

用 Bash（`run_in_background: true`）启动，然后把地址告诉用户：

```bash
python3 <目录>/decide.py            # 默认 http://127.0.0.1:8765，自动开浏览器
# --port N 换端口；--no-browser 不开浏览器；--dir <数据目录> 数据不在脚本旁时指定
# --idle-timeout N 空闲 N 秒自动退出（默认 3600；页面开着不计时；0 = 一直运行）
```

服务空闲一小时（无页面连接、无请求）会自动退出，不会留下孤儿进程；决策预计拖更久时可调大或置 0。

### 4. 值守（关键）：启动 Monitor 监听提问与保存

```bash
# Monitor 工具，persistent: true；<数据目录> 替换为实际路径
cd <数据目录> && tail -F -n 0 chat.jsonl decisions-log.md 2>/dev/null | grep --line-buffered -E '"role": "user"|^## '
```

触发后按事件类型处理：

- **用户提问**（chat.jsonl 出现 user 行）：读 `chat.jsonl` 看完整上下文，结合项目知识作答：
  ```bash
  python3 <目录>/decide.py reply - <<'EOF'
  回答内容（支持 **加粗**、`代码`、列表、```代码块```，会渲染成气泡）
  EOF
  ```
  回答要短而具体；用户在页面上等着，先快速应答，再做耗时调查后补充第二条。
- **用户要求修改决策内容**（加选项、改背景、增删决策项）：直接编辑 `decisions.json` → 页面自动热更新 → 再 `reply` 一句"已更新，请刷新查看 D2 的新选项 C"之类的确认（页面其实不用刷新，礼貌性告知即可）。
- **用户保存决策**（decisions-log.md 出现 `## ` 行）：进入第 6 步回填执行，并 `reply` 确认收到。

### 5. 可选：交给子 agent 值守

若主会话需要继续做其它工作，可把值守交给后台子 agent（Agent 工具，`run_in_background: true`），prompt 模板：

> 你负责值守 <数据目录> 的决策页。用 Monitor（persistent）执行：`cd <数据目录> && tail -F -n 0 chat.jsonl decisions-log.md 2>/dev/null | grep --line-buffered -E '"role": "user"|^## '`。用户提问时读 chat.jsonl，结合以下背景作答（用 `python3 decide.py reply -` 写回）：<决策背景摘要>。用户要求修改决策内容时直接编辑 decisions.json。授权范围：只允许改 <数据目录> 内的 decisions.json 和调用 reply，不得改动其他文件。用户保存决策（decisions-log.md 新增 `## ` 条目）后，向我汇报结论并结束值守。

子 agent 没有主会话的上下文，prompt 里必须带足决策背景摘要。简单场景优先用第 4 步的主会话 Monitor（上下文最全、回答质量最高）。

### 6. 回填执行（保存触发或后续会话）

1. 读 `decisions-log.md` 最新条目（带 `待智能体回填` 注释的）。
2. 把结论回填到项目的决策表/待办文档，解锁对应任务。
3. 把该条目注释改为 `已回填（日期）`。
4. 按结论开始执行（遵守项目工作流，如分支 + PR）。

注意：第 3 步原地修改日志会让值守的 `tail -F` 重读整个文件，随即收到一批**自触发的 Monitor 事件**（旧 `## ` 条目重放一遍）。这不是新决策——核对日志确无新条目（所有条目均已标记回填）后忽略即可，不要重复执行回填。

### 7. 收尾

决策全部完成后：TaskStop 停掉 Monitor，杀掉后台 `decide.py` 进程（即使忘了，空闲超时也会让它自动退出）；确认日志条目均已标记回填。

## 注意

- `decide.py` 只绑定 127.0.0.1，零依赖；不要改成监听 0.0.0.0。
- 多行回复一律用 `reply - <<'EOF'` 的 stdin 形式，避免 shell 引号问题。
- 页面以 `file://` 直接打开会提示功能不可用；必须经 `decide.py` 访问。
- 用户保存后 `decisions.json` 中对应项会变为 `"status": "decided"` 并带 `result`；Claude 修改 `decisions.json` 时不要覆盖已有的 decided 状态。
