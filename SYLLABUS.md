# My Agent Syllabus

> 本大纲整理了我的 Claude Code 环境中已配置的 MCP Servers 和自定义 Skills，用于 AI 辅助软件工程工作流。

---

## 一、MCP Servers

| Server | 用途 | 来源 |
|--------|------|------|
| `fetch` | 网页内容抓取 | `@modelcontextprotocol/server-fetch` |
| `sequential-thinking` | 序列化推理与思维链 | `@modelcontextprotocol/server-sequential-thinking` |
| `sqlite` | 本地 SQLite 数据库操作 (`~/.claude/data.db`) | `@modelcontextprotocol/server-sqlite` |
| `memory` | 记忆持久化 | `@modelcontextprotocol/server-memory` |
| `filesystem` | 文件系统访问 (Desktop, Documents) | `@modelcontextprotocol/server-filesystem` |
| `chrome-devtools` | Chrome DevTools 集成 (调试、截图、性能分析) | `chrome-devtools-mcp@latest` |

---

## 二、Skills 目录

### 2.1 沟通与协作

| Skill | 描述 | 触发场景 |
|-------|------|----------|
| [`caveman`](.claude/skills/caveman/SKILL.md) | 极简通信模式，压缩 token 使用约 75% | 说 "caveman mode" / "less tokens" / "be brief" |
| [`handoff`](.claude/skills/handoff/SKILL.md) | 将当前会话压缩为交接文档供新 agent 继续 | 需要跨会话延续工作时 |
| [`zoom-out`](.claude/skills/zoom-out/SKILL.md) | 提供更高层次的宏观视角和模块地图 | 不熟悉代码区域或需要大局观时 |

### 2.2 调试与诊断

| Skill | 描述 | 触发场景 |
|-------|------|----------|
| [`diagnose`](.claude/skills/diagnose/SKILL.md) | 严谨的 bug 诊断循环：复现 → 最小化 → 假设 → 探测 → 修复 → 回归测试 | "diagnose this" / "debug this" / 报告 bug / 性能回归 |

### 2.3 设计与评审

| Skill | 描述 | 触发场景 |
|-------|------|----------|
| [`grill-me`](.claude/skills/grill-me/SKILL.md) | 对用户计划进行无情追问直至达成共识 | "grill me" / 压力测试计划 |
| [`grill-with-docs`](.claude/skills/grill-with-docs/SKILL.md) | 结合现有领域模型文档进行追问，并实时更新 CONTEXT.md / ADR | 需要对照项目文档审视计划时 |
| [`prototype`](.claude/skills/prototype/SKILL.md) | 构建可丢弃的原型来验证设计（逻辑分支或 UI 分支） | "prototype this" / 探索设计选项 / 试玩 |

### 2.4 工程实践

| Skill | 描述 | 触发场景 |
|-------|------|----------|
| [`tdd`](.claude/skills/tdd/SKILL.md) | 测试驱动开发，红-绿-重构循环 | "red-green-refactor" / 测试优先开发 |
| [`improve-codebase-architecture`](.claude/skills/improve-codebase-architecture/SKILL.md) | 发现代码库深化机会，输出 HTML 可视化报告 | 改进架构 / 寻找重构机会 / 提高可测试性 |
| [`write-a-skill`](.claude/skills/write-a-skill/SKILL.md) | 创建结构规范的新 agent skill | 创建/编写/构建新 skill 时 |

### 2.5 项目管理

| Skill | 描述 | 触发场景 |
|-------|------|----------|
| [`setup-matt-pocock-skills`](.claude/skills/setup-matt-pocock-skills/SKILL.md) | 为仓库搭建 issue tracker、triage labels、domain docs 配置 | 首次使用工程类 skills 前运行 |
| [`to-issues`](.claude/skills/to-issues/SKILL.md) | 将计划拆分为独立的垂直切片 issues | 将计划转为可执行任务时 |
| [`to-prd`](.claude/skills/to-prd/SKILL.md) | 将当前会话上下文整理为 PRD 并发布到 issue tracker | 需要创建 PRD 时 |
| [`triage`](.claude/skills/triage/SKILL.md) | 通过状态机驱动的问题分类流程 | 创建/分类/审查 issues 时 |

---

## 三、快速参考

### 常用命令速查

```bash
# 诊断问题
/diagnose

# 压力测试设计
/grill-me

# 构建原型
/prototype

# TDD 开发
/tdd

# 生成 PRD
/to-prd

# 任务拆解
/to-issues

# 进入极简模式
caveman mode
```

### MCP 能力速查

- **Web 抓取**: `fetch` server 可抓取任意网页内容
- **数据库**: `sqlite` server 操作本地 `~/.claude/data.db`
- **记忆**: `memory` server 持久化跨会话记忆
- **文件系统**: `filesystem` server 访问 Desktop/Documents
- **浏览器**: `chrome-devtools` server 驱动 Chrome 进行调试、截图、性能分析
- **推理**: `sequential-thinking` server 支持复杂问题的分步推理

---

## 四、配置位置

| 配置项 | 路径 |
|--------|------|
| MCP Servers (主要配置) | `~/.claude/settings.local.json` |
| MCP Servers (补充配置) | `~/.claude.json` |
| Skills 目录 | `~/.claude/skills/` |
| 记忆文件 | `~/.claude/projects/C--Users-D1405/memory/` |

---

*Generated on 2026-05-28*
