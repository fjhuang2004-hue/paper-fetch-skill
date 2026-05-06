# Copernicus / MDPI / IEEE Provider TODO

本清单跟踪三家待接入 provider 的实现工作；不要把这些条目合并进根目录 `todo.md`。

## 1. Copernicus

- [ ] 在 provider catalog 中新增 `copernicus`：domain、publisher aliases、DOI prefix `10.5194/`、默认 asset profile 和 status 顺序。
- [ ] 接入 routing / preferred provider / provider status / CLI / MCP 可见 provider 列表。
- [ ] 实现 landing HTML fetch 与 `citation_xml_url` / XML 下载链接发现。
- [ ] 实现 Copernicus NLM/JATS XML -> Markdown：章节、摘要、图表 caption、OASIS 表格、MathML、参考文献、supplementary links。
- [ ] 增加 direct HTML fallback 和 PDF text-only fallback；PDF 临时不可用时不影响 XML/HTML 成功。
- [ ] 接入 `asset_profile=body|all` 的正文图片、表格图片、公式图片和 supplementary 下载。
- [ ] 增加 unit fixtures、provider routing 测试、Markdown golden 测试和 live smoke 样本。
- [ ] 同步 `docs/providers.md`、`references/api_notes.md`、`references/routing_rules.md` 和 CI live/skip 说明。

## 2. MDPI

- [ ] 在 provider catalog 中新增 `mdpi`：domain `mdpi.com`、publisher aliases、DOI prefix `10.3390/`、默认 asset profile 和 status 顺序。
- [ ] 接入 routing / preferred provider / provider status / CLI / MCP 可见 provider 列表。
- [ ] 实现 landing HTML fetch 与 article XML 链接发现；固定 `/xml` 路由只作为 secondary candidate。
- [ ] 实现 MDPI XML -> Markdown：章节、摘要、图表 caption、表格、公式、参考文献和 supplementary links。
- [ ] 实现 provider-cleaned article HTML fallback，清理导航、菜单、推荐文章、评论入口和引用弹层。
- [ ] direct HTTP 被 CDN 拦截时增加 direct Playwright HTML fallback；不引入 FlareSolverr，除非未来出现明确 Cloudflare challenge。
- [ ] 增加 PDF text-only fallback 和 `asset_profile=body|all` 资产下载。
- [ ] 增加 unit fixtures、CDN/403 降级测试、provider routing 测试、Markdown golden 测试和 live smoke 样本。
- [ ] 同步 `docs/providers.md`、`references/api_notes.md`、`references/routing_rules.md` 和 CI live/skip 说明。

## 3. IEEE

- [ ] 在 provider catalog 中新增 `ieee`：domain `ieeexplore.ieee.org`、publisher aliases、DOI prefix `10.1109/`、默认 asset profile 和 status 顺序。
- [ ] 接入 routing / preferred provider / provider status / CLI / MCP 可见 provider 列表。
- [ ] 实现 DOI / landing URL 到 IEEE article number 的解析。
- [ ] 实现动态全文端点请求：`/rest/document/{article_number}/?logAccess=true`。
- [ ] 保留 publisher 页面上下文请求头：document `Referer`、`x-security-request: required`、browser UA 和兼容 `Accept`。
- [ ] 实现 full-text HTML marker 校验，排除登录页、access gate、验证码、摘要页、空壳和错误 HTML。
- [ ] 实现 IEEE HTML -> Markdown：章节、图表、表格、公式、参考文献和内部引用。
- [ ] 默认 `fulltext_first`，但在无权限、无全文或校验失败时降级到 `abstract_only` / `metadata_only`。
- [ ] 增加授权上下文 live smoke、无授权降级测试、provider routing 测试、Markdown golden 测试和 source trail 断言。
- [ ] 同步 `docs/providers.md`、`references/api_notes.md`、`references/routing_rules.md` 和 CI live/skip 说明。
