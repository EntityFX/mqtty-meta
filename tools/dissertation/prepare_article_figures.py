"""Deterministic technical tables and figures from verified, frozen evidence."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
from html import escape
import io
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
BROKERS = ("Aedes", "Mosquitto", "ActiveMQ", "EMQX")
ACCURACY = "reports/audits/dissertation/accuracy-analysis-v1"
SENSITIVITY = "reports/audits/dissertation/sensitivity-final-v1"
T95_DF2 = 4.302652729911275
LIMITS = {"rps": .15, "latencyP99Ms": .20, "publishFailureRate": .05, "deliveryLossRate": .05}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


def save(path, value):
    write(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def checked(path, expected):
    actual = sha(path)
    if actual != expected:
        raise ValueError("Source SHA-256 mismatch: " + str(path))
    return actual


def interval(values):
    """Untruncated Student interval for a mean of exactly three repeats."""
    if len(values) != 3 or any(v is None or not math.isfinite(v) for v in values):
        raise ValueError("Exactly three finite repeats required")
    avg = statistics.mean(values)
    sd = statistics.stdev(values)
    half = T95_DF2 * sd / math.sqrt(3)
    return dict(n=3, mean=avg, sd=sd, min=min(values), max=max(values),
                ci95Low=avg-half, ci95High=avg+half)


def load_inputs(root):
    ap, sp = root / ACCURACY, root / SENSITIVITY
    am, sm = read(ap / "manifest.json"), read(sp / "artifact-sha256.json")
    sources = {}
    for name in ("analysis.json", "criteria.csv", "points.csv"):
        sources[str((Path(ACCURACY) / name).as_posix())] = checked(ap / name, am["files"][name])
    for name in ("analysis/analysis.json", "analysis/criteria.csv", "analysis/point-metrics.csv", "observation.json"):
        sources[str((Path(SENSITIVITY) / name).as_posix())] = checked(sp / name, sm["files"][name])
    np = root / "reports/data/native-evidence.json"
    matches = [value for name, value in am["sourceSha256"].items() if name.replace("\\", "/").endswith("reports/data/native-evidence.json")]
    if len(matches) != 1:
        raise ValueError("Native source identity missing")
    sources["reports/data/native-evidence.json"] = checked(np, matches[0])
    for path in (ap / "manifest.json", sp / "artifact-sha256.json"):
        sources[path.relative_to(root).as_posix()] = sha(path)
    return read(ap / "analysis.json"), read(sp / "analysis/analysis.json"), read(np), sources


def check_criteria(rows):
    for row in rows:
        name, status = row["name"], row["status"]
        if status == "notApplicable":
            if name != "latencyP99Ms" or int(row["qos"]) != 0:
                raise ValueError("Unexpected notApplicable")
            continue
        ref, sim = float(row["reference"]), float(row["simulated"])
        if name in ("rps", "latencyP99Ms"):
            if ref == 0:
                expected = 0.0 if sim == 0 else math.inf
            else:
                expected = abs(sim-ref)/abs(ref)
        else:
            expected = abs(sim-ref)
        if not math.isclose(float(row["error"]), expected, abs_tol=1e-12, rel_tol=1e-10):
            raise ValueError("Criterion error formula differs")
        if float(row["limit"]) != LIMITS[name] or status != ("passed" if expected <= LIMITS[name] else "failed"):
            raise ValueError("Criterion decision differs")


def build(accuracy, sensitivity, native):
    check_criteria(accuracy["criteria"])
    grouped = defaultdict(list)
    for row in native["runs"]:
        k = row["key"]
        grouped[(row["broker"], row["stage"], k["messageBytes"], k["qos"], k["publishers"])].append(row)
    if len(native["runs"]) != 576 or len(grouped) != 192:
        raise ValueError("Native coverage incomplete")
    native_rows = []
    for (broker, stage, size, qos, publishers), runs in sorted(grouped.items()):
        if sorted(r["key"]["repeat"] for r in runs) != [1, 2, 3]:
            raise ValueError("Native repeat identities differ")
        for metric in ("attemptedRps", "completedRps", "publishFailureRate", "deliveryLossRate", "p50Ms", "p95Ms", "p99Ms"):
            vals = [(r["observation"]["measurement"].get("publishLatencyMs") or {}).get(metric)
                    if metric.startswith("p") and metric.endswith("Ms") else r["observation"]["measurement"][metric] for r in runs]
            base = dict(broker=broker, stage=stage, payloadBytes=size, qos=qos, publishers=publishers, metric=metric,
                        population="three_native_repeats", status="notApplicable" if qos == 0 and metric.endswith("Ms") else "observed")
            if base["status"] == "notApplicable":
                base.update(dict(n=0, mean=None, sd=None, min=None, max=None, ci95Low=None, ci95High=None))
            else:
                base.update(interval(vals))
            native_rows.append(base)
    models = defaultdict(list)
    for row in accuracy["criteria"]:
        models[(row["broker"], row["bytes"], row["qos"], row["publishers"], row["name"])].append(row)
    seed_rows = []
    for (broker, size, qos, publishers, metric), rows in sorted(models.items()):
        if sorted(r["seed"] for r in rows) != [42, 43, 44]:
            raise ValueError("Model seed identities differ")
        status = "notApplicable" if all(r["status"] == "notApplicable" for r in rows) else "observed"
        base = dict(broker=broker, payloadBytes=size, qos=qos, publishers=publishers, metric=metric,
                    population="three_seeds_fixed_profile_reference", status=status,
                    failed=sum(r["status"] == "failed" for r in rows),
                    reference=rows[0]["reference"])
        if any(r["reference"] != rows[0]["reference"] for r in rows):
            raise ValueError("Seeds use different references")
        base.update(interval([r["simulated"] for r in rows]) if status == "observed" else
                    dict(n=0, mean=None, sd=None, min=None, max=None, ci95Low=None, ci95High=None))
        seed_rows.append(base)
    broker_rows = []
    for broker in BROKERS:
        criteria = [r for r in accuracy["criteria"] if r["broker"] == broker]
        failures = {r["key"] for r in criteria if r["status"] == "failed"}
        s = sensitivity["matrixDimensions"]["broker"][broker]
        row = dict(broker=broker, holdoutRuns=72, holdoutPassed=72-len(failures), holdoutFailed=len(failures),
                   sensitivityRuns=s["completed"], sensitivityPassed=s["completed"]-s["scientificFailed"],
                   sensitivityFailed=s["scientificFailed"])
        for metric in LIMITS:
            rows = [r for r in criteria if r["name"] == metric]
            row[metric+"Applicable"] = sum(r["status"] != "notApplicable" for r in rows)
            row[metric+"Failed"] = sum(r["status"] == "failed" for r in rows)
        broker_rows.append(row)
    if sum(r["holdoutFailed"] for r in broker_rows) != 85 or sum(r["sensitivityFailed"] for r in broker_rows) != 55:
        raise ValueError("Frozen failure totals differ")
    return dict(brokers=broker_rows, nativeUncertainty=native_rows, conditionalSeedSpread=seed_rows,
                holdoutPoints=accuracy["points"], historicalCriteria=accuracy["criteria"],
                sensitivityCoverage=sensitivity["coverage"])


def csv_file(path, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader(); writer.writerows(rows)
    write(path, stream.getvalue())


def error_map(rows):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1060" height="850" viewBox="0 0 1060 850">',
             '<rect width="1060" height="850" fill="white"/>',
             '<g font-family="sans-serif" font-size="12">',
             '<text x="20" y="23">Число seed, нарушивших критерий, из трёх; серый — ACK QoS 0 неприменим</text>']
    colors = ["#e8f5e9", "#fff59d", "#ffb74d", "#e57373"]
    for bi, broker in enumerate(BROKERS):
        ox, oy = 15+(bi%2)*525, 50+(bi//2)*395
        parts.append(f'<text x="{ox}" y="{oy}">{escape(broker)}</text>')
        for mi, metric in enumerate(LIMITS):
            x0 = ox+100+mi*102
            parts.append(f'<text x="{x0}" y="{oy+20}" font-size="10">{escape(metric)}</text>')
            for pi, publishers in enumerate((1,16,64,128)):
                parts.append(f'<text x="{x0+pi*24+3}" y="{oy+37}" font-size="9">{publishers}</text>')
            for ri, (size,qos) in enumerate((s,q) for s in (16,256) for q in (0,1,2)):
                if mi == 0:
                    parts.append(f'<text x="{ox}" y="{oy+62+ri*45}">{size} Б QoS {qos}</text>')
                for pi, publishers in enumerate((1,16,64,128)):
                    selected = [r for r in rows if r["broker"] == broker and (r["bytes"],r["qos"],r["publishers"],r["name"]) == (size,qos,publishers,metric)]
                    if len(selected) != 3: raise ValueError("Map coverage differs")
                    na = all(r["status"] == "notApplicable" for r in selected)
                    count = sum(r["status"] == "failed" for r in selected)
                    x,y = x0+pi*24, oy+45+ri*45
                    parts.append(f'<rect x="{x}" y="{y}" width="23" height="38" fill="{"#ddd" if na else colors[count]}"/>')
                    parts.append(f'<text x="{x+7}" y="{y+23}">{"—" if na else count}</text>')
    return "\n".join(parts+["</g></svg>\n"])


METHOD = """flowchart TD
  A[Реальный брокер: калибровочные измерения] --> B[Фиксация профиля и SHA-256]
  C[Отдельная натурная серия: три повтора в точке] --> D[Средние, разброс, Student CI95 для среднего]
  B --> E[MqttY: три seed при фиксированном профиле]
  D --> F[Сравнение RPS, ACK p99, failure и loss]
  E --> F
  F --> G[Каждый критерий: passed, failed или notApplicable]
  B --> H[Чувствительность: baseline, halfTick, doubleWindow]
  H --> I[216 запусков и шесть точных повторов]
  G --> J[Таблицы и карты ошибок с provenance]
  I --> J
"""



def uncertainty_svg(rows):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1160" height="920" viewBox="0 0 1160 920">',
             '<rect width="1160" height="920" fill="white"/>',
             '<g font-family="sans-serif" font-size="11">',
             '<text x="20" y="22">Натурный контроль: средний completed RPS и Student CI95 по трём повторам</text>']
    for bi, broker in enumerate(BROKERS):
        selected = sorted([r for r in rows if r["broker"]==broker and r["stage"]=="validation" and r["metric"]=="completedRps"],
                          key=lambda r:(r["payloadBytes"],r["qos"],r["publishers"]))
        ox,oy=20+(bi%2)*575,50+(bi//2)*435
        low=min(0,min(r["ci95Low"] for r in selected))
        high=max(r["ci95High"] for r in selected)*1.05
        def yp(value): return oy+290-(value-low)/(high-low)*255
        parts.append(f'<text x="{ox}" y="{oy}">{broker}; шкала панели от {low:.0f} до {high:.0f} RPS</text>')
        parts.append(f'<path d="M{ox+45} {oy+30} V{oy+290} H{ox+550}" fill="none" stroke="#777"/>')
        for step in range(5):
            value=low+(high-low)*step/4
            y=yp(value)
            parts.append(f'<text x="{ox}" y="{y+3:.3f}" font-size="9">{value:.0f}</text>')
        for i,row in enumerate(selected):
            x=ox+55+i*21
            top,bottom,center=yp(row["ci95High"]),yp(row["ci95Low"]),yp(row["mean"])
            label=f'{row["payloadBytes"]}/q{row["qos"]}/p{row["publishers"]}'
            parts += [f'<path d="M{x} {top:.3f} V{bottom:.3f} M{x-3} {top:.3f} h6 M{x-3} {bottom:.3f} h6" stroke="#1565c0" fill="none"/>',
                      f'<circle cx="{x}" cy="{center:.3f}" r="2.6" fill="#1565c0"/>',
                      f'<text transform="translate({x},{oy+303}) rotate(65)" font-size="9">{label}</text>']
    return "\n".join(parts+["</g></svg>\n"])


def methodology_svg():
    labels = ["Натурная калибровка → фиксированный профиль и SHA-256",
              "Отдельные натурные измерения: три повтора в точке",
              "MqttY: три seed; профиль и контрольный reference фиксированы",
              "RPS / ACK p99 / failure / loss → passed / failed / notApplicable",
              "Чувствительность tick и окна; шесть точных повторов",
              "Таблицы, ошибки, условный разброс; границы выводов"]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="920" height="480" viewBox="0 0 920 480">',
             '<rect width="920" height="480" fill="white"/>']
    for i,label in enumerate(labels):
        y=15+i*77
        parts += [f'<rect x="20" y="{y}" width="880" height="54" rx="6" fill="#e3f2fd" stroke="#456"/>',
                  f'<text x="35" y="{y+32}" font-family="sans-serif" font-size="16">{escape(label)}</text>']
        if i<5: parts.append(f'<path d="M460 {y+54} v18 l-4 -5 m4 5 l4 -5" fill="none" stroke="#456"/>')
    return "\n".join(parts+["</svg>\n"])


def report(data):
    lines = ["# Технический пакет таблиц и рисунков о точности двойников", "",
             "Это подготовленные данные и схемы для автора, а не текст статьи. Новых замеров нет. "
             "Исторический контроль — отдельная серия; полная слепая независимость от доработок модели по истории не доказана.", "",
             "| Брокер | Исторический контроль: прошли / не прошли | Чувствительность: прошли / не прошли |",
             "| --- | ---: | ---: |"]
    for row in data["brokers"]:
        lines.append(f'| {row["broker"]} | {row["holdoutPassed"]} / {row["holdoutFailed"]} из 72 | {row["sensitivityPassed"]} / {row["sensitivityFailed"]} из 54 |')
    lines += ["", "Всего: 203/288 и 161/216 прошли все применимые критерии. Провалы критериев не являются падениями процесса. "
              "Критерии пересекаются, поэтому их провалы нельзя складывать как число запусков.", "",
              "## Правила расчёта", "",
              "Для каждой из 192 натурных точек (два этапа × 96) среднее и SD вычисляются по трём отдельным запускам, "
              "с равным весом запуска. CI95 среднего: mean ± 4,30265273 × SD/√3. Это Student-интервал с двумя степенями свободы, "
              "при допущении независимых повторов и приемлемости нормального приближения; n=3 не позволяет надёжно проверить эти допущения. "
              "Интервал не обрезается до допустимых физических границ. При малом n он может выходить за [0,1]; это ограничение метода. "
              "CI для среднего из трёх p99 — не доверительный интервал индивидуального p99 распределения событий.", "",
              "Для трёх seed отдельно показаны среднее, SD, min/max и условный Student-интервал среднего модели. "
              "Профиль и контрольное среднее неизменны. Этот интервал описывает только вариацию модели по seed и не оценивает "
              "неопределённость натурных замеров, профиля, систематическую ошибку и переносимость на другое оборудование. "
              "Не следует считать его доказательством физической точности. Нулевой SD не означает точную физическую модель.", "",
              "ACK p50/p95/p99 QoS 0 — notApplicable: пустое значение, n=0; нули в средние не добавляются. "
              "Failure = failedPublishes/attemptedPublishes; loss относится к доставке завершённых публикаций по определению исходной программы. "
              "RPS нормирован на длительность измерения, но завершения и доставки при drain могут входить в итоговые счётчики. "
              "Метрики ACK и доставки не взаимозаменяемы. Разные окна исторического контроля и чувствительности не объединяются в одну выборку.", "",
              "Относительные ошибки RPS и p99: |model−reference|/|reference|; абсолютные ошибки failure/loss: |model−reference|. "
              "Пороги сохранены: 0,15 / 0,20 / 0,05 / 0,05. Проверяется каждый seed отдельно, а не только среднее.", "",
              "## Рисунки", "", "![Карта по отдельным критериям](criterion-error-map.svg)", "",
              "![Средние и интервалы натурного RPS](native-rps-ci95.svg)", "", "![Методика](methodology.svg)", "", "```mermaid", METHOD.rstrip(), "```", "",
              "## Файлы и повторение", "",
              "- `broker-summary.csv`: покрытие и пересекающиеся провалы по четырём критериям.",
              "- `native-uncertainty.csv`: средние, SD, диапазон и Student CI95 по трём натурным повторам; два этапа разделены.",
              "- `conditional-seed-spread.csv`: условный разброс исторических симуляций по seed; reference не меняется.",
              "- `holdout-points.csv`: все 96 точек и число проваленных seed.",
              "- `criterion-error-map.svg`: четыре критерия по всем нагрузочным точкам; 0–3 провала seed.",
              "- `native-rps-ci95.svg`: все 96 контрольных точек, три натурных повтора, среднее и CI95; шкалы панелей различаются.",
              "- `figure-inventory.csv`: назначение, знаменатели и источники каждого рисунка.",
              "- `methodology.svg` и `methodology.mmd`: схема получения и проверки результатов; SVG — последовательная краткая схема, Mermaid — зависимости.",
              "- `package.json`: машинные данные; `manifest.json`: SHA-256 источников, генератора и каждого результата.",
              "", "Из корня мета репозитория:", "", "```powershell",
              "python tools/dissertation/prepare_article_figures.py --output reports/audits/dissertation/article-figures-replay",
              "```", "", "Генератор требует новый каталог, проверяет исходные SHA и формулы, не меняет исходники. "
              "Все выходные файлы воспроизводятся побайтно при тех же входах и генераторе; календарные даты и абсолютные пути в результаты не включены.",
              "", "## Границы технической готовности", "",
              "Пакет закрывает подготовку таблиц точности, карт ошибок, оценок разброса и схемы методики. "
              "Он не заменяет научное согласование критериев, независимую внешнюю проверку, новые натурные измерения, "
              "выбор научной новизны и написание статьи автором. Сетевые pilots, MqttRelay и федерация не входят в доказательство "
              "точности одиночного брокера этим пакетом.", ""]
    return "\n".join(lines)


def generate(root, output):
    if output.exists(): raise ValueError("Output already exists")
    accuracy, sensitivity, native, sources = load_inputs(root)
    data = build(accuracy, sensitivity, native)
    # Independently check all sensitivity criterion rows as well.
    with (root / SENSITIVITY / "analysis/criteria.csv").open(encoding="utf-8", newline="") as stream:
        check_criteria(list(csv.DictReader(stream)))
    output.mkdir(parents=True)
    for filename, key in (("broker-summary.csv","brokers"), ("native-uncertainty.csv","nativeUncertainty"),
                          ("conditional-seed-spread.csv","conditionalSeedSpread"), ("holdout-points.csv","holdoutPoints")):
        csv_file(output / filename, data[key])
    write(output / "criterion-error-map.svg", error_map(data["historicalCriteria"]))
    write(output / "native-rps-ci95.svg", uncertainty_svg(data["nativeUncertainty"]))
    csv_file(output / "figure-inventory.csv", [
        dict(file="criterion-error-map.svg", meaning="failed seeds per criterion", denominator="3 seeds per point; QoS0 ACK excluded", source="accuracy-analysis-v1/analysis.json"),
        dict(file="native-rps-ci95.svg", meaning="native repeat mean with Student CI95", denominator="3 native validation runs per point", source="reports/data/native-evidence.json"),
        dict(file="methodology.svg", meaning="concise sequential method", denominator="not a metric", source="generator SHA in manifest"),
        dict(file="methodology.mmd", meaning="method dependencies", denominator="not a metric", source="generator SHA in manifest")])
    write(output / "methodology.mmd", METHOD)
    write(output / "methodology.svg", methodology_svg())
    write(output / "Article-figure-package.md", report(data))
    save(output / "package.json", data)
    save(output / "manifest.json", dict(generatorSha256=sha(Path(__file__)), sources=sources,
         files={p.name:sha(p) for p in sorted(output.iterdir()) if p.is_file()},
         thresholdsUnchanged=LIMITS, humanScientificApproval="notProvidedByThisPackage"))
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args=parser.parse_args()
    data=generate(ROOT,args.output)
    print(json.dumps(data["brokers"],ensure_ascii=False))


if __name__ == "__main__": main()
