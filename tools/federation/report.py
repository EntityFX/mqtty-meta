"""Publish the verified study with explicit missing latency coverage and interpretation."""
import argparse
import platform
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study


def publish(work):
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["svg.hashsalt"] = "static-federation-interpreted-v1"
    import matplotlib.pyplot as plt

    study.publish(work)
    reports = study.ROOT / "reports"
    data = reports / "data/federation-full-mesh-v1"
    # Opposite full-mesh links cross in the four-site view. Their arrows already
    # identify the origin; omit redundant origin captions without changing paths.
    for path in data.glob("*-relay-logical.svg"):
        tree = ET.parse(path)
        root = tree.getroot()
        original_paths = [dict(e.attrib) for e in root if e.tag.endswith("}path")]
        children = list(root)
        for index, element in enumerate(children):
            if element.tag.endswith("}text") and (element.text or "").startswith("источник "):
                previous = children[index - 1]
                assert previous.tag.endswith("}rect") and previous.get("fill") == "white"
                root.remove(previous)
                root.remove(element)
        assert original_paths == [dict(e.attrib) for e in root if e.tag.endswith("}path")]
        tree.write(path, encoding="utf-8", xml_declaration=True)
    aggregates = study.read(data / "aggregates.json")
    records = study.read(data / "runs.json")
    warnings = []
    for record in records:
        s = record["summary"]
        reasons = []
        if s["deliveryLoss"] is not None and s["deliveryLoss"] > 0:
            reasons.append("incompleteDelivery")
        if s["expectedDeliveries"] > 0 and s["uniqueDeliveries"] == 0:
            reasons.append("noMeasuredDeliveries")
        if s["queueDrops"]:
            reasons.append("queueOverflow")
        if s["hopLimitDrops"]:
            reasons.append("hopLimitReached")
        warnings.append(dict(id=record["id"], reasons=reasons))
    study.write(data / "warnings.json", warnings)
    report_path = reports / "Federation-study.md"
    text = report_path.read_text(encoding="utf-8")
    text = text.replace("Каждый subscriber получает все исходные сообщения.", "Для каждого subscriber ожидаются сообщения всех источников; фактические получения проверяются по ID.")
    for a in aggregates:
        prefix = f"| {a['family']} | {a['mode']} | {a['qos']} | "
        line = next(line for line in text.splitlines() if line.startswith(prefix))
        replacement = line
        for key in ("sourceCompletedRps", "uniqueGoodput", "deliveryLoss", "receiverP99Ms", "forwardAttempts", "suppressedDuplicates"):
            value = a[key]
            if 0 < value["n"] < 3:
                value_text = f"{value['mean']:.4f}"
                replacement = replacement.replace("| " + value_text + " |", f"| {value_text} (n={value['n']}/3; н/п={3-value['n']}) |")
        text = text.replace(line, replacement)
    text = text.replace("## Полученные результаты\n", "## Полученные результаты\n\nЕсли в запуске нет измерительных получений, его p99 неприменим. "
                        "В неполных группах указаны n и число неприменимых результатов; CI95 при n<3 не рассчитывается. "
                        "Доверительные интервалы по трём seeds являются описательными и не учитывают ошибку профиля.\n")
    for family in ("ideal", "mosquitto"):
        for qos in range(3):
            groups = [a for a in aggregates if a["family"] == family and a["qos"] == qos and a["mode"] in ("relay", "federation")]
            if any(a["receiverP99Ms"]["n"] < 3 for a in groups):
                prefix = f"| {family} | {qos} | "
                line = next(line for line in text.splitlines() if line.startswith(prefix))
                fields = line.split(" | ")
                fields[2] = "н/п: неполное покрытие p99"
                text = text.replace(line, " | ".join(fields))
    def get(family, mode, qos):
        return next(a for a in aggregates if (a["family"], a["mode"], a["qos"]) == (family, mode, qos))
    ideal = [r["summary"] for r in records if r["family"] == "ideal"]
    assert len(ideal) == 27 and all(s["apiAttempts"] == 200 and s["uniqueDeliveries"] == 600 for s in ideal)
    latencies = {mode: get("ideal", mode, 2)["receiverP99Ms"]["mean"] for mode in study.MODES}
    forwarding = {mode: get("ideal", mode, 2)["forwardAttempts"]["mean"] for mode in ("relay", "federation")}
    loss = [get("mosquitto", mode, qos)["deliveryLoss"]["mean"] for mode in ("relay", "federation") for qos in (1, 2)]
    interpretation = f"""## Наблюдаемые ограничения реализации

В идеальной модели все три архитектуры доставили все 200 исходных сообщений каждому из трёх subscribers во всех повторах. При QoS 2 Relay выполнил в среднем {forwarding['relay']:.0f} межброкерных пересылок, федерация — {forwarding['federation']:.0f}. В Relay каждый брокер экспортирует свой поток всем остальным, а импорт направляет только локальным subscribers. Повторная рассылка импорта отсутствует; это проверено по источнику, месту отправки и адресату каждой пересылки.

При QoS 2 среднее p99 получения: single — {latencies['single']:.3f} мс, Relay — {latencies['relay']:.3f} мс, федерация — {latencies['federation']:.3f} мс. На каждой площадке работает один MqttRelay, с раздельными MQTT-клиентами для маршрутов разных источников. Федератор использует один выходной MQTT-клиент на направление и ждёт завершения QoS-обмена. Число служебных соединений и способ обслуживания различаются, поэтому сравнение не изолирует стоимость алгоритма. Для этого нужен контролируемый опыт с одинаковым бюджетом соединений и более длинной нагрузкой.

В профилированных распределённых режимах при QoS 1/2 средняя недоставка находится в диапазоне {min(loss)*100:.3f}–{max(loss)*100:.3f}%. Сохранённая условная потеря применяется при доставке служебным клиентам и на следующих этапах; потери накапливаются. ACK исходному publisher не гарантирует доставку конечному subscriber. p99 рассчитан только по дошедшим сообщениям: меньшая задержка при почти полной недоставке не означает лучшего обслуживания потока. Запуски без получений имеют неприменимый p99.

Предупреждения отделены от технических отказов: [причины по каждому запуску](data/federation-full-mesh-v1/warnings.json). Они показывают фактическую недоставку, отсутствие получений и ограничения очередей/переходов.

Это отдельная программа с полносвязным Relay. [Предыдущий отчёт с деревьями](Federation-study-bfs.md) и его данные сохранены; запуски разных программ не объединяются в статистические повторы.

"""
    drain_values = [r["summary"]["uniqueDuringDrain"] / r["summary"]["uniqueDeliveries"] for r in records
                    if (r["family"], r["mode"], r["qos"]) == ("ideal", "federation", 2)]
    interpretation += (f"В идеальной федерации с QoS 2 в среднем {sum(drain_values)/len(drain_values)*100:.1f}% получений произошли в drain. "
                       "Поэтому одинаковый goodput когорты, делённый на время подачи нагрузки, не означает одинаковую устойчивую пропускную способность: "
                       "часть работы завершена после остановки подачи. Для проверки устойчивого режима нужен более длинный опыт с наблюдением роста очередей.\n\n")
    text = text.replace("## Вывод и ограничения\n", interpretation + "## Вывод и ограничения\n")
    text = text.replace("python tools/federation/study.py report --work-dir", "python tools/federation/report.py --work-dir")
    text += "\n[Квитанции внешнего хранения и дополнительные проверки](data/federation-full-mesh-v1/README.md).\n"
    report_path.write_text(text, encoding="utf-8", newline="\n")
    labels = {"sourceCompletedRps": "Завершения исходных / с", "deliveryLoss": "Недоставка, доля", "receiverP99Ms": "p99 получения, мс", "forwardAttempts": "Попытки пересылки"}
    for family in ("ideal", "mosquitto"):
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
        for ax, metric in zip(axes.flat, labels):
            for qos in range(3):
                rows = [get(family, mode, qos)[metric] for mode in study.MODES]
                x = [i for i, row in enumerate(rows) if row["mean"] is not None]
                y = [rows[i]["mean"] for i in x]
                (line,) = ax.plot(x, y, marker="o", label=f"QoS {qos}")
                for i in x:
                    row = rows[i]
                    if row["ci95"] is not None:
                        ax.errorbar([i], [row["mean"]], yerr=[row["ci95"]], color=line.get_color(), capsize=4)
                    if row["n"] < 3:
                        ax.annotate(f"n={row['n']}/3", (i, row["mean"]), xytext=(4, 7), textcoords="offset points", color=line.get_color())
            ax.set_xticks(range(3), ("Один", "Relay", "Федерация"))
            ax.set_title(labels[metric])
            ax.grid(alpha=.2)
            ax.legend()
        fig.suptitle(f"{family}: средние; t-CI95 только для трёх применимых seeds")
        fig.savefig(data / f"{family}-comparison.svg", metadata={"Date": None})
        plt.close(fig)
    study.write(data / "reporting-verification.json", dict(pythonVersion=platform.python_version(), matplotlibVersion=matplotlib.__version__,
        missingLatencyExplicit=True, confidenceIntervalsOnlyForThreeApplicableSeeds=True,
        relaySvgOriginCaptionsOmitted=True, relaySvgPathsPreserved=True,
        sourcesSha256={name: study.sha(Path(__file__).with_name(name)) for name in ("study.py", "report.py", "archive.py")}))
    reseal(data)


def reseal(data):
    reports = study.ROOT / "reports"
    files = [*data.iterdir(), reports / "Federation-study.md"]
    study.write(data / "publication-sha256.json", {p.relative_to(reports).as_posix(): study.sha(p) for p in sorted(files) if p.is_file() and p.name != "publication-sha256.json"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    publish(args.work_dir.resolve())

# Canonical meta-repository documentation layout.
if __name__ == "__main__":
    import sys as _layout_sys
    _layout_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "documentation"))
    from layout import organize_reports as _organize_reports
    _organize_reports(study.ROOT / "reports")
