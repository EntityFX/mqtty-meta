"""Bounded browser acceptance check against a separately started Web host.
Requires Playwright/Chromium. Creates a short ideal-model job, never a native measurement.
"""
import argparse
import json
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright


def run(url, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    errors = []
    checks = []
    suffix = uuid.uuid4().hex[:8]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url + "/#/json")
        page.locator('#json-file').set_input_files({"name":"probe.json", "mimeType":"application/json", "buffer":json.dumps({"probe":"<img src=x onerror=alert(1)>","ticks":12}).encode()})
        page.locator('#json-content .json-key').first.wait_for()
        assert page.locator('#json-content img').count() == 0
        assert '<img src=x' in page.locator('#json-content').inner_text()
        checks.append('JSON viewer highlights content without interpreting payload as HTML')
        page.goto(url + "/#/designer")
        page.locator("#dsg-tpl-generate").click()
        page.locator("#dsg-canvas .node").first.wait_for()
        count = page.locator("#dsg-canvas .node").count()
        assert count > 3
        assert page.locator(".workspace-sidebar").bounding_box()["width"] <= 290
        assert page.locator(".workspace-commandbar #dsg-load").is_visible()
        checks.append("compact sidebar and visible saved topology chooser")
        # Selecting a node opens an editable properties form.
        page.locator("#dsg-canvas .node").last.click()
        page.locator("#prop-name").wait_for()
        page.locator("#prop-name").fill("Узел проверки")
        page.locator("#prop-apply").click()
        node = page.locator("#dsg-canvas .node").last
        box = node.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + 75, box["y"] + 55, steps=8)
        page.mouse.up()
        draft = page.evaluate("JSON.parse(sessionStorage.getItem('mqtty-editor-draft'))")
        assert any(n.get("name") == "Узел проверки" for n in draft["graph"]["nodes"])
        checks.append("node click, name edit, drag persisted")
        page.locator('a[href="#/simulations"]').first.click()
        page.locator("#mon-job").wait_for()
        page.locator('a[href="#/designer"]').first.click()
        page.locator("#dsg-canvas .node").first.wait_for()
        assert page.locator("#dsg-canvas .node").count() == count
        assert page.evaluate("JSON.parse(sessionStorage.getItem('mqtty-editor-draft'))") == draft
        checks.append("screen switching preserves full draft")
        page.locator('[data-panel="links"]').click()
        before = page.evaluate("JSON.parse(sessionStorage.getItem('mqtty-editor-draft')).graph.links.length")
        page.locator('#dsg-connect-mode').click()
        networks = page.locator('#dsg-canvas .node.network')
        networks.first.click()
        networks.last.click()
        after = page.evaluate("JSON.parse(sessionStorage.getItem('mqtty-editor-draft')).graph.links.length")
        assert after == before + 1
        page.locator('#dsg-link').select_option(str(after - 1))
        page.locator('#dsg-delete-link').click()
        assert page.evaluate("JSON.parse(sessionStorage.getItem('mqtty-editor-draft')).graph.links.length") == before
        checks.append("network-to-network link creation and removal")
        # Add a second topology without replacing the first, then connect components.
        page.locator('[data-panel="create"]').click()
        page.locator('#dsg-tpl-generate').click()
        page.wait_for_function(f"document.querySelectorAll('#dsg-canvas .node').length === {count * 2}")
        page.locator('[data-panel="links"]').click()
        page.locator('#dsg-connect-mode').click()
        page.locator('#dsg-canvas .node.network').first.click()
        page.locator('#dsg-canvas .node.network').nth(3).click()
        checks.append('second topology appended and connected without losing first')
        page.locator("#dsg-start-sim").click()
        page.wait_for_url("**/#/simulations")
        page.wait_for_function("document.querySelector('#mon-status')?.textContent === 'Завершена'", timeout=60000)
        page.wait_for_function("document.querySelectorAll('#mon-stats .stat-card').length > 0", timeout=10000)
        assert page.locator("#mon-canvas .node").count() > 0
        assert "summary.json" in page.locator("#mon-files").text_content()
        checks.append("saved topology executes and summary/statistics available")
        assert page.locator('#mon-search').is_visible()
        assert 'всего' in page.locator('#mon-page').inner_text()
        assert 'Завершена' in page.locator('#mon-job option:checked').inner_text()
        assert 'тик:' in page.locator('#mon-phase').inner_text()
        checks.append('monitoring exposes paged search, start/status labels and tick duration')
        page.locator("#mon-canvas .node").last.click()
        page.locator("#brokerModal.show").wait_for()
        page.wait_for_function("document.querySelector('#brokerModalBody')?.textContent.includes('Счётчики')")
        assert "Статистика узла" in page.locator("#brokerModalTitle").inner_text()
        page.locator("#brokerModal .btn-close").click()
        checks.append("monitoring node opens statistics modal")
        page.locator('#mon-layout').select_option('grid')
        page.locator('#mon-layout').select_option('auto')
        checks.append('monitoring supports grid and topology-aware layout')
        page.locator('#mon-results').click()
        page.locator('#resultsModal.show').wait_for()
        page.locator('[data-result-tab="charts"]').click()
        assert page.locator('#resultsModal svg').count() >= 1
        page.locator('[data-result-tab="files"]').click()
        assert 'summary.json' in page.locator('[data-result-panel="files"]').inner_text()
        page.locator('[data-json-preview][data-title="summary.json"]').click()
        page.locator('#json-preview').wait_for()
        assert page.locator('#json-preview .json-key').count() > 0
        page.locator('#json-preview button').click()
        page.locator('#resultsModal .btn-close').click()
        checks.append('results modal has timestamps, histogram charts and downloads')
        page.screenshot(path=str(output / "monitoring.png"), full_page=True)
        page.locator('a[href="#/designer"]').first.click()
        page.locator("#dsg-canvas .node").first.wait_for()
        page.screenshot(path=str(output / "designer.png"), full_page=True)
        # Update the saved graph without creating another record.
        before_count = len(page.request.get(url + '/api/topologies').json())
        page.locator('#dsg-name').fill('Изменённая топология проверки')
        page.locator('#dsg-save').click()
        page.wait_for_function("document.querySelector('[data-feedback]')?.textContent.includes('сохранена')")
        assert len(page.request.get(url + '/api/topologies').json()) == before_count
        checks.append('saved topology edited in place')
        page.locator('a[href="#/presets"]').first.click()
        page.locator('#pr-add').click()
        page.locator('#pr-name').fill('Browser check broker preset ' + suffix)
        page.locator('#pr-type').fill('ResearchBroker-' + suffix)
        page.locator('#pr-form button[type="submit"]').click()
        page.wait_for_function("document.querySelector('[data-feedback]')?.textContent.includes('сохранены')")
        page.locator('#pr-name').fill('Updated browser check broker preset ' + suffix)
        page.locator('#pr-form button[type="submit"]').click()
        page.locator('#pr-list strong', has_text='Updated browser check broker preset ' + suffix).wait_for()
        assert page.locator('#pr-type').input_value() == 'ResearchBroker-' + suffix
        checks.append('broker preset create and edit')
        page.locator('[data-tab="topologies"]').click()
        page.locator('#pr-add').click()
        page.locator('#pr-name').fill('Browser check topology preset ' + suffix)
        page.locator('#pr-form button[type="submit"]').click()
        page.locator('#pr-list strong', has_text='Browser check topology preset ' + suffix).wait_for()
        page.locator('#pr-name').fill('Updated browser check topology preset ' + suffix)
        page.locator('#pr-form button[type="submit"]').click()
        page.locator('#pr-list strong', has_text='Updated browser check topology preset ' + suffix).wait_for()
        checks.append('topology preset create and edit')
        browser.close()
    receipt = {"passed": not errors, "checks": checks, "browserErrors": errors, "url": url}
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert not errors, errors
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5091")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.url.rstrip("/"), args.output)
