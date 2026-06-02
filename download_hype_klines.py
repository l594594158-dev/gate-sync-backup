#!/usr/bin/env python3
"""
Gate HYPE 合约全量 K 线下载 v2
Gate 限制: 每周期最多返回最近 10000 根
从最新往回拉，分页直到用完限额
"""

import ccxt
import pandas as pd
from datetime import datetime, timezone
import time
import os

SYMBOL = 'HYPE/USDT:USDT'
DATA_DIR = '/root/liucangyang/databases'
os.makedirs(DATA_DIR, exist_ok=True)

# Gate 限制: 最近 10000 根
# 各周期对应的大概时间跨度:
LIMITS = {
    '1d':  {'max': 10000, 'span': '~27年', 'requests': 10},
    '4h':  {'max': 10000, 'span': '~4.5年', 'requests': 10},
    '1h':  {'max': 10000, 'span': '~416天', 'requests': 10},
    '5m':  {'max': 10000, 'span': '~34天', 'requests': 10},
    '1m':  {'max': 10000, 'span': '~6.9天', 'requests': 10},
    '1s':  {'max': 10000, 'span': '~2.8小时', 'requests': 10},
}

def download(symbol, tf, max_req, label):
    g = ccxt.gate({'options': {'defaultType': 'swap'}})
    g.load_markets()

    all_candles = []
    since = None  # 从最新开始
    request_count = 0
    earliest = None

    print(f'\n═══ {label} ({tf}) | Gate限制: 最近 {LIMITS[tf]["max"]} 根 {LIMITS[tf]["span"]} ═══')

    while request_count < max_req:
        try:
            if since is None:
                candles = g.fetch_ohlcv(symbol, tf, limit=1000)
            else:
                candles = g.fetch_ohlcv(symbol, tf, since=since, limit=1000)
        except Exception as e:
            err = str(e)[:120]
            if 'too long ago' in err or '10000' in err:
                print(f'  ⏹ 已达 Gate 历史边界')
            else:
                print(f'  ❌ {err}')
            break

        if not candles or len(candles) <= 1:
            print(f'  ✅ 无更多数据')
            break

        request_count += 1

        # 去重处理
        new_candles = []
        for c in candles:
            if not all_candles or c[0] > all_candles[-1][0]:
                new_candles.append(c)
            elif all_candles and c[0] < all_candles[0][0]:
                new_candles.append(c)

        if not new_candles and request_count > 1:
            print(f'  ✅ 数据已覆盖全部')
            break

        all_candles.extend(new_candles)
        all_candles.sort(key=lambda x: x[0])

        first_ts = datetime.fromtimestamp(all_candles[0][0]/1000, tz=timezone.utc)
        last_ts = datetime.fromtimestamp(all_candles[-1][0]/1000, tz=timezone.utc)

        bar = '█' * min(request_count, 40)
        print(f'  [{bar:<40}] #{request_count}: {first_ts.strftime("%m-%d %H:%M")} ~ {last_ts.strftime("%m-%d %H:%M")} | {len(all_candles)} 根')

        # 下一页: 从最早时间往前拉
        since = all_candles[0][0] - 1

        # 如果返回不是满的，到头了
        if len(candles) < 999:
            print(f'  ✅ 边界到达')
            break

        time.sleep(0.12)

    # 去重 + 排序保存
    if all_candles:
        df = pd.DataFrame(all_candles, columns=['ts','open','high','low','close','volume'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms')
        df = df.drop_duplicates(subset=['ts']).sort_index()

        path = f'{DATA_DIR}/hype_{tf}.pkl'
        df.to_pickle(path)
        print(f'  💾 {path} ({len(df)} 根 | {df.index[0]} ~ {df.index[-1]})')
        return len(df)
    return 0

if __name__ == '__main__':
    print('═══ Gate HYPE 合约全量 K 线下载 v2 ═══')
    print(f'策略: 从最新往回分页拉取，充分利用 10000 根限额')
    print()

    total = 0
    for tf in ['1d', '4h', '1h', '5m', '1m', '1s']:
        n = download(SYMBOL, tf, LIMITS[tf]['requests'], tf)
        total += n

    print(f'\n{"="*65}')
    print(f'{"周期":>5s}  {"根数":>8s}  {"起始":<20s}  {"结束":<20s}  覆盖')
    print(f'{"-"*65}')
    for tf in ['1s','1m','5m','1h','4h','1d']:
        path = f'{DATA_DIR}/hype_{tf}.pkl'
        if os.path.exists(path):
            df = pd.read_pickle(path)
            days = (df.index[-1] - df.index[0]).days
            hrs = (df.index[-1] - df.index[0]).total_seconds() / 3600
            span = f'{days}d' if days > 0 else f'{hrs:.1f}h'
            print(f'{tf:>5s}  {len(df):>8d}  {str(df.index[0])[:19]:<20s}  {str(df.index[-1])[:19]:<20s}  {span}')
    print(f'{"="*65}')
    print(f'总计: {total} 根 | 目录: {DATA_DIR}/')
