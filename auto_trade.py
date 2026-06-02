#!/usr/bin/env python3
"""
BTC合约 EMA5/EMA10 1h交叉策略 v4.0
- 1h扫描 | 1h EMA5/EMA10交叉方向 | RSI/vol_ratio | ADX1h>20 | ADX4h<50
- TP 2.0% / SL 5.0% / 25x逐仓 / 0.005 BTC/仓 (50张, 0.0001 BTC/张)
- 双向各3仓
- 回测: +111.2%含费 / 233笔 / 79.8%胜率 / DD-24.4% / 仅1亏损月
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

# ========== BTC专属参数 ==========
SYMBOL = 'BTC/USDT:USDT'
GATE_BASE_QTY = 0.005                # BTC 每仓基础数量
GATE_CONTRACT_SIZE = 0.0001          # Gate 1张=0.0001BTC
def to_contracts(btc): return int(btc / GATE_CONTRACT_SIZE)
def to_btc(contracts): return contracts * GATE_CONTRACT_SIZE
LEVERAGE = 25
BASE_DIR = '/root/liucangyang'
STATE_FILE = f'{BASE_DIR}/databases/state_btc.json'
WORK_LOG = f'{BASE_DIR}/logs/work_log_btc.txt'
NOTIFY_QUEUE = f'{BASE_DIR}/databases/notify_queue_btc.json'
PAUSE_FLAG = f'{BASE_DIR}/databases/btc_pause.flag'

# ========== 策略参数（回测: +111.2%含费 233笔 79.8%胜率 DD-24.4%）==========
STOP_LOSS_PCT = 5.0 / 100              # 5.0%止损
TAKE_PROFIT_PCT = 2.0 / 100            # 2.0%止盈
ADX1H_MIN = 20                         # 1h ADX下限
ADX4H_MAX = 50                         # 4h ADX上限
VOL_MIN = 3.0                          # 1h量比下限（vol/20均量）
MAX_POS_PER_SIDE = 3                   # 同向最多3仓
POLL_INTERVAL = 1                      # 扫描间隔（秒）

# ========== 日志 ==========
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [BTC] {msg}")

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
    result = []
    for tf, limit in [('1h', 200), ('4h', 200)]:
        try:
            url = f'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval={tf}&limit={limit}'
            resp = requests.get(url, timeout=5)
            klines = resp.json()
            data = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in klines]
            result.append(data)
        except Exception as e:
            log(f'获取{tf}失败: {e}')
            result.append([])
    return result

# ========== 指标计算 ==========
def calc_1h(df):
    close = df['c']; high = df['h']; low = df['l']; volume = df['v']
    lv = len(df) - 1; closed_lv = max(0, lv - 1)

    ema5 = close.ewm(span=5, adjust=False).mean()
    ema10 = close.ewm(span=10, adjust=False).mean()
    h1_bull = ema5.iloc[closed_lv] > ema10.iloc[closed_lv]

    try: rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[closed_lv]
    except: rsi = 50

    try: adx1h = ta.trend.ADXIndicator(high, low, close, 14).adx().iloc[closed_lv]
    except: adx1h = 25

    avg_vol = volume.iloc[max(0, closed_lv-19):closed_lv+1].mean()
    cur_vol = volume.iloc[closed_lv]
    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

    return {
        'price': close.iloc[lv], 'close_lv': close.iloc[closed_lv],
        'h1_bull': h1_bull, 'ema5': ema5.iloc[closed_lv], 'ema10': ema10.iloc[closed_lv],
        'rsi': rsi, 'adx1h': adx1h, 'vol_ratio': vol_ratio,
        'open': df['o'].iloc[lv]
    }

def calc_4h(df):
    close = df['c']; high = df['h']; low = df['l']
    closed_lv = max(0, len(df) - 2)
    try: adx4h = ta.trend.ADXIndicator(high, low, close, 14).adx().iloc[closed_lv]
    except: adx4h = 30
    return {'adx4h': adx4h}

# ========== 信号判断 ==========
def check_entry(data):
    r1 = data['1h']; r4 = data['4h']
    entry_price = r1['open']
    h1_bull = r1['h1_bull']

    adx1h = r1['adx1h']
    if adx1h <= ADX1H_MIN:
        return None, f"观望 | 1hADX={adx1h:.1f}≤{ADX1H_MIN}"

    adx4h = r4['adx4h']
    if adx4h >= ADX4H_MAX:
        return None, f"观望 | 4hADX={adx4h:.1f}≥{ADX4H_MAX}"

    vol_ratio = r1['vol_ratio']
    if vol_ratio < VOL_MIN:
        return None, f"观望 | 缩量 vol={vol_ratio:.1f}x<{VOL_MIN}"

    rsi1h = r1['rsi']
    if h1_bull and rsi1h > 40:
        return ('LONG', f"【LONG】EMA交叉 RSI={rsi1h:.1f} ADX1h={adx1h:.1f} vol={vol_ratio:.1f}x",
                {'h1_bull': h1_bull, 'adx1h': adx1h, 'adx4h': adx4h, 'rsi1h': rsi1h, 'vol_ratio': vol_ratio})

    if (not h1_bull) and rsi1h < 60:
        return ('SHORT', f"【SHORT】EMA交叉 RSI={rsi1h:.1f} ADX1h={adx1h:.1f} vol={vol_ratio:.1f}x",
                {'h1_bull': h1_bull, 'adx1h': adx1h, 'adx4h': adx4h, 'rsi1h': rsi1h, 'vol_ratio': vol_ratio})

    dir_txt = '多' if h1_bull else '空'
    return None, f"观望 | 1h{dir_txt} RSI={rsi1h:.1f}"

# ========== 多仓管理 ==========
def manage_positions(state, price, signal, reason, kl_time, indicators=None):
    surv_long = []
    for lp in state.get('long_pos', []):
        pnl = (price - lp['entry']) / lp['entry']
        if pnl <= -STOP_LOSS_PCT:
            log(f"🛑 LONG止损 | ${lp['entry']:.2f} → ${price:.2f} ({pnl*100:+.2f}%)")
            do_close('LONG', price, lp, '止损')
            state['last_exit_kl_time'] = kl_time
        elif pnl >= TAKE_PROFIT_PCT:
            log(f"✅ LONG止盈 | ${lp['entry']:.2f} → ${price:.2f} ({pnl*100:+.2f}%)")
            do_close('LONG', price, lp, '止盈')
            state['last_exit_kl_time'] = kl_time
        else:
            surv_long.append(lp)
    state['long_pos'] = surv_long

    surv_short = []
    for sp in state.get('short_pos', []):
        pnl = (sp['entry'] - price) / sp['entry']
        if pnl <= -STOP_LOSS_PCT:
            log(f"🛑 SHORT止损 | ${sp['entry']:.2f} → ${price:.2f} ({pnl*100:+.2f}%)")
            do_close('SHORT', price, sp, '止损')
            state['last_exit_kl_time'] = kl_time
        elif pnl >= TAKE_PROFIT_PCT:
            log(f"✅ SHORT止盈 | ${sp['entry']:.2f} → ${price:.2f} ({pnl*100:+.2f}%)")
            do_close('SHORT', price, sp, '止盈')
            state['last_exit_kl_time'] = kl_time
        else:
            surv_short.append(sp)
    state['short_pos'] = surv_short
    save_state(state)

    if kl_time <= state.get('last_exit_kl_time', 0):
        return
    if kl_time <= state.get('last_entry_kl_time', 0):
        return  # 同K线已开过仓,等换棒

    if signal == 'LONG':
        if len(state.get('long_pos', [])) >= MAX_POS_PER_SIDE:
            return
        ep = do_open('LONG', price, reason)
        if ep:
            if indicators:
                indicators['tp_price'] = ep * (1 + TAKE_PROFIT_PCT)
                indicators['sl_price'] = ep * (1 - STOP_LOSS_PCT)
                log_entry('BTC', 'LONG', ep, indicators)
            state['last_entry_kl_time'] = kl_time
            state.setdefault('long_pos', []).append(
                {'entry': ep, 'signal': reason, 'open_time': datetime.now().isoformat()})
            save_state(state)
    elif signal == 'SHORT':
        if len(state.get('short_pos', [])) >= MAX_POS_PER_SIDE:
            return
        ep = do_open('SHORT', price, reason)
        if ep:
            if indicators:
                indicators['tp_price'] = ep * (1 - TAKE_PROFIT_PCT)
                indicators['sl_price'] = ep * (1 + STOP_LOSS_PCT)
                log_entry('BTC', 'SHORT', ep, indicators)
            state['last_entry_kl_time'] = kl_time
            state.setdefault('short_pos', []).append(
                {'entry': ep, 'signal': reason, 'open_time': datetime.now().isoformat()})
            save_state(state)

# ========== 开仓执行 ==========
def do_open(direction, price, reason):
    try:
        ticker = trade_gate.fetch_ticker(SYMBOL)
        gate_price = ticker['last']
        if abs(gate_price - price) / price > 0.01:
            log(f"🛡 Gate价差 | Binance:{price:.2f} Gate:{gate_price:.2f} | 拒绝")
            return False

        side = 'buy' if direction == 'LONG' else 'sell'
        contracts = to_contracts(GATE_BASE_QTY)
        order = trade_gate.create_order(SYMBOL, 'market', side, contracts)
        entry_price = order.get('average', price)

        current_count = len(load_state().get(direction.lower()+'_pos', []))
        log(f"🚀 {direction}开仓#{current_count+1} | {reason} | ${entry_price:.2f} | {GATE_BASE_QTY}BTC")

        msg = (f"🟢 BTC {direction}开仓\n"
               f"${entry_price:,.2f} | {GATE_BASE_QTY}BTC | {LEVERAGE}x\n"
               f"TP:{TAKE_PROFIT_PCT*100:.1f}% SL:{STOP_LOSS_PCT*100:.1f}%\n{reason}")
        notify_alert(msg)
        work_log("开仓", f"{direction} | ${entry_price:.2f} | {GATE_BASE_QTY}BTC | #{current_count+1} | {reason}")
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

        log(f"✅ {direction}平仓 | ${close_price:.2f} | {pnl_pct:+.2f}% | {reason}")
        msg = f"{'🟢' if pnl_pct > 0 else '🔴'} BTC {direction}{reason}\n${close_price:,.2f} | {pnl_pct:+.2f}%"
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
        per_pos = to_contracts(GATE_BASE_QTY)
        for _ in range(qty // max(per_pos, 1)):
            pos = {'entry': entry, 'signal': '交易所恢复', 'open_time': datetime.now().isoformat()}
            if side == 'long': exchange_long.append(pos)
            else: exchange_short.append(pos)

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
    r1 = data['1h']; r4 = data['4h']
    price = r1['price']; rsi = r1['rsi']
    adx1h = r1['adx1h']; adx4h = r4['adx4h']; vol = r1['vol_ratio']
    dir_txt = '📈多' if r1['h1_bull'] else '📉空'
    now = datetime.now().strftime('%H:%M:%S')
    print(f"\n╔══ BTC v4.0 EMA交叉 1h {now} ═══")
    print(f"║ 💰 {price:>10.2f} | RSI:{rsi:.1f} | EMA5:{r1['ema5']:.2f} EMA10:{r1['ema10']:.2f}")
    print(f"║ 1h{dir_txt} | ADX1h:{adx1h:.1f} ADX4h:{adx4h:.1f} | vol:{vol:.1f}x")
    lp = state.get('long_pos', []); sp = state.get('short_pos', [])
    if lp:
        for i, p in enumerate(lp):
            pnl = (price - p['entry']) / p['entry'] * 100
            print(f"║ 🟢 LONG#{i+1} ${p['entry']:.2f} | {pnl:+.2f}%")
    if sp:
        for i, p in enumerate(sp):
            pnl = (p['entry'] - price) / p['entry'] * 100
            print(f"║ 🔴 SHORT#{i+1} ${p['entry']:.2f} | {pnl:+.2f}%")
    if not lp and not sp:
        _, obs = check_entry(data)
        print(f"║ ⚪ {obs[:78]}")
    print(f"╚════════════════════════════════════════════╝")

# ========== 主循环 ==========
def main():
    log(f"🚀 BTC v4.0 EMA交叉 启动 | {LEVERAGE}x | {GATE_BASE_QTY}BTC/仓 | {MAX_POS_PER_SIDE}仓/边")
    log(f"策略: 1h EMA5/10 | TP{TAKE_PROFIT_PCT*100:.1f}%/SL{STOP_LOSS_PCT*100:.1f}% | ADX>{ADX1H_MIN} 4h<{ADX4H_MAX} vol>{VOL_MIN}")
    log(f"回测: +111.2%/233笔/79.8%/DD-24.4%/1亏月")

    try:
        trade_gate.set_margin_mode('isolated', SYMBOL)
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
            k1h, k4h = get_data()
            if not k1h:
                time.sleep(POLL_INTERVAL)
                continue

            df1h = pd.DataFrame(k1h, columns=['t','o','h','l','c','v'])
            df4h = pd.DataFrame(k4h, columns=['t','o','h','l','c','v'])

            data = {'1h': calc_1h(df1h), '4h': calc_4h(df4h)}
            state = load_state()
            price = data['1h']['price']

            result = check_entry(data)
            sig, reason = result if result[0] is not None else (None, result[1])
            indicators = result[2] if result[0] is not None else None

            current_kl = int(df1h['t'].iloc[-2])
            if sig and current_kl <= state.get('last_exit_kl_time', 0):
                sig = None; reason = f"冷却中"

            if sig and os.path.exists(PAUSE_FLAG):
                log(f"⏸ 暂停 | {PAUSE_FLAG}")
                sig = None; reason = f"暂停"

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
            print_status(data, state)
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
