from playwright.sync_api import expect, sync_playwright


def intersection_ratio(first, second):
    width = max(
        0,
        min(first["x"] + first["width"], second["x"] + second["width"])
        - max(first["x"], second["x"]),
    )
    height = max(
        0,
        min(first["y"] + first["height"], second["y"] + second["height"])
        - max(first["y"], second["y"]),
    )
    return (width * height) / min(
        first["width"] * first["height"], second["width"] * second["height"]
    )


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1875, "height": 1029})
    page.goto("http://127.0.0.1:3000", wait_until="networkidle")
    page.locator("#produit").scroll_into_view_if_needed()
    expect(page.locator(".journey-count strong")).to_have_text("02", timeout=10000)
    page.wait_for_function(
        """() => [`.event-stock`, `.label-shop`].every(
          selector => Number.parseFloat(getComputedStyle(document.querySelector(selector)).opacity) > .95
        )"""
    )
    page.screenshot(
        path=".impeccable/review/desktop-auto-stage-clear.png", full_page=False
    )

    stage_scale = page.locator(".world-stage").evaluate(
        "el => new DOMMatrixReadOnly(getComputedStyle(el).transform).a"
    )
    stock = page.locator(".event-stock").bounding_box()
    shop_label = page.locator(".label-shop").bounding_box()
    assert stock and shop_label
    overlap = intersection_ratio(stock, shop_label)
    print(f"stage_scale={stage_scale:.3f} stock_label_overlap={overlap:.3f}")
    browser.close()

    assert abs(stage_scale - 0.94) <= 0.01, "world stage is not dezoomed from the start"
    assert overlap <= 0.02, "stock card and shop label visually collide"
