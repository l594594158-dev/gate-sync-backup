#!/usr/bin/env python3
"""
NEAR合约 v3.0 — 5m EMA5/10交叉 + SMA10±1.5% 2仓/边
- 5m扫描 | EMA5/EMA10金叉死叉 | ADX1h>25 | ADX4h<45 | vol>2.5 | SMA10±1.5% | RSI(40,60)
- TP 2.0% / SL 4.0% / 25x逐仓 / 220 NEAR/仓
- 双向各2仓 (回测+168.0%/960笔/71.2%胜率/DD-33.8%/仅3亏月)
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

# 行情分析实例（读取权限）
read_binance = ccxt.binance({
    'apiKey': READ_API_KEY,
    'secret': READ_SECRET,
    'options': {'defaultType': 'swap', 'settle': 'usdt'}
})

# 交易执行实例（交易权限）
trade_gate = ccxt.gate({
    'apiKey': TRADE_API_KEY,
    'secret': TRADE_SECRET,
    'options': {'defaultType': 'swap', 'settle': 'usdt'}
})

# ========== NEAR专属参数 ==========
SYMBOL = 'NEAR/USDT:USDT'
GATE_BASE_QTY = 100                   # NEAR 数量（Gate合约: 1 NEAR/张）
GATE_CONTRACT_SIZE = 1.0  # Gate NEAR合约面值
def to_contracts(amt): return int(amt / GATE_CONTRACT_SIZE)
def to_base(contracts): return contracts * GATE_CONTRACT_SIZE
LEVERAGE = 25              # 25x杠杆
BASE_DIR = '/root/liucangyang'
STATE_FILE = f'{BASE_DIR}/databases/state_near.json'
WORK_LOG = f'{BASE_DIR}/logs/work_log_near.txt'
NOTIFY_QUEUE = f'{BASE_DIR}/databases/notify_queue_near.json'

# ========== 策略参数 ==========
STOP_LOSS_PCT = 4.0 / 100    # NEAR: 4.0%止损 (回测最优)
TAKE_PROFIT_PCT = 2.0 / 100  # NEAR: 2.0%止盈 (回测最优)
MAX_POS_PER_SIDE = 2          # 每边最多2仓
POLL_INTERVAL = 1             # 扫描间隔（秒）
# 冷却: 平仓后同K线禁止重开，K线换棒即解冻

# ========== 日志 ==========
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [NEAR] {msg}")

def work_log(event, detail):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os.makedirs(os.path.dirname(WORK_LOG), exist_ok=True)
    with open(WORK_LOG, 'a') as f:
        f.write(f"[{ts}] [{event}] {detail}\n")

# ========== 状态管理 ==========
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'long_poss': [], 'short_poss': []}

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

# ========== 数据获取（NEAR合约K线）==========
def get_data():
    """用NEARUSDT合约K线做指标计算（NEAR无现货，直接用fapi）"""
    result = []
    for tf, limit in [('5m', 100), ('1h', 200), ('4h', 200)]:
        try:
            url = f'https://fapi.binance.com/fapi/v1/klines?symbol=NEARUSDT&interval={tf}&limit={limit}'
            resp = requests.get(url, timeout=5)
            klines = resp.json()
            data = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in klines]
            result.append(data)
        except Exception as e:
            log(f'获取{tf}失败: {e}')
            result.append([])
    return result

def calc(df):
    close = df['c']
    high = df['h']
    low = df['l']
    volume = df['v']
    lv = len(df) - 1

    price = close.iloc[lv]
    sma10 = ta.trend.SMAIndicator(close, 10).sma_indicator().iloc[lv]  # v3.0: SMA10

    # EMA5/EMA10闭K方向
    closed_lv = max(0, lv - 1)
    ema5_series = close.ewm(span=5, adjust=False).mean()
    ema10_series = close.ewm(span=10, adjust=False).mean()
    ema5_bull = ema5_series.iloc[closed_lv] > ema10_series.iloc[closed_lv] if closed_lv >= 0 else True
    rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[lv-1]

    try:
        adx_ind = ta.trend.ADXIndicator(high, low, close, window=14)
        adx = adx_ind.adx().iloc[lv]
        adx_pos = adx_ind.adx_pos().iloc[lv]
        adx_neg = adx_ind.adx_neg().iloc[lv]
    except:
        adx = 25; adx_pos = 25; adx_neg = 25

    # 闭K指标 (与回测一致)
    avg_vol = volume.iloc[max(0, closed_lv-19):closed_lv+1].mean()
    cur_vol = volume.iloc[closed_lv]
    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

    close_closed = close.iloc[closed_lv]
    adx_closed = adx_ind.adx().iloc[closed_lv] if 'adx_ind' in dir() else 25

    return {
        'price': price, 'rsi': rsi,
        'adx': adx, 'adx_pos': adx_pos, 'adx_neg': adx_neg,
        'vol_ratio': vol_ratio, 'sma10': sma10,
        'close_closed': close_closed, 'adx_closed': adx_closed,
        'open': df['o'].iloc[lv],
        'ema5_bull': ema5_bull
    }

# ========== 信号判断（5m EMA5/10交叉方向，无SMA，闭K一致）==========
def check_entry(data):
    r5 = data['5m']; r1 = data['1h']; r4 = data['4h']

    entry_price = r5['open']
    rsi5m = r5['rsi']
    adx1h = r1.get('adx_closed', r1['adx'])
    adx4h = r4.get('adx_closed', r4['adx'])
    vol_ratio = r5['vol_ratio']
    ema5_bull = r5.get('ema5_bull', True)

    # ① 1h ADX > 25
    if adx1h <= 25:
        return None, f"观望 | 1hADX={adx1h:.1f}≤25"

    # ② 4h ADX < 45
    if adx4h >= 45:
        return None, f"观望 | 4hADX={adx4h:.1f}≥45"

    # ③ 5m 放量 ≥2.5x
    if vol_ratio < 2.5:
        return None, f"观望 | 缩量 vol={vol_ratio:.1f}x"

    # ③b SMA10偏离 ≤1.5% (v3.0)
    sma5m = r5.get('sma_closed', r5.get('sma10', 0))
    live_price = r5['price']
    if sma5m > 0 and not (sma5m * 0.985 <= live_price <= sma5m * 1.015):
        return None, f"观望 | 偏离SMA ±{abs(live_price/sma5m-1)*100:.1f}%"

    # ④ LONG: EMA金叉 + RSI>40
    if ema5_bull and rsi5m > 40:
        return ('LONG', f"【LONG】EMA金叉 RSI={rsi5m:.1f}↑ ADX1h={adx1h:.1f} vol={vol_ratio:.1f}x",
                {'ema5_bull': True, 'adx1h': adx1h, 'adx4h': adx4h, 'rsi5m': rsi5m, 'vol_ratio': vol_ratio})

    # ⑤ SHORT: EMA死叉 + RSI<60
    if (not ema5_bull) and rsi5m < 60:
        return ('SHORT', f"【SHORT】EMA死叉 RSI={rsi5m:.1f}↓ ADX1h={adx1h:.1f} vol={vol_ratio:.1f}x",
                {'ema5_bull': False, 'adx1h': adx1h, 'adx4h': adx4h, 'rsi5m': rsi5m, 'vol_ratio': vol_ratio})

    dir_ema = '多' if ema5_bull else '空'
    return None, f"观望 | EMA{dir_ema} RSI={rsi5m:.1f} ADX1h={adx1h:.1f} vol={vol_ratio:.1f}x"

# ========== 双向各2仓管理 ==========
def manage_positions(state, price, signal, reason, kl_time, indicators=None):
    closed = False

    # ── LONG止盈止损 (遍历所有多仓) ──
    for lp in list(state.get('long_poss', [])):
        pnl = (price - lp['entry']) / lp['entry']
        if pnl <= -STOP_LOSS_PCT:
            log(f"🛑 LONG止损 | ${lp['entry']:.4f} → ${price:.4f} ({pnl*100:+.2f}%)")
            do_close('LONG', price, lp, '止损')
            state['long_poss'].remove(lp)
            state['last_exit_kl_time'] = kl_time
            save_state(state)
            closed = True
        elif pnl >= TAKE_PROFIT_PCT:
            log(f"✅ LONG止盈 | ${lp['entry']:.4f} → ${price:.4f} ({pnl*100:+.2f}%)")
            do_close('LONG', price, lp, '止盈')
            state['long_poss'].remove(lp)
            state['last_exit_kl_time'] = kl_time
            save_state(state)
            closed = True

    # ── SHORT止盈止损 (遍历所有空仓) ──
    for sp in list(state.get('short_poss', [])):
        pnl = (sp['entry'] - price) / sp['entry']
        if pnl <= -STOP_LOSS_PCT:
            log(f"🛑 SHORT止损 | ${sp['entry']:.4f} → ${price:.4f} ({pnl*100:+.2f}%)")
            do_close('SHORT', price, sp, '止损')
            state['short_poss'].remove(sp)
            state['last_exit_kl_time'] = kl_time
            save_state(state)
            closed = True
        elif pnl >= TAKE_PROFIT_PCT:
            log(f"✅ SHORT止盈 | ${sp['entry']:.4f} → ${price:.4f} ({pnl*100:+.2f}%)")
            do_close('SHORT', price, sp, '止盈')
            state['short_poss'].remove(sp)
            state['last_exit_kl_time'] = kl_time
            save_state(state)
            closed = True

    # ── 新信号（每边最多2仓）──
    if closed:
        return closed
    if signal == 'LONG':
        if len(state.get('long_poss', [])) >= MAX_POS_PER_SIDE:
            log(f"⏭ LONG信号跳过 | 已有{len(state['long_poss'])}个LONG仓")
        else:
            entry_price = do_open('LONG', price, reason)
            if entry_price:
                if indicators:
                    indicators['tp_price'] = entry_price * (1 + TAKE_PROFIT_PCT)
                    indicators['sl_price'] = entry_price * (1 - STOP_LOSS_PCT)
                    log_entry('NEAR', 'LONG', entry_price, indicators)
                state.setdefault('long_poss', []).append(
                    {'entry': entry_price, 'signal': reason, 'open_time': datetime.now().isoformat()})
                save_state(state)
    elif signal == 'SHORT':
        if len(state.get('short_poss', [])) >= MAX_POS_PER_SIDE:
            log(f"⏭ SHORT信号跳过 | 已有{len(state['short_poss'])}个SHORT仓")
        else:
            entry_price = do_open('SHORT', price, reason)
            if entry_price:
                if indicators:
                    indicators['tp_price'] = entry_price * (1 - TAKE_PROFIT_PCT)
                    indicators['sl_price'] = entry_price * (1 + STOP_LOSS_PCT)
                    log_entry('NEAR', 'SHORT', entry_price, indicators)
                state.setdefault('short_poss', []).append(
                    {'entry': entry_price, 'signal': reason, 'open_time': datetime.now().isoformat()})
                save_state(state)

    return closed

# ========== 开仓执行 ==========
def do_open(direction, price, reason):
    try:
        # ① 交易所级防护: 同方向总仓位≥配置量×2则拒绝
        positions = trade_gate.fetch_positions()
        max_contracts = to_contracts(GATE_BASE_QTY) * MAX_POS_PER_SIDE
        for p in positions:
            if p.get('symbol') != SYMBOL:
                continue
            qty = float(p.get('contracts', 0))
            if qty <= 0:
                continue
            side = 'LONG' if p.get('side') == 'long' else 'SHORT'
            if side == direction and qty >= max_contracts:
                log(f"🛡 交易所防护 | {direction}总仓{qty}张≥{max_contracts}张 | 拒绝开仓")
                return False

        # ② Gate实时价校验
        ticker = trade_gate.fetch_ticker(SYMBOL)
        gate_price = ticker['last']
        if abs(gate_price - price) / price > 0.01:
            log(f"🛡 Gate价差保护 | Binance:{price:.4f} Gate:{gate_price:.4f} 偏差{abs(gate_price/price-1)*100:.2f}% | 拒绝")
            return False

        # ③ 市价开仓
        open_side = 'buy' if direction == 'LONG' else 'sell'
        contracts = to_contracts(GATE_BASE_QTY)
        order = trade_gate.create_order(SYMBOL, 'market', open_side, contracts)
        entry_price = order.get('average', price)

        log(f"🚀 {direction}开仓 | {reason} | ${entry_price:.4f} | {GATE_BASE_QTY}NEAR")

        msg = (f"🟢 NEAR开仓\n"
               f"{direction} @ ${entry_price:,.4f}\n"
               f"数量: {GATE_BASE_QTY}NEAR | 杠杆: {LEVERAGE}x\n"
               f"{reason}")
        notify_alert(msg)
        work_log("开仓", f"{direction} | ${entry_price:.4f} | {GATE_BASE_QTY}NEAR | {reason}")
        return entry_price

    except Exception as e:
        log(f"❌ {direction}开仓失败: {e}")
        work_log("错误", f"开仓失败: {e}")
        return None

# ========== 平仓执行 ==========
def do_close(direction, price, pos_data, reason):
    try:
        close_side = 'sell' if direction == 'LONG' else 'buy'

        # 查当前持仓数量
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

        log(f"✅ {direction}平仓 | ${close_price:.4f} | {pnl_pct:+.2f}% | {reason}")

        msg = (f"{'🟢' if pnl_pct > 0 else '🔴'} NEAR平仓\n"
               f"{direction} {reason} | ${close_price:,.4f}\n"
               f"盈亏: {pnl_pct:+.2f}%")
        notify_alert(msg)
        work_log(reason, f"{direction} | PnL:{pnl_pct:+.2f}%")

    except Exception as e:
        log(f"❌ 平仓失败: {e}")
        work_log("错误", f"平仓失败: {e}")

# ========== 挂止盈止损单（Gate 条件单，防重复）==========
def ensure_sl_tp(state):
    """Gate SL/TP: 条件单reduce_only=True
    当有新仓位需要挂单时：全清→全挂，确保双向持仓都有SL/TP"""
    contract = 'BTC_USDT' if 'BTC' in SYMBOL else 'NEAR_USDT'

    # 收集所有活跃仓位 (从交易所验证)
    all_active = []
    has_pending = False
    try:
        positions = trade_gate.fetch_positions(symbols=[SYMBOL])
    except:
        return

    for d_key, direction in [('long_poss', 'LONG'), ('short_poss', 'SHORT')]:
        for pos in state.get(d_key, []):
            # 在交易所找对应仓位
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
            if qty == 0:
                continue

            if direction == 'LONG':
                sl = round(exchange_entry * (1 - STOP_LOSS_PCT), 4)
                tp = round(exchange_entry * (1 + TAKE_PROFIT_PCT), 4)
                sl_rule = 2; tp_rule = 1
                order_size = -qty   # 负值=平多
            else:
                sl = round(exchange_entry * (1 + STOP_LOSS_PCT), 4)
                tp = round(exchange_entry * (1 - TAKE_PROFIT_PCT), 4)
                sl_rule = 1; tp_rule = 2
                order_size = qty    # 正值=平空

            all_active.append({'pos': pos, 'qty': qty, 'sl': sl, 'tp': tp,
                               'sl_rule': sl_rule, 'tp_rule': tp_rule,
                               'order_size': order_size})

            # 有仓位需要挂单（sl_tp_placed为False或entry不匹配）
            if not (pos.get('sl_tp_placed') and pos.get('sl_tp_entry') is not None
                    and abs(pos.get('sl_tp_entry', 0) - pos.get('entry', 0)) < 0.001):
                has_pending = True

    if not has_pending:
        return

    # 统一清理本合约所有条件单
    try:
        existing = trade_gate.private_futures_get_settle_price_orders({
            'settle': 'usdt', 'status': 'open', 'contract': contract
        })
        for o in existing:
            try:
                trade_gate.private_futures_delete_settle_price_orders_order_id(
                    {'settle': 'usdt', 'order_id': o['id']})
            except:
                pass
    except:
        pass

    # 全量重挂所有活跃仓位的SL/TP
    for item in all_active:
        pos = item['pos']; qty = item['qty']; order_size = item['order_size']
        try:
            trade_gate.private_futures_post_settle_price_orders({
                'settle': 'usdt',
                'trigger': {'strategy_type': 0, 'price_type': 0, 'price': str(item['tp']), 'rule': item['tp_rule'], 'expiration': 0},
                'initial': {'contract': contract, 'size': order_size, 'price': '0', 'tif': 'ioc', 'reduce_only': True}
            })
            log(f"  \U0001f3af TP: ${item['tp']}")
        except Exception as e:
            log(f"  TP失败: {e}")
        try:
            trade_gate.private_futures_post_settle_price_orders({
                'settle': 'usdt',
                'trigger': {'strategy_type': 0, 'price_type': 0, 'price': str(item['sl']), 'rule': item['sl_rule'], 'expiration': 0},
                'initial': {'contract': contract, 'size': order_size, 'price': '0', 'tif': 'ioc', 'reduce_only': True}
            })
            log(f"  \U0001f512 SL: ${item['sl']}")
        except Exception as e:
            log(f"  SL失败: {e}")
            pos['sl_tp_placed'] = True
            pos['sl_tp_entry'] = pos['entry']

    save_state(state)
def sync_state(state):
    try:
        positions = trade_gate.fetch_positions(symbols=[SYMBOL])
    except:
        return False

    has_long = False; has_short = False
    longs_found = []; shorts_found = []

    for p in positions:
        if p.get('symbol') != SYMBOL: continue
        qty = float(p.get('contracts', 0))
        if qty <= 0: continue
        side = p.get('side', 'long')
        exchange_entry = float(p.get('entryPrice', 0))
        if side == 'long':
            has_long = True
            longs_found.append({'entry': exchange_entry, 'signal': '交易所恢复', 'open_time': datetime.now().isoformat()})
        elif side == 'short':
            has_short = True
            shorts_found.append({'entry': exchange_entry, 'signal': '交易所恢复', 'open_time': datetime.now().isoformat()})

    if has_long:
        if not state.get('long_poss'):
            state['long_poss'] = longs_found
            log(f"🔄 恢复{len(longs_found)}个LONG仓")
    else:
        if state.get('long_poss'):
            log("🔄 交易所LONG已消失，清除本地")
            state['long_poss'] = []

    if has_short:
        if not state.get('short_poss'):
            state['short_poss'] = shorts_found
            log(f"🔄 恢复{len(shorts_found)}个SHORT仓")
    else:
        if state.get('short_poss'):
            log("🔄 交易所SHORT已消失，清除本地")
            state['short_poss'] = []

    save_state(state)
    return has_long or has_short

# ========== 状态显示 ==========
def print_status(data, state):
    r5 = data['5m']; r4 = data['4h']; r1 = data['1h']
    price = r5['price']; rsi = r5['rsi']; adx1h = r1['adx']; adx4h = r4['adx']
    vol = r5['vol_ratio']

    dir_ema = '📈多' if r5.get('ema5_bull', True) else '📉空'

    now = datetime.now().strftime('%H:%M:%S')
    print(f"\n╔══ NEAR v3.0 EMA+SMA10 {now} ═══")
    print(f"║ 💰 {price:>10.4f} | RSI:{rsi:.1f} | EMA5/10{dir_ema}")
    print(f"║ ADX1h:{adx1h:.1f} ADX4h:{adx4h:.1f} | vol:{vol:.1f}x")

    lps = state.get('long_poss', [])
    sps = state.get('short_poss', [])
    for lp in lps:
        pnl = (price - lp['entry']) / lp['entry'] * 100
        print(f"║ 🟢 LONG ${lp['entry']:.4f} | {pnl:+.2f}%")
    for sp in sps:
        pnl = (sp['entry'] - price) / sp['entry'] * 100
        print(f"║ 🔴 SHORT ${sp['entry']:.4f} | {pnl:+.2f}%")
    if not lps and not sps:
        _, obs = check_entry(data)
        print(f"║ ⚪ {obs[:70]}")

    print(f"╚══════════════════════════╝")

# ========== 主循环 ==========
def main():
    log(f"🚀 NEAR v3.0 EMA+SMA10 启动 | {LEVERAGE}x | {GATE_BASE_QTY}NEAR/仓 | {MAX_POS_PER_SIDE}仓/边")
    log(f"策略: 5mEMA5/10+SMA10±1.5% | TP2.0%/SL4.0% | ADX>25 vol>2.5 (回测+168.0%/960笔/DD-33.8%)")

    # 设置杠杆 + 逐仓
    try:
        trade_gate.set_margin_mode('isolated', SYMBOL)
        log(f"保证金模式: 逐仓")
    except Exception as e:
        log(f"保证金模式: {e}")
    try:
        trade_gate.set_leverage(LEVERAGE, SYMBOL)
        log(f"杠杆设置: {LEVERAGE}x")
    except Exception as e:
        log(f"杠杆设置: {e}")

    state = load_state()
    if 'long_poss' not in state: state['long_poss'] = []
    if 'short_poss' not in state: state['short_poss'] = []

    sync_state(state)
    log("📊 请确认API Key IP白名单: 43.128.79.184")

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
                '5m': calc(df5m),
                '1h': calc(df1h),
                '4h': calc(df4h)
            }

            state = load_state()
            if 'long_poss' not in state: state['long_poss'] = []
            if 'short_poss' not in state: state['short_poss'] = []

            price = data['5m']['price']
            result = check_entry(data)
            if result[0] is not None:
                sig, reason, indicators = result
            else:
                sig, reason = result
                indicators = None

            # 冷却期检查：平仓后同K线禁止开仓，K线换棒即解除
            current_kl = int(df5m['t'].iloc[-1])
            if sig and current_kl <= state.get('last_exit_kl_time', 0):
                sig = None
                reason = f"冷却中(同K线)"

            # 暂停开仓检查
            PAUSE_FLAG = f'{BASE_DIR}/databases/near_pause.flag'
            if sig and os.path.exists(PAUSE_FLAG):
                if not hasattr(manage_positions, '_pause_notified'):
                    log(f"⏸ 暂停开仓 | 检测到 {PAUSE_FLAG}")
                    manage_positions._pause_notified = True
                sig = None
                reason = f"暂停 | {reason}"
            elif not os.path.exists(PAUSE_FLAG):
                manage_positions._pause_notified = False

            # 仓位强制保护锁：交易所查仓，单方向>配置量则拒绝开仓
            max_ct = to_contracts(GATE_BASE_QTY)
            if sig:
                try:
                    for p in trade_gate.fetch_positions(symbols=[SYMBOL]):
                        qty = float(p.get('contracts', 0))
                        if qty >= max_ct:
                            side = 'LONG' if p.get('side') == 'long' else 'SHORT'
                            if side == sig:
                                log(f"🔒 仓位保护锁 | {side}已有{qty}张≥{max_ct}张 | 拒绝{reason}")
                                sig = None
                                reason = f"仓位已满 | {reason}"
                                break
                except:
                    pass

            manage_positions(state, price, sig, reason, current_kl, indicators)
            # 双保险: 重挂条件单
            if state.get("long_poss"):
                ensure_sltp(trade_gate, SYMBOL, "LONG", state["long_poss"], TAKE_PROFIT_PCT, STOP_LOSS_PCT, GATE_CONTRACT_SIZE, log_fn=log)
            if state.get("short_poss"):
                ensure_sltp(trade_gate, SYMBOL, "SHORT", state["short_poss"], TAKE_PROFIT_PCT, STOP_LOSS_PCT, GATE_CONTRACT_SIZE, log_fn=log)

            has_pos = bool(state.get('long_poss') or state.get('short_poss'))
            if has_pos:
                ensure_sl_tp(state)
            else:
                # Gate 单向模式无需清理条件单
                pass

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
