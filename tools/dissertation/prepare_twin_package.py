"""Consolidate existing twin evidence; no simulation, SSH or scientific tuning."""
import argparse
import csv
import hashlib
import io
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BROKERS = ("Aedes", "Mosquitto", "ActiveMQ", "EMQX")
CRITERIA = {"rps", "latencyP99Ms", "publishFailureRate", "deliveryLossRate"}
SOURCES = {
    "accuracy": "reports/audits/dissertation/accuracy-analysis-v1/analysis.json",
    "accuracyManifest": "reports/audits/dissertation/accuracy-analysis-v1/manifest.json",
    "sensitivity": "reports/audits/dissertation/sensitivity-final-v1/analysis/analysis.json",
    "history": "reports/audits/dissertation/control-history-v1/evidence.json",
    "method": "docs/dissertation/broker-twin-method-map.md",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def reconcile(analysis):
    """Reconcile criterion-level decisions with run and point-level summaries."""
    decisions = defaultdict(dict)
    for row in analysis["criteria"]:
        key = (row["broker"], row["key"])
        if row["name"] not in CRITERIA or row["name"] in decisions[key]:
            raise ValueError("Unknown or duplicate criterion")
        if row["status"] not in ("passed", "failed", "notApplicable"):
            raise ValueError("Unknown criterion status")
        if row["qos"] == 0 and row["name"] == "latencyP99Ms" and row["status"] != "notApplicable":
            raise ValueError("QoS 0 ACK is not applicable")
        decisions[key][row["name"]] = row
    expected = {(b, f"m{size}.q{qos}.p{pub}.s{seed}") for b, size, qos, pub, seed in
                itertools.product(BROKERS, (16, 256), range(3), (1, 16, 64, 128), (42, 43, 44))}
    if set(decisions) != expected or any(set(rows) != CRITERIA for rows in decisions.values()):
        raise ValueError("Incomplete experimental coverage")
    failures = {(b, key): {name for name, row in rows.items() if row["status"] == "failed"}
                for (b, key), rows in decisions.items()}
    failures = {key: value for key, value in failures.items() if value}
    listed = {}
    for row in analysis["failures"]:
        key = (row["broker"], row["key"])
        if key in listed:
            raise ValueError("Duplicate failed run")
        listed[key] = set(row["failedCriteria"].split(";"))
    # The earlier exporter uses a comma between intersecting criteria.
    listed = {key: {name for item in names for name in item.split(",")} for key, names in listed.items()}
    if failures != listed:
        raise ValueError("Failure list differs from criterion decisions")
    points = {(p["broker"], p["bytes"], p["qos"], p["publishers"]): p for p in analysis["points"]}
    if len(points) != len(analysis["points"]) or len(points) != 96:
        raise ValueError("Missing or duplicate native-context point")
    result = []
    for (broker, key), names in sorted(failures.items()):
        row = next(iter(decisions[(broker, key)].values()))
        point = points[(broker, row["bytes"], row["qos"], row["publishers"])]
        result.append({
            "broker": broker, "key": key, "payloadBytes": row["bytes"], "qos": row["qos"],
            "publishers": row["publishers"], "seed": row["seed"],
            "failedCriteria": ";".join(sorted(names)),
            "nativeLossRepeatRange": point["nativeLossRepeatRange"],
            "nativeLossRangeAbove005": point["nativeLossRepeatRange"] > .05,
            "nativeRpsShiftRelative": point["nativeRpsShiftRelative"],
            "nativeRpsShiftAbove015": point["nativeRpsShiftRelative"] > .15,
            "causeStatus": "notIdentifiedPerRun",
            "evidenceScope": "Criterion mismatch verified; native context is association, not a causal attribution",
        })
    for broker in analysis["brokers"]:
        if sum(r["broker"] == broker["broker"] for r in result) != broker["holdoutFailed"]:
            raise ValueError("Broker summary differs from run decisions")
    return result


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def build(output):
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a new output directory; old evidence is immutable")
    raw = {key: (ROOT / name).read_bytes() for key, name in SOURCES.items()}
    data = json.loads(raw["accuracy"])
    prior = json.loads(raw["accuracyManifest"])
    if digest(raw["accuracy"]) != prior["files"]["analysis.json"]:
        raise ValueError("Accuracy source no longer matches original manifest")
    for name, expected in prior["files"].items():
        source = (ROOT / SOURCES["accuracyManifest"]).parent / name
        if digest(source.read_bytes()) != expected:
            raise ValueError("Accuracy artifact differs from sealed source: " + name)
    rows = reconcile(data)
    sensitivity = json.loads(raw["sensitivity"])
    if sensitivity["verification"]["status"] != "verified":
        raise ValueError("Unverified sensitivity evidence")
    matrix = sensitivity["coverage"]["matrix"]
    table = []
    for broker in BROKERS:
        failed = [r for r in rows if r["broker"] == broker]
        counts = Counter(name for r in failed for name in r["failedCriteria"].split(";"))
        native = next(r for r in data["brokers"] if r["broker"] == broker)
        table.append({"broker": broker, "passed": 72-len(failed), "failed": len(failed),
                      **{name: counts[name] for name in sorted(CRITERIA)},
                      "failedRunsWithHighNativeLossRange": sum(r["nativeLossRangeAbove005"] for r in failed),
                      "nativePointsHighLossRange": native["nativePointsLossRangeAbove005"],
                      "nativePointsRpsShift": native["nativePointsRpsShiftAbove015"]})
    text = """# Проверенный пакет по двойникам четырёх MQTT-брокеров

Пакет объединяет методику, проверку чисел и разбор ошибок. Он предназначен для подготовки статьи автором. Новых измерений и симуляций нет; профили, пороги и старые результаты не менялись. Исторический контроль не объявляется слепой независимой проверкой.

## Результат и покрытие

Натурная основа: 576 выбранных измерений, по 72 калибровочных и 72 контрольных на брокер. Их независимый пересчёт ранее завершён без расхождений; проверены 592 архива, включая дополнительные и отклонённые попытки. Здесь анализируются все 288 исторических контрольных симуляций: четыре брокера × два payload × три QoS × четыре числа publishers × три seed. Это 96 точек и 1152 решения критериев. Калибровочные Fidelity и сетевые опыты в знаменатель 288 не входят.

Критерии: относительная ошибка completed RPS ≤15%, p99 публикационного завершения ≤20%, абсолютная разница failure и loss ≤0,05. Для QoS 0 ACK отсутствует. `notApplicable` сохраняется отдельно. Критерии одного запуска могут пересекаться.

| Брокер | Все критерии пройдены | Не прошли | RPS / 72 | p99 / 48 | failure / 72 | loss / 72 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
"""
    for row in table:
        text += f"| {row['broker']} | {row['passed']}/72 | {row['failed']}/72 | {row['rps']} | {row['latencyP99Ms']} | {row['publishFailureRate']} | {row['deliveryLossRate']} |\n"
    text += f"""
Всего **{288-len(rows)}/288** прошли все применимые критерии, **{len(rows)}/288** не прошли хотя бы один. Это расхождения точности, а не число падений процессов. Доля прошедших сценариев не является вероятностью точности произвольного будущего опыта.

![Карта ошибок всех контрольных точек](../../../accuracy-analysis-v1/holdout-map.svg)

## Как проверены эти числа

1. Проверен SHA исходного analysis.json по его прежнему manifest.
2. Независимо от готовой таблицы восстановлена полная сетка из 288 ключей; для каждого проверены четыре различных критерия и сохранена неприменимость QoS 0.
3. Список провалов заново получен как объединение failed-критериев; он точно совпадает с исходными 85 строками, включая сочетания критериев.
4. Каждый провал сопоставлен со своей натурной точкой, не с соседним числом publishers или payload. Проверены 96 уникальных контекстов и сводки четырёх брокеров.
5. В failures-context.csv сохранены все 85 строк, в broker-summary.csv — пересчитанные итоги. Никакие провалы не исключены по величине ошибки или наличию объяснения.

## Разбор по брокерам
"""
    interpretations = {
        "Aedes": "В натурной контрольной серии не зарегистрированы API-ошибки, но расхождения доставки есть. Значит, отсутствие исключений вызова Publish недостаточно для воспроизведения потерь. Конкретный источник каждого расхождения не установлен.",
        "Mosquitto": "Основные расхождения относятся к loss; p99 проходит все применимые точки. Это не означает, что доставка точна: подтверждение ACK и получение subscriber различаются. Ошибки соединений зарегистрированы, но их причинная связь с каждым провалом не установлена.",
        "ActiveMQ": "Это наиболее слабый двойник по доле прошедших сценариев данной серии. Это не рейтинг качества реального брокера. В журналах зарегистрированы многочисленные попытки API после отключения клиента; offered load по API не равен числу PUBLISH, принятых брокером. Первопричина разрыва соединения и вклад этого эффекта в каждый провал не идентифицированы.",
        "EMQX": "Важная группа расхождений относится к p99; есть и нарушения loss. Низкая доля поздних получений в рассмотренном крайнем случае не доказывает отсутствие иных источников ошибки. Отдельные нестабильные натурные режимы нельзя обобщать на весь брокер.",
    }
    for row in table:
        broker = row["broker"]
        reasons = next(b["errorReasons"] for b in data["brokers"] if b["broker"] == broker)
        text += f"\n### {broker}\n\n{interpretations[broker]}\n\n"
        text += f"- Натурные контрольные точки с разбросом loss >0,05: **{row['nativePointsHighLossRange']}/24**. Со сдвигом среднего RPS между калибровкой и контролем >15%: **{row['nativePointsRpsShift']}/24**.\n"
        text += f"- Среди {row['failed']} модельных провалов **{row['failedRunsWithHighNativeLossRange']}** относятся к точкам с таким разбросом loss. Это сопоставление контекстов, не число доказанно объяснённых провалов.\n"
        if reasons:
            text += "\nЗарегистрированные API-ошибки натурного контроля (не новые ошибки модели):\n\n| Сообщение | События |\n| --- | ---: |\n"
            for reason, count in sorted(reasons.items()):
                text += f"| `{reason}` | {count} |\n"
    text += f"""
## Установленные механизмы и предел причинного объяснения

| Наблюдение | Статус | Что следует | Что не следует |
| --- | --- | --- | --- |
| Счётчики 576 измерений воспроизводятся из сохранённых событий | Проверка внутренней согласованности | Ошибки опубликованной арифметики в проверенном объёме не обнаружены | Сам измеритель не может иметь систематической ошибки |
| Регистрация API-attempt предшествует Publish; после перехваченного исключения цикл продолжается | Механизм подтверждён кодом и ошибками журнала | Attempted RPS может включать вызовы отключённого клиента | Все расхождения ActiveMQ вызваны именно этим механизмом |
| Уникальные получения продолжаются после измерительного окна | Факт отдельных натурных запусков | Сравнивать ACK, получение и временные знаменатели нужно раздельно | Поздняя доставка означает потерю или дефект MQTT |
| Разброс loss повторов >0,05 | Диагностическое описание | Следует показывать повторы и ограничить вывод о среднем | Можно расширить порог проверки модели или объявить статистическую значимость |
| При уменьшении tick отклонения малы в выбранной матрице | Парная проверка чувствительности | Проверенное уменьшение шага не устранило основную проблему loss | Все ошибки модели происходят от tick либо tick вообще ни на что не влияет |
| Потери и очередь, состояние соединений, границы фаз | Причинные гипотезы | Есть конкретные механизмы для будущих изолированных проверок | Причина каждого из 85 провалов установлена |

Для каждого из 85 запусков causeStatus оставлен **notIdentifiedPerRun**. Это осознанная граница результата: ошибка критерия проверена, но причинный диагноз отдельной точки не выдуман. Шаг анализа закрывается технически картой фактов и гипотез, а не обещанием устранить все расхождения.

## Что добавила чувствительность

Завершены {matrix['completed']} основных симуляций: {matrix['completed']-matrix['scientificFailed']} прошли критерии, {matrix['scientificFailed']} не прошли; технических ошибок {matrix['technicalErrors']}. Отдельно 8 pilots и 6 точных повторов. Все шесть повторов совпали в проверенной научной части; два воспроизводят нарушения критериев.

| Изменение | Максимальное изменение RPS | Максимальное изменение p99 |
| --- | ---: | ---: |
"""
    for variant in ("halfTick", "doubleWindow"):
        metrics = sensitivity["pairedSummary"][variant]["metrics"]
        text += f"| {variant} | {100*metrics['completedRps']['maxAbsoluteRelativeDelta']:.3f}% | {100*metrics['latencyP99Ms']['maxAbsoluteRelativeDelta']:.3f}% |\n"
    text += """
Это изменения между парными симуляциями, а не ошибка относительно натурного эталона. Число нарушений loss одинаково для трёх вариантов (12 на вариант); уменьшение tick не является подтверждённым исправлением loss. Изменение p99 до 26,923% при удвоении окна не позволяет считать короткое окно универсально достаточным.

## Что делать с моделью дальше

1. Сохранить действующие профили и пороги; этот пакет описывает область применимости текущей версии, а не её исправление.
2. В отдельном будущем эксперименте различать API-attempt, передачу в транспорт, приём брокером и получение subscriber. Для ActiveMQ фиксировать состояние соединения и момент первого разрыва. Не приписывать старым raw отсутствующие события.
3. Отдельно проверять когорты измерительных публикаций и завершения при drain; не заменять delivered/expected данными receiver иного охвата.
4. Если модель будет изменена по историческому контролю, проверять точность на новом заранее зафиксированном наборе. Старые 203/288 не переименовывать в независимую валидацию новой модели.
5. Начинать исправления с коротких механизмных тестов с известным ответом. Новая многочасовая серия не нужна для подготовки этого пакета.

## Приёмка и работа автора

- Техническая часть шага 10: полная карта ошибок, сверка каждого провала, подтверждённые механизмы и явно обозначенные гипотезы подготовлены.
- Шаг 09: описание метода приложено ниже; ожидается чтение автором. Подготовленный текст не равен принятой рукописи.
- Автору нужно подтвердить определения loss/ACK/drain, условный характер контрольной нагрузки, ограничения независимости и допустимую формулировку научного вклада. Решение не подставляется автоматически.
- Пакет не подтверждает точность реальной Relay/федеративной сети, CPU/RSS двойника, перенос натурных чисел на другую машину или достоверность через EST. EST — условный рейтинг с выбранными весами, не вероятность.

## Приложение: методика и ответственность приложений

"""
    method = raw["method"].decode("utf-8-sig")
    # Preserve content, making source headings subordinate in the consolidated report.
    for line in method.splitlines():
        if line.startswith("#"):
            line = "##" + line
        # Links in original method live relative to docs/dissertation, not this package.
        # Keep the methodological text self-contained; source registry is below.
        if '](' in line:
            import re
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        text += line + "\n"
    # Use a repository-relative link from the package's actual depth.
    text = text.replace("../../../accuracy-analysis-v1/holdout-map.svg", "../accuracy-analysis-v1/holdout-map.svg")
    text += "\n## Источники и повторение\n\n"
    for key, name in SOURCES.items():
        text += f"- {key}: `{name}`, SHA-256 `{digest(raw[key])}`.\n"
    text += "\nПовторение: `python tools/dissertation/prepare_twin_package.py --output <новый-каталог>`. Нужен только Python стандартной поставки и локальный checkout с источниками выше. Запуск не обращается к Linux. Все файлы пакета перечислены в manifest.json; старый каталог не перезаписывается. Исходные raw и архивы остаются вне Git.\n"
    output.mkdir(parents=True)
    (output / "failures-context.csv").write_bytes(csv_bytes(rows))
    (output / "broker-summary.csv").write_bytes(csv_bytes(table))
    (output / "Report.md").write_bytes(text.encode("utf-8"))
    checks = {"coverageRuns": 288, "criterionDecisions": len(data["criteria"]),
              "failedRunsReconciled": len(rows), "nativePoints": len(data["points"]),
              "technicalReviewPassed": True, "scientificAuthorApproval": False,
              "perRunCausesIdentified": 0, "noExperimentsStarted": True}
    (output / "verification.json").write_bytes((json.dumps(checks, indent=2) + "\n").encode())
    manifest = {"schemaVersion": 1, "sources": {SOURCES[k]: digest(v) for k, v in raw.items()},
                "generatorSha256": digest(Path(__file__).read_bytes()),
                "files": {p.name: digest(p.read_bytes()) for p in sorted(output.iterdir())}}
    (output / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(checks))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
