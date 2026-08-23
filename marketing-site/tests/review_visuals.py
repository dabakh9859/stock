from pathlib import Path
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".impeccable" / "review"
OUTPUT.mkdir(parents=True, exist_ok=True)


def capture(page, name: str, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto("http://127.0.0.1:3000", wait_until="networkidle")
    page.wait_for_selector(".hero-world")
    page.wait_for_timeout(1800)
    page.screenshot(path=str(OUTPUT / f"{name}-top.png"), full_page=False)

    if width >= 900:
        story = page.locator("#produit")
        story_top = story.evaluate("el => el.getBoundingClientRect().top + window.scrollY")
        page.evaluate("y => window.scrollTo(0, y + 2350)", story_top)
    else:
        page.locator("#produit").scroll_into_view_if_needed()
    page.wait_for_timeout(700)
    page.screenshot(path=str(OUTPUT / f"{name}-story.png"), full_page=False)

    if width < 900:
        for index in range(4):
            step = page.locator(f".journey-step--{index}")
            step.evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - window.innerHeight * 0.50)")
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUTPUT / f"{name}-story-{index + 1}.png"), full_page=False)

    page.locator("#metiers").evaluate("el => el.scrollIntoView({block: 'start', behavior: 'instant'})")
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUTPUT / f"{name}-profiles.png"), full_page=False)

    print({
        "viewport": name,
        "hero": page.locator(".hero-world").count(),
        "profile": page.locator(".profile-product h3").count(),
        "overflow": page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"),
        "images_loaded": page.evaluate("[...document.images].every(i => i.complete && i.naturalWidth > 0)"),
    })


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    errors = []
    page.on("console", lambda message: errors.append(f"console: {message.text}") if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(f"page: {error}"))
    capture(page, "desktop", 1440, 1000)
    capture(page, "user-1280", 1280, 720)
    capture(page, "mobile", 390, 844)
    browser.close()

if errors:
    raise RuntimeError("\n".join(errors))
