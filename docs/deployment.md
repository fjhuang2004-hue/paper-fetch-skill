# 部署指南

这份文档解决：

- 如何安装 `paper-fetch-skill`
- 如何准备配置文件
- 如何注册 MCP server
- 如何做最小化验证和更新

这份文档不解决：

- provider 差异、路由规则和限速语义
- Wiley / Science / PNAS / AMS 的浏览器运行时细节
- 架构实现细节

provider 与环境变量说明见 [`providers.md`](providers.md)，架构说明见 [`architecture/target-architecture.md`](architecture/target-architecture.md)。

## 1. 安装 Python 包

如果目标是把本仓库的完整本地运行环境一次性准备好，推荐先使用顶层一键安装脚本：

```bash
./install.sh
```

默认行为：

- 创建仓库内 `.venv`
- 安装当前 Python 包
- 如果存在 `.env.example` 且用户配置文件还不存在，创建 `~/.config/paper-fetch/.env`
- 安装 Python 依赖、CloakBrowser 运行时依赖和外部公式后端；默认 provider-owned HTML bootstrap 使用 CloakBrowser
- 安装结束时提示 Elsevier 官方 API key 的申请入口和配置位置；抓取 Elsevier 全文前需要从 <https://dev.elsevier.com/> 申请并设置 `ELSEVIER_API_KEY`

补充说明：

- 这是在线一键安装入口：用户不需要手动准备公式后端；浏览器路径统一由 CloakBrowser runtime 负责
- 如果只想安装 Python 包和配置骨架，不准备浏览器链路，使用 `./install.sh --lite`
- 如果要装进当前 `python3` 环境而不是 `.venv`，使用 `./install.sh --system`
- arXiv 不需要本地转换器；official HTML 不可用或质量检测失败时直接进入 text-only PDF fallback
- 如果只想跳过公式 Node fallback，可使用 `--no-node`

### 离线包

离线发布支持 Linux x86_64 和 Windows x86_64。Linux 继续按 CPython ABI 提供 3.11、3.12、3.13、3.14 tarball，但 tarball 内容是预安装 runtime 包，不再复制仓库源码快照；Windows 提供一个内置 CPython 3.13 x64 的 Inno Setup 安装器：

```text
paper-fetch-skill-offline-linux-x86_64-cp311.tar.gz
paper-fetch-skill-offline-linux-x86_64-cp312.tar.gz
paper-fetch-skill-offline-linux-x86_64-cp313.tar.gz
paper-fetch-skill-offline-linux-x86_64-cp314.tar.gz
paper-fetch-skill-windows-x86_64-setup.exe
```

CI 自动发布规则：

- 推送 `v*` tag 时，CI 会先等待 `lint`、`unit`、`integration`、`package-smoke`、`offline-linux-x86-64` 和 `offline-windows-x86-64` 全部成功，再创建对应 GitHub Release。
- release job 会下载本次运行产出的 `paper-fetch-skill-*` artifacts，确认上面 5 个文件都存在且没有额外文件，然后把它们作为 release assets 上传。
- 手动运行 workflow 时，只有在 `v*` tag 上显式设置 `publish_release=true` 才会发布，确保 release tag 和本次构建产物来自同一个 commit。
- 发布使用 workflow 内置的 `GITHUB_TOKEN`，release job 单独声明 `contents: write` 和 `actions: read` 权限，不需要额外 PAT。

主包版本号同步清单：

- `pyproject.toml` 的 `[project].version` 是 Python 包和离线构建脚本读取的主版本来源。
- `src/paper_fetch/config.py` 的 `DEFAULT_USER_AGENT` 需要同步默认 `paper-fetch-skill/<version>`。
- `skills/paper-fetch-skill/references/environment.md` 不写死版本号，只指向运行时 `paper_fetch.config.DEFAULT_USER_AGENT`。
- `installer/paper-fetch-skill.iss` 的 `AppVersion` 默认值需要同步；正常 Windows 构建会从 `pyproject.toml` 传入覆盖值，但直接运行 Inno Setup 模板时会使用这里的默认值。
- `tests/unit/test_offline_install.py` 中用于离线安装测试的 runtime fixture 需要与 Linux 安装脚本的布局保持同步。
- `CHANGELOG.md` 需要新增对应版本章节。`paper-fetch-skill-formula-tools` 的 `package.json` / `package-lock.json` 是公式辅助 Node 包版本，除非单独发布该辅助包，否则不跟随 Python 主包版本。

Linux 目标机解压后运行：

```bash
./install-offline.sh --preset=headless --no-user-config
```

WSLg 或桌面显示环境可用：

```bash
./install-offline.sh --preset=wslg --no-user-config
```

Preset 选项：

- `headless` 面向服务器或无桌面环境。
- `wslg` 面向 WSLg 或桌面显示环境。

Shell rc 写入策略：

- Linux 安装脚本会使用包内 `bin/` 启动器和 `runtime/site-packages/` 已安装 Python 包，复制 Codex / Claude Code / Gemini CLI skill，并注册 MCP。
- Bash 写 `~/.bashrc`，Zsh 写 `~/.zshrc`，Fish 写 `~/.config/fish/conf.d/paper-fetch-offline.fish`。
- 无法识别 `$SHELL` 时写 `~/.profile` 并打印提示。

`activate-offline.sh` 入口：

- 安装后新开 shell，或临时执行 `source ./activate-offline.sh`。

Linux MCP 注册行为与 Windows 对齐：检测到 `codex` CLI 时执行 `codex mcp remove/add paper-fetch`，没有 CLI 或注册失败时更新 `~/.codex/config.toml` 中的 `mcp_servers.paper-fetch`；检测到 `claude` CLI 时执行 `claude mcp remove/add -s user paper-fetch`，没有 Claude CLI 时只安装 skill 并跳过 Claude MCP 注册；检测到 `gemini` CLI 时执行 `gemini mcp remove/add paper-fetch`，没有 Gemini CLI 时只安装 skill 并跳过 Gemini MCP 注册，不写 `~/.gemini/settings.json` fallback。Codex / Claude Code / Gemini CLI 需要重启后才会重新扫描 skill 和 MCP 配置。

Windows 目标机运行安装器即可：

```powershell
.\paper-fetch-skill-windows-x86_64-setup.exe
```

Windows 安装器默认安装到 `%LOCALAPPDATA%\PaperFetchSkill`，不要求管理员权限。安装器会复制运行组件，写入用户 PATH，复制 Codex / Claude Code / Gemini CLI skill，并执行基础 smoke check。检测到 `codex` CLI 时会用 `codex mcp remove/add` 注册 MCP；没有 Codex CLI 时会备份并更新 `%USERPROFILE%\.codex\config.toml` 中的 `mcp_servers.paper-fetch`。检测到 `claude` CLI 时会用 `claude mcp remove/add -s user` 注册；没有 Claude CLI 时只安装 skill 并跳过 Claude MCP 注册。检测到 `gemini` CLI 时会用 `gemini mcp remove/add` 注册；没有 Gemini CLI 时只安装 skill 并跳过 Gemini MCP 注册，不写 Gemini JSON fallback。

离线更新：

- Windows：下载新版 `paper-fetch-skill-windows-x86_64-setup.exe` 并直接运行。安装路径和 `AppId` 固定，安装器会覆盖程序文件，保留已有 `offline.env` 中用户写入的内容，只替换 `# BEGIN/END paper-fetch offline managed` 运行时块，并重新写入 PATH、skill 和 MCP 注册。
- Linux：下载与目标机 CPython ABI 匹配的新 tarball，建议解压到新目录。若希望更新时不改动旧 `offline.env`，用 `--reuse-env-file` 指向现有文件；安装脚本不会写入该文件，只会把 shell 启动文件和 Codex fallback config 中的 managed block 替换为新 bundle 的 PATH / MCP runtime 路径。

```bash
cd /path/to/new-bundle
./install-offline.sh --preset=headless --no-user-config --reuse-env-file /path/to/old-bundle/offline.env
source ./activate-offline.sh
```

被复用的 `offline.env` 可以保留旧 managed block；运行时路径会通过 shell / MCP 进程环境覆盖为新 bundle 路径，避免继续指向旧目录。如果后续要删除旧解压目录，先把 `offline.env` 移到不会被删除的位置，并把 `--reuse-env-file` 指向该位置。更新后重启 Codex / Claude Code / Gemini CLI。

离线卸载：

- Windows：在“设置 > 应用 > 已安装的应用”中卸载 `Paper Fetch Skill`，或运行 `%LOCALAPPDATA%\PaperFetchSkill\unins000.exe`。如需保留安装目录内 `offline.env` 的 API key，卸载前先备份该文件。卸载器会删除安装目录、安装器复制的 Codex / Claude Code / Gemini CLI skill、用户 PATH 中的安装目录 `bin`，并移除安装器管理的 MCP 注册；不会删除用户手写的其它 Codex / Claude / Gemini 配置。
- Linux：在原离线包解压目录运行 `./install-offline.sh --uninstall`。该路径不做 checksum、Python ABI 或 bundle asset 检查，只删除 `~/.codex/skills/paper-fetch-skill`、`~/.claude/skills/paper-fetch-skill`、`~/.gemini/skills/paper-fetch-skill`，清理 shell 启动文件和 Codex fallback config 中的 installer managed block，并通过可用的 `codex` / `claude` / `gemini` CLI 移除 MCP；不会删除解压目录、包内 `bin/`、`runtime/`、`offline.env`、`downloads/` 或用户配置目录。

离线安装约束：

- Linux Python 版本必须与包名和 `offline-manifest.json` 的 `target.python_tag` 完全匹配；例如 `cp313` 包只能用 CPython `3.13.x` 运行，避免包内已安装 runtime 的 ABI 不匹配
- Linux 安装时会把通过 `PAPER_FETCH_OFFLINE_PYTHON_BIN` / `python3` 选中的解释器路径写入 `runtime/python-bin`，后续 `bin/python`、CLI wrapper 和 MCP 都复用该解释器
- Windows 安装器固定使用包内 CPython 3.13 x64 embeddable runtime；目标机不需要预装 Python
- Linux 构建阶段用临时 wheelhouse 把项目和依赖安装进 `runtime/site-packages`，然后只打包安装后的 runtime、`bin/` 启动器、公式工具和 skill；目标机安装阶段不运行 pip，不包含源码树、`dist/` 或 `wheelhouse/`
- CloakBrowser Python 包随 Linux `runtime/site-packages` 和 Windows embedded runtime 分发；浏览器 binary 不随包分发，受限环境可预先安装并设置 `CLOAKBROWSER_BINARY_PATH`
- Linux tarball 不包含仓库源码快照和 `tests/` 目录；离线安装目标是运行已打包工具，不在目标机执行项目测试
- Linux 公式工具使用包内 `formula-tools/bin/texmath`，Windows 使用 `formula-tools/bin/texmath.exe`；目标机不编译 texmath，也不运行 `npm install`
- Linux 默认写包内 `offline.env`、生成 `activate-offline.sh`、复制 `~/.codex/skills/paper-fetch-skill`、`~/.claude/skills/paper-fetch-skill` 和 `~/.gemini/skills/paper-fetch-skill`，并把离线 CLI PATH、formula tools PATH、`PAPER_FETCH_ENV_FILE`、`PAPER_FETCH_FORMULA_TOOLS_DIR`、`CLOAKBROWSER_HEADLESS` 写入当前 shell 对应启动文件；只有显式传 `--user-config` 才会把受标记管理的运行时块合并到 `~/.config/paper-fetch/.env`
- Linux `--reuse-env-file <path>` 会把 `PAPER_FETCH_ENV_FILE` 指向现有文件且不修改该文件；其它 runtime 路径仍由新 bundle 写入 shell / MCP 环境
- Linux 写入 shell 启动文件和 Codex fallback config 时会先替换旧的受管理 block，重复安装不会重复追加；不修改 `/etc/profile`
- Windows 首次安装会写安装目录内 `offline.env`；升级安装会保留用户已有内容，只替换 `# BEGIN/END paper-fetch offline managed` 包围的运行时 block。MCP 注册环境固定指向安装目录内 `offline.env`、`downloads/`、`formula-tools/` 和包内 `runtime/Lib/site-packages/playwright/driver/node.exe`，并设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、`CLOAKBROWSER_HEADLESS=true`、`MATHML_TO_LATEX_NODE_BIN=<install-root>/runtime/Lib/site-packages/playwright/driver/node.exe`。Linux 也会在包内 Playwright Node 存在时把 `MATHML_TO_LATEX_NODE_BIN` 指向 `runtime/site-packages/playwright/driver/node`
- Windows 安装、升级或手工修改 `offline.env` 后，需要重启 Codex Desktop / Claude Code / Gemini CLI；已启动的 MCP 服务不会自动继承新写入的 env。
- Windows GUI 安装完成页会提示 Elsevier API key 申请入口和包内 `offline.env` 位置，并提供可选的 Notepad 打开项；silent 安装不会弹出该提示。离线环境抓取 Elsevier 全文前，从 <https://dev.elsevier.com/> 申请 key，并在该文件中填写 `ELSEVIER_API_KEY`
- `--preset=headless` 会在安装阶段检查 `Xvfb`；`--preset=wslg` 会检查 `DISPLAY` 或 `WAYLAND_DISPLAY`

构建离线包：

```bash
scripts/build-offline-package.sh --output-dir dist
```

Windows 构建在 PowerShell 中执行：

```powershell
.\scripts\build-offline-package-windows.ps1 -OutputDir dist
```

Linux 构建脚本会从当前 Python 推导包名 tag；例如 `PYTHON_BIN=python3.13 scripts/build-offline-package.sh` 会默认生成 `paper-fetch-skill-offline-linux-x86_64-cp313.tar.gz`。构建时会先解析 binary wheelhouse，再把项目和依赖安装进 `runtime/site-packages`，预编译 bytecode，写入 `bin/paper-fetch`、`bin/paper-fetch-mcp`、`bin/paper-fetch-install-formula-tools` 和 `bin/python` 启动器，最终 tarball 不携带源码树或 wheelhouse。Windows 构建必须在 CPython 3.13 x64 上运行，会下载官方 CPython 3.13 embeddable x64 runtime、生成 standalone staging，把 Python 包安装进 embedded runtime 后清理 staging 中的 wheel 构建产物，并通过 Inno Setup 生成 `paper-fetch-skill-windows-x86_64-setup.exe`。

安装器共享配置集中在 `installer/manifest.json`：`skill.name`、`mcp.name`、`mcp.env_keys`、managed block marker 和离线包命名都从这里读取。Linux / Windows 离线安装脚本、Windows Inno helper 和离线包构建脚本都使用该 manifest，新增 MCP 环境变量或调整 managed block 文案时应优先改这里。

验证离线包：

```bash
scripts/verify-offline-package.sh dist/paper-fetch-skill-offline-linux-x86_64-cp311.tar.gz
```

上面的验证路径按实际构建出的 `cp311`、`cp312`、`cp313` 或 `cp314` 包名替换。

验证脚本会先确认 tarball 包含 `runtime/site-packages` 和 `bin/` 启动器，且不包含源码树或 build wheelhouse；再用 guard 拦截 `curl`、`git`、`npm`、`playwright` 等命令来确认安装器没有在线下载或目标机 patch 动作，并使用临时 HOME 和 fake `codex` / `claude` / `gemini` CLI 验证 Linux shell 写入、skill 复制和 MCP remove/add 注册；随后检查 `paper-fetch --help`、`texmath --help`、`cloakbrowser` import、`paper_fetch.mcp.fetch_tool.provider_status_payload`，最后执行 `install-offline.sh --uninstall` 验证用户级集成可清理且不删除包内 `offline.env` 或 runtime。

Windows CI 在 `offline-windows-x86-64` job 中执行安装器验证：通过 `Start-Process -Wait -PassThru` silent install 并检查安装器进程退出码，失败时输出安装日志；随后验证 bundled `runtime\python.exe` import 和 `provider_status_payload()`、`bin\paper-fetch.cmd --help`、`texmath.exe --help`、CloakBrowser package smoke，并用 fake `codex` / `claude` / `gemini` CLI 验证 MCP remove/add 命令。

只需要复核 Windows 安装器时，可以手动触发 `CI` workflow 并设置 `run_offline_windows_only=true`；该模式只运行 `offline-windows-x86-64`，其它常规 job 会跳过。

### 手动安装

先把包安装到目标环境：

```bash
python3 -m pip install .
```

安装完成后，当前环境会提供这些命令：

- `paper-fetch`
- `paper-fetch-mcp`
- `paper-fetch-install-formula-tools`

## 2. 准备配置文件

默认主配置文件是：

```text
~/.config/paper-fetch/.env
```

如果你需要 provider API key、自定义下载目录或自定义 `User-Agent`，可以先这样准备：

```bash
mkdir -p ~/.config/paper-fetch
cp .env.example ~/.config/paper-fetch/.env
```

Elsevier 官方 XML/API 和 PDF fallback 至少需要从 <https://dev.elsevier.com/> 申请并配置：

```bash
ELSEVIER_API_KEY="..."
```

补充说明：

- 运行时默认读取 `platformdirs` 解析出的用户配置目录下的 `.env`；常见 Linux/XDG 布局为 `~/.config/paper-fetch/.env`
- 仓库内的 `.env` 不会自动加载
- 如果要显式指定配置文件，请设置：

```bash
PAPER_FETCH_ENV_FILE=/path/to/.env
```

完整变量说明见 [`providers.md`](providers.md)。

## 3. 可选：安装公式后端

主抓取链路不依赖外部公式后端；只有当你希望公式转换效果更好时，才需要这一步。

即使没有安装外部公式后端，运行时仍会对已经拿到的 LaTeX 做轻量 normalize，例如把 `\updelta` 这类 upright Greek 宏改成 KaTeX 常用宏，并把 `\mspace{Nmu}` 改成 `\mkernNmu`。外部后端只影响 MathML 到 LaTeX 的转换能力，不是这些 normalize 规则的开关。

### 已安装环境

如果你已经 `pip install .`，推荐直接执行：

```bash
paper-fetch-install-formula-tools
```

### 当前仓库里的 repo-local 开发

如果你只是在当前仓库里开发：

```bash
./install-formula-tools.sh
```

补充说明：

- `paper-fetch-install-formula-tools` 会把工具装到用户数据目录，更适合部署环境
- `./install-formula-tools.sh` 会把工具装到当前仓库的 `./.formula-tools/`
- 如果只想安装公式工具但跳过 Node fallback，可给仓库脚本加 `--no-node`
- 运行时可用 `PAPER_FETCH_FORMULA_TOOLS_DIR` 覆盖公式工具查找目录；默认会考虑 repo-local `.formula-tools` 和用户数据目录下的 `formula-tools`

### CI / GitHub Actions

普通 CI 的 unit suite 会验证 Elsevier display formula 的 `texmath` 输出格式。GitHub Actions 因此需要先准备 Haskell/cabal，再执行：

```bash
python -m paper_fetch.formula.install --target-dir "$PWD/.formula-tools" --no-node
./.formula-tools/bin/texmath --help >/dev/null
```

测试步骤应设置 `PAPER_FETCH_FORMULA_TOOLS_DIR=$GITHUB_WORKSPACE/.formula-tools`。这里用 `--no-node` 是为了避免安装失败后静默落到 `mathml-to-latex` fallback；如果 `texmath` 没有装好，CI 会在验证步骤直接失败。

CI 还包含 package smoke job：执行 `python -m build` 生成 sdist / wheel，然后在干净 venv 里安装 wheel，验证 `paper-fetch --help` 可运行，并确认 `paper-fetch-mcp` console script entry point 可以解析和 import。

本地清理构建、测试缓存和 rollout 日志时可以用：

```bash
scripts/clean-local-artifacts.sh --dry-run
scripts/clean-local-artifacts.sh --days 7
```

该脚本只删除 `git check-ignore` 确认为 ignored 的目标；未被 `.gitignore` 覆盖的路径会跳过。

## 4. Elsevier / Wiley / Science / PNAS / AMS / IEEE 接入入口

`elsevier` 不依赖本地浏览器链路；它只需要官方 API 凭据，并走 `官方 XML/API -> 官方 API PDF fallback -> metadata-only`。

`ieee` 不需要 IEEE API key；它走 `landing metadata / article number -> direct REST HTML -> clean-browser HTML -> direct HTTP PDF fallback -> seeded-browser PDF fallback`，但全文是否可用仍取决于当前环境对 IEEE Xplore 的合法访问上下文。clean-browser HTML 使用新的 CloakBrowser context，不读取本机浏览器 profile、不复用用户登录态、不自动登录、不处理验证码，也不绕过访问权限。direct HTTP PDF 返回 `stamp.jsp` HTML wrapper 或 access/challenge 页面时，seeded-browser PDF fallback 只复用当前页面运行期间获得的合法 IEEE cookies/session。

`wiley`、`science`、`pnas`、`ams` 默认通过 CloakBrowser HTML bootstrap 进入 provider-owned browser workflow。是否能拿到全文仍取决于 publisher 访问权限、paywall/challenge 与远端站点行为。

这些 Atypon HTML route 会在 challenge/paywall 判定前先等待正文 DOM 稳定；如果正文已经可抽取，页面残留的 Cloudflare/challenge 文案不会提前中断 HTML route，最终全文/摘要/降级结论仍由 Markdown 抽取后的 availability 判定负责。

默认 browser workflow 的最小可选配置：

```bash
export CLOAKBROWSER_HEADLESS="true"
export CLOAKBROWSER_TIMEOUT_MS="120000"
```

AGU/Wiley 页面如果因为 Cloudflare challenge 无法在默认 headless browser 路径下通过，可只覆盖 browser context UA，不影响 Crossref / API 等非浏览器 HTTP 请求：

```bash
export PAPER_FETCH_BROWSER_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
```

补充：

- `wiley` / `science` / `pnas` / `ams` 还需要 browser runtime，因为 PNAS direct HTML preflight、HTML 正文图片资产下载和 seeded-browser PDF/ePDF fallback 都会使用 browser context
- `elsevier` 只需要 `ELSEVIER_API_KEY`
- `ieee` 不需要额外 env；普通 fetch 在无授权或 REST/browser/PDF route 返回非全文时会降级到 provider abstract-only / metadata-only；golden criteria live review 面向具备合法 IEEE Xplore 授权上下文的机器，IEEE 样本预期为 fulltext，降级会作为 blocked live fetch 暴露；配置了 `download_dir` 且 artifact mode 为 `all` 时 PDF fallback 的最后一个非 PDF HTML 会保存在 `ieee_pdf_fallback/pdf.failure.html`
- `arxiv` 不需要额外 env；路径细节见 [`providers.md` 的 arXiv 小节](providers.md#arxiv)。
- 如果只想启用 `wiley` 的官方 TDM API PDF lane，可以只配置 `WILEY_TDM_CLIENT_TOKEN`；这不会启用 HTML 资产下载或 seeded-browser PDF/ePDF fallback
- `wiley` / `science` / `pnas` / `ams` 的 browser workflow 顺序见 [`providers.md`](providers.md#wiley-science-pnas-browser-workflow)。

## 5. 部署到 Codex

最常用流程：

```bash
python3 -m pip install .
./scripts/install-codex-skill.sh --register-mcp
```

这个脚本会：

- 安装当前包
- 复制静态 skill bundle
- 在显式传入 `--register-mcp` 时注册 `paper-fetch` MCP server
- 注册 Codex MCP 时把当前 `python3` 解释器写入 `PAPER_FETCH_MCP_PYTHON_BIN`，并让 Codex 调用仓库里的 launcher
- 在 WSL 下默认通过 `scripts/run-codex-paper-fetch-mcp.sh` 启动 MCP；如需 headed browser，请在外部环境中设置 `CLOAKBROWSER_HEADLESS=false` 并提供可用显示环境

常用选项：

- `--project`
- `--env-file <path>`
- `--mcp-name <name>`

## 6. 部署到 Claude Code

最常用流程：

```bash
python3 -m pip install .
./scripts/install-claude-skill.sh --register-mcp
```

常用选项：

- `--project`
- `--env-file <path>`
- `--mcp-scope local|user|project`
- `--mcp-name <name>`

## 7. 部署到 Gemini CLI

最常用流程：

```bash
python3 -m pip install .
./scripts/install-gemini-skill.sh --register-mcp
```

这个脚本会：

- 安装当前包
- 复制静态 skill bundle 到 `~/.gemini/skills/paper-fetch-skill`
- 在显式传入 `--register-mcp` 且检测到 `gemini` CLI 时，执行 `gemini mcp remove paper-fetch` 后再注册 `paper-fetch` MCP server
- 传入 `--env-file <path>` 时，把它映射为 Gemini MCP env 参数 `PAPER_FETCH_ENV_FILE=<path>`
- 如果没有检测到 `gemini` CLI，只安装 skill 并跳过 MCP 注册，不写 `~/.gemini/settings.json`

常用选项：

- `--project`
- `--env-file <path>`
- `--mcp-name <name>`

## 8. 手动注册 MCP

如果你不想使用安装脚本，也可以直接挂一个 stdio MCP server：

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

如果你是在 WSL 下给 Codex 挂宿主 MCP，推荐直接用：

```bash
./scripts/run-codex-paper-fetch-mcp.sh
```

这个包装脚本会：

- 在 WSL 下补齐缺失的 `XDG_RUNTIME_DIR`
- 如需 headed browser，可设置 `CLOAKBROWSER_HEADLESS=false`
- 默认保持 headless，适合 stdio MCP 场景

如果配置文件不在进程环境里，额外设置：

```bash
PAPER_FETCH_ENV_FILE=/path/to/.env
```

当前 MCP server 适合挂到支持 stdio MCP 的 host。

常用抓取参数的默认模式、`artifact_mode`、`prefer_cache`、`no_download` 和 `save_markdown` 语义见 [`providers.md`](providers.md#mcp-download-and-markdown-save)。

## 9. 更新方式

离线 release 包的更新方式见“离线包”小节。本节只针对源码或在线安装环境。

更新当前仓库版本时，进入原来的 Python 环境后重新安装即可：

```bash
python3 -m pip install .
```

如果你还在使用 Codex、Claude Code 或 Gemini CLI，推荐顺手重跑对应安装脚本，让 skill 和 MCP 一起更新：

```bash
./scripts/install-codex-skill.sh --register-mcp
./scripts/install-claude-skill.sh --register-mcp
./scripts/install-gemini-skill.sh --register-mcp
```

## 10. 最小验证步骤

先做一个最小 smoke test：

```bash
paper-fetch --query "10.1186/1471-2105-11-421"
```

CLI 默认打印 Markdown 到终端；如果指定 `--output-dir` 且未显式传 `--output`，主输出会写入 `<doi>.md`、`<doi>.json` 或 `<doi>.both.json`，不再把正文打印到终端。完整输出、artifact、资产下载和错误码语义见 [`cli.md`](cli.md)。

如果你在仓库源码目录里做 repo-local 验证，先安装测试依赖，并推荐显式带上 `PYTHONPATH=src`。默认 `pytest` 覆盖 `tests/unit` + `tests/integration` + `tests/devtools` 并启用多进程并行；`tests/live` 需要显式指定路径并串行运行：

```bash
python3 -m pip install '.[dev]'
PYTHONPATH=src pytest tests/unit/test_cli.py tests/unit/test_service_*.py tests/unit/test_mcp_*.py
PYTHONPATH=src pytest
```

完整 golden corpus regression 默认跳过，可在本地或 workflow dispatch 中显式打开；该测试已按 fixture 参数化，默认复用 `pyproject.toml` 的 pytest-xdist 并行配置：

```bash
PAPER_FETCH_RUN_FULL_GOLDEN=1 PYTHONPATH=src python3 -m pytest tests/integration/test_golden_corpus.py -q
```

如果你要额外验证 `wiley` / `science` / `pnas` / `ams` live 路径，请先确认 CloakBrowser package 和合法 publisher 访问上下文可用，再运行对应 live 测试。

## 相关文档

- [`../README.md`](../README.md)
- [`docs/README.md`](README.md)
- [`providers.md`](providers.md)
- [`architecture/target-architecture.md`](architecture/target-architecture.md)
