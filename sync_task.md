# 币安 -> Gate.io 仓位同步任务

## 任务信息
- 脚本: `/root/.openclaw/binance_gate_sync.py`
- 日志: `/tmp/sync.log`
- 状态: 运行中
- 进程ID: 910233

## 同步规则
- 币安开仓 → Gate同步同方向开仓
- 币安平仓 → Gate同步平仓
- 杠杆: 20x 逐仓
- 无止盈止损
- 转换: 币安1手 = 10 Gate合约

## API配置
- 币安: 已配置
- Gate: 已配置

## 运行命令
```bash
# 启动
cd ~/.openclaw && python3 binance_gate_sync.py

# 查看日志
tail -f /tmp/sync.log

# 检查状态
ps aux | grep binance_gate_sync
```

## 注意事项
- Gate账户需保持足够USDT余额
- 转换比例: round() 避免小数截断为0