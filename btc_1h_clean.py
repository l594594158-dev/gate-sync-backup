#!/usr/bin/env python3
"""BTC 1h EMA5/10交叉 四条件回测 | 全部1h K线 | TP2.0/SL5.0/3仓"""
import numpy as np, pandas as pd, ta
from collections import defaultdict

DB = '/root/liucangyang/databases'
df1 = pd.read_pickle(f'{DB}/btc_futures_1h.pkl')
df4 = pd.read_pickle(f'{DB}/btc_futures_4h.pkl')

n = len(df1)
c1 = df1['c'].values; h1 = df1['h'].values; l1 = df1['l'].values; o1 = df1['o'].values; v1 = df1['v'].values

# ─── 指标全部在1h上计算 ───
ema5 = pd.Series(c1).ewm(span=5, adjust=False).mean().values
ema10 = pd.Series(c1).ewm(span=10, adjust=False).mean().values

rsi1h = ta.momentum.RSIIndicator(df1['c'], 14).rsi().values
adx1h = ta.trend.ADXIndicator(df1['h'], df1['l'], df1['c'], 14).adx().values
adx4h_raw = ta.trend.ADXIndicator(df4['h'], df4['l'], df4['c'], 14).adx().values

H1_MS = 3600_000
i4h_map = np.clip(np.searchsorted(df4['t'], df1['t'] - 4*H1_MS, side='right')-1, 0, len(df4)-1)
adx4h = adx4h_raw[i4h_map]

vr1h = np.full(n, np.nan)
for i in range(21, n):
    vr1h[i] = v1[i] / v1[max(0,i-19):i+1].mean()

# ─── 参数 ───
TP, SL = 2.0, 5.0; tp_f = TP/100; sl_f = SL/100
ADX_MIN, ADX4_MAX, VOL_MIN = 20, 50, 3.0
MAX_POS = 3

# ─── 回测 ───
longs = []; shorts = []
w = l = 0; pnl_cum = 0; max_pnl = 0; max_dd = 0
monthly = defaultdict(float)
trade_log = []

for i in range(1, n-1):  # i=刚闭K, i+1=下一根(用其OHLC检查TP/SL和入场)
    hi, lo = h1[i+1], l1[i+1]
    
    # TP/SL 现有持仓
    for lst, mult in [(longs, 1), (shorts, -1)]:
        survivors = []
        for pos in lst:
            entry = pos['entry']
            tp_px = entry * (1 + mult * tp_f)
            sl_px = entry * (1 - mult * sl_f)
            exit_px = None
            if mult == 1:
                if hi >= tp_px: exit_px = tp_px
                elif lo <= sl_px: exit_px = sl_px
            else:
                if lo <= tp_px: exit_px = tp_px
                elif hi >= sl_px: exit_px = sl_px
            if exit_px is not None:
                net = (exit_px - entry) / entry * mult - 0.001
                pnl_cum += net; max_pnl = max(max_pnl, pnl_cum)
                max_dd = min(max_dd, pnl_cum - max_pnl)
                m = pd.to_datetime(df1['t'].iloc[i+1], unit='ms').strftime('%Y-%m')
                monthly[m] = monthly[m] + net
                if net > 0: w += 1
                else: l += 1
            else:
                survivors.append(pos)
        lst[:] = survivors

    # 信号: 用bar i数据 (刚闭)
    bi = ema5[i] > ema10[i]
    ai = adx1h[i]; a4i = adx4h[i]; ri = rsi1h[i]; vi = vr1h[i]
    if np.isnan(ai) or np.isnan(a4i) or np.isnan(ri) or np.isnan(vi): continue
    if ai <= ADX_MIN or a4i >= ADX4_MAX: continue
    if vi < VOL_MIN: continue

    ep = o1[i+1]  # 入场价=bar i+1开盘
    
    # 开仓 + 同根K线TP/SL检查
    for dir_key, cond, lst in [('LONG', bi and ri > 40, longs), ('SHORT', not bi and ri < 60, shorts)]:
        if not cond or len(lst) >= MAX_POS: continue
        mult = 1 if dir_key == 'LONG' else -1
        tp_px = ep * (1 + mult * tp_f)
        sl_px = ep * (1 - mult * sl_f)
        exit_px = None
        if mult == 1:
            if hi >= tp_px: exit_px = tp_px
            elif lo <= sl_px: exit_px = sl_px
        else:
            if lo <= tp_px: exit_px = tp_px
            elif hi >= sl_px: exit_px = sl_px
        if exit_px is not None:
            net = (exit_px - ep) / ep * mult - 0.001
            pnl_cum += net; max_pnl = max(max_pnl, pnl_cum)
            max_dd = min(max_dd, pnl_cum - max_pnl)
            m = pd.to_datetime(df1['t'].iloc[i+1], unit='ms').strftime('%Y-%m')
            monthly[m] = monthly[m] + net
            if net > 0: w += 1
            else: l += 1
        else:
            lst.append({'entry': ep, 'kl': i+1})

# 未平仓按最后一根收盘价结算
for lst, mult in [(longs, 1), (shorts, -1)]:
    for pos in lst:
        net = (c1[-1] - pos['entry']) / pos['entry'] * mult - 0.001
        pnl_cum += net
        m = pd.to_datetime(df1['t'].iloc[-1], unit='ms').strftime('%Y-%m')
        monthly[m] = monthly[m] + net
        if net > 0: w += 1
        else: l += 1

total = w + l
wr = w / total * 100 if total else 0
neg_m = sum(1 for v in monthly.values() if v < 0)

print(f"═══ BTC 1h EMA5/10交叉 一年回测 ═══")
print(f"数据: {pd.to_datetime(df1.t.iloc[0],unit='ms')} ~ {pd.to_datetime(df1.t.iloc[-1],unit='ms')}")
print(f"参数: TP{TP}%/SL{SL}% | ADX1h>{ADX_MIN} 4h<{ADX4_MAX} vol>{VOL_MIN}x | {MAX_POS}仓/边 | 四条件无SMA")
print(f"\n  ▲ 累计含费: {pnl_cum*100:+.1f}%")
print(f"  ▲ 交易笔数: {total}")
print(f"  ▲ 胜率: {wr:.1f}%")
print(f"  ▲ 最大回撤: {max_dd*100:+.1f}%")
print(f"  ▲ 亏损月数: {neg_m}/{len(monthly)}")
print(f"\n月度明细:")
for m in sorted(monthly.keys()):
    v = monthly[m]
    bar = "█" * max(0, min(20, int(abs(v)*2)))
    print(f"  {m}: {v*100:>+6.1f}% {bar}")
