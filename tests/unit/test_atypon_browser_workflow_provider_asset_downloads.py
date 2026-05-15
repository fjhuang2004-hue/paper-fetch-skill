# ruff: noqa: F403,F405
from __future__ import annotations

from ._atypon_browser_workflow_provider_support import *


class AtyponBrowserWorkflowProviderAssetDownloadTests(AtyponBrowserWorkflowProviderTestCase):
    def test_science_provider_download_related_assets_body_profile_ignores_supplementary(self) -> None:
        html = """
<article>
  <figure>
    <img src="https://www.science.org/images/large/figure1.png" alt="Figure 1 alt" />
    <figcaption>Figure 1 caption</figcaption>
  </figure>
  <section id="supplementary-materials" class="core-supplementary-materials">
    <h2>Supplementary Materials</h2>
    <a href="https://www.science.org/doi/suppl/10.1126/science.sample/suppl_file/appendix.pdf">Download</a>
  </section>
</article>
"""
        figure_url = "https://www.science.org/images/large/figure1.png"
        transport = AssetTransport({})
        client = science_provider.ScienceClient(transport=transport, env={})
        shared_fetcher = mock.Mock(
            return_value={
                "status_code": 200,
                "headers": {"content-type": "image/png"},
                "body": png_header(640, 480),
                "url": figure_url,
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "science", SCIENCE_SAMPLE.doi)
            raw_payload = _typed_raw_payload(
                provider="science",
                source_url=SCIENCE_SAMPLE.landing_url,
                content_type="text/html",
                body=html.encode("utf-8"),
                route="html",
                markdown_text=f"# {SCIENCE_SAMPLE.title}\n\n## Results\n\n" + ("Body text " * 120),
                browser_context_seed={},
            )
            mocked_fetch = mock.Mock()
            mocked_builder = mock.Mock(return_value=shared_fetcher)
            install_browser_workflow_deps(
                client,
                load_runtime_config=mock.Mock(return_value=runtime),
                ensure_runtime_ready=mock.Mock(),
                fetch_html_with_flaresolverr=mocked_fetch,
                _build_shared_browser_image_fetcher=mocked_builder,
            )
            with (
                mock.patch.object(html_assets, "_build_cookie_seeded_opener") as mocked_opener,
                mock.patch.object(html_assets, "_request_with_opener") as mocked_request,
            ):
                result = client.download_related_assets(
                    SCIENCE_SAMPLE.doi,
                    {"doi": SCIENCE_SAMPLE.doi, "title": SCIENCE_SAMPLE.title},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="body",
                )
                saved_path = Path(result["assets"][0]["path"])
                saved_bytes = saved_path.read_bytes()

        mocked_fetch.assert_not_called()
        mocked_builder.assert_called_once()
        mocked_opener.assert_not_called()
        mocked_request.assert_not_called()
        self.assertEqual(transport.calls, [])
        shared_fetcher.assert_called_once()
        self.assertEqual(shared_fetcher.call_args.args[0], figure_url)
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["kind"], "figure")
        self.assertEqual(result["assets"][0]["download_tier"], "full_size")
        self.assertEqual(result["asset_failures"], [])
        self.assertEqual(saved_bytes, png_header(640, 480))
    def test_science_provider_download_related_assets_all_profile_downloads_supplementary_via_file_fetcher(self) -> None:
        figure_url = "https://www.science.org/images/large/figure1.png"
        supplementary_url = "https://www.science.org/doi/suppl/10.1126/science.sample/suppl_file/appendix.pdf"
        html = f"""
<article>
  <figure>
    <img src="{figure_url}" alt="Figure 1 alt" />
    <figcaption>Figure 1 caption</figcaption>
  </figure>
  <section id="supplementary-materials" class="core-supplementary-materials">
    <h2>Supplementary Materials</h2>
    <a href="{supplementary_url}">Download</a>
  </section>
</article>
"""
        transport = AssetTransport({})
        client = science_provider.ScienceClient(transport=transport, env={})
        shared_image_fetcher = mock.Mock(
            return_value={
                "status_code": 200,
                "headers": {"content-type": "image/png"},
                "body": png_header(640, 480),
                "url": figure_url,
            }
        )
        shared_file_fetcher = mock.Mock(
            return_value={
                "status_code": 200,
                "headers": {"content-type": "application/pdf"},
                "body": b"%PDF-1.7 supplementary",
                "url": supplementary_url,
            }
        )
        challenge_html = {
            "status_code": 403,
            "headers": {"content-type": "text/html; charset=utf-8"},
            "body": (
                b"<html><head><title>Just a moment...</title></head>"
                b"<body>Checking your browser before accessing</body></html>"
            ),
            "url": supplementary_url,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "science", SCIENCE_SAMPLE.doi)
            raw_payload = _typed_raw_payload(
                provider="science",
                source_url=SCIENCE_SAMPLE.landing_url,
                content_type="text/html",
                body=html.encode("utf-8"),
                route="html",
                markdown_text=f"# {SCIENCE_SAMPLE.title}\n\n## Results\n\n" + ("Body text " * 120),
                browser_context_seed={},
            )
            mocked_image_builder = mock.Mock(return_value=shared_image_fetcher)
            mocked_file_builder = mock.Mock(return_value=shared_file_fetcher)
            install_browser_workflow_deps(
                client,
                load_runtime_config=mock.Mock(return_value=runtime),
                ensure_runtime_ready=mock.Mock(),
                _build_shared_browser_image_fetcher=mocked_image_builder,
                _build_shared_browser_file_fetcher=mocked_file_builder,
            )
            with (
                mock.patch.object(html_assets, "_build_cookie_seeded_opener", return_value=object()) as mocked_opener,
                mock.patch.object(html_assets, "_request_with_opener", return_value=challenge_html) as mocked_request,
            ):
                result = client.download_related_assets(
                    SCIENCE_SAMPLE.doi,
                    {"doi": SCIENCE_SAMPLE.doi, "title": SCIENCE_SAMPLE.title},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="all",
                )

        mocked_opener.assert_called_once()
        mocked_request.assert_called_once()
        mocked_image_builder.assert_called_once()
        mocked_file_builder.assert_called_once()
        self.assertEqual(transport.calls, [])
        shared_image_fetcher.assert_called_once()
        shared_file_fetcher.assert_called_once()
        self.assertEqual(shared_file_fetcher.call_args.args[0], supplementary_url)
        self.assertEqual(
            [asset["kind"] for asset in result["assets"]],
            ["figure", "supplementary"],
        )
        self.assertEqual(result["assets"][1]["download_tier"], "supplementary_file")
        self.assertEqual(result["asset_failures"], [])
    def test_pnas_provider_download_related_assets_uses_figure_page_and_falls_back_to_preview(self) -> None:
        figure_page_url = "https://www.pnas.org/figures/figure-1"
        preview_url = "https://www.pnas.org/images/preview/figure1.png"
        full_size_url = "https://www.pnas.org/images/original/figure1.png"
        html = f"""
<article>
  <figure>
    <a href="{figure_page_url}">View figure</a>
    <img src="{preview_url}" alt="Preview figure" />
    <figcaption>Figure 1 caption</figcaption>
  </figure>
</article>
"""
        transport = AssetTransport({})
        client = pnas_provider.PnasClient(transport=transport, env={})
        initial_seed = {
            "browser_cookies": [{"name": "cf_clearance", "value": "secret", "domain": ".pnas.org", "path": "/"}],
            "browser_user_agent": "Mozilla/5.0",
            "browser_final_url": PNAS_SAMPLE.landing_url,
        }
        warmed_seed = {
            "browser_cookies": [{"name": "sessionid", "value": "warm", "domain": ".pnas.org", "path": "/"}],
            "browser_user_agent": "Mozilla/5.0",
            "browser_final_url": figure_page_url,
        }
        shared_fetcher = mock.Mock(
            side_effect=[
                None,
                {
                    "status_code": 200,
                    "headers": {"content-type": "image/png"},
                    "body": png_header(320, 240),
                    "url": preview_url,
                },
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "pnas", PNAS_SAMPLE.doi)
            raw_payload = _typed_raw_payload(
                provider="pnas",
                source_url=PNAS_SAMPLE.landing_url,
                content_type="text/html",
                body=html.encode("utf-8"),
                route="html",
                markdown_text=f"# {PNAS_SAMPLE.title}\n\n## Results\n\n" + ("Body text " * 120),
                browser_context_seed=initial_seed,
            )
            mocked_fetch = mock.Mock(
                return_value=_flaresolverr.FetchedPublisherHtml(
                    source_url=figure_page_url,
                    final_url=figure_page_url,
                    html=(
                        "<html><head>"
                        f"<meta property='og:image' content='{full_size_url}' />"
                        "</head><body></body></html>"
                    ),
                    response_status=200,
                    response_headers={"content-type": "text/html"},
                    title="Figure page",
                    summary="Figure page summary",
                    browser_context_seed=warmed_seed,
                )
            )
            mocked_builder = mock.Mock(return_value=shared_fetcher)
            install_browser_workflow_deps(
                client,
                load_runtime_config=mock.Mock(return_value=runtime),
                ensure_runtime_ready=mock.Mock(),
                fetch_html_with_flaresolverr=mocked_fetch,
                _build_shared_browser_image_fetcher=mocked_builder,
            )
            with (
                mock.patch.object(html_assets, "_build_cookie_seeded_opener") as mocked_opener,
                mock.patch.object(html_assets, "_request_with_opener") as mocked_request,
            ):
                result = client.download_related_assets(
                    PNAS_SAMPLE.doi,
                    {"doi": PNAS_SAMPLE.doi, "title": PNAS_SAMPLE.title},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="body",
                )
                saved_path = Path(result["assets"][0]["path"])
                saved_bytes = saved_path.read_bytes()

        mocked_fetch.assert_called_once()
        self.assertEqual(mocked_fetch.call_args.args[0], [figure_page_url])
        mocked_builder.assert_called_once()
        mocked_opener.assert_not_called()
        mocked_request.assert_not_called()
        self.assertEqual(transport.calls, [])
        self.assertEqual([call.args[0] for call in shared_fetcher.call_args_list], [full_size_url, preview_url])
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["asset_failures"], [])
        self.assertEqual(result["assets"][0]["download_tier"], "preview")
        self.assertEqual(saved_bytes, png_header(320, 240))
    def test_pnas_provider_download_related_assets_uses_shared_browser_primary_path_before_preview(self) -> None:
        """rule: rule-browser-primary-image-download-path"""
        figure_page_url = "https://www.pnas.org/figures/figure-1"
        preview_url = "https://www.pnas.org/images/preview/figure1.png"
        full_size_url = "https://www.pnas.org/images/original/figure1.png"
        html = f"""
<article>
  <figure>
    <a href="{figure_page_url}">View figure</a>
    <img src="{preview_url}" alt="Preview figure" />
    <figcaption>Figure 1 caption</figcaption>
  </figure>
</article>
"""
        transport = AssetTransport({})
        client = pnas_provider.PnasClient(transport=transport, env={})
        initial_seed = {
            "browser_cookies": [{"name": "cf_clearance", "value": "secret", "domain": ".pnas.org", "path": "/"}],
            "browser_user_agent": "Mozilla/5.0",
            "browser_final_url": PNAS_SAMPLE.landing_url,
        }
        shared_fetcher = mock.Mock(
            return_value={
                "status_code": 200,
                "headers": {"content-type": "image/jpeg"},
                "body": b"\xff\xd8\xffprimary-image",
                "url": full_size_url,
                "dimensions": {"width": 1200, "height": 800},
            },
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "pnas", PNAS_SAMPLE.doi)
            raw_payload = _typed_raw_payload(
                provider="pnas",
                source_url=PNAS_SAMPLE.landing_url,
                content_type="text/html",
                body=html.encode("utf-8"),
                route="html",
                markdown_text=f"# {PNAS_SAMPLE.title}\n\n## Results\n\n" + ("Body text " * 120),
                browser_context_seed=initial_seed,
            )
            install_browser_workflow_deps(
                client,
                load_runtime_config=mock.Mock(return_value=runtime),
                ensure_runtime_ready=mock.Mock(),
                fetch_html_with_flaresolverr=mock.Mock(
                    return_value=_flaresolverr.FetchedPublisherHtml(
                        source_url=figure_page_url,
                        final_url=figure_page_url,
                        html=(
                            "<html><head>"
                            f"<meta property='og:image' content='{full_size_url}' />"
                            "</head><body></body></html>"
                        ),
                        response_status=200,
                        response_headers={"content-type": "text/html"},
                        title="Figure page",
                        summary="Figure page summary",
                        browser_context_seed=initial_seed,
                    )
                ),
                _build_shared_browser_image_fetcher=mock.Mock(
                    return_value=shared_fetcher
                ),
            )
            mocked_builder = client.deps._build_shared_browser_image_fetcher
            with (
                mock.patch.object(html_assets, "_build_cookie_seeded_opener") as mocked_opener,
                mock.patch.object(html_assets, "_request_with_opener") as mocked_request,
            ):
                result = client.download_related_assets(
                    PNAS_SAMPLE.doi,
                    {"doi": PNAS_SAMPLE.doi, "title": PNAS_SAMPLE.title},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="body",
                )
                saved_path = Path(result["assets"][0]["path"])
                saved_bytes = saved_path.read_bytes()

        mocked_builder.assert_called_once()
        mocked_opener.assert_not_called()
        mocked_request.assert_not_called()
        self.assertEqual(transport.calls, [])
        shared_fetcher.assert_called_once()
        self.assertEqual(shared_fetcher.call_args.args[0], full_size_url)
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["asset_failures"], [])
        self.assertEqual(result["assets"][0]["download_tier"], "full_size")
        self.assertEqual(saved_bytes, b"\xff\xd8\xffprimary-image")
    def test_pnas_provider_reuses_cached_figure_page_for_repeated_assets(self) -> None:
        figure_page_url = "https://www.pnas.org/figures/figure-1"
        preview_url_one = "https://www.pnas.org/images/preview/figure1-a.png"
        preview_url_two = "https://www.pnas.org/images/preview/figure1-b.png"
        full_size_url = "https://www.pnas.org/images/original/figure1.png"
        html = f"""
<article>
  <figure>
    <a href="{figure_page_url}">View figure</a>
    <img src="{preview_url_one}" alt="Preview figure one" />
    <figcaption>Figure 1 caption</figcaption>
  </figure>
  <figure>
    <a href="{figure_page_url}">View figure</a>
    <img src="{preview_url_two}" alt="Preview figure two" />
    <figcaption>Figure 2 caption</figcaption>
  </figure>
</article>
"""
        transport = AssetTransport({})
        client = pnas_provider.PnasClient(transport=transport, env={})
        seed = {
            "browser_cookies": [{"name": "cf_clearance", "value": "secret", "domain": ".pnas.org", "path": "/"}],
            "browser_user_agent": "Mozilla/5.0",
            "browser_final_url": PNAS_SAMPLE.landing_url,
        }
        shared_fetcher = mock.Mock(
            return_value={
                "status_code": 200,
                "headers": {"content-type": "image/png"},
                "body": png_header(640, 480),
                "url": full_size_url,
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "pnas", PNAS_SAMPLE.doi)
            raw_payload = _typed_raw_payload(
                provider="pnas",
                source_url=PNAS_SAMPLE.landing_url,
                content_type="text/html",
                body=html.encode("utf-8"),
                route="html",
                markdown_text=f"# {PNAS_SAMPLE.title}\n\n## Results\n\n" + ("Body text " * 120),
                browser_context_seed=seed,
            )
            mocked_fetch = mock.Mock(
                return_value=_flaresolverr.FetchedPublisherHtml(
                    source_url=figure_page_url,
                    final_url=figure_page_url,
                    html=(
                        "<html><head>"
                        f"<meta property='og:image' content='{full_size_url}' />"
                        "</head><body></body></html>"
                    ),
                    response_status=200,
                    response_headers={"content-type": "text/html"},
                    title="Figure page",
                    summary="Figure page summary",
                    browser_context_seed=seed,
                )
            )
            install_browser_workflow_deps(
                client,
                load_runtime_config=mock.Mock(return_value=runtime),
                ensure_runtime_ready=mock.Mock(),
                fetch_html_with_flaresolverr=mocked_fetch,
                _build_shared_browser_image_fetcher=mock.Mock(
                    return_value=shared_fetcher
                ),
            )
            result = client.download_related_assets(
                PNAS_SAMPLE.doi,
                {"doi": PNAS_SAMPLE.doi, "title": PNAS_SAMPLE.title},
                raw_payload,
                Path(tmpdir),
                asset_profile="body",
            )

        self.assertEqual(mocked_fetch.call_count, 1)
        self.assertEqual(shared_fetcher.call_count, 1)
        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual(result["asset_failures"], [])
        self.assertEqual([asset["download_url"] for asset in result["assets"]], [full_size_url, full_size_url])
    def test_science_provider_reuses_cached_image_candidate_for_repeated_assets(self) -> None:
        full_size_url = "https://www.science.org/images/original/figure1.png"
        preview_url_one = "https://www.science.org/images/preview/figure1-a.png"
        preview_url_two = "https://www.science.org/images/preview/figure1-b.png"
        html = "<article><p>Body text</p></article>"
        transport = AssetTransport({})
        client = science_provider.ScienceClient(transport=transport, env={})
        shared_fetcher = mock.Mock(
            return_value={
                "status_code": 200,
                "headers": {"content-type": "image/png"},
                "body": png_header(640, 480),
                "url": full_size_url,
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "science", SCIENCE_SAMPLE.doi)
            raw_payload = _typed_raw_payload(
                provider="science",
                source_url=SCIENCE_SAMPLE.landing_url,
                content_type="text/html",
                body=html.encode("utf-8"),
                route="html",
                markdown_text=f"# {SCIENCE_SAMPLE.title}\n\n## Results\n\n" + ("Body text " * 120),
                browser_context_seed={},
            )
            mocked_fetch = mock.Mock()
            install_browser_workflow_deps(
                client,
                load_runtime_config=mock.Mock(return_value=runtime),
                ensure_runtime_ready=mock.Mock(),
                fetch_html_with_flaresolverr=mocked_fetch,
                _build_shared_browser_image_fetcher=mock.Mock(
                    return_value=shared_fetcher
                ),
            )
            with (
                mock.patch.object(
                    atypon_browser_workflow_asset_scopes,
                    "extract_scoped_html_assets",
                    return_value=[
                        {
                            "kind": "figure",
                            "heading": "Figure 1",
                            "caption": "Figure 1 caption",
                            "url": full_size_url,
                            "preview_url": preview_url_one,
                            "full_size_url": full_size_url,
                            "section": "body",
                        },
                        {
                            "kind": "figure",
                            "heading": "Figure 2",
                            "caption": "Figure 2 caption",
                            "url": full_size_url,
                            "preview_url": preview_url_two,
                            "full_size_url": full_size_url,
                            "section": "body",
                        },
                    ],
                ),
            ):
                result = client.download_related_assets(
                    SCIENCE_SAMPLE.doi,
                    {"doi": SCIENCE_SAMPLE.doi, "title": SCIENCE_SAMPLE.title},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="body",
                )

        mocked_fetch.assert_not_called()
        self.assertEqual(shared_fetcher.call_count, 1)
        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual(result["asset_failures"], [])
        self.assertEqual([asset["download_url"] for asset in result["assets"]], [full_size_url, full_size_url])
    def test_science_provider_records_preview_dimensions_and_acceptance(self) -> None:
        preview_url = "https://www.science.org/images/preview/figure1.png"
        html = f"""
<article>
  <figure>
    <img src="{preview_url}" alt="Preview figure" />
    <figcaption>Figure 1 caption</figcaption>
  </figure>
</article>
"""
        image_body = png_header(640, 480)
        transport = AssetTransport({})
        client = science_provider.ScienceClient(transport=transport, env={})
        shared_fetcher = mock.Mock(
            return_value={
                "status_code": 200,
                "headers": {"content-type": "image/png"},
                "body": image_body,
                "url": preview_url,
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "science", SCIENCE_SAMPLE.doi)
            raw_payload = _typed_raw_payload(
                provider="science",
                source_url=SCIENCE_SAMPLE.landing_url,
                content_type="text/html",
                body=html.encode("utf-8"),
                route="html",
                markdown_text=f"# {SCIENCE_SAMPLE.title}\n\n## Results\n\n" + ("Body text " * 120),
                browser_context_seed={},
            )
            mocked_fetch = mock.Mock()
            mocked_builder = mock.Mock(return_value=shared_fetcher)
            install_browser_workflow_deps(
                client,
                load_runtime_config=mock.Mock(return_value=runtime),
                ensure_runtime_ready=mock.Mock(),
                fetch_html_with_flaresolverr=mocked_fetch,
                _build_shared_browser_image_fetcher=mocked_builder,
            )
            with (
                mock.patch.object(html_assets, "_build_cookie_seeded_opener") as mocked_opener,
                mock.patch.object(html_assets, "_request_with_opener") as mocked_request,
            ):
                result = client.download_related_assets(
                    SCIENCE_SAMPLE.doi,
                    {"doi": SCIENCE_SAMPLE.doi, "title": SCIENCE_SAMPLE.title},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="body",
                )

        mocked_fetch.assert_not_called()
        mocked_builder.assert_called_once()
        mocked_opener.assert_not_called()
        mocked_request.assert_not_called()
        self.assertEqual(transport.calls, [])
        shared_fetcher.assert_called_once()
        self.assertEqual(shared_fetcher.call_args.args[0], preview_url)
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["download_tier"], "preview")
        self.assertEqual(result["assets"][0]["width"], 640)
        self.assertEqual(result["assets"][0]["height"], 480)
        self.assertTrue(result["assets"][0]["preview_accepted"])
