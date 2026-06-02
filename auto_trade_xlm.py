#!/usr/bin/env python3
"""
XLM合约 EMA5/EMA10 15m交叉策略 v5.0
- 15m扫描 | 15m EMA5/EMA10交叉方向 | RSI/vol_ratio | ADX15m>18
- TP 2.5% / SL 4.0% / 25x逐仓 / 1 XLM/仓 (10张, 10 XLM/张)
- 双向各2仓 | 无4h过滤
- 回测: +194.1%含费 / 719笔 / 68.2%胜率 / DD-51.8% / 仅2亏损月
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

# ========== XLM专属参数 ==========
SYMBOL = 'XLM/USDT:USDT'
GATE_BASE_QTY = 1                      # XLM 数量 (Gate: 10 XLM/张)
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
ADX_MIN = 18                           # 15m ADX下限
VOL_MIN = 2.5                          # 15m量比下限（vol/20均量）
MAX_POS_PER_SIDE = 2                   # 同向最多2仓
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
    """获取15m K线"""
    try:
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol=XLMUSDT&interval=15m&limit=200'
        resp = requests.get(url, timeout=5)
        klines = resp.json()
        data = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in klines]
        return data
    except Exception as e:
        log(f'获取15m失败: {e}')
        return []

# ========== 指标计算 ==========
def calc(df):
    """15m指标: EMA5/10交叉方向、RSI、ADX、量比"""
    close = df['c']; high = df['h']; low = df['l']; volume = df['v']
    lv = len(df) - 1; closed_lv = max(0, lv - 1)

    # EMA5/EMA10 交叉 (lv-1闭K)
    ema5 = close.ewm(span=5, adjust=False).mean()
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema5_closed = ema5.iloc[closed_lv]
    ema10_closed = ema10.iloc[closed_lv]
    h1_bull = ema5_closed > ema10_closed

    # RSI (闭K)
    try: rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[closed_lv]
    except: rsi = 50

    # ADX (闭K)
    try: adx = ta.trend.ADXIndicator(high, low, close, 14).adx().iloc[closed_lv]
    except: adx = 25

    # 量比 (lv-1闭K / 20均量)
    avg_vol = volume.iloc[max(0, closed_lv-19):closed_lv+1].mean()
    cur_vol = volume.iloc[closed_lv]
    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

    return {
        'price': close.iloc[lv],
        'close_lv': close.iloc[closed_lv],
        'h1_bull': h1_bull,
        'ema5': ema5_closed,
        'ema10': ema10_closed,
        'rsi': rsi,
        'adx': adx,
        'vol_ratio': vol_ratio,
        'open': df['o'].iloc[lv]
    }

# ========== 信号判断 ==========
def check_entry(data):
    r = data  # 单周期, 无嵌套
    entry_price = r['open']
    h1_bull = r['h1_bull']

    # ① ADX 15m > 18
    adx = r['adx']
    if adx <= ADX_MIN:
        return None, f"观望 | ADX={adx:.1f}≤{ADX_MIN}"

    # ② 量比 > 2.5x
    vol_ratio = r['vol_ratio']
    if vol_ratio < VOL_MIN:
        return None, f"观望 | 缩量 vol={vol_ratio:.1f}x<{VOL_MIN}"

    # ③ LONG: EMA多头 + RSI>40
    rsi = r['rsi']
    if h1_bull and rsi > 40:
        return ('LONG', f"【LONG】EMA交叉 RSI={rsi:.1f} ADX={adx:.1f} vol={vol_ratio:.1f}x",
                {'h1_bull': h1_bull, 'adx': adx, 'rsi': rsi, 'vol_ratio': vol_ratio, 'ema5': r['ema5'], 'ema10': r['ema10']})

    # SHORT: EMA空头 + RSI<60
    if (not h1_bull) and rsi < 60:
        return ('SHORT', f"【SHORT】EMA交叉 RSI={rsi:.1f} ADX={adx:.1f} vol={vol_ratio:.1f}x",
                {'h1_bull': h1_bull, 'adx': adx, 'rsi': rsi, 'vol_ratio': vol_ratio, 'ema5': r['ema5'], 'ema10': r['ema10']})

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
    r = data; price = r['price']; rsi = r['rsi']; adx = r['adx']; vol = r['vol_ratio']
    dir_txt = '📈多' if r['h1_bull'] else '📉空'
    now = datetime.now().strftime('%H:%M:%S')
    print(f"\n╔══ XLM v5.0 EMA交叉 15m {now} ═══")
    print(f"║ 💰 {price:>10.5f} | RSI:{rsi:.1f} | EMA5:{r['ema5']:.5f} EMA10:{r['ema10']:.5f}")
    print(f"║ {dir_txt} | ADX:{adx:.1f} | vol:{vol:.1f}x")
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
    log(f"🚀 XLM v5.0 EMA交叉 启动 | {LEVERAGE}x | {GATE_BASE_QTY}XLM/仓 | {MAX_POS_PER_SIDE}仓/边")
    log(f"策略: 15m EMA5/10 | TP{TAKE_PROFIT_PCT*100:.1f}%/SL{STOP_LOSS_PCT*100:.1f}% | ADX>{ADX_MIN} vol>{VOL_MIN}")
    log(f"回测: +194.1%/719笔/68.2%/DD-51.8%/2亏月")

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
            klines = get_data()
            if not klines:
                time.sleep(POLL_INTERVAL)
                continue

            df = pd.DataFrame(klines, columns=['t','o','h','l','c','v'])
            data = calc(df)

            state = load_state()
            price = data['price']

            result = check_entry(data)
            if result[0] is not None:
                sig, reason, indicators = result
            else:
                sig, reason = result
                indicators = None

            # 冷却期
            current_kl = int(df['t'].iloc[-2])
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
