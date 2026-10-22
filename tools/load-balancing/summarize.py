"""Add a concise interpretation and independently audit the saved study and six replays."""
import argparse
import platform
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study
import topologies


def scaling(records, family, qos, reference):
    ratios = []
    for seed in study.SEEDS:
        pair = {r["variant"]: r["summary"] for r in records if
                (r["family"], r["load"], r["qos"], r["seed"]) == (family, 60000, qos, seed) and r["summary"]["status"] == "completed"}
        if "roundRobin" in pair and reference in pair:
            ratios.append(study.safe_ratio(pair["roundRobin"]["uniqueGoodput"], pair[reference]["uniqueGoodput"]))
    return study.estimate(ratios)


def audit(work):
    plan, state, records, equality = study.collect(work)
    if len(records) != len(plan["cases"]):
        raise ValueError("Study coverage is incomplete; do not issue a final assessment.")
    replay_results = []
    for case_id in plan["replayCaseIds"]:
        original = work / "runs" / case_id
        repeated = work / "replays" / case_id
        study.verify_seal(repeated)
        summary = study.read(repeated / "summary.json")
        study.validate_summary(summary)
        if summary["status"] != "completed" or state["replays"][case_id]["exitCode"] != 0:
            raise ValueError("Replay did not complete: " + case_id)
        original_hash = study.digest(original / "summary.json")
        repeated_hash = study.digest(repeated / "summary.json")
        if original_hash != repeated_hash:
            raise ValueError("Nonidentical replay: " + case_id)
        replay_results.append({"caseId": case_id, "originalSummarySha256": original_hash,
                               "replaySummarySha256": repeated_hash, "identical": True})
    for record in records:
        manifest = study.read(work / "runs" / record["id"] / "manifest.json")
        options = manifest["resolvedOptions"]
        for key, expected in {"measurementSeconds": .2, "warmupSeconds": .01, "drainTimeoutSeconds": 1,
                              "backendCount": record["brokers"], "totalPublishers": record["publishers"],
                              "payloadBytes": 16, "publishQos": record["qos"], "subscribeQos": record["qos"],
                              "offeredRps": record["load"], "seed": record["seed"], "mode": record["mode"],
                              "networkLength": 2, "ideal": record["family"] == "demo"}.items():
            if options[key] != expected:
                raise ValueError(f"Resolved protocol mismatch: {record['id']} / {key}")
        if options["network"] != plan["network"] or options["tickPeriod"] != "00:00:00.0001000":
            raise ValueError("Transport mismatch")
        counts = [p["publishers"] for p in manifest["backendProfileLookup"]]
        if counts != ([16, 16, 16] if record["brokers"] == 3 else [record["publishers"]]):
            raise ValueError("Publisher/profile lookup mismatch")
        if (manifest["profile"] is None) != (record["family"] == "demo"):
            raise ValueError("Demo/profile mode mismatch")
    if not all(group["threeSummariesIdentical"] for group in equality):
        raise ValueError("Equal-assignment strategy control failed; investigate before drawing conclusions.")
    return plan, state, records, equality, replay_results


def publish(work, reports):
    import matplotlib
    plan, state, records, equality, replays = audit(work)
    data = reports / "data/load-balancing-study-v1"
    aggregates = study.read(data / "aggregates.json")
    observations = study.read(work / "inputs/observations.json")
    success = sum(r["summary"]["status"] == "completed" for r in records)
    study.write(data / "independent-verification.json", {
        "successful": success, "planned": len(plan["cases"]), "formulaCheckedRuns": len(records),
        "protocolAndLookupCheckedRuns": len(records), "replays": replays,
        "equalStrategyGroups": sum(g["threeSummariesIdentical"] for g in equality), "strategyGroups": len(equality),
        "reportingScriptSha256": study.digest(__file__), "pythonVersion": platform.python_version(), "matplotlibVersion": matplotlib.__version__,
        "reportingSourcesSha256": {name: study.digest(Path(__file__).with_name(name)) for name in ("study.py", "summarize.py", "topologies.py")},
        "sourceProfileUnchanged": study.digest(work / "inputs/profile.json") == plan["sources"]["profileSha256"]})
    lines = ["# Один и три брокера в MqttY: эффективность и границы доказательств", "",
             f"Выполнены **{success}/{len(plan['cases'])}** основных симуляций и **{len(replays)}/{len(plan['replayCaseIds'])}** повторов. "
             f"Технических отказов: **{len(records)-success}**. Это модельные результаты; новых натурных измерений нет.", "",
             "**В этой программе победителя среди directBalanced, Round Robin и Least Connections по результатам симуляции нет.** "
             f"Все три режима дали побайтно одинаковые scientific summaries в {sum(g['threeSummariesIdentical'] for g in equality)}/{len(equality)} сочетаниях нагрузки, QoS и seed. "
             "Причина — одинаковые постоянные сессии и одинаковая карта назначений. Стоимость самого алгоритма назначения не моделируется.", "",
             "**Технически завершённый запуск не означает полную доставку.** В исходном профиле Mosquitto N=16 задана "
             "условная недоставка около 91–92% для QoS 1/2. Поэтому ниже главным показателем является unique goodput, "
             "а не только число завершённых публикаций. Этот коэффициент перенесён из конкретных калибровочных условий и применяется моделью также к новым нагрузкам.", "",
             "## Результаты высокой нагрузки", "",
             "Общая заданная нагрузка — 60 000 сообщений/с. Числа — среднее по seeds 42, 43, 44; полный разброс и CI95 приведены в отдельных отчётах. "
             "RR3 представляет все три равных варианта пула. Полезная скорость учитывает только первые корректные получения измерительных ID.", "",
             "| Модель | QoS | Один, 16 publishers: goodput/с | Один, 48 publishers: goodput/с | Один, 64 publishers: goodput/с | Три × 16: goodput/с |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    def mean(family, qos, variant, key):
        return next((r[key]["mean"] for r in aggregates if (r["family"], r["load"], r["qos"], r["variant"]) == (family, 60000, qos, variant)), None)
    for family in ("demo", "mosquitto"):
        for qos in range(3):
            values = [study.number(mean(family, qos, v, "uniqueGoodput")) for v in ("single16", "single48", "single64", "roundRobin")]
            lines.append(f"| {'Идеальная' if family == 'demo' else 'Профиль Mosquitto'} | {qos} | " + " | ".join(values) + " |")
    lines += ["", "## Коэффициенты масштабирования", "",
              "S = goodput пула / goodput одиночного варианта; E=S/3. Показаны среднее парных отношений при одном seed и t-CI95 по трём seeds. "
              "E — условная производительность на backend относительно baseline. Это не оценка использования CPU/энергии и не интегральный балл EST. "
              "В сравнении разных профильных режимов E может превысить 1; его нельзя читать как балл от 0 до 1. "
              "S около 1 при низкой нагрузке означает отсутствие необходимости в дополнительной пропускной способности в этом опыте.", "",
              "| Модель | QoS | Одиночный baseline | S, среднее ± CI95 | E=S/3 |",
              "| --- | ---: | --- | ---: | ---: |"]
    for family in ("demo", "mosquitto"):
        references = ("single48",) if family == "demo" else ("single16", "single64")
        for qos in range(3):
            for reference in references:
                value = scaling(records, family, qos, reference)
                lines.append(f"| {'Идеальная' if family == 'demo' else 'Профиль Mosquitto'} | {qos} | {reference} | {study.interval(value, 4)} | {study.number(None if value['mean'] is None else value['mean']/3, 4)} |")
    lines += ["", "**Идеальная часть:** сравнение single48 с 3×16 сохраняет число publishers, их нагрузку и длину пути. "
              "Различие здесь относится к модельной топологии и её ресурсам, а не к точности настоящего брокера.", "",
              "**Часть Mosquitto:** single48 не моделировался, поскольку точка N=48 отсутствует в исходном профиле v4. "
              "Single16 → 3×16 одновременно увеличивает число publishers и брокеров втрое; single64 → 3×16 меняет число publishers и выбранную точку профиля. "
              "Поэтому эти отношения нельзя использовать как чистую причинную оценку ускорения от трёх реальных брокеров. "
              "Заменять отсутствующий N соседней точкой или интерполяцией не пришлось.", "",
              "## Топология брокеров", "",
              "Одиночный профилированный вариант и пул показаны ниже. Состав узлов и физические связи проверены по экспортированному GraphML; "
              "назначения publishers взяты из manifest. Receivers находятся в общем клиентском сегменте, не за backend и не на отдельной машине. "
              "Пунктир обозначает выбор backend перед CONNECT; отдельного TCP proxy в модели нет.", "",
              "![Один Mosquitto, 16 publishers](data/load-balancing-study-v1/topology-mosquitto-single16.svg)", "",
              "![Три Mosquitto, по 16 publishers](data/load-balancing-study-v1/topology-mosquitto-pool3.svg)", "",
              "Для идеального сравнения single48 и для одиночного контроля Mosquitto N=64 отдельные диаграммы включены в подробные отчёты. "
              "[Проверка топологий](data/load-balancing-study-v1/topology-verification.json). Редактируемые исходники Mermaid лежат рядом с SVG.", "",
              "## Что было измерено при построении профиля Mosquitto", "",
              "Это прежние калибровочные измерения с payload 16 байт; приведены средние по трём повторам. Они подтверждают происхождение профиля, "
              "но не являются натурным эталоном нового опыта с балансировщиком.", "",
              "| Publishers | QoS | Натурные attempted RPS | Натурные completed RPS | Среднее натурное p99, мс | Недоставка среди завершённых, % |",
              "| ---: | ---: | ---: | ---: | ---: | ---: |"]
    for point in observations["points"]:
        if point["messageBytes"] == 16 and point["publishers"] in (16, 64):
            latency = (point.get("observedLatencyQuantiles") or {}).get("p99Ms")
            lines.append(f"| {point['publishers']} | {point['qos']} | {study.number(point['attemptedRps']['mean'])} | {study.number(point['completedRps']['mean'])} | {study.number(latency, 4)} | {study.number(100*point['deliveryLossRate']['mean'], 4)} |")
    lines += ["", "Новые offeredRps 1000/60 000 отличаются от интенсивностей исходной калибровки. "
              "Модель выбирает профиль по payload/QoS/N, а не по предлагаемой нагрузке. Результат исследовательский: "
              "натурная точность при этих новых интенсивностях не подтверждена. "
              "Также здесь используется фиксированный транспорт, без специального EmpiricalServiceBudget одиночного BrokerFidelity; "
              "сетевая протокольная задержка может стать главным ограничением RPS и p99.", "",
              "Высокая недоставка в исходных точках — эмпирический результат конкретного измерительного тракта и нагрузки, "
              "а не универсальная характеристика Mosquitto. По одним агрегатам нельзя отделить поведение брокера от очередей, "
              "ограничений receivers и других условий стенда. Профиль применяет сохранённую вероятность потери независимо от нового offeredRps; "
              "поэтому большая потеря в модели при 1000 сообщений/с требует отдельной проверки настоящего стенда и нагрузки, "
              "а не вывода о том, что настоящий Mosquitto обязательно теряет столько же при малой интенсивности.", "",
              "## Проверяемость и воспроизводимость", "",
              f"- MqttY: `{plan['sources']['mqttYCommit']}`; runtime зафиксирован до первого запуска.",
              f"- Профиль Mosquitto SHA-256: `{plan['sources']['profileSha256']}`; не изменялся.",
              f"- Независимо пересчитаны скорости, failure/loss, уникальные доставки, drain и квантили {len(records)} scientific summaries.",
              f"- Проверены фактические настройки и профильные ключи всех {len(records)} запусков; все используют точные наблюдавшиеся N.",
              f"- Шесть повторных запусков из сохранённых конфигов дали идентичные SHA-256 scientific summary; seals повторов проверены заново.",
              "- Таблицы и SVG построены из сохранённых JSON/CSV. Хэши подтверждают целостность; пересчёт формул и повторные запуски обеспечивают проверяемость.", "",
              "Короткое измерительное окно — 0.2 модельной секунды; прогрев 0.01 с, drain до 1 с, tick 0.1 мс. "
              "Результаты включают drain измерительной когорты и делятся на длительность нагрузки. "
              "t-CI95 по трём seeds отражает только условный разброс модели; это не доверительный интервал её ошибки относительно настоящего брокера.", "",
              "## Что можно утверждать в диссертации", "",
              "1. Реализовано и проверено назначение постоянных MQTT-соединений одному из трёх независимых backend. "
              "Учёт подтверждает доставку своим receivers, а не объединение подписок между брокерами.",
              "2. При одинаковых назначениях directBalanced, RR и LC воспроизводят одинаковые модельные результаты. "
              "Это результат конкретной программы с постоянными сессиями, не доказательство равенства стратегий при неоднородной нагрузке.",
              "3. Получены модельные коэффициенты масштабирования. Для Mosquitto они условны из-за смены числа publishers, "
              "точки профиля и непроверенной новой нагрузки; переносить их на настоящий пул как доказанную эффективность нельзя.",
              "4. Подтверждена повторяемость выбранных модельных расчётов, но не натурная точность TCP-балансировщика, "
              "не стационарный p99 и не моделирование CPU/RSS.", "",
              "Для следующего этапа нужны отдельные натурные точки N=48 и 3×16 при одинаковой общей нагрузке, "
              "новая независимая контрольная серия, более длинное окно, а затем неодинаковая длительность/интенсивность сессий для различения стратегий. "
              "Профиль и пороги должны фиксироваться до контроля.", "",
              "## Отчёты и исходные числа", "",
              "- [Идеальные брокеры: полный отчёт](Load-balancing-demo.md).",
              "- [Mosquitto: полный отчёт](Load-balancing-Mosquitto.md).",
              "- [CSV](data/load-balancing-study-v1/runs.csv), [scientific summaries](data/load-balancing-study-v1/runs.json), "
              "[агрегаты](data/load-balancing-study-v1/aggregates.json), [независимая проверка](data/load-balancing-study-v1/independent-verification.json).",
              "- [Расположение и SHA-256 полного архива на диске доказательств](data/load-balancing-study-v1/archive-receipt.json).",
              "- [SHA-256 опубликованных материалов](data/load-balancing-study-v1/publication-sha256.json).", ""]
    (reports / "Load-balancing-study.md").write_text("\n".join(lines), encoding="utf-8")
    for family, filename in (("demo", "Load-balancing-demo.md"), ("mosquitto", "Load-balancing-Mosquitto.md")):
        file = reports / filename
        text = file.read_text(encoding="utf-8").split("\n<!-- study-conclusion -->")[0].replace("\n\nУспешно **", "\n\nТехнически завершены **")
        conclusion = ["", "<!-- study-conclusion -->", "", "## Вывод", ""]
        for qos in range(3):
            reference = "single48" if family == "demo" else "single16"
            value = scaling(records, family, qos, reference)
            conclusion.append(f"- При QoS {qos} и 60 000 сообщений/с RR3/{reference}: S={study.interval(value, 4)}. Результаты D3 и LC3 совпадают с RR3.")
        conclusion += ["", "Отдельного победителя среди стратегий в программе с одинаковыми постоянными сессиями нет. "
                       + ("В основной идеальной паре сохраняются 48 publishers." if family == "demo" else
                          "В профилированной паре одновременно меняются publishers и выбранная точка профиля; чистый выигрыш трёх настоящих брокеров не выделен."),
                       "", "Техническое завершение не гарантирует полной доставки. "
                       + ("При QoS 1/2 число незавершённых слотов определяется также ожиданием ACK отдельными publishers." if family == "demo" else
                          "В профиль N=16 перенесена исходная недоставка около 91–92% для QoS 1/2. Её применение к новой нагрузке — допущение модели, которое требует натурной проверки. Смотрите именно goodput и loss, а не только RPS завершений."),
                       "", "[Общий разбор и границы доказательств](Load-balancing-study.md). "
                       "[Квитанция полного архива](data/load-balancing-study-v1/archive-receipt.json).", ""]
        file.write_text(text + "\n".join(conclusion), encoding="utf-8")
    seal_publication(reports)


def seal_publication(reports):
    data = reports / "data/load-balancing-study-v1"
    files = [*data.glob("*"), *reports.glob("Load-balancing-*.md")]
    study.write(data / "publication-sha256.json", {p.relative_to(reports).as_posix(): study.digest(p) for p in sorted(files)
                                                 if p.is_file() and p.name != "publication-sha256.json"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--reports", type=Path, default=study.ROOT / "reports")
    args = parser.parse_args()
    import matplotlib
    matplotlib.rcParams["svg.hashsalt"] = "mqtt-load-balancing-study-v1"
    work = args.work_dir.resolve()
    reports = args.reports.resolve()
    study.report(work, reports)
    for svg in (reports / "data/load-balancing-study-v1").glob("*.svg"):
        svg.write_text(re.sub(r"\s*<dc:date>[^<]*</dc:date>", "", svg.read_text(encoding="utf-8")), encoding="utf-8")
    topologies.publish(work, reports)
    publish(work, reports)
