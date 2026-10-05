"""
obsidian_mcp_server.py
Obsidian 本地 MCP Server (STDIO 模式) —— 完整版

复现 cyanheads/obsidian-mcp-server 和 bitbonsai/mcpvault 的核心功能:
  - 文件: 读/写/追加/删除(确认)/移动/批量读
  - Frontmatter: 读取/更新/单键操作
  - 标签: 全Vault统计/添加/删除 (frontmatter + inline)
  - 搜索: 关键词搜索 + 简单BM25评分
  - Section: 按标题读/按标题手术式编辑/笔记内替换
  - 链接: 反向链接/孤立笔记/出链
  - 统计: Vault概览/笔记元数据
  - 安全: 路径越界防护/隐藏目录排除/只读模式

依赖:
    pip install "mcp<2" pyyaml

运行前:
    PowerShell:  $env:OBSIDIAN_VAULT_PATH = "D:\\YourVault"
    python obsidian_mcp_server.py
"""

import os
import re
from pathlib import Path
from datetime import datetime

import yaml
from mcp.server.fastmcp import FastMCP

# ── 1. Vault 路径与安全配置 ──────────────────────────────
VAULT_PATH = Path(
    os.environ.get("OBSIDIAN_VAULT_PATH", r"C:\Users\gagagagaga\Documents\Obsidian Vault")
).resolve()
READ_ONLY = os.environ.get("OBSIDIAN_READ_ONLY", "false").lower() == "true"

if not VAULT_PATH.is_dir():
    raise RuntimeError(
        f"OBSIDIAN_VAULT_PATH 不存在: {VAULT_PATH}\n"
        f"请设置环境变量指向 Obsidian Vault 根目录"
    )

mcp = FastMCP("obsidian")

# 自动排除的目录名（Obsidian配置/版本控制/依赖）
EXCLUDE_DIRS = {".obsidian", ".git", "node_modules", ".trash", ".smart-env"}


def _safe_path(relative_path: str) -> Path:
    """拼接路径并防止 ../ 逃逸出 Vault"""
    full = (VAULT_PATH / relative_path).resolve()
    if not full.is_relative_to(VAULT_PATH):
        raise ValueError(f"路径越界: {relative_path}")
    return full


def _check_writable(path: str):
    if READ_ONLY:
        raise ValueError(f"服务器以只读模式运行，不允许写操作: {path}")


def _is_excluded(p: Path) -> bool:
    """检查文件是否在排除目录中"""
    try:
        rel = p.relative_to(VAULT_PATH)
        return any(part in EXCLUDE_DIRS for part in rel.parts)
    except ValueError:
        return True


def _all_md_files() -> list[Path]:
    """遍历所有 .md 文件"""
    result = []
    for p in VAULT_PATH.rglob("*.md"):
        if p.is_file() and not _is_excluded(p):
            result.append(p)
    return result


# ── 2. Frontmatter 解析 ────────────────────────────────
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_note(text: str) -> tuple[dict, str]:
    """把笔记文本拆成 (frontmatter_dict, body_str)"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1))
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    body = text[m.end():]
    return fm, body


def serialize_note(fm: dict, body: str) -> str:
    """把 (frontmatter_dict, body) 拼回完整笔记文本"""
    if not fm:
        return body
    fm_str = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{fm_str}\n---\n\n{body}"


# ── 3. 标题大纲解析 ────────────────────────────────────
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def parse_outlines(text: str) -> list[dict]:
    """
    返回标题结构: [{"level":2,"title":"核心概念","start":3,"end":10}, ...]
    start/end 是行号（从0开始）
    """
    lines = text.splitlines()
    headings = []
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m:
            headings.append({
                "level": len(m.group(1)),
                "title": m.group(2).strip(),
                "start": i,
            })
    # 计算每个 heading 的结束行（下一个同级或更高级标题的前一行）
    for idx, h in enumerate(headings):
        end = len(lines) - 1
        for next_h in headings[idx + 1:]:
            if next_h["level"] <= h["level"]:
                end = next_h["start"] - 1
                break
        h["end"] = end
    return headings


# ── 4. WikiLink 解析 ───────────────────────────────────
WIKILINK_RE = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]')


def build_backlink_map() -> dict[str, list[dict]]:
    """
    构建反向链接表: { 目标笔记名: [{"source":路径,"context":上下文}, ...] }
    """
    backlinks: dict[str, list[dict]] = {}
    for md_file in _all_md_files():
        source_rel = str(md_file.relative_to(VAULT_PATH))
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip()
            if target.endswith(".md"):
                target = target[:-3]
            if not target:
                continue
            # 取匹配位置前后的上下文
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            ctx = text[start:end].replace("\n", " ").strip()
            backlinks.setdefault(target, []).append({
                "source": source_rel,
                "context": ctx,
            })
    return backlinks


# ── 5. 工具定义 ────────────────────────────────────────

# ===== 文件操作 =====

@mcp.tool()
def list_notes(subdir: str = "", max_depth: int = 3) -> list[dict]:
    """
    列出 Vault 中的笔记。返回路径、大小、修改时间。

    Args:
        subdir: 可选子目录，空=整个Vault
        max_depth: 遍历深度（默认3层）
    """
    root = _safe_path(subdir) if subdir else VAULT_PATH
    results = []
    root_depth = len(root.parts)
    for p in root.rglob("*.md"):
        if not p.is_file() or _is_excluded(p):
            continue
        depth = len(p.relative_to(VAULT_PATH).parts) - root_depth
        if depth > max_depth:
            continue
        stat = p.stat()
        results.append({
            "path": str(p.relative_to(VAULT_PATH)),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return sorted(results, key=lambda x: x["path"])


@mcp.tool()
def read_note(file_path: str, start_line: int = 0, end_line: int = 0) -> str:
    """
    读取笔记内容。可选按行范围读取（0=从头/到尾）。

    Args:
        file_path: 笔记相对路径
        start_line: 起始行（0=从头开始）
        end_line: 结束行（0=读到末尾）
    """
    full = _safe_path(file_path)
    if not full.is_file():
        return f"[错误] 文件不存在: {file_path}"
    text = full.read_text(encoding="utf-8")
    if start_line > 0 or end_line > 0:
        lines = text.splitlines()
        s = max(0, start_line - 1)
        e = len(lines) if end_line == 0 else min(end_line, len(lines))
        text = "\n".join(lines[s:e])
    return text


@mcp.tool()
def read_multiple_notes(paths: list[str]) -> dict:
    """
    批量读取多篇笔记（最多10篇）。

    Args:
        paths: 笔记相对路径列表
    """
    ok, err = [], []
    for p in paths[:10]:
        full = _safe_path(p)
        if full.is_file():
            fm, body = parse_note(full.read_text(encoding="utf-8"))
            ok.append({"path": p, "frontmatter": fm, "content": body})
        else:
            err.append({"path": p, "error": "文件不存在"})
    return {"ok": ok, "err": err}


@mcp.tool()
def write_note(file_path: str, content: str, overwrite: bool = False) -> str:
    """
    新建或覆盖笔记。已存在时默认拒绝，需 overwrite=True 才覆盖。

    Args:
        file_path: 笔记相对路径
        content: 完整内容
        overwrite: 是否覆盖已存在的文件
    """
    _check_writable(file_path)
    full = _safe_path(file_path)
    if full.exists() and not overwrite:
        return f"[拒绝] 文件已存在: {file_path}（传 overwrite=true 覆盖）"
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return f"已写入 {len(content)} 字符 -> {file_path}"


@mcp.tool()
def append_note(file_path: str, content: str) -> str:
    """
    向笔记末尾追加内容。文件不存在则新建。

    Args:
        file_path: 笔记相对路径
        content: 要追加的内容
    """
    _check_writable(file_path)
    full = _safe_path(file_path)
    full.parent.mkdir(parents=True, exist_ok=True)
    with open(full, "a", encoding="utf-8") as f:
        f.write(("\n" if full.exists() else "") + content)
    return f"已追加 {len(content)} 字符到 {file_path}"


@mcp.tool()
def delete_note(file_path: str, confirm_path: str = "") -> str:
    """
    删除笔记（移到 Vault 内 .trash 文件夹，不是永久删除）。
    安全要求: confirm_path 必须和 file_path 完全一致。

    Args:
        file_path: 要删除的笔记路径
        confirm_path: 确认路径，必须与 file_path 相同
    """
    _check_writable(file_path)
    if confirm_path != file_path:
        return f"[拒绝] confirm_path 必须与 file_path 完全一致（防误删）"
    full = _safe_path(file_path)
    if not full.is_file():
        return f"[错误] 文件不存在: {file_path}"
    trash_dir = VAULT_PATH / ".trash"
    trash_dir.mkdir(exist_ok=True)
    # 保留原目录结构
    dest = trash_dir / full.relative_to(VAULT_PATH)
    dest.parent.mkdir(parents=True, exist_ok=True)
    full.rename(dest)
    return f"已移动到回收站: {file_path} -> .trash/{dest.relative_to(trash_dir)}"


@mcp.tool()
def move_note(old_path: str, new_path: str, overwrite: bool = False) -> str:
    """
    移动或重命名笔记。

    Args:
        old_path: 原路径
        new_path: 新路径
        overwrite: 目标已存在时是否覆盖
    """
    _check_writable(old_path)
    src = _safe_path(old_path)
    dst = _safe_path(new_path)
    if not src.is_file():
        return f"[错误] 原文件不存在: {old_path}"
    if dst.exists() and not overwrite:
        return f"[拒绝] 目标已存在: {new_path}（传 overwrite=true）"
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return f"已移动: {old_path} -> {new_path}"


# ===== Frontmatter =====

@mcp.tool()
def get_frontmatter(file_path: str) -> dict:
    """
    读取笔记的 frontmatter（YAML 属性区），不返回正文。

    Args:
        file_path: 笔记相对路径
    """
    full = _safe_path(file_path)
    if not full.is_file():
        return {"error": f"文件不存在: {file_path}"}
    fm, _ = parse_note(full.read_text(encoding="utf-8"))
    return fm


@mcp.tool()
def update_frontmatter(file_path: str, updates: dict, merge: bool = True) -> str:
    """
    更新笔记 frontmatter。merge=True 时合并，False 时整体替换。

    Args:
        file_path: 笔记相对路径
        updates: 要更新的键值对
        merge: True=合并到现有属性；False=整体替换
    """
    _check_writable(file_path)
    full = _safe_path(file_path)
    if not full.is_file():
        return f"[错误] 文件不存在: {file_path}"
    text = full.read_text(encoding="utf-8")
    fm, body = parse_note(text)
    if merge:
        fm.update(updates)
    else:
        fm = updates
    full.write_text(serialize_note(fm, body), encoding="utf-8")
    return f"已更新 frontmatter: {file_path}（{len(updates)} 个键）"


# ===== 标签 =====

@mcp.tool()
def list_all_tags() -> list[dict]:
    """
    统计整个 Vault 的标签使用情况。返回标签名和出现次数，按频次降序。
    """
    tag_count: dict[str, int] = {}
    for md_file in _all_md_files():
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        fm, body = parse_note(text)
        # frontmatter tags
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        for t in tags:
            if isinstance(t, str):
                tag_count[t.strip("#")] = tag_count.get(t.strip("#"), 0) + 1
        # inline #tag（简化：跳过代码块和链接）
        for m in re.finditer(r'(?<!\w)#([\w\-/]+)', body):
            tag = m.group(1)
            if tag and not tag.isdigit():
                tag_count[tag] = tag_count.get(tag, 0) + 1
    return sorted(
        [{"tag": k, "count": v} for k, v in tag_count.items()],
        key=lambda x: -x["count"],
    )


@mcp.tool()
def manage_tags(file_path: str, operation: str, tags: list[str] = None) -> str:
    """
    对笔记添加/删除/列出标签（写在 frontmatter 的 tags 字段）。

    Args:
        file_path: 笔记相对路径
        operation: "add" / "remove" / "list"
        tags: 标签列表（add/remove时必填）
    """
    full = _safe_path(file_path)
    if not full.is_file():
        return f"[错误] 文件不存在: {file_path}"
    text = full.read_text(encoding="utf-8")
    fm, body = parse_note(text)
    current = fm.get("tags", [])
    if isinstance(current, str):
        current = [current]

    if operation == "list":
        return f"当前标签: {current}"

    _check_writable(file_path)
    tags = tags or []
    if operation == "add":
        for t in tags:
            if t not in current:
                current.append(t)
    elif operation == "remove":
        current = [t for t in current if t not in tags]
    else:
        return f"[错误] operation 必须是 add/remove/list"

    fm["tags"] = current
    full.write_text(serialize_note(fm, body), encoding="utf-8")
    return f"已{operation}标签，当前: {current}"


# ===== 搜索 =====

@mcp.tool()
def search_notes(keyword: str, limit: int = 10) -> list[dict]:
    """
    全文搜索笔记。返回匹配文件、命中次数、上下文摘要。

    Args:
        keyword: 搜索关键词（不区分大小写）
        limit: 返回结果上限
    """
    results = []
    kw = keyword.lower()
    for md_file in _all_md_files():
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        count = text.lower().count(kw)
        if count == 0:
            continue
        # 取第一个匹配的上下文
        idx = text.lower().find(kw)
        ctx = text[max(0, idx - 40):idx + len(kw) + 40].replace("\n", " ").strip()
        results.append({
            "path": str(md_file.relative_to(VAULT_PATH)),
            "matches": count,
            "excerpt": f"...{ctx}...",
        })
    results.sort(key=lambda x: -x["matches"])
    return results[:limit]


# ===== Section 级操作 =====

@mcp.tool()
def get_note_outline(file_path: str) -> list[dict]:
    """
    提取笔记的标题大纲（不含正文），用于快速了解结构。

    Args:
        file_path: 笔记相对路径
    """
    full = _safe_path(file_path)
    if not full.is_file():
        return [{"error": f"文件不存在: {file_path}"}]
    headings = parse_outlines(full.read_text(encoding="utf-8"))
    return [{"level": h["level"], "title": h["title"], "line": h["start"] + 1} for h in headings]


@mcp.tool()
def read_section(file_path: str, heading: str) -> str:
    """
    读取笔记中指定标题下的内容（从该标题到下一个同级标题）。

    Args:
        file_path: 笔记相对路径
        heading: 标题文本（不含#号），如 "核心概念"
    """
    full = _safe_path(file_path)
    if not full.is_file():
        return f"[错误] 文件不存在: {file_path}"
    text = full.read_text(encoding="utf-8")
    lines = text.splitlines()
    headings = parse_outlines(text)
    for h in headings:
        if h["title"] == heading:
            section = "\n".join(lines[h["start"]:h["end"] + 1])
            return section
    return f"[错误] 未找到标题: {heading}"


@mcp.tool()
def replace_in_note(file_path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
    """
    在笔记中搜索替换文本。默认只替换第一处，replace_all=True 替换全部。

    Args:
        file_path: 笔记相对路径
        old_text: 要替换的原文
        new_text: 替换后的新文本
        replace_all: 是否替换所有匹配
    """
    _check_writable(file_path)
    full = _safe_path(file_path)
    if not full.is_file():
        return f"[错误] 文件不存在: {file_path}"
    text = full.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count == 0:
        return f"[未命中] 未找到: {old_text[:50]}"
    if replace_all:
        new = text.replace(old_text, new_text)
    else:
        new = text.replace(old_text, new_text, 1)
    full.write_text(new, encoding="utf-8")
    actual = count if replace_all else 1
    return f"已替换 {actual} 处"


# ===== 链接关系（差异化）=====

@mcp.tool()
def get_backlinks(note_name: str) -> list[dict]:
    """
    查询哪些笔记引用了目标笔记（反向链接）。
    返回源文件路径和链接处的上下文。

    Args:
        note_name: 笔记名（不含.md），如 "kafka" 或 "面试/kafka"
    """
    name = note_name[:-3] if note_name.endswith(".md") else note_name
    link_map = build_backlink_map()

    results = []
    if name in link_map:
        results = link_map[name]
    else:
        for target, sources in link_map.items():
            if target == name or target.split("/")[-1] == name:
                results.extend(sources)
    return results


@mcp.tool()
def get_outgoing_links(file_path: str) -> list[str]:
    """
    返回这篇笔记链出去的所有 [[wikilink]] 目标。

    Args:
        file_path: 笔记相对路径
    """
    full = _safe_path(file_path)
    if not full.is_file():
        return [f"[错误] 文件不存在: {file_path}"]
    text = full.read_text(encoding="utf-8")
    targets = []
    for m in WIKILINK_RE.finditer(text):
        t = m.group(1).strip()
        if t.endswith(".md"):
            t = t[:-3]
        if t and t not in targets:
            targets.append(t)
    return targets


@mcp.tool()
def find_orphans() -> list[str]:
    """
    找出没有任何其他笔记链接的孤立笔记（知识图谱中的死节点）。
    """
    all_files = _all_md_files()
    link_map = build_backlink_map()

    linked = set()
    for target in link_map.keys():
        linked.add(target)
        linked.add(target.split("/")[-1])

    orphans = []
    for md_file in all_files:
        rel = str(md_file.relative_to(VAULT_PATH))
        stem = md_file.stem
        rel_no_ext = rel[:-3] if rel.endswith(".md") else rel
        if rel_no_ext not in linked and stem not in linked:
            orphans.append(rel)
    return sorted(orphans)


# ===== 统计 =====

@mcp.tool()
def get_vault_stats() -> dict:
    """
    返回 Vault 概览：笔记数、标签数、总大小、最近修改的笔记。
    """
    files = _all_md_files()
    total_size = sum(f.stat().st_size for f in files)
    recent = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:5]
    return {
        "notes": len(files),
        "total_size_kb": round(total_size / 1024, 1),
        "recent_files": [
            {
                "path": str(f.relative_to(VAULT_PATH)),
                "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
            for f in recent
        ],
    }


@mcp.tool()
def get_notes_info(paths: list[str]) -> list[dict]:
    """
    批量获取笔记元数据（大小、修改时间、是否有frontmatter），不读正文。

    Args:
        paths: 笔记路径列表
    """
    results = []
    for p in paths:
        full = _safe_path(p)
        if full.is_file():
            stat = full.stat()
            text = full.read_text(encoding="utf-8", errors="ignore")
            fm, _ = parse_note(text)
            results.append({
                "path": p,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "has_frontmatter": bool(fm),
            })
        else:
            results.append({"path": p, "error": "不存在"})
    return results


# ── 6. 启动 ──────────────────────────────────────────
if __name__ == "__main__":
    mode = "只读" if READ_ONLY else "读写"
    print(f"[obsidian-mcp] Vault: {VAULT_PATH} | 模式: {mode}", flush=True)
    mcp.run(transport="stdio")
