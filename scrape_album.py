#!/usr/bin/env python3
"""
Scrape a Google Photos shared album to map baby names (from comments)
to photo filenames (from info panel).

Usage:
    python3 scrape_album.py "https://photos.google.com/share/..." --output mapping.json
"""

import argparse
import json
import re
import time

from playwright.sync_api import sync_playwright


def scrape_album(album_url: str, output_path: str, headless: bool = False):
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )

        print(f"Opening album...")
        page.goto(album_url, wait_until="networkidle", timeout=30000)
        time.sleep(4)

        # Scroll to load all photos
        for _ in range(30):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            time.sleep(0.3)
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1)

        photo_count = page.evaluate(
            "() => document.querySelectorAll('a[href*=\"/photo/\"]').length"
        )
        print(f"Found {photo_count} photos\n")

        if photo_count == 0:
            browser.close()
            return []

        # Click first photo
        page.evaluate("document.querySelector('a[href*=\"/photo/\"]').click()")
        time.sleep(2)

        for idx in range(photo_count):
            name = ""
            filename = ""

            try:
                time.sleep(0.5)

                # --- Step 1: Open info panel for filename ---
                page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        const label = (btn.getAttribute('aria-label') || '');
                        if (label === 'Open info') {
                            btn.click(); return;
                        }
                    }
                }""")
                time.sleep(1.2)

                info_text = page.evaluate("() => document.body.innerText")
                fn_match = re.search(
                    r'([\w\-\(\) ]+\.(?:jpg|jpeg|png|heic|mp4|mov|JPG|JPEG|PNG|MP4))',
                    info_text
                )
                if fn_match:
                    filename = fn_match.group(1).strip()

                # Close info panel by clicking the same button again
                page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        const label = (btn.getAttribute('aria-label') || '');
                        if (label === 'Close info' || label === 'Open info') {
                            btn.click(); return;
                        }
                    }
                }""")
                time.sleep(0.5)

                # --- Step 2: Click the comment button (.f2Vjld) ---
                comment_clicked = page.evaluate("""() => {
                    const el = document.querySelector('.f2Vjld');
                    if (el) { el.click(); return true; }
                    return false;
                }""")

                if comment_clicked:
                    time.sleep(1.2)
                    activity_text = page.evaluate("() => document.body.innerText")

                    # Parse: lines after "Activity" / "Close activity"
                    lines = activity_text.split('\n')
                    in_activity = False
                    for line in lines:
                        line = line.strip()
                        if line == 'Activity' or 'Close activity' in line:
                            in_activity = True
                            continue
                        if in_activity and line and not line.startswith('Photo'):
                            # Skip author lines ("Name · Xd")
                            if '·' in line:
                                continue
                            # Skip UI text
                            if line in ('Say something', 'Like', '1', '2',
                                        'comments', 'Save', 'Info',
                                        'More options', 'Zoom'):
                                continue
                            if line.startswith('Press '):
                                continue
                            name = line
                            break

                    # Close activity panel
                    page.evaluate("""() => {
                        const btns = document.querySelectorAll('button');
                        for (const btn of btns) {
                            const label = (btn.getAttribute('aria-label') || '');
                            if (label.includes('Close activity')) {
                                btn.click(); return;
                            }
                        }
                    }""")
                    time.sleep(0.5)

            except Exception as e:
                print(f"  {idx+1}: error - {str(e)[:60]}")

            results.append({"index": idx, "name": name, "filename": filename})

            parts = []
            if name:
                parts.append(f"name='{name}'")
            if filename:
                parts.append(f"file='{filename}'")
            print(f"  {idx+1}/{photo_count}: {', '.join(parts) or 'no data'}")

            # Next photo
            page.keyboard.press("ArrowRight")
            time.sleep(1)

        browser.close()

    # Save raw results
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)

    # Build mapping
    mapping = {}
    for r in results:
        if r['name'] and r['filename']:
            mapping[r['name']] = r['filename']

    no_name = [r for r in results if not r['name'] and r['filename']]

    print(f"\n{'='*50}")
    print(f"Total: {len(results)} | Matched: {len(mapping)} | No comment: {len(no_name)}")
    print(f"\n=== BABY NAME → FILENAME MAPPING ===")
    for name, fname in sorted(mapping.items()):
        print(f"  {name:30s} → {fname}")

    if no_name:
        print(f"\n=== PHOTOS WITHOUT COMMENTS ===")
        for r in no_name:
            print(f"  #{r['index']}: {r['filename']}")

    mapping_path = output_path.replace('.json', '_clean.json')
    with open(mapping_path, 'w') as f:
        json.dump(mapping, f, indent=2)
    print(f"\nMapping saved: {mapping_path}")

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url', help='Shared album URL')
    parser.add_argument('--output', default='baby_mapping.json')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    scrape_album(args.url, args.output, headless=args.headless)


if __name__ == '__main__':
    main()
