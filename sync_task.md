# 币安 -> Gate.io 仓位同步任务 (v8)

## 任务信息
- 脚本: /root/.openclaw/binance_gate_sync.py
- 版本: v8（开仓通知）
- 同步日志: /tmp/sync.log
- 持仓日志: /tmp/binance_positions.log
- 告警日志: /tmp/position_alerts.log
- 累计计数: /tmp/position_count.txt

## 同步规则
- 币安开仓 → Gate同方向开仓
- 币安平仓 → Gate同步平仓
- 杠杆: 20x 逐仓
- 转换: 1 BTC = 10000 Gate合约
- 差异 < 0.5合约跳过
- 2s轮询 | IOC限价单
- 健康检查: 每5分钟

## v8 新增
- 开仓通知: 新开仓/方向翻转时推送到企业微信
- 本地留底: /tmp/position_alerts.log + /tmp/position_count.txt
