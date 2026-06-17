# paper-fetch-skill 架构文档

> 最后更新：2026-06-17 | ASM全部完成✅ | ACS+Elsevier+Wiley+T&F+PNAS+Science+Springer+RSC+ASM专属DOM完成，登录ACS/Elsevier(含Cell Press)/Wiley/T&F/Springer/RSC，ASM:OA only(HZAU未购买)，OUP受阻，PNAS/Science HZAU未购买

## 一、整体流程

```
用户给一个 DOI
    │
    ▼
╔══════════════════════════════════════════════════════════╗
║  ①  DOI 解析 + 元数据（WSL，无 CF）                      ║
║  Crossref API → 标题/作者/期刊/publisher/DOI前缀          ║
║  同时识别 OA 状态（license 字段含 creativecommons）       ║
╚══════════════════════════════════════════════════════════╝
    │
    ▼
╔══════════════════════════════════════════════════════════╗
║  ②  OA 全文捷径 ✅（WSL，无 CF，~5s）                    ║
║  EPMC 搜 PMCID → PMC E-utilities efetch JATS XML         ║
║  → ElementTree → markdown → article_from_markdown()       ║
║  非 OA → 走 ②.5                                           ║
╚══════════════════════════════════════════════════════════╝
    │
    ▼
╔══════════════════════════════════════════════════════════╗
║  ②.5  WSL→Windows 桥接 ✅ 🆕                             ║
║  _is_wsl()=True + provider需浏览器                        ║
║  → cmd.exe /c bridge_windows.py --doi --publisher         ║
║  → [Windows] CF绕过+登录+HTML→MD+图片下载（一轮浏览器）   ║
║  → WSL 回读 MD → article_from_markdown()                 ║
║  实测 ACS: ~80s, Elsevier: ~83s, T&F: ~61s               ║
║  非 WSL → 走 ③                                            ║
╚══════════════════════════════════════════════════════════╝
    │
    ▼
╔══════════════════════════════════════════════════════════╗
║  ③  浏览器取 HTML（Windows + nodriver）                  ║
║  ┌─ CF 绕过（publisher 无关）─┐                          ║
║  │  bezier 鼠标轨迹             │                          ║
║  │  JS 挑战自动消解            │                          ║
║  │  fresh copy profile         │                          ║
║  │  kill_chrome 精准清残留     │                          ║
║  └───────────────────────────┘                          ║
║  ┌─ 被墙检测 + 自动登录 ─┐                                ║
║  │  acs ✅     wiley ✅      │                             ║
║  │  elsevier ✅ tandf ✅    │                             ║
║  │  springer ✅ 🆕           │                             ║
║  │  oup ⚠️     pnas (待测)  │                             ║
║  │  ASM ❌                  │                             ║
║  └─────────────────────────┘                              ║
╚══════════════════════════════════════════════════════════╝
    │
    ▼
╔══════════════════════════════════════════════════════════╗
║  ④  HTML → Markdown（按出版社分派）                      ║
║  ACS:      专属 DOM 提取器 (_acs_html.py)                 ║
║  Elsevier: 专属 DOM 提取器 (_elsevier_html.py)             ║
║  Wiley:    专属 DOM 提取器 (_wiley_dom.py)                 ║
║  T&F:      专属 DOM 提取器 (_tandf_dom.py)                 ║
║  PNAS:     专属 DOM 提取器 (_pnas_dom.py)                  ║
║  Science:  专属 DOM 提取器 (_science_dom.py)               ║
║  Springer: 专属 DOM 提取器 (html_springer_nature.py) 🆕    ║
║  RSC:      专属 DOM 提取器 (_rsc_html.py)                  ║
║  ASM:      专属 DOM 提取器 (_asm_html.py) 🆕               ║
║  IEEE/MDPI/Oxford/...: 各自 DOM 提取器                    ║
║  PLOS/Copernicus: JATS XML 解析                           ║
║  AIP/AMS/IOP: render_container_markdown                   ║
║  ✅ 图片下载: CDP Network.loadNetworkResource+IO.read     ║
║  ⚠️ PDF 兜底未适配 nodriver                              ║
╚══════════════════════════════════════════════════════════╝
```

## 二、核心调用链（非OA 文章，桥接路径）

```
bridge_windows.py (单次 asyncio.run, 一轮浏览器)
  → _try_once_keep_alive(url, publisher)
    → CF绕过 → 自动登录 → outerHTML
    → ScienceDirect: 检测 #body 缺失 → reload 全文
  → extract_browser_workflow_markdown(html, url, publisher, metadata)
    → if publisher == "acs":
        → _acs_extract_body(_raw_body)            # ACS 专属 DOM
    → elif publisher == "elsevier":
        → _elsevier_extract_body(_raw_body)       # Elsevier 专属 DOM
    → elif publisher == "wiley":
        → _wiley_extract_body(_raw_body)          # Wiley 专属 DOM
    → elif publisher == "tandf":
        → _tandf_extract_body(_raw_body)          # T&F 专属 DOM 🆕
    → else:
        → render_container_markdown()             # 通用 DOM
  → _download_images_async(tab, img_urls)         # CDP Network.loadNetworkResource
  → rewrite_image_urls_to_local()                 # CDN→images/xxx
  → 保存 bridge_article.md + images/              # 输出到 DOI 文件夹
```

## 三、提取器分布（20 个出版社）

| 类型 | 数量 | 出版社 | 关键文件 |
|------|------|--------|----------|
| 专属 DOM | 16 | ACS, Elsevier, Wiley, T&F, **PNAS**, **Science**, Springer, IEEE, MDPI, Oxford, Ann.Rev., R.Soc., arXiv, Annual Reviews, **RSC**, **ASM** 🆕 | 各自 `_{name}_html.py` 或 `_{name}_dom.py` |
| JATS XML | 2 | PLOS, Copernicus | `_article_markdown_*.py` |
| 通用 DOM | 3 | AIP, AMS, IOP | `render_container_markdown()` |

### RSC DOM 提取器规则 🆕
- 范围: `#wrapper`（articlehtml 端点）或 `#pnlArticleContentLoaded`（landing page AJAX）
- 段落: 纯 `<p>` 标签（跳过 `p.header_text` / `p.bold.italic`），含无名 `<div>` 包裹的正文
- 标题: h2/h3/h4（无 class），跳过 `div.abstract` 内的 "Abstract"
- 后置截断: Author contributions / Conflicts of interest / Acknowledgements / Notes and references 处停止
- 图片: 优先 `<a href="..._hi-res.gif">` 高清链接，fallback 到 `<img>` src
- landing page 懒加载: 从 `data-original` 取真实 URL，过滤 `LoadingBackGround` 占位符
- 文件: `src/paper_fetch/providers/_rsc_html.py`

### ASM DOM 提取器规则 🆕
- 平台: `journals.asm.org`（Atypon Literatum pb 新版前端，非经典 Atypon）
- 范围: `<article>` → `#bodymatter` → `div.core-container` → `<section id="sec-N">`
- 段落: **`<div>`（无 class）**，非 `<p>` — 与 ACS/T&F 完全不同
- 标题: h2（一级节）, h3（子节）, h4（孙节），递归遍历嵌套 `<section>`
- 后置截断: `<section id="acknowledgments">` / `bibliography` / `data-availability` / `supplementary-materials` 处停止
- 图片: `<div class="figure-wrap">` → `<figure class="graphic">` → **`<img data-viewer-src=".../large/...jpg">`**（hi-res），`src` 为 medium fallback
- 表格: `<div class="table-wrap">` → `<table>`，含 table-in-figure 降级处理
- 摘要: 双前端兼容 — 新 pb: `#primary-abstract` + `#abs-sec-1`，旧 Atypon: `#abstract`
- 图片 base URL: `https://journals.asm.org`
- 登录: ❌ 无 handler（HZAU 未购买 ASM 订阅），OA 文章全链路通，付费文章回退元数据
- 文件: `src/paper_fetch/providers/_asm_html.py` (350行)

### ACS DOM 提取器规则
- 范围: `.hlFld-FullText` 内
- 段落: `<div class='NLM_p'>` (ACS 专用，非 `<p>`)
- 标题: h2/h3/h4（跳过 `fig-label`）
- 后置截断: Supporting Info / Acknowledgments / References 处停止
- 化学式: `_normalise_chem()` 转 `<sub>/<sup>` → Unicode
- 文件: `src/paper_fetch/providers/_acs_html.py`

### Elsevier DOM 提取器规则
- 范围: `#body` 内（排除侧边栏 outline）
- 段落: `<div class='u-margin-s-bottom'>` (ScienceDirect 专用)
- 标题: h2/h3/h4 from section[id]
- 后置截断: Declaration / CRediT / Acknowledgement / Funding / Appendix 处停止
- 图片: `<figure>` → CDN URL `ars.els-cdn.com/content/image/...`
- 表格: `<table>` → Markdown table
- 特殊: 登录后需 reload 才能加载全文（ScienceDirect 默认摘要页）
- 文件: `src/paper_fetch/providers/_elsevier_html.py`

### Wiley DOM 提取器规则
- 范围: `section.article-section__full` 内
- 段落: 纯 `<p>` 标签（无特殊 class）
- 标题: h2.article-section__title / h3.article-section__sub-title / h4.section3
- 后置截断: Acknowledgments / Author Contributions / Conflict of Interest / Data Availability / Supporting Information
- 图片: `<figure class="figure">` → `<img src="/cms/asset/...">` → 绝对 URL
- 题注: `<figcaption class="figure__caption">` 清洗 "Open in figure viewer PowerPoint" 噪音
- 图片下载: CDP `Network.loadNetworkResource` + `IO.read` stream（非OA CDN 有 CF 保护）
- 文件: `src/paper_fetch/providers/_wiley_dom.py`

### T&F DOM 提取器规则 🆕
- 范围: `div.hlFld-Fulltext` 内
- 段落: 纯 `<p>` 标签（`<p class="last">`）
- 标题: h2.section-heading-2 / h3.section-heading-3 / h4.section-heading-4
- 章节: `div.NLM_sec.NLM_sec_level_N`
- 后置截断: Acknowledgements / Disclosure / Funding / References / Author Contributions / Data Availability
- 图片: `<div class="figureView">` → `<img>` + `<p class="captionText">`
- 摘要: `div.hlFld-Abstract`（独立提取，不在 body 中重复）
- 关键词: `div.hlFld-KeywordText`（跳过）
- 文件: `src/paper_fetch/providers/_tandf_dom.py` 🆕

## 四、登录模块 `_nodriver_login.py`

| 出版社 | handler | SSO 模式 | 状态 |
|--------|---------|----------|------|
| ACS | `_AcsLoginHandler` | CARSI → IdP → CAS | ✅ |
| Elsevier | `_ScienceDirectLoginHandler` | Shibboleth → IdP → CAS | ✅（含Cell Press，linkinghub→SD转换）|
| Wiley | `_WileyLoginHandler` | CARSI → IdP → CAS | ✅ |
| Springer Nature | `_SpringerLoginHandler` 🆕 | WAYF → CARSI Shibboleth → IdP → CAS | ✅ 4/4期刊 |
| T&F | `_TandfLoginHandler` | CARSI → IdP → CAS | ✅ ~61s |
| PNAS | `_PnasLoginHandler` | CARSI → IdP → CAS | ⚠️ HZAU未购买，无法验证 |
| Science | `_ScienceLoginHandler` | CARSI → IdP → CAS | ⚠️ HZAU未购买，无法验证 |
| OUP | `_OxfordAcademicLoginHandler` | Shibboleth(SAMS Sigma) → IdP → CAS | ⚠️ DS页reCAPTCHA |
| ASM | — | — | ❌ |
| RSC | `_RscLoginHandler` 🆕 | Federated Access → Shibboleth → CAS | ✅ ~35s，18/18 hi-res图片 |
| **ASM** | — | — | ❌ HZAU 未购买，仅 OA |

**Cell Press (Elsevier 子品牌)**：DOI 走 `linkinghub.elsevier.com`→SD 转换，登录复用 Elsevier handler，DOM 复用 `_elsevier_html.py`。无需独立 handler 或提取器。

**Springer Nature 登录特殊处理**：Nature 不用 Atypon `/action/ssostart`，而是用 `wayf.springernature.com`。通过 GET `?redirect_uri=...&search=Huazhong` 直接跳到搜索结果，绕过 JS autocomplete 组件。之后走标准 CARSI Shibboleth → HZAU IdP → CAS 流程。

## 五、项目结构

```
src/paper_fetch/
├─ service.py                   ★ fetch_paper() 入口
├─ workflow/
│  ├─ fulltext.py               ★ 主编排 (OA→bridge→provider)
│  ├─ bridge.py                 ★ 🆕 WSL→Windows 桥接
│  ├─ oa_shortcut.py            ★ OA 捷径
│  └─ routing.py                ★ 路由
├─ providers/
│  ├─ _nodriver_fetch.py        ★ CF 绕过引擎
│  ├─ _nodriver_login.py        ★ 自动登录 (6 handlers)
│  ├─ _nodriver_runtime.py      ★ Chrome 进程管理
│  ├─ _acs_html.py              ★ ACS 专属 DOM 提取器
│  ├─ _elsevier_html.py         ★ Elsevier 专属 DOM 提取器
│  ├─ _wiley_dom.py             ★ Wiley 专属 DOM 提取器
│  ├─ _tandf_dom.py             ★ T&F 专属 DOM 提取器 🆕
│  ├─ _html_section_markdown.py ★ 通用 DOM 遍历器
│  ├─ atypon_browser_workflow/
│  │  ├─ markdown.py            ★ 提取调度 (acs/elsevier/wiley/tandf/springer/rsc/asm/pnas/science/generic) 🆕
│  │  └─ postprocess.py         ★ 通用后处理
│  ├─ browser_workflow/
│  │  ├─ asset_download.py      ★ 图片下载 (Playwright, 桥接已绕过)
│  │  └─ pdf_fallback.py        ★ ⚠️ PDF兜底(Playwright,未适配)
│  └─ acs.py / wiley.py / ...   ★ 各出版社 Client
└─ extraction/html/
   ├─ _runtime.py               ★ trafilatura 已从此文件删除
   └─ figure_links.py           ★ inject_inline_figure_links
```

## 六、CF 绕过技术栈

| 技术点 | 实现 |
|--------|------|
| 引擎 | nodriver (CDP直连) |
| Profile | 每次 fresh copy，排除锁定文件 |
| Turnstile | 5层DOM选择器 + 贝塞尔轨迹 |
| 进程清理 | Win: PowerShell→taskkill ; Linux: pkill -9 -i -f |
| 图片下载 | CDP Network.loadNetworkResource + IO.read stream |

## 七、当前进度

| 模块 | 状态 | 备注 |
|------|------|------|
| CF 绕过 | ✅ 24/24 | |
| ACS 登录 | ✅ | ~70s 全链路 |
| Wiley 登录 | ✅ | |
| Elsevier 登录 | ✅ | |
| T&F 登录 | ✅ | ~61s 全链路 |
| **Springer Nature 登录** | ✅ 🆕 | 4/4期刊, 7/7图片 |
| **Cell Press** | ✅ 🆕 | 复用Elsevier登录+DOM，Trends Biotechnol实测通 |
| OUP 登录 | ⚠️ | 1篇成功，DS页reCAPTCHA阻塞 |
| PNAS 登录 | ⚠️ | OA可用，handler已写，HZAU未购买无法验证 |
| Science 登录 | ⚠️ | OA可用，handler已写，HZAU未购买无法验证 |
| OA 捷径 | ✅ | ~5s WSL |
| WSL→Windows 桥接 | ✅ | ACS/T&F/Springer 实测通 |
| trafilatura 删除 | ✅ | _runtime.py + 所有补丁清理 |
| ACS 专属 DOM | ✅ | 6/6 论文通过 |
| Elsevier 专属 DOM | ✅ | 4/4 论文通过 |
| Wiley 专属 DOM | ✅ | 4/4 论文通过 |
| T&F 专属 DOM | ✅ | 1/1 论文通过，4图下载 |
| PNAS 专属 DOM | ✅ | 1/1 论文通过 |
| Science 专属 DOM | ✅ | 1/1 论文通过 |
| **Springer 专属 DOM** | ✅ 🆕 | 4/4 论文通过，图7/7 |
| 桥接图片下载 | ✅ | CDP + 协议相对URL标准化 |
| ASM 出版社接入 | ❌ | 代码库不存在 |
| RSC 出版社接入 | ✅ 🆕 | 登录+DOM+接线全完成，18/18 hi-res图片 |
| ASM 登录 | ❌ | 待补 |
| ASM 出版社接入 | ✅ 🆕 | 全链路：DOI注册+DOM+接线，11/11 hi-res图片，OA/付费自动分流 |
| PDF 兜底适配 | 🟡 | 仍用 Playwright |

## 八、API 配置

- 已切换至 OpenCode Go: `https://opencode.ai/zen/go/v1`
- Model: `deepseek-v4-pro`
