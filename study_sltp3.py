"""Gate 双向模式 SL 深度研究"""
import ccxt, json

KEY = 'a261449031d180a2bd5537390261a461'
SECRET = 'b981402bbeb1f6d7f9ea878bd1972cd0cd7a900de437e5a27646c2c780837d8b'

g = ccxt.gate({'apiKey': KEY, 'secret': SECRET, 'options': {'defaultType': 'swap'}})
g.load_markets()

entry = 76664.9; sl = round(entry * 1.015, 1); tp = round(entry * 0.975, 1)
print(f"SL={sl} TP={tp}")

print("\n=== 1. 尝试直接在仓位上设SL/TP ===")
try:
    r = g.private_futures_put_settle_dual_comp_positions_contract({
        'settle': 'usdt',
        'contract': 'BTC_USDT',
        'stop_loss': {'trigger_price': str(sl), 'rule': 1}
    })
    print(f"✅ {json.dumps(r)}")
except Exception as e:
    print(f"❌ {str(e)[:200]}")

# 但这个需要先有仓位。现在仓位已平，先不管。

print("\n=== 2. 测试: create_order 的各种 stop 参数组合 ===")

# 先全撤
for o in g.fetch_open_orders('BTC/USDT:USDT'):
    try: g.cancel_order(o['id'], 'BTC/USDT:USDT')
    except: pass
for o in g.private_futures_get_settle_price_orders({'settle':'usdt','status':'open','contract':'BTC_USDT'}):
    try: g.private_futures_delete_settle_price_orders_order_id({'settle':'usdt','order_id':o['id']})
    except: pass

# 测试: 看看 ccxt Gate 的 create_order 究竟把 stop 订单变成什么
# 先挂一个高价limit单（不会成交的价），看它的info

# 测试A: 用 ccxt create_order('stop')
print("\n[A] create_order('stop',...):")
try:
    o = g.create_order('BTC/USDT:USDT', 'stop', 'buy', 1, 80000,
        params={'reduceOnly': True})
    print(f"  type={o['type']} side={o['side']} amount={o['amount']} stopPrice={o.get('stopPrice')}")
    print(f"  info: {json.dumps(o['info'], indent=4)}")
    g.cancel_order(o['id'], 'BTC/USDT:USDT')
except Exception as e:
    print(f"  ❌ {str(e)[:200]}")

# 测试B: 用原生API创建stop-limit
print("\n[B] 原生 price_order with reduce_only:")
try:
    body = {
        'settle': 'usdt',
        'trigger': {'strategy_type': 0, 'price_type': 0, 'price': str(sl), 'rule': 1, 'expiration': 0},
        'initial': {'contract': 'BTC_USDT', 'size': 100, 'price': str(sl), 'tif': 'gtc', 'reduce_only': True}
    }
    r = g.private_futures_post_settle_price_orders(body)
    print(f"  ✅ id={r['id'][-8:]}")
    init = r.get('initial', {})
    print(f"  is_reduce_only={init.get('is_reduce_only')} is_close={init.get('is_close')} direction={r.get('direction')}")
    g.private_futures_delete_settle_price_orders_order_id({'settle':'usdt','order_id':r['id']})
except Exception as e:
    print(f"  ❌ {str(e)[:200]}")

# 测试C: initial 里同时 reduce_only=True 和 direction
print("\n[C] reduce_only + direction:")
try:
    body = {
        'settle': 'usdt',
        'trigger': {'strategy_type': 0, 'price_type': 0, 'price': str(sl), 'rule': 1, 'expiration': 0},
        'initial': {'contract': 'BTC_USDT', 'size': 100, 'price': str(sl), 'tif': 'post_only', 'reduce_only': True, 'direction': 'short'}
    }
    r = g.private_futures_post_settle_price_orders(body)
    print(f"  ✅ id={r['id'][-8:]}")
    init = r.get('initial', {})
    print(f"  is_reduce_only={init.get('is_reduce_only')} is_close={init.get('is_close')} direction={r.get('direction')}")
    g.private_futures_delete_settle_price_orders_order_id({'settle':'usdt','order_id':r['id']})
except Exception as e:
    print(f"  ❌ {str(e)[:200]}")

print("\n=== 结论 ===")
# 检查当前无遗留单
sl_orders = g.private_futures_get_settle_price_orders({'settle':'usdt','status':'open','contract':'BTC_USDT'})
tp_orders = g.fetch_open_orders('BTC/USDT:USDT')
print(f"剩余: 条件单{len(sl_orders)} 挂单{len(tp_orders)}")
