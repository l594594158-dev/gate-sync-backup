#!/usr/bin/env python3
"""
仓位反向同步策略 v2
- 监控账户(只读) → 检测仓位变动
- 交易账户(执行) → 反向30%开仓 + 止盈止损
- 每秒扫描，每分钟自检
"""
import ccxt, time, json, os, signal
from datetime import datetime
from decimal import Decimal

# ========== 配置 ==========
MONITOR_KEY = "17cd51c6bacc6de57bea112fc49901b4"
MONITOR_SECRET = "e4d88c9cdb83f7d6544315ea650dc46f52f19d9a09980e80b03741c46d15b928"
TRADE_KEY = "4268af1264ca47863b887883a9c047e9"
TRADE_SECRET = "ddd87aaf4012ab6f107e377238cc196d1bbf47c0023f887e62e15061099743be"

BASE_DIR = "/root/gatedaidan"
LOCK_FILE = f"{BASE_DIR}/reverse_sync.lock"
STATE_FILE = f"{BASE_DIR}/databases/reverse_state.json"
SYNC_LOG = f"{BASE_DIR}/logs/reverse_sync.log"

SYNC_RATIO = 0.05
POLL_INTERVAL = 1

TP_SL = {
    "BTC/USDT:USDT":  {"tp": 1.2, "sl": 1.0},
    "HYPE/USDT:USDT": {"tp": 2.0, "sl": 1.5},
    "ZEC/USDT:USDT":  {"tp": 2.0, "sl": 1.5},
    "NEAR/USDT:USDT": {"tp": 2.0, "sl": 1.5},
    "XLM/USDT:USDT":  {"tp": 1.5, "sl": 1.2},
}
SYMBOLS = list(TP_SL.keys())

# ========== 工具函数 ==========
def round_price(price, symbol):
    """按标的tick size精确四舍五入"""
    tick = trade.market(symbol)['precision']['price']
    d = Decimal(str(price))
    t = Decimal(str(tick))
    return float((d / t).quantize(Decimal('1')) * t)

# ========== API ==========
monitor = ccxt.gate({'apiKey': MONITOR_KEY, 'secret': MONITOR_SECRET, 'options': {'defaultType': 'swap', 'settle': 'usdt'}})
trade   = ccxt.gate({'apiKey': TRADE_KEY, 'secret': TRADE_SECRET, 'options': {'defaultType': 'swap', 'settle': 'usdt'}})
trade.load_markets()

# ========== 文件锁（防多开）==========
def acquire_lock():
    import os as _os
    my_pid = str(_os.getpid())
    # 先写PID再回读验证（防竞态）
    try:
        fd = _os.open(LOCK_FILE, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o644)
        with _os.fdopen(fd, 'w') as f:
            f.write(my_pid)
        return True
    except FileExistsError:
        try:
            with open(LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            _os.kill(old_pid, 0)
            print(f"⚠️ 已有实例 PID:{old_pid}，退出")
            return False
        except (OSError, ValueError):
            # 旧进程已死，覆盖
            _os.remove(LOCK_FILE)
            return acquire_lock()

def release_lock():
    import os as _os
    try:
        with open(LOCK_FILE) as f:
            if int(f.read().strip()) == _os.getpid():
                _os.remove(LOCK_FILE)
    except:
        pass

# ========== 日志 ==========
def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)

# ========== 状态 ==========
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(s):
    with open(STATE_FILE, 'w') as f:
        json.dump(s, f, indent=2, default=str)

# ========== 仓位获取 ==========
def get_monitor_positions():
    result = {}
    try:
        for p in monitor.fetch_positions():
            sym = p.get('symbol', '')
            side = p.get('side', '')
            qty = float(p.get('contracts', 0))
            if qty > 0 and sym in SYMBOLS:
                key = f"{sym}_{side}"
                if key not in result:
                    result[key] = {
                        'contracts': qty,
                        'entry': float(p.get('entryPrice', 0)),
                    }
    except Exception as e:
        log(f"⚠️ 监控查询失败: {e}")
    return result

def get_trade_positions():
    result = {}
    try:
        for p in trade.fetch_positions():
            sym = p.get('symbol', '')
            side = p.get('side', '')
            qty = float(p.get('contracts', 0))
            if qty > 0 and sym in SYMBOLS:
                key = f"{sym}_{side}"
                result[key] = {'contracts': qty}
    except Exception as e:
        log(f"⚠️ 交易查询失败: {e}")
    return result

# ========== 方向转换 ==========
def opposite(side):
    return 'short' if side == 'long' else 'long'

def to_order_side(direction):
    """long→buy, short→sell (Gate/ccxt通用)"""
    return 'buy' if direction == 'long' else 'sell'

# ========== 余额检查 ==========
def get_balance():
    try:
        b = trade.fetch_balance()
        return b.get('USDT', {}).get('free', 0)
    except:
        return 0

def estimate_margin(symbol, contracts):
    """估算10x杠杆所需保证金"""
    try:
        ticker = trade.fetch_ticker(symbol)
        price = ticker['last']
        market = trade.market(symbol)
        cs = market.get('contractSize', 0.0001)
        value = contracts * cs * price
        return value / 10  # 10x
    except:
        return 99999

# ========== 交易操作 ==========
def trade_open(symbol, direction, qty, reason=""):
    """以指定方向开仓 (direction: 'long'/'short')"""
    try:
        # 设置杠杆
        try:
            trade.set_leverage(10, symbol)
        except:
            pass

        order_side = to_order_side(direction)  # buy or sell
        contracts = int(qty)
        order = trade.create_order(symbol, 'market', order_side, contracts)
        price = order.get('average', 0) or trade.fetch_ticker(symbol)['last']

        log(f"  🚀 {symbol} {direction} {contracts}张 @ ${price:.2f} | {reason}")
        return price
    except Exception as e:
        log(f"  ❌ 开仓失败 {symbol} {direction}: {e}")
        return None

def trade_close(symbol, direction):
    """平掉指定方向的全部仓位"""
    try:
        close_side = 'sell' if direction == 'long' else 'buy'
        positions = trade.fetch_positions(symbols=[symbol])
        actual_qty = 0
        for p in positions:
            if p.get('symbol') == symbol and p.get('side') == direction:
                actual_qty = float(p.get('contracts', 0))
                break
        if actual_qty == 0:
            return False
        order = trade.create_order(symbol, 'market', close_side, int(actual_qty), None, {'reduce_only': True})
        price = order.get('average', 0)
        log(f"  🏁 平仓 {symbol} {direction} {int(actual_qty)}张 @ ${price:.2f}")
        return True
    except Exception as e:
        log(f"  ❌ 平仓失败 {symbol} {direction}: {e}")
        return False

def set_sl_tp(symbol, direction, entry_price, skip_if_exists=False):
    """挂止盈止损（Gate价格触发单）。skip_if_exists=True 时先检查是否已有条件单"""
    cfg = TP_SL.get(symbol, {"tp": 1.5, "sl": 1.2})
    tp_pct = cfg["tp"] / 100
    sl_pct = cfg["sl"] / 100

    try:
        # 获取持仓数量
        positions = trade.fetch_positions(symbols=[symbol])
        pos_qty = 0
        for p in positions:
            if p.get('side') == direction and float(p.get('contracts', 0)) > 0:
                pos_qty = int(float(p['contracts']))
                break
        if pos_qty == 0:
            return

        # 合约名用下划线格式
        contract = symbol.replace('/USDT:USDT', '_USDT').replace(':USDT', '_USDT')

        # 如果要求跳过已存在的，先检查
        if skip_if_exists:
            try:
                existing = trade.private_futures_get_settle_price_orders({
                    "settle": "usdt", "contract": contract, "status": "open"
                })
                if len(existing) >= 2:  # TP + SL 都在
                    return
            except:
                pass

        # 计算止盈止损价
        if direction == 'long':
            tp_price = round_price(entry_price * (1 + tp_pct), symbol)
            sl_price = round_price(entry_price * (1 - sl_pct), symbol)
            tp_rule = 1   # >= 触发
            sl_rule = 2   # <= 触发
        else:
            tp_price = round_price(entry_price * (1 - tp_pct), symbol)
            sl_price = round_price(entry_price * (1 + sl_pct), symbol)
            tp_rule = 2   # <= 触发
            sl_rule = 1   # >= 触发

        base_params = {"settle": "usdt"}

        # 先挂新单
        tp_order = trade.private_futures_post_settle_price_orders({
            **base_params,
            "initial": {"contract": contract, "size": -pos_qty, "price": str(tp_price)},
            "trigger": {"price": str(tp_price), "rule": tp_rule, "price_type": 1},
        })
        time.sleep(0.1)

        sl_order = trade.private_futures_post_settle_price_orders({
            **base_params,
            "initial": {"contract": contract, "size": -pos_qty, "price": str(sl_price)},
            "trigger": {"price": str(sl_price), "rule": sl_rule, "price_type": 1},
        })

        # 再清旧单（保留刚创建的两个）
        try:
            new_ids = {tp_order.get('id', ''), sl_order.get('id', '')}
            existing = trade.private_futures_get_settle_price_orders({
                "settle": "usdt", "contract": contract, "status": "open"
            })
            for order in existing:
                if order['id'] not in new_ids:
                    try:
                        trade.private_futures_delete_settle_price_orders_order_id(
                            {"settle": "usdt", "contract": contract, "order_id": order['id']}
                        )
                    except:
                        pass
        except:
            pass

        log(f"  🎯 SL/TP {symbol} {direction}: TP={tp_price:.4f} SL={sl_price:.4f}")
    except Exception as e:
        log(f"  ⚠️ SL/TP失败 {symbol}: {str(e)[:80]}")

# ========== 主同步 ==========
def sync():
    mp = get_monitor_positions()
    tp = get_trade_positions()
    state = load_state()
    changed = False

    # 监控账户异常时跳过同步，防止误平全部仓位
    if not mp:
        return

    # 遍历监控仓位，同步到交易账户（反向30%）
    for m_key, m_info in mp.items():
        symbol = m_key.rsplit('_', 1)[0]
        m_side = m_key.rsplit('_', 1)[1]
        m_qty = m_info['contracts']

        trade_side = opposite(m_side)  # 反向
        target_qty = int(m_qty * SYNC_RATIO)
        if target_qty <= 0:
            continue

        t_key = f"{symbol}_{trade_side}"
        current_qty = int(tp.get(t_key, {}).get('contracts', 0))

        if current_qty == 0:
            # 新开仓
            margin = estimate_margin(symbol, target_qty)
            balance = get_balance()
            if balance < margin * 1.2:
                log(f"  ⚠️ {symbol} 需要${margin:.1f} | 余额${balance:.2f} | 跳过")
                continue

            log(f"🔔 监控{m_side} {m_qty:.0f}张 → 反向{trade_side} {target_qty}张 {symbol}")
            entry = trade_open(symbol, trade_side, target_qty,
                              f"监控{m_side} @ ${m_info['entry']:.2f}")
            if entry:
                state[t_key] = {'monitor_key': m_key, 'qty': target_qty, 'entry': entry}
                set_sl_tp(symbol, trade_side, entry)
                state[t_key]['sl_tp_set'] = True
                changed = True

        elif abs(current_qty - target_qty) > max(1, target_qty * 0.2):
            # 仓位调整
            log(f"🔧 {symbol} {trade_side}: {current_qty}→{target_qty}张")
            trade_close(symbol, trade_side)
            time.sleep(0.5)
            entry = trade_open(symbol, trade_side, target_qty, "仓位调整")
            if entry:
                state[t_key] = {'monitor_key': m_key, 'qty': target_qty, 'entry': entry}
                set_sl_tp(symbol, trade_side, entry)
                state[t_key]['sl_tp_set'] = True
                changed = True

    # 平掉无对应监控仓的交易仓
    for t_key in list(state.keys()):
        m_key = state[t_key].get('monitor_key', '')
        if m_key not in mp:
            symbol = t_key.rsplit('_', 1)[0]
            side = t_key.rsplit('_', 1)[1]
            if t_key in tp:
                log(f"🔔 监控仓{m_key}已平 → 平交易仓 {t_key}")
                trade_close(symbol, side)
            del state[t_key]
            changed = True

    if changed:
        save_state(state)

# ========== 自检 ==========
last_health = 0
def health_check():
    global last_health
    now = time.time()
    if now - last_health < 60:
        return
    last_health = now

    state = load_state()
    tp = get_trade_positions()
    mp = get_monitor_positions()

    # 清理孤儿仓（交易有但状态无，且无对应监控仓）
    for t_key in tp:
        if t_key not in state:
            symbol = t_key.rsplit('_', 1)[0]
            side = t_key.rsplit('_', 1)[1]
            # 检查是否有对应监控仓
            m_side = opposite(side)
            m_key = f"{symbol}_{m_side}"
            if m_key not in mp:
                log(f"🧹 孤儿仓 {t_key} → 清理")
                trade_close(symbol, side)
                time.sleep(0.3)

    # 补设止盈止损（仅未设置时；已设置则验证条件单存在）
    sl_tp_changed = False
    for t_key, info in state.items():
        if t_key in tp:
            symbol = t_key.rsplit('_', 1)[0]
            side = t_key.rsplit('_', 1)[1]
            skip = info.get('sl_tp_set', False)
            set_sl_tp(symbol, side, info['entry'], skip_if_exists=skip)
            if not skip:
                info['sl_tp_set'] = True
                sl_tp_changed = True
            time.sleep(0.3)
    if sl_tp_changed:
        save_state(state)

    log(f"🩺 自检: 监控{len(mp)}仓 | 交易{len(tp)}仓 | 状态{len(state)}条")

# ========== 主循环 ==========
def main():
    if not acquire_lock():
        return

    log("=" * 50)
    log("🚀 仓位反向同步 v2 启动")
    log(f"   比例: {SYNC_RATIO*100}% | 间隔: {POLL_INTERVAL}s")
    log(f"   杠杆: 10x | 标的: {', '.join(SYMBOLS)}")
    log("=" * 50)

    while True:
        try:
            sync()
            health_check()
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log("🛑 停止")
            release_lock()
            break
        except Exception as e:
            log(f"❌ {e}")
            import traceback
            traceback.print_exc()
            time.sleep(POLL_INTERVAL)

# 注册退出清理
import atexit
atexit.register(release_lock)

if __name__ == "__main__":
    main()
