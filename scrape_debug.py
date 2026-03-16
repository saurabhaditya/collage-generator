#!/usr/bin/env python3
"""Debug: find and click the comment button to reveal comment text."""

import time
from playwright.sync_api import sync_playwright

url = "https://photos.google.com/share/AF1QipMDy7g35PQIH21p1aOHibKA9FdW8DzM6bcHOTvR6pXlEBhJRKL_IoTPh4grZia-7g?key=MlR4ejE2S0k3RzY3Q2kzT0VYVWZkeG5ybjVBWUt3"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(url, wait_until="networkidle", timeout=30000)
    time.sleep(4)

    # Click first photo
    page.evaluate("document.querySelector('a[href*=\"/photo/\"]').click()")
    time.sleep(2)

    # Navigate to photo #5 (SavitBaranwalBaby)
    for _ in range(4):
        page.keyboard.press("ArrowRight")
        time.sleep(1.5)

    # List ALL buttons and their aria-labels
    buttons = page.evaluate("""() => {
        const result = [];
        document.querySelectorAll('button, [role="button"]').forEach(el => {
            result.push({
                tag: el.tagName,
                ariaLabel: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                text: el.innerText.trim().substring(0, 50),
                className: (el.className || '').substring(0, 60),
            });
        });
        return result;
    }""")

    print("=== ALL BUTTONS ===")
    for b in buttons:
        print(f"  label='{b['ariaLabel']}' title='{b['title']}' text='{b['text']}' class='{b['className']}'")

    # Look for comment-related elements
    print("\n=== COMMENT-RELATED ELEMENTS ===")
    comment_els = page.evaluate("""() => {
        const result = [];
        // Look for anything with comment in aria-label, class, or nearby
        document.querySelectorAll('*').forEach(el => {
            const label = el.getAttribute('aria-label') || '';
            const cls = el.className || '';
            const title = el.getAttribute('title') || '';
            if (label.toLowerCase().includes('comment') ||
                cls.toString().toLowerCase().includes('comment') ||
                title.toLowerCase().includes('comment')) {
                result.push({
                    tag: el.tagName,
                    ariaLabel: label,
                    className: cls.toString().substring(0, 60),
                    text: el.innerText.trim().substring(0, 50),
                    title: title,
                });
            }
        });
        return result;
    }""")

    for ce in comment_els:
        print(f"  {ce}")

    # Also look for the chat/comment bubble icon
    print("\n=== CLICKABLE ICONS NEAR BOTTOM ===")
    icons = page.evaluate("""() => {
        const result = [];
        document.querySelectorAll('button, [role="button"], a').forEach(el => {
            const rect = el.getBoundingClientRect();
            // Bottom area of the page (where comment icon is)
            if (rect.top > 500) {
                result.push({
                    tag: el.tagName,
                    ariaLabel: el.getAttribute('aria-label') || '',
                    text: el.innerText.trim().substring(0, 30),
                    top: Math.round(rect.top),
                    left: Math.round(rect.left),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                });
            }
        });
        return result;
    }""")
    for ic in icons:
        print(f"  {ic}")

    # Try clicking elements that might be the comment bubble
    # The "1" text we saw is likely in a button/link near the comment icon
    print("\n=== TRYING TO CLICK COMMENT AREA ===")

    # Click at the position where we saw the comment icon (bottom-right area)
    # From screenshot: icon is at roughly x=680, y=400 (in a 700x450 screenshot)
    # Scale to viewport 1400x900: approximately x=1360, y=800
    page.mouse.move(1340, 850)
    time.sleep(0.5)
    page.screenshot(path="/Users/asb/Downloads/debug_hover_comment.png")

    # Click it
    page.mouse.click(1340, 850)
    time.sleep(2)
    page.screenshot(path="/Users/asb/Downloads/debug_clicked_comment.png")

    # Get text after clicking
    text = page.evaluate("() => document.body.innerText")
    print("=== TEXT AFTER CLICKING COMMENT AREA ===")
    for line in text.split('\n'):
        line = line.strip()
        if line:
            print(f"  |{line}|")

    browser.close()
