#!/usr/bin/env python3
"""
LLM-based QA for yearbook collage pages.

Converts each PDF page to an image and sends it to Claude for visual QA,
checking for: face visibility, photo quality, cropping issues, layout problems.

Usage:
    python3 qa_llm.py /path/to/yearbook.pdf
    python3 qa_llm.py /path/to/yearbook.pdf --only "Claire Chen"
    python3 qa_llm.py /path/to/yearbook.pdf --page 5
"""

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import tempfile

import anthropic


def pdf_page_to_png(pdf_path: str, page_num: int, dpi: int = 200) -> bytes:
    """Convert a single PDF page to PNG bytes using poppler, PyMuPDF, or pdf2image."""
    # Try using poppler's pdftoppm with temp file output
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = os.path.join(tmpdir, 'page')
            result = subprocess.run(
                ['pdftoppm', '-png', '-r', str(dpi), '-f', str(page_num),
                 '-l', str(page_num), pdf_path, prefix],
                capture_output=True, timeout=30
            )
            if result.returncode == 0:
                # pdftoppm creates files like page-01.png
                import glob
                pngs = glob.glob(os.path.join(tmpdir, '*.png'))
                if pngs:
                    with open(pngs[0], 'rb') as f:
                        return f.read()
    except FileNotFoundError:
        pass

    # Fallback: use Python with PyMuPDF if available
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")
    except ImportError:
        pass

    # Fallback: use PIL + pdf2image
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(pdf_path, dpi=dpi, first_page=page_num,
                                   last_page=page_num)
        buf = io.BytesIO()
        images[0].save(buf, format='PNG')
        return buf.getvalue()
    except ImportError:
        pass

    raise RuntimeError(
        "No PDF renderer available. Install poppler (brew install poppler), "
        "PyMuPDF (pip install pymupdf), or pdf2image (pip install pdf2image)"
    )


def get_page_count(pdf_path: str) -> int:
    """Get total pages in a PDF."""
    from PyPDF2 import PdfReader
    return len(PdfReader(pdf_path).pages)


def get_child_name_from_page(pdf_path: str, page_num: int) -> str:
    """Extract child name from PDF page text."""
    from PyPDF2 import PdfReader
    reader = PdfReader(pdf_path)
    text = reader.pages[page_num - 1].extract_text() or ''
    # The name is typically the first non-empty line
    for line in text.split('\n'):
        line = line.strip()
        if line and len(line) > 2 and not line.startswith('Dear') and not line.startswith('We'):
            return line
    return f"Page {page_num}"


QA_PROMPT = """You are a yearbook page quality inspector. Analyze this child's yearbook collage page and report any issues.

Check for these specific problems:
1. **Face visibility**: Are faces clearly visible in each photo? Are any faces cut off, cropped out, or obscured?
2. **Photo quality**: Are any photos blurry, dark, or poorly exposed?
3. **Subject presence**: Does each photo actually show a child? Flag any photos that show only scenery, objects, or text without a child visible.
4. **Cropping issues**: Are any photos cropped in a way that cuts off heads, shows only partial bodies awkwardly, or misses the main subject?
5. **Layout**: Does the page layout look correct? Is text readable? Is the name displayed properly?
6. **Baby photo**: If there's a circular baby photo, is it properly showing a baby/infant face?

Respond in this JSON format:
{
  "child_name": "Name shown on page",
  "overall_grade": "A/B/C/D/F",
  "issues": [
    {
      "photo_position": "top-left/top-center/top-right/bottom-left/bottom-right/baby",
      "severity": "minor/moderate/major",
      "description": "Brief description of the issue"
    }
  ],
  "summary": "One sentence overall assessment"
}

Grade guide:
- A: All photos show the child clearly with good face visibility
- B: Minor issues (slightly off-center, one photo not ideal but acceptable)
- C: Noticeable problems (face partially cut, one photo missing child)
- D: Significant issues (multiple photos missing faces or badly cropped)
- F: Major problems (most photos don't show the child properly)

Only report actual issues. If the page looks good, return an empty issues array with grade A."""


def qa_page(client: anthropic.Anthropic, png_bytes: bytes, page_num: int) -> dict:
    """Send a page image to Claude for QA analysis."""
    b64_image = base64.standard_b64encode(png_bytes).decode('utf-8')

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64_image,
                    },
                },
                {
                    "type": "text",
                    "text": QA_PROMPT,
                },
            ],
        }],
    )

    text = response.content[0].text
    # Extract JSON from response
    try:
        # Try to find JSON in the response
        start = text.index('{')
        end = text.rindex('}') + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {
            "child_name": f"Page {page_num}",
            "overall_grade": "?",
            "issues": [{"photo_position": "unknown", "severity": "major",
                        "description": f"Could not parse LLM response: {text[:200]}"}],
            "summary": "QA parse error"
        }


def main():
    parser = argparse.ArgumentParser(description='LLM-based yearbook QA')
    parser.add_argument('pdf', help='Path to yearbook PDF')
    parser.add_argument('--only', help='Only QA pages matching this name')
    parser.add_argument('--page', type=int, help='Only QA this page number')
    parser.add_argument('--output', default=None, help='Save results JSON to this path')
    parser.add_argument('--dpi', type=int, default=150, help='Render DPI (default 150)')
    args = parser.parse_args()

    client = anthropic.Anthropic()
    total_pages = get_page_count(args.pdf)
    print(f"Yearbook: {args.pdf} ({total_pages} pages)")

    results = []
    pages_to_check = range(1, total_pages + 1)

    if args.page:
        pages_to_check = [args.page]
    elif args.only:
        pages_to_check = []
        for i in range(1, total_pages + 1):
            name = get_child_name_from_page(args.pdf, i)
            if args.only.lower() in name.lower():
                pages_to_check.append(i)
        if not pages_to_check:
            print(f"No pages matching '{args.only}'")
            sys.exit(1)

    grade_counts = {}
    issues_by_severity = {"minor": 0, "moderate": 0, "major": 0}

    for page_num in pages_to_check:
        child_name = get_child_name_from_page(args.pdf, page_num)
        print(f"\n[{page_num}/{total_pages}] {child_name}...", end=" ", flush=True)

        try:
            png_bytes = pdf_page_to_png(args.pdf, page_num, dpi=args.dpi)
            result = qa_page(client, png_bytes, page_num)
            result["page_number"] = page_num
            results.append(result)

            grade = result.get("overall_grade", "?")
            grade_counts[grade] = grade_counts.get(grade, 0) + 1

            issues = result.get("issues", [])
            for issue in issues:
                sev = issue.get("severity", "minor")
                issues_by_severity[sev] = issues_by_severity.get(sev, 0) + 1

            # Print result
            grade_color = {"A": "32", "B": "33", "C": "33", "D": "31", "F": "31"}.get(grade, "37")
            print(f"\033[{grade_color}m{grade}\033[0m", end="")
            if issues:
                print(f" - {len(issues)} issue(s)")
                for issue in issues:
                    sev_icon = {"minor": ".", "moderate": "!", "major": "X"}.get(issue["severity"], "?")
                    print(f"    [{sev_icon}] {issue['photo_position']}: {issue['description']}")
            else:
                print(" - No issues")

        except Exception as e:
            print(f"ERROR: {e}")
            results.append({
                "page_number": page_num,
                "child_name": child_name,
                "overall_grade": "?",
                "issues": [{"severity": "major", "description": str(e)}],
                "summary": "Error during QA"
            })

    # Summary
    print(f"\n{'='*60}")
    print(f"QA SUMMARY - {len(results)} pages checked")
    print(f"{'='*60}")
    print(f"Grades: {', '.join(f'{g}={c}' for g, c in sorted(grade_counts.items()))}")
    print(f"Issues: {issues_by_severity['major']} major, {issues_by_severity['moderate']} moderate, {issues_by_severity['minor']} minor")

    # List pages needing attention
    problem_pages = [r for r in results if r.get("overall_grade", "A") not in ("A", "B")]
    if problem_pages:
        print(f"\nPages needing attention:")
        for r in problem_pages:
            print(f"  Page {r['page_number']}: {r.get('child_name', '?')} - Grade {r['overall_grade']}")
            for issue in r.get("issues", []):
                print(f"    - [{issue.get('severity', '?')}] {issue.get('photo_position', '?')}: {issue['description']}")

    # Save results
    output_path = args.output or args.pdf.replace('.pdf', '_qa_results.json')
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")


if __name__ == '__main__':
    main()
