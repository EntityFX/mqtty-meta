"""Reproducible comparison of one vs three brokers; no native measurements are performed."""
import argparse
import csv
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App/bin/Release/net6.0/EntityFX.MqttY.LoadBalancing.App.dll"
PROFILE = ROOT / "reports/data/Mosquitto/profile/broker-calibration.v3.json"
OBSERVATIONS = ROOT / "reports/data/Mosquitto/calibration/observations.json"
SEEDS = (42, 43, 44)
LOADS = (1000, 60000)
MODES = ("directBalanced", "roundRobin", "leastConnections")
LABELS = {"single16": "Один / 16 publishers", "single48": "Один / 48 publishers",
          "single64": "Один / 64 publishers", "directBalanced": "Три / directBalanced",
          "roundRobin": "Три / Round Robin", "leastConnections": "Три / Least Connections"}
T95_DF2 = 4.302652729911275


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT / "mqtty"), *args], text=True).strip()


def verify_seal(directory):
    directory = Path(directory).resolve()
    registry = read(directory / "sha256.json")
    if not registry:
        raise ValueError("Empty registry: " + str(directory))
    for relative, expected in registry.items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or digest(path) != expected:
            raise ValueError("Evidence integrity failure: " + str(path))


def validate_summary(summary):
    """Independently recalculate rates, cohort accounting and nearest-rank quantiles."""
    seconds = summary["measurementSeconds"]
    backends = summary["backends"]
    delivery = summary["delivery"]
    if summary["apiAttempts"] != delivery["measurementCreated"]:
        raise ValueError("API attempt/ledger mismatch")
    if summary["scheduledPublishes"] - summary["apiAttempts"] != summary["backpressureUnsent"]:
        raise ValueError("Schedule/backpressure mismatch")
    if delivery["uniqueDelivered"] != sum(b["uniqueReceived"] for b in backends):
        raise ValueError("Backend/ledger delivery mismatch")
    if delivery["uniqueDelivered"] != sum(delivery["latencyHistogramTicks"].values()):
        raise ValueError("Receiver histogram count mismatch")
    if delivery["uniqueDelivered"] != summary["uniqueReceivedBeforeDrain"] + summary["uniqueReceivedDuringDrain"]:
        raise ValueError("Drain accounting mismatch")
    if not 0 <= delivery["uniqueDelivered"] <= summary["apiAttempts"]:
        raise ValueError("Impossible unique delivery count")
    if summary["status"] != "completed":
        return
    if summary["graphErrors"] != 0 or summary["publisherPendingAfterDrain"] != 0:
        raise ValueError("Completed run has graph errors or pending publishers")
    expected = {
        "backendCompletedRps": sum(b["metrics"]["completed"] for b in backends) / seconds,
        "uniqueGoodput": delivery["uniqueDelivered"] / seconds,
        "attemptDeliveryLoss": ratio_loss(delivery["uniqueDelivered"], summary["apiAttempts"]),
        "backendDeliveryLossRate": ratio_loss(sum(b["metrics"]["delivered"] for b in backends), sum(b["metrics"]["expectedDeliveries"] for b in backends)),
        "backendPublishFailureRate": safe_ratio(sum(b["metrics"]["rateRejected"] + b["metrics"]["publishFailed"] for b in backends), sum(b["metrics"]["attempted"] for b in backends)),
    }
    merged = defaultdict(int)
    for backend in backends:
        for tick, count in backend["metrics"]["latencyHistogramTicks"].items():
            merged[int(tick)] += count
    if dict(merged) != {int(k): v for k, v in summary["publishLatencyHistogramTicks"].items()}:
        raise ValueError("Publication histogram sum mismatch")
    for p in (50, 95, 99):
        expected[f"receiverP{p}Ms"] = quantile(delivery["latencyHistogramTicks"], p / 100, summary["tickMilliseconds"])
        # QoS 0 has no completion latency histogram; null is the explicit notApplicable value.
        expected[f"publishP{p}Ms"] = quantile(merged, p / 100, summary["tickMilliseconds"])
    for key, value in expected.items():
        actual = summary[key]
        if value is None and actual is not None or value is not None and (actual is None or not math.isclose(value, actual, rel_tol=1e-11, abs_tol=1e-11)):
            raise ValueError(f"Formula mismatch {key}: {actual} != {value}")


def safe_ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def ratio_loss(delivered, attempted):
    value = safe_ratio(delivered, attempted)
    return None if value is None else 1 - value


def quantile(histogram, probability, tick_ms):
    total = sum(histogram.values())
    if not total:
        return None
    rank = max(1, math.ceil(probability * total))
    cumulative = 0
    for tick, count in sorted((int(t), n) for t, n in histogram.items()):
        cumulative += count
        if cumulative >= rank:
            return tick * tick_ms
    raise ValueError("Invalid histogram")


def estimate(values):
    values = [value for value in values if value is not None]
    if not values:
        return {"n": 0, "mean": None, "ci95HalfWidth": None}
    mean = statistics.mean(values)
    half = T95_DF2 * statistics.stdev(values) / math.sqrt(3) if len(values) == 3 else None
    return {"n": len(values), "mean": mean, "ci95HalfWidth": half}


def prepare(work):
    if work.exists():
        return read(work / "study-plan.json")
    if git("status", "--porcelain"):
        raise ValueError("Commit or isolate MqttY source changes before measurement.")
    if not APP.exists():
        raise ValueError("Build LoadBalancing.App in Release before running this study.")
    profile, observations = read(PROFILE), read(OBSERVATIONS)
    if profile["provenance"]["observationsSha256"].lower() != digest(OBSERVATIONS):
        raise ValueError("Mosquitto calibration observations do not match the frozen profile.")
    points = {(p["messageBytes"], p["qos"], p["publishers"]) for p in profile["calibration"]["points"] if p["broker"] == "Mosquitto"}
    for qos in range(3):
        if not {(16, qos, 16), (16, qos, 64)} <= points:
            raise ValueError("Required exact Mosquitto points are missing.")
    if any((16, qos, 48) in points for qos in range(3)):
        raise ValueError("Protocol assumes N=48 is absent; revise the design before a new study.")
    work.mkdir(parents=True)
    (work / "inputs").mkdir()
    shutil.copyfile(PROFILE, work / "inputs/profile.json")
    shutil.copyfile(OBSERVATIONS, work / "inputs/observations.json")
    shutil.copyfile(__file__, work / "study-script.py")
    runtime = work / "runtime"
    runtime.mkdir()
    for source in APP.parent.iterdir():
        if source.suffix == ".dll" or source.name.endswith((".deps.json", ".runtimeconfig.json")):
            shutil.copyfile(source, runtime / source.name)
    cases = []
    for family in ("demo", "mosquitto"):
        variants = ["single16", "single48", "single64", *MODES] if family == "demo" else ["single16", "single64", *MODES]
        for load in LOADS:
            for qos in range(3):
                for variant in variants:
                    for seed in SEEDS:
                        publishers = int(variant[6:]) if variant.startswith("single") else 48
                        cases.append({"id": f"{family}-r{load}-q{qos}-{variant}-s{seed}", "family": family,
                                      "load": load, "qos": qos, "variant": variant, "seed": seed,
                                      "publishers": publishers, "brokers": 1 if variant.startswith("single") else 3,
                                      "mode": "single" if variant.startswith("single") else variant})
    plan = {"protocol": "one-vs-three-v1", "payloadBytes": 16, "warmupSeconds": .01,
            "measurementSeconds": .2, "drainTimeoutSeconds": 1, "tickSeconds": .0001,
            "networkLength": 2, "network": {"capacityBytesPerSecond": 125000000,
            "throughputWindowTicks": 100, "queueCapacity": 100000, "transferTicks": 2},
            "limitation": "No native profile N=48; Mosquitto single16/single64 vs 3x16 changes publisher count.",
            "sources": {"mqttYCommit": git("rev-parse", "HEAD"), "scriptSha256": digest(__file__),
                        "profileSha256": digest(PROFILE), "observationsSha256": digest(OBSERVATIONS),
                        "runtime": {p.name: digest(p) for p in sorted(runtime.iterdir())}}, "cases": cases}
    plan["replayCaseIds"] = [c["id"] for c in cases if c["seed"] == 42 and c["load"] == 60000 and
                            ((c["qos"] == 2 and c["variant"] in (("single48", "roundRobin") if c["family"] == "demo" else ("single16", "single64", "roundRobin"))) or
                             (c["family"] == "mosquitto" and c["qos"] == 0 and c["variant"] == "single16"))]
    write(work / "study-plan.json", plan)
    return plan


def command(work, case, plan):
    output = work / "runs" / case["id"]
    if case["family"] == "demo":
        return ["dotnet", str(APP), "demo", "--brokers", str(case["brokers"]),
                "--publishers", str(case["publishers"]), "--mode", case["mode"], "--qos", str(case["qos"]),
                "--rps", str(case["load"]), "--seconds", str(plan["measurementSeconds"]), "--seed", str(case["seed"]), "--output", str(output)]
    config = {"schemaVersion": 1, "mode": case["mode"], "brokerType": "Mosquitto", "ideal": False,
              "backendCount": case["brokers"], "totalPublishers": case["publishers"], "payloadBytes": 16,
              "publishQos": case["qos"], "subscribeQos": case["qos"], "offeredRps": case["load"], "seed": case["seed"],
              "warmupSeconds": plan["warmupSeconds"], "measurementSeconds": plan["measurementSeconds"],
              "drainTimeoutSeconds": plan["drainTimeoutSeconds"], "tickPeriod": "00:00:00.0001000",
              "networkLength": plan["networkLength"], "network": plan["network"], "brokerProfilePath": "../inputs/profile.json"}
    path = work / "configs" / (case["id"] + ".json")
    write(path, config)
    return ["dotnet", str(APP), "run", "--config", str(path), "--output", str(output)]


def execute(cmd, log):
    if shutil.disk_usage(log.parent).free < 5 * 1024**3:
        raise RuntimeError("Less than 5 GiB free; preserve evidence and stop before filling the system disk.")
    started = time.perf_counter()
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(cmd, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    return {"exitCode": result.returncode, "wallSeconds": time.perf_counter() - started, "command": cmd}


def run(work):
    plan = prepare(work)
    if digest(__file__) != plan["sources"]["scriptSha256"]:
        raise ValueError("Resume requires the original study script; restore it from the saved study-script.py.")
    if git("rev-parse", "HEAD") != plan["sources"]["mqttYCommit"] or git("status", "--porcelain"):
        raise ValueError("MqttY source identity changed during the study.")
    for name, expected in plan["sources"]["runtime"].items():
        if digest(work / "runtime" / name) != expected:
            raise ValueError("Frozen runtime changed: " + name)
    for file, key in (("profile.json", "profileSha256"), ("observations.json", "observationsSha256")):
        if digest(work / "inputs" / file) != plan["sources"][key]:
            raise ValueError("Frozen input changed: " + file)
    logs = work / "logs"
    logs.mkdir(exist_ok=True)
    state = read(work / "state.json") if (work / "state.json").exists() else {"runs": {}, "replays": {}}
    for index, case in enumerate(plan["cases"], 1):
        output = work / "runs" / case["id"]
        if (output / "summary.json").exists() and case["id"] in state["runs"]:
            verify_seal(output)
            validate_summary(read(output / "summary.json"))
            continue
        if output.exists():
            raise RuntimeError("Interrupted attempt preserved; investigate before resuming: " + str(output))
        if git("rev-parse", "HEAD") != plan["sources"]["mqttYCommit"] or git("status", "--porcelain"):
            raise ValueError("MqttY source identity changed during measurement.")
        for name, expected in plan["sources"]["runtime"].items():
            if digest(APP.parent / name) != expected:
                raise ValueError("Executing binary changed: " + name)
        cmd = command(work, case, plan)
        info = execute(cmd, logs / (case["id"] + ".log"))
        summary = read(output / "summary.json") if (output / "summary.json").exists() else None
        if summary is not None:
            verify_seal(output)
            validate_summary(summary)
        info["status"] = summary["status"] if summary else "processFailed"
        state["runs"][case["id"]] = info
        write(work / "state.json", state)
        print(f"[{index}/{len(plan['cases'])}] {case['id']}: {info['status']}; {info['wallSeconds']:.1f}s", flush=True)
        if info["exitCode"] not in (0, 2) or summary is None:
            raise RuntimeError("Process failure; evidence retained: " + case["id"])
    for case_id in plan["replayCaseIds"]:
        if case_id in state["replays"]:
            continue
        original = work / "runs" / case_id
        output = work / "replays" / case_id
        cmd = ["dotnet", str(APP), "run", "--config", str(original / "run-config.json"), "--output", str(output)]
        info = execute(cmd, logs / (case_id + "-replay.log"))
        verify_seal(output)
        info["identicalSummary"] = (original / "summary.json").read_bytes() == (output / "summary.json").read_bytes()
        state["replays"][case_id] = info
        write(work / "state.json", state)
        print("Replay " + case_id + ": " + str(info["identicalSummary"]), flush=True)
        if not info["identicalSummary"] or info["exitCode"] != 0:
            raise ValueError("Replay failure: " + case_id)
    return plan, state


def collect(work):
    plan = read(work / "study-plan.json")
    state = read(work / "state.json")
    records = []
    for case in plan["cases"]:
        output = work / "runs" / case["id"]
        if not (output / "summary.json").exists():
            continue
        verify_seal(output)
        summary = read(output / "summary.json")
        validate_summary(summary)
        manifest = read(output / "manifest.json")
        if manifest["runtime"]["mqttYCommit"] != plan["sources"]["mqttYCommit"] or manifest["runtime"]["sourceStatus"]:
            raise ValueError("Manifest does not identify the clean MqttY source used by the study.")
        if manifest["runtime"]["binarySha256"] != {name: value for name, value in plan["sources"]["runtime"].items() if name.endswith(".dll")}:
            raise ValueError("Manifest executable identity mismatch")
        if case["family"] == "mosquitto" and manifest["profile"]["sha256"] != plan["sources"]["profileSha256"]:
            raise ValueError("Profile identity mismatch")
        records.append(dict(case, summary=summary, summarySha256=digest(output / "summary.json"),
                            manifestSha256=digest(output / "manifest.json"), command=state["runs"][case["id"]]["command"]))
    equality = []
    for family in ("demo", "mosquitto"):
        for load in LOADS:
            for qos in range(3):
                for seed in SEEDS:
                    group = [r for r in records if (r["family"], r["load"], r["qos"], r["seed"]) == (family, load, qos, seed) and r["variant"] in MODES]
                    equality.append({"family": family, "load": load, "qos": qos, "seed": seed,
                                     "threeSummariesIdentical": len(group) == 3 and len({r["summarySha256"] for r in group}) == 1})
    return plan, state, records, equality


def groups(records):
    result = []
    index = defaultdict(list)
    for record in records:
        index[(record["family"], record["load"], record["qos"], record["variant"])].append(record)
    for (family, load, qos, variant), group in index.items():
        valid = [r for r in group if r["summary"]["status"] == "completed"]
        row = {"family": family, "load": load, "qos": qos, "variant": variant, "successful": len(valid), "planned": 3}
        for key in ("backendCompletedRps", "uniqueGoodput", "publishP99Ms", "receiverP99Ms", "attemptDeliveryLoss", "backendPublishFailureRate", "backendDeliveryLossRate"):
            row[key] = estimate([r["summary"][key] for r in valid])
        row["scheduleFulfillment"] = estimate([safe_ratio(r["summary"]["delivery"]["uniqueDelivered"], r["summary"]["scheduledPublishes"]) for r in valid])
        result.append(row)
    return result


def number(value, digits=2):
    return "н/п" if value is None else f"{value:.{digits}f}"


def interval(value, digits=2):
    if value["mean"] is None:
        return "н/п"
    return number(value["mean"], digits) + (" ± " + number(value["ci95HalfWidth"], digits) if value["ci95HalfWidth"] is not None else " (CI н/п)")


def charts(aggregates, family, target):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    variants = [r["variant"] for r in aggregates if r["family"] == family]
    variants = list(dict.fromkeys(variants))
    names = {"single16": "S16", "single48": "S48", "single64": "S64", "directBalanced": "D3", "roundRobin": "RR3", "leastConnections": "LC3"}
    for metric in ("uniqueGoodput", "publishP99Ms"):
        fig, axes = plt.subplots(3, 2, figsize=(11, 9), constrained_layout=True)
        for qos in range(3):
            for col, load in enumerate(LOADS):
                ax = axes[qos, col]
                values = [next((r[metric] for r in aggregates if (r["family"], r["load"], r["qos"], r["variant"]) == (family, load, qos, v)), estimate([])) for v in variants]
                if not any(v["mean"] is not None for v in values):
                    ax.text(.5, .5, "QoS 0: ACK latency notApplicable" if metric == "publishP99Ms" and qos == 0 else "No completed results", transform=ax.transAxes, ha="center", fontsize=9)
                else:
                    ax.bar(range(len(variants)), [v["mean"] if v["mean"] is not None else float("nan") for v in values],
                           yerr=[v["ci95HalfWidth"] or 0 for v in values], capsize=3,
                           color=["#687d99" if v.startswith("single") else "#389678" for v in variants])
                ax.set_xticks(range(len(variants)), [names[v] for v in variants])
                ax.set_title(f"QoS {qos}; offered {load} msg/s")
                ax.set_ylabel("unique messages / load second" if metric == "uniqueGoodput" else "publish completion p99, ms")
                ax.grid(axis="y", alpha=.2)
        fig.suptitle(f"{family}: model results, mean and conditional t-CI95 across seeds")
        fig.savefig(target / f"{family}-{metric}.svg")
        plt.close(fig)


def report(work, reports):
    plan, state, records, equality = collect(work)
    aggregates = groups(records)
    data = reports / "data/load-balancing-study-v1"
    data.mkdir(parents=True, exist_ok=True)
    write(data / "study-plan.json", plan)
    write(data / "runs.json", records)
    write(data / "aggregates.json", aggregates)
    write(data / "verification.json", {"arithmeticChecked": len(records), "strategyEquality": equality,
          "replays": state["replays"], "fullMatrixComplete": len(records) == len(plan["cases"]),
          "successful": sum(r["summary"]["status"] == "completed" for r in records)})
    with (data / "runs.csv").open("w", encoding="utf-8", newline="") as stream:
        keys = ["id", "family", "load", "qos", "variant", "seed", "publishers", "brokers", "status", "apiAttempts", "uniqueReceived", "backendCompletedRps", "uniqueGoodput", "publishP99Ms", "receiverP99Ms", "attemptDeliveryLoss", "scheduledPublishes", "backpressureUnsent"]
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        for record in records:
            summary = record["summary"]
            writer.writerow({**{k: record[k] for k in keys if k in record}, **{k: summary[k] for k in keys if k in summary}, "uniqueReceived": summary["delivery"]["uniqueDelivered"]})
    shared = """## Условия и границы вывода

Проверяется назначение постоянного MQTT-соединения одному backend до CONNECT. На каждом backend — свой прямой receiver; подписки между независимыми брокерами не объединяются. CPU, RSS, стоимость TCP proxy, health checks и миграция сессий не моделируются. Поэтому здесь нет оценки EST и доказательства производительности настоящего балансировщика.

Payload — 16 байт; QoS публикации и подписки совпадают. Нагрузка общая: 1000 либо 60 000 сообщений/с; seeds 42, 43, 44. Прогрев / measurement / drain: 0.01 / 0.2 / до 1 модельной секунды, tick 0.1 мс. Один клиентский сегмент, два одинаковых сетевых участка к каждому backend; канал 125 000 000 байт/с, окно 100 ticks, transfer 2 ticks, очередь 100 000. Транспорт фиксирован до опытов; параметры по полученному результату не подбирались. Режим EmpiricalServiceBudget одиночного BrokerFidelity не переносится на этот пул.

RPS завершений = завершения измерительной когорты backend / 0.2 с. Goodput = первые корректные получения её ID / 0.2 с, включая поздние получения в drain. Выполнение плана = uniqueReceived / scheduledPublishes. Недоставка = 1−uniqueReceived/apiAttempts; это другой знаменатель. Backpressure — scheduled−apiAttempts, а немедленный отказ Publish не равен всем протокольным ошибкам. Нулевой знаменатель — н/п. Прогрев исключается по ID/поколению. Глобальные p99 считаются из объединённых гистограмм; QoS 0 ACK latency неприменима.

В таблицах — среднее ± t-CI95 для трёх seeds (t=4.302652729911275, df=2). Это условный разброс стохастической модели с одним замороженным профилем, а не неопределённость натурных измерений. Совпавшие детерминированные результаты дают нулевую ширину CI; это не доказывает нулевую ошибку относительно реальности. Короткое окно предназначено для проверки механизма и предварительного анализа, а не для вывода о стационарном p99.

## Эффективность и сравнение стратегий

S = goodput пула / goodput одиночного варианта, E = S/3. E — условный throughput на добавленный backend относительно baseline, не оценка CPU/энергии и не интегральный балл брокера. При недогруженном одиночном сервере S≈1 и E≈1/3 ожидаемы. Если одновременно меняются число publishers и точка профиля, S нельзя целиком приписывать числу брокеров.

DirectBalanced — контроль заранее заданной карты без выбора во время CONNECT. При одинаковых постоянных сессиях RR и Least Connections с немедленным резервированием дают ту же карту. Их превосходство друг над другом в этой программе не ожидается; проверяется побайтное равенство scientific summary при одинаковых входах и seed.
"""
    for family, filename in (("demo", "Load-balancing-demo.md"), ("mosquitto", "Load-balancing-Mosquitto.md")):
        selected = [r for r in records if r["family"] == family]
        successful = sum(r["summary"]["status"] == "completed" for r in selected)
        planned = sum(c["family"] == family for c in plan["cases"])
        title = "Идеальные брокеры: один против трёх" if family == "demo" else "Двойник Mosquitto: один против трёх"
        text = f"# {title}\n\nУспешно **{successful}/{planned}**, технических отказов **{len(selected)-successful}**; отсутствует результатов **{planned-len(selected)}**. Все числа ниже получены запуском MqttY, а не оценкой заранее.\n\n"
        if family == "demo":
            text += "Основное сопоставление: один брокер с 48 publishers против трёх по 16, при одинаковой общей интенсивности. Дополнительные одиночные варианты N=16 и N=64 показывают влияние числа клиентов. Реальные профили не читаются; использована команда demo с сохранением результатов.\n\n"
        else:
            text += "Использован неизменённый эмпирический профиль Mosquitto v4, построенный по прежним натурным измерениям. **Новых натурных измерений в этой работе нет.** Сравниваются single N=16, single N=64 и пул 3×16.\n\n**Строго одинаковая популяция для single N=48 отсутствует в профиле.** Интерполяция/подмена N не выполнялась. Поэтому S относительно N=16 включает увеличение числа publishers; вариант N=64 — дополнительный контроль с большим числом клиентов у одиночного брокера. Это предварительное модельное сравнение; чистый эффект трёх реальных брокеров здесь не выделен.\n\n"
        text += shared
        text += "\n## Полученные результаты\n\nS16/S48/S64 обозначают одиночный брокер и общее число publishers; D3/RR3/LC3 — три брокера с 48 publishers всего.\n\n"
        for load in LOADS:
            for qos in range(3):
                text += f"### Нагрузка {load:,} сообщений/с, QoS {qos}\n\n".replace(",", " ")
                text += "| Вариант | Завершения/с | Goodput/с | Выполнение плана, % | Недоставка, доля | p99 завершения, мс | Успешно |\n| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n"
                for row in aggregates:
                    if (row["family"], row["load"], row["qos"]) != (family, load, qos):
                        continue
                    fulfilled = row["scheduleFulfillment"]["mean"]
                    text += f"| {LABELS[row['variant']]} | {interval(row['backendCompletedRps'])} | {interval(row['uniqueGoodput'])} | {number(None if fulfilled is None else 100*fulfilled)} | {number(row['attemptDeliveryLoss']['mean'], 6)} | {interval(row['publishP99Ms'], 3)} | {row['successful']}/3 |\n"
                text += "\n"
        text += "## Масштабирование при высокой нагрузке\n\n| QoS | Baseline | S (RR3/baseline), среднее ± CI95 | E=S/3 |\n| --- | --- | ---: | ---: |\n"
        references = ("single16", "single48", "single64") if family == "demo" else ("single16", "single64")
        for qos in range(3):
            for reference in references:
                ratios = []
                for seed in SEEDS:
                    pair = {r["variant"]: r["summary"] for r in selected if r["qos"] == qos and r["load"] == 60000 and r["seed"] == seed and r["summary"]["status"] == "completed"}
                    if "roundRobin" in pair and reference in pair:
                        ratios.append(safe_ratio(pair["roundRobin"]["uniqueGoodput"], pair[reference]["uniqueGoodput"]))
                value = estimate(ratios)
                text += f"| {qos} | {reference} | {interval(value, 4)} | {number(None if value['mean'] is None else value['mean']/3, 4)} |\n"
        text += f"\n![Goodput](data/load-balancing-study-v1/{family}-uniqueGoodput.svg)\n\n![p99](data/load-balancing-study-v1/{family}-publishP99Ms.svg)\n\n"
        matched = [e for e in equality if e["family"] == family]
        text += f"## Проверяемость\n\nПобайтно совпали scientific summaries D3/RR3/LC3 в **{sum(e['threeSummariesIdentical'] for e in matched)}/{len(matched)}** сочетаниях нагрузки, QoS и seed. Это контроль отсутствия искусственного выигрыша алгоритма. Все три одинаковых варианта выполнялись отдельно.\n\n"
        text += f"MqttY commit: `{plan['sources']['mqttYCommit']}`. Профиль SHA-256: `{plan['sources']['profileSha256']}`" + (" (в демо не применяется).\n\n" if family == "demo" else ".\n\n")
        text += "Проверены seals, суммы backend, RPS, уникальные доставки, учёт drain и квантили по гистограммам. Данные: [CSV](data/load-balancing-study-v1/runs.csv), [scientific summaries](data/load-balancing-study-v1/runs.json), [агрегаты](data/load-balancing-study-v1/aggregates.json), [проверки и повторные запуски](data/load-balancing-study-v1/verification.json), [точная программа](data/load-balancing-study-v1/study-plan.json). Полные данные сохраняются отдельно от компактной публикации; расположение и хэш архива указываются в archive-receipt.json после внешнего архивирования.\n\n"
        text += "## Как повторить\n\nИз корня мета-репозитория: собрать LoadBalancing.App в Release, затем выполнить `python tools/load-balancing/study.py run --work-dir <новый-каталог-на-диске-данных>` и `python tools/load-balancing/study.py report --work-dir <тот-же-каталог>`. Профиль и observations берутся из опубликованных входов Mosquitto и сверяются по SHA. Для отчётов нужен Matplotlib. Параметры программы фиксируются до первого запуска. Каждый отдельный сохранённый опыт повторяется обычной командой приложения `run --config <run>/run-config.json --output <new-directory>`.\n"
        (reports / filename).write_text(text, encoding="utf-8")
        charts(aggregates, family, data)
    write(data / "publication-sha256.json", {p.relative_to(reports).as_posix(): digest(p) for p in sorted([*data.glob("*"), reports / "Load-balancing-demo.md", reports / "Load-balancing-Mosquitto.md"]) if p.is_file() and p.name != "publication-sha256.json"})
    print(f"Published {len(records)} scientific summaries and two Markdown reports", flush=True)


def archive(work, target):
    collect(work)
    if target.exists():
        raise ValueError("Archive already exists; never overwrite evidence.")
    with tarfile.open(target, "w:gz", compresslevel=1) as stream:
        stream.add(work, arcname=work.name)
    with tarfile.open(target, "r:gz") as stream:
        for member in stream.getmembers():
            if member.isfile():
                relative = Path(member.name).relative_to(work.name)
                data = stream.extractfile(member)
                expected = digest(work / relative)
                value = hashlib.sha256()
                for chunk in iter(lambda: data.read(1024 * 1024), b""):
                    value.update(chunk)
                if value.hexdigest() != expected:
                    raise ValueError("Archive member mismatch: " + str(relative))
    write(target.with_suffix(target.suffix + ".receipt.json"), {"archiveSha256": digest(target), "bytes": target.stat().st_size, "allMembersVerified": True})
    print("Verified archive: " + str(target), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "report", "verify", "archive"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--reports", type=Path, default=ROOT / "reports")
    parser.add_argument("--archive-path", type=Path)
    args = parser.parse_args()
    directory = args.work_dir.resolve()
    if args.action == "run":
        run(directory)
    elif args.action == "report":
        report(directory, args.reports.resolve())
    elif args.action == "verify":
        plan, state, records, equality = collect(directory)
        print(json.dumps({"planned": len(plan["cases"]), "collected": len(records), "successful": sum(r["summary"]["status"] == "completed" for r in records),
                          "equalStrategies": sum(e["threeSummariesIdentical"] for e in equality), "equalityGroups": len(equality), "replays": len(state["replays"])}))
    elif args.action == "archive":
        if args.archive_path is None:
            parser.error("archive requires --archive-path outside --work-dir")
        target = args.archive_path.resolve()
        if target.is_relative_to(directory):
            parser.error("The archive must be outside the evidence directory")
        archive(directory, target)
