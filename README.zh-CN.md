# Claude 技能装备栏

> [English](README.md) · **中文**

一个本地运行、暗黑破坏神风格的网页，用来管理你的
[Claude Code](https://docs.claude.com/en/docs/claude-code) 个人 skill。把 skill
拖进装备格 = **启用**，拖出来 = **禁用**。

skill 多了以后，很难记清哪些在生效。这个工具把它变成游戏 —— 左边是技能典籍书架，
右边是装备栏。

## "装备"为什么是真生效

这不是只改外观的开关。启用 / 禁用会**真实移动 skill 目录**：

- **已装备**的 skill 放在 `~/.claude/skills/` —— Claude Code 真正读取的目录。
- **已卸下**的 skill 移动到 `~/.claude/skills-armory/` —— Claude 不读取的仓库。
- 把 skill 拖进格子 = 把目录移回 `skills/`；拖出格子 = 移到 armory 仓库。

用移动目录而不是软链接，是因为移动目录 100% 可靠 —— 能确保 Claude Code 一定能
（或一定不能）发现该 skill。整个过程**绝不删除、绝不覆盖**任何文件。

> ⚠️ Claude Code 在会话启动时加载 skill。**装备 / 卸下后，需重启 Claude 会话才
> 会生效。**

## 功能

- 🗂 只管理 `~/.claude/skills/` 下的个人 skill —— 不碰 plugin skill。
- 🎮 暗黑破坏神风格深色界面，拖拽式装备格。
- 🔁 通过移动目录真实启用 / 禁用 —— 安全，不删除、不覆盖。
- 🔗 **依赖检测** —— 已装备的 skill 若依赖某个未装备的 skill，其格子会显示
  ⚠️ 角标。
- 🌐 中英文双语界面 —— 右上角按钮一键切换。
- 🪶 单文件、零第三方依赖 —— 只需 Python 3。

## 环境要求

- Python 3.7+
- Claude Code，且 `~/.claude/skills/` 下至少有一个个人 skill

## 快速开始

```bash
python3 server.py
```

然后在浏览器打开 <http://127.0.0.1:8777>。

- **左侧面板** —— 当前*未装备*的技能典籍。鼠标 hover 卡片可看完整介绍。
- **右侧面板** —— 12 个装备格。
- 左侧 → 空格子 = **装备**；格子 → 左侧面板 = **卸下**；格子 ↔ 格子 =
  调整位置 / 交换。

服务只绑定 `127.0.0.1`，不会暴露到网络。

## 什么算一个 skill

`~/.claude/skills/` 下任何包含 `SKILL.md` 文件的目录。没有 `SKILL.md` 的目录会被
忽略，绝不触碰。

## 依赖检测

有些 skill 会依赖其它 skill。依赖来自以下两种途径之一：

1. **显式声明** —— `SKILL.md` frontmatter 里的 `dependencies:`（或 `depends_on:`）
   字段，例如 `dependencies: deep-research, other-skill`。
2. **路径引用** —— skill 目录下任意 `.md` 文件中对
   `~/.claude/skills/<其它 skill>/...` 的路径引用。

若某个已装备 skill 的依赖未装备，它的格子会变成橙色边框并带 ⚠️ 角标，tooltip 里
列出缺失的 skill。这只是**温和提示**，不会拦截操作。

## 状态如何存储

| 路径 | 用途 |
|------|------|
| `~/.claude/skills/` | 已装备的 skill（Claude Code 读取） |
| `~/.claude/skills-armory/` | 已卸下的 skill（首次卸下时创建） |
| `~/.claude/skill-manager-layout.json` | 仅记录格子位置（视觉布局） |

"是否装备"的真相 = skill 目录在哪个文件夹。布局文件只记住格子位置。

## 配置

`server.py` 顶部的常量：

- `NUM_SLOTS` —— 装备格数量（默认 `12`）。
- `PORT` —— 服务端口（默认 `8777`）。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
