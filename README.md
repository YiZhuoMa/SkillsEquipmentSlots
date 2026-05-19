# Claude Skill Equipment

> **English** · [中文](README.zh-CN.md)

A local, Diablo-style web page for managing your personal
[Claude Code](https://docs.claude.com/en/docs/claude-code) skills. Drag a skill
into an equipment slot to **enable** it; drag it out to **disable** it.

As your collection of skills grows it gets hard to keep track of which ones are
active. This tool turns that into a game — a tome shelf on the left, an
equipment grid on the right.

## Why "equip" actually works

This is not a cosmetic toggle. Enabling / disabling physically moves the skill
folder:

- **Equipped** skills live in `~/.claude/skills/` — the directory Claude Code
  actually reads.
- **Unequipped** skills are moved to `~/.claude/skills-armory/` — a store that
  Claude ignores.
- Dragging a skill into a slot moves its folder back into `skills/`; dragging it
  out moves it to the armory.

Moving folders is used instead of symlinks because it is 100% reliable —
Claude Code is guaranteed to discover (or not discover) the skill. Nothing is
ever deleted or overwritten.

> ⚠️ Claude Code loads skills at session start. **Restart your Claude session
> for an equip / unequip to take effect.**

## Features

- 🗂 Manages personal skills in `~/.claude/skills/` only — plugin skills are
  left untouched.
- 🎮 Diablo-style dark UI with drag-and-drop equipment slots.
- 🔁 Real enable / disable by moving folders — safe, never deletes or
  overwrites.
- 🔗 **Dependency detection** — if an equipped skill depends on another skill
  that is not equipped, its slot shows a ⚠️ badge.
- 🌐 Bilingual UI — switch between English and 中文 with the top-right button.
- 🪶 A single file, zero third-party dependencies — just Python 3.

## Requirements

- Python 3.7+
- Claude Code, with at least one personal skill in `~/.claude/skills/`

## Quick start

```bash
python3 server.py
```

Then open <http://127.0.0.1:8777> in your browser.

- **Left panel** — skill tomes that are currently *unequipped*. Hover a card
  for its full description.
- **Right panel** — 12 equipment slots.
- Drag left → empty slot = **equip**. Drag a slot → left panel = **unequip**.
  Drag slot ↔ slot = rearrange / swap.

The server binds to `127.0.0.1` only; it is not exposed to the network.

## What counts as a skill

Any directory under `~/.claude/skills/` that contains a `SKILL.md` file.
Directories without a `SKILL.md` are ignored and never touched.

## Dependency detection

Some skills rely on others. A dependency is detected from either:

1. **Explicit declaration** — a `dependencies:` (or `depends_on:`) field in the
   `SKILL.md` frontmatter, e.g. `dependencies: deep-research, other-skill`.
2. **Path references** — any `.md` file inside the skill folder that references
   `~/.claude/skills/<other-skill>/...`.

If an equipped skill's dependency is not equipped, its slot is outlined in
orange with a ⚠️ badge and the tooltip lists the missing skills. This is a
hint only — it never blocks the action.

## How state is stored

| Path | Purpose |
|------|---------|
| `~/.claude/skills/` | Equipped skills (read by Claude Code) |
| `~/.claude/skills-armory/` | Unequipped skills (created on first unequip) |
| `~/.claude/skill-manager-layout.json` | Slot positions only (visual layout) |

The source of truth for "equipped or not" is which folder a skill is in. The
layout file only remembers slot positions.

## Configuration

Constants at the top of `server.py`:

- `NUM_SLOTS` — number of equipment slots (default `12`).
- `PORT` — server port (default `8777`).

## License

MIT — see [LICENSE](LICENSE).
