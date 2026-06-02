#!/usr/bin/env python3
"""
HYPE合约 EMA3/10趋势策略 v4.0 — 5m EMA3/10交叉方向 + ADX1h上升过滤 (回测最优)
- 5m扫描 | 5m EMA3/EMA10金叉死叉方向 (闭K[i-1])
- TP 3.0% / SL 4.5% | ADX1h>27+上升 | 4h<50 | vol>3.0x | SMA10±1.5%
- RSI(40,60) | 25x逐仓 | 双向各2仓
- v4.0: EMA5→EMA3, ADX上升过滤, 2仓/边, 回测+139.6%/374笔/66.3%胜率/DD-38.3%/仅1亏月
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

# ========== HYPE专属参数 ==========
SYMBOL = 'HYPE/USDT:USDT'
GATE_BASE_QTY = 3                     # HYPE 数量（Gate合约: 0.1 HYPE/张）
GATE_CONTRACT_SIZE = 0.1
def to_contracts(amt): return int(amt / GATE_CONTRACT_SIZE)
def to_base(contracts): return contracts * GATE_CONTRACT_SIZE
LEVERAGE = 25
BASE_DIR = '/root/liucangyang'
STATE_FILE = f'{BASE_DIR}/databases/state_hype.json'
WORK_LOG = f'{BASE_DIR}/logs/work_log_hype.txt'
NOTIFY_QUEUE = f'{BASE_DIR}/databases/notify_queue_hype.json'

# ========== 策略参数 (v4.0) ==========
STOP_LOSS_PCT = 4.5 / 100    # v4.0: 4.5%止损
TAKE_PROFIT_PCT = 3.0 / 100  # v4.0: 3.0%止盈
MAX_POS_PER_SIDE = 2          # v4.0: 双向各2仓
POLL_INTERVAL = 1

# ========== 日志 ==========
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [HYPE] {msg}")

def work_log(event, detail):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os.makedirs(os.path.dirname(WORK_LOG), exist_ok=True)
    with open(WORK_LOG, 'a') as f:
        f.write(f"[{ts}] [{event}] {detail}\n")

# ========== 状态管理 ==========
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
    else:
        s = {}
    if 'long_positions' not in s: s['long_positions'] = []
    if 'short_positions' not in s: s['short_positions'] = []
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
    for tf, limit in [('5m', 100), ('1h', 200), ('4h', 200)]:
        try:
            url = f'https://fapi.binance.com/fapi/v1/klines?symbol=HYPEUSDT&interval={tf}&limit={limit}'
            resp = requests.get(url, timeout=5)
            klines = resp.json()
            data = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in klines]
            result.append(data)
        except Exception as e:
            log(f'获取{tf}失败: {e}')
            result.append([])
    return result

def calc(df, sma_period=10, return_adx_prev=False):
    """v4.0: EMA3/EMA10方向, SMA10, 可选返回ADX前值"""
    close = df['c']
    high = df['h']
    low = df['l']
    volume = df['v']
    lv = len(df) - 1

    price = close.iloc[lv]
    sma = ta.trend.SMAIndicator(close, sma_period).sma_indicator().iloc[lv]

    # EMA3/EMA10 方向 (闭K lv-1)
    closed_lv = max(0, lv - 1)
    ema3_series = close.ewm(span=3, adjust=False).mean()
    ema10_series = close.ewm(span=10, adjust=False).mean()
    ema3_bull = ema3_series.iloc[closed_lv] > ema10_series.iloc[closed_lv] if closed_lv >= 0 else True
    rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[lv-1]

    try:
        adx_ind = ta.trend.ADXIndicator(high, low, close, window=14)
        adx = adx_ind.adx().iloc[lv]
        adx_pos = adx_ind.adx_pos().iloc[lv]
        adx_neg = adx_ind.adx_neg().iloc[lv]
    except:
        adx = 25; adx_pos = 25; adx_neg = 25

    # 闭K指标
    avg_vol = volume.iloc[max(0, closed_lv-19):closed_lv+1].mean()
    cur_vol = volume.iloc[closed_lv]
    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

    close_closed = close.iloc[closed_lv]
    sma_closed = ta.trend.SMAIndicator(close, sma_period).sma_indicator().iloc[closed_lv]
    adx_closed = adx_ind.adx().iloc[closed_lv] if 'adx_ind' in dir() else 25

    result = {
        'price': price, 'sma': sma, 'rsi': rsi,
        'adx': adx, 'adx_pos': adx_pos, 'adx_neg': adx_neg,
        'vol_ratio': vol_ratio,
        'close_closed': close_closed, 'sma_closed': sma_closed,
        'adx_closed': adx_closed,
        'open': df['o'].iloc[lv],
        'ema3_bull': ema3_bull
    }

    if return_adx_prev:
        # 返回上一根1h bar的ADX (用于ADX上升判断)
        prev_lv = lv - 1
        if prev_lv >= 0:
            try:
                adx_ind_prev = ta.trend.ADXIndicator(high.iloc[:lv], low.iloc[:lv], close.iloc[:lv], window=14)
                result['adx_prev'] = adx_ind_prev.adx().iloc[prev_lv]
            except:
                result['adx_prev'] = 0
        else:
            result['adx_prev'] = 0

    return result

# ========== 信号判断 ==========
def check_entry(data):
    r5 = data['5m']; r1 = data['1h']; r4 = data['4h']

    entry_price = r5['open']
    rsi5m = r5['rsi']
    adx1h = r1.get('adx_closed', r1['adx'])
    adx1h_prev = r1.get('adx_prev', 0)   # v4.0: 前一1h bar ADX
    adx4h = r4.get('adx_closed', r4['adx'])
    vol_ratio = r5['vol_ratio']
    sma5m = r5.get('sma_closed', r5['sma'])

    # ① 5m EMA3/EMA10交叉方向
    ema3_bull = r5.get('ema3_bull', True)

    # ② 1h ADX > 27 (v4.0)
    if adx1h <= 27:
        return None, f"观望 | 1hADX={adx1h:.1f}≤27"

    # ②b ADX1h上升: 当前1h ADX > 前一1h bar ADX (v4.0)
    if adx1h_prev > 0 and adx1h <= adx1h_prev:
        return None, f"观望 | ADX1h未上升({adx1h:.1f}≤{adx1h_prev:.1f})"

    # ③ 4h ADX < 50 (v4.0)
    if adx4h >= 50:
        return None, f"观望 | 4hADX={adx4h:.1f}≥50"

    # ④ SMA10 ±1.5% (v4.0)
    live_price = r5['price']
    if not (sma5m * 0.985 <= live_price <= sma5m * 1.015):
        return None, f"观望 | 偏离SMA ±{abs(live_price/sma5m-1)*100:.1f}%"

    # ⑤ 5m 放量 ≥3.0x (v4.0)
    if vol_ratio < 3.0:
        return None, f"观望 | 缩量 vol={vol_ratio:.1f}x"

    # ⑥ LONG: EMA3/10金叉 + RSI>40
    if ema3_bull and rsi5m > 40:
        return ('LONG', f"【LONG】EMA3金叉 RSI={rsi5m:.1f}↑ ADX1h={adx1h:.1f}↑ vol={vol_ratio:.1f}x",
                {'ema3_bull': ema3_bull, 'adx1h': adx1h, 'adx1h_prev': adx1h_prev, 'adx4h': adx4h, 'rsi5m': rsi5m, 'vol_ratio': vol_ratio, 'sma5m': sma5m, 'live_price': live_price})

    # SHORT: EMA3/10死叉 + RSI<60
    if (not ema3_bull) and rsi5m < 60:
        return ('SHORT', f"【SHORT】EMA3死叉 RSI={rsi5m:.1f}↓ ADX1h={adx1h:.1f}↑ vol={vol_ratio:.1f}x",
                {'ema3_bull': ema3_bull, 'adx1h': adx1h, 'adx1h_prev': adx1h_prev, 'adx4h': adx4h, 'rsi5m': rsi5m, 'vol_ratio': vol_ratio, 'sma5m': sma5m, 'live_price': live_price})

    dir_ema = '多' if ema3_bull else '空'
    return None, f"观望 | EMA3{dir_ema} RSI={rsi5m:.1f} ADX1h={adx1h:.1f}"

# ========== 双向各2仓管理 (v4.0) ==========
def manage_positions(state, price, signal, reason, sma5m, kl_time, indicators=None):
    closed = False

    # ── LONG止盈止损 (遍历所有LONG仓) ──
    lp_list = state.get('long_positions', [])
    survivors = []
    for lp in lp_list:
        pnl = (price - lp['entry']) / lp['entry']
        if pnl <= -STOP_LOSS_PCT:
            log(f"🛑 LONG止损 #{lp['id']} | ${lp['entry']:.4f} → ${price:.4f} ({pnl*100:+.2f}%)")
            do_close('LONG', price, lp, '止损')
            state['last_exit_kl_time'] = kl_time
            save_state(state)
            closed = True
        elif pnl >= TAKE_PROFIT_PCT:
            log(f"✅ LONG止盈 #{lp['id']} | ${lp['entry']:.4f} → ${price:.4f} ({pnl*100:+.2f}%)")
            do_close('LONG', price, lp, '止盈')
            state['last_exit_kl_time'] = kl_time
            save_state(state)
            closed = True
        else:
            survivors.append(lp)
    state['long_positions'] = survivors

    # ── SHORT止盈止损 (遍历所有SHORT仓) ──
    sp_list = state.get('short_positions', [])
    survivors = []
    for sp in sp_list:
        pnl = (sp['entry'] - price) / sp['entry']
        if pnl <= -STOP_LOSS_PCT:
            log(f"🛑 SHORT止损 #{sp['id']} | ${sp['entry']:.4f} → ${price:.4f} ({pnl*100:+.2f}%)")
            do_close('SHORT', price, sp, '止损')
            state['last_exit_kl_time'] = kl_time
            save_state(state)
            closed = True
        elif pnl >= TAKE_PROFIT_PCT:
            log(f"✅ SHORT止盈 #{sp['id']} | ${sp['entry']:.4f} → ${price:.4f} ({pnl*100:+.2f}%)")
            do_close('SHORT', price, sp, '止盈')
            state['last_exit_kl_time'] = kl_time
            save_state(state)
            closed = True
        else:
            survivors.append(sp)
    state['short_positions'] = survivors

    # ── 新信号 (v4.0: 双向各2仓) ──
    if closed:
        return closed

    # 生成仓位ID
    import random
    def gen_id(): return random.randint(1000, 9999)

    if signal == 'LONG':
        if len(state.get('long_positions', [])) >= MAX_POS_PER_SIDE:
            log(f"⏭ LONG信号跳过 | 已有{len(state['long_positions'])}仓(上限{MAX_POS_PER_SIDE})")
        else:
            entry_price = do_open('LONG', price, reason, sma5m)
            if entry_price:
                if indicators:
                    indicators['tp_price'] = entry_price * (1 + TAKE_PROFIT_PCT)
                    indicators['sl_price'] = entry_price * (1 - STOP_LOSS_PCT)
                    log_entry('HYPE', 'LONG', entry_price, indicators)
                pid = gen_id()
                state.setdefault('long_positions', []).append({
                    'id': pid, 'entry': entry_price, 'signal': reason,
                    'open_time': datetime.now().isoformat()
                })
                save_state(state)
    elif signal == 'SHORT':
        if len(state.get('short_positions', [])) >= MAX_POS_PER_SIDE:
            log(f"⏭ SHORT信号跳过 | 已有{len(state['short_positions'])}仓(上限{MAX_POS_PER_SIDE})")
        else:
            entry_price = do_open('SHORT', price, reason, sma5m)
            if entry_price:
                if indicators:
                    indicators['tp_price'] = entry_price * (1 - TAKE_PROFIT_PCT)
                    indicators['sl_price'] = entry_price * (1 + STOP_LOSS_PCT)
                    log_entry('HYPE', 'SHORT', entry_price, indicators)
                pid = gen_id()
                state.setdefault('short_positions', []).append({
                    'id': pid, 'entry': entry_price, 'signal': reason,
                    'open_time': datetime.now().isoformat()
                })
                save_state(state)

    return closed

# ========== 开仓执行 ==========
def do_open(direction, price, reason, sma5m):
    try:
        max_contracts = to_contracts(GATE_BASE_QTY)
        positions = trade_gate.fetch_positions()
        for p in positions:
            if p.get('symbol') != SYMBOL: continue
            qty = float(p.get('contracts', 0))
            if qty <= 0: continue
            side = 'LONG' if p.get('side') == 'long' else 'SHORT'
            if side == direction:
                log(f"🛡 交易所防护 | 已有{direction}仓{qty}张 | 拒绝开仓")
                return False

        ticker = trade_gate.fetch_ticker(SYMBOL)
        gate_price = ticker['last']
        if abs(gate_price - price) / price > 0.01:
            log(f"🛡 Gate价差保护 | 偏差{abs(gate_price/price-1)*100:.2f}%")
            return False

        if not (sma5m * 0.985 <= gate_price <= sma5m * 1.015):
            log(f"🛡 Gate插针保护 | 偏离{abs(gate_price/sma5m-1)*100:.2f}%>1.5%")
            return False

        open_side = 'buy' if direction == 'LONG' else 'sell'
        contracts = to_contracts(GATE_BASE_QTY)
        order = trade_gate.create_order(SYMBOL, 'market', open_side, contracts)
        entry_price = order.get('average', price)

        log(f"🚀 {direction}开仓 | {reason} | ${entry_price:.4f} | {GATE_BASE_QTY}HYPE")
        msg = (f"🟢 HYPE开仓\n{direction} @ ${entry_price:,.4f}\n数量: {GATE_BASE_QTY}HYPE | {reason}")
        notify_alert(msg)
        work_log("开仓", f"{direction} | ${entry_price:.4f} | {GATE_BASE_QTY}HYPE | {reason}")
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
                    qty = float(p['contracts'])
                    break
        if qty == 0:
            log(f"⚠️ 未找到{direction}持仓，可能已被平")
            return

        order = trade_gate.create_order(SYMBOL, 'market', close_side, qty, None, {'reduce_only': True})
        close_price = order.get('average', price)

        if direction == 'LONG':
            pnl_pct = (close_price - pos_data['entry']) / pos_data['entry'] * 100
        else:
            pnl_pct = (pos_data['entry'] - close_price) / pos_data['entry'] * 100

        log(f"✅ {direction}平仓 #{pos_data.get('id','?')} | ${close_price:.4f} | {pnl_pct:+.2f}% | {reason}")
        msg = (f"{'🟢' if pnl_pct > 0 else '🔴'} HYPE平仓\n{direction} #{pos_data.get('id','?')} {reason}\n盈亏: {pnl_pct:+.2f}%")
        notify_alert(msg)
        work_log(reason, f"{direction} #{pos_data.get('id','?')} | PnL:{pnl_pct:+.2f}%")
    except Exception as e:
        log(f"❌ 平仓失败: {e}")
        work_log("错误", f"平仓失败: {e}")

# ========== 挂止盈止损单 ==========
def ensure_sl_tp(state):
    contract = 'HYPE_USDT'
    try:
        positions = trade_gate.fetch_positions(symbols=[SYMBOL])
    except:
        return

    all_active = []
    for d_key, direction in [('long_positions', 'LONG'), ('short_positions', 'SHORT')]:
        pos_list = state.get(d_key, [])
        for pos in pos_list:
            qty = 0
            exchange_entry = pos['entry']
            for p in positions:
                if p.get('symbol') == SYMBOL and float(p.get('contracts', 0)) > 0:
                    side_check = 'LONG' if p.get('side') == 'long' else 'SHORT'
                    if side_check == direction:
                        qty = int(p['contracts'])
                        ep = float(p.get('entryPrice', 0))
                        if ep > 0: exchange_entry = ep
                        break
            if qty == 0: continue

            if direction == 'LONG':
                sl = round(exchange_entry * (1 - STOP_LOSS_PCT), 3)
                tp = round(exchange_entry * (1 + TAKE_PROFIT_PCT), 3)
                sl_rule = 2; tp_rule = 1; order_size = -qty
            else:
                sl = round(exchange_entry * (1 + STOP_LOSS_PCT), 3)
                tp = round(exchange_entry * (1 - TAKE_PROFIT_PCT), 3)
                sl_rule = 1; tp_rule = 2; order_size = qty

            all_active.append({'sl': sl, 'tp': tp, 'sl_rule': sl_rule, 'tp_rule': tp_rule, 'order_size': order_size})

    if not all_active:
        return

    try:
        existing = trade_gate.private_futures_get_settle_price_orders({
            'settle': 'usdt', 'status': 'open', 'contract': contract
        })
        for o in existing:
            try:
                trade_gate.private_futures_delete_settle_price_orders_order_id(
                    {'settle': 'usdt', 'order_id': o['id']})
            except: pass
    except: pass

    for item in all_active:
        try:
            trade_gate.private_futures_post_settle_price_orders({
                'settle': 'usdt',
                'trigger': {'strategy_type': 0, 'price_type': 0, 'price': str(item['tp']), 'rule': item['tp_rule'], 'expiration': 0},
                'initial': {'contract': contract, 'size': item['order_size'], 'price': '0', 'tif': 'ioc', 'reduce_only': True}
            })
        except Exception as e: log(f"  TP失败: {e}")
        try:
            trade_gate.private_futures_post_settle_price_orders({
                'settle': 'usdt',
                'trigger': {'strategy_type': 0, 'price_type': 0, 'price': str(item['sl']), 'rule': item['sl_rule'], 'expiration': 0},
                'initial': {'contract': contract, 'size': item['order_size'], 'price': '0', 'tif': 'ioc', 'reduce_only': True}
            })
        except Exception as e: log(f"  SL失败: {e}")

def sync_state(state):
    try:
        positions = trade_gate.fetch_positions(symbols=[SYMBOL])
    except:
        return False

    long_qty = sum(1 for p in positions if p.get('symbol') == SYMBOL and float(p.get('contracts', 0)) > 0 and p.get('side') == 'long')
    short_qty = sum(1 for p in positions if p.get('symbol') == SYMBOL and float(p.get('contracts', 0)) > 0 and p.get('side') == 'short')

    if long_qty == 0 and state.get('long_positions'):
        log("🔄 交易所LONG已消失，清除本地")
        state['long_positions'] = []
    if short_qty == 0 and state.get('short_positions'):
        log("🔄 交易所SHORT已消失，清除本地")
        state['short_positions'] = []

    save_state(state)
    return long_qty > 0 or short_qty > 0

# ========== 状态显示 ==========
def print_status(data, state):
    r5 = data['5m']; r4 = data['4h']; r1 = data['1h']
    price = r5['price']; rsi = r5['rsi']; adx1h = r1['adx']; adx4h = r4['adx']
    vol = r5['vol_ratio']
    dir_ema = '📈多' if r5.get('ema3_bull', True) else '📉空'

    now = datetime.now().strftime('%H:%M:%S')
    lp_list = state.get('long_positions', [])
    sp_list = state.get('short_positions', [])
    print(f"\n╔══ HYPE v4.0 EMA3+ADX↑ {now} ═══")
    print(f"║ 💰 {price:>10.4f} | RSI:{rsi:.1f} | SMA10:{r5['sma']:.4f}")
    print(f"║ EMA3{dir_ema} | ADX1h:{adx1h:.1f}(prev:{r1.get('adx_prev',0):.1f}) ADX4h:{adx4h:.1f} | vol:{vol:.1f}x")

    for lp in lp_list:
        pnl = (price - lp['entry']) / lp['entry'] * 100
        print(f"║ 🟢 LONG #{lp['id']} ${lp['entry']:.4f} | {pnl:+.2f}%")
    for sp in sp_list:
        pnl = (sp['entry'] - price) / sp['entry'] * 100
        print(f"║ 🔴 SHORT #{sp['id']} ${sp['entry']:.4f} | {pnl:+.2f}%")
    if not lp_list and not sp_list:
        _, obs = check_entry(data)
        print(f"║ ⚪ {obs[:70]}")
    print(f"╚══ {len(lp_list)}L/{len(sp_list)}S ═══")

# ========== 主循环 ==========
def main():
    log(f"🚀 HYPE v4.0 EMA3+ADX↑ 启动 | {LEVERAGE}x | {GATE_BASE_QTY}HYPE/仓 | {MAX_POS_PER_SIDE}仓/边")
    log(f"策略: EMA3/10+ADX27↑+TP3.0%/SL4.5%+vol3.0/SMA10±1.5% (回测+139.6%/374笔/66.3%/DD-38.3%)")

    try:
        trade_gate.set_margin_mode('isolated', SYMBOL)
    except Exception as e: log(f"保证金: {e}")
    try:
        trade_gate.set_leverage(LEVERAGE, SYMBOL)
    except Exception as e: log(f"杠杆: {e}")

    state = load_state()
    sync_state(state)
    log("📊 API Key IP白名单: 43.128.79.184")

    while True:
        try:
            k5m, k1h, k4h = get_data()
            if not k5m:
                time.sleep(POLL_INTERVAL)
                continue

            df5m = pd.DataFrame(k5m, columns=['t','o','h','l','c','v'])
            df1h = pd.DataFrame(k1h, columns=['t','o','h','l','c','v'])
            df4h = pd.DataFrame(k4h, columns=['t','o','h','l','c','v'])

            data = {
                '5m': calc(df5m, 10),
                '1h': calc(df1h, 10, return_adx_prev=True),   # 1h需要ADX前值
                '4h': calc(df4h)
            }

            state = load_state()

            price = data['5m']['price']
            result = check_entry(data)
            if result[0] is not None:
                sig, reason, indicators = result
            else:
                sig, reason = result
                indicators = None

            current_kl = int(df5m['t'].iloc[-1])
            if sig and current_kl <= state.get('last_exit_kl_time', 0):
                sig = None; reason = f"冷却中(同K线)"

            PAUSE_FLAG = f'{BASE_DIR}/databases/hype_pause.flag'
            if sig and os.path.exists(PAUSE_FLAG):
                if not hasattr(manage_positions, '_pause_notified'):
                    log(f"⏸ 暂停开仓 | {PAUSE_FLAG}")
                    manage_positions._pause_notified = True
                sig = None; reason = f"暂停"
            elif not os.path.exists(PAUSE_FLAG):
                manage_positions._pause_notified = False

            manage_positions(state, price, sig, reason, data['5m']['sma'], current_kl, indicators)

            has_pos = bool(state.get('long_positions') or state.get('short_positions'))
            if has_pos:
                ensure_sl_tp(state)

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
