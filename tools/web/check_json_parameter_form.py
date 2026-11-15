"""Browser check of lazy profile controls; synthetic fields, no scientific runs."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright


def run(root, output):
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(Path(root).resolve())))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            result = page.evaluate('''async () => {
                const module = await import('/js/json-viewer.js');
                const container = document.createElement('div'); document.body.append(container);
                const initial = {enabled:true,limit:10,optional:null,bins:Array.from({length:500},(_,i)=>i)};
                const read = module.jsonParameterForm(container,initial);
                const collapsedInputs = container.querySelectorAll('input').length;
                container.querySelector('details').open = true;
                await new Promise(resolve=>setTimeout(resolve,30));
                const openedInputs = container.querySelectorAll('input').length;
                container.querySelector('button').click();
                const expandedInputs = container.querySelectorAll('input').length;
                container.querySelector('input[type=checkbox]').checked = false;
                container.querySelector('input[type=number]').value = '12.5';
                const edited = read();
                return {collapsedInputs,openedInputs,expandedInputs,typedValuesPreserved:edited.enabled===false&&edited.limit===12.5&&edited.optional===null,
                    unseenArrayValuesPreserved:JSON.stringify(edited.bins)===JSON.stringify(initial.bins)};
            }''')
            browser.close()
        assert result['collapsedInputs'] == 3, result
        assert result['openedInputs'] == 103, result
        assert result['expandedInputs'] == 203, result
        assert result['typedValuesPreserved'] and result['unseenArrayValuesPreserved'], result
        target = Path(output)
        with target.open('x', encoding='utf-8') as stream:
            json.dump(dict(passed=True, **result), stream, indent=2)
        print(json.dumps(dict(passed=True, **result)))
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    run(args.root, args.output)
