# paper-fetch-skill 架构文档

> 最后更新：2026-06-16 | trafilatura 已死，ACS 专属 DOM 提取器完成

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
║  → [Windows] CF绕过+登录+HTML→MD+图片下载 → 共享目录     ║
║  → WSL 回读 MD → article_from_markdown()                 ║
║  实测 ACS: ~70s, HTML+MD+图片全链路 ✅                     ║
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
║  │  acs ✅  wiley ✅  elsevier ✅  │                      ║
║  │  PNAS/ASM/OUP/T&F/cell ❌     │                       ║
║  └─────────────────────────┘                              ║
╚══════════════════════════════════════════════════════════╝
    │
    ▼
╔══════════════════════════════════════════════════════════╗
║  ④  HTML → Markdown（按出版社分派）                      ║
║  ACS:     专属 DOM 提取器 (_acs_html.py)                  ║
║  Springer/                                                                 ieee/mdpi/oxford/...: 各自 DOM 提取器       ║
║  Elsevier/PLOS: JATS XML 解析                             ║
║  Wiley/PNAS/Science/AIP/...: render_container_markdown    ║
║  ✅ 图片下载: nodriver fetch() CDN→本地                   ║
║  ⚠️ PDF 兜底未适配 nodriver                              ║
╚══════════════════════════════════════════════════════════╝
```

## 二、核心调用链（非OA ACS 文章，桥接路径）

```
bridge_windows.py
  → fetch_html_with_nodriver(url, "acs")
    → CF绕过 → CARSI登录 → outerHTML (~750KB)
  → extract_browser_workflow_markdown(html, url, "acs", metadata)
    → if publisher == "acs":
        → extract_body_markdown(body_container)   # ACS 专属 DOM
          → .hlFld-FullText → div.NLM_p/p → h2/h3 → 后置截断
        → inject_inline_figure_links()            # 图注→![Figure]
        → finalize_extraction()                   # 作者/参考文献
        → _inject_front_matter()                  # YAML 元数据
      else:
        → render_container_markdown()             # 通用 DOM
  → _download_images_via_nodriver(img_urls)       # nodriver fetch()
  → rewrite_image_urls_to_local()                 # CDN→images/xxx
  → 保存 bridge_article.md + images/              # 输出到 DOI 文件夹
```

## 三、提取器分布（19 个出版社）

| 类型 | 数量 | 出版社 | 关键文件 |
|------|------|--------|----------|
| 专属 DOM | 9 | ACS, Springer, IEEE, MDPI, Oxford, Ann.Rev., R.Soc., arXiv, Annual Reviews | 各自 `_{name}_html.py` |
| JATS XML | 3 | Elsevier, PLOS, Copernicus | `_article_markdown_*.py` |
| 通用 DOM | 7 | Wiley, PNAS, Science, AIP, AMS, IOP, T&F | `render_container_markdown()` |
| 不存在 | 2 | ASM, RSC | — |

### ACS DOM 提取器规则
- 范围: `.hlFld-FullText` 内
- 段落: `<div class='NLM_p'>` (ACS 专用，非 `<p>`)
- 标题: h2/h3/h4（跳过 `fig-label`）
- 后置截断: Supporting Info / Acknowledgments / References 处停止
- 化学式: `_normalise_chem()` 转 `<sub>/<sup>` → Unicode
- 文件: `src/paper_fetch/providers/_acs_html.py`

## 四、项目结构

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
│  ├─ _nodriver_login.py        ★ 自动登录
│  ├─ _nodriver_runtime.py      ★ Chrome 进程管理
│  ├─ _acs_html.py              ★ ACS 专属 DOM 提取器
│  ├─ _html_section_markdown.py ★ 通用 DOM 遍历器 (非 ACS 用)
│  ├─ atypon_browser_workflow/
│  │  ├─ markdown.py            ★ 提取调度 (if publisher=="acs")
│  │  └─ postprocess.py         ★ 通用后处理 (ACS 已跳过)
│  ├─ browser_workflow/
│  │  ├─ asset_download.py      ★ 图片下载 (Playwright, 桥接已绕过)
│  │  └─ pdf_fallback.py        ★ ⚠️ PDF兜底(Playwright,未适配)
│  └─ acs.py / wiley.py / ...   ★ 各出版社 Client
└─ extraction/html/
   ├─ _runtime.py               ★ trafilatura 已从此文件删除
   └─ figure_links.py           ★ inject_inline_figure_links
```

## 五、CF 绕过技术栈

| 技术点 | 实现 |
|--------|------|
| 引擎 | nodriver (CDP直连) |
| Profile | 每次 fresh copy，排除锁定文件 |
| Turnstile | 5层DOM选择器 + 贝塞尔轨迹 |
| 进程清理 | Win: PowerShell→taskkill ; Linux: pkill -9 -i -f |
| 图片下载 | 桥接: nodriver JS fetch() CDN→base64→本地 |

## 六、当前进度

| 模块 | 状态 | 备注 |
|------|------|------|
| CF 绕过 | ✅ 24/24 | |
| ACS 登录 | ✅ | ~70s 全链路 (HTML+MD+图片) |
| Wiley 登录 | ✅ | |
| Elsevier 登录 | ✅ | |
| OA 捷径 | ✅ | ~5s WSL |
| WSL→Windows 桥接 | ✅ | ACS实测通 |
| trafilatura 删除 | ✅ | _runtime.py + 所有补丁清理 |
| ACS 专属 DOM 提取器 | ✅ | 6/6 论文通过 |
| 桥接图片下载 | ✅ | nodriver fetch API |
| 图片下载重写 | ✅ | 桥接用 nodriver，主流程仍 Playwright |
| Wiley/PNAS/Science DOM | ❌ | 待写 |
| ASM 出版社接入 | ❌ | 代码库不存在 |
| RSC 出版社接入 | ❌ | 代码库不存在 |
| PNAS/ASM/OUP/T&F/cell 登录 | ❌ | 待补 |
| PDF 兜底适配 | 🟡 | 仍用 Playwright |
