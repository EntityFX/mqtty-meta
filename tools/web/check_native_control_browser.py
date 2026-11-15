"""One bounded browser acceptance of saved native controls; starts one model, no native broker."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright


def run(url, references, presets, output):
    output=Path(output);output.mkdir()  # Preserve every attempt; never overwrite.
    started=time.monotonic();receipt={'passed':False,'newNativeRuns':0,'modelRuns':0,'url':url}
    def save(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url+'/#/twin-validation');page.locator('#twin-reference').wait_for()
            # Check every genuine bundle through the same file-import form.
            imported={}
            for broker,info in presets.items():
                source=Path(references)/(broker+'.native-control.json')
                identity=hashlib.sha256(source.read_bytes()).hexdigest()
                page.locator('details summary').click()
                page.locator('#twin-reference-file').set_input_files(str(source))
                page.locator('#twin-reference-import').click()
                page.wait_for_function("document.querySelector('#twin-feedback').textContent.startsWith('Контроль загружен')")
                assert page.locator('#twin-reference').input_value()==identity
                page.locator('#twin-preset').select_option(info['presetId'])
                if broker=='Aedes':
                    page.locator('[name="measurementSeconds"]').fill('.1')
                    page.locator('#twin-validate').click()
                    page.wait_for_function("document.querySelector('#twin-feedback').textContent.startsWith('Ошибка:')")
                    mismatch=page.locator('#twin-feedback').inner_text()
                    assert 'Окна' in mismatch,mismatch
                    receipt['mismatchedWindowRejected']=mismatch
                page.locator('#twin-reference-apply').click()
                page.locator('[name="timeLimitSeconds"]').fill('180')
                page.locator('#twin-validate').click()
                page.wait_for_function("document.querySelector('#twin-feedback').textContent.startsWith('Параметры корректны')")
                feedback=page.locator('#twin-feedback').inner_text();assert identity in feedback,feedback
                imported[broker]={'sha256':identity,'feedback':feedback}
                page.locator('details summary').click()
            receipt['imported']=imported
            page.locator('#twin-preset').select_option(presets['Aedes']['presetId'])
            page.locator('#twin-reference').select_option(imported['Aedes']['sha256'])
            page.locator('#twin-reference-apply').click()
            page.locator('[name="name"]').fill('Native control browser acceptance Aedes')
            page.locator('[name="seed"]').fill('42')
            page.locator('[name="tickSeconds"]').fill('.00001')
            page.locator('#twin-run').click();receipt['modelRuns']=1
            page.wait_for_function("document.querySelector('#twin-job strong')?.textContent.match(/Completed|Failed|Cancelled/)",timeout=190000)
            job_id=page.evaluate("JSON.parse(sessionStorage.getItem('mqtty-selected-simulation'))")
            job=page.request.get(url+'/api/simulations/'+job_id).json();receipt['job']=job
            assert job['state']=='Completed',job
            page.locator('#twin-results').click()
            card=page.locator('#native-control-comparison');card.wait_for()
            text=card.inner_text();assert 'Натурный контроль' in text and '3' in text,text
            receipt['comparisonVisibleText']=text
            page.screenshot(path=str(output/'comparison.png'),full_page=True)
            prefix=url+f'/api/simulations/{job_id}/results/'
            listing=page.request.get(prefix.rstrip('/')).json()
            seals=page.request.get(prefix+'sha256.json').json()
            for name,expected in seals.items():
                response=page.request.get(prefix+name);assert response.ok,name
                assert hashlib.sha256(response.body()).hexdigest()==expected,name
            receipt['sealedFilesVerified']=len(seals)
            comparison=page.request.get(prefix+'native-control-comparison.json').json()
            save('comparison.json',comparison)
            snapshot=page.request.get(prefix+'native-control-snapshot.json').body()
            assert hashlib.sha256(snapshot).hexdigest()==imported['Aedes']['sha256']
            assert comparison['referenceSha256']==imported['Aedes']['sha256']
            assert comparison['referenceRuns']==3
            source=json.loads(snapshot);runs=source['runs']
            means={'rps':sum(r['completed']/r['measurementSeconds'] for r in runs)/3,
                   'p99':sum(r['p99PublishMs'] for r in runs)/3,
                   'failure':sum(r['failed']/r['attempted'] for r in runs)/3,
                   'loss':sum(1-r['completedIdsDelivered']/r['completed'] for r in runs)/3}
            for key,value in means.items():
                assert abs(comparison['metrics'][key]['reference']-value)<1e-8,key
            receipt['referenceMeansIndependentlyChecked']=means
            page.locator('[data-result-tab="charts"]').click()
            charts=page.locator('[data-result-panel="charts"]').inner_text()
            assert '400000 тиков' in charts and '500000 тиков' in charts,charts
            receipt['chartsText']=charts
            page.screenshot(path=str(output/'phases.png'),full_page=True)
            page.locator('[data-result-tab="files"]').click()
            page.locator('[data-title="native-control-comparison.json"]').click()
            page.locator('#json-preview .json-key').first.wait_for()
            receipt['highlightedJsonVisible']=True
            page.screenshot(path=str(output/'json.png'),full_page=True)
            assert not errors,errors
            receipt.update(browserErrors=errors,browserVersion=browser.version,passed=True)
            browser.close()
    except Exception as error:
        receipt['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        receipt['elapsedSeconds']=time.monotonic()-started
        save('receipt.json',receipt)
    print(json.dumps({'passed':True,'jobId':receipt['job']['id'],'elapsedSeconds':receipt['elapsedSeconds']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--references',required=True)
    parser.add_argument('--presets',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    run(args.url.rstrip('/'),args.references,json.loads(Path(args.presets).read_text(encoding='utf-8')),args.output)
