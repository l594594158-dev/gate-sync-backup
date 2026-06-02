#!/usr/bin/env python3
"""开仓日志：记录每次开仓的完整六条件指标值，便于每日复盘"""

import os
from datetime import datetime

ENTRY_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'entry_log.txt')

_HEADER = (
    "时间                 品种   方向    入场价       SMA20(5m)  偏离%   1h方向  ADX1h  ADX4h  RSI5m  量比    TP        SL\n"
    "─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────"
)

def log_entry(symbol, direction, entry_price, indicators):
    """
    写入开仓日志。

    indicators 字典需包含:
        sma5m, live_price: float
        h1_bull: bool
        adx1h, adx4h, rsi5m, vol_ratio: float
        tp_price, sl_price: float
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sma5m = indicators['sma5m']
    live_price = indicators['live_price']
    deviation = (live_price - sma5m) / sma5m * 100
    dir_1h = '多' if indicators['h1_bull'] else '空'
    adx1h = indicators['adx1h']
    adx4h = indicators['adx4h']
    rsi5m = indicators['rsi5m']
    vol_ratio = indicators['vol_ratio']
    tp = indicators['tp_price']
    sl = indicators['sl_price']

    line = (f"{now}  {symbol:<6} {direction:<5}  "
            f"${entry_price:<10.4f} ${sma5m:<10.2f} {deviation:>+6.2f}%  "
            f"{dir_1h:<5}  {adx1h:<5.1f}  {adx4h:<5.1f}  {rsi5m:<5.1f}  "
            f"{vol_ratio:<5.2f}x  ${tp:<8.4f} ${sl:<8.4f}")

    # 如果文件不存在或空，先写表头
    write_header = not os.path.exists(ENTRY_LOG_FILE) or os.path.getsize(ENTRY_LOG_FILE) == 0
    with open(ENTRY_LOG_FILE, 'a') as f:
        if write_header:
            f.write(_HEADER + '\n')
        f.write(line + '\n')
