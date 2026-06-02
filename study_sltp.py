"""深度学习：Gate双向下平空单的正确挂法"""
import ccxt, json

KEY = 'a261449031d180a2bd5537390261a461'
SECRET = 'b981402bbeb1f6d7f9ea878bd1972cd0cd7a900de437e5a27646c2c780837d8b'

g = ccxt.gate({'apiKey': KEY, 'secret': SECRET, 'options': {'defaultType': 'swap'}})
g.load_markets()

# === 1. 当前持仓 ===
print("=" * 50)
print("1. 当前持仓")
for p in g.fetch_positions(symbols=['BTC/USDT:USDT']):
    c = float(p.get('contracts', 0))
    if c > 0:
        entry = float(p['entryPrice'])
        side = p['side']
        print(f"   {side} {int(c)}张 @ ${entry:.1f}")
        break

# === 2. 清空所有旧单 ===
print("\n2. 清空旧单")
for o in g.fetch_open_orders('BTC/USDT:USDT'):
    g.cancel_order(o['id'], 'BTC/USDT:USDT')
    print(f"   取消挂单: {o['id'][-8:]}")
for o in g.private_futures_get_settle_price_orders({'settle':'usdt','status':'open','contract':'BTC_USDT'}):
    g.private_futures_delete_settle_price_orders_order_id({'settle':'usdt','order_id':o['id']})
    print(f"   取消条件单: {o['id'][-8:]}")

# === 3. 计算 ===
sl = round(entry * 1.015, 1)  # 76664.9 * 1.015 = 77814.9
tp = round(entry * 0.975, 1)  # 76664.9 * 0.975 = 74748.3
print(f"\n3. 目标: SL=${sl}  TP=${tp}")

# === 4. 测试各种平空方式 ===
print("\n4. 测试平空SL (触发价高于市场)")

# 方式A: 条件单 initial.direction='short'
print("\n[A] 条件单 direction=short:")
body_a = {
    'settle': 'usdt',
    'trigger': {'strategy_type': 0, 'price_type': 0, 'price': str(sl), 'rule': 1, 'expiration': 0},
    'initial': {'contract': 'BTC_USDT', 'size': 100, 'price': '0', 'tif': 'ioc', 'direction': 'short'}
}
try:
    oa = g.private_futures_post_settle_price_orders(body_a)
    print(f"   ✅ id={oa['id'][-8:]}")
    print(f"   direction={oa.get('direction')} is_close={oa['initial'].get('is_close')}")
    g.private_futures_delete_settle_price_orders_order_id({'settle':'usdt','order_id':oa['id']})
except Exception as e:
    print(f"   ❌ {str(e)[:100]}")

# 方式B: 条件单 initial.is_close=true (之前报错dual mode)
print("\n[B] 条件单 is_close=True:")
body_b = {
    'settle': 'usdt',
    'trigger': {'strategy_type': 0, 'price_type': 0, 'price': str(sl), 'rule': 1, 'expiration': 0},
    'initial': {'contract': 'BTC_USDT', 'size': 100, 'price': '0', 'tif': 'ioc', 'close': True}
}
try:
    ob = g.private_futures_post_settle_price_orders(body_b)
    print(f"   ✅ id={ob['id'][-8:]}")
    g.private_futures_delete_settle_price_orders_order_id({'settle':'usdt','order_id':ob['id']})
except Exception as e:
    print(f"   ❌ {str(e)[:100]}")

# 方式C: create_order stop market + reduceOnly  
print("\n[C] create_order stop market reduceOnly:")
try:
    oc = g.create_order('BTC/USDT:USDT', 'market', 'buy', 100, None,
        params={'stopPrice': sl, 'triggerPrice': sl, 'reduceOnly': True, 'stop': 'loss'})
    print(f"   ✅ id={oc['id'][-8:]}")
    g.cancel_order(oc['id'], 'BTC/USDT:USDT')
except Exception as e:
    print(f"   ❌ {str(e)[:120]}")

# 方式D: create_order 用stop_loss_price
print("\n[D] create_order stop-loss:")
try:
    od = g.create_order('BTC/USDT:USDT', 'market', 'buy', 100, None,
        params={'stopLossPrice': sl, 'reduceOnly': True})
    print(f"   ✅ id={od['id'][-8:]}")
except Exception as e:
    print(f"   ❌ {str(e)[:120]}")

# 方式E: 用 ccxt 原生 stop 类型
print("\n[E] create_order type='stop':")
try:
    oe = g.create_order('BTC/USDT:USDT', 'stop', 'buy', 100, sl,
        params={'reduceOnly': True, 'triggerPrice': sl})
    print(f"   ✅ id={oe['id'][-8:]}")
except Exception as e:
    print(f"   ❌ {str(e)[:120]}")

# === 5. 最终挂正确的单 ===
print("\n" + "=" * 50)
print("5. 挂正式SL和TP")
