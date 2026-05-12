# ruff: noqa: F403,F405
from __future__ import annotations

from ._atypon_browser_workflow_provider_support import *


class AtyponBrowserWorkflowProviderRetryTests(AtyponBrowserWorkflowProviderTestCase):
    def test_browser_workflow_download_related_assets_retries_after_partial_failures(self) -> None:
        figure_url = "https://www.pnas.org/images/large/figure1.png"
        html = f"""
<article>
  <figure>
    <img src="{figure_url}" alt="Figure 1" />
    <figcaption>Figure 1 caption</figcaption>
  </figure>
</article>
"""
        client = pnas_provider.PnasClient(transport=AssetTransport({}), env={})
        initial_seed = {
            "browser_cookies": [{"name": "cf_clearance", "value": "initial", "domain": ".pnas.org", "path": "/"}],
            "browser_user_agent": "Mozilla/5.0",
            "browser_final_url": PNAS_SAMPLE.landing_url,
        }
        refreshed_seed = {
            "browser_cookies": [{"name": "cf_clearance", "value": "refreshed", "domain": ".pnas.org", "path": "/"}],
            "browser_user_agent": "Mozilla/5.0",
            "browser_final_url": PNAS_SAMPLE.landing_url,
        }
        failing_fetcher = mock.Mock(return_value=None)
        successful_fetcher = mock.Mock(
            return_value={
                "status_code": 200,
                "headers": {"content-type": "image/png"},
                "body": png_header(640, 480),
                "url": figure_url,
                "dimensions": {"width": 640, "height": 480},
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
                browser_context_seed=initial_seed,
            )
            with (
                mock.patch.object(browser_workflow, "load_runtime_config", return_value=runtime),
                mock.patch.object(browser_workflow, "ensure_runtime_ready"),
                mock.patch.object(
                    browser_workflow,
                    "warm_browser_context_with_flaresolverr",
                    return_value=refreshed_seed,
                ) as mocked_warm,
                mock.patch.object(
                    browser_workflow,
                    "_build_shared_playwright_image_fetcher",
                    side_effect=[failing_fetcher, successful_fetcher],
                ) as mocked_builder,
            ):
                result = client.download_related_assets(
                    PNAS_SAMPLE.doi,
                    {"doi": PNAS_SAMPLE.doi, "title": PNAS_SAMPLE.title},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="body",
                )

        self.assertEqual(mocked_builder.call_count, 2)
        mocked_warm.assert_called_once()
        self.assertEqual(
            mocked_builder.call_args_list[1].kwargs["browser_context_seed_getter"]()["browser_cookies"][0]["value"],
            "refreshed",
        )
        failing_fetcher.assert_called_once()
        successful_fetcher.assert_called_once()
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["asset_failures"], [])
        self.assertEqual(result["assets"][0]["download_url"], figure_url)
    def test_browser_workflow_retries_only_failed_supplementary_assets(self) -> None:
        doi = "10.5555/retry-supplement"
        article_url = "https://example.test/article"
        figure_asset = {
            "kind": "figure",
            "heading": "Figure 1",
            "caption": "Figure caption",
            "url": "https://example.test/figure1.png",
            "section": "body",
        }
        supplementary_asset = {
            "kind": "supplementary",
            "heading": "Supplement 1",
            "url": "https://example.test/supplement.docx",
            "section": "supplementary",
        }
        figure_result = {
            "assets": [
                {
                    "kind": "figure",
                    "heading": "Figure 1",
                    "caption": "Figure caption",
                    "download_url": "https://example.test/figure1.png",
                    "source_url": "https://example.test/figure1.png",
                    "section": "body",
                }
            ],
            "asset_failures": [],
        }
        supplementary_failure = {
            "assets": [],
            "asset_failures": [
                {
                    "kind": "supplementary",
                    "heading": "Supplement 1",
                    "source_url": "https://example.test/supplement.docx",
                    "section": "supplementary",
                    "reason": "cloudflare_challenge",
                }
            ],
        }
        supplementary_success = {
            "assets": [
                {
                    "kind": "supplementary",
                    "heading": "Supplement 1",
                    "download_url": "https://example.test/supplement.docx",
                    "source_url": "https://example.test/supplement.docx",
                    "section": "supplementary",
                    "download_tier": "supplementary_file",
                }
            ],
            "asset_failures": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "science", doi)
            client = browser_workflow.BrowserWorkflowClient(AssetTransport({}), {})
            client.name = "science"
            raw_payload = _typed_raw_payload(
                provider="science",
                source_url=article_url,
                content_type="text/html",
                body=b"<html></html>",
                route="html",
                browser_context_seed={"browser_final_url": article_url},
            )
            with (
                mock.patch.object(browser_workflow, "load_runtime_config", return_value=runtime),
                mock.patch.object(browser_workflow, "ensure_runtime_ready"),
                mock.patch.object(
                    browser_workflow,
                    "_cached_browser_workflow_assets",
                    return_value=[figure_asset, supplementary_asset],
                ),
                mock.patch.object(
                    browser_workflow,
                    "warm_browser_context_with_flaresolverr",
                    return_value={"browser_final_url": article_url},
                ) as mocked_warm,
                mock.patch.object(
                    browser_workflow,
                    "download_figure_assets_with_image_document_fetcher",
                    return_value=figure_result,
                ) as mocked_figures,
                mock.patch.object(
                    browser_workflow,
                    "download_supplementary_assets",
                    side_effect=[supplementary_failure, supplementary_success],
                ) as mocked_supplementary,
            ):
                result = client.download_related_assets(
                    doi,
                    {"doi": doi, "title": "Retry Supplement"},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="all",
                )

        mocked_warm.assert_called_once()
        mocked_figures.assert_called_once()
        self.assertEqual(mocked_figures.call_args.kwargs["assets"], [figure_asset])
        self.assertEqual(mocked_supplementary.call_count, 2)
        self.assertEqual(mocked_supplementary.call_args_list[0].kwargs["assets"], [supplementary_asset])
        self.assertEqual(mocked_supplementary.call_args_list[1].kwargs["assets"], [supplementary_asset])
        self.assertEqual(
            [(asset["kind"], asset["download_url"]) for asset in result["assets"]],
            [
                ("figure", "https://example.test/figure1.png"),
                ("supplementary", "https://example.test/supplement.docx"),
            ],
        )
        self.assertEqual(result["asset_failures"], [])
    def test_browser_workflow_retries_only_failed_body_assets(self) -> None:
        doi = "10.5555/retry-figure"
        article_url = "https://example.test/article"
        first_figure = {
            "kind": "figure",
            "heading": "Figure 1",
            "caption": "Figure caption",
            "url": "https://example.test/figure1.png",
            "section": "body",
        }
        second_figure = {
            "kind": "figure",
            "heading": "Figure 2",
            "caption": "Second figure caption",
            "url": "https://example.test/figure2.png",
            "section": "body",
        }
        supplementary_asset = {
            "kind": "supplementary",
            "heading": "Supplement 1",
            "url": "https://example.test/supplement.docx",
            "section": "supplementary",
        }
        initial_body_result = {
            "assets": [
                {
                    "kind": "figure",
                    "heading": "Figure 2",
                    "caption": "Second figure caption",
                    "download_url": "https://example.test/figure2.png",
                    "source_url": "https://example.test/figure2.png",
                    "section": "body",
                }
            ],
            "asset_failures": [
                {
                    "kind": "figure",
                    "heading": "Figure 1",
                    "caption": "Figure caption",
                    "source_url": "https://example.test/figure1.png",
                    "section": "body",
                    "reason": "cloudflare_challenge",
                }
            ],
        }
        retry_body_result = {
            "assets": [
                {
                    "kind": "figure",
                    "heading": "Figure 1",
                    "caption": "Figure caption",
                    "download_url": "https://example.test/figure1.png",
                    "source_url": "https://example.test/figure1.png",
                    "section": "body",
                }
            ],
            "asset_failures": [],
        }
        supplementary_result = {
            "assets": [
                {
                    "kind": "supplementary",
                    "heading": "Supplement 1",
                    "download_url": "https://example.test/supplement.docx",
                    "source_url": "https://example.test/supplement.docx",
                    "section": "supplementary",
                    "download_tier": "supplementary_file",
                }
            ],
            "asset_failures": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "science", doi)
            client = browser_workflow.BrowserWorkflowClient(AssetTransport({}), {})
            client.name = "science"
            raw_payload = _typed_raw_payload(
                provider="science",
                source_url=article_url,
                content_type="text/html",
                body=b"<html></html>",
                route="html",
                browser_context_seed={"browser_final_url": article_url},
            )
            with (
                mock.patch.object(browser_workflow, "load_runtime_config", return_value=runtime),
                mock.patch.object(browser_workflow, "ensure_runtime_ready"),
                mock.patch.object(
                    browser_workflow,
                    "_cached_browser_workflow_assets",
                    return_value=[first_figure, second_figure, supplementary_asset],
                ),
                mock.patch.object(
                    browser_workflow,
                    "warm_browser_context_with_flaresolverr",
                    return_value={"browser_final_url": article_url},
                ) as mocked_warm,
                mock.patch.object(
                    browser_workflow,
                    "download_figure_assets_with_image_document_fetcher",
                    side_effect=[initial_body_result, retry_body_result],
                ) as mocked_figures,
                mock.patch.object(
                    browser_workflow,
                    "download_supplementary_assets",
                    return_value=supplementary_result,
                ) as mocked_supplementary,
            ):
                result = client.download_related_assets(
                    doi,
                    {"doi": doi, "title": "Retry Figure"},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="all",
                )

        mocked_warm.assert_called_once()
        self.assertEqual(mocked_figures.call_count, 2)
        self.assertEqual(mocked_figures.call_args_list[0].kwargs["assets"], [first_figure, second_figure])
        self.assertEqual(mocked_figures.call_args_list[1].kwargs["assets"], [first_figure])
        mocked_supplementary.assert_called_once()
        self.assertEqual(mocked_supplementary.call_args.kwargs["assets"], [supplementary_asset])
        self.assertEqual(
            sorted((asset["kind"], asset["download_url"]) for asset in result["assets"]),
            [
                ("figure", "https://example.test/figure1.png"),
                ("figure", "https://example.test/figure2.png"),
                ("supplementary", "https://example.test/supplement.docx"),
            ],
        )
        self.assertEqual(result["asset_failures"], [])
    def test_wiley_provider_download_related_assets_uses_shared_playwright_primary_path(self) -> None:
        full_size_url = "https://onlinelibrary.wiley.com/cms/asset/full/figure1.jpg"
        html = f"""
<article>
  <figure>
    <img src="{full_size_url}" alt="Figure 1" />
    <figcaption>Figure 1 caption</figcaption>
  </figure>
</article>
"""
        client = wiley_provider.WileyClient(transport=AssetTransport({}), env={})
        seed = {
            "browser_cookies": [{"name": "cf_clearance", "value": "secret", "domain": ".wiley.com", "path": "/"}],
            "browser_user_agent": "Mozilla/5.0",
            "browser_final_url": "https://onlinelibrary.wiley.com/doi/10.1111/gcb.16011",
        }
        shared_fetcher = mock.Mock(
            return_value={
                "status_code": 200,
                "headers": {"content-type": "image/jpeg"},
                "body": b"\xff\xd8\xffprimary-image",
                "url": full_size_url,
                "dimensions": {"width": 1400, "height": 900},
            },
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "wiley", "10.1111/gcb.16011")
            raw_payload = _typed_raw_payload(
                provider="wiley",
                source_url="https://onlinelibrary.wiley.com/doi/10.1111/gcb.16011",
                content_type="text/html",
                body=html.encode("utf-8"),
                route="html",
                markdown_text="# Title\n\n## Results\n\n" + ("Body text " * 120),
                browser_context_seed=seed,
            )
            with (
                mock.patch.object(browser_workflow, "load_runtime_config", return_value=runtime),
                mock.patch.object(browser_workflow, "ensure_runtime_ready"),
                mock.patch.object(html_assets, "_build_cookie_seeded_opener") as mocked_opener,
                mock.patch.object(html_assets, "_request_with_opener") as mocked_request,
                mock.patch.object(
                    browser_workflow,
                    "_build_shared_playwright_image_fetcher",
                    return_value=shared_fetcher,
                ) as mocked_builder,
            ):
                result = client.download_related_assets(
                    "10.1111/gcb.16011",
                    {"doi": "10.1111/gcb.16011", "title": "Title"},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="body",
                )
                saved_bytes = Path(result["assets"][0]["path"]).read_bytes()

        mocked_builder.assert_called_once()
        mocked_opener.assert_not_called()
        mocked_request.assert_not_called()
        shared_fetcher.assert_called_once()
        self.assertEqual(shared_fetcher.call_args.args[0], full_size_url)
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["asset_failures"], [])
        self.assertEqual(result["assets"][0]["download_tier"], "full_size")
        self.assertEqual(saved_bytes, b"\xff\xd8\xffprimary-image")
    def test_wiley_provider_download_related_assets_reuses_shared_playwright_fetcher_across_assets(self) -> None:
        first_url = "https://onlinelibrary.wiley.com/cms/asset/full/figure1.jpg"
        second_url = "https://onlinelibrary.wiley.com/cms/asset/full/figure2.jpg"
        html = f"""
<article>
  <figure>
    <img src="{first_url}" alt="Figure 1" />
    <figcaption>Figure 1 caption</figcaption>
  </figure>
  <figure>
    <img src="{second_url}" alt="Figure 2" />
    <figcaption>Figure 2 caption</figcaption>
  </figure>
</article>
"""
        client = wiley_provider.WileyClient(transport=AssetTransport({}), env={})
        seed = {
            "browser_cookies": [{"name": "cf_clearance", "value": "secret", "domain": ".wiley.com", "path": "/"}],
            "browser_user_agent": "Mozilla/5.0",
            "browser_final_url": "https://onlinelibrary.wiley.com/doi/10.1111/gcb.16011",
        }
        shared_fetcher = mock.Mock(
            side_effect=[
                {
                    "status_code": 200,
                    "headers": {"content-type": "image/jpeg"},
                    "body": b"\xff\xd8\xfffigure-one",
                    "url": first_url,
                    "dimensions": {"width": 1200, "height": 800},
                },
                {
                    "status_code": 200,
                    "headers": {"content-type": "image/jpeg"},
                    "body": b"\xff\xd8\xfffigure-two",
                    "url": second_url,
                    "dimensions": {"width": 1400, "height": 900},
                },
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._runtime_config(tmpdir, "wiley", "10.1111/gcb.16011")
            raw_payload = _typed_raw_payload(
                provider="wiley",
                source_url="https://onlinelibrary.wiley.com/doi/10.1111/gcb.16011",
                content_type="text/html",
                body=html.encode("utf-8"),
                route="html",
                markdown_text="# Title\n\n## Results\n\n" + ("Body text " * 120),
                browser_context_seed=seed,
            )
            with (
                mock.patch.object(browser_workflow, "load_runtime_config", return_value=runtime),
                mock.patch.object(browser_workflow, "ensure_runtime_ready"),
                mock.patch.object(html_assets, "_build_cookie_seeded_opener") as mocked_opener,
                mock.patch.object(html_assets, "_request_with_opener") as mocked_request,
                mock.patch.object(
                    browser_workflow,
                    "_build_shared_playwright_image_fetcher",
                    return_value=shared_fetcher,
                ) as mocked_builder,
            ):
                result = client.download_related_assets(
                    "10.1111/gcb.16011",
                    {"doi": "10.1111/gcb.16011", "title": "Title"},
                    raw_payload,
                    Path(tmpdir),
                    asset_profile="body",
                )

        mocked_builder.assert_called_once()
        mocked_opener.assert_not_called()
        mocked_request.assert_not_called()
        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual(result["asset_failures"], [])
        self.assertEqual(shared_fetcher.call_count, 2)
        self.assertEqual(shared_fetcher.call_args_list[0].args[0], first_url)
        self.assertEqual(shared_fetcher.call_args_list[1].args[0], second_url)
        shared_fetcher.close.assert_called_once()
