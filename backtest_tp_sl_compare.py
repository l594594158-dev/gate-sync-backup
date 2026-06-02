#!/usr/bin/env python3
"""对比 TP1.5/SL2.0 vs TP1.5/SL1.5 盈利率"""
import pandas as pd, ta, numpy as np

DB = '/root/liucangyang/databases'
df5 = pd.read_pickle(f'{DB}/hype_5m.pkl')
df1 = pd.read_pickle(f'{DB}/hype_1h.pkl')
df4 = pd.read_pickle(f'{DB}/hype_4h.pkl')
df5['dt'] = pd.to_datetime(df5['t'], unit='ms')
df1['dt'] = pd.to_datetime(df1['t'], unit='ms')
df4['dt'] = pd.to_datetime(df4['t'], unit='ms')

# 预计算
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

c5 = df5['c'].values; v5 = df5['v'].values; n = len(df5)

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

GAP_PCT = 1.0/100
sma5_raw = pd.Series(c5).rolling(20).mean().values

def run_backtest(tp_pct, sl_pct):
    state = {'long': None, 'short': None, 'last_exit_kl': 0}
    pnl_cum = 0.0; peak = 0.0; max_dd = 0.0
    cons_loss = 0; max_cons_loss = 0
    wins = 0; losses = 0
    monthly_pnl = {}

    for i in range(100, n):
        if i <= state['last_exit_kl']:
            continue
        dt = df5['dt'].iloc[i]
        month_key = dt.strftime('%Y-%m')
        monthly_pnl.setdefault(month_key, 0.0)

        # === 出场检查 ===
        for pos_dir, pos_key in [('LONG', 'long'), ('SHORT', 'short')]:
            p = state[pos_key]
            if p is None:
                continue
            if pos_dir == 'LONG':
                pnl = (c5[i] - p['entry']) / p['entry']
                hit_tp = c5[i] >= p['entry'] * (1 + tp_pct)
                hit_sl = c5[i] <= p['entry'] * (1 - sl_pct)
            else:
                pnl = (p['entry'] - c5[i]) / p['entry']
                hit_tp = c5[i] <= p['entry'] * (1 - tp_pct)
                hit_sl = c5[i] >= p['entry'] * (1 + sl_pct)

            if hit_tp or hit_sl:
                exit_p = p['entry'] * (1 + tp_pct) if hit_tp else p['entry'] * (1 - sl_pct) if pos_dir == 'LONG' else p['entry'] * (1 + sl_pct) if hit_sl else p['entry'] * (1 - tp_pct)
                # use actual close if crossed
                if hit_tp and pos_dir == 'LONG':
                    exit_p = max(c5[i], p['entry'] * (1 + tp_pct))
                elif hit_tp and pos_dir == 'SHORT':
                    exit_p = min(c5[i], p['entry'] * (1 - tp_pct))
                elif hit_sl and pos_dir == 'LONG':
                    exit_p = min(c5[i], p['entry'] * (1 - sl_pct))
                else:
                    exit_p = max(c5[i], p['entry'] * (1 + sl_pct))

                pnl_val = (exit_p - p['entry']) / p['entry'] if pos_dir == 'LONG' else (p['entry'] - exit_p) / p['entry']
                pnl_cum += pnl_val
                peak = max(peak, pnl_cum)
                max_dd = max(max_dd, peak - pnl_cum)
                if pnl_val > 0:
                    wins += 1; cons_loss = 0
                else:
                    losses += 1; cons_loss += 1
                    max_cons_loss = max(max_cons_loss, cons_loss)
                monthly_pnl[month_key] += pnl_val
                state[pos_key] = None
                state['last_exit_kl'] = i  # 下根K线才能开仓

        # === 入场信号 ===
        if state['last_exit_kl'] >= i:
            continue

        bull1h = df5['bull1h'].iloc[i]
        bull4h = df5['bull4h'].iloc[i]
        adx1h = df5['adx1h'].iloc[i]
        adx4h = df5['adx4h'].iloc[i]
        rsi_val = rsi5_raw[i]
        sma_val = sma5_raw[i]
        vr = vol_ratio[i]

        if np.isnan(adx1h) or np.isnan(adx4h) or np.isnan(rsi_val) or np.isnan(sma_val) or np.isnan(vr):
            continue

        # 六条件
        if adx1h <= 20 or adx4h >= 55:
            continue
        if abs(c5[i] - sma_val) / sma_val > 0.015:
            continue
        if vr < 1.0:
            continue

        for pos_dir, pos_key, bull_cond, rsi_cond, gap_check in [
            ('LONG', 'long', bull1h and bull4h, rsi_val > 40,
             state['short'] is not None and abs(c5[i] - state['short']['entry']) / state['short']['entry'] <= GAP_PCT),
            ('SHORT', 'short', not bull1h and not bull4h, rsi_val < 60,
             state['long'] is not None and abs(c5[i] - state['long']['entry']) / state['long']['entry'] <= GAP_PCT)
        ]:
            if state[pos_key] is not None:
                continue
            if gap_check:
                continue
            if not bull_cond:
                continue
            if not rsi_cond:
                continue
            state[pos_key] = {'entry': c5[i], 'kl': i}

    total_trades = wins + losses
    win_rate = wins / total_trades * 100 if total_trades > 0 else 0
    annual_return = pnl_cum  # 近一年

    # 月度统计
    neg_months = sum(1 for v in monthly_pnl.values() if v < 0)
    total_months = len(monthly_pnl)

    return {
        'tp': tp_pct*100, 'sl': sl_pct*100,
        'total_return': f'{pnl_cum*100:+.1f}%',
        'win_rate': f'{win_rate:.1f}%',
        'wins': wins, 'losses': losses, 'total_trades': total_trades,
        'max_dd': f'{max_dd*100:.1f}%',
        'max_cons_loss': max_cons_loss,
        'neg_months': f'{neg_months}/{total_months}',
        'expectancy': f'{pnl_cum/total_trades*100:.3f}%' if total_trades > 0 else 'N/A'
    }

print("=" * 55)
print("  HYPE 近一年回测: TP 1.5%/SL 2.0% vs TP 1.5%/SL 1.5%")
print("=" * 55)

r1 = run_backtest(1.5/100, 2.0/100)
r2 = run_backtest(1.5/100, 1.5/100)

for label, r in [("当前: TP 1.5% / SL 2.0%", r1), ("提议: TP 1.5% / SL 1.5%", r2)]:
    print(f"\n--- {label} ---")
    print(f"  累计收益: {r['total_return']}")
    print(f"  期望值/笔: {r['expectancy']}")
    print(f"  胜率: {r['win_rate']}  ({r['wins']}W / {r['losses']}L / {r['total_trades']}笔)")
    print(f"  最大回撤: {r['max_dd']}")
    print(f"  最大连败: {r['max_cons_loss']}")
    print(f"  亏损月份: {r['neg_months']}")

# 结论
print("\n" + "=" * 55)
ret1 = float(r1['total_return'].replace('%','').replace('+',''))
ret2 = float(r2['total_return'].replace('%','').replace('+',''))
exp1 = float(r1['expectancy'].replace('%',''))
exp2 = float(r2['expectancy'].replace('%',''))
dd1 = float(r1['max_dd'].replace('%',''))
dd2 = float(r2['max_dd'].replace('%',''))

print(f"  收益差: {ret2 - ret1:+.1f}%")
print(f"  期望差: {exp2 - exp1:+.3f}%/笔")
print(f"  回撤差: {dd2 - dd1:+.1f}%")
if ret2 > ret1:
    print("  → TP 1.5%/SL 1.5% 盈利率更高 ✓")
else:
    print("  → TP 1.5%/SL 2.0% 盈利率更高")
