"""Prepare a local evidence ledger without starting or polling experiments."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "reports/analysis/Claims-and-evidence.md"
MANIFEST = ROOT / "reports/audits/dissertation/claims-and-evidence-v1/manifest.json"
SOURCES = {
    "native": "reports/status/linux-native-audit-summary.json",
    "accuracy": "reports/audits/dissertation/accuracy-analysis-v1/analysis.json",
    "sensitivity": "reports/audits/dissertation/sensitivity-final-v1/analysis/analysis.json",
    "pilots": "reports/audits/dissertation/network-pilot-reuse-v1/registry.json",
    "decision": "reports/audits/dissertation/network-full-3h-v1/pilot-technical-decision.json",
    "web": "reports/audits/web-linux-v1/user-observed-10m-verification.json",
}


def main():
    raw = {key: (ROOT / path).read_bytes() for key, path in SOURCES.items()}
    data = {key: json.loads(value.decode("utf-8-sig")) for key, value in raw.items()}
    accuracy = data["accuracy"]
    runs = len({(row["broker"], row["key"]) for row in accuracy["criteria"]})
    failed = len(accuracy["failures"])
    matrix = data["sensitivity"]["coverage"]["matrix"]
    replay = data["sensitivity"]["coverage"]["replay"]
    web = data["web"]["metrics"]
    assert runs == 288 and failed == 85
    assert matrix["completed"] == matrix["planned"] == 216
    assert data["pilots"]["selectedCount"] == 30
    assert data["decision"]["decision"] == "accept"
    assert data["decision"]["pilotRegistrySha256"] == hashlib.sha256(raw["pilots"]).hexdigest()
    assert data["web"]["frozenRawVerification"]["passed"]
    assert web["uniqueDeliveries"] <= web["expectedDeliveries"]

    def link(key):
        return f"[Источник](../../{SOURCES[key]})"

    text = f"""# Какие выводы подтверждены экспериментами

Это техническая таблица для подготовки двух статей и диссертации. Она отделяет проверку расчётов, точность модели и повторяемость. Автор утверждает научные положения и пишет текст. Завершённый процесс сам по себе не подтверждает точность модели.

| Вопрос | Полученный результат | Что можно утверждать | Ограничение |
| --- | --- | --- | --- |
| Правильно ли рассчитаны натурные метрики? | Проверены 592 архива; пересчитаны 576 выбранных измерений, расхождений 0. {link('native')} | Сохранённые события согласуются с опубликованными счётчиками и проверенными метриками | Пересчёт не исключает систематическую ошибку исходного измерения; SHA подтверждает целостность, а не истинность |
| Насколько точны двойники? | Все применимые критерии прошли {runs-failed}/{runs}; не прошли {failed}. {link('accuracy')} | Точность подтверждается только в прошедших точках и по заданным критериям | История использования контроля не позволяет объявить всю историческую серию слепой независимой проверкой |
| Устойчив ли результат к настройкам времени? | Завершены {matrix['completed']} основных опытов: {matrix['completed']-matrix['scientificFailed']} без нарушений критериев, {matrix['scientificFailed']} с нарушениями; технических ошибок {matrix['technicalErrors']}. {link('sensitivity')} | Есть измеренная чувствительность к tick и окну нагрузки | Нельзя распространять вывод на все нагрузки; удвоение окна не во всех случаях сохраняет p99 в пределах допуска |
| Воспроизводятся ли выбранные результаты? | {replay['completed']} точных повторов; проверка совпадений описана в итоговом анализе чувствительности. {link('sensitivity')} | Проверенные входы и runtime воспроизводят выбранные научные результаты | Шесть точек не доказывают переносимость натурных чисел на другой CPU и память; научные провалы также могут воспроизводиться |
| Готова ли программа сетевого сравнения? | Проверены 30 сохранённых пилотных точек и оформлено отдельное решение. {link('pilots')}, {link('decision')} | Новая программа технически допущена: 162 опыта и 6 повторов; четыре независимых процесса, общий предел 3 часа | Пилоты составные, с разными версиями runtime. Это не новая однородная серия. Основная матрица ещё не принята по результатам |
| Работает ли запуск через Web? | Завершены {web['sourceCompleted']}/{web['scheduledPublishes']} исходных публикаций; получено {web['uniqueDeliveries']}/{web['expectedDeliveries']} ожидаемых пар ({100*web['uniqueDeliveries']/web['expectedDeliveries']:.4f}%). {link('web')} | Проверены реальный runner, экспорт, HTTP-результат и пересчёт raw | Это проверка механизма. queueDrops={web['queueDrops']}; число отброшенных пересылок не равно числу недоставленных пар |

## Точность по каждому брокеру

| Брокер | Все критерии пройдены | Есть нарушения |
| --- | ---: | ---: |
"""
    for broker in accuracy["brokers"]:
        count = len({row["key"] for row in accuracy["criteria"] if row["broker"] == broker["broker"]})
        failures = broker["holdoutFailed"]
        text += f"| {broker['broker']} | {count-failures}/{count} | {failures}/{count} |\n"
    text += """
Критерии Fidelity: относительная ошибка RPS ≤15%, p99 ≤20%, абсолютная разница failure/loss ≤0,05. Неприменимые критерии не считаются успешными измерениями: например, ACK latency для QoS 0 отсутствует. p99 полученных сообщений не описывает задержку потерянных сообщений.

## Что ещё нельзя считать доказанным

- Натурная точность сети MqttRelay и статической федерации: нет сопоставимого натурного сетевого эталона. Пока сравниваются модельные механизмы при заданных ресурсах.
- Превосходство одного сетевого режима: сначала нужно проверить полное фактическое покрытие основной матрицы, повторы и предупреждения. Исчерпание трёхчасового бюджета означает неполное покрытие.
- Универсальная точность двойника на другом оборудовании: модель повторяет зафиксированный профиль; для нового физического стенда нужна новая калибровка и контроль.
- Вероятность достоверности через EST: это взвешенный сравнительный балл, а не вероятность. Его значение зависит от весов, нагрузочной точки, нормировки и состава брокеров. CPU/RSS не моделируются двойником.
- Отказоустойчивость и самоорганизация федерации: первая версия статическая; такие свойства требуют отдельной реализации и программы отказов.

## Кто создаёт доказательства

```mermaid
flowchart LR
    B[Реальный брокер] --> M[MqttBenchmark: нагрузка и первичные события]
    M --> V[Пересчёт событий и проверка метрик]
    M --> P[MqttY: калибровка профиля]
    P --> F[MqttY: BrokerFidelity]
    P --> N[MqttY: MqttRelay и федерация]
    F --> C[Сравнение с контрольными наблюдениями]
    V --> C
    N --> R[Проверка доставок, пересылок и повторов]
    C --> A[Автор: границы выводов и научный текст]
    R --> A
```

Web управляет модельными заданиями и показывает их результаты. Он не заменяет MqttBenchmark и сам не проводит натурную калибровку. Терминальный монитор читает прогресс внешней кампании; Web-очередь и внешняя кампания имеют разные реестры заданий.

## Следующие независимые работы

1. Подготовить шаблон сравнения сетевых режимов и формулы до получения итогов, без объявления победителя.
2. Проверить согласованность терминов и знаменателей в материалах для автора; замечания к защищённым исходникам диссертации передать отдельно.
3. Подготовить пакет другому сотруднику для независимого пересчёта; агент не заменяет человеческую проверку.
4. После завершения Linux один раз забрать компактные результаты, проверить покрытие и повторы, затем заполнить сравнение фактическими числами.

Документ пересоздаётся командой `python tools/dissertation/prepare_claims_matrix.py`. Хэши использованных локальных источников и самого документа сохраняются в `reports/audits/dissertation/claims-and-evidence-v1/manifest.json`. Скрипт не обращается к Linux и не меняет экспериментальные входы.
"""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(text.encode("utf-8"))
    manifest = {
        "schemaVersion": 1,
        "scientificApproval": False,
        "sources": {path: hashlib.sha256(raw[key]).hexdigest() for key, path in SOURCES.items()},
        "generatorSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "document": OUTPUT.relative_to(ROOT).as_posix(),
        "documentSha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print("Evidence ledger and source manifest prepared; no remote operations.")


if __name__ == "__main__":
    main()
