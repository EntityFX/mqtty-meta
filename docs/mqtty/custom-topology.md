# Минимальная собственная топология через public API

Пример [Program.cs](../../mqtty/doc/examples/custom-network/Program.cs) и [проект](../../mqtty/doc/examples/custom-network/CustomNetwork.csproj) строит две области lan↔wan: publisher/subscriber в lan, брокер в wan, QoS1, payload16, seed42. Нужны SDK, net6 runtime и checkout mqtty; установка на [Linux/Windows](../mqtt-benchmark/machine-preparation.md). Рабочий каталог — корень mqtty. Designer не нужен.

Сначала проверка механизма без профиля:

```powershell
dotnet run -c Release --project doc/examples/custom-network -- --ideal results/custom-ideal-001
```

Ожидаются delivered=1, errors=0 и новый каталог с summary.json, topology.graphml, topology.puml. --ideal служит проверкой маршрутизации и MQTT, не калибровкой и не обходным профилем v2. Проверка компиляции и доставки выполнена на текущей ALT; Windows здесь не запускался.

С готовой внешней calibration v3:

```powershell
dotnet run -c Release --project doc/examples/custom-network -- `
  results/broker-calibration.v3.json results/custom-calibrated-001
```

Пример выбирает Mosquitto point16/QoS1/publishers1, сохраняет SHA-256 потреблённых bytes. Есть две actual connections; profile lookup count=1. Один publish может быть отказан/потерян калиброванной моделью: эта демонстрация не содержит статистической выборки для fidelity. Существующий output запрещён; Ctrl+C прерывает пример, graph.Clear освобождает simulator в finally. При нормальном окончании граф очищается после экспорта.

| Шаг | API / назначение |
|---|---|
| Создать граф | NetworkSimulator + DijkstraPathFinder + TicksOptions |
| Добавить области | Network и NetworkOptions; AddNetwork |
| Связать направления | Link(lan,wan) и Link(wan,lan), затем UpdateRoutes |
| Создать MQTT зависимости | GenericBuilder.GetMqttTopicEvaluator/GetMqttPacketManager |
| Добавить брокер | Подкласс публичного MqttNetworkBuilder вызывает protected CreateServer; internal конструкторы не используются |
| Добавить клиентов | MqttClient, регистрации в области и графе |
| Подготовить протокол | BeginConnect → барьер IsConnected; BeginSubscribe → барьер IsSubscribed |
| Измерить передачу | ResetMeasurement, Publish, bounded Refresh до IsQuiescent |
| Экспортировать | GetMetrics, GraphML, PlantUML; затем Clear |

Ограничение каждой wait-фазы 20000 ticks предотвращает бесконечный hang. Чтобы изменить сеть, редактируйте отдельную копию примера: NetworkOptions/links/размещение узлов. После изменения маршрутов заново вызывайте UpdateRoutes; CONNECT и SUBACK должны предшествовать публикации. Для серии измерений используйте experiment runner с фазами и provenance, а не один publish этого примера.
