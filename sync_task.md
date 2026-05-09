# 币安 -> Gate.io 仓位同步任务 (v8)

## 任务信息
- 脚本: `/root/.openclaw/binance_gate_sync.py`
- 版本: v8（新增开仓通知）
- 日志: `/tmp/sync.log`
- 持仓日志: `/tmp/binance_positions.log`
- 告警日志: `/tmp/position_alerts.log`
- 状态: 运行中
- 同步PID: 1772041
- 健康检查PID: 1771063

## 同步规则
- 币安开仓 → Gate同步同方向开仓
- 币安平仓 → Gate同步平仓
- 杠杆: 20x 逐仓
- 转换: 1 BTC = 10000 Gate合约
- 差异 < 0.5合约跳过（噪声过滤）
- 2秒轮询 | IOC限价单
- 健康检查: 每5分钟自检

## v8 新增功能
- 开仓通知: 币安新开仓/方向翻转时推送企业微信通知
- 通知模板: 方向/杠杆/数量/开仓价/累计计数
- 本地留底: /tmp/position_alerts.log
- 累计计数: /tmp/position_count.txt

## API配置
- 币安: 已配置
- Gate: 已配置

## 运行命令
```bash
# 启动同步
cd ~/.openclaw && nohup python3 binance_gate_sync.py > /dev/null 2>&1 &

# 启动健康检查
cd ~/.openclaw && nohup python3 health_check.py > /tmp/health_check.log 2>&1 &

# 查看日志
tail -f /tmp/sync.log
tail -f /tmp/health_check.log

# 检查状态
ps aux | grep -E 'binance_gate_sync|health_check'
```

## 注意事项
- Gate账户需保持足够USDT余额
- 转换比例: round() 避免小数截断为0
- 通知在无人交互时即时送达，会话活跃时排队投递
