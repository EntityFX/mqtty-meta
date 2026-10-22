"""Run and verify a separate ten-broker Relay study through an empty transit network."""
import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study

PUBLICATION = "federation-transit-10-v1"


def verify_network(directory):
    directory = Path(directory)
    summary = study.verify_run(directory)
    options = study.read(directory / "run-config.json")
    assert options["topology"] == "star" and options["mode"] == "relay"
    assert (options["brokers"], options["publishers"]) == (10, 160)
    evidence = study.read(directory / "network-evidence.json")
    assert evidence["networkCount"] == 11 and evidence["brokerCount"] == 10
    assert evidence["name"] == "transit" and not evidence["hasAttachedEndpoints"]
    routes = evidence["availablePaths"]
    assert {(r["source"], r["target"]) for r in routes} == {(a, b) for a in range(10) for b in range(10) if a != b}
    assert len(routes) == 90
    for route in routes:
        assert route["networks"] == [f"site{route['source']}", "transit", f"site{route['target']}"]
    before, after = evidence["beforeMeasurement"], evidence["final"]
    assert before["tick"] <= after["tick"] and before["queuedPackets"] == after["queuedPackets"] == 0
    delta = {key: after["counters"][key] - value for key, value in before["counters"].items()}
    assert delta == evidence["measurementAndDrainCounters"] and all(v >= 0 for v in delta.values())
    assert delta["InboundPackets"] == delta["OutboundPackets"] == delta["TransferPackets"]
    assert delta["Inbound"] == delta["Outbound"]
    assert delta["TransferPackets"] >= summary["forwardAccepted"]
    physical = (directory / "topology-physical.mmd").read_text(encoding="utf-8")
    logical = (directory / "topology-logical.mmd").read_text(encoding="utf-8")
    assert physical.count("<-->") == 10 and logical.count(" -->") == 90
    for site in range(10):
        assert f'S{site} <--> |' in physical.replace('<-->|', '<--> |')
    if options["ideal"]:
        assert summary["apiAttempts"] == 200 and summary["uniqueDeliveries"] == 2000
        assert summary["forwardAttempts"] == 1800 and summary["duplicateDeliveries"] == 0
    return summary, evidence


def cases():
    return [dict(id=f"{family}-relay-q{qos}-s{seed}", family=family, qos=qos, seed=seed)
            for family in ("ideal", "mosquitto") for qos in range(3) for seed in (42, 43, 44)]


def run(work):
    if study.git("status", "--porcelain"):
        raise ValueError("Freeze and clean MqttY before the series")
    sources = dict(mqttYCommit=study.git("rev-parse", "HEAD"), scriptSha256=study.sha(__file__),
                   verifierSha256=study.sha(Path(study.__file__)), profileSha256=study.sha(study.PROFILE),
                   binaries={p.name: study.sha(p) for p in sorted(study.DLL.parent.glob("*.dll"))})
    work.mkdir(parents=True, exist_ok=True)
    plan_path = work / "study-plan.json"
    if plan_path.exists():
        plan = study.read(plan_path)
        assert plan["sources"] == sources
    else:
        plan = dict(experiment=PUBLICATION, sources=sources, cases=cases(), mode="relay", topology="star",
                    brokers=10, publishers=160, offeredRps=1000, payloadBytes=16,
                    warmupSeconds=.01, measurementSeconds=.2, drainTimeoutSeconds=1, tickSeconds=.0001)
        study.write(plan_path, plan)
        (work / "study-script.py").write_bytes(Path(__file__).read_bytes())
        (work / "verifier.py").write_bytes(Path(study.__file__).read_bytes())
        (work / "profile.json").write_bytes(study.PROFILE.read_bytes())
        runtime = work / "runtime"
        runtime.mkdir()
        for path in study.DLL.parent.iterdir():
            if path.is_file() and path.suffix in (".dll", ".json"):
                shutil.copyfile(path, runtime / path.name)
    state_path = work / "state.json"
    state = study.read(state_path) if state_path.exists() else dict(runs={}, replays={})
    for case in plan["cases"]:
        identity = case["id"]
        output = work / "runs" / identity
        if identity in state["runs"]:
            verify_network(output)
            continue
        assert study.git("status", "--porcelain") == "" and study.git("rev-parse", "HEAD") == sources["mqttYCommit"]
        assert {p.name: study.sha(p) for p in sorted(study.DLL.parent.glob("*.dll"))} == sources["binaries"]
        assert study.sha(study.PROFILE) == sources["profileSha256"]
        if shutil.disk_usage(work).free < 5 * 1024**3:
            raise ValueError("Result disk reserve below 5 GiB")
        options = dict(mode="relay", topology="star", brokers=10, publishers=160, qos=case["qos"], seed=case["seed"],
                       ideal=case["family"] == "ideal", payloadBytes=16, offeredRps=1000,
                       warmupSeconds=.01, measurementSeconds=.2, drainTimeoutSeconds=1)
        if not options["ideal"]:
            options.update(brokerType="Mosquitto", brokerProfilePath=str(work / "profile.json"))
        path = work / "configs" / (identity + ".json")
        study.write(path, options)
        command = ["dotnet", str(study.DLL), "run", "--config", str(path), "--output", str(output)]
        print(f"[{len(state['runs']) + 1}/18] {identity}", flush=True)
        result = subprocess.run(command, capture_output=True, timeout=300)
        study.write(work / "commands" / (identity + ".json"), command)
        (output / "stdout.txt").write_bytes(result.stdout)
        (output / "stderr.txt").write_bytes(result.stderr)
        summary, _ = verify_network(output)
        state["runs"][identity] = dict(exitCode=result.returncode, status=summary["status"])
        study.write(state_path, state)
    for case in [c for c in plan["cases"] if c["seed"] == 42]:
        identity = case["id"]
        if identity not in state["replays"]:
            original = work / "runs" / identity
            output = work / "replays" / identity
            print("Replay " + identity, flush=True)
            command = ["dotnet", str(study.DLL), "run", "--config", str(original / "run-config.json"), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, timeout=300)
            (output / "stdout.txt").write_bytes(result.stdout)
            (output / "stderr.txt").write_bytes(result.stderr)
            verify_network(output)
            assert study.sha(original / "summary.json") == study.sha(output / "summary.json")
            assert study.sha(original / "network-evidence.json") == study.sha(output / "network-evidence.json")
            state["replays"][identity] = dict(exitCode=result.returncode, identicalSummary=True, identicalNetworkEvidence=True)
            study.write(state_path, state)


def publish(work):
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["svg.hashsalt"] = PUBLICATION
    import matplotlib.pyplot as plt
    reports = study.ROOT / "reports"
    data = reports / "data" / PUBLICATION
    data.mkdir(parents=True, exist_ok=True)
    plan = study.read(work / "study-plan.json")
    state = study.read(work / "state.json")
    records = []
    for case in plan["cases"]:
        summary, evidence = verify_network(work / "runs" / case["id"])
        records.append(dict(**case, summary=summary, network=evidence))
    assert len(state["replays"]) == 6
    for identity in state["replays"]:
        verify_network(work / "replays" / identity)
        for name in ("summary.json", "network-evidence.json"):
            assert study.sha(work / "runs" / identity / name) == study.sha(work / "replays" / identity / name)
    groups = []
    metrics = ("sourceCompletedRps", "uniqueGoodput", "deliveryLoss", "receiverP99Ms", "publishP99Ms", "forwardAttempts")
    for family in ("ideal", "mosquitto"):
        for qos in range(3):
            group = [r for r in records if (r["family"], r["qos"]) == (family, qos)]
            groups.append(dict(family=family, qos=qos, **{m: study.estimate([r["summary"][m] for r in group]) for m in metrics},
                               transitPackets=study.estimate([r["network"]["measurementAndDrainCounters"]["TransferPackets"] for r in group])))
    study.write(data / "runs.json", records)
    study.write(data / "aggregates.json", groups)
    study.write(data / "study-plan.json", plan)
    study.write(data / "verification.json", dict(mainRuns=18, technicalFailures=sum(r["summary"]["status"] != "completed" for r in records),
        replays=state["replays"], rawPairsAndPayloadChecked=True, transitPathAndCounterDeltasChecked=True,
        warnings=[dict(id=r["id"], deliveryLoss=r["summary"]["deliveryLoss"], noMeasuredReceipts=r["summary"]["uniqueDeliveries"] == 0,
                       transitAdmissionRefusals=r["network"]["measurementAndDrainCounters"]["Refused"])
                  for r in records if r["summary"]["deliveryLoss"] or r["network"]["measurementAndDrainCounters"]["Refused"]]))
    with (data / "runs.csv").open("w", encoding="utf-8", newline="") as stream:
        columns = ["id", "family", "qos", "seed", *metrics, "transitPackets"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for r in records:
            writer.writerow({**{k: r[k] for k in columns[:4]}, **{m: r["summary"][m] for m in metrics},
                             "transitPackets": r["network"]["measurementAndDrainCounters"]["TransferPackets"]})
    for kind in ("physical", "logical"):
        for suffix in ("svg", "mmd"):
            shutil.copyfile(work / "runs/ideal-relay-q1-s42" / f"topology-{kind}.{suffix}", data / f"topology-{kind}.{suffix}")
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5), constrained_layout=True)
    for ax, metric, title in zip(axes, ("deliveryLoss", "receiverP99Ms", "transitPackets"), ("Недоставка, доля", "p99 получения, мс", "Пакеты через transit")):
        for family in ("ideal", "mosquitto"):
            rows = [g for g in groups if g["family"] == family]
            applicable = [g for g in rows if g[metric]["mean"] is not None]
            line, = ax.plot([g["qos"] for g in applicable], [g[metric]["mean"] for g in applicable], marker="o", label=family)
            for g in applicable:
                value = g[metric]
                if value["ci95"] is not None:
                    ax.errorbar(g["qos"], value["mean"], yerr=value["ci95"], color=line.get_color(), capsize=4)
                if value["n"] < 3:
                    ax.annotate(f"n={value['n']}/3", (g["qos"], value["mean"]))
        ax.set_xticks(range(3)); ax.set_xlabel("QoS"); ax.set_title(title); ax.grid(alpha=.2); ax.legend()
    fig.savefig(data / "comparison.svg", metadata={"Date": None}); plt.close(fig)
    def number(item):
        if item["mean"] is None:
            return "н/п"
        value = f"{item['mean']:.4f}"
        return value + (f" ± {item['ci95']:.4f}" if item["ci95"] is not None else f" (n={item['n']}/3)")
    lines = ["# Relay: десять брокеров через промежуточный сетевой узел", "",
        "Выполнено 18/18 симуляций: идеальная модель и профиль Mosquitto × QoS 0/1/2 × seeds 42/43/44. "
        "Шесть выбранных точек повторены: summary.json и network-evidence.json совпадают побайтно. Это отдельная программа; прежние результаты не объединяются с ней.", "",
        "Десять брокеров находятся на десяти площадках. Каждая площадка связана только с сетью transit; у transit нет брокера, клиента или MQTT-приложения. "
        "В Relay настроено 90 направлений: каждый брокер экспортирует исходные сообщения всем остальным, а импорт доставляет только локальным subscribers. "
        "Физический путь любой межброкерной связи — siteA → transit → siteB.", "",
        "160 исходных publishers — по 16 на брокер; по одному subscriber на площадке. Общая нагрузка 1000 сообщений/с, payload 16 байт. "
        "Прогрев 0,01 с, измерение 0,2 с, drain до 1 с, tick 0,1 мс. Для каждого запуска запланировано 200 исходных сообщений и 2000 пар доставки. "
        "Все одиннадцать модельных сетей имеют ёмкость 125000000 байт/с, окно 100 ticks, очередь 100000 и задержку 2 ticks. "
        "Ёмкость transit общая для проходящих через него направлений, а не выделенная каждой MQTT-связи.", "",
        "## Топология", "", f"![Физическая звезда](data/{PUBLICATION}/topology-physical.svg)", "",
        f"![90 логических направлений](data/{PUBLICATION}/topology-logical.svg)", "",
        "Логическая схема показана матрицей: строка — источник, столбец — назначение. Mermaid сохраняет все 90 направлений. "
        "Матрица не означает, что у брокеров есть физические связи в обход transit.", "",
        "## Результаты", "", "Среднее ± t-CI95 по трём seeds; t=4,302652729911275. Интервал отражает условный разброс симулятора, а не натурную точность. "
        "При отсутствии получений p99 неприменим; для неполных групп показано n, CI95 не рассчитывается.", "",
        "| Модель | QoS | Исходные завершения/с | Уникальные доставки/с | Недоставка | p99 получения, мс | Пересылки | Пакеты через transit |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for group in groups:
        lines.append(f"| {group['family']} | {group['qos']} | " + " | ".join(number(group[m]) for m in ("sourceCompletedRps", "uniqueGoodput", "deliveryLoss", "receiverP99Ms", "forwardAttempts", "transitPackets")) + " |")
    lines += ["", f"![Сравнение](data/{PUBLICATION}/comparison.svg)", "", "## Что проверено и что означают числа", "",
        "В идеальной модели получены все 2000 ожидаемых пар в каждом запуске, дубликатов нет; 200 исходных сообщений дали 1800 межброкерных пересылок. "
        "Это 200 × 9 адресатов, без повторного экспорта импорта. Маршруты всех 90 направлений сверены с PathFinder; счётчики transit подтверждают фактическое прохождение модельных пакетов. "
        "Из конечного снимка вычтен снимок после прогрева: соединения, подписки и прогрев исключены. Пакеты и байты transit включают MQTT-обмен, в том числе подтверждения; они не равны числу исходных сообщений. "
        "Отказ приёма в очередь не равен потере публикации: передача может быть отложена.", "",
        "Исходные RPS учитывают только publishers; служебные публикации сюда не добавляются. Уникальные доставки считаются по парам ID–subscriber. "
        "Ожидаемые пары = исходные API-попытки × 10; недоставка = 1 − полученные уникальные пары / ожидаемые. "
        "Завершения и получения в drain относятся к той же когорте и делятся на 0,2 с подачи нагрузки. Такой goodput не доказывает устойчивую предельную пропускную способность. "
        "p99 относится только к дошедшим сообщениям; ACK QoS 0 неприменим.", "",
        "Federation.App задаёт конфигурацию, строит граф и запускает фазы. MqttRelay выполняет MQTT-пересылку; ядро MqttY исполняет пакеты, задержки и очереди. "
        "transit обслуживает сетевую очередь и не разбирает темы MQTT. Python проверяет первичные журналы, разности счётчиков и строит отчёт. MqttBenchmark здесь не запускается.", "",
        "## Ограничения", "", "Mosquitto использует неизменённую точку профиля N=16. Профиль содержит условные потери, которые повторно применяются на этапах доставки служебным клиентам и подписчикам. "
        "Поэтому большая недоставка здесь не доказывает такие же потери настоящего Mosquitto при новой нагрузке. Дополнительные клиенты, fanout и 90 направлений не калибровались натурно. "
        "Меньший p99 среди немногих получений не означает лучшую обработку всего потока. CPU/RSS, EST, отказоустойчивость и предельная производительность не исследованы. "
        "Сравнение с single по этому профилю не выполнено: для 160 publishers на центральном брокере нужна отсутствующая точка N=160. Подмена соседней точкой запрещена.", "",
        "## Повторение и данные", "", "Из корня мета-репозитория:", "", "```text",
        "dotnet run --project mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App -c Release -- demo --brokers 10 --publishers 160 --topology star --mode relay --qos 1 --seed 42",
        "python tools/federation/transit_study.py run --work-dir <новый-каталог>",
        "python tools/federation/transit_study.py report --work-dir <тот-же-каталог>", "```", "",
        f"MqttY commit: `{plan['sources']['mqttYCommit']}`. SHA-256 профиля: `{plan['sources']['profileSha256']}`.", "",
        f"[Программа](data/{PUBLICATION}/study-plan.json), [CSV](data/{PUBLICATION}/runs.csv), [сводки и сетевые доказательства](data/{PUBLICATION}/runs.json), "
        f"[агрегаты](data/{PUBLICATION}/aggregates.json), [проверки и предупреждения](data/{PUBLICATION}/verification.json). "
        f"[Квитанция архива](data/{PUBLICATION}/archive-receipt.json) подтверждает внешнее хранение полных первичных журналов. Архивы не добавляются в Git.", ""]
    (reports / "Federation-ten-brokers.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    reseal(data)
    print("Verified and published 18 runs and 6 exact replays", flush=True)


def reseal(data):
    reports = study.ROOT / "reports"
    files = [p for p in data.iterdir() if p.is_file() and p.name != "publication-sha256.json"] + [reports / "Federation-ten-brokers.md"]
    study.write(data / "publication-sha256.json", {p.relative_to(reports).as_posix(): study.sha(p) for p in sorted(files)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "report"))
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    {"run": run, "report": publish}[args.command](args.work_dir.resolve())
