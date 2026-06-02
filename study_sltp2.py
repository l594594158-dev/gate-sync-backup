"""清理所有旧单，测试最终正确方案"""
import ccxt, json

KEY = 'a261449031d180a2bd5537390261a461'
SECRET = 'b981402bbeb1f6d7f9ea878bd1972cd0cd7a900de437e5a27646c2c780837d8b'

g = ccxt.gate({'apiKey': KEY, 'secret': SECRET, 'options': {'defaultType': 'swap'}})
g.load_markets()

entry = 76664.9
sl = round(entry * 1.015, 1)   # 77814.9
tp = round(entry * 0.975, 1)   # 74748.3
qty = 100

# === 全撤 ===
print("1. 全撤")
for o in g.fetch_open_orders('BTC/USDT:USDT'):
    try: g.cancel_order(o['id'], 'BTC/USDT:USDT')
    except: pass
for o in g.private_futures_get_settle_price_orders({'settle':'usdt','status':'open','contract':'BTC_USDT'}):
    try: g.private_futures_delete_settle_price_orders_order_id({'settle':'usdt','order_id':o['id']})
    except: pass

# 确认清空
sl_left = g.private_futures_get_settle_price_orders({'settle':'usdt','status':'open','contract':'BTC_USDT'})
tp_left = g.fetch_open_orders('BTC/USDT:USDT')
print(f"   剩余: 条件单{len(sl_left)} 挂单{len(tp_left)}")

# === 测试正确SL方案 ===
print("\n2. 测试SL")

# 方案1: create_order stop market + reduceOnly
print("\n  方案1: stop market + reduceOnly")
try:
    o = g.create_order('BTC/USDT:USDT', 'market', 'buy', qty, None,
        params={'stopPrice': sl, 'reduceOnly': True})
    print(f"  ✅ {o['type']} {o['side']} reduce={o.get('reduceOnly')}")
    print(f"  info: stop_price={o['info'].get('stop_price')} status={o['info'].get('status')}")
except Exception as e:
    print(f"  ❌ {str(e)[:150]}")

# 方案2: create_order stop type
print("\n  方案2: order type='stop'")
try:
    o = g.create_order('BTC/USDT:USDT', 'stop', 'buy', qty, sl,
        params={'reduceOnly': True})
    print(f"  ✅ {o['type']} {o['side']} stopPrice={o.get('stopPrice')} reduce={o.get('reduceOnly')}")
except Exception as e:
    print(f"  ❌ {str(e)[:150]}")

# 方案3: 用 stop_loss_price 参数
print("\n  方案3: stopLossPrice param")
try:
    o = g.private_futures_post_settle_dual_comp_position_contract({
        'settle': 'usdt',
        'contract': 'BTC_USDT',
        'stop_loss': {'trigger_price': str(sl), 'rule': 1}
    })
    print(f"  ✅ {json.dumps(o)}")
except Exception as e:
    print(f"  ❌ {str(e)[:150]}")

print("\n3. 当前所有单:")
for o in g.fetch_open_orders('BTC/USDT:USDT'):
    print(f'  limit: {o["side"]} {o["amount"]} @ {o.get("price","MKT")} reduce={o.get("reduceOnly")} stop={o.get("stopPrice")}')
    print(f'    status={o.get("status")} info.status={o.get("info",{}).get("status")}')
    for k in ('stop_price','is_reduce_only','is_close','stp_act'):
        print(f'    {k}={o.get("info",{}).get(k)}')
for o in g.private_futures_get_settle_price_orders({'settle':'usdt','status':'open','contract':'BTC_USDT'}):
    t = o.get('trigger',{})
    print(f'  条件单: trigger={t.get("price")} rule={t.get("rule")} dir={o.get("direction")}')
