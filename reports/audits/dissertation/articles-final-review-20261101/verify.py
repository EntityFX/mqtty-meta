"""Check current Markdown tables against accepted compact evidence; no new experiments.

Run from any directory with Python 3.12. Raw-event acceptance remains a separate check.
"""
from pathlib import Path
import csv
import hashlib
import json
import math
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
checks = []
inputs = {}


def check(name, condition):
    checks.append({'check': name, 'passed': bool(condition)})
    if not condition:
        raise AssertionError(name)


def read(path):
    p = ROOT / path
    inputs[path] = hashlib.sha256(p.read_bytes()).hexdigest()
    return p.read_text(encoding='utf-8-sig')


def load(path):
    return json.loads(read(path))


def near(name, actual, expected, tolerance):
    check(name, math.isfinite(actual) and abs(actual - expected) <= tolerance)


def number(value):
    return float(value.replace('%', '').replace('п.п.', '').replace(' ', '').replace(',', '.'))


def table(text, header):
    block = text[text.index(header):].split('\n\n', 1)[0]
    return [[c.strip() for c in line.strip().strip('|').split('|')]
            for line in block.splitlines()[2:] if line.startswith('|')]


def main():
    a1path = 'docs/dissertation/articles/article-1-twin-validation.md'
    a2path = 'docs/dissertation/articles/article-2-relay-federation.md'
    a1, a2 = read(a1path), read(a2path)
    root = 'reports/audits/dissertation/'
    twin = load(root + 'article-1-matched-main-final-v1/verification.json')
    temporal = list(csv.DictReader(read(root + 'linux-selected-20261030/temporal-comparison.csv').splitlines()))
    net = list(csv.DictReader(read(root + 'network-main-final-v1/raw-recount/comparison.csv').splitlines()))
    transit = list(csv.DictReader(read(root + 'network-main-final-v1/transit.csv').splitlines()))
    acceptance = load(root + 'network-main-final-v1/acceptance.json')
    queue = load(root + 'linux-selected-20261030/remaining-result/acceptance.json')
    core = load(root + 'linux-selected-20261030/acceptance.json')['core']
    check('article1 accepted jobs', twin['jobs'] == 40)
    check('article1 model comparisons', len(twin['modelComparisons']) == 12)
    check('article1 replays', len(twin['replays']) == 4)
    conditions = {c['broker']: c for c in twin['conditions']}
    main_table = table(a1, '| Брокер | Показатель | Новый контроль |')
    check('article1 comparison row count', len(main_table) == 16)
    keys = {'Завершений/с': 'rps', 'p99, мс': 'p99', 'Ошибки, %': 'failure', 'Недоставка, %': 'loss'}
    for row in main_table:
        broker, label = row[:2]
        metric = keys[label]
        c = conditions[broker]
        factor = 100 if metric in ('loss', 'failure') else 1
        for pos, kind in [(2, 'control'), (3, 'calibration'), (4, 'model')]:
            near(f'{broker}/{metric}/{kind}', number(row[pos]), c['statistics'][kind][metric]['mean'] * factor, .000051)
        ref = c['statistics']['control'][metric]['mean']
        for pos, kind in [(5, 'calibration'), (6, 'model')]:
            value = c['statistics'][kind][metric]['mean']
            error = abs(value - ref) if metric in ('failure', 'loss') else abs(value-ref)/abs(ref)
            near(f'{broker}/{metric}/{kind} error', number(row[pos]), 100*error, .00051)
    thresholds = {'rps': .15, 'p99': .20, 'failure': .05, 'loss': .05}
    for row in twin['modelComparisons']:
        for metric, threshold in thresholds.items():
            ref, model = row['native'][metric], row['model'][metric]
            error = abs(model-ref) if metric in ('failure','loss') else abs(model-ref)/abs(ref)
            near(f"seed {row['broker']}/{row['seed']}/{metric}", error, row['errors'][metric], 1e-12)
            check(f"threshold {row['broker']}/{row['seed']}/{metric}", error <= threshold)
    max_rows = table(a1, '| Брокер | Наибольшая ошибка RPS')
    check('article1 maximum rows', len(max_rows) == 4)
    for row in max_rows:
        broker = row[0].replace(' Classic', '')
        selected = [x for x in twin['modelComparisons'] if x['broker'] == broker]
        for pos, metric in [(1,'rps'),(2,'p99'),(3,'loss')]:
            near(f'{broker}/{metric}/maximum table', number(row[pos]), 100*max(x['errors'][metric] for x in selected), .00051)
    temporal_table = table(a1, '| Брокер | p99 первого контроля, мс |')
    check('temporal rows', len(temporal_table) == 4)
    for row in temporal_table:
        saved = next(x for x in temporal if x['broker']==row[0] and x['metric']=='p99PublishMs')
        for pos, key in [(1,'oldNativeMean'),(2,'newNativeMean'),(3,'modelMean')]:
            near(f'temporal {row[0]}/{key}', number(row[pos]), float(saved[key]), .000051)
        check(f'temporal pass count {row[0]}', row[4] == saved['passedSeeds']+' из 3')
    check('temporal accepted comparisons', sum(int(r['passedSeeds']) for r in temporal if r['metric']=='p99PublishMs') == 10)
    ci = conditions['Aedes']['statistics']['control']['p99']
    half = 4.30265 * ci['standardDeviation'] / math.sqrt(3)
    near('Aedes interval lower', ci['mean']-half, 1.4617, .000051)
    near('Aedes interval upper', ci['mean']+half, 2.8947, .000051)
    check('network full matrix and repeats', acceptance['matrixRawPassed']==162 and acceptance['replaysRawPassed']==6)
    check('network repeated files', acceptance['replayScientificFileComparisons']==36)
    rows = table(a2, '| Сеть | QoS | Сообщ./с |')
    check('network table conditions', len(rows)==18 and len(net)==54)
    names = {'Линия':'line', 'Кольцо':'ring', 'Звезда с транзитом':'star'}
    for row in rows:
        selected = {r['mode']: r for r in net if r['topology']==names[row[0]] and r['qos']==row[1] and r['offeredRps']==row[2]}
        check(f'network match {row[:3]}', len(selected)==3)
        for mode in ['single','relay']:
            check(f'complete {row[:3]}/{mode}', float(selected[mode]['uniqueDeliveries'])==float(selected[mode]['expectedDeliveries']))
        fed=selected['federation']
        near(f'fullness {row[:3]}', number(row[4]),100*float(fed['uniqueDeliveries'])/float(fed['expectedDeliveries']),.00051)
        for pos, mode in [(5,'single'),(6,'relay'),(7,'federation')]:
            near(f'delivery p99 {row[:3]}/{mode}',number(row[pos]),float(selected[mode]['receiverP99Ms']),.00051)
        check(f'seeds {row[:3]}', all(int(r['matchedSeedCount'])==int(row[3])==3 for r in selected.values()))
    for row in table(a2,'| Режим | Вход в транзит, байт |'):
        selected=[r for r in transit if r['id'].startswith('matrix.star.'+row[0]+'.q2.r5000.')]
        check('transit triplet '+row[0],len(selected)==3)
        for pos,key in [(1,'inboundBytes'),(2,'transferredPackets'),(3,'refused')]:
            near('transit '+row[0]+'/'+key,number(row[pos]),sum(float(r[key]) for r in selected)/3,.001)
    for q in queue['windows']:
        capacity=q['queueCapacity']
        r=q['independentCount']
        near(f'queue {capacity} completeness',100*r['uniquePairs']/r['expectedPairs'],19.3045 if capacity==1024 else 38.2565,1e-9)
        check(f'queue {capacity} common denominator',r['expectedPairs']==200000 and r['sourcePublications']==50000)
    check('partial result not promoted',not queue['completedPairAccepted'] and not queue['partialRunPromotedToCompleted'])
    check('parallel original byte gate failed',not core['originalStrictGatePassed'])
    check('parallel receipt multiset equal',core['fullReceiptMultisetEqual'])
    check('no measured stable speedup',not core['stableSpeedupEstablished'])
    local_links=0
    for path,text in [(a1path,a1),(a2path,a2)]:
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if re.match(r'https?://|#',target):
                continue
            p=(ROOT/path).parent / target.split('#')[0]
            check('local link '+path+' '+target,p.is_file())
            local_links+=1
        # Reference coverage, not an audit of full external source content.
        body,bib=text.rsplit('## ',1)
        refs={int(x) for x in re.findall(r'(?m)^(\d+)\. ',bib)}
        used={int(n) for group in re.findall(r'\[([\d, –-]+)\](?!\()',body) for n in re.findall(r'\d+',group)}
        check('citation numbering '+path,used==refs)
        check('no unfinished support markers '+path,not re.search('CITATION_NEEDED|EVIDENCE_NEEDED|FIGURE_NEEDED|TABLE_NEEDED',text))
    result={'status':'compactEvidenceAndMarkdownChecksPassed','date':'2026-11-01',
            'scope':'Current manuscript tables vs accepted compact evidence and local links; no new raw recount, experiments, or submission-readiness verdict.',
            'checksPassed':len(checks),'localLinksChecked':local_links,'inputsSha256':inputs,
            'checks':checks,'submissionReady':None}
    (HERE/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['status','checksPassed','localLinksChecked']},ensure_ascii=False))


if __name__ == '__main__':
    main()
