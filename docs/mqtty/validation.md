# Проверка симуляции и интерпретация fidelity

Калибровка — преобразование observations в параметры. Fidelity — отдельный эксперимент сравнения модели с измерениями той же точки и установки. Успешное calibrate, compile или smoke не доказывает fidelity.

| Критерий runner | Граница | Reference |
|---|---|---|
| CompletedRps | относительная ошибка <=0.15 | observed completedRps.mean |
| PublishFailureRate | абсолютная разница <=0.05 | observed publishFailureRate.mean |
| DeliveryLossRate | абсолютная разница <=0.05 | observed deliveryLossRate.mean |
| p99 QoS1/2 | относительная ошибка <=0.20 | observed completion p99 |
| p99 QoS0 | notApplicable | ACK latency отсутствует |

Относительный критерий при нулевом/отсутствующем reference — notApplicable; это не успешное сравнение. Высокая натурная loss не запрещает точку сама по себе: критерий оценивает **разницу** модели и наблюдения. Причины потерь наблюдаемой установки надо исследовать отдельно. Resource CPU/EST — notModeled, resource telemetry автоматически не калибруется.

## Повторение и покрытие

Для стандартного покрытия: 96 single-broker fidelity points × три заранее выбранных seed =288 симуляций. Выберите те же broker/payload/QoS/N, lookup=publishers, attemptedRps и транспорт/RTT. Это отдельная серия после получения полноценной calibration. Результаты с интерполяцией или extrapolation отмечайте отдельно; наличие профильной интерполяции не подтверждает её точность.

Для relay smoke 4 брокера ×2 payload ×3 QoS =24 сценария, если применяется этот план покрытия. Смешанная сеть — отдельная топология и требует своей натурной валидации для заявления точности сети. Процент готовности документации не означает процент успешной натурной fidelity.

Deterministic replay использует одинаковые config/profile bytes, commits, tick options, sequential refresh и seed. Сравнивайте scientific summaries/counters/histograms; run IDs/путь/время исполнения могут различаться. Проверяйте фактические deliveries удалённым receivers, а не только завершение publisher. После drain simulation ledger заморожен; measurement-time denominator соответствует runner, а не wall-clock секундомеру.

## Доступные проверки кода

Из mqtty, Linux/Windows, PowerShell 7:

```powershell
dotnet test src/EntityFX.MqttSimulator/EntityFX.Tests.Integration -c Release `
  --filter 'FullyQualifiedName~CalibrationV3Tests|FullyQualifiedName~CalibrationStatisticsRoundoffTests|FullyQualifiedName~ExperimentV3Tests'
dotnet run -c Release --project doc/examples/custom-network -- --ideal results/custom-ideal-check-001
```

Ожидаются успешные tests и одна ideal доставка. Synthetic test fixtures проверяют схемы/формулы, а не реальные свойства брокеров. Остановка — Ctrl+C; output/check directory должен быть новым. Полный smoke/тесты текущего пакета и непроверенные платформы перечислены в [отчёте](../../reports/history/setup/platform-validation.md).

| Проблема | Исправление |
|---|---|
| Неполное покрытие | Для полного набора нужны 288/96, для `calibrate --broker` — 72/24 выбранного брокера; сокращённая программа должна соответствовать своему явному coverage protocol |
| profile SHA изменился | Восстановить bytes или создать новую серию/manifest |
| Dirty source | Использовать чистый checkout нужного commit |
| Ошибочный external path | Разрешить путь относительно CWD или задать абсолютный |
| Config/output существует | Config/calibration — новый файл; experiment output parent может содержать другие уникальные runs |
| Неверная сеть/RTT/N | Исправить отдельную конфигурацию; сравнивать сопоставимую точку |
| exit3 | Изучить failed criteria, сохранить bundle; не объявлять результат успешной fidelity |
