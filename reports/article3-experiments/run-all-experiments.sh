#!/bin/bash
# ============================================================================
# Скрипт запуска всех экспериментов статьи 3 (сельскохозяйственная IoT-архитектура)
# Запускать из корня репозитория mqtty (где лежит EntityFX.MqttY.Federation.App)
# ============================================================================

set -euo pipefail

MQTTY_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
echo "MQTTY_ROOT: $MQTTY_ROOT"

# Проверяем наличие Federation.App
FEDERATION_DLL="$MQTTY_ROOT/mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/bin/Release/net6.0/EntityFX.MqttY.Federation.App.dll"

if [ ! -f "$FEDERATION_DLL" ]; then
    echo "Сборка Federation.App..."
    dotnet build "$MQTTY_ROOT/mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/EntityFX.MqttY.Federation.App.csproj" -c Release
fi

CONFIGS_DIR="$(dirname "$0")"
RESULTS_DIR="$MQTTY_ROOT/results/article3"
mkdir -p "$RESULTS_DIR"

run_experiment() {
    local config="$1"
    local name=$(basename "$config" .json)
    echo ""
    echo "=========================================="
    echo "Запуск эксперимента: $name"
    echo "Конфиг: $config"
    echo "=========================================="
    
    dotnet "$FEDERATION_DLL" run --config "$config" --output "$RESULTS_DIR/$name"
    
    if [ $? -eq 0 ]; then
        echo "✓ Эксперимент $name завершен успешно"
    else
        echo "✗ Эксперимент $name завершен с ошибкой"
    fi
}

# Список всех экспериментов
EXPERIMENTS=(
    "$CONFIGS_DIR/exp-1-star-config.json"
    "$CONFIGS_DIR/exp-2-ring-config.json"
    "$CONFIGS_DIR/exp-5-relay-config.json"
)

# Запуск всех экспериментов
for config in "${EXPERIMENTS[@]}"; do
    run_experiment "$config"
done

echo ""
echo "=========================================="
echo "Все эксперименты завершены"
echo "Результаты в: $RESULTS_DIR"
echo "=========================================="