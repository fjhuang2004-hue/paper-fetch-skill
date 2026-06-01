# Paper-Fetch 使用说明与改动记录（for Hermes）

> **版本**: 基于 commit `d4575e0` + 后续修复  
> **更新日期**: 2026-06-02  
> **目标**: 让 Hermes 能够正确调用 paper-fetch 获取 40 本白名单期刊的论文全文

---

## 一、paper-fetch 概述

paper-fetch 是一个 CLI/MCP/Skill 工具，将学术论文转换为 AI 可读的 Markdown：
- **输入**: DOI / 论文URL / 标题
- **输出**: 结构化 Markdown（元数据 + 正文 + 图表 + 参考文献）
- **安装位置**: `~/tools/paper-fetch-skill/`（WSL）
- **Windows 克隆**: `D:\git\paper-fetch-skill`（用于推送到 GitHub）
- **虚拟环境**: `.venv/bin/python3`（Python 3.14）
- **配置目录**: `~/.config/paper-fetch/.env`

### 基本用法

```bash
cd ~/tools/paper-fetch-skill
source .venv/bin/activate

# 单篇获取
paper-fetch --query "10.1021/jacs.6c00927" --output-dir ./output

# 批量获取
paper-fetch --query-file ./dois.txt --batch-concurrency 1 --output-dir ./output

# 指定输出文件
paper-fetch --query "DOI" --output ./paper.md
```

**重要**: ACS/RSC/Wiley 等需要 CloakBrowser 的期刊，建议 `--batch-concurrency 1`，避免多个 headless 浏览器争抢资源导致超时。

---

## 二、配置文件

**文件**: `~/.config/paper-fetch/.env`

```bash
# CloakBrowser 持久化 profile 目录（用于保存 Cloudflare 等会话状态）
CLOAKBROWSER_USER_DATA_DIR=~/.local/share/paper-fetch/browser-profile

# Elsevier API key（从 dev.elsevier.com 申请，华中农业大学机构账号）
ELSEVIER_API_KEY=<your-api-key-here>
```

- `CLOAKBROWSER_USER_DATA_DIR` 用于保存浏览器的 `storage-state.json`（含 Cloudflare cookies）
- `ELSEVIER_API_KEY` 是华中农业大学申请的机构级 API key（**注意**: `view=FULL` 参数不可用，见下文修复）

---

## 三、所有改动记录

### 改动 1: Elsevier API 修复 — 移除 `view=FULL` 参数

**问题**: 华中农业大学的 Elsevier API key 不支持 `?view=FULL` 参数，返回 HTTP 400。去掉该参数后，裸 endpoint 正常返回完整 XML。

**修改文件**: `src/paper_fetch/providers/elsevier.py`

**修改内容**: 移除两处 `query={"view": "FULL"}` 参数（约第 718 行和第 771 行）

```python
# 修改前（会报 400 错误）:
response = requests.get(url, headers=headers, params={"query": {"view": "FULL"}})

# 修改后:
response = requests.get(url, headers=headers)
```

**运行时说明**: 该修改必须同时作用于两个位置：
1. `src/paper_fetch/providers/elsevier.py`（源码）
2. `.venv/lib/python3.14/site-packages/paper_fetch/providers/elsevier.py`（实际运行时加载的文件）

如果重新 `pip install -e .`，site-packages 中的文件会被覆盖，需要再次同步。建议操作：
```bash
cp src/paper_fetch/providers/elsevier.py \
   .venv/lib/python3.14/site-packages/paper_fetch/providers/elsevier.py
```

**验证方法**:
```bash
paper-fetch --query "10.1016/j.ymben.2020.03.003" --output-dir /tmp/test
# 应返回 has_fulltext: true, source: elsevier_xml
```

---

### 改动 2: Taylor & Francis Provider 添加

**来源**: `https://github.com/fjhuang2004-hue/paper-fetch-skill`（用户 fork）

**添加文件**:
- `src/paper_fetch/providers/tandf.py` — T&F provider 注册（使用 BrowserWorkflowClient + CloakBrowser）
- `src/paper_fetch/providers/_tandf_html.py` — T&F HTML 提取规则（Atypon profile）

**DOI 前缀**: `10.1080/`

**获取方式**: 通过 CloakBrowser 访问 `https://www.tandfonline.com/doi/full/{doi}`，提取 HTML 正文

**Git commit**: `d4575e0 feat: add Taylor & Francis provider with CDP browser support`

---

### 改动 3: ACS Cloudflare Turnstile 绕过 — Primer 脚本

**问题**: ACS 期刊（`pubs.acs.org`）使用 Cloudflare Turnstile 防机器人，headless 浏览器会被拦截。paper-fetch 已有 `storage-state.json` 持久化机制——只要用 headful 浏览器手动访问一次，保存会话后 headless 即可复用 cookies。

**Primer 脚本**: `~/tools/paper-fetch-acs-primer.py`

**使用场景**:
1. **首次配置** — 从未在 headful 下访问过 ACS
2. **Cookies 过期** — `storage-state.json` 中的 Cloudflare cookies 过期（通常约 24 小时），ACS 期刊重新返回 `crossref_meta`

**执行方法**:
```bash
cd ~/tools/paper-fetch-skill
source .venv/bin/activate
python3 ~/tools/paper-fetch-acs-primer.py --wait 90
```

**脚本工作流程**:
1. 启动 headful CloakBrowser（可见窗口，需要 `DISPLAY` 环境变量，WSLg 自动提供）
2. 加载已有的 `storage-state.json`（如果存在）
3. 导航到 ACS 论文页面
4. 等待 90 秒（用户可 `touch /tmp/acs-primer-done` 提前结束等待）
5. 保存浏览器会话到 `~/.local/share/paper-fetch/browser-profile/storage-state.json`

**注意事项**:
- **必须设置 `DISPLAY` 环境变量**，WSLg 下默认为 `:0`
- 有时 Cloudflare 不弹验证框（已有有效 cookies），此时浏览器直接显示论文页面，脚本正常保存即可
- `storage-state.json` 通常在 30KB 左右（含有效 cookies 时）

---

## 四、40 本白名单期刊获取一览

| # | 期刊 | Provider | 方法 | 状态 | 特殊说明 |
|---|------|----------|------|------|----------|
| | **综合顶刊 (4)** | | | | |
| 1 | Nature | springer | CloakBrowser | ✅ | Springer HTML/PDF 路径 |
| 2 | Science | science | CloakBrowser | ✅ | `science.org` |
| 3 | Cell | elsevier | Elsevier API | ✅ | 走 elsevier_xml |
| 4 | PNAS | pnas | CloakBrowser | ✅ | `pnas.org` |
| | **生物技术 (5)** | | | | |
| 5 | Nat Biotechnol | springer | CloakBrowser | ✅ | Springer 体系 |
| 6 | Nat Chem Biol | springer | CloakBrowser | ✅ | Springer 体系 |
| 7 | Nat Catal | springer | CloakBrowser | ✅ | Springer 体系 |
| 8 | Sci Adv | science | CloakBrowser | ❌ | 同 science.org 但走不同路径 |
| 9 | Nat Synth | — | — | ⬜ | Crossref 无数据，需手动获取 DOI |
| | **微生物 (7)** | | | | |
| 10 | Nat Rev Microbiol | springer | CloakBrowser | ✅ | Springer 体系 |
| 11 | Nat Microbiol | springer | CloakBrowser | ✅ | Springer 体系 |
| 12 | Cell Host Microbe | elsevier | Elsevier API | ✅ | Cell Press/Elsevier |
| 13 | PLoS Genet | plos | PDF 提取 | ✅ | PLOS 开放获取 |
| 14 | FEMS Microbiol Rev | oxfordacademic | HTTP | ❌ | Oxford 有反爬机制 |
| 15 | ISME J | oxfordacademic | HTTP | ❌ | Oxford 有反爬机制 |
| | **化学催化 (9)** | | | | |
| 16 | Chem Rev | acs | CloakBrowser + PDF 回退 | ✅ | **需要 primer** 维持 cookies；HTML 失败自动回退 PDF |
| 17 | Chem Soc Rev | royalsocietypub | HTTP | ❌ | RSC Cloudflare 拦截 |
| 18 | Nat Rev Chem | springer | CloakBrowser | ✅ | Springer 体系 |
| 19 | EES | royalsocietypub | HTTP | ❌ | RSC Cloudflare 拦截 |
| 20 | JACS | acs | CloakBrowser | ✅ | **需要 primer** 维持 cookies |
| 21 | Angew Chem | wiley | CloakBrowser | ❌ | Wiley Cloudflare，可能需要 primer |
| 22 | ACS Catal | acs | CloakBrowser | ✅ | **需要 primer** |
| 23 | JACS Au | acs | CloakBrowser | ✅ | **需要 primer** |
| 24 | Green Chem | royalsocietypub | HTTP | ❌ | RSC Cloudflare 拦截 |
| | **代谢工程 (4)** | | | | |
| 25 | Metab Eng | elsevier | Elsevier API | ✅ | elsevier_xml |
| 26 | ACS Synth Biol | acs | CloakBrowser | ✅ | **需要 primer** |
| 27 | Biotechnol Adv | elsevier | Elsevier API | ✅ | elsevier_pdf |
| 28 | Trends Biotechnol | elsevier | Elsevier API | ✅ | Cell Press/Elsevier |
| | **高相关 (3)** | | | | |
| 29 | Nat Commun | springer | CloakBrowser | ❌ | 返回 `crossref_meta`，疑似 DOI 问题 |
| 30 | Mol Syst Biol | springer | CloakBrowser | ✅ | EMBO/Springer |
| 31 | Curr Opin Biotechnol | elsevier | Elsevier API | ✅ | elsevier_xml |
| | **边缘 (2)** | | | | |
| 32 | Nat Methods | springer | CloakBrowser | ✅ | Springer 体系 |
| 33 | Nat Chem Eng | springer | CloakBrowser | ✅ | Springer 体系 |
| | **补充 (7)** | | | | |
| 34 | Cell Rep | elsevier | Elsevier API | ✅ | Cell Press/Elsevier |
| 35 | Cell Syst | elsevier | Elsevier API | ❌ | batch 测试返回 `crossref_meta` |
| 36 | PLoS Biol | plos | PDF 提取 | ✅ | PLOS 开放获取 |
| 37 | EMBO J | springer | CloakBrowser | ✅ | EMBO/Springer |
| 38 | Bioresour Technol | elsevier | Elsevier API | ✅ | elsevier_pdf |
| 39 | Crit Rev Biotechnol | tandf | CloakBrowser | ❌ | T&F，batch 测试返回 `crossref_meta` |
| 40 | Nat Rev Bioeng | — | — | ⬜ | Crossref 无数据，新刊需手动获取 DOI |

**图例**: ✅ 成功获取全文 | ❌ 失败（仅 Crossref 元数据） | ⬜ 无法测试（无 DOI）

**统计**: 23/38 成功（61%），2 无法测试，15 需修复

---

## 五、各 Provider 获取方法与注意事项

### 5.1 Elsevier 系（elsevier_xml / elsevier_pdf）

**覆盖期刊**: Cell, Cell Host Microbe, Metab Eng, Biotechnol Adv, Trends Biotechnol, Curr Opin Biotechnol, Cell Rep, Cell Syst, Bioresour Technol

**获取方式**: 通过 Elsevier API（`api.elsevier.com`）获取 XML/PDF 全文

**配置要求**:
- `~/.config/paper-fetch/.env` 中配置 `ELSEVIER_API_KEY`
- **已修复**: 去掉了 `view=FULL` 参数（华中农业大学 key 不支持此参数）

**验证**:
```bash
paper-fetch --query "10.1016/j.ymben.2020.03.003" --output-dir /tmp/test
```

**注意事项**:
- API 有速率限制，批量获取建议 `--batch-concurrency 1`
- 某些论文可能只有 PDF 没有 XML → 自动回退 `elsevier_pdf`
- Cell Syst 的测试 DOI 可能需要更换

---

### 5.2 Springer/Nature 系（springer_html / springer_pdf）

**覆盖期刊**: Nature 系列 (15 本)、Mol Syst Biol、EMBO J

**获取方式**: 通过 CloakBrowser 访问 `nature.com` / `link.springer.com`，提取 HTML 或 PDF 回退

**注意事项**:
- Springer 体系较稳定，通常不需要额外配置
- 部分 Nature 子刊可能被 paywall 拦截，自动回退 PDF
- **Nat Commun** batch 测试失败，可能需要更换 DOI 重试

---

### 5.3 ACS 系（acs）⚠️ 需要维护

**覆盖期刊**: Chem Rev, JACS, ACS Catal, JACS Au, ACS Synth Biol

**获取方式**: 通过 CloakBrowser 访问 `pubs.acs.org`

**关键依赖**: `~/.local/share/paper-fetch/browser-profile/storage-state.json`

#### Cloudflare Cookie 维护

ACS 使用 Cloudflare Turnstile 防机器人，cookies 约 24 小时过期。

**判断过期**: paper-fetch 返回 `source: crossref_meta`，或日志显示 `cloakbrowser_request_failed`

**刷新 cookies**:
```bash
cd ~/tools/paper-fetch-skill && source .venv/bin/activate
python3 ~/tools/paper-fetch-acs-primer.py --wait 90
```

**验证 cookies 有效**:
```bash
paper-fetch --query "10.1021/jacs.6c00927" --output-dir /tmp/acs-check
grep "has_fulltext:" /tmp/acs-check/*.md
# 应显示 has_fulltext: true
```

**注意事项**:
- HTML 路径可能偶发失败，paper-fetch 会自动回退到 PDF 提取
- PDF 回退只能提取文本，不包含图表
- ACS 期刊单篇获取约 2-3 分钟（浏览器启动 + Cloudflare 验证 + 内容提取）

---

### 5.4 Science 系（science）

**覆盖期刊**: Science, Science Advances

**获取方式**: 通过 CloakBrowser 访问 `science.org`

**状态**:
- Science ✅ 稳定
- Science Advances ❌ batch 测试失败（返回 `crossref_meta`），走不同访问路径，需排查

---

### 5.5 PNAS（pnas）

**覆盖期刊**: PNAS

**获取方式**: 通过 CloakBrowser 访问 `pnas.org`

**状态**: ✅ 稳定

---

### 5.6 Wiley 系（wiley）

**覆盖期刊**: Angew Chem

**获取方式**: 通过 CloakBrowser 访问 `onlinelibrary.wiley.com`

**状态**: ❌ batch 测试失败（返回 `crossref_meta`）

**注意事项**:
- Wiley 使用独立 Cloudflare 保护，策略与 ACS 不同
- 当前测试 DOI (`10.1002/anie.8506641`) 需验证是否存在
- Wiley 慢路径有时可通过，但 batch 测试中未成功

---

### 5.7 RSC 系（royalsocietypublishing）

**覆盖期刊**: Chem Soc Rev, Energy Environ Sci (EES), Green Chem

**获取方式**: HTTP 访问 `pubs.rsc.org`（不走 CloakBrowser）

**状态**: ❌ 全部失败，RSC 使用 Cloudflare 拦截 HTTP 请求

**注意事项**:
- RSC 走 HTTP 路径而非浏览器路径
- 可能需要类似 ACS primer 的方案，或切换到浏览器路径
- **这是下一个优先修复的目标**

---

### 5.8 Oxford 系（oxfordacademic）

**覆盖期刊**: FEMS Microbiol Rev, ISME J

**获取方式**: HTTP 访问 `academic.oup.com`（不走 CloakBrowser）

**状态**: ❌ 全部失败，Oxford 有反爬机制

**注意事项**:
- 可能需要切换到浏览器路径（类似 ACS）

---

### 5.9 PLOS 系（plos）

**覆盖期刊**: PLoS Genet, PLoS Biol

**获取方式**: PDF 下载 + 文本提取

**状态**: ✅ PLOS 是开放获取期刊，稳定

---

### 5.10 T&F 系（tandf）

**覆盖期刊**: Crit Rev Biotechnol

**获取方式**: 通过 CloakBrowser 访问 `tandfonline.com`

**状态**: ❌ batch 测试返回 `crossref_meta`

**注意事项**:
- 这是从用户 fork 添加的 provider（commit `d4575e0`）
- 单独运行时曾成功，batch 时失败
- 测试 DOI (`10.1080/07388551.2026.2647978`) 需验证
- T&F 需要通过机构登录（CARSI → 华中农业大学）

---

## 六、Provider 完整列表（19 个）

| # | Provider | 显示名称 | 官方 | 浏览器 | 获取方式 |
|---|----------|----------|------|--------|----------|
| 0 | crossref | Crossref | ❌ | ❌ | 元数据回退（所有期刊的最低保障） |
| 1 | elsevier | Elsevier | ✅ | ❌ | API (Elsevier API key) |
| 2 | springer | Springer | ✅ | ❌ | CloakBrowser → nature.com |
| 3 | wiley | Wiley | ✅ | ✅ | CloakBrowser → wiley.com |
| 4 | science | Science | ✅ | ✅ | CloakBrowser → science.org |
| 5 | pnas | PNAS | ✅ | ✅ | CloakBrowser → pnas.org |
| 6 | ieee | IEEE | ✅ | ❌ | HTTP → ieeexplore.ieee.org |
| 7 | arxiv | arXiv | ✅ | ❌ | API (开放获取) |
| 8 | copernicus | Copernicus | ✅ | ❌ | HTTP |
| 9 | ams | AMS | ✅ | ✅ | CloakBrowser → journals.ametsoc.org |
| 10 | mdpi | MDPI | ✅ | ✅ | CloakBrowser |
| 11 | royalsocietypublishing | RSC | ✅ | ❌ | HTTP → pubs.rsc.org |
| 12 | annualreviews | Annual Reviews | ✅ | ✅ | CloakBrowser |
| 13 | plos | PLOS | ✅ | ❌ | PDF 下载 (开放获取) |
| 14 | oxfordacademic | Oxford Academic | ✅ | ❌ | HTTP → academic.oup.com |
| 15 | acs | ACS | ✅ | ✅ | CloakBrowser → pubs.acs.org ⚠️ |
| 16 | iop | IOP Publishing | ✅ | ✅ | CloakBrowser |
| 17 | aip | AIP Publishing | ✅ | ✅ | CloakBrowser |
| 18 | tandf | Taylor & Francis | ✅ | ✅ | CloakBrowser → tandfonline.com |

---

## 七、日常维护 Checklist

### 每次使用前
- [ ] ACS cookies 是否有效：`paper-fetch --query "10.1021/jacs.6c00927" --output-dir /tmp/acs-check`
- [ ] 如返回 `crossref_meta`，执行 primer：`python3 ~/tools/paper-fetch-acs-primer.py --wait 90`

### 按优先级待修复
1. **RSC 系** (Chem Soc Rev, EES, Green Chem) — Cloudflare 拦截 HTTP 路径
2. **Oxford 系** (FEMS Microbiol Rev, ISME J) — 反爬机制
3. **Science Advances** — 访问路径与 Science 主刊不同
4. **Nat Commun** — 验证/更换 DOI
5. **Wiley/Angew Chem** — Cloudflare primer
6. **T&F/Crit Rev Biotechnol** — 验证 DOI
7. **Cell Syst** — 验证 DOI

### 配置文件备份
```bash
cp ~/.config/paper-fetch/.env ~/.config/paper-fetch/.env.bak
cp ~/.local/share/paper-fetch/browser-profile/storage-state.json \
   ~/.local/share/paper-fetch/browser-profile/storage-state.json.bak
```

---

## 八、命令速查

```bash
# 进入环境
cd ~/tools/paper-fetch-skill && source .venv/bin/activate

# 测试 ACS
paper-fetch --query "10.1021/jacs.6c00927" --output-dir /tmp/acs-check

# 刷新 ACS cookies
python3 ~/tools/paper-fetch-acs-primer.py --wait 90

# 测试 Elsevier API
paper-fetch --query "10.1016/j.ymben.2020.03.003" --output-dir /tmp/els-check

# 批量获取（建议 concurrency=1）
paper-fetch --query-file ./dois.txt --batch-concurrency 1 --output-dir ./output

# 统计结果
grep "has_fulltext:" output/*.md | sort | uniq -c
```
