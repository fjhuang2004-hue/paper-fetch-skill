"""Quick test: ASM extraction through markdown.py dispatch."""
import sys
sys.path.insert(0, r"D:\git\paper-fetch-skill\src")

from paper_fetch.providers.atypon_browser_workflow.markdown import (
    extract_browser_workflow_markdown,
)

html_path = r"D:\Temp\asm_recon\asm_oa_1.html"
html = open(html_path, encoding="utf-8").read()
url = "https://journals.asm.org/doi/10.1128/aem.02455-25"

md, payload = extract_browser_workflow_markdown(html, url, "asm")
print(f"MD: {len(md)} chars")
print(f"Title: {payload.get('title')}")
print(f"Abstract: {len(payload.get('abstract_text', '') or '')} chars")
print(f"Sections: {len(payload.get('section_hints', []))}")

# Count headings and figures
headings = [l for l in md.split('\n') if l.startswith('#')]
figures = [l for l in md.split('\n') if l.startswith('![](')]
print(f"Headings: {len(headings)}, Figures: {len(figures)}")

# Show first 400 chars
print()
print("=== First 400 chars ===")
print(md[:400])

# Show last 300 chars
print()
print("=== Last 300 chars ===")
print(md[-300:])
