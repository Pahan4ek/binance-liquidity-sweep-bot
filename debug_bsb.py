import pandas as pd
import requests
from datetime import datetime, timezone

SYMBOL = "bsbusdt"
INTERVAL = "15m"
LIMIT = 50

# Время из твоего TradingView: 29 апреля 2026, 09:15 (UTC+3)
# В UTC (время серверов Binance) это минус 3 часа = 06:15
target_time_utc = datetime(2026, 4, 29, 6, 15, tzinfo=timezone.utc)
target_ts = int(target_time_utc.timestamp() * 1000)

# Запрашиваем свечи ДО 06:30 UTC, чтобы наша свеча 06:15 была последней закрытой
end_time = target_ts + (15 * 60 * 1000) 
url = f"https://fapi.binance.com/fapi/v1/klines?symbol={SYMBOL.upper()}&interval={INTERVAL}&endTime={end_time}&limit={LIMIT+1}"
response = requests.get(url).json()

# Пакуем в таблицу
df = pd.DataFrame(response, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
df['time'] = pd.to_datetime(df['time'], unit='ms')
for col in ['open', 'high', 'low', 'close', 'volume']:
    df[col] = df[col].astype(float)

# Отрезаем последнюю свечу как "нашу", остальные 50 - история
last_candle = df.iloc[-1]
history = df.iloc[:-1]

liquidity_pool = history['high'].max()
avg_volume = history['volume'].mean()

print(f"\n=== ДЕТЕКТИВНЫЙ ОТЧЕТ ПО СВЕЧЕ {last_candle['time']} UTC ===")
print(f"1. Уровень пула ликвидности (макс за {LIMIT} свечей): {liquidity_pool}")
print(f"2. Хай нашей свечи: {last_candle['high']} -> Пробой был? {'✅ ДА' if last_candle['high'] > liquidity_pool else '❌ НЕТ'}")
print(f"3. Закрытие свечи: {last_candle['close']} -> Закрылась НИЖЕ уровня? {'✅ ДА' if last_candle['close'] < liquidity_pool else '❌ НЕТ'}")
print(f"4. Средний объем: {avg_volume:.2f} | Объем свечи: {last_candle['volume']:.2f}")
print(f"5. Аномальный объем (x1.5)? {'✅ ДА' if last_candle['volume'] > avg_volume * 1.5 else '❌ НЕТ'}")

is_sweep = last_candle['high'] > liquidity_pool and last_candle['close'] < liquidity_pool
is_high_volume = last_candle['volume'] > (avg_volume * 1.5)

print("\n" + "="*40)
if is_sweep and is_high_volume:
    print("ВЕРДИКТ: Скрипт должен был сработать! Ищем баг в коде.")
else:
    print("ВЕРДИКТ: Скрипт проигнорировал сетап. Причины:")
    if not (last_candle['high'] > liquidity_pool): print("- Свеча вообще не обновила хай.")
    if not (last_candle['close'] < liquidity_pool): print("- Свеча закрылась ВЫШЕ уровня (закрепление, а не ложный пробой).")
    if not is_high_volume: print("- Объем оказался слишком низким по сравнению со средним.")
print("="*40 + "\n")