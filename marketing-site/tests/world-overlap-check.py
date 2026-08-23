from playwright.sync_api import expect, sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 2254, "height": 1243})
    page.goto("http://127.0.0.1:3000", wait_until="networkidle")
    page.locator("#produit").scroll_into_view_if_needed()
    expect(page.locator(".journey-count strong")).to_have_text("04", timeout=15000)
    page.wait_for_timeout(1400)
    page.screenshot(path=".impeccable/review/desktop-overlap-fixed.png", full_page=False)

    supplier = page.locator(".supplier-node").bounding_box()
    shop = page.locator(".shop-node").bounding_box()
    assert supplier and shop

    overlap = max(
        0,
        min(supplier["x"] + supplier["width"], shop["x"] + shop["width"])
        - max(supplier["x"], shop["x"]),
    )
    ratio = overlap / min(supplier["width"], shop["width"])
    print(f"final_world_overlap={overlap:.1f}px ratio={ratio:.3f}")
    browser.close()

    assert ratio <= 0.08, "supplier and shop visually collide in the final story state"
