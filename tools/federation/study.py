"""Run, independently verify and publish a bounded static federation acceptance study."""
import argparse
import base64
import csv
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MQTTY = ROOT / "mqtty"
APP = MQTTY / "src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App"
DLL = APP / "bin/Release/net6.0/EntityFX.MqttY.Federation.App.dll"
PROFILE = ROOT / "reports/data/Mosquitto/profile/broker-calibration.v3.json"
MODES = ("single", "relay", "federation")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(*args):
    return subprocess.check_output(["git", "-C", str(MQTTY), *args], text=True).strip()


def seal(directory):
    directory = Path(directory).resolve()
    registry = read(directory / "sha256.json")
    for relative, expected in registry.items():
        target = (directory / relative).resolve()
        if not target.is_relative_to(directory) or not target.is_file() or sha(target) != expected:
            raise ValueError("Evidence seal failed: " + str(target))


def close(actual, expected):
    if actual is None or expected is None:
        assert actual is expected, (actual, expected)
    else:
        assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10), (actual, expected)


def quantile(histogram, fraction, tick_ms):
    count = sum(histogram.values())
    if not count:
        return None
    rank = math.ceil(count * fraction)
    total = 0
    for tick, n in sorted((int(t), n) for t, n in histogram.items()):
        total += n
        if total >= rank:
            return tick * tick_ms
    raise ValueError("Invalid histogram")


def verify_run(directory):
    directory = Path(directory)
    seal(directory)
    s = read(directory / "summary.json")
    o = read(directory / "run-config.json")
    publications = {p["id"]: p for p in read(directory / "publications.json")}
    hashes = {identity: hashlib.sha256(base64.b64decode(p["payload"])).hexdigest() for identity, p in publications.items()}
    assert len({(p["origin"], p["sequence"]) for p in publications.values()}) == len(publications)
    transmissions = read(directory / "transmissions.json")
    for t in transmissions:
        p = publications[t["publicationId"]]
        assert t["measurement"] == p["measurement"] and t["payloadSha256"] == hashes[p["id"]]
        assert t["payloadBytes"] == o["payloadBytes"]
        assert t["topicPayloadBytes"] == len(t["topic"].encode("utf-8")) + t["payloadBytes"]
    source = [t for t in transmissions if t["measurement"] and t["role"] == "source"]
    forwards = [t for t in transmissions if t["measurement"] and t["role"] == "forward"]
    assert len(source) == s["apiAttempts"] == sum(p["measurement"] for p in publications.values())
    assert len({t["publicationId"] for t in source}) == len(source)
    assert sum(not t["accepted"] for t in source) == s["sendRejected"]
    assert s["scheduledPublishes"] - len(source) == s["backpressureUnsent"] >= 0
    assert len(forwards) == s["forwardAttempts"]
    assert sum(t["accepted"] for t in forwards) == s["forwardAccepted"]
    assert sum(not t["accepted"] for t in forwards) == s["forwardRejected"]
    assert sum(t["topicPayloadBytes"] for t in forwards) == s["forwardTopicPayloadBytes"]
    receivers = {r["site"]: r for r in s["receivers"]}
    expected = len(source) * len(receivers)
    assert expected == s["expectedDeliveries"]
    seen = set()
    per_receiver = {r: Counter() for r in receivers}
    duplicates = Counter()
    before = 0
    drain = next((p["startTick"] for p in s["phases"] if p["name"] == "drain"), None)
    for receipt in read(directory / "delivery-receipts.json"):
        p = publications[receipt["publicationId"]]
        assert receipt["payloadSha256"] == hashes[p["id"]]
        assert receipt["topic"] == f"federation/delivered/{p['origin']}"
        assert receipt["tick"] >= p["tick"]
        if not p["measurement"]:
            continue
        pair = (p["id"], receipt["receiver"])
        assert pair[1] in receivers
        if pair in seen:
            duplicates[pair[1]] += 1
        else:
            seen.add(pair)
            per_receiver[pair[1]][receipt["tick"] - p["tick"]] += 1
            before += drain is not None and receipt["tick"] < drain
    assert len(seen) == s["uniqueDeliveries"]
    if drain is not None:
        assert before == s["uniqueBeforeDrain"] and len(seen) - before == s["uniqueDuringDrain"]
    assert sum(duplicates.values()) == s["duplicateDeliveries"]
    rh = Counter()
    for site, receiver in receivers.items():
        hist = {str(t): n for t, n in per_receiver[site].items()}
        assert hist == receiver["latencyHistogramTicks"]
        assert sum(hist.values()) == receiver["uniqueDeliveries"]
        assert duplicates[site] == receiver["duplicates"]
        rh.update(per_receiver[site])
    assert {str(t): n for t, n in rh.items()} == s["receiverHistogramTicks"]
    fully = sum(p["measurement"] and all((p["id"], r) in seen for r in receivers) for p in publications.values()) if receivers else None
    assert fully == s["fullyDeliveredPublications"]
    ph = Counter()
    completed = 0
    for p in s["sources"]:
        m = p["metrics"]
        assert 0 <= m["completed"] <= m["admitted"] <= m["attempted"]
        assert m["completed"] + m["publishFailed"] + m["rateRejected"] <= m["attempted"]
        completed += m["completed"]
        ph.update({int(t): n for t, n in m["latencyHistogramTicks"].items()})
    assert completed == s["sourceCompleted"] == s["sourceCompletedBeforeDrain"] + s["sourceCompletedDuringDrain"]
    assert {str(t): n for t, n in ph.items()} == s["publishHistogramTicks"]
    if o["qos"]:
        assert sum(ph.values()) == completed
    for label, h in (("publish", ph), ("receiver", rh)):
        for percent in (50, 95, 99):
            expected_q = None if label == "publish" and o["qos"] == 0 else quantile(h, percent / 100, s["tickMilliseconds"])
            close(s[f"{label}P{percent}Ms"], expected_q)
    close(s["deliveryLoss"], 1 - len(seen) / expected if expected else None)
    if s["status"] == "completed":
        close(s["sourceCompletedRps"], completed / o["measurementSeconds"])
        close(s["uniqueGoodput"], len(seen) / o["measurementSeconds"])
    return s


def cases():
    result = []
    for family in ("ideal", "mosquitto"):
        for mode in MODES:
            for qos in (0, 1, 2):
                for seed in (42, 43, 44):
                    result.append(dict(id=f"{family}-{mode}-q{qos}-s{seed}", family=family, mode=mode, qos=qos, seed=seed))
    return result


def run(work):
    if git("status", "--porcelain"):
        raise ValueError("Commit and clean MqttY before the acceptance study.")
    work.mkdir(parents=True, exist_ok=True)
    plan_path = work / "study-plan.json"
    sources = dict(mqttYCommit=git("rev-parse", "HEAD"), scriptSha256=sha(__file__), profileSha256=sha(PROFILE),
                   binaries={p.name: sha(p) for p in sorted(DLL.parent.glob("*.dll"))})
    if plan_path.exists():
        plan = read(plan_path)
        assert plan["sources"] == sources
    else:
        plan = dict(experiment="static-federation-acceptance-v1", sources=sources, cases=cases(),
                    duration=dict(warmup=.01, measurement=.2, drain=1, tick=.0001), offeredRps=1000)
        write(plan_path, plan)
        (work / "study-script.py").write_bytes(Path(__file__).read_bytes())
        (work / "profile.json").write_bytes(PROFILE.read_bytes())
        runtime = work / "runtime"
        runtime.mkdir()
        for p in DLL.parent.iterdir():
            if p.is_file() and p.suffix in (".dll", ".json"):
                (runtime / p.name).write_bytes(p.read_bytes())
    state = read(work / "state.json") if (work / "state.json").exists() else {"runs": {}, "replays": {}}
    for case in plan["cases"]:
        identity = case["id"]
        output = work / "runs" / identity
        if identity in state["runs"]:
            verify_run(output)
            continue
        if git("status", "--porcelain") or git("rev-parse", "HEAD") != sources["mqttYCommit"]:
            raise ValueError("MqttY changed during study")
        assert {p.name: sha(p) for p in sorted(DLL.parent.glob("*.dll"))} == sources["binaries"]
        assert sha(PROFILE) == sources["profileSha256"]
        if shutil.disk_usage(work).free < 5 * 1024**3:
            raise ValueError("Disk reserve below 5 GiB")
        config = dict(mode=case["mode"], qos=case["qos"], seed=case["seed"], topology="ring", ideal=case["family"] == "ideal",
                      brokers=3 if case["family"] == "ideal" else 4, publishers=12 if case["family"] == "ideal" else 64)
        if case["family"] != "ideal":
            config.update(brokerType="Mosquitto", brokerProfilePath=str(work / "profile.json"))
        path = work / "configs" / (identity + ".json")
        write(path, config)
        print(f"[{len(state['runs'])+1}/54] {identity}", flush=True)
        command = ["dotnet", str(DLL), "run", "--config", str(path), "--output", str(output)]
        result = subprocess.run(command, capture_output=True, timeout=300)
        write(work / "commands" / (identity + ".json"), command)
        (output / "process-stdout.txt").write_bytes(result.stdout)
        (output / "process-stderr.txt").write_bytes(result.stderr)
        summary = verify_run(output)
        state["runs"][identity] = dict(exitCode=result.returncode, status=summary["status"])
        write(work / "state.json", state)
    chosen = [c for c in plan["cases"] if c["qos"] == 2 and c["seed"] == 42]
    for case in chosen:
        identity = case["id"]
        if identity not in state["replays"]:
            original = work / "runs" / identity
            output = work / "replays" / identity
            print("Replay " + identity, flush=True)
            result = subprocess.run(["dotnet", str(DLL), "run", "--config", str(original / "run-config.json"), "--output", str(output)], capture_output=True, timeout=300)
            (output / "process-stdout.txt").write_bytes(result.stdout)
            (output / "process-stderr.txt").write_bytes(result.stderr)
            verify_run(output)
            state["replays"][identity] = dict(exitCode=result.returncode, identical=sha(original / "summary.json") == sha(output / "summary.json"))
            write(work / "state.json", state)


def estimate(values):
    values = [v for v in values if v is not None]
    if not values:
        return dict(n=0, mean=None, ci95=None)
    return dict(n=len(values), mean=statistics.mean(values), ci95=4.302652729911275 * statistics.stdev(values) / math.sqrt(3) if len(values) == 3 else None)


def publish(work):
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["svg.hashsalt"] = "static-federation-v1"
    import matplotlib.pyplot as plt
    plan = read(work / "study-plan.json")
    data = ROOT / "reports/data/federation-study-v1"
    data.mkdir(parents=True, exist_ok=True)
    records = []
    for case in plan["cases"]:
        directory = work / "runs" / case["id"]
        s = verify_run(directory)
        o = read(directory / "run-config.json")
        for key in ("mode", "qos", "seed"):
            assert o[key] == case[key]
        assert o["ideal"] == (case["family"] == "ideal")
        assert (o["brokers"], o["publishers"]) == ((3, 12) if o["ideal"] else (4, 64))
        assert (o["warmupSeconds"], o["measurementSeconds"], o["drainTimeoutSeconds"], o["offeredRps"], o["payloadBytes"]) == (.01, .2, 1, 1000, 16)
        if not o["ideal"]:
            assert sha(directory / "inputs/profile.json") == plan["sources"]["profileSha256"]
        records.append({**case, "summary": s})
    replays = []
    for case in plan["cases"]:
        replay = work / "replays" / case["id"]
        if replay.exists():
            verify_run(replay)
            equal = sha(replay / "summary.json") == sha(work / "runs" / case["id"] / "summary.json")
            assert equal
            replays.append(dict(id=case["id"], identical=True, summarySha256=sha(replay / "summary.json")))
    write(data / "study-plan.json", plan)
    write(data / "runs.json", records)
    write(data / "verification.json", dict(formulaAndRawDeliveryChecked=len(records), replays=replays,
          technicalFailures=sum(r["summary"]["status"] != "completed" for r in records), method="recompute ID/receiver pairs, payload hashes, latency ranks, drain, forwarding counts and source counter formulas"))
    aggregates = []
    for family in ("ideal", "mosquitto"):
        for mode in MODES:
            for qos in range(3):
                group = [r["summary"] for r in records if (r["family"], r["mode"], r["qos"]) == (family, mode, qos)]
                good = [s for s in group if s["status"] == "completed"]
                aggregates.append(dict(family=family, mode=mode, qos=qos, successful=len(good), failed=len(group)-len(good),
                    **{key: estimate([s[key] for s in good]) for key in ("sourceCompletedRps", "uniqueGoodput", "deliveryLoss", "receiverP99Ms", "publishP99Ms", "forwardAttempts", "suppressedDuplicates")}))
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
        for ax, metric in zip(axes.flat, ("sourceCompletedRps", "deliveryLoss", "receiverP99Ms", "forwardAttempts")):
            for qos in range(3):
                rows = [next(a for a in aggregates if (a["family"], a["mode"], a["qos"]) == (family, mode, qos)) for mode in MODES]
                means = [a[metric]["mean"] for a in rows]
                errors = [a[metric]["ci95"] or 0 for a in rows]
                if all(x is not None for x in means):
                    ax.errorbar(range(3), means, yerr=errors, marker="o", capsize=4, label=f"QoS {qos}")
            ax.set_xticks(range(3), MODES)
            ax.set_title(metric)
            ax.grid(alpha=.2)
            ax.legend()
        fig.suptitle(f"{family}: model means and conditional t-CI95 across 3 seeds")
        fig.savefig(data / f"{family}-comparison.svg", metadata={"Date": None})
        plt.close(fig)
        for mode in MODES:
            directory = work / "runs" / f"{family}-{mode}-q1-s42"
            for kind in ("physical", "logical"):
                for extension in ("svg", "mmd"):
                    (data / f"{family}-{mode}-{kind}.{extension}").write_bytes((directory / f"topology-{kind}.{extension}").read_bytes())
    write(data / "aggregates.json", aggregates)
    with (data / "runs.csv").open("w", encoding="utf-8", newline="") as stream:
        keys = ["id", "family", "mode", "qos", "seed", "status", "sourceCompletedRps", "uniqueGoodput", "deliveryLoss", "receiverP99Ms", "forwardAttempts", "suppressedDuplicates"]
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        for r in records:
            writer.writerow({key: r.get(key, r["summary"].get(key)) for key in keys})
    def number(value):
        return "н/п" if value is None else f"{value:.4f}"
    def interval(value):
        return number(value["mean"]) + (" ± " + number(value["ci95"]) if value["ci95"] is not None else "")
    lines = ["# Статическая федерация: сравнение с MqttRelay и одиночным брокером", "",
             f"Выполнено {len(records)}/54 основных симуляций; технических отказов {sum(r['summary']['status']!='completed' for r in records)}. "
             f"Повторены {len(replays)}/6 точек; scientific summaries совпадают побайтно. Новых натурных измерений нет.", "",
             "## Условия и исполнители", "",
             "Federation.App строит сеть и запускает фазы; ядро MqttY исполняет MQTT и транспорт. StaticFederator выполняет пересылку по соседям с подавлением повторов. "
             "В режиме relay работает существующий MqttRelay с деревом от каждого источника. Python-сценарий проверяет журналы и строит этот отчёт. MqttBenchmark не запускается.", "",
             "Идеальная модель: три площадки, 12 publishers, три subscribers. Mosquitto: четыре площадки, 64 publishers, четыре subscribers. "
             "Для каждого семейства расположение клиентов и физическое кольцо сохраняются в трёх режимах; центральный брокер single находится на площадке 0. "
             "Каждый subscriber получает все исходные сообщения. В двух семействах разный fanout, поэтому их goodput нельзя непосредственно ранжировать.", "",
             "Общая нагрузка 1000 сообщений/с, payload 16 байт, QoS 0/1/2, seeds 42/43/44. Прогрев 0,01 с, нагрузка 0,2 с, drain до 1 модельной секунды, "
             "tick 0,1 мс. Прогрев полностью завершается до начала измерения. Фиксированная сеть: 125000000 байт/с, окно 100 ticks, очередь 100000, задержка 2 ticks на площадку. "
             "Очередь федератора 1024 на направление, кэш 100000 ID на узел без вытеснения, предел переходов равен числу площадок.", "",
             "## Полученные результаты", "",
             "Среднее ± t-CI95 по трём seeds, t=4,302652729911275. Это условный разброс модели при фиксированных входах, не интервал натурной точности.", "",
             "| Модель | Режим | QoS | Завершения исходных/с | Goodput доставок/с | Недоставка, доля | p99 получения, мс | Пересылки | Подавлено повторов | ✅/план/❌ |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for a in aggregates:
        lines.append(f"| {a['family']} | {a['mode']} | {a['qos']} | " + " | ".join(interval(a[k]) for k in ("sourceCompletedRps","uniqueGoodput","deliveryLoss","receiverP99Ms","forwardAttempts","suppressedDuplicates")) + f" | ✅{a['successful']}/3/❌{a['failed']} |")
    lines += ["", "### Федерация относительно Relay", "",
              "Отношения средних, без проверки статистической значимости; это диагностическое сравнение фиксированного опыта. "
              "Для потерь приведена абсолютная разность долей: положительное значение означает большую недоставку федерации.", "",
              "| Модель | QoS | p99 федерации / p99 Relay | Пересылки федерации / Relay | Loss федерации − loss Relay |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for family in ("ideal", "mosquitto"):
        for qos in range(3):
            def group(mode):
                return next(a for a in aggregates if (a["family"], a["mode"], a["qos"]) == (family, mode, qos))
            f, r = group("federation"), group("relay")
            def ratio(key):
                x, y = f[key]["mean"], r[key]["mean"]
                return x / y if x is not None and y is not None and y != 0 else None
            loss_f, loss_r = f["deliveryLoss"]["mean"], r["deliveryLoss"]["mean"]
            difference = loss_f - loss_r if loss_f is not None and loss_r is not None else None
            lines.append(f"| {family} | {qos} | {number(ratio('receiverP99Ms'))} | {number(ratio('forwardAttempts'))} | {number(difference)} |")
    lines += ["", "## Графики", "", "![Идеальная модель](data/federation-study-v1/ideal-comparison.svg)", "",
              "![Профиль Mosquitto](data/federation-study-v1/mosquitto-comparison.svg)", "", "## Топологии", "",
              "Физическая сеть и расположение исходных клиентов сверены с GraphML приложением. Логические стрелки обозначают MQTT-соединения, а не обход физических сетевых участков. "
              "Разные источники Relay имеют разные деревья; в федерации связи допускают циклы, а идентификаторы препятствуют повторной обработке.", ""]
    for family in ("ideal", "mosquitto"):
        lines += [f"### {family}", "", f"![Физическая сеть](data/federation-study-v1/{family}-federation-physical.svg)", ""]
        for mode in MODES:
            lines += [f"![Логическая топология {mode}](data/federation-study-v1/{family}-{mode}-logical.svg)", ""]
    lines += ["## Что означают числа", "",
              "Source completed RPS учитывает только завершения исходных publishers; служебные публикации не добавляются к нему. Goodput — уникальные пары ID–subscriber за секунду нагрузки. "
              "Ожидаемая доставка = число API-попыток исходного Publish × число subscribers; потери включают немедленные исходные отказы. Поздние получения в drain включены в свою когорту и показаны отдельно в JSON. "
              "p99 получения строится по первым получениям каждой пары; исходные ACK приведены отдельно в данных, для QoS 0 неприменимы. "
              "Исходные завершения могут быть успешными при недоставке subscribers. Байты пересылки в JSON — UTF-8 темы плюс payload, без заголовков; симулятор передаёт полные MQTT-пакеты.", "",
              "## Вывод и ограничения", "",
              "Статическая федерация обеспечивает модельную межброкерную пересылку с конечной обработкой циклов; действительное покрытие и потери приведены в таблице. "
              "Преимущество не предполагается заранее: сравниваются фактическая доставка, задержка и дополнительный трафик. В идеальной модели при малой нагрузке одинаковая доставка не означает равную стоимость архитектур. "
              "У федерации есть избыточные пути и накладные расходы повторных межброкерных получений; Relay использует настроенные деревья без циклов. "
              "Одиночный брокер исключает межброкерную пересылку, но меняет место обслуживания клиентов.", "",
              "Профиль Mosquitto неизменён: N=64 для single, N=16 для распределённых брокеров. Он содержит исходную высокую условную недоставку для QoS 1/2. "
              "Дополнительные федераторы, fanout, новые темы и новая интенсивность не калибровались; результат не доказывает ту же недоставку у настоящего Mosquitto или точность настоящей федерации. "
              "Нет самоорганизации, выбора ядра, beacons, No Local и перестроения после отказа. CPU/RSS, EST и отказоустойчивость не подтверждаются. "
              "Короткое окно является приёмочной проверкой механизма, не стационарным исследованием производительности. Для натурной проверки нужны отдельные измерения всех трёх архитектур с тем же расположением клиентов и независимым контролем.", "",
              "## Воспроизводимость", "",
              f"MqttY commit: `{plan['sources']['mqttYCommit']}`. Профиль SHA-256: `{plan['sources']['profileSha256']}`. "
              "Сохранены snapshots профиля, runtime, конфиги, команды, первичные получения и пересылки. Проверка независимо пересчитывает пары получателей, целостность payload, гистограммы и квантили, знаменатели, drain и формулы RPS по исходным счётчикам. "
              "Счётчики исходных завершений отдельно проверены тестами инструментации; они не выводятся из одного исчезновения pending.", "",
              "Повтор всей программы: собрать приложение в Release, выполнить `python tools/federation/study.py run --work-dir <новый-каталог>`, "
              "затем `python tools/federation/study.py report --work-dir <тот-же-каталог>`. Отдельный опыт повторяется командой приложения `run --config <run>/run-config.json --output <новый-каталог>`.", "",
              "[CSV](data/federation-study-v1/runs.csv), [scientific summaries](data/federation-study-v1/runs.json), [агрегаты](data/federation-study-v1/aggregates.json), "
              "[проверка](data/federation-study-v1/verification.json), [точная программа](data/federation-study-v1/study-plan.json). "
              "Полные архивы хранятся отдельно от Git; квитанция внешнего хранения публикуется после проверки архива.", ""]
    (ROOT / "reports/Federation-study.md").write_text("\n".join(lines),encoding="utf-8",newline="\n")
    files = [*data.iterdir(), ROOT / "reports/Federation-study.md"]
    write(data / "publication-sha256.json", {p.relative_to(ROOT / "reports").as_posix(): sha(p) for p in sorted(files) if p.name != "publication-sha256.json"})
    print(f"Verified and published {len(records)} runs and {len(replays)} replays", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "report", "verify"))
    parser.add_argument("--work-dir",type=Path,required=True)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    if args.command == "run":
        run(work)
    elif args.command == "report":
        publish(work)
    else:
        for case in read(work / "study-plan.json")["cases"]:
            verify_run(work / "runs" / case["id"])
        print("Verified all saved main runs")
