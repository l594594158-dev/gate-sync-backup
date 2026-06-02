#!/usr/bin/env python3
"""
双保险止盈止损模块
- 主: manage_positions 每秒轮询 → 市价单
- 备: 条件挂单 → 程序挂了也能触发
每仓位变动后自动重挂
"""
import ccxt
import time
from datetime import datetime

def log(msg, prefix='SLTP'):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{prefix}] {msg}")

def ensure_sltp(exchange, symbol, direction, state_positions, tp_pct, sl_pct, contract_size, log_fn=None):
    """
    全清→全挂模式 (避免rule冲突/重复)
    exchange: ccxt gate实例
    symbol: 'BTC/USDT:USDT'
    direction: 'LONG' or 'SHORT'
    state_positions: state中该方向仓位列表 [{entry:...,...}, ...]
    tp_pct: 止盈比例 (如 0.02)
    sl_pct: 止损比例 (如 0.05)
    contract_size: 合约面值
    """
    L = log_fn or log
    if not state_positions:
        # 无仓位 → 清理该方向条件单
        return _clear_direction_orders(exchange, symbol, direction, L)

    try:
        m = exchange.market(symbol)
        contract = m['id']
    except:
        L(f'market查询失败 {symbol}')
        return

    # 1. 全清该方向已有条件单
    _clear_direction_orders(exchange, symbol, direction, L, contract)

    # 2. 计算总张数和均价
    total_qty = 0
    total_entry = 0
    for p in state_positions:
        q = contract_size  # 每仓合约张数
        total_qty += q
        total_entry += p['entry'] * q
    if total_qty == 0:
        return
    avg_entry = total_entry / total_qty

    # 3. 创建新条件单
    size_sign = -1 if direction == 'LONG' else 1  # 负=平多 正=平空

    if direction == 'LONG':
        sl_price = avg_entry * (1 - sl_pct)
        tp_price = avg_entry * (1 + tp_pct)
    else:
        sl_price = avg_entry * (1 + sl_pct)
        tp_price = avg_entry * (1 - tp_pct)

    # 止损: LONG价跌→rule2(≤)  SHORT价涨→rule1(≥)
    sl_rule = 2 if direction == 'LONG' else 1
    # 止盈: LONG价涨→rule1(≥)  SHORT价跌→rule2(≤)
    tp_rule = 1 if direction == 'LONG' else 2

    try:
        exchange.private_futures_post_settle_price_orders({
            'settle': 'usdt',
            'contract': contract,
            'size': int(size_sign * total_qty),
            'price': '0',
            'trigger_price': str(round(sl_price, 4)),
            'trigger_rule': sl_rule,
            'reduce_only': True,
        })
        L(f'{direction} SL条件单: {round(sl_price,4)} x{total_qty}张')
    except Exception as e:
        L(f'{direction} SL挂载失败: {e}')

    try:
        exchange.private_futures_post_settle_price_orders({
            'settle': 'usdt',
            'contract': contract,
            'size': int(size_sign * total_qty),
            'price': '0',
            'trigger_price': str(round(tp_price, 4)),
            'trigger_rule': tp_rule,
            'reduce_only': True,
        })
        L(f'{direction} TP条件单: {round(tp_price,4)} x{total_qty}张')
    except Exception as e:
        L(f'{direction} TP挂载失败: {e}')


def _clear_direction_orders(exchange, symbol, direction, L, contract=None):
    """清除指定方向所有条件单"""
    try:
        if contract is None:
            m = exchange.market(symbol)
            contract = m['id']

        conds = exchange.private_futures_get_settle_price_orders({
            'settle': 'usdt', 'contract': contract, 'status': 'open'
        })

        cleaned = 0
        for c in conds:
            size_val = int(c.get('size', 0))
            # size负=平多(LONG)  size正=平空(SHORT)
            if (direction == 'LONG' and size_val < 0) or (direction == 'SHORT' and size_val > 0):
                exchange.private_futures_delete_settle_price_orders_order_id({
                    'settle': 'usdt', 'order_id': c['id']
                })
                cleaned += 1

        if cleaned > 0:
            L(f'清{cleaned}条{direction}旧条件单')
    except Exception as e:
        L(f'清条件单异常: {e}')
