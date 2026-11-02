# MqttY — метарепозиторий

- `mqtty` — симулятор MQTT-сетей.
- `MqttBenchmark` — натурные эксперименты с брокерами.
- `diss` — материалы диссертации.

## Воспроизводимость

[Отчёт Windows x64 → ALT Linux / Эльбрус от 02.11.2026](reproducibility/reports/linux-e2k-20261102/README.md):
6 сценариев, 12 удалённых запусков; научные результаты совпали с Windows baseline.
Разница runtime и ограничения эксперимента отражены в отчёте.

[Инструкция и запуск](reproducibility/README.md). Для проверки сохранённых артефактов:

```bash
python3 reproducibility/verify_evidence.py reproducibility/reports/linux-e2k-20261102
```
