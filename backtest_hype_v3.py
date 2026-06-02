#!/usr/bin/env python3
"""
HYPE 一年回测 v3 — 双向持仓 + 多空间隔>1% + 出场后下根K线开仓
"""
import pandas as pd, ta, json, numpy as np
from datetime import datetime

TP_PCT, SL_PCT, QTY = 1.5/100, 2.0/100, 8
GAP_PCT = 1.0/100  # 多空开仓间隔 >1%

DB = '/root/liucangyang/databases'
df5 = pd.read_pickle(f'{DB}/hype_5m.pkl')
df1 = pd.read_pickle(f'{DB}/hype_1h.pkl')
df4 = pd.read_pickle(f'{DB}/hype_4h.pkl')
df5['dt'] = pd.to_datetime(df5['t'], unit='ms')
df1['dt'] = pd.to_datetime(df1['t'], unit='ms')
df4['dt'] = pd.to_datetime(df4['t'], unit='ms')

# ---- 预计算 ----
for name, df in [('1h', df1), ('4h', df4)]:
    c = df['c']; h = df['h']; l = df['l']
    df['sma20'] = ta.trend.SMAIndicator(c, 20).sma_indicator()
    df['adx'] = ta.trend.ADXIndicator(h, l, c, 14).adx()
    df['bull'] = c > df['sma20']

def map_closed(df_src, df_target, col_map):
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

sma5_raw = pd.Series(c5).rolling(20).mean().values

def wilder_rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full(len(close), np.nan); avg_loss = np.full(len(close), np.nan)
    avg_gain[period] = gain[1:period+1].mean()
    avg_loss[period] = loss[1:period+1].mean()
    for i in range(period+1, len(close)):
        avg_gain[i] = (avg_gain[i-1]*13 + gain[i])/14
        avg_loss[i] = (avg_loss[i-1]*13 + loss[i])/14
    rs = avg_gain / avg_loss
    return 100 - 100/(1+rs)

rsi5_raw = wilder_rsi(c5)

vol_ratio = np.full(n, np.nan)
for i in range(21, n):
    clv = i - 1
    vol_ratio[i] = v5[clv] / v5[max(0,clv-19):clv+1].mean()

# ---- 回测 ----
state = {'long': None, 'short': None, 'last_exit_kl': 0}
trades = []
monthly = {}
pnl_cum = 0.0; peak = 0.0; max_dd = 0.0
cons_loss = 0; max_cons_loss = 0

def process_exit(pos_dir, p, exit_p, reason, kl_time, dt):
    global pnl_cum, peak, max_dd, cons_loss, max_cons_loss
    pnl = (exit_p - p['entry'])/p['entry'] if pos_dir=='LONG' else (p['entry']-exit_p)/p['entry']
    pnl_cum += pnl
    peak = max(peak, pnl_cum)
    max_dd = max(max_dd, peak - pnl_cum)
    
    if pnl < 0:
        cons_loss += 1
        max_cons_loss = max(max_cons_loss, cons_loss)
    else:
        cons_loss = 0
    
    mk = dt.strftime('%Y-%m')
    monthly.setdefault(mk, {'pnl':0,'count':0,'wins':0})
    monthly[mk]['pnl'] += pnl
    monthly[mk]['count'] += 1
    if pnl > 0: monthly[mk]['wins'] += 1
    
    trades.append({
        'time': dt.strftime('%Y-%m-%d %H:%M'), 'dir': pos_dir,
        'entry': round(p['entry'],4), 'exit': round(exit_p,4),
        'pnl_pct': round(pnl*100,2), 'reason': reason
    })

for i in range(21, n - 1):
    kl_ts = int(df5['t'].iloc[i])
    
    sma5 = sma5_raw[i-1]; rsi5 = rsi5_raw[i-1]; vr = vol_ratio[i]
    adx1 = df5['adx1h'].iloc[i]; adx4 = df5['adx4h'].iloc[i]
    bull1 = df5['bull1h'].iloc[i]; price_live = c5[i]
    
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
    
    # 冷却: 出场后下根K线才允许开仓
    if sig:
        if kl_ts <= state['last_exit_kl']:
            sig = None
    
    # 仓位锁: 同方向已持仓则跳过
    if sig == 'LONG' and state['long'] is not None:
        sig = None
    if sig == 'SHORT' and state['short'] is not None:
        sig = None
    
    # 双向间隔锁: 开反向仓需价差 >1%
    if sig == 'LONG' and state['short'] is not None:
        gap = abs(price_live - state['short']['entry']) / state['short']['entry']
        if gap <= GAP_PCT:
            sig = None
    if sig == 'SHORT' and state['long'] is not None:
        gap = abs(price_live - state['long']['entry']) / state['long']['entry']
        if gap <= GAP_PCT:
            sig = None
    
    # ---- 出场检查 ----
    if state['long'] is not None:
        p = state['long']; nc = df5.iloc[i+1]
        if nc['h'] >= p['tp']:
            process_exit('LONG', p, p['tp'], 'TP', int(df5['t'].iloc[i+1]), df5['dt'].iloc[i+1])
            state['long'] = None
            state['last_exit_kl'] = int(df5['t'].iloc[i+1])
        elif nc['l'] <= p['sl']:
            process_exit('LONG', p, p['sl'], 'SL', int(df5['t'].iloc[i+1]), df5['dt'].iloc[i+1])
            state['long'] = None
            state['last_exit_kl'] = int(df5['t'].iloc[i+1])
    
    if state['short'] is not None:
        p = state['short']; nc = df5.iloc[i+1]
        if nc['l'] <= p['tp']:
            process_exit('SHORT', p, p['tp'], 'TP', int(df5['t'].iloc[i+1]), df5['dt'].iloc[i+1])
            state['short'] = None
            state['last_exit_kl'] = int(df5['t'].iloc[i+1])
        elif nc['h'] >= p['sl']:
            process_exit('SHORT', p, p['sl'], 'SL', int(df5['t'].iloc[i+1]), df5['dt'].iloc[i+1])
            state['short'] = None
            state['last_exit_kl'] = int(df5['t'].iloc[i+1])
    
    # 跳过出场后的开仓（同K线不出又进）
    if state.get('_skip_open'):
        del state['_skip_open']
        continue
    
    # ---- 开仓 ----
    if sig is None:
        continue
    
    entry = df5['o'].iloc[i+1]
    pos = {'entry': entry, 'time': df5['dt'].iloc[i+1],
           'tp': entry*(1+TP_PCT) if sig=='LONG' else entry*(1-TP_PCT),
           'sl': entry*(1-SL_PCT) if sig=='LONG' else entry*(1+SL_PCT)}
    
    if sig == 'LONG':
        state['long'] = pos
    else:
        state['short'] = pos

# 末仓平掉
for d, key in [('LONG','long'),('SHORT','short')]:
    if state[key]:
        p = state[key]; lp = c5[-1]
        pnl = (lp-p['entry'])/p['entry'] if d=='LONG' else (p['entry']-lp)/p['entry']
        pnl_cum += pnl
        mk = p['time'].strftime('%Y-%m')
        monthly.setdefault(mk, {'pnl':0,'count':0,'wins':0})
        monthly[mk]['pnl'] += pnl; monthly[mk]['count'] += 1
        if pnl > 0: monthly[mk]['wins'] += 1
        trades.append({'time': p['time'].strftime('%Y-%m-%d %H:%M'), 'dir': d,
                       'entry': round(p['entry'],4), 'exit': round(lp,4),
                       'pnl_pct': round(pnl*100,2), 'reason': 'EOD'})

# ---- 汇总 ----
tt = len(trades)
w = sum(1 for t in trades if t['pnl_pct']>0)
l = sum(1 for t in trades if t['pnl_pct']<0)
wr = w/tt*100 if tt>0 else 0
aw = sum(t['pnl_pct'] for t in trades if t['pnl_pct']>0)/w if w>0 else 0
al = sum(t['pnl_pct'] for t in trades if t['pnl_pct']<0)/l if l>0 else 0

# 双向同持统计
dual_count = 0
long_trades = [t for t in trades if t['dir']=='LONG']
short_trades = [t for t in trades if t['dir']=='SHORT']

print(f"{'='*60}")
print(f"  HYPE 一年回测 v3 — 双向持仓 | 间隔>{GAP_PCT*100:.0f}% | 出场后下根K开仓")
print(f"  {df5['dt'].iloc[0].strftime('%Y-%m-%d')} ~ {df5['dt'].iloc[-1].strftime('%Y-%m-%d')}")
print(f"{'='*60}")
print(f"  总笔数: {tt}  |  {w}W / {l}L  |  胜率: {wr:.1f}%")
print(f"  LONG: {len(long_trades)}笔  SHORT: {len(short_trades)}笔")
print(f"  累计收益: {pnl_cum*100:+.1f}%")
print(f"  最大回撤: {max_dd*100:.1f}%")
print(f"  最大连败: {max_cons_loss} 笔")
print(f"  均盈: {aw:+.2f}%  均亏: {al:+.2f}%")

print(f"\n  {'月份':<10} {'笔数':>4} {'LONG':>5} {'SHORT':>6} {'胜率':>6} {'盈亏':>8}")
print(f"  {'-'*45}")
for m in sorted(monthly):
    d = monthly[m]
    mwr = d['wins']/d['count']*100 if d['count']>0 else 0
    ml = sum(1 for t in trades if t['dir']=='LONG' and t['time'][:7]==m)
    ms = sum(1 for t in trades if t['dir']=='SHORT' and t['time'][:7]==m)
    print(f"  {m:<10} {d['count']:>4} {ml:>5} {ms:>6} {mwr:>5.0f}% {d['pnl']*100:>+7.1f}%")

# ---- 保存 ----
report = {
    'version': 'v3_bidirectional',
    'config': {'TP':'1.5%','SL':'2.0%','QTY':8,'LEVERAGE':'25x','reverse_gap':'>1%','cooldown':'next_kline'},
    'period': f"{df5['dt'].iloc[0]} ~ {df5['dt'].iloc[-1]}",
    'total_trades': tt, 'long_trades': len(long_trades), 'short_trades': len(short_trades),
    'wins': w, 'losses': l, 'win_rate': round(wr,1),
    'total_pnl_pct': round(pnl_cum*100,1),
    'max_drawdown_pct': round(max_dd*100,1),
    'max_consecutive_losses': max_cons_loss,
    'avg_win_pct': round(aw,2), 'avg_loss_pct': round(al,2),
    'monthly': {m: {'trades': d['count'], 'win_rate': round(d['wins']/d['count']*100 if d['count'] else 0,1), 'pnl_pct': round(d['pnl']*100,1)} for m,d in sorted(monthly.items())},
    'trades': trades,
}
with open(f'{DB}/hype_backtest_v3.json','w') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"\n  已保存: {DB}/hype_backtest_v3.json")
