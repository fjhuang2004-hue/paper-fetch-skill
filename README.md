# Paper Fetch Skill

> Fetch papers as agent-ready markdown — DOI/URL/title in, structured full text out. CLI · MCP · Skill.

**Paper Fetch Skill** —— 已知论文的 AI 阅读层。
你有 DOI、URL 或标题；它返回结构化元数据 + 干净 Markdown 全文 + 图表资源，直接喂给 Codex / Claude Code / Gemini CLI / 任意 MCP host。
不绕付费墙，只在你本就有访问权限的地方，把 AI 从「只能读摘要」升级到「读全文」。

如果觉得有帮助，欢迎 star⭐ 支持！

## 🙁 AI agent 读论文的痛点

1. 你有权限获取全文，但 AI 没有权限，AI 只能读到摘要。
2. PDF 无法正确解析文字、图片，agent 理解效果不如 markdown。
3. 文章 html 有很多无关的网页信息，给 agent 造成语义负担。
4. 文章 html 中的图片 agent 读不到。


## 😍 这个项目做什么

✅这个项目把这些问题收敛到一个工具层：
1. 当你有全文获取权限时，让 AI 也能获取全文，而不仅是摘要。
2. 输入 n 篇已知论文，抓取 AI 更容易理解的 markdown 版本，为后续知识库构建做好干净的数据基础。

✅项目提供三个主要入口：

1. `paper-fetch`：命令行工具，适合手动大规模快速抓取文献。
2. `paper-fetch-mcp`：stdio MCP server，适合接入 Codex、Claude Code、Gemini CLI 等支持 MCP 的 host。
3. `skills/paper-fetch-skill/`：静态 agent skill，告诉 agent 什么时候应该调用论文抓取工具。

核心能力：

- 支持 DOI、URL 和标题查询。
- 输出结构化论文元数据、正文 Markdown、引用信息和本地缓存资源。
- 支持常见 provider 路由，包括 Crossref、arXiv、Elsevier、Springer、Wiley、Science、PNAS、IEEE 和 Copernicus。
- 在无法取得全文时返回带 warning 的 abstract-only 或 metadata-only 结果。

项目边界：

- 不做主题检索、文献推荐或综述生成。
- 不绕过付费墙或访问授权；可用性取决于 provider、凭据和本机运行环境。
- Wiley、Science、PNAS、AMS 的浏览器路径统一使用 CloakBrowser；IEEE 路线不需要额外 API key，但全文可用性取决于 IEEE Xplore 对当前环境的合法访问上下文。

## 效果展示

agent 安装 skill 后，可以识别 `paper-fetch-skill` 的适用边界，并在抓取前确认是否保存全文和图表资源。

![agent 识别 paper-fetch-skill 能力范围](figures/agent-skill-overview.png)

以下示例来自 `figures/` 中的真实开放抓取产物。

### Nature 示例

- 论文：Towards end-to-end automation of AI research
- DOI：`10.1038/s41586-026-10265-5`
- 来源：Springer/Nature HTML full text
- 许可：[`CC BY 4.0`](https://creativecommons.org/licenses/by/4.0)
- Markdown 全文：[`towards-end-to-end-automation-of-ai-research.md`](figures/towards-end-to-end-automation-of-ai-research.md)

![Nature 论文抓取结果](figures/nature-oa-fetch-result.png)

### Science Advances 示例

- 论文：Deforestation-induced runoff changes dominated by forest-climate feedbacks
- DOI：`10.1126/sciadv.adp3964`
- 来源：Science Advances / Science provider
- Markdown 全文：[`deforestation-induced-runoff-changes-dominated-by-forest-climate-feedbacks.md`](figures/deforestation-induced-runoff-changes-dominated-by-forest-climate-feedbacks.md)

![Science Advances 论文抓取结果](figures/science-fetch-result.png)

## 快速安装

### 离线安装（推荐）

离线 release asset 包含 4 个 Linux ABI tarball 和 1 个 Windows x86_64 安装器：

```text
paper-fetch-skill-offline-linux-x86_64-cp311.tar.gz
paper-fetch-skill-offline-linux-x86_64-cp312.tar.gz
paper-fetch-skill-offline-linux-x86_64-cp313.tar.gz
paper-fetch-skill-offline-linux-x86_64-cp314.tar.gz
paper-fetch-skill-windows-x86_64-setup.exe
```

#### **I. Windows x86_64：**

**1. 下载安装包**

在 Releases 中下载 
```text
paper-fetch-skill-windows-x86_64-setup.exe
```

**2. 双击安装或者在本地终端运行安装程序**
```powershell
.\paper-fetch-skill-windows-x86_64-setup.exe
```

安装器默认安装到 `%LOCALAPPDATA%\PaperFetchSkill`，不要求管理员权限。会自动安装 paper-fetch CLI 工具、注册 MCP 并安装 Skill。

**3. 验证安装**

安装后新开一个 PowerShell 

```powershell
paper-fetch --help
```
如果有输出`usage: cli.py [-h] -`（后略）则安装成功

**4. 开启 Wiley / Science / PNAS / AMS 浏览器路径**

安装器会注册 CloakBrowser 默认 headless 环境。受限环境可在 `offline.env` 中设置 `CLOAKBROWSER_BINARY_PATH` 指向预装浏览器。

**5. 开启 Elsevier 获取权限**

Elsevier 官方 XML/API 和 PDF fallback 需要从 <https://dev.elsevier.com/> 申请 key，并写入安装目录下的 `offline.env`：

```powershell
notepad "$env:LOCALAPPDATA\PaperFetchSkill\offline.env"
```

**6. 刷新 agent skill**

修改 Codex / Claude Code / Gemini CLI skill 或 MCP 配置后需要重启对应 host。

**7. 常见问题**

Windows 安装器和离线安装细节见 [`docs/deployment.md`](docs/deployment.md)。


#### **II. Linux** 

**1. 下载安装包**

检查python版本
```bash
python3 --version
```


在 Releases 中选择与目标机 Python 版本的包下载。
```text
paper-fetch-skill-offline-linux-x86_64-cp311.tar.gz
paper-fetch-skill-offline-linux-x86_64-cp312.tar.gz
paper-fetch-skill-offline-linux-x86_64-cp313.tar.gz
paper-fetch-skill-offline-linux-x86_64-cp314.tar.gz
```

Ubuntu 24.04 系统默认 Python 版本 3.12，Ubuntu 26.04 为 3.14。

解压后执行：

```bash
./install-offline.sh --preset=headless --no-user-config
source ./activate-offline.sh
```

WSLg 或桌面显示环境可改用：

```bash
./install-offline.sh --preset=wslg --no-user-config
```

#### **III. 更新和卸载**

**更新**

Windows 下载新版 `paper-fetch-skill-windows-x86_64-setup.exe` 后直接运行覆盖安装。安装器会保留 `%LOCALAPPDATA%\PaperFetchSkill\offline.env` 中用户写入的 API key，只刷新受管理的运行时配置、PATH、Skill 和 MCP 注册。

Linux 下载与目标机 Python 版本匹配的新版 tarball，解压到新目录。若希望更新时不改动旧 `offline.env`，使用 `--reuse-env-file` 指向现有文件，再使用原来的 preset / flags 重新安装：

```bash
cd /path/to/new-bundle
./install-offline.sh --preset=headless --no-user-config --reuse-env-file /path/to/old/offline.env
source ./activate-offline.sh
```

这会让新安装的 PATH / Skill / MCP 指向新目录，但不会修改被复用的 `offline.env`。如果后续要删除旧解压目录，先把 `offline.env` 放到不会被删除的位置，并把 `--reuse-env-file` 指向该位置。更新后重启 Codex / Claude Code / Gemini CLI。

**卸载**

Windows 在“设置 > 应用 > 已安装的应用”中卸载 `Paper Fetch Skill`，或运行：

```powershell
& "$env:LOCALAPPDATA\PaperFetchSkill\unins000.exe"
```

如需保留 `offline.env` 中的 API key，卸载前先备份该文件。

Linux 在原离线包解压目录运行：

```bash
./install-offline.sh --uninstall
```

该命令只清理用户级 PATH / Skill / MCP 集成，不删除解压目录、包内 `.venv/`、`offline.env` 或 `downloads/`；确认不再需要后可手动删除解压目录。

### 在线安装（不推荐，开发使用）

在仓库根目录执行：

```bash
./install.sh
```

默认会创建仓库内 `.venv`，安装 Python 包，并准备 CloakBrowser 依赖和公式后端等运行组件。

如果只想安装 Python 包和基础配置：

```bash
./install.sh --lite
```

arXiv 路径细节见 [`docs/providers.md`](docs/providers.md#arxiv)。

如果只想装进当前 Python 环境：

```bash
python3 -m pip install .
```

安装后可用命令：

```bash
paper-fetch --query "10.1186/1471-2105-11-421"
paper-fetch-mcp
```

### CLI 行为速查

`paper-fetch` 的输出与本地 artifact 参数分工如下：

- `--format markdown|json|both` 指定 stdout、`--output` 或 `--output-dir` 默认主输出文件的序列化格式，默认是 `markdown`。
- `--query-file <path>` 启用批量抓取，每行一个 DOI、URL 或标题；空行和以 `#` 开头的注释行会被忽略。批量模式不向 stdout 输出正文，而是把每篇主输出写到输出目录，并生成 JSONL 汇总。
- `--output <path>` 把这份格式化结果写到指定文件；显式 `--output -` 表示打印到终端。
- `--output-dir <dir>` 是默认主输出、Markdown、PDF fallback 来源文件和本地资产的保存目录；CLI 会在抓取前自动创建该目录，未显式传 `--output` 时，主输出会写到 `<doi>.md`、`<doi>.json` 或 `<doi>.both.json`，不再把正文打印到终端。
- `--batch-concurrency <1..8>` 控制批量并发，默认 `1`；`--batch-results <path>` 可覆盖默认的 `<output-dir>/batch-results.jsonl`。
- `--artifact-mode markdown-assets|all|none` 控制中间产物保留，CLI 默认是 `markdown-assets`：保存 Markdown、按 `--asset-profile` 保存资产，不保留 provider 原始 HTML/XML、fetch-envelope/cache JSON 或 HTTP textual cache；如果正文来自 PDF fallback，仍会保存 PDF 源文件便于溯源。
- `--artifact-mode all` 保留旧行为：provider HTML/PDF、辅助 artifact、HTTP textual cache 等调试 artifact 都可落盘。
- `--artifact-mode none` 不保存 provider artifact 或资产；显式 `--output <path>`、`--save-markdown`，以及未显式 `--output` 时由 `--output-dir` 承接的主输出仍可写文件。`--no-download` 保留兼容，但已弃用，等价于 `--artifact-mode none`。
- `--asset-profile none|body|all` 控制本地内容资产下载范围，CLI 默认是 `body`：`none` 不下载本地资产但保留 Markdown 中可解析的远程图片链接，`body` 保存正文图片/图表/公式图片，`all` 额外保存补充材料。

完整命令组合、主输出与 artifact 的区别、错误输出和 exit code 见 [`docs/cli.md`](docs/cli.md)。

例如：

```bash
paper-fetch --query "https://www.nature.com/articles/s41559-026-03039-9" \
  --output-dir ./papers
```

这会把 Markdown 写到 `./papers/<doi>.md`，不打印正文到终端，并按默认 `--asset-profile body` 保存正文图片等资产；默认不会保存 provider 原始 HTML/XML 或 JSON/cache sidecar。需要完整调试 artifact 时显式使用 `--artifact-mode all`。如果需要强制打印到终端，显式传 `--output -`。

批量抓取时先准备 query 文件：

```text
# 每行一个 DOI、URL 或标题
10.1186/1471-2105-11-421
https://www.nature.com/articles/s41559-026-03039-9
```

然后运行：

```bash
paper-fetch --query-file ./queries.txt \
  --output-dir ./papers \
  --batch-concurrency 4
```

这会把每篇 Markdown 和正文资产写到 `./papers`，并生成 `./papers/batch-results.jsonl`。单篇失败会记录到 JSONL 并继续处理后续条目。

如果只想控制格式化结果的文件路径，显式使用 `--output`：

```bash
paper-fetch --query "10.1186/1471-2105-11-421" \
  --format markdown \
  --output ./papers/article.md \
  --output-dir ./papers
```

显式 `--output <path>` 只控制主输出文件路径，不会自动创建该文件的父目录。

安装脚本结束时会提示 Elsevier 官方 API 配置入口。抓取 Elsevier 全文前，需要从 <https://dev.elsevier.com/> 申请 key，并在配置文件中填写 `ELSEVIER_API_KEY`。

### 配置文件

默认配置文件位置：

```text
~/.config/paper-fetch/.env
```

需要 API key、自定义下载目录或 User-Agent 时，可以先创建配置文件：

```bash
mkdir -p ~/.config/paper-fetch
cp .env.example ~/.config/paper-fetch/.env
```

其中 Elsevier 官方 XML/API 和 PDF fallback 至少需要从 <https://dev.elsevier.com/> 申请并配置：

```bash
ELSEVIER_API_KEY="..."
```

也可以通过环境变量显式指定：

```bash
export PAPER_FETCH_ENV_FILE=/path/to/.env
```

完整环境变量说明见 [`docs/providers.md`](docs/providers.md)。


### 接入 Codex

安装 skill 并注册 MCP server：

```bash
./scripts/install-codex-skill.sh --register-mcp
```

带配置文件注册：

```bash
./scripts/install-codex-skill.sh --register-mcp --env-file ~/.config/paper-fetch/.env
```

只安装到当前项目：

```bash
./scripts/install-codex-skill.sh --project --register-mcp
```

安装后重启 Codex，让它重新扫描 skills 和 MCP 配置。

### 接入 Claude Code

```bash
./scripts/install-claude-skill.sh --register-mcp
```

常用参数包括：

```bash
./scripts/install-claude-skill.sh --project --register-mcp
./scripts/install-claude-skill.sh --register-mcp --env-file ~/.config/paper-fetch/.env
```

### 接入 Gemini CLI

安装 skill 并注册 MCP server：

```bash
./scripts/install-gemini-skill.sh --register-mcp
```

带配置文件注册：

```bash
./scripts/install-gemini-skill.sh --register-mcp --env-file ~/.config/paper-fetch/.env
```

只安装到当前项目：

```bash
./scripts/install-gemini-skill.sh --project --register-mcp
```

如果本机没有 `gemini` CLI，脚本会安装 skill 并跳过自动 MCP 注册；安装 Gemini CLI 后可重跑同一命令。

### 手动注册 MCP

任何支持 stdio MCP 的 host 都可以直接运行：

```bash
paper-fetch-mcp
```

或：

```bash
python3 -m paper_fetch.mcp.server
```

Gemini CLI 可手动注册同一个 stdio server：

```bash
gemini mcp add paper-fetch -- python3 -m paper_fetch.mcp.server
```

WSL 下给 Codex 挂 MCP 时，推荐使用仓库包装脚本：

```bash
./scripts/run-codex-paper-fetch-mcp.sh
```

### 常用抓取参数

MCP 默认模式、`artifact_mode`、`prefer_cache`、`no_download` 和 `save_markdown` 的完整语义见 [`docs/providers.md`](docs/providers.md#mcp-download-and-markdown-save)。MCP `artifact_mode` 默认是 `markdown-assets`；`strategy.asset_profile` 支持 `none`、`body`、`all`，MCP/Python API 未显式设置时默认由 provider 决定。

### 更新

更新仓库后重新安装包和 agent 集成：

```bash
python3 -m pip install .
./scripts/install-codex-skill.sh --register-mcp
```

Claude Code 用户对应执行：

```bash
./scripts/install-claude-skill.sh --register-mcp
```

Gemini CLI 用户对应执行：

```bash
./scripts/install-gemini-skill.sh --register-mcp
```

## 文档

- [`docs/deployment.md`](docs/deployment.md)：安装、配置、MCP 注册和更新。
- [`docs/providers.md`](docs/providers.md)：provider 能力、环境变量和运行时配置。
- [`docs/README.md`](docs/README.md)：完整文档导航。
- [`docs/architecture/target-architecture.md`](docs/architecture/target-architecture.md)：架构边界和维护者视角。

## 免责声明

本项目通过公开可访问的开放获取接口、publisher 路由和用户配置的凭据获取研究论文内容。

- 获取的文献仅供个人学术研究和学习使用，不得用于商业用途。
- 请遵守所在国家/地区著作权法律法规及所在机构的知识产权政策。
- 本项目不绕过付费墙或访问授权；可用性取决于 provider、凭据和本机运行环境。
- 本项目不存储、分发或传播任何文献内容，仅协助用户定位、抓取或转换用户有权访问的论文内容。
- 使用者应对自身的文献获取和使用行为承担全部责任。

## 社区

<https://linux.do/>
