"""Atypon browser-workflow HTML extraction package for Science, PNAS, Wiley, and AMS."""

from __future__ import annotations

from .markdown import extract_browser_workflow_markdown, extract_atypon_browser_workflow_markdown
from .asset_scopes import (
    extract_browser_workflow_asset_html_scopes,
    extract_scoped_html_assets,
    extract_supplementary_assets,
)
from .postprocess import rewrite_inline_figure_links

__all__ = [
    "extract_browser_workflow_asset_html_scopes",
    "extract_browser_workflow_markdown",
    "extract_atypon_browser_workflow_markdown",
    "extract_scoped_html_assets",
    "extract_supplementary_assets",
    "rewrite_inline_figure_links",
]
