"""Bounded browser check of article-1 MODEL execution; never starts a real broker."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright


def run(url, preset, output):
    started=time.monotonic()
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True)
        page=browser.new_page()
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url.rstrip('/')+'/#/twin-validation')
        page.locator('#twin-preset').select_option(preset)
        values={'name':'Article 1 Web technical pilot','publishers':16,'qos':1,'seed':42,
                'warmupSeconds':.01,'measurementSeconds':.05,'drainSeconds':.1,'tickSeconds':.00001,'timeLimitSeconds':60}
        for key,value in values.items():page.locator(f'[name="{key}"]').fill(str(value))
        invalid=page.locator('#twin-form input:invalid').evaluate_all('(items)=>items.map(e=>({name:e.name,value:e.value,message:e.validationMessage,stepMismatch:e.validity.stepMismatch}))')
        assert not invalid,invalid
        page.locator('#twin-validate').click()
        page.wait_for_function("document.querySelector('#twin-feedback').textContent.includes('Параметры корректны')",timeout=15000)
        page.locator('#twin-run').click()
        page.wait_for_function("document.querySelector('#twin-job strong')?.textContent.includes('Completed')",timeout=60000)
        first_id=page.evaluate("JSON.parse(sessionStorage.getItem('mqtty-selected-simulation'))")
        first=page.request.get(url+'/api/simulations/'+first_id).json()
        assert first['engine']=='twinValidation' and first['totalTicks']>=16000 and first['step']>0,first
        page.locator('#twin-results').click()
        page.locator('#resultsModal').get_by_text('Скорость завершения, публикаций/с',exact=True).wait_for()
        page.locator('[data-result-tab="charts"]').click()
        page.locator('[data-result-panel="charts"]').get_by_text('Фазы в модельном времени',exact=True).wait_for()
        charts=page.locator('[data-result-panel="charts"]').inner_text()
        assert '5000 тиков' in charts and '10000 тиков' in charts,charts
        page.locator('[data-result-tab="files"]').click()
        for name in ('publishes.ndjson','connections.ndjson','profile-snapshot.json','sha256.json'):
            page.locator('[data-result-panel="files"]').get_by_text('⇩ '+name,exact=True).wait_for()
        summary=page.request.get(url+f'/api/simulations/{first_id}/results/summary.json').body()
        config={**values,'brokerPresetId':preset}
        launched=page.request.post(url+'/api/twin-validations',data=config)
        assert launched.status==202,launched.text()
        repeat_id=launched.json()['id']
        for _ in range(120):
            repeat=page.request.get(url+'/api/simulations/'+repeat_id).json()
            if repeat['state'] not in ('Queued','Running'):break
            time.sleep(.25)
        assert repeat['state']=='Completed',repeat
        repeated_summary=page.request.get(url+f'/api/simulations/{repeat_id}/results/summary.json').body()
        assert summary==repeated_summary,'Web repeat summary differs'
        seal_response=page.request.get(url+f'/api/simulations/{first_id}/results/sha256.json')
        assert seal_response.ok,seal_response.text()
        seals=seal_response.json()
        for name,expected in seals.items():
            response=page.request.get(url+f'/api/simulations/{first_id}/results/{name}')
            assert response.ok and hashlib.sha256(response.body()).hexdigest()==expected,name
        assert not errors,errors
        receipt={'passed':True,'url':url,'firstJob':first_id,'repeatJob':repeat_id,'firstStatus':first,
                 'summarySha256':hashlib.sha256(summary).hexdigest(),'repeatSummaryByteIdentical':True,
                 'sealedFilesVerified':len(seals),'browserErrors':errors,'elapsedSeconds':time.monotonic()-started,
                 'scope':'Technical model pilot; no new native measurement or accuracy claim'}
        Path(output).write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(receipt,ensure_ascii=True))
        browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--preset',default='mosquitto-default')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if Path(args.output).exists():raise FileExistsError('Use a new receipt path')
    run(args.url.rstrip('/'),args.preset,args.output)
