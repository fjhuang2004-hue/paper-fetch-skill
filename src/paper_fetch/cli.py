"""CLI entrypoint for paper-fetch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .artifacts import ArtifactStore
from .config import build_runtime_env, resolve_cli_download_dir
from .models import FetchEnvelope, RenderOptions
from .providers.base import ProviderFailure
from .service import FetchStrategy, PaperFetchFailure, fetch_paper
from .utils import sanitize_filename
from .workflow.pipeline import FetchPipeline, MarkdownSaveSpec
from .workflow.request_builder import build_fetch_pipeline_request
from .workflow.rendering import rewrite_markdown_asset_links
from .workflow.rendering import save_markdown_to_disk as save_markdown_to_disk_for_target


def save_markdown_to_disk(envelope: FetchEnvelope, *, output_dir: Path, render: RenderOptions) -> Path | None:
    return save_markdown_to_disk_for_target(
        envelope,
        output_dir=output_dir,
        render=render,
        request_label="--save-markdown",
    )


def serialize_envelope(envelope: FetchEnvelope, *, output_format: str, markdown_override: str | None = None) -> str:
    if output_format == "markdown":
        return markdown_override if markdown_override is not None else envelope.markdown or ""
    if output_format == "json":
        if envelope.article is None:
            raise ValueError("CLI json output requires the article payload.")
        return envelope.article.to_json()
    if envelope.article is None:
        raise ValueError("CLI both output requires the article payload.")
    markdown = markdown_override if markdown_override is not None else envelope.markdown
    return json.dumps({"article": envelope.article.to_dict(), "markdown": markdown}, ensure_ascii=False, indent=2)


def write_output(serialized: str, output: str) -> None:
    if output == "-":
        sys.stdout.write(serialized)
        if not serialized.endswith("\n"):
            sys.stdout.write("\n")
        return
    Path(output).write_text(serialized, encoding="utf-8")


def _has_explicit_option(argv: list[str], option: str) -> bool:
    return any(value == option or value.startswith(f"{option}=") for value in argv)


def _should_save_formatted_output_copy(args: argparse.Namespace, *, explicit_format: bool) -> bool:
    return bool(explicit_format and args.output == "-" and args.output_dir)


def _formatted_output_filename(envelope: FetchEnvelope, *, output_format: str) -> str:
    identifier = envelope.doi
    if not identifier and envelope.article is not None:
        identifier = envelope.article.metadata.title
    if not identifier and envelope.metadata is not None:
        identifier = envelope.metadata.title
    stem = sanitize_filename(identifier or "article")
    suffix = {
        "markdown": ".md",
        "json": ".json",
        "both": ".both.json",
    }[output_format]
    return f"{stem}{suffix}"


def save_formatted_output_copy(
    envelope: FetchEnvelope,
    *,
    output_dir: Path,
    output_format: str,
    render: RenderOptions,
) -> Path:
    target = output_dir / _formatted_output_filename(envelope, output_format=output_format)
    markdown_override = (
        rewrite_markdown_asset_links(
            envelope.markdown or "",
            envelope,
            target_path=target,
            render=render,
        )
        if output_format in {"markdown", "both"}
        else None
    )
    serialized = serialize_envelope(envelope, output_format=output_format, markdown_override=markdown_override)
    return ArtifactStore.from_download_dir(output_dir).write_text_file(target, serialized, encoding="utf-8")


def parse_max_tokens(value: str) -> int | str:
    normalized = value.strip().lower()
    if normalized == "full_text":
        return "full_text"
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max_tokens must be a positive integer or 'full_text'.") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("max_tokens must be greater than 0.")
    return parsed


def _compute_modes(args: argparse.Namespace) -> set[str]:
    modes = {"markdown"} if args.format == "markdown" else {"article"}

    # Writing Markdown to a file or saving an extra Markdown copy needs the
    # structured article payload so we can rewrite local asset links relative
    # to the target path and decide whether full text was actually usable.
    if args.format == "markdown" and (args.output != "-" or getattr(args, "save_output_copy", False)):
        modes.add("article")
    if args.format == "both" or args.save_markdown:
        modes.add("markdown")
    if args.save_markdown:
        modes.add("article")
    return modes


def exit_code_for_error(error: Exception) -> int:
    if isinstance(error, PaperFetchFailure):
        status = error.status
    elif isinstance(error, ProviderFailure):
        status = error.code
    else:
        status = "error"

    if status == "ambiguous":
        return 2
    if status == "no_access":
        return 3
    if status == "rate_limited":
        return 4
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch AI-friendly full text for a paper by DOI, URL, or title.")
    parser.add_argument("--query", required=True, help="DOI, paper landing URL, or title query")
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "both"),
        default="markdown",
        help=(
            "Serialization format for stdout or --output. When explicitly set with --output-dir "
            "and stdout output, a same-format copy is also saved under --output-dir."
        ),
    )
    parser.add_argument("--output", default="-", help="Output destination. Use - for stdout.")
    parser.add_argument(
        "--output-dir",
        help=(
            "Directory for raw provider downloads, HTML, and assets. Also receives the formatted "
            "output copy when --format is explicit and --output is stdout. Defaults to "
            "PAPER_FETCH_DOWNLOAD_DIR or the user data downloads directory."
        ),
    )
    parser.add_argument("--no-download", action="store_true", help="Do not write provider PDF/binary payloads to disk.")
    parser.add_argument(
        "--save-markdown",
        action="store_true",
        help=(
            "Also write the rendered AI Markdown full text to disk (defaults to PAPER_FETCH_DOWNLOAD_DIR "
            "or the user data downloads directory, "
            "overridable via --output-dir). Only writes when full text was actually retrieved. "
            "For Wiley the preferred Markdown route is provider-managed HTML; TDM or browser PDF/ePDF "
            "fallbacks may be lower fidelity than Elsevier XML or publisher-managed HTML."
        ),
    )
    parser.add_argument("--include-refs", choices=("none", "top10", "all"), default=None)
    parser.add_argument("--asset-profile", choices=("none", "body", "all"), default=None)
    parser.add_argument("--max-tokens", type=parse_max_tokens, default="full_text")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_args = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(raw_args)
    args.save_output_copy = _should_save_formatted_output_copy(
        args,
        explicit_format=_has_explicit_option(raw_args, "--format"),
    )

    try:
        runtime_env = build_runtime_env()
        output_dir = Path(args.output_dir) if args.output_dir else resolve_cli_download_dir(runtime_env)
        modes = _compute_modes(args)
        render_options = RenderOptions(
            include_refs=args.include_refs,
            asset_profile=args.asset_profile,
            max_tokens=args.max_tokens,
        )
        result = FetchPipeline(fetch_paper).run(
            build_fetch_pipeline_request(
                query=args.query,
                modes=modes,
                strategy=FetchStrategy(
                    allow_metadata_only_fallback=True,
                    asset_profile=args.asset_profile,
                ),
                render=render_options,
                env=runtime_env,
                download_dir=output_dir,
                no_download=args.no_download,
                markdown_save=(
                    MarkdownSaveSpec(
                        output_dir=output_dir,
                        render=render_options,
                        request_label="--save-markdown",
                    )
                    if args.save_markdown
                    else None
                ),
            )
        )
        envelope = result.envelope
        markdown_override = (
            rewrite_markdown_asset_links(
                envelope.markdown or "",
                envelope,
                target_path=Path(args.output),
                render=render_options,
            )
            if args.output != "-" and args.format in {"markdown", "both"}
            else None
        )
        serialized = serialize_envelope(envelope, output_format=args.format, markdown_override=markdown_override)
        if args.save_output_copy:
            save_formatted_output_copy(
                envelope,
                output_dir=output_dir,
                output_format=args.format,
                render=render_options,
            )
        write_output(serialized, args.output)
        return 0
    except PaperFetchFailure as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "status": exc.status,
                    "reason": exc.reason,
                    "candidates": exc.candidates or None,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        return exit_code_for_error(exc)
    except ProviderFailure as exc:
        sys.stderr.write(json.dumps({"status": exc.code, "reason": exc.message}, ensure_ascii=False) + "\n")
        return exit_code_for_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
