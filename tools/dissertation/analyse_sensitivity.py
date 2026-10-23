"""Recount a saved sensitivity snapshot without SSH or changing experiment inputs."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import mean, median


IDENTITY = re.compile(r"(?:(pilot|replay)\.)?(Aedes|Mosquitto|ActiveMQ|EMQX)\.m(\d+)\.q([012])\.p(\d+)\.s(\d+)\.(baseline|halfTick|doubleWindow)")
METRICS = ("completedRps", "latencyP99Ms", "publishFailureRate", "deliveryLossRate")


def identity(value):
    match = IDENTITY.fullmatch(value)
    if not match:
        raise ValueError("Unknown sensitivity identity: " + value)
    kind, broker, payload, qos, publishers, seed, variant = match.groups()
    return {"kind": kind or "matrix", "broker": broker, "payload": int(payload),
            "qos": int(qos), "publishers": int(publishers), "seed": int(seed), "variant": variant}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def analyse(observation):
    """Observed results only; missing or technically failed rows cannot form scientific pairs."""
    jobs = observation["plan"]["jobs"]
    planned = {j["id"]: identity(j["id"]) for j in jobs}
    if len(planned) != len(jobs):
        raise ValueError("Duplicate planned job")
    rows = {}
    for item in observation["rows"]:
        row = item.get("data", item)
        key = row["id"]
        if key not in planned or key in rows:
            raise ValueError("Unknown or duplicate result: " + key)
        dims = planned[key]
        if row.get("kind") != dims["kind"] or row.get("variant") != dims["variant"]:
            raise ValueError("Result identity differs from metadata: " + key)
        if not row.get("technicalError"):
            if row.get("returnCode") not in (0, 3) or row.get("summary", {}).get("status") != "completed":
                raise ValueError("Successful row lacks completed scientific output: " + key)
            failed = any(c["status"] == "failed" for c in row["summary"]["acceptanceCriteria"])
            if row.get("fidelityFailed") is not failed:
                raise ValueError("Failure flag differs from criteria: " + key)
        rows[key] = row

    def stats(keys):
        selected = [rows[k] for k in keys if k in rows]
        usable = [r for r in selected if not r.get("technicalError")]
        timings = [r["wallSeconds"] for r in usable if finite(r.get("wallSeconds"))]
        rss = [r["peakRssBytes"] for r in usable if finite(r.get("peakRssBytes"))]
        return {"planned": len(keys), "recorded": len(selected), "completed": len(usable),
                "scientificFailed": sum(r["fidelityFailed"] for r in usable),
                "technicalErrors": sum(bool(r.get("technicalError")) for r in selected),
                "failedCriteria": dict(sorted(Counter(c["name"] for r in usable
                    for c in r["summary"]["acceptanceCriteria"] if c["status"] == "failed").items())),
                "medianWallSeconds": median(timings) if timings else None,
                "totalWorkerWallSeconds": sum(timings), "peakRssBytes": max(rss, default=None)}

    by_phase = {phase: stats([k for k, d in planned.items() if d["kind"] == phase])
                for phase in ("pilot", "matrix", "replay")}
    dimensions = {}
    for field in ("broker", "qos", "publishers", "variant"):
        dimensions[field] = {str(value): stats([k for k, d in planned.items()
                           if d["kind"] == "matrix" and d[field] == value])
                             for value in sorted({d[field] for d in planned.values()})}

    pairs = []
    for key, dims in planned.items():
        if dims["kind"] != "matrix" or dims["variant"] == "baseline" or key not in rows:
            continue
        base_id = key.rsplit(".", 1)[0] + ".baseline"
        if base_id not in rows:
            continue
        base, variant = rows[base_id], rows[key]
        if base.get("technicalError") or variant.get("technicalError"):
            continue
        comparison = {}
        for metric in METRICS:
            a = base["summary"]["simulated"].get(metric)
            b = variant["summary"]["simulated"].get(metric)
            comparable = finite(a) and finite(b)
            comparison[metric] = {"baseline": a, "variant": b,
                "absoluteDelta": b - a if comparable else None,
                "relativeDelta": (b - a) / abs(a) if comparable and a != 0 else None,
                "status": "compared" if comparable else "notApplicable"}
        pairs.append({"baselineId": base_id, "variantId": key, **dims,
                      "metrics": comparison, "wallTimeRatio": variant["wallSeconds"] / base["wallSeconds"]
                        if finite(base.get("wallSeconds")) and base["wallSeconds"] > 0 else None,
                      "baselineFailedCriteria": [c["name"] for c in base["summary"]["acceptanceCriteria"] if c["status"] == "failed"],
                      "variantFailedCriteria": [c["name"] for c in variant["summary"]["acceptanceCriteria"] if c["status"] == "failed"]})
    paired_summary = {}
    for variant in ("halfTick", "doubleWindow"):
        selected = [p for p in pairs if p["variant"] == variant]
        paired_summary[variant] = {"pairs": len(selected), "metrics": {}}
        for metric in METRICS:
            deltas = [p["metrics"][metric]["relativeDelta"] for p in selected
                      if p["metrics"][metric]["relativeDelta"] is not None]
            absolute = [p["metrics"][metric]["absoluteDelta"] for p in selected
                        if p["metrics"][metric]["absoluteDelta"] is not None]
            paired_summary[variant]["metrics"][metric] = {
                "relativeDeltaPairs": len(deltas),
                "meanAbsoluteRelativeDelta": mean(abs(v) for v in deltas) if deltas else None,
                "maxAbsoluteRelativeDelta": max((abs(v) for v in deltas), default=None),
                "absoluteDeltaPairs": len(absolute),
                "maxAbsoluteDelta": max((abs(v) for v in absolute), default=None)}
    scientific_failures = [{"id": k, **planned[k], "criteria": [c for c in r["summary"]["acceptanceCriteria"]
                           if c["status"] == "failed"]} for k, r in sorted(rows.items())
                           if not r.get("technicalError") and r.get("fidelityFailed")]
    return {"observedAtUtc": observation.get("observedAtUtc"), "planSha256": observation.get("progress", {}).get("planSha256"),
            "coverage": by_phase, "matrixDimensions": dimensions,
            "pairedSummary": paired_summary, "matchedPairs": sorted(pairs, key=lambda p: p["variantId"]),
            "scientificFailures": scientific_failures,
            "technicalFailures": [{"id": k, "reason": r["technicalError"]} for k, r in sorted(rows.items()) if r.get("technicalError")],
            "limitations": ["Only saved completed results; no new server observation or archive verification",
                "Scientific criterion failures do not establish broker or simulator defect causes",
                "Variant comparisons use identical broker, payload, QoS, publishers and seed, matrix phase only",
                "Relative deltas with zero baseline and QoS0 ACK latency are not applicable",
                "Wall times describe this execution; ratios alone do not prove hardware-independent acceleration",
                "Worker wall-time sum is not elapsed campaign time; partial, ordered coverage cannot represent all brokers"]}


def render(result):
    lines = ["# Разбор сохранённых результатов проверки чувствительности", "",
             "Проверка чувствительности отвечает, меняются ли результаты при уменьшении шага модельного времени и увеличении окна нагрузки. Это разбор имеющихся результатов, а не новая серия и не текст статьи.", "",
             f"Источник: `{result.get('observationFile', 'snapshot')}`. SHA-256: `{result.get('observationSha256', 'not recorded')}`.", "",
             "Срез наблюдения: `" + str(result["observedAtUtc"]) + "`. Последующие результаты сюда автоматически не включаются.", "",
             "## Фактическое покрытие", "", "| Этап | Завершено / план | Не прошли критерии | Технические ошибки |",
             "| --- | ---: | ---: | ---: |"]
    for kind, stats in result["coverage"].items():
        lines.append(f"| {kind} | {stats['completed']}/{stats['planned']} | {stats['scientificFailed']} | {stats['technicalErrors']} |")
    lines += ["", "Столбец критериев показывает расхождение модели с натурным наблюдением. Код завершения 3 в этой программе означает такой результат; он не означает падение процесса. Технические ошибки считаются отдельно по сохранённым причинам.", "",
              "## Основная матрица по брокерам", "", "| Брокер | Завершено / план | Не прошли критерии | Какие критерии | Медиана времени, с |", "| --- | ---: | ---: | --- | ---: |"]
    for name, stats in result["matrixDimensions"]["broker"].items():
        criteria = ", ".join(f"{k}: {v}" for k, v in stats["failedCriteria"].items()) or "—"
        timing = f"{stats['medianWallSeconds']:.1f}" if stats["medianWallSeconds"] is not None else "—"
        lines.append(f"| {name} | {stats['completed']}/{stats['planned']} | {stats['scientificFailed']} | {criteria} | {timing} |")
    lines += ["", "Медиана для смешанных уровней нагрузки может скрывать дорогие точки. Поэтому ниже нагрузка показана отдельно. Эти числа относятся только к завершённой части основной матрицы.", "",
              "| Разрез | Значение | Завершено / план | Не прошли критерии | Медиана времени, с |", "| --- | --- | ---: | ---: | ---: |"]
    for dimension in ("qos", "publishers", "variant"):
        for name, stats in result["matrixDimensions"][dimension].items():
            timing = f"{stats['medianWallSeconds']:.1f}" if stats["medianWallSeconds"] is not None else "—"
            lines.append(f"| {dimension} | {name} | {stats['completed']}/{stats['planned']} | {stats['scientificFailed']} | {timing} |")
    lines += ["", "## Зафиксированные расхождения", "", "| Точка | Критерий | Натурное значение | Модель | Ошибка | Допуск |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for failure in result["scientificFailures"]:
        for c in failure["criteria"]:
            values = [f"{c.get(field):.6g}" if finite(c.get(field)) else "—" for field in ("reference", "simulated", "error", "limit")]
            lines.append(f"| `{failure['id']}` | {c['name']} | " + " | ".join(values) + " |")
    if not result["scientificFailures"]:
        lines.append("| Нет в сохранённой части | — | — | — | — | — |")
    lines += ["", "RPS и p99 проверяются по относительной ошибке; failure/loss — по абсолютной разнице долей. Уменьшение числа таких расхождений в одном варианте само по себе не объясняет причину и не разрешает выбрать параметры по контрольным данным."]
    lines += ["", "## Парные сравнения с исходным вариантом", "", "Сравниваются только одинаковые брокер, payload, QoS, число publishers и seed в основной матрице. Пилоты в пары не входят. Увеличение окна меняет число публикаций; сравниваются скорости и доли, а не абсолютные счётчики.", "",
              "| Вариант | Пар всего | Метрика | Пар с ненулевой базой | Среднее абсолютное относительное изменение | Максимум |", "| --- | ---: | --- | ---: | ---: | ---: |"]
    for name, data in result["pairedSummary"].items():
        for metric, values in data["metrics"].items():
            avg = f"{values['meanAbsoluteRelativeDelta'] * 100:.3f}%" if values["meanAbsoluteRelativeDelta"] is not None else "неприменимо"
            maximum = f"{values['maxAbsoluteRelativeDelta'] * 100:.3f}%" if values["maxAbsoluteRelativeDelta"] is not None else "неприменимо"
            lines.append(f"| {name} | {data['pairs']} | {metric} | {values['relativeDeltaPairs']} | {avg} | {maximum} |")
    lines += ["", "Для нулевой исходной доли ошибка относительного изменения не определена. Абсолютная разница сохранена для каждой пары в JSON. QoS 0 не имеет задержки публикационного ACK. Эти сравнения не заменяют независимую натурную проверку и не меняют исходные пороги.", "",
              "## Что делать дальше", "", "1. Довести запланированное покрытие и повторы до конца; не перезапускать завершённые точки только из-за непройденных критериев.",
              "2. При технической ошибке сохранить попытку, проверить причину и повторить только затронутую точку по отдельному решению.",
              "3. После полного покрытия проверить влияние шага и окна попарно для каждого QoS и числа publishers. До этого не переносить выводы Aedes на остальные брокеры.",
              "4. Ускорять управляющий планировщик и устранять простои между независимыми процессами; runtime, профили, численное расписание и пороги внутри этой серии сохранять.",
              "5. Проверку сети и подготовку таблиц выполнять независимо; конкурентные процессы ограничивать общей памятью и свободным диском.", "",
              "## Границы выводов", "", "Из среза нельзя установить причину расхождений, доказать точность всех брокеров или подтвердить натурную точность MqttRelay. Хэши файла позволяют повторить этот разбор, но сами по себе не подтверждают корректность исходных измерений.", "",
              "Рабочее время процесса не равно суммарному времени кампании. Это описательный анализ частичного, упорядоченного покрытия; доверительные интервалы для всей серии здесь не оцениваются.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New result directory; never overwrite")
    args = parser.parse_args()
    raw = args.observation.read_bytes()
    result = analyse(json.loads(raw.decode("utf-8-sig")))
    result.update(observationFile=args.observation.as_posix(), observationSha256=hashlib.sha256(raw).hexdigest(),
                  analyserSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (args.output / "Sensitivity-review.md").write_text(render(result), encoding="utf-8")
    print(json.dumps({"coverage": result["coverage"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
