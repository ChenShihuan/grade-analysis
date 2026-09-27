# `.agents/` —— 与工具无关的 agent 协议目录

这个目录存在的意义：**让工作区的流程知识与 AI 工具解耦。**

原先这些流程文档放在 `班主任工作相关/.claude/skills/` 下——那是 Claude Code 专有的位置，
换一个工具（Cursor、Codex、Copilot、Gemini CLI…）就看不见，而且文档与数据分在两个目录，
工作区不算自包含。现在把内容搬到工作区内部的 `.agents/`，任何支持该协议的工具都能读。

## 为什么是复数 `.agents`

**`.agents/skills/<名字>/SKILL.md` 是跨工具的社区标准**，源自 Agent Skills 规范
（agentskills.io）。目前已采纳该路径的有 Amp、Codex、Cursor、GitHub Copilot、Gemini CLI、
Cline、OpenCode、Replit、Warp、Kimi Code CLI 等十余个工具。

单数 `.agent/` 是 Antigravity 时期的旧路径，生态已经迁移走；连当初提这个议案的
OpenSpec issue 最后也是按**复数**实现并关闭的。所以这里用复数。

**一个容易踩的格式要求**：规范认的是 `skills/<名字>/SKILL.md` 这种**目录形式**，
放在 `skills/` 下光秃秃的 `.md`（如 `skills/foo.md`）**会被忽略**。本目录已按规范组织。

## 目录约定

```
.agents/
└─ skills/
    ├─ grade-analysis/
    │   ├─ SKILL.md              流程一：新成绩表 → 拆分 → 等级分布报告
    │   └─ scripts/
    │       └─ 探查工作簿.py       该流程第一步用的探查脚本
    └─ subject-teacher-report/
        └─ SKILL.md              流程二：按科目出科任老师文档（含目标生、图片导出）
```

每个 `SKILL.md` 带 YAML frontmatter，`name` 与 `description` 两项是规范要求的；
`description` 写成触发条件（「当用户…时使用」），工具靠它判断要不要加载。

入口文件是工作区根目录的 **`AGENTS.md`**：先读它，它再把人/agent 导向上面这两个流程。

## 给不同工具怎么接

| 工具 | 怎么读到这些流程 |
|---|---|
| 认 `.agents/` 的工具（Codex、Cursor、Copilot、Gemini CLI 等） | 直接读本目录，无需额外配置 |
| Claude Code | 读 `.claude/skills/`；本目录**不是**它的原生位置。靠 `班主任工作相关/.claude/skills/<名>/SKILL.md` 那层**指路存根**桥接 |
| 识别 `AGENTS.md` 的工具 | 读工作区根目录的 `AGENTS.md`，它会导向这里 |
| 其他 / 人 | 直接读 `skills/*/SKILL.md`，都是普通 Markdown，无专有格式 |

### Claude Code 的发现机制（2026-09-27 查证，本机 2.1.281）

- **`AGENTS.md` 是原生支持的**（2.1.277 起）。文件名候选是**字面量 `AGENTS.md`**——
  `agent.md`（单数、小写）**不在候选里**。Windows 不区分大小写所以本地无差别，
  但拷到飞牛（Linux）就会失效，**一律用大写**。
- **只在没有 CLAUDE.md 时才读 AGENTS.md**（设置项 `instructionFiles`，默认
  `claude-md-or-agents-md`）。本仓库没有 CLAUDE.md，所以 `AGENTS.md` 会被读到；
  **但若哪天在同一个目录加了 CLAUDE.md，`AGENTS.md` 就不再被读**——加之前想清楚。
- **加载范围 = 启动目录 + 各级父目录**（和 CLAUDE.md 一样）。**子目录里的不预加载**，
  要等 Claude 读到该子树里的文件时才按需加载。
- **skill 同理**：启动目录 + 父目录的 `.claude/skills` 预加载，子目录的按需发现。
  存根放在 `班主任工作相关/.claude/skills/`，所以**从工作区或它下面任何一层启动都能命中**
  （父链）；从更外层启动时，用
  `claude --add-dir "…\班主任工作相关"` 把它带进来。
  注意 `settings.json` 的 `permissions.additionalDirectories` **只给文件访问权、不加载 skill**。
- **`.agents/skills` 不是 Claude Code 的原生 skill 位置**，不会被它自动发现——
  这正是需要 `.claude/skills/` 那层存根桥接的原因。也不存在「自定义 skill 路径」的配置项
  （只有未实现的 feature request）。

## 两条规矩

1. **内容只有一份，就在这个目录里。** 别在别处再抄一份——本项目已经因为「两份口径不同」
   出过事（同一份成绩表两张副本、排名基数不同，差出 20~30 个名次）。
   指路存根只写「去哪找」，不写内容。
2. **改流程就改这里**，并顺手确认 `AGENTS.md` 里提到它的那几行还对得上。
