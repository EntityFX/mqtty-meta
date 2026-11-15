# Презентация: MqttY / MqttBenchmark — результаты работы

*Цель презентации: краткий обзор проделанной работы по моделированию, калибровке и ускорению симулятора MQTT-сетей MqttY и стенда измерений MqttBenchmark.*

---

## Слайд 1. Титульный

**MqttY + MqttBenchmark: моделирование, калибровка и ускорение симулятора MQTT-сетей**

- Проект: `mqtty-meta`
- Компоненты: MqttY (симулятор), MqttBenchmark (измерение реальных брокеров)
- Автор: EntityFX
- Дата: ноябрь 2026

![Титульная заставка проекта](figures/title.svg)

---

## Введение

### Слайд 2. Что такое MqttY

MqttY — дискретно-событийный симулятор MQTT-сетей в модельном времени.

- Клиенты, брокеры, сетевые задержки, очереди и протокольные обмены моделируются внутри одного процесса
- Не запускает реальные брокеры
- Поддерживает профили реальных брокеров (полученные из MqttBenchmark)
- Три движка: LoadBalancing, Relay, Federation
- Стек: C#, target framework net6.0, сборка .NET 8 SDK

![Архитектура проекта MqttY](mqtty/doc/architecture.md#диаграмма-проекта)

---

### Слайд 3. Что такое MqttBenchmark

MqttBenchmark измеряет реальные MQTT-брокеры и сохраняет наблюдения для калибровки MqttY.

- Четыре брокера: Aedes, Mosquitto, ActiveMQ Classic, EMQX
- Native-процессы без Docker (основной маршрут)
- Кампании v3: 288 успешных ключей, 96 агрегированных точек
- Поддержка Linux (Ubuntu/ALT), Windows, WSL2, SSH-стенды

![Схема стенда измерений](MqttBenchmark/docs/architecture.md#диаграмма-стенда)

---

### Слайд 4. Задачи работы

1. Создать двойники (профили) четырёх реальных MQTT-брокеров для симуляции
2. Доработать стенд и симулятор MqttY (переписывание S0–S5, оптимизация)
3. Исследовать топологии: LoadBalancing, Federation (star/ring/line), Relay
4. Ускорить движок симуляции (сравнение с летним периодом)
5. Провести моделирование каждого брокера и MqttRelay на двойниках

![Дорожная карта работ](figures/roadmap.svg)

---

### Слайд 5. Стек и окружение

- .NET SDK: 6.0.136, 8.0.121, 9.0.111, 10.0.200
- Target framework: net6.0
- Хост: Intel Xeon E5-2680 v3, 12 физических / 24 логических ядра, 30 МБ L3
- Для гейтов: workers=12 (физические ядра); SMT-переподписка на 24 потоках регрессивна

![Стек технологий](figures/stack.svg)

---

### Слайд 6. Структура репозитория mqtty-meta

```
mqtty-meta/
├── MqttBenchmark/   # измерение реальных брокеров
├── mqtty/           # симулятор MqttY
└── diss/            # диссертация (не трогалась)
```

---

## Двойники брокеров

### Слайд 7. Что такое двойник брокера

Двойник (брокер-профиль) — JSON-файл `broker-calibration.v3.json`, содержащий натурные наблюдения за реальным брокером:

- Capacity (пропускная способность) по числам клиентов и QoS
- Publish failure rate
- Delivery loss rate
- Наблюдаемая и обрабатывающая задержка (latency knots)
- RTT baseline

Профиль подключается к симуляции через `brokerProfilePath` и используется вместо идеального брокера.

![Схема двойника брокера](figures/broker-twin.svg)

---

### Слайд 8. Четыре брокера для калибровки

| Брокер | Версия | Порт | Примечание |
|--------|--------|------|------------|
| Aedes | 1.1.2 | 1883 | Node.js |
| Mosquitto | 2.x | 1883/2883 | C |
| ActiveMQ Classic | 6.3.2 | 3883 | Java 17, JDK |
| EMQX | 6.3.0 | 4883 | Erlang, Linux/WSL2 |

Все четыре брокера проверены на ALT Linux (native, без Docker).

![Четыре брокера](figures/four-brokers.svg)

---

### Слайд 9. Параметры профиля (QoS 1, payload 16 байт)

| Брокер | capacityRps (1 клиент) | 16 клиентов | 64 клиента | publishFailureRate 1/16/64 |
|--------|------------------------|-------------|------------|---------------------------|
| Mosquitto | 3916,4 | 7256,4 | 35 941,3 | 0 / 0 / 0,71 |
| ActiveMQ | 3511,0 | 7083,5 | 42 301,3 | 0 / 0 / 0 |
| Aedes | 3491,2 | 5736,2 | 6866,3 | 0 / 0 / 0 |
| EMQX | 3157,6 | 36 460,6 | 51 032,5 | 0 / 0,97 / 0,99 |

Источник: встроенный профиль `broker-benchmark.v2.json` (schemaVersion 2).

![График пропускной способности брокеров](figures/broker-capacity.svg)

---

### Слайд 10. Формулы калибровки v3

- `Fconditional = (Ftarget - Frate) / (1 - Frate)`
- Точки с `Frate > Ftarget` отклоняются
- Вычитается 1 RTT из задержки QoS 1, 2 RTT из QoS 2 (обрезаются в ноль)
- QoS 0 остаётся не применимым (latency = null)
- Schema v3 хранит AttemptedRps, CompletedRps, RTT baseline, provenance

![Схема калибровки](figures/calibration-flow.svg)

---

### Слайд 11. Схема данных профиля

```
broker-calibration.v3.json:
├── AttemptedRps
├── CompletedRps (target)
├── Conditional publish-failure rate
├── Conditional delivery-loss rate
├── Observed / processing latency
├── RTT baseline
└── Provenance (SHA-256 байтов профиля)
```

Профиль читается в неизменяемый snapshot по SHA-256; хэш проверяется при исполнении.

![Схема данных профиля](figures/profile-schema.svg)

---

### Слайд 12. Калибровка MqttY: поток данных

MqttBenchmark (измерения) → broker-observations.v3.json → calibrate → broker-calibration.v3.json → MqttY experiment (с профилем)

![Поток калибровки](figures/calibration-pipeline.svg)

---

## Доработки стенда MqttSimulator

### Слайд 13. Переписывание S0–S5

Общий объём работ: **584 человеко-часа** (план закрыт на 100 %).

| Направление | План | Выполнено | Остаток |
|-------------|------|-----------|---------|
| Переписывание S0–S5 | 384 ч | 384 ч | 0 ч |
| Производительность M0–M8 | 200 ч | 200 ч | 0 ч |
| **Итого** | **584 ч** | **584 ч** | **0 ч** |

![Прогресс плана](progress.svg)

---

### Слайд 14. Что было переписано (S0–S5)

| Этап | Что сделано |
|------|-------------|
| S0 | golden packet trace, детерминизм seq/par |
| S1 | швы ISimulationClock, TopologySnapshot, TickContext, IAdmissionPolicy |
| S2 | data-oriented слоты вместо wrapper/pool |
| S3f–S3i | раздельные inbound/outbound namespace, кодек Span, кэш topic/filter, admission/metrics вне протокола |
| S4d–S4g | единая result schema, RuntimeOptions, единый CLI, MetricsCollector |
| S5d–S5g | Web handlers, versioned persistence, безопасность, DesignDocument |

![Статус S0–S5](figures/s0-s5-status.svg)

---

### Слайд 15. Data-oriented транспортные слоты (S2)

Замена wrapper-классов и object pool на `NodePacketSlot` / `NetworkPacketSlot`:
- path-массив + NextHop без копирования на каждый hop
- готовая очередь обрабатывается по снимку длины
- неготовые пакеты уходят в deferred-списки

![Устройство транспортных слотов](figures/packet-slot.svg)

---

### Слайд 16. CounterClock и точечные счётчики

- Один publish `(tick, step)` на тик вместо обхода дерева счётчиков
- Кэширование массивов Counters
- Latency-гистограмма: dense tick-массив, запись O(1), снапшот без копирования

![CounterClock](figures/counter-clock.svg)

---

### Слайд 17. TickWorkerPool и двухфазный refresh

Постоянный пул воркеров вместо Task/Parallel.For на каждый тик:
- Фаза 1: все сети → fence
- Фаза 2: все узлы → fence
- Spin/monitor автовыбор барьера
- Динамический захват работы (nodeChunk=1)

![Двухфазный refresh](figures/tick-worker-pool.svg)

---

## Исследование топологий

### Слайд 18. Экспериментальный дизайн

- 63 основных наблюдения: 19 LB, 28 Federation (star/ring/line), 16 Relay
- 13 крупных конфигураций
- 18 robustness-прогонов: QoS 0/1/2 × seed 1/7/42
- Два хоста: Xeon E5-2680 v3 (12C/24T) и Celeron N3350 (2C/2T)
- measure=1–3 с, warmup=0.01 с, drain=10 с, seed 42, payload 16 байт

![Экспериментальный дизайн](figures/experiment-design.svg)

---

### Слайд 19. Переменные и обозначения

- $M = RT$ — запланированные публикации
- $D$ — уникальные доставки
- $X$ — сетевые пакеты
- $B$ — брокеры, $P$ — публикаторы, $C$ — клиенты, $N$ — сети, $L$ — длина пути

![Таблица переменных](figures/variables.svg)

---

### Слайд 20. Формулы доставок

| Движок | Доставки |
|--------|----------|
| LoadBalancing | $D_{LB} = M$ |
| Relay | $D_{Relay} = MB$ |
| Federation | $D_{Fed} = MB$ |

В LB каждый брокер не увеличивает число доставок; в Relay/Federation каждый брокер добавляет $M$ доставок.

![Формулы доставок](figures/deliveries-formula.svg)

---

### Слайд 21. Формулы трафика (основные)

| Топология | Трафик $X/M$ | Рост |
|-----------|--------------|------|
| LB | $\approx 4(L+1)$ | $O(MN)$ |
| Relay | $\approx 12.29B - 4.46$ | $O(MN)$ |
| Federation star | $\approx 8.08B^2 - 11.98B + 11.84$ | $O(MN^2)$ |
| Federation ring | $\approx 10.70B + 2.78$ | $O(MN)$ |
| Federation line | $\approx 10.12B - 1.96$ | $O(MN)$ |

![Формулы трафика](figures/traffic-formulas.svg)

---

### Слайд 22. Holdout-верификация

| Модель | Отложенная точка | Ошибка |
|--------|------------------|--------|
| LB | $L=4$ | 0.08 % |
| Relay | $B=16$ | 0.08 % |
| Federation star | $B=16$ | 0.09 % |
| Federation line | $B=16$ | <0.01 % |
| Federation ring | $B=16$ | 9.1 % |

![График holdout-ошибки](figures/holdout-error.svg)

---

### Слайд 23. Аппаратная инвариантность

На Celeron N3350 и Xeon E5-2680 v3 одинаковые конфигурации дают одинаковые $M$, $D$, $X$ (отклонение 0–0.1 %). Различается только скорость (speedup).

![Сравнение хостов](figures/hardware-invariance.svg)

---

## Топологии с Load Balancer, Federation, Relay

### Слайд 24. Load Balancing (LB)

- Клиентская LAN + $B$ backend-цепочек длины $L$
- $N = 1 + BL$, $C = P + B$
- Каждая публикация доставляется одному получателю ($D = M$)
- При QoS 1 четыре пакета проходят $L+1$ сетей

![Топология LB](figures/topology-lb.svg)

---

### Слайд 25. Federation star

- $B$ сайтов подключены к одному транзитному узлу
- Логический граф пересылок — полный граф
- $N = B + 1$, $C = P + B^2 + 2B$
- Квадратичный трафик $O(B^2)$

![Топология Federation star](figures/topology-star.svg)

---

### Слайд 26. Federation ring

- $B$ сайтов соединены в кольцо
- $N = B$, $C = P + 5B$
- Каждое ребро используется не более одного раза
- Линейный трафик $O(B)$

![Топология Federation ring](figures/topology-ring.svg)

---

### Слайд 27. Federation line

- $B$ сайтов соединены в цепочку
- $N = B$, $C = P + 5B - 2$
- Минимальный трафик, но длинные пути
- Линейный трафик $O(B)$, точно описывается моделью

![Топология Federation line](figures/topology-line.svg)

---

### Слайд 28. Relay (star без flood)

- Star-транспорт, но сайт-origin отправляет один раз в каждый удалённый сайт
- Импортные сообщения не экспортируются дальше
- $N = B + 1$, $C = P + 3B^2$
- Линейный трафик $O(B)$

![Топология Relay](figures/topology-relay.svg)

---

### Слайд 29. Сравнение топологий при $B=16$

| Топология | $X$, млн пакетов | wall, с | speedup |
|-----------|------------------|---------|---------|
| Federation star | 3.78 | 4.23 | 4.45× |
| Federation ring | 0.346 | 1.92 | 3.94× |
| Federation line | 0.322 | 3.18 | 2.59× |
| Relay | 0.385 | 2.02 | 3.91× |

![Сравнение топологий](figures/topology-comparison.svg)

---

### Слайд 30. Выбор топологии

- **Star** — минимальная задержка, квадратичный трафик, маленькие $B$
- **Ring** — компромисс: линейный трафик, устойчивость к отказу ребра
- **Line** — минимальный трафик, длинные пути, нет резервных путей
- **Relay** — доставка на все сайты без flood, линейный трафик
- **LB** — балансировка клиентов по бэкендам

![Матрица выбора](figures/topology-selection.svg)

---

## Результаты ускорения движка с летнего периода

### Слайд 31. Сравнение с августом 2026 (commit 9655fe8)

| Метрика | Август (9655fe8) | Сейчас (develop) | Ускорение |
|---------|------------------|------------------|-----------|
| Общий wall `original` | 20.7 с | 1.76 с | **11.8×** |
| seq-прогон | 4.96 с | 1.22 с | 4.1× |
| par-прогон | 3.63 с | 0.73 с | 5.0× |
| par+counters | 12.02 с | 0.79 с | 15.2× |

![Сравнение с августом](figures/august-vs-november.svg)

---

### Слайд 32. Ускорение на большой сети

| Прогон | Август | Сейчас | Ускорение | µs/шаг август | µs/шаг сейчас | На шаг |
|--------|--------|--------|-----------|---------------|---------------|--------|
| seq | 2.76 с | 2.11 с | 1.31× | 7.48 | 1.82 | 4.1× |
| par | 1.98 с | 0.43 с | 4.60× | 5.37 | 0.371 | 14.5× |
| par+counters | 8.54 с | 0.41 с | 20.8× | 23.1 | 0.353 | 65× |

Внутренний par/seq вырос с 1.39× до **4.88×**.

![Большая сеть](figures/large-network-speedup.svg)

---

### Слайд 33. Матрица 8 конфигураций

| Режим | Время |
|-------|-------|
| seq (1 шард × 12 воркеров) | 33.9–34.1 с |
| par (4 шарда × 3 воркера, LPT) | 19.6–19.8 с |
| **Ускорение матрицы** | **1.73×** |

Результаты битово идентичны, `core=match`.

![Матрица конфигураций](figures/matrix-speedup.svg)

---

### Слайд 34. Reference-бенчмарк (ноябрь 2026)

| Конфиг | wall speedup | refresh speedup | core |
|--------|--------------|-----------------|------|
| LB L (64 брокера, 20k rps) | 5.17× | 5.87× | match |
| LB reference | 2.98× | 3.36× | match |
| Relay (2k rps) | 7.62× | 8.89× | match |
| Federation L | 4.1–4.2× | 5.0–5.1× | match / diag differ |

![Reference-бенчмарк](figures/reference-benchmark.svg)

---

### Слайд 35. Ключевые оптимизации

1. Диссеминационный барьер (вместо counter + Thread.Yield)
2. Cache-line padding счётчиков TickWorkerPool
3. Owned ranges для распределения работы узлов
4. Мемоизация GetEnumDescription (рефлексия)
5. Кэш Enum.GetValues
6. Parallel Pump для Federation (RunSingle)
7. SHA-256 payload один раз на публикацию
8. Array.Sort вместо LINQ для transmission evidence
9. Early-out пустых очередей узлов

![Оптимизации](figures/optimizations.svg)

---

### Слайд 36. Гейты производительности (G1–G6)

| Гейт | Критерий | Статус |
|------|----------|--------|
| G1 | refresh ≥ 4× | 🟢 (кроме LB ref) |
| G2 | wall ≥ 4× | 🟡 (Fed на границе, LB ref 🔴) |
| G3 | efficiency ≥ 50 % | 🔴 (42–44 %) |
| G4 | seq == par | 🟡 (Fed diag differ) |
| G5 | масштабный ≥ 4× | 🟢 |
| G6 | тесты | 🟢 (436/436, 62/62) |

![Гейты](figures/gates.svg)

---

## Моделирование каждого брокера: по слайду на брокера

### Слайд 37. Моделирование Aedes

Aedes 1.1.2, Node.js.
- capacityRps: 3491,2 (1 клиент), 5736,2 (16), 6866,3 (64)
- publishFailureRate: 0 / 0 / 0
- Почти плоская кривая capacity от числа клиентов
- p99 latency: 1.00 / 1.80 / 1.80 мс (QoS 1)

![Aedes профиль](figures/broker-aedes.svg)

---

### Слайд 38. Моделирование Mosquitto

Mosquitto 2.x.
- capacityRps: 3916,4 (1), 7256,4 (16), 35 941,3 (64)
- publishFailureRate: 0 / 0 / 0,71
- Резкий рост capacity к 64 клиентам
- p99 latency: 0.90 / 1.70 / 1.70 мс (QoS 1)

![Mosquitto профиль](figures/broker-mosquitto.svg)

---

### Слайд 39. Моделирование ActiveMQ Classic

ActiveMQ Classic 6.3.2, Java 17.
- capacityRps: 3511,0 (1), 7083,5 (16), 42 301,3 (64)
- publishFailureRate: 0 / 0 / 0
- Резкий рост capacity к 64 клиентам
- p99 latency: 0.90 / 1.70 / 1.70 мс (QoS 1)

![ActiveMQ профиль](figures/broker-activemq.svg)

---

### Слайд 40. Моделирование EMQX

EMQX 6.3.0, Erlang.
- capacityRps: 3157,6 (1), 36 460,6 (16), 51 032,5 (64)
- publishFailureRate: 0 / 0,97 / 0,99
- Резкий рост capacity, но почти полный отказ при 16+ клиентах
- p99 latency: 0.90 / 1.70 / 1.70 мс (QoS 1)

![EMQX профиль](figures/broker-emqx.svg)

---

### Слайд 41. Сравнение всех четырёх брокеров

| Брокер | Кривая capacity | Failure rate при 64 клиентах | Особенность |
|--------|-----------------|------------------------------|-------------|
| Mosquitto | Резкий рост | 0,71 | Стабильность до 64 клиентов |
| ActiveMQ | Резкий рост | 0 | Высшая capacity при 64 |
| Aedes | Почти плоская | 0 | Предсказуемая производительность |
| EMQX | Резкий рост | 0,99 | Отказы при 16+ клиентах |

![Сравнение брокеров](figures/broker-comparison.svg)

---

## Моделирование MqttRelay на двойниках брокеров

### Слайд 42. MqttRelay.App с профилями брокеров

- MqttRelay использует существующие профили брокеров из калибровки
- BrokerFidelity: N publishers + 1 subscriber
- Профиль загружается через `brokerProfilePath`, SHA-256 проверяется при исполнении
- Режимы: BrokerFidelity | MqttRelay, Publishers | ConnectedClients

![MqttRelay архитектура](figures/mqttrelay-architecture.svg)

---

### Слайд 43. Результаты симуляции MqttRelay (QoS 1, 2000 rps, 30 000 тиков)

| Брокер | attempted | admitted | completed | RPS | delivered | p50/p95/p99, мс |
|--------|-----------|----------|-----------|-----|-----------|-----------------|
| Mosquitto | 4501 | 4501 | 4501 | 1500,3 | 4501 | 0,90 / 1,70 / 1,70 |
| ActiveMQ | 6002 | 3001 | 3001 | 1000,3 | 3001 | 0,90 / 1,70 / 1,70 |
| Aedes | 4502 | 4502 | 4502 | 1500,7 | 4502 | 1,00 / 1,80 / 1,80 |
| EMQX | 6002 | 3001 | 3001 | 1000,3 | 3001 | 0,90 / 1,70 / 1,70 |

![MqttRelay результаты](figures/mqttrelay-results.svg)

---

### Слайд 44. Наблюдения по MqttRelay

- Mosquitto и ActiveMQ: резкий рост capacity к 64 клиентам
- Aedes: почти плоская кривая capacity
- EMQX: резкий рост capacity, но почти полный отказ публикаций при 16+ клиентах
- Латентность модели (p99 1,7–1,8 мс) определяется тиком 0,1 мс и профилем обработки
- Отказы rateRejected — следствие ограничения бюджета токенов, а не сбоев брокера

![Наблюдения](figures/mqttrelay-observations.svg)

---

## Выводы

### Слайд 45. Итоги работы

1. Созданы профили (двойники) четырёх реальных MQTT-брокеров для калибровки MqttY
2. Стенд и симулятор доработаны: переписывание S0–S5 завершено (584 ч, 100 %)
3. Исследованы топологии: LB, Federation (star/ring/line), Relay с формулами трафика и доставок
4. Движок ускорен: с августа по ноябрь overall ускорение 11.8× на исходном сценарии, 4.60× в пар на большой сети
5. Проведено моделирование каждого брокера и MqttRelay на двойниках

![Итоги](figures/results-summary.svg)

---

### Слайд 46. Научные результаты

- Формулы трафика проверены на 63 основных, 13 крупных и 18 robustness-конфигурациях
- Holdout-ошибка 0.08–0.09 % для LB/Relay/star/line
- Аппаратная инвариантность: одинаковые $M$, $D$, $X$ на двух разных хостах
- Делivered: $D_{LB} = M$, $D_{Relay} = D_{Fed} = MB$

![Научные результаты](figures/scientific-results.svg)

---

### Слайд 47. Ограничения

- Payload только 16 байт
- Federation: $B \le 16$
- QoS 0/1/2 проверены на ключевых конфигурациях; коэффициенты QoS-специфичны
- Один получатель на backend в LB
- Benchmark speedup зависит от теплового/фонового шума (CI ±3–5 %)

![Ограничения](figures/limitations.svg)

---

### Слайд 48. Дальнейшие работы

- Снижение инфляции работы узлов (~1.8× в параллели)
- Уменьшение Federation drain (~45 % wall)
- Закрытие гейтов G1–G3 (efficiency, wall для LB reference)
- Многократная независимая верификация benchmark-чисел
- Интеграция гейтов в CI

![Дальнейшие работы](figures/future-work.svg)

---

### Слайд 49. Отчёты и документы

| Документ | Содержание |
|----------|------------|
| `doc/architecture.md` | Устройство, инварианты, статус S0–S5 |
| `doc/performance.md` | Методика замеров, результаты, гейты |
| `doc/project-reviews-and-skills.md` | Прогресс, критик, навыки, сводка топологий |
| `doc/engine-acceleration.md` | Было → стало, гипотезы ускорения |
| `doc/analysis/message-scaling/article.md` | Статья о масштабировании сообщений |
| `doc/analysis/message-scaling/super-article.md` | Единая статья: топологии, трафик, доставка |
| `MqttBenchmark/docs/metrics.md` | Метрики, контракт кампании v3, размещение |

![Документация](figures/documentation.svg)

---

### Слайд 50. Заключение

MqttY с двойниками брокеров позволяет проводить детерминированное моделирование MQTT-сетей без запуска реальных брокеров. Калибровка из MqttBenchmark обеспечивает связь с натурными измерениями. Оптимизации движка дают до 11.8× ускорения по сравнению с летней версией. Исследование топологий даёт формулы для планирования экспериментов.

![Заключение](figures/conclusion.svg)

---

## Приложение. Предполагаемые картинки

Все картинки являются заполнителями (placeholders). Фактические файлы следует сгенерировать из данных experiments:

- `figures/title.svg` — титульная заставка
- `figures/roadmap.svg` — дорожная карта работ
- `figures/stack.svg` — стек технологий
- `figures/broker-twin.svg` — схема двойника брокера
- `figures/four-brokers.svg` — четыре брокера
- `figures/broker-capacity.svg` — график пропускной способности
- `figures/calibration-flow.svg` — схема калибровки
- `figures/profile-schema.svg` — схема данных профиля
- `figures/calibration-pipeline.svg` — поток калибровки
- `figures/s0-s5-status.svg` — статус S0–S5
- `figures/packet-slot.svg` — устройство транспортных слотов
- `figures/counter-clock.svg` — CounterClock
- `figures/tick-worker-pool.svg` — двухфазный refresh
- `figures/experiment-design.svg` — экспериментальный дизайн
- `figures/variables.svg` — таблица переменных
- `figures/deliveries-formula.svg` — формулы доставок
- `figures/traffic-formulas.svg` — формулы трафика
- `figures/holdout-error.svg` — график holdout-ошибки
- `figures/hardware-invariance.svg` — сравнение хостов
- `figures/topology-lb.svg` — топология LB
- `figures/topology-star.svg` — Federation star
- `figures/topology-ring.svg` — Federation ring
- `figures/topology-line.svg` — Federation line
- `figures/topology-relay.svg` — топология Relay
- `figures/topology-comparison.svg` — сравнение при B=16
- `figures/topology-selection.svg` — матрица выбора
- `figures/august-vs-november.svg` — сравнение с августом
- `figures/large-network-speedup.svg` — ускорение на большой сети
- `figures/matrix-speedup.svg` — матрица конфигураций
- `figures/reference-benchmark.svg` — reference-бенчмарк
- `figures/optimizations.svg` — ключевые оптимизации
- `figures/gates.svg` — гейты производительности
- `figures/broker-aedes.svg` — профиль Aedes
- `figures/broker-mosquitto.svg` — профиль Mosquitto
- `figures/broker-activemq.svg` — профиль ActiveMQ
- `figures/broker-emqx.svg` — профиль EMQX
- `figures/broker-comparison.svg` — сравнение брокеров
- `figures/mqttrelay-architecture.svg` — архитектура MqttRelay
- `figures/mqttrelay-results.svg` — результаты MqttRelay
- `figures/mqttrelay-observations.svg` — наблюдения MqttRelay
- `figures/results-summary.svg` — итоги работы
- `figures/scientific-results.svg` — научные результаты
- `figures/limitations.svg` — ограничения
- `figures/future-work.svg` — дальнейшие работы
- `figures/documentation.svg` — документация
- `figures/conclusion.svg` — заключение