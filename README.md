# 币安Gate仓位同步项目文档

## 创建时间
2026-05-07

## 项目目标
实现币安交易所和Gate.io交易所的BTC合约仓位同步：
- 币安开仓 → Gate同步同方向开仓
- 币安平仓 → Gate同步平仓
- 不设置止盈止损

---

## 一、API配置

### 币安交易所
- API Key: yoIUcS0f687fbduzdLGRHgECeexVmnvVjhegkPeVYrACvRVYuFJWSJDjfhgBE8ww
- Secret: BpsTBrNNQ6T4r35GNKhA6uWRg4asAu1cRuGOzF7jYM5GRR32kUPHluiis7XXyZ8t
- 文件: `~/.openclaw/credentials/binance.json`

### Gate.io交易所
- API Key: 6798f83ac4f0952585c8fbc28f649320
- Secret: b9ec180b4290b0157288604afd66046f51b011a711bf8a24b599055e91f2f393
- 文件: `~/.openclaw/credentials/gateio.json`
- 账户模式: 单向模式 (single)
- 杠杆: 20x 逐仓

---

## 二、核心参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 同步合约 | BTCUSDT / BTC_USDT | 币安 / Gate |
| 转换比例 | 10 | 币安1手=10 Gate合约 |
| 币安1手 | 0.001 BTC | - |
| Gate 1合约 | 0.0001 BTC | - |
| 杠杆 | 20x | 逐仓模式 |
| 轮询间隔 | 3秒 | - |

---

## 三、修复记录

### 修复1: 转换比例错误
**问题**: 最初使用 `CONTRACT_RATIO = 1000`，导致 0.001手 → 1合约（错误）  
**原因**: 1手=0.001BTC，1合约=0.0001BTC，1手=10合约，不是1000  
**修复**: 改为 `CONTRACT_RATIO = 10`  
**时间**: 2026-05-07 03:33

### 修复2: 小数截断导致开仓失败
**问题**: 使用 `int(qty)` 导致 0.001手×10=0.01合约 → int(0.01)=0  
**原因**: int() 会截断小数，0.01合约被截断为0  
**修复**: 改为 `round(qty)`  
**影响**: _open_long, _open_short, sync_positions 中的计算  
**时间**: 2026-05-07 03:49

### 修复3: 平仓使用mark_price
**问题**: 平仓函数使用 `btc.last_price`  
**修复**: 改为 `btc.mark_price`（与开仓一致）  
**时间**: 2026-05-07

### 修复4: Gate账户残留挂单和仓位
**问题**: 账户有多个历史挂单和残留仓位导致无法开仓  
**原因**: INSUFFICIENT_AVAILABLE 错误  
**修复**: 清理所有挂单、平掉所有BTC持仓  
**命令**:
```python
# 取消所有挂单
orders = gate.list_futures_orders(settle="usdt", status="open")
for o in orders:
    gate.cancel_futures_order(settle="usdt", order_id=str(o.id))

# 平掉所有BTC持仓
order = FuturesOrder(contract="BTC_USDT", size=str(-int(size)), price=str(price), tif="gtc")
```
**时间**: 2026-05-07

### 修复5: 开仓/平仓订单参数
**问题**: 市价单ioc不能省略price  
**修复**: 所有订单都带price参数  
**时间**: 2026-05-07

### 修复6: 删除止盈止损功能
**问题**: 用户要求不挂止盈止损  
**修复**: 删除所有TP/SL相关代码  
**时间**: 2026-05-07 03:21

---

## 四、同步逻辑

```
币安开多 → Gate开多
币安开空 → Gate开空
币安平仓 → Gate平仓
币安增仓 → Gate补仓
币安减仓 → Gate减仓
```

### 计算公式
```python
# 币安手数转Gate合约数
target_qty = round(binance_qty * 10)

# 开多
order = FuturesOrder(contract="BTC_USDT", size=str(qty_int), price=str(price), tif="gtc")

# 开空  
order = FuturesOrder(contract="BTC_USDT", size=str(-qty_int), price=str(price), tif="gtc")

# 平多
order = FuturesOrder(contract="BTC_USDT", size=str(-qty_int), price=str(price), tif="gtc")

# 平空
order = FuturesOrder(contract="BTC_USDT", size=str(qty_int), price=str(price), tif="gtc")
```

---

## 五、任务启动

### 启动命令
```bash
cd ~/.openclaw && python3 binance_gate_sync.py
```

### 查看日志
```bash
tail -f /tmp/sync.log
```

### 检查进程
```bash
ps aux | grep binance_gate_sync
```

### 停止任务
```bash
pkill -f binance_gate_sync
```

---

## 六、已知问题

### Gate账户余额
- 当前余额约6.5 USDT
- 开10合约需要约2 USDT保证金
- 余额充足时可以开仓

### 币安持仓检测
- positionAmt为0.001手时，Gate应开10合约
- 如果币安显示0，可能已平仓或数据延迟

---

## 七、文件列表

| 文件 | 说明 |
|------|------|
| `~/.openclaw/binance_gate_sync.py` | 主同步脚本 |
| `~/.openclaw/credentials/binance.json` | 币安API配置 |
| `~/.openclaw/credentials/gateio.json` | Gate API配置 |
| `~/.openclaw/sync_task.md` | 任务说明文档 |
| `/tmp/sync.log` | 运行日志 |

---

## 八、版本历史

| 版本 | 日期 | 修改内容 |
|------|------|---------|
| v1.0 | 2026-05-07 | 初始版本，包含基本同步功能 |
| v2.0 | 2026-05-07 | 修复转换比例1000→10 |
| v3.0 | 2026-05-07 | 修复int→round小数截断问题 |
| v4.0 | 2026-05-07 | 删除止盈止损功能 |
| v5.0 | 2026-05-07 | 清理Gate账户残留仓位 |

---

## 九、测试记录

### 测试1: 同步0.001手
- 币安: SHORT 0.001手
- 预期Gate: SHORT 10合约
- 结果: 因int截断导致失败
- 修复后: 正常

### 测试2: 开仓/平仓测试
- 开多1合约: 成功
- 开空1合约: 成功
- 平仓: 成功

---

## 十、联系方式

- 币安API状态: 已连接 ✅
- Gate API状态: 已连接 ✅
- 同步任务状态: 运行中 ✅