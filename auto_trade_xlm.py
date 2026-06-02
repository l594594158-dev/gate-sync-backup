#!/usr/bin/env python3
"""
XLM合约 v6.1 — 15m EMA3/EMA10交叉 + ADX1h>23 3仓/边
- 15m扫描 | 15m EMA3/EMA10纯方向 | RSI(40,60) | ADX1h>23 | vol>3.0
- TP 2.5% / SL 4.0% / 25x逐仓 / 2000 XLM/仓
- 双向各3仓 (回测+133.0%/310笔/69.7%胜率/DD-40.1%/4亏月)
- v6.1: EMA3代替EMA5, ADX1h代替ADX15m, vol3.0, 3仓
"""
import ccxt
import requests
import pandas as pd
import ta
import time
import json
import os
from datetime import datetime
from entry_logger import log_entry
from sltp_guard import ensure_sltp

# ========== API 双Key架构 ==========
from api_config import READ_API_KEY, READ_SECRET, TRADE_API_KEY, TRADE_SECRET

read_binance = ccxt.binance({
    'apiKey': READ_API_KEY,
    'secret': READ_SECRET,
    'options': {'defaultType': 'swap', 'settle': 'usdt'}
})

trade_gate = ccxt.gate({
    'apiKey': TRADE_API_KEY,
    'secret': TRADE_SECRET,
    'options': {'defaultType': 'swap', 'settle': 'usdt'}
})

# ========== XLM专属参数 ==========
SYMBOL = 'XLM/USDT:USDT'
GATE_BASE_QTY = 1000                  # XLM 数量 (Gate: 10 XLM/张)
GATE_CONTRACT_SIZE = 10
def to_contracts(amt): return int(amt / GATE_CONTRACT_SIZE)
def to_base(contracts): return contracts * GATE_CONTRACT_SIZE
LEVERAGE = 25                          # 25x杠杆
BASE_DIR = '/root/liucangyang'
STATE_FILE = f'{BASE_DIR}/databases/state_xlm.json'
WORK_LOG = f'{BASE_DIR}/logs/work_log_xlm.txt'
NOTIFY_QUEUE = f'{BASE_DIR}/databases/notify_queue_xlm.json'
PAUSE_FLAG = f'{BASE_DIR}/databases/xlm_pause.flag'

# ========== 策略参数（回测: +194.1%含费 719笔 68.2%胜率 DD-51.8%）==========
STOP_LOSS_PCT = 4.0 / 100              # 4.0%止损
TAKE_PROFIT_PCT = 2.5 / 100            # 2.5%止盈
ADX1H_MIN = 23                         # 1h ADX下限 (v6.1)
VOL_MIN = 3.0                          # 15m量比下限 (v6.1)
MAX_POS_PER_SIDE = 3                   # 同向最多3仓 (v6.1)
POLL_INTERVAL = 1                      # 扫描间隔（秒）

# ========== 日志 ==========
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [XLM] {msg}")

def work_log(event, detail):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os.makedirs(os.path.dirname(WORK_LOG), exist_ok=True)
    with open(WORK_LOG, 'a') as f:
        f.write(f"[{ts}] [{event}] {detail}\n")

# ========== 状态管理（多仓数组）==========
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
    else:
        s = {}
    if 'long_pos' not in s: s['long_pos'] = []
    if 'short_pos' not in s: s['short_pos'] = []
    if not isinstance(s['long_pos'], list): s['long_pos'] = [s['long_pos']] if s['long_pos'] else []
    if not isinstance(s['short_pos'], list): s['short_pos'] = [s['short_pos']] if s['short_pos'] else []
    return s

def save_state(s):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(s, f)

# ========== 通知 ==========
def notify_alert(msg):
    ts = datetime.now().isoformat()
    try:
        os.makedirs(os.path.dirname(NOTIFY_QUEUE), exist_ok=True)
        queue = []
        if os.path.exists(NOTIFY_QUEUE):
            with open(NOTIFY_QUEUE) as f:
                queue = json.load(f)
        queue.append({'time': ts, 'msg': msg, 'sent': False})
        queue = queue[-50:]
        with open(NOTIFY_QUEUE, 'w') as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f'⚠️ 通知写入失败: {e}')

# ========== 数据获取 ==========
def get_data():
    """获取15m + 1h K线"""
    try:
        url15 = f'https://fapi.binance.com/fapi/v1/klines?symbol=XLMUSDT&interval=15m&limit=200'
        url1h = f'https://fapi.binance.com/fapi/v1/klines?symbol=XLMUSDT&interval=1h&limit=200'
        resp15 = requests.get(url15, timeout=5)
        resp1h = requests.get(url1h, timeout=5)
        k15 = resp15.json()
        k1h = resp1h.json()
        d15 = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in k15]
        d1h = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in k1h]
        return d15, d1h
    except Exception as e:
        log(f'获取K线失败: {e}')
        return [], []

# ========== 指标计算 ==========
def calc(df15, df1h):
    """v6.1: 15m EMA3/10 + 1h ADX映射"""
    close = df15['c']; high = df15['h']; low = df15['l']; volume = df15['v']
    lv = len(df15) - 1; closed_lv = max(0, lv - 1)

    # EMA3/EMA10 (lv-1闭K)
    ema3 = close.ewm(span=3, adjust=False).mean()
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema3_closed = ema3.iloc[closed_lv]
    ema10_closed = ema10.iloc[closed_lv]
    h1_bull = ema3_closed > ema10_closed

    # RSI (闭K)
    try: rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[closed_lv]
    except: rsi = 50

    # 1h ADX 映射 (取已完成1h bar)
    import numpy as np
    t15 = df15['t'].iloc[lv]
    i1h = np.clip(np.searchsorted(df1h['t'], t15 - 3600000, side='right') - 1, 0, len(df1h)-1)
    try:
        adx1h = ta.trend.ADXIndicator(df1h['h'], df1h['l'], df1h['c'], 14).adx().iloc[i1h]
        if np.isnan(adx1h): adx1h = 0
    except:
        adx1h = 0

    # 量比 (lv-1闭K / 20均量, 含自身)
    avg_vol = volume.iloc[max(0, closed_lv-19):closed_lv+1].mean()
    cur_vol = volume.iloc[closed_lv]
    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

    return {
        'price': close.iloc[lv],
        'close_lv': close.iloc[closed_lv],
        'h1_bull': h1_bull,
        'ema3': ema3_closed,
        'ema10': ema10_closed,
        'rsi': rsi,
        'adx1h': adx1h,
        'vol_ratio': vol_ratio,
        'open': df15['o'].iloc[lv]
    }

# ========== 信号判断 ==========
def check_entry(data):
    r = data
    entry_price = r['open']
    h1_bull = r['h1_bull']

    # ① ADX1h > 23 (v6.1)
    adx1h = r['adx1h']
    if adx1h <= ADX1H_MIN:
        return None, f"观望 | ADX1h={adx1h:.1f}≤{ADX1H_MIN}"

    # ② 量比 > 3.0x (v6.1)
    vol_ratio = r['vol_ratio']
    if vol_ratio < VOL_MIN:
        return None, f"观望 | 缩量 vol={vol_ratio:.1f}x<{VOL_MIN}"

    # ③ LONG: EMA3>EMA10 + RSI>40
    rsi = r['rsi']
    if h1_bull and rsi > 40:
        return ('LONG', f"【LONG】EMA3/10 RSI={rsi:.1f} ADX1h={adx1h:.1f} vol={vol_ratio:.1f}x",
                {'h1_bull': h1_bull, 'adx1h': adx1h, 'rsi': rsi, 'vol_ratio': vol_ratio, 'ema3': r['ema3'], 'ema10': r['ema10']})

    # SHORT: EMA3<EMA10 + RSI<60
    if (not h1_bull) and rsi < 60:
        return ('SHORT', f"【SHORT】EMA3/10 RSI={rsi:.1f} ADX1h={adx1h:.1f} vol={vol_ratio:.1f}x",
                {'h1_bull': h1_bull, 'adx1h': adx1h, 'rsi': rsi, 'vol_ratio': vol_ratio, 'ema3': r['ema3'], 'ema10': r['ema10']})

    dir_txt = '多' if h1_bull else '空'
    return None, f"观望 | {dir_txt} RSI={rsi:.1f}"

# ========== 多仓管理 ==========
def manage_positions(state, price, signal, reason, kl_time, indicators=None):
    surv_long = []
    for lp in state.get('long_pos', []):
        pnl = (price - lp['entry']) / lp['entry']
        if pnl <= -STOP_LOSS_PCT:
            log(f"🛑 LONG止损 | ${lp['entry']:.5f} → ${price:.5f} ({pnl*100:+.2f}%)")
            do_close('LONG', price, lp, '止损')
            state['last_exit_kl_time'] = kl_time
        elif pnl >= TAKE_PROFIT_PCT:
            log(f"✅ LONG止盈 | ${lp['entry']:.5f} → ${price:.5f} ({pnl*100:+.2f}%)")
            do_close('LONG', price, lp, '止盈')
            state['last_exit_kl_time'] = kl_time
        else:
            surv_long.append(lp)
    state['long_pos'] = surv_long

    surv_short = []
    for sp in state.get('short_pos', []):
        pnl = (sp['entry'] - price) / sp['entry']
        if pnl <= -STOP_LOSS_PCT:
            log(f"🛑 SHORT止损 | ${sp['entry']:.5f} → ${price:.5f} ({pnl*100:+.2f}%)")
            do_close('SHORT', price, sp, '止损')
            state['last_exit_kl_time'] = kl_time
        elif pnl >= TAKE_PROFIT_PCT:
            log(f"✅ SHORT止盈 | ${sp['entry']:.5f} → ${price:.5f} ({pnl*100:+.2f}%)")
            do_close('SHORT', price, sp, '止盈')
            state['last_exit_kl_time'] = kl_time
        else:
            surv_short.append(sp)
    state['short_pos'] = surv_short
    save_state(state)

    if kl_time <= state.get('last_exit_kl_time', 0):
        return

    if signal == 'LONG':
        if len(state.get('long_pos', [])) >= MAX_POS_PER_SIDE:
            log(f"⏭ LONG跳过 | 已有{len(state['long_pos'])}仓")
            return
        ep = do_open('LONG', price, reason)
        if ep:
            if indicators:
                indicators['tp_price'] = ep * (1 + TAKE_PROFIT_PCT)
                indicators['sl_price'] = ep * (1 - STOP_LOSS_PCT)
                log_entry('XLM', 'LONG', ep, indicators)
            state.setdefault('long_pos', []).append(
                {'entry': ep, 'signal': reason, 'open_time': datetime.now().isoformat()})
            save_state(state)
    elif signal == 'SHORT':
        if len(state.get('short_pos', [])) >= MAX_POS_PER_SIDE:
            log(f"⏭ SHORT跳过 | 已有{len(state['short_pos'])}仓")
            return
        ep = do_open('SHORT', price, reason)
        if ep:
            if indicators:
                indicators['tp_price'] = ep * (1 - TAKE_PROFIT_PCT)
                indicators['sl_price'] = ep * (1 + STOP_LOSS_PCT)
                log_entry('XLM', 'SHORT', ep, indicators)
            state.setdefault('short_pos', []).append(
                {'entry': ep, 'signal': reason, 'open_time': datetime.now().isoformat()})
            save_state(state)

# ========== 开仓执行 ==========
def do_open(direction, price, reason):
    try:
        ticker = trade_gate.fetch_ticker(SYMBOL)
        gate_price = ticker['last']
        if abs(gate_price - price) / price > 0.01:
            log(f"🛡 Gate价差 | Binance:{price:.5f} Gate:{gate_price:.5f} | 拒绝")
            return False

        side = 'buy' if direction == 'LONG' else 'sell'
        contracts = to_contracts(GATE_BASE_QTY)
        order = trade_gate.create_order(SYMBOL, 'market', side, contracts)
        entry_price = order.get('average', price)

        current_count = len(load_state().get(direction.lower()+'_pos', []))
        log(f"🚀 {direction}开仓#{current_count+1} | {reason} | ${entry_price:.5f} | {GATE_BASE_QTY}XLM")

        msg = (f"🟢 XLM {direction}开仓\n"
               f"${entry_price:,.5f} | {GATE_BASE_QTY}XLM | {LEVERAGE}x\n"
               f"TP:{TAKE_PROFIT_PCT*100:.1f}% SL:{STOP_LOSS_PCT*100:.1f}%\n{reason}")
        notify_alert(msg)
        work_log("开仓", f"{direction} | ${entry_price:.5f} | {GATE_BASE_QTY}XLM | #{current_count+1} | {reason}")
        return entry_price
    except Exception as e:
        log(f"❌ {direction}开仓失败: {e}")
        work_log("错误", f"开仓失败: {e}")
        return None

# ========== 平仓执行 ==========
def do_close(direction, price, pos_data, reason):
    try:
        close_side = 'sell' if direction == 'LONG' else 'buy'
        positions = trade_gate.fetch_positions()
        qty = 0
        for p in positions:
            if p.get('symbol') == SYMBOL and float(p.get('contracts', 0)) > 0:
                side_check = 'LONG' if p.get('side') == 'long' else 'SHORT'
                if side_check == direction:
                    qty += int(p['contracts'])
        if qty == 0:
            log(f"⚠️ 未找到{direction}持仓")
            return

        close_qty = min(to_contracts(GATE_BASE_QTY), qty)
        order = trade_gate.create_order(SYMBOL, 'market', close_side, close_qty, None, {'reduce_only': True})
        close_price = order.get('average', price)

        if direction == 'LONG':
            pnl_pct = (close_price - pos_data['entry']) / pos_data['entry'] * 100
        else:
            pnl_pct = (pos_data['entry'] - close_price) / pos_data['entry'] * 100

        log(f"✅ {direction}平仓 | ${close_price:.5f} | {pnl_pct:+.2f}% | {reason}")
        msg = f"{'🟢' if pnl_pct > 0 else '🔴'} XLM {direction}{reason}\n${close_price:,.5f} | {pnl_pct:+.2f}%"
        notify_alert(msg)
        work_log(reason, f"{direction} | PnL:{pnl_pct:+.2f}%")
    except Exception as e:
        log(f"❌ 平仓失败: {e}")
        work_log("错误", f"平仓失败: {e}")

# ========== 交易所同步 ==========
def sync_state(state):
    try:
        positions = trade_gate.fetch_positions(symbols=[SYMBOL])
    except:
        return False

    exchange_long = []; exchange_short = []
    for p in positions:
        if p.get('symbol') != SYMBOL: continue
        qty = int(float(p.get('contracts', 0)))
        if qty <= 0: continue
        side = p.get('side', 'long')
        entry = float(p.get('entryPrice', 0))
        for _ in range(qty // to_contracts(GATE_BASE_QTY)):
            pos = {'entry': entry, 'signal': '交易所恢复', 'open_time': datetime.now().isoformat()}
            if side == 'long':
                exchange_long.append(pos)
            else:
                exchange_short.append(pos)

    if exchange_long:
        state['long_pos'] = exchange_long[:MAX_POS_PER_SIDE]
        log(f"🔄 恢复 {len(state['long_pos'])} LONG仓")
    if exchange_short:
        state['short_pos'] = exchange_short[:MAX_POS_PER_SIDE]
        log(f"🔄 恢复 {len(state['short_pos'])} SHORT仓")
    save_state(state)
    return bool(exchange_long or exchange_short)

# ========== 状态显示 ==========
def print_status(data, state):
    r = data; price = r['price']; rsi = r['rsi']; adx1h = r['adx1h']; vol = r['vol_ratio']
    dir_txt = '📈多' if r['h1_bull'] else '📉空'
    now = datetime.now().strftime('%H:%M:%S')
    print(f"\n╔══ XLM v6.1 EMA3/10 15m {now} ═══")
    print(f"║ 💰 {price:>10.5f} | RSI:{rsi:.1f} | EMA3:{r['ema3']:.5f} EMA10:{r['ema10']:.5f}")
    print(f"║ {dir_txt} | ADX1h:{adx1h:.1f} | vol:{vol:.1f}x")
    lp = state.get('long_pos', []); sp = state.get('short_pos', [])
    if lp:
        for i, p in enumerate(lp):
            pnl = (price - p['entry']) / p['entry'] * 100
            print(f"║ 🟢 LONG#{i+1} ${p['entry']:.5f} | {pnl:+.2f}%")
    if sp:
        for i, p in enumerate(sp):
            pnl = (p['entry'] - price) / p['entry'] * 100
            print(f"║ 🔴 SHORT#{i+1} ${p['entry']:.5f} | {pnl:+.2f}%")
    if not lp and not sp:
        _, obs = check_entry(data)
        print(f"║ ⚪ {obs[:65]}")
    print(f"╚══════════════════════════════════╝")

# ========== 主循环 ==========
def main():
    log(f"🚀 XLM v6.1 EMA3/10 启动 | {LEVERAGE}x | {GATE_BASE_QTY}XLM/仓 | {MAX_POS_PER_SIDE}仓/边")
    log(f"策略: 15m EMA3/10 | TP{TAKE_PROFIT_PCT*100:.1f}%/SL{STOP_LOSS_PCT*100:.1f}% | ADX1h>{ADX1H_MIN} vol>{VOL_MIN}")
    log(f"回测: +133.0%/310笔/69.7%/DD-40.1%/4亏月")

    try:
        trade_gate.set_margin_mode('isolated', SYMBOL)
        log(f"逐仓模式")
    except Exception as e:
        log(f"逐仓: {e}")
    try:
        trade_gate.set_leverage(LEVERAGE, SYMBOL)
        log(f"杠杆: {LEVERAGE}x")
    except Exception as e:
        log(f"杠杆: {e}")

    state = load_state()
    sync_state(state)

    while True:
        try:
            klines, klines_1h = get_data()
            if not klines or not klines_1h:
                time.sleep(POLL_INTERVAL)
                continue

            df15 = pd.DataFrame(klines, columns=['t','o','h','l','c','v'])
            df1h = pd.DataFrame(klines_1h, columns=['t','o','h','l','c','v'])
            r = calc(df15, df1h)

            state = load_state()
            price = r['price']

            result = check_entry(r)
            if result[0] is not None:
                sig, reason, indicators = result
            else:
                sig, reason = result
                indicators = None

            # 冷却期
            current_kl = int(df15['t'].iloc[-2])
            if sig and current_kl <= state.get('last_exit_kl_time', 0):
                sig = None; reason = f"冷却中"

            # 暂停开仓
            if sig and os.path.exists(PAUSE_FLAG):
                log(f"⏸ 暂停 | {PAUSE_FLAG}")
                sig = None; reason = f"暂停"

            # 仓位保护锁
            if sig:
                total_ct = 0
                try:
                    for p in trade_gate.fetch_positions(symbols=[SYMBOL]):
                        if p.get('side') == ('long' if sig == 'LONG' else 'short'):
                            total_ct += int(float(p.get('contracts', 0)))
                except: pass
                if total_ct >= to_contracts(GATE_BASE_QTY) * MAX_POS_PER_SIDE:
                    log(f"🔒 仓位已满 | {sig} {total_ct}张")
                    sig = None; reason = f"仓位已满"

            manage_positions(state, price, sig, reason, current_kl, indicators)
            # 双保险: 重挂条件单
            if state.get("long_pos"):
                ensure_sltp(trade_gate, SYMBOL, "LONG", state["long_pos"], TAKE_PROFIT_PCT, STOP_LOSS_PCT, GATE_CONTRACT_SIZE, log_fn=log)
            if state.get("short_pos"):
                ensure_sltp(trade_gate, SYMBOL, "SHORT", state["short_pos"], TAKE_PROFIT_PCT, STOP_LOSS_PCT, GATE_CONTRACT_SIZE, log_fn=log)
            print_status(r, state)
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log("🛑 停止")
            break
        except Exception as e:
            log(f"❌ {e}")
            import traceback; traceback.print_exc()
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
