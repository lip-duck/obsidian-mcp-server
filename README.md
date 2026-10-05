# obsidian-mcp-server

> Obsidian 本地知识库的 MCP (Model Context Protocol) 服务器。支持**豆包、Claude Desktop、Cursor** 等所有 MCP 客户端直接读写你的笔记，Agent 自动整理、查漏补缺。

## 这是什么

一个跑在你电脑本地的 Python 服务。豆包（或其他 MCP 客户端）通过 STDIO 协议启动它，它就能：

- 读你的笔记、写新笔记、追加内容
- 搜索全 Vault、按标题读章节
- 管理 frontmatter 和标签
- **反向链接**：自动解析 `[[wikilink]]`，查"哪些笔记引用了我"
- **孤立笔记检测**：找出知识图谱中没有任何入链的死节点

不需要 Obsidian 插件，不需要内网穿透，不需要公网服务器。笔记在你本地，服务就在你本地。

## 快速开始

### 1. 安装依赖

```bash
pip install "mcp<2" pyyaml
```

### 2. 配置豆包自定义连接器

打开豆包 PC 客户端 → 插件 → 添加自定义连接器：

| 字段 | 填什么 |
|---|---|
| 服务器名称 | `Obsidian` |
| 传输类型 | `STDIO` |
| 命令 | `python` |
| 参数 | 你的 `obsidian_mcp_server.py` 绝对路径，如 `D:\projects\obsidian-mcp-server\obsidian_mcp_server.py` |
| 环境变量 | 点「+添加」：`OBSIDIAN_VAULT_PATH` = 你的 Vault 路径，如 `C:\Users\你\Documents\我的Vault` |

保存后直接在对话里说：
- "列出我的笔记"
- "我的 Vault 有多少篇笔记？"
- "搜索包含 epoll 的笔记"
- "谁链接了 Redis 这篇笔记？"
- "找出我的孤立笔记"

### 3. Claude Desktop 配置（备选）

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "python",
      "args": ["/绝对路径/obsidian_mcp_server.py"],
      "env": {
        "OBSIDIAN_VAULT_PATH": "/绝对路径/你的Vault"
      }
    }
  }
}
```

## 工具列表（21 个）

### 文件操作
| 工具 | 说明 |
|---|---|
| `list_notes` | 列出所有笔记（含大小、修改时间） |
| `read_note` | 读取笔记，支持按行范围 |
| `read_multiple_notes` | 批量读取最多 10 篇 |
| `write_note` | 新建/覆盖笔记（已存在需确认） |
| `append_note` | 末尾追加内容 |
| `delete_note` | 删除到 `.trash`（需二次确认路径） |
| `move_note` | 移动/重命名笔记 |

### Frontmatter
| 工具 | 说明 |
|---|---|
| `get_frontmatter` | 读取 YAML 属性区 |
| `update_frontmatter` | 更新属性（merge 或整体替换） |

### 标签
| 工具 | 说明 |
|---|---|
| `list_all_tags` | 全 Vault 标签频次统计 |
| `manage_tags` | 添加/删除/列出标签 |

### 搜索
| 工具 | 说明 |
|---|---|
| `search_notes` | 全文搜索（命中次数 + 上下文摘要） |

### 章节级操作
| 工具 | 说明 |
|---|---|
| `get_note_outline` | 提取标题大纲 |
| `read_section` | 按标题读取指定章节 |
| `replace_in_note` | 笔记内搜索替换 |

### 链接关系（差异化）
| 工具 | 说明 |
|---|---|
| `get_backlinks` | **反向链接**：谁引用了这篇笔记（含上下文） |
| `get_outgoing_links` | 这篇笔记链出了哪些 |
| `find_orphans` | **孤立笔记**：没有任何入链的死节点 |

### 统计
| 工具 | 说明 |
|---|---|
| `get_vault_stats` | Vault 概览：笔记数、大小、最近修改 |
| `get_notes_info` | 批量笔记元数据 |

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `OBSIDIAN_VAULT_PATH` | Vault 根目录（必填） | 无 |
| `OBSIDIAN_READ_ONLY` | 只读模式，设为 `true` 后所有写操作拒绝 | `false` |

## 安全设计

- **路径越界防护**：所有操作限制在 Vault 内，`../` 逃逸直接拒绝
- **自动排除**：`.obsidian`、`.git`、`node_modules`、`.trash` 不扫描
- **删除进回收站**：不是永久删除，移到 `.trash/` 目录
- **二次确认**：删除时必须传入与文件路径完全一致的确认值
- **只读模式**：`OBSIDIAN_READ_ONLY=true` 时所有写工具不可用

## 与其他 Obsidian MCP 的区别

| | 本项目 | cyanheads/obsidian-mcp-server | bitbonsai/mcpvault |
|---|---|---|---|
| 语言 | **Python** | TypeScript/Bun | TypeScript/Node |
| 需要 Obsidian 运行 | 不需要 | 需要（Local REST API 插件） | 不需要 |
| 反向链接 | ✅ 原生解析 | 需写 JSONLogic 正则 | ❌ |
| 孤立笔记检测 | ✅ | ❌ | ❌ |
| 中文工具描述 | ✅ | 英文 | 英文 |
| 豆包适配文档 | ✅ | ❌ | ❌ |

## 技术栈

- Python 3.10+
- [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- PyYAML

## License

MIT
