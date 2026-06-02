#!/usr/bin/env python3
"""
HYPE 一年回测 v2 — 预计算+向量化
"""
import pandas as pd, ta, json, numpy as np
from datetime import datetime

TP_PCT, SL_PCT, QTY = 1.5/100, 2.0/100, 8

# ---- 加载 ----
DB = '/root/liucangyang/databases'
df5 = pd.read_pickle(f'{DB}/hype_5m.pkl')
df1 = pd.read_pickle(f'{DB}/hype_1h.pkl')
df4 = pd.read_pickle(f'{DB}/hype_4h.pkl')
df5['dt'] = pd.to_datetime(df5['t'], unit='ms')
df1['dt'] = pd.to_datetime(df1['t'], unit='ms')
df4['dt'] = pd.to_datetime(df4['t'], unit='ms')

# ---- 预计算 1h/4h 闭K指标 ----
for name, df in [('1h', df1), ('4h', df4)]:
    c = df['c']; h = df['h']; l = df['l']
    df['sma20'] = ta.trend.SMAIndicator(c, 20).sma_indicator()
    adx = ta.trend.ADXIndicator(h, l, c, 14).adx()
    df['adx'] = adx
    df['bull'] = c > df['sma20']

# ---- 把 1h/4h 闭K指标映射到 5m 时间轴 ----
def map_closed(df_src, df_target, col_map):
    """对每个5m时间点，取 <= 该时间的最新闭K指标"""
    src_t = df_src['dt'].values
    tgt_t = df_target['dt'].values
    idx = np.searchsorted(src_t, tgt_t, side='right') - 1
    idx = np.clip(idx, 0, len(df_src)-1)
    for src_col, tgt_col in col_map.items():
        df_target[tgt_col] = df_src[src_col].values[idx]

map_closed(df1, df5, {'sma20': 'sma1h', 'adx': 'adx1h', 'bull': 'bull1h', 'c': 'c1h'})
map_closed(df4, df5, {'sma20': 'sma4h', 'adx': 'adx4h', 'bull': 'bull4h'})

# ---- 5m 滚动指标 ----
c5 = df5['c'].values; h5 = df5['h'].values; l5 = df5['l'].values; v5 = df5['v'].values
n = len(df5)

# 用 pandas rolling 预计算
sma5_raw = pd.Series(c5).rolling(20).mean().values
rsi5_raw = np.full(n, np.nan)

# RSI Wilder 14
def wilder_rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full(len(close), np.nan)
    avg_loss = np.full(len(close), np.nan)
    avg_gain[period] = gain[1:period+1].mean()
    avg_loss[period] = loss[1:period+1].mean()
    for i in range(period+1, len(close)):
        avg_gain[i] = (avg_gain[i-1]*13 + gain[i])/14
        avg_loss[i] = (avg_loss[i-1]*13 + loss[i])/14
    rs = avg_gain / avg_loss
    return 100 - 100/(1+rs)

rsi5_raw = wilder_rsi(c5)

# 量比: lv-1闭K量 / 20根均量
vol_ratio = np.full(n, np.nan)
for i in range(21, n):
    clv = i - 1
    vol_ratio[i] = v5[clv] / v5[max(0,clv-19):clv+1].mean()

# ---- 回测主循环 ----
state = {'pos': None, 'last_exit_kl': 0}
trades = []
monthly = {}
pnl_cum = 0.0
peak = 0.0; max_dd = 0.0
cons_loss = 0; max_cons_loss = 0

for i in range(21, n - 1):
    kl_ts = int(df5['t'].iloc[i])
    
    # 指标
    sma5 = sma5_raw[i-1]  # 闭K SMA20
    rsi5 = rsi5_raw[i-1]  # 闭K RSI
    vr = vol_ratio[i]
    adx1 = df5['adx1h'].iloc[i]
    adx4 = df5['adx4h'].iloc[i]
    bull1 = df5['bull1h'].iloc[i]
    price_live = c5[i]
    
    if pd.isna(sma5) or pd.isna(adx1) or pd.isna(adx4):
        continue
    
    # 六条件
    sig = None
    if adx1 <= 20 or adx4 >= 55 or vr < 1.0:
        pass
    elif not (sma5 * 0.985 <= price_live <= sma5 * 1.015):
        pass
    elif bull1 and rsi5 > 40:
        sig = 'LONG'
    elif (not bull1) and rsi5 < 60:
        sig = 'SHORT'
    
    # 冷却
    if sig and kl_ts <= state['last_exit_kl']:
        sig = None
    # 仓位锁
    if sig and state['pos'] is not None:
        sig = None
    
    # 出场检查
    if state['pos'] is not None:
        p = state['pos']
        nc = df5.iloc[i+1]  # 下一根K线
        exit_p = None; reason = None
        
        if p['dir'] == 'LONG':
            if nc['h'] >= p['tp']: exit_p = p['tp']; reason = 'TP'
            elif nc['l'] <= p['sl']: exit_p = p['sl']; reason = 'SL'
        else:
            if nc['l'] <= p['tp']: exit_p = p['tp']; reason = 'TP'
            elif nc['h'] >= p['sl']: exit_p = p['sl']; reason = 'SL'
        
        if exit_p:
            pnl = (exit_p - p['entry'])/p['entry'] if p['dir']=='LONG' else (p['entry']-exit_p)/p['entry']
            pnl_cum += pnl
            peak = max(peak, pnl_cum)
            max_dd = max(max_dd, peak - pnl_cum)
            
            if pnl < 0:
                cons_loss += 1
                max_cons_loss = max(max_cons_loss, cons_loss)
            else:
                cons_loss = 0
            
            mk = p['time'].strftime('%Y-%m')
            monthly.setdefault(mk, {'pnl':0,'count':0,'wins':0})
            monthly[mk]['pnl'] += pnl
            monthly[mk]['count'] += 1
            if pnl > 0: monthly[mk]['wins'] += 1
            
            trades.append({
                'time': p['time'].strftime('%Y-%m-%d %H:%M'),
                'dir': p['dir'], 'entry': round(p['entry'],4),
                'exit': round(exit_p,4), 'pnl_pct': round(pnl*100,2), 'reason': reason
            })
            state['pos'] = None
            state['last_exit_kl'] = int(df5['t'].iloc[i+1])
            continue
    
    # 开仓
    if sig and state['pos'] is None:
        entry = df5['o'].iloc[i+1]
        state['pos'] = {
            'dir': sig, 'entry': entry,
            'time': df5['dt'].iloc[i+1],
            'tp': entry*(1+TP_PCT) if sig=='LONG' else entry*(1-TP_PCT),
            'sl': entry*(1-SL_PCT) if sig=='LONG' else entry*(1+SL_PCT),
        }

# 末仓平掉
if state['pos']:
    p = state['pos']; lp = c5[-1]
    pnl = (lp-p['entry'])/p['entry'] if p['dir']=='LONG' else (p['entry']-lp)/p['entry']
    pnl_cum += pnl
    mk = p['time'].strftime('%Y-%m')
    monthly.setdefault(mk, {'pnl':0,'count':0,'wins':0})
    monthly[mk]['pnl'] += pnl; monthly[mk]['count'] += 1
    if pnl > 0: monthly[mk]['wins'] += 1
    trades.append({'time': p['time'].strftime('%Y-%m-%d %H:%M'), 'dir': p['dir'],
                   'entry': round(p['entry'],4), 'exit': round(lp,4),
                   'pnl_pct': round(pnl*100,2), 'reason': 'EOD'})

# ---- 汇总 ----
tt = len(trades)
w = sum(1 for t in trades if t['pnl_pct']>0)
l = sum(1 for t in trades if t['pnl_pct']<0)
wr = w/tt*100 if tt>0 else 0
aw = sum(t['pnl_pct'] for t in trades if t['pnl_pct']>0)/w if w>0 else 0
al = sum(t['pnl_pct'] for t in trades if t['pnl_pct']<0)/l if l>0 else 0

print(f"{'='*55}")
print(f"  HYPE 一年回测 | {df5['dt'].iloc[0].strftime('%Y-%m-%d')} ~ {df5['dt'].iloc[-1].strftime('%Y-%m-%d')}")
print(f"{'='*55}")
print(f"  总笔数: {tt}  |  {w}W / {l}L  |  胜率: {wr:.1f}%")
print(f"  累计收益: {pnl_cum*100:+.1f}%")
print(f"  最大回撤: {max_dd*100:.1f}%")
print(f"  最大连败: {max_cons_loss} 笔")
print(f"  均盈: {aw:+.2f}%  均亏: {al:+.2f}%")

print(f"\n  {'月份':<10} {'笔数':>4} {'胜率':>6} {'盈亏':>8}")
print(f"  {'-'*35}")
for m in sorted(monthly):
    d = monthly[m]
    mwr = d['wins']/d['count']*100 if d['count']>0 else 0
    print(f"  {m:<10} {d['count']:>4} {mwr:>5.0f}% {d['pnl']*100:>+7.1f}%")

# ---- 保存 ----
report = {
    'period': f"{df5['dt'].iloc[0]} ~ {df5['dt'].iloc[-1]}",
    'total_trades': tt, 'wins': w, 'losses': l, 'win_rate': round(wr,1),
    'total_pnl_pct': round(pnl_cum*100,1),
    'max_drawdown_pct': round(max_dd*100,1),
    'max_consecutive_losses': max_cons_loss,
    'avg_win_pct': round(aw,2), 'avg_loss_pct': round(al,2),
    'monthly': {m: {'trades': d['count'], 'win_rate': round(d['wins']/d['count']*100 if d['count'] else 0,1), 'pnl_pct': round(d['pnl']*100,1)} for m,d in sorted(monthly.items())},
    'trades': trades,
    'config': {'TP':'1.5%','SL':'2.0%','QTY':8,'LEVERAGE':'25x'},
}
with open(f'{DB}/hype_backtest.json','w') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"\n  已保存: {DB}/hype_backtest.json")
