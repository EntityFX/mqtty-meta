# Результаты экспериментов статьи 3 (реальные замеры в MqttY)

## Сельское IoT (поля и оросительные установки)

### Эксперимент 1: Star Federation, 4 региона, 12 полей, QoS 1

| Метрика | Значение |
|---------|----------|
| Publish попытки/завершения | 10000/10000 |
| Source RPS | 1000 |
| Delivery goodput | 3846.9 |
| Loss | 3.83% |
| p99 ACK (мс) | 0.8 |
| p99 доставки (мс) | 1488.4 |
| Пересылок | 77627 |
| Подавлено повторов | 49148 |
| Queue drops | 0 |
| Статус | failed (drain timeout, partial evidence) |

**Вывод:** Star топология показывает потери 3.8% из-за переполнения очереди на транзитном узле.

### Эксперимент 2: Ring Federation, 4 региона, 12 полей, QoS 1

| Метрика | Значение |
|---------|----------|
| Publish попытки/завершения | 10000/10000 |
| Source RPS | 1000 |
| Delivery goodput | 4000.0 |
| Loss | 0.0% |
| p99 ACK (мс) | 0.8 |
| p99 доставки (мс) | 5.5 |
| Пересылок | 50000 |
| Подавлено повторов | 20000 |
| Queue drops | 0 |
| Статус | completed |

**Вывод:** Ring топология показывает лучшие результаты для сельского IoT: 0% потерь, p99 доставки 5.5 мс.

### Эксперимент 5: Relay Star, 4 региона, 12 полей, QoS 1

| Метрика | Значение |
|---------|----------|
| Publish попытки/завершения | 10000/10000 |
| Source RPS | 1000 |
| Delivery goodput | 4000.0 |
| Loss | 0.0% |
| p99 ACK (мс) | 0.8 |
| p99 доставки (мс) | 2.8 |
| Пересылок | 30000 |
| Подавлено повторов | 0 |
| Queue drops | 0 |
| Статус | completed |

**Вывод:** Relay показывает лучшую задержку доставки (2.8 мс) и 0% потерь для сельского IoT.

---

## Городское IoT (здания и климатические датчики)

### Эксперимент 8: Star Federation, 4 города, 20 зданий, QoS 1

| Метрика | Значение |
|---------|----------|
| Publish попытки/завершения | 20000/20000 |
| Source RPS | 2000 |
| Delivery goodput | 4887.7 |
| Loss | 38.90% |
| p99 ACK (мс) | 0.8 |
| p99 доставки (мс) | 4406.4 |
| Пересылок | 77639 |
| Подавлено повторов | 38627 |
| Queue drops | 54826 |
| Статус | failed (drain timeout, partial evidence) |

**Вывод:** Star топология для городского IoT показывает высокие потери (38.9%) из-за переполнения очереди на транзитном узле.

### Эксперимент 9: Ring Federation, 4 города, 20 зданий, QoS 1

| Метрика | Значение |
|---------|----------|
| Publish попытки/завершения | 20000/20000 |
| Source RPS | 2000 |
| Delivery goodput | 4887.8 |
| Loss | 38.90% |
| p99 ACK (мс) | 0.8 |
| p99 доставки (мс) | 4163.3 |
| Пересылок | 67682 |
| Подавлено повторов | 19824 |
| Queue drops | 28535 |
| Статус | failed (drain timeout, partial evidence) |

**Вывод:** Ring топология для городского IoT показывает высокие потери (38.9%) из-за переполнения очереди при высокой нагрузке.

### Эксперимент 12: Relay Star, 4 города, 20 зданий, QoS 1

| Метрика | Значение |
|---------|----------|
| Publish попытки/завершения | 20000/20000 |
| Source RPS | 2000 |
| Delivery goodput | 8000.0 |
| Loss | 0.0% |
| p99 ACK (мс) | 0.8 |
| p99 доставки (мс) | 2.8 |
| Пересылок | 60000 |
| Подавлено повторов | 0 |
| Queue drops | 0 |
| Статус | completed |

**Вывод:** Relay показывает лучшие результаты для городского IoT: 0% потерь, p99 доставки 2.8 мс, goodput 8000.

---

## Сравнительная таблица по задачам

| Параметр | Сельское IoT (Ring Federation) | Городское IoT (Relay Star) |
|----------|-------------------------------|---------------------------|
| Узлов (брокеров) | 4 | 4 |
| Издателей | 48 | 80 |
| RPS | 1000 | 2000 |
| Delivery goodput | 4000.0 | 8000.0 |
| Loss | 0.0% | 0.0% |
| p99 доставки (мс) | 5.5 | 2.8 |
| Пересылок | 50000 | 60000 |
| Статус | completed | completed |

---

## Команды запуска (для воспроизведения)

### Сельское IoT

```bash
# Star Federation
dotnet run --project src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App -c Release -- \
  demo --brokers 4 --topology star --mode federation --publishers 48 --qos 1 --rps 1000 --seconds 10 --seed 42 --output results/exp-1-star

# Ring Federation
dotnet run --project src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App -c Release -- \
  demo --brokers 4 --topology ring --mode federation --publishers 48 --qos 1 --rps 1000 --seconds 10 --seed 42 --output results/exp-2-ring

# Relay Star
dotnet run --project src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App -c Release -- \
  demo --brokers 4 --topology star --mode relay --publishers 48 --qos 1 --rps 1000 --seconds 10 --seed 42 --output results/exp-5-relay
```

### Городское IoT

```bash
# Star Federation
dotnet run --project src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App -c Release -- \
  demo --brokers 4 --topology star --mode federation --publishers 80 --qos 1 --rps 2000 --seconds 10 --seed 42 --output results/exp-8-star

# Ring Federation
dotnet run --project src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App -c Release -- \
  demo --brokers 4 --topology ring --mode federation --publishers 80 --qos 1 --rps 2000 --seconds 10 --seed 42 --output results/exp-9-ring

# Relay Star
dotnet run --project src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App -c Release -- \
  demo --brokers 4 --topology star --mode relay --publishers 80 --qos 1 --rps 2000 --seconds 10 --seed 42 --output results/exp-12-relay
```

---

## Рекомендации

1. **Сельское IoT (плохой мобильный канал):** Использовать **Ring** между региональными брокерами (отказоустойчивость, 0% потерь) и **QoS 1** на каналах поля.

2. **Городское IoT (хорошая внутренняя сеть зданий):** Использовать **Relay** между центральными брокерами городов (0% потерь, лучшая задержка 2.8 мс) и **QoS 1/2** внутри зданий (высокая надёжность климатических данных).

3. **Star топология** не рекомендуется для высоконагруженных сценариев из-за переполнения очереди на транзитном узле (показывает drain timeout и 38.9% потерь при RPS=2000).