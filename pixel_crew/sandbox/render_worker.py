"""Рендер HTML внутри песочницы (Windows Job Object). Запускается ТОЛЬКО из
sandbox/windows_runner.py.

Читает html_path, пишет скриншоты и result.json в out_dir.
Блокируются все запросы, кроме file:/data:/about:, чтобы зафиксировать попытки выйти наружу.
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

VIEWPORTS = {"mobile": (375, 812), "desktop": (1280, 800)}
MIN_TARGET = 24  # WCAG 2.2 SC 2.5.8 Target Size (Minimum), px

PROBE = """(minTarget) => {
  const de = document.documentElement;
  const small = [];
  for (const el of document.querySelectorAll('a, button, input, select, textarea, [role=button]')) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || r.width === 0 || r.height === 0) continue;
    if (r.width < minTarget || r.height < minTarget)
      small.push((el.tagName.toLowerCase() + ' «' + (el.innerText || el.value || '').trim().slice(0, 30) + '» '
                  + Math.round(r.width) + '×' + Math.round(r.height)));
  }
  const h1 = document.querySelector('h1');
  return {
    scroll_width: de.scrollWidth,
    viewport_width: window.innerWidth,
    horizontal_overflow: de.scrollWidth > window.innerWidth + 1,
    page_height: de.scrollHeight,
    h1_visible: !!h1 && h1.getBoundingClientRect().height > 0,
    small_targets: small,
    visible_text: (document.body ? document.body.innerText : '').slice(0, 4000),
  };
}"""


def main(html_path, out_dir):
    t0 = time.time()
    result = {"viewports": {}, "console_errors": [], "page_errors": [], "blocked_requests": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-gpu"])   # песочница Chromium включена
        for name, (w, h) in VIEWPORTS.items():
            page = browser.new_page(viewport={"width": w, "height": h})

            def route(r):
                if r.request.url.startswith(("file:", "data:", "about:")):
                    r.continue_()
                else:
                    if r.request.url not in result["blocked_requests"]:
                        result["blocked_requests"].append(r.request.url)
                    r.abort()

            page.route("**/*", route)
            def add(key, text):
                if text[:200] not in result[key]:
                    result[key].append(text[:200])

            page.on("console", lambda m: m.type == "error" and add("console_errors", m.text))
            page.on("pageerror", lambda e: add("page_errors", str(e)))
            page.goto(Path(html_path).as_uri(), wait_until="load", timeout=15000)
            info = page.evaluate(PROBE, MIN_TARGET)
            shot = f"{out_dir}/screenshot_{name}.png"
            page.screenshot(path=shot, full_page=True)
            info["screenshot"] = f"screenshot_{name}.png"
            result["viewports"][name] = info
            page.close()
        browser.close()
    result["render_seconds"] = round(time.time() - t0, 2)
    with open(f"{out_dir}/result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
