#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Skills 装备栏管理页面 —— 单文件本地服务。

启动:   python3 server.py
访问:   http://127.0.0.1:8777

生效机制(见 对话记录与需求设计.md):
  - 已装备的 skill 物理存放在 ~/.claude/skills/      (Claude 真正读取)
  - 卸下的 skill 移动到      ~/.claude/skills-armory/  (仓库, Claude 不读)
  - "是否装备"的真相 = 目录在哪个文件夹; layout.json 只记录格子位置(视觉)
"""

import json
import os
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------- 路径常量 ------------------------------------
HOME = os.path.expanduser("~")
SKILLS_DIR = os.path.join(HOME, ".claude", "skills")          # 已装备
ARMORY_DIR = os.path.join(HOME, ".claude", "skills-armory")   # 仓库(卸下)
LAYOUT_FILE = os.path.join(HOME, ".claude", "skill-manager-layout.json")
NUM_SLOTS = 12          # 装备栏格子数, 想收紧只改这一个数字
PORT = 8777

DIR_RE = re.compile(r"^[A-Za-z0-9._-]+$")   # 防路径穿越
# 通用依赖检测: 匹配 SKILL.md 正文里对 ~/.claude/skills/<skill>/ 的路径引用
DEP_PATH_RE = re.compile(r"\.claude/skills/([A-Za-z0-9._-]+)")

# ------------------------- SKILL.md frontmatter ----------------------------

def parse_skill_md(skill_md_path):
    """解析 SKILL.md, 返回 (name, description, explicit_deps:list, body:str)。

    - frontmatter = 开头两个 --- 之间的 YAML; body = 其后的正文。
    - explicit_deps 来自 frontmatter 的 dependencies / depends_on 字段(可选)。
    """
    name, description, explicit, body = "", "", [], ""
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return name, description, explicit, body

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return name, description, explicit, text   # 无 frontmatter, 整体当 body

    # 收集到第二个 --- 之前的 frontmatter 行; 其后为 body
    fm, body_start = [], None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        fm.append(line)
    body = "\n".join(lines[body_start:]) if body_start is not None else ""

    # 解析 key: value, 支持下一行缩进续行(多行 description)
    key, buf = None, {}
    for line in fm:
        m = re.match(r"^([A-Za-z_][\w-]*):\s?(.*)$", line)
        if m:
            key = m.group(1)
            buf[key] = m.group(2)
        elif key is not None and line.strip():
            buf[key] += " " + line.strip()

    name = _strip_quotes(buf.get("name", "").strip())
    description = _strip_quotes(buf.get("description", "").strip())
    explicit = _parse_dep_field(buf.get("dependencies", "")
                                or buf.get("depends_on", ""))
    return name, description, explicit, body


def _parse_dep_field(raw):
    """解析 frontmatter 依赖字段: 支持 'a, b' / '[a, b]' / 'a b' 等写法。"""
    raw = (raw or "").strip().strip("[]")
    if not raw:
        return []
    return [_strip_quotes(p.strip()) for p in re.split(r"[,\s]+", raw) if p.strip()]


def _strip_quotes(s):
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].strip()
    return s


def brief_of(description):
    """卡片简介: 取首句并截断。"""
    if not description:
        return "(无简介)"
    parts = re.split(r"[。.!?！？\n]", description, maxsplit=1)
    first = parts[0].strip()
    if len(first) > 48:
        first = first[:48] + "…"
    return first or "(无简介)"

# ----------------------------- 数据扫描 ------------------------------------

def scan_md_path_refs(skill_dir):
    """递归扫描 skill 目录下所有 .md 文件, 返回引用到的 skill 目录名集合。

    覆盖 SKILL.md 以及它引用的子文件(如 references/*.md), 只认对
    .claude/skills/<x>/ 的路径引用。
    """
    refs = set()
    for root, _dirs, files in os.walk(skill_dir):
        for fn in files:
            if not fn.lower().endswith(".md"):
                continue
            try:
                with open(os.path.join(root, fn), "r",
                          encoding="utf-8", errors="ignore") as f:
                    refs.update(DEP_PATH_RE.findall(f.read()))
            except OSError:
                pass
    return refs


def scan_dir(base):
    """扫描 base 下含 SKILL.md 的目录。

    返回 [{dir,name,description,brief,_explicit,_path_refs}]; 下划线字段供
    build_state 计算依赖后会被剔除, 不出现在 API 输出里。
    """
    out = []
    if not os.path.isdir(base):
        return out
    for entry in sorted(os.listdir(base)):
        d = os.path.join(base, entry)
        skill_md = os.path.join(d, "SKILL.md")
        if os.path.isdir(d) and os.path.isfile(skill_md):
            name, desc, explicit, _body = parse_skill_md(skill_md)
            out.append({
                "dir": entry,
                "name": name or entry,
                "description": desc,
                "brief": brief_of(desc),
                "_explicit": explicit,
                "_path_refs": sorted(scan_md_path_refs(d)),
            })
    return out


def attach_deps(equipped, stored):
    """为每个 skill 计算 deps(依赖的其它 skill 目录名), 并剔除下划线临时字段。

    依赖 = frontmatter 显式声明 ∪ 目录下所有 .md 文件中对
    .claude/skills/<x>/ 的路径引用, 且仅保留确实存在的 skill, 排除自身。
    """
    all_names = {s["dir"] for s in equipped} | {s["dir"] for s in stored}
    for s in equipped + stored:
        found = set(s.pop("_explicit", []))
        found |= set(s.pop("_path_refs", []))
        s["deps"] = sorted(d for d in found
                           if d in all_names and d != s["dir"])


def load_layout():
    try:
        with open(LAYOUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): v for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_layout(layout):
    with open(LAYOUT_FILE, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)


def build_state():
    """汇总当前状态; 顺便清理 layout 中的失效条目并为无格子的已装备 skill 补位。"""
    equipped = scan_dir(SKILLS_DIR)
    stored = scan_dir(ARMORY_DIR)
    attach_deps(equipped, stored)
    equipped_dirs = {s["dir"] for s in equipped}

    layout = load_layout()
    # 1) 去掉指向已不存在/已卸下 skill 的格子
    layout = {slot: d for slot, d in layout.items()
              if d in equipped_dirs and 0 <= int(slot) < NUM_SLOTS}
    # 2) 去重: 同一个 dir 只保留一个格子
    seen, cleaned = set(), {}
    for slot in sorted(layout, key=lambda x: int(x)):
        d = layout[slot]
        if d not in seen:
            seen.add(d)
            cleaned[slot] = d
    layout = cleaned
    # 3) 为没有格子的已装备 skill 自动补到最靠前的空格
    placed = set(layout.values())
    used_slots = {int(s) for s in layout}
    for s in equipped:
        if s["dir"] not in placed:
            for i in range(NUM_SLOTS):
                if i not in used_slots:
                    layout[str(i)] = s["dir"]
                    used_slots.add(i)
                    break

    save_layout(layout)
    return {
        "equipped": equipped,
        "stored": stored,
        "layout": layout,
        "num_slots": NUM_SLOTS,
    }

# ----------------------------- 文件操作 ------------------------------------

class ApiError(Exception):
    """message 为错误码(见前端 I18N.errors), 前端按当前语言翻译。"""


def _valid_dir(name):
    if not name or not DIR_RE.match(name):
        raise ApiError("bad_dir")
    return name


def do_equip(dir_name, slot):
    """armory -> skills, 并占用指定格子。"""
    dir_name = _valid_dir(dir_name)
    try:
        slot = int(slot)
    except (TypeError, ValueError):
        raise ApiError("bad_slot")
    if not (0 <= slot < NUM_SLOTS):
        raise ApiError("slot_out_of_range")

    src = os.path.join(ARMORY_DIR, dir_name)
    dst = os.path.join(SKILLS_DIR, dir_name)
    if not os.path.isfile(os.path.join(src, "SKILL.md")):
        raise ApiError("not_in_armory")
    if os.path.exists(dst):
        raise ApiError("dst_exists")

    layout = load_layout()
    layout = {s: d for s, d in layout.items() if d != dir_name}
    if str(slot) in layout:
        raise ApiError("slot_taken")

    shutil.move(src, dst)
    layout[str(slot)] = dir_name
    save_layout(layout)


def do_unequip(dir_name):
    """skills -> armory。"""
    dir_name = _valid_dir(dir_name)
    src = os.path.join(SKILLS_DIR, dir_name)
    dst = os.path.join(ARMORY_DIR, dir_name)
    if not os.path.isfile(os.path.join(src, "SKILL.md")):
        raise ApiError("not_in_equip")
    os.makedirs(ARMORY_DIR, exist_ok=True)   # 首次卸下时才创建仓库
    if os.path.exists(dst):
        raise ApiError("armory_name_clash")

    shutil.move(src, dst)
    layout = load_layout()
    layout = {s: d for s, d in layout.items() if d != dir_name}
    save_layout(layout)


def do_arrange(new_layout):
    """仅调整格子位置, 不动文件。"""
    if not isinstance(new_layout, dict):
        raise ApiError("bad_layout")
    equipped_dirs = {s["dir"] for s in scan_dir(SKILLS_DIR)}
    cleaned, seen = {}, set()
    for slot, d in new_layout.items():
        try:
            si = int(slot)
        except (TypeError, ValueError):
            continue
        if not (0 <= si < NUM_SLOTS):
            continue
        if d in equipped_dirs and d not in seen:
            cleaned[str(si)] = d
            seen.add(d)
    save_layout(cleaned)

# ----------------------------- HTTP 处理 -----------------------------------

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):   # 安静一点
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(PAGE_HTML)
        elif self.path == "/api/skills":
            try:
                self._send_json(build_state())
            except Exception as e:                       # noqa: BLE001
                self._send_json({"error": "server_error", "detail": str(e)}, 500)
        else:
            self._send_json({"error": "not_found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            self._send_json({"error": "bad_json"}, 400)
            return

        try:
            if self.path == "/api/equip":
                do_equip(payload.get("dir"), payload.get("slot"))
            elif self.path == "/api/unequip":
                do_unequip(payload.get("dir"))
            elif self.path == "/api/arrange":
                do_arrange(payload.get("layout"))
            else:
                self._send_json({"error": "not_found"}, 404)
                return
        except ApiError as e:
            self._send_json({"error": str(e)}, 400)
            return
        except Exception as e:                           # noqa: BLE001
            self._send_json({"error": "server_error", "detail": str(e)}, 500)
            return

        self._send_json(build_state())

# ----------------------------- 前端页面 ------------------------------------

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Skill Equipment</title>
<style>
  :root{
    --bg:#0d0a07; --panel:#1a140d; --panel2:#221a10;
    --gold:#c8a24a; --gold-dim:#7a6230; --gold-bright:#f0d488;
    --text:#d8c9a8; --text-dim:#8a7a5a; --red:#a33;
  }
  *{box-sizing:border-box;}
  body{
    margin:0; background:var(--bg);
    background-image:radial-gradient(ellipse at top,#1c140c 0%,#0d0a07 70%);
    color:var(--text);
    font-family:"Noto Serif CJK SC","Noto Serif SC",Georgia,"Songti SC",serif;
    min-height:100vh;
  }
  h1{
    text-align:center; color:var(--gold-bright);
    font-size:26px; letter-spacing:4px; margin:18px 0 4px;
    text-shadow:0 0 12px rgba(200,162,74,.4);
  }
  .sub{text-align:center; color:var(--text-dim); font-size:13px; margin-bottom:14px;}
  .wrap{
    display:flex; gap:22px; max-width:1080px; margin:0 auto; padding:0 22px 40px;
    align-items:flex-start;
  }
  .col{
    background:var(--panel);
    border:2px solid var(--gold-dim);
    border-radius:8px;
    box-shadow:0 0 24px rgba(0,0,0,.6),inset 0 0 30px rgba(0,0,0,.5);
  }
  .col-head{
    padding:10px 16px; color:var(--gold-bright);
    font-size:16px; letter-spacing:2px;
    border-bottom:1px solid var(--gold-dim);
    text-shadow:0 0 8px rgba(200,162,74,.3);
  }
  /* 左: 典籍仓库 */
  #armory{flex:1; min-width:340px;}
  #armory-list{padding:12px; display:flex; flex-direction:column; gap:10px;
    min-height:120px; max-height:70vh; overflow-y:auto;}
  .card{
    background:linear-gradient(180deg,var(--panel2),#160f08);
    border:1px solid var(--gold-dim);
    border-radius:6px; padding:9px 11px; cursor:grab;
    display:flex; gap:10px; align-items:center;
    transition:border-color .15s,box-shadow .15s,transform .05s;
  }
  .card:hover{border-color:var(--gold); box-shadow:0 0 12px rgba(200,162,74,.35);}
  .card:active{cursor:grabbing; transform:scale(.98);}
  .card .icon{font-size:24px; width:30px; text-align:center; flex:none;}
  .card .meta{overflow:hidden;}
  .card .nm{color:var(--gold-bright); font-size:15px;}
  .card .br{color:var(--text-dim); font-size:12px; margin-top:2px;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .empty-hint{color:var(--text-dim); font-size:13px; text-align:center;
    padding:24px 8px; font-style:italic;}
  /* 右: 装备栏 */
  #equip{flex:none; width:430px;}
  #grid{
    padding:16px; display:grid; grid-template-columns:repeat(3,1fr);
    gap:14px;
  }
  .slot{
    aspect-ratio:1/1; border:2px solid var(--gold-dim); border-radius:6px;
    background:radial-gradient(ellipse at center,#1d1409,#0c0905);
    box-shadow:inset 0 0 18px rgba(0,0,0,.8);
    display:flex; flex-direction:column; align-items:center;
    justify-content:center; gap:4px; padding:6px; position:relative;
    transition:border-color .15s,box-shadow .15s;
  }
  .slot.empty::after{
    content:attr(data-label); color:#3a2f1c; font-size:13px; position:absolute;
  }
  .slot.filled{cursor:grab; border-color:var(--gold);
    box-shadow:inset 0 0 16px rgba(0,0,0,.7),0 0 14px rgba(200,162,74,.3);}
  .slot.filled:active{cursor:grabbing;}
  .slot.warn{border-color:#c87a2a;
    box-shadow:inset 0 0 16px rgba(0,0,0,.7),0 0 14px rgba(200,122,42,.45);}
  .slot.drag-over{border-color:var(--gold-bright);
    box-shadow:0 0 20px rgba(240,212,136,.6);}
  .slot .icon{font-size:32px; z-index:1;}
  .slot .nm{font-size:12px; color:var(--gold-bright); text-align:center;
    z-index:1; line-height:1.2; word-break:break-all;}
  .slot .badge{position:absolute; top:1px; right:3px; font-size:15px;
    z-index:2; pointer-events:none; filter:drop-shadow(0 0 3px #000);}
  #armory-list.drag-over{outline:2px dashed var(--gold); outline-offset:-6px;}
  /* tooltip */
  #tip{
    position:fixed; pointer-events:none; z-index:99; max-width:340px;
    background:#0a0805; border:1px solid var(--gold);
    border-radius:6px; padding:10px 12px; font-size:13px; color:var(--text);
    box-shadow:0 0 20px rgba(0,0,0,.9); display:none; line-height:1.55;
  }
  #tip .t-nm{color:var(--gold-bright); font-size:14px; margin-bottom:5px;}
  #tip .t-warn{color:#e0a050; font-size:12px; margin-top:7px;
    border-top:1px solid #4a3a1a; padding-top:6px;}
  /* toast */
  #toast{
    position:fixed; left:50%; bottom:34px; transform:translateX(-50%);
    background:#1a0d0d; border:1px solid var(--red); color:#e8b4b4;
    padding:10px 20px; border-radius:6px; font-size:14px; display:none;
    box-shadow:0 0 18px rgba(0,0,0,.8); z-index:100;
  }
  #toast.ok{border-color:var(--gold); color:var(--gold-bright);}
  .note{
    max-width:1080px; margin:0 auto 22px; padding:0 22px;
    color:var(--text-dim); font-size:12px; text-align:center; line-height:1.7;
  }
  /* 语言切换按钮 */
  #lang-btn{
    position:fixed; top:14px; right:18px; z-index:101;
    background:var(--panel2); color:var(--gold-bright);
    border:1px solid var(--gold-dim); border-radius:5px;
    padding:5px 13px; font-size:13px; cursor:pointer;
    font-family:inherit; letter-spacing:1px;
    transition:border-color .15s,box-shadow .15s;
  }
  #lang-btn:hover{border-color:var(--gold);
    box-shadow:0 0 10px rgba(200,162,74,.35);}
</style>
</head>
<body>
  <button id="lang-btn"></button>
  <h1 id="h1"></h1>
  <div class="sub" id="sub"></div>

  <div class="wrap">
    <div class="col" id="armory">
      <div class="col-head" id="armory-head"></div>
      <div id="armory-list"></div>
    </div>
    <div class="col" id="equip">
      <div class="col-head" id="equip-head"></div>
      <div id="grid"></div>
    </div>
  </div>

  <div class="note" id="note"></div>

  <div id="tip"></div>
  <div id="toast"></div>

<script>
const NUM_SLOTS_FALLBACK = 12;
let STATE = {equipped:[], stored:[], layout:{}, num_slots:NUM_SLOTS_FALLBACK};

/* ---- 中英文文案 ---- */
const I18N = {
  zh: {
    htmlLang:"zh-CN",
    title:"Claude 技能装备栏",
    h1:"⚔ 技能装备栏 ⚔",
    sub:"将左侧技能典籍拖入右侧装备栏即可启用 · 拖出即收回仓库",
    armoryHead:"📚 技能典籍（未装备）",
    equipHead:"🛡 装备栏",
    armoryEmpty:"仓库空空如也 —— 所有技能皆已装备",
    emptySlot:"空",
    note:'生效机制：装备 = 目录移入 <code>~/.claude/skills/</code>；'
       + '卸下 = 移入 <code>~/.claude/skills-armory/</code>。<br>'
       + 'Claude Code 在会话启动时加载 skill，'
       + '<b>装备 / 卸下后需重启 Claude 会话才生效</b>。',
    noDesc:"（该 skill 无 description）",
    depWarnHead:"⚠️ 依赖未装备：",
    depWarnTail:"<br>此 skill 运行时会读取它们的文件，建议一并装备。",
    depSep:"、",
    toggle:"EN",
    toastEquipped:"已装备",
    toastUnequipped:"已收回仓库",
    netErr:"网络错误：",
    errors:{
      bad_dir:"非法的目录名", bad_slot:"格子编号无效",
      slot_out_of_range:"格子编号超出范围", not_in_armory:"仓库中找不到该 skill",
      dst_exists:"目标目录已存在，已取消（不会覆盖）",
      slot_taken:"该格子已被占用", not_in_equip:"装备栏中找不到该 skill",
      armory_name_clash:"仓库中已存在同名目录，已取消（不会覆盖）",
      bad_layout:"layout 格式错误", bad_json:"请求体不是合法 JSON",
      not_found:"接口不存在", server_error:"服务器错误",
    },
  },
  en: {
    htmlLang:"en",
    title:"Claude Skill Equipment",
    h1:"⚔ Skill Equipment ⚔",
    sub:"Drag a skill tome from the left into a slot to enable it · drag it out to return it",
    armoryHead:"📚 Skill Tomes (Unequipped)",
    equipHead:"🛡 Equipment Slots",
    armoryEmpty:"The armory is empty — every skill is equipped",
    emptySlot:"empty",
    note:'How it works: equip = move the folder into <code>~/.claude/skills/</code>; '
       + 'unequip = move it into <code>~/.claude/skills-armory/</code>.<br>'
       + 'Claude Code loads skills at session start, so '
       + '<b>restart your Claude session for equip / unequip to take effect</b>.',
    noDesc:"(this skill has no description)",
    depWarnHead:"⚠️ Missing dependencies: ",
    depWarnTail:"<br>This skill reads their files at runtime — equip them together.",
    depSep:", ",
    toggle:"中",
    toastEquipped:"Equipped",
    toastUnequipped:"Returned to armory",
    netErr:"Network error: ",
    errors:{
      bad_dir:"Invalid directory name", bad_slot:"Invalid slot number",
      slot_out_of_range:"Slot number out of range",
      not_in_armory:"Skill not found in the armory",
      dst_exists:"Target directory already exists — cancelled (nothing overwritten)",
      slot_taken:"That slot is already taken",
      not_in_equip:"Skill not found in the equipment slots",
      armory_name_clash:"A folder with that name already exists in the armory — "
        + "cancelled (nothing overwritten)",
      bad_layout:"Invalid layout format", bad_json:"Request body is not valid JSON",
      not_found:"No such endpoint", server_error:"Server error",
    },
  },
};
let LANG = (localStorage.getItem("skill-armory-lang") === "zh") ? "zh" : "en";
const T = () => I18N[LANG];

const ICONS = {
  "deep-research":"🔍", "octopus-research":"🐙", "local-data-guide":"🗄️",
  "lab-to-prod":"🚀", "skill-creator":"🛠️", "superpowers":"⚡",
};
const iconOf = d => ICONS[d] || "📜";

const $ = s => document.querySelector(s);
const tip = $("#tip"), toast = $("#toast");
const esc = s => (s||"").replace(/[&<>"]/g, c=>(
  {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function showToast(msg, ok){
  toast.textContent = msg;
  toast.className = ok ? "ok" : "";
  toast.style.display = "block";
  clearTimeout(showToast._t);
  showToast._t = setTimeout(()=>toast.style.display="none", 2600);
}

/* 把服务端返回的错误码翻译成当前语言 */
function errText(data){
  const code = data && data.error;
  return (T().errors[code]) || code || "error";
}

function skillByDir(d){
  return STATE.equipped.find(s=>s.dir===d) || STATE.stored.find(s=>s.dir===d);
}

/* 该 skill 声明/引用的依赖中, 当前不在装备栏的那些 */
function missingDepsOf(skill){
  if(!skill || !skill.deps || !skill.deps.length) return [];
  const eq = new Set(STATE.equipped.map(s=>s.dir));
  return skill.deps.filter(d=>!eq.has(d));
}

/* ---- tooltip ---- */
function bindTip(el, skill){
  el.addEventListener("mousemove", e=>{
    const t = T();
    let html = '<div class="t-nm">'+iconOf(skill.dir)+' '+esc(skill.name)+'</div>'
      + esc(skill.description || t.noDesc);
    const miss = missingDepsOf(skill);
    if(miss.length){
      html += '<div class="t-warn">' + t.depWarnHead
        + miss.map(d=>esc(d)).join(t.depSep) + t.depWarnTail + '</div>';
    }
    tip.innerHTML = html;
    tip.style.display = "block";
    let x = e.clientX + 16, y = e.clientY + 16;
    const r = tip.getBoundingClientRect();
    if(x + r.width  > innerWidth)  x = e.clientX - r.width  - 16;
    if(y + r.height > innerHeight) y = e.clientY - r.height - 16;
    tip.style.left = x+"px"; tip.style.top = y+"px";
  });
  el.addEventListener("mouseleave", ()=>tip.style.display="none");
}

/* ---- render ---- */
function render(){
  tip.style.display = "none";
  const t = T();
  // 左侧: 未装备
  const list = $("#armory-list");
  list.innerHTML = "";
  if(STATE.stored.length === 0){
    list.innerHTML = '<div class="empty-hint">'+esc(t.armoryEmpty)+'</div>';
  }
  STATE.stored.forEach(s=>{
    const c = document.createElement("div");
    c.className = "card";
    c.draggable = true;
    c.innerHTML = '<div class="icon">'+iconOf(s.dir)+'</div>'
      + '<div class="meta"><div class="nm">'+esc(s.name)+'</div>'
      + '<div class="br">'+esc(s.brief)+'</div></div>';
    c.addEventListener("dragstart", e=>{
      e.dataTransfer.setData("text/plain", JSON.stringify({type:"armory", dir:s.dir}));
      tip.style.display="none";
    });
    bindTip(c, s);
    list.appendChild(c);
  });

  // 右侧: 装备栏格子
  const grid = $("#grid");
  grid.innerHTML = "";
  const N = STATE.num_slots || NUM_SLOTS_FALLBACK;
  for(let i=0;i<N;i++){
    const slot = document.createElement("div");
    slot.className = "slot";
    slot.dataset.slot = i;
    const dir = STATE.layout[String(i)];
    if(dir){
      const s = skillByDir(dir);
      slot.classList.add("filled");
      slot.draggable = true;
      slot.innerHTML = '<div class="icon">'+iconOf(dir)+'</div>'
        + '<div class="nm">'+esc(s?s.name:dir)+'</div>';
      if(missingDepsOf(s).length){
        slot.classList.add("warn");
        slot.innerHTML += '<div class="badge">⚠️</div>';
      }
      slot.addEventListener("dragstart", e=>{
        e.dataTransfer.setData("text/plain",
          JSON.stringify({type:"slot", dir:dir, slot:i}));
        tip.style.display="none";
      });
      if(s) bindTip(slot, s);
    } else {
      slot.classList.add("empty");
      slot.dataset.label = t.emptySlot;
    }
    bindSlotDnD(slot, i);
    grid.appendChild(slot);
  }
}

function bindSlotDnD(slot, idx){
  slot.addEventListener("dragover", e=>{e.preventDefault();
    slot.classList.add("drag-over");});
  slot.addEventListener("dragleave", ()=>slot.classList.remove("drag-over"));
  slot.addEventListener("drop", e=>{
    e.preventDefault(); slot.classList.remove("drag-over");
    let d; try{ d = JSON.parse(e.dataTransfer.getData("text/plain")); }catch(_){return;}
    if(d.type === "armory"){
      // 仓库 -> 格子: 装备
      if(STATE.layout[String(idx)]){ showToast(T().errors.slot_taken); return; }
      api("/api/equip", {dir:d.dir, slot:idx});
    } else if(d.type === "slot"){
      // 格子 -> 格子: 移动 / 交换
      if(d.slot === idx) return;
      const layout = Object.assign({}, STATE.layout);
      const here = layout[String(idx)];
      layout[String(idx)] = d.dir;
      if(here){ layout[String(d.slot)] = here; }
      else { delete layout[String(d.slot)]; }
      api("/api/arrange", {layout});
    }
  });
}

/* 左侧面板作为"卸下"投放区 */
const armoryList = $("#armory-list");
armoryList.addEventListener("dragover", e=>{e.preventDefault();
  armoryList.classList.add("drag-over");});
armoryList.addEventListener("dragleave", ()=>armoryList.classList.remove("drag-over"));
armoryList.addEventListener("drop", e=>{
  e.preventDefault(); armoryList.classList.remove("drag-over");
  let d; try{ d = JSON.parse(e.dataTransfer.getData("text/plain")); }catch(_){return;}
  if(d.type === "slot"){ api("/api/unequip", {dir:d.dir}); }
});

/* ---- 应用语言 ---- */
function applyLang(){
  const t = T();
  document.documentElement.lang = t.htmlLang;
  document.title = t.title;
  $("#h1").textContent = t.h1;
  $("#sub").textContent = t.sub;
  $("#armory-head").textContent = t.armoryHead;
  $("#equip-head").textContent = t.equipHead;
  $("#note").innerHTML = t.note;
  $("#lang-btn").textContent = t.toggle;
  render();
}
$("#lang-btn").addEventListener("click", ()=>{
  LANG = (LANG === "zh") ? "en" : "zh";
  localStorage.setItem("skill-armory-lang", LANG);
  applyLang();
});

/* ---- api ---- */
async function api(path, body){
  try{
    const r = await fetch(path, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body),
    });
    const data = await r.json();
    if(!r.ok || data.error){ showToast(errText(data)); }
    else {
      STATE = data; render();
      if(path==="/api/equip")   showToast(T().toastEquipped, true);
      if(path==="/api/unequip") showToast(T().toastUnequipped, true);
    }
  }catch(err){ showToast(T().netErr + err); }
}

async function load(){
  try{
    const r = await fetch("/api/skills");
    STATE = await r.json();
    if(STATE.error){ showToast(errText(STATE)); return; }
    render();
  }catch(err){ showToast(T().netErr + err); }
}

applyLang();
load();
</script>
</body>
</html>
"""

# ----------------------------- 入口 ----------------------------------------

def main():
    if not os.path.isdir(SKILLS_DIR):
        print("Error: skills directory not found: %s" % SKILLS_DIR)
        return
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("Skill Equipment is running ->  http://127.0.0.1:%d" % PORT)
    print("  equipped (read by Claude): %s" % SKILLS_DIR)
    print("  armory (unequipped store): %s  [created on first unequip]" % ARMORY_DIR)
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
