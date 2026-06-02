#!/usr/bin/env python3
"""五策略 manage_positions 改造: 纯条件单模式"""
import py_compile

PATCHES = [
    ('auto_trade.py',       'long_pos',       'short_pos',       'BTC'),
    ('auto_trade_hype.py',  'long_positions', 'short_positions', 'HYPE'),
    ('auto_trade_near.py',  'long_poss',      'short_poss',      'NEAR'),
    ('auto_trade_zec.py',   'long_pos',       'short_pos',       'ZEC'),
    ('auto_trade_xlm.py',   'long_pos',       'short_pos',       'XLM'),
]

for filename, lk, sk, sym in PATCHES:
    with open(filename) as f:
        code = f.read()
    
    new_body = '''
def manage_positions(state, price, signal, reason, kl_time, indicators=None):
    """纯条件单模式: 交易所闭环, 只做状态同步"""
    lk = '__LK__'
    sk = '__SK__'
    # ① 同步交易所 → 检测条件单已平仓
    try:
        positions = trade_gate.fetch_positions(symbols=[SYMBOL])
        exch_long_ct = 0; exch_short_ct = 0
        for p in positions:
            if float(p.get('contracts', 0)) > 0:
                if p.get('side') == 'long': exch_long_ct += 1
                else: exch_short_ct += 1

        lps = state.get(lk, [])
        sps = state.get(sk, [])

        if exch_long_ct < len(lps):
            removed = len(lps) - exch_long_ct
            if exch_long_ct > 0:
                state[lk] = lps[:exch_long_ct]
            else:
                state[lk] = []
            log(f"🔄 LONG条件单触发 -{removed}仓 (剩余{exch_long_ct})")
            state['last_exit_kl_time'] = kl_time
            save_state(state)

        if exch_short_ct < len(sps):
            removed = len(sps) - exch_short_ct
            if exch_short_ct > 0:
                state[sk] = sps[:exch_short_ct]
            else:
                state[sk] = []
            log(f"🔄 SHORT条件单触发 -{removed}仓 (剩余{exch_short_ct})")
            state['last_exit_kl_time'] = kl_time
            save_state(state)

        lps = state.get(lk, [])
        sps = state.get(sk, [])
    except Exception as e:
        log(f"同步异常: {e}")
        lps = state.get(lk, [])
        sps = state.get(sk, [])

    # ② 刷新条件单
    if lps:
        try:
            ensure_sltp(trade_gate, SYMBOL, "LONG", lps,
                       TAKE_PROFIT_PCT, STOP_LOSS_PCT, GATE_CONTRACT_SIZE, log_fn=log)
        except: pass
    else:
        try:
            from sltp_guard import _clear_direction_orders
            _clear_direction_orders(trade_gate, SYMBOL, "LONG", log)
        except: pass

    if sps:
        try:
            ensure_sltp(trade_gate, SYMBOL, "SHORT", sps,
                       TAKE_PROFIT_PCT, STOP_LOSS_PCT, GATE_CONTRACT_SIZE, log_fn=log)
        except: pass
    else:
        try:
            from sltp_guard import _clear_direction_orders
            _clear_direction_orders(trade_gate, SYMBOL, "SHORT", log)
        except: pass

    # ③ 冷却检查
    if kl_time <= state.get('last_exit_kl_time', 0):
        return

    # ④ 开仓
    if signal == 'LONG' and len(lps) < MAX_POS_PER_SIDE:
        ep = do_open('LONG', price, reason)
        if ep:
            if indicators:
                log_entry('__SYM__', 'LONG', ep, indicators)
            state.setdefault(lk, []).append(
                {'entry': ep, 'signal': reason, 'open_time': datetime.now().isoformat()})
            save_state(state)
            try:
                ensure_sltp(trade_gate, SYMBOL, "LONG", state.get(lk, []),
                           TAKE_PROFIT_PCT, STOP_LOSS_PCT, GATE_CONTRACT_SIZE, log_fn=log)
            except: pass
    elif signal == 'SHORT' and len(sps) < MAX_POS_PER_SIDE:
        ep = do_open('SHORT', price, reason)
        if ep:
            if indicators:
                log_entry('__SYM__', 'SHORT', ep, indicators)
            state.setdefault(sk, []).append(
                {'entry': ep, 'signal': reason, 'open_time': datetime.now().isoformat()})
            save_state(state)
            try:
                ensure_sltp(trade_gate, SYMBOL, "SHORT", state.get(sk, []),
                           TAKE_PROFIT_PCT, STOP_LOSS_PCT, GATE_CONTRACT_SIZE, log_fn=log)
            except: pass
'''
    new_body = new_body.replace('__LK__', lk).replace('__SK__', sk).replace('__SYM__', sym).strip('\n') + '\n'
    
    # 替换旧 manage_positions
    start = code.index('def manage_positions')
    rest = code[start:]
    next_def = rest.find('\ndef ', 10)
    if next_def < 0:
        next_def = rest.find('\n#===', 10)
    if next_def < 0:
        next_def = len(rest)
    
    old_len = len(rest[:next_def])
    new_code = code[:start] + new_body + code[start + old_len:]
    
    with open(filename, 'w') as f:
        f.write(new_code)
    
    try:
        py_compile.compile(filename, doraise=True)
        print(f"✅ {filename}")
    except py_compile.PyCompileError as e:
        print(f"❌ {filename}: {e}")

print("\n全部改造完成")
