#!/bin/bash
# gate-sync supervisor 启动器 v15
# 管理三进程：sync / notifier / watchdog
# 用法: bash start_gate_sync.sh {start|stop|status|restart}

set -e

SYNC_SCRIPT="$HOME/.openclaw/binance_gate_sync.py"
NOTIFIER_SCRIPT="$HOME/.openclaw/alert_notifier.py"
WATCHDOG_SCRIPT="$HOME/.openclaw/gate_sync_watchdog.py"

SYNC_PID="/tmp/sync.pid"
NOTIFIER_PID="/tmp/notifier.pid"
WATCHDOG_PID="/tmp/watchdog.pid"

SYNC_LOG="/tmp/sync.log"
NOTIFIER_LOG="/tmp/notifier.log"
WATCHDOG_LOG="/tmp/watchdog.log"

kill_pidfile() {
    local pf=$1
    local name=$2
    if [ -f "$pf" ]; then
        local pid=$(cat "$pf")
        if kill -0 "$pid" 2>/dev/null; then
            echo "停止 $name (PID: $pid)..."
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pf"
    fi
}

start_proc() {
    local script=$1
    local pidfile=$2
    local logfile=$3
    local name=$4

    if [ -f "$pidfile" ] && kill -0 "$(cat $pidfile)" 2>/dev/null; then
        echo "$name 已在运行 (PID: $(cat $pidfile))"
        return
    fi

    echo "启动 $name ..."
    nohup python3 "$script" >> "$logfile" 2>&1 &
    sleep 1
    if [ -f "$pidfile" ]; then
        echo "  → PID: $(cat $pidfile)"
    fi
}

case "${1:-status}" in
    start)
        echo "=== gate-sync supervisor 启动 ==="
        # 清理残余
        rm -f /tmp/alert_queue.jsonl /tmp/alert_cursor.txt /tmp/pending_alert.flag

        start_proc "$SYNC_SCRIPT"     "$SYNC_PID"     "$SYNC_LOG"     "sync"
        sleep 2
        start_proc "$NOTIFIER_SCRIPT" "$NOTIFIER_PID" "$NOTIFIER_LOG" "notifier"
        sleep 1
        start_proc "$WATCHDOG_SCRIPT" "$WATCHDOG_PID" "$WATCHDOG_LOG" "watchdog"

        echo "=== 全部启动完成 ==="
        ;;

    stop)
        echo "=== gate-sync supervisor 停止 ==="
        kill_pidfile "$WATCHDOG_PID"  "watchdog"
        kill_pidfile "$NOTIFIER_PID"  "notifier"
        kill_pidfile "$SYNC_PID"      "sync"
        echo "=== 全部停止 ==="
        ;;

    restart)
        $0 stop
        sleep 2
        $0 start
        ;;

    status)
        echo "=== gate-sync 状态 ==="
        for pf in "$SYNC_PID" "$NOTIFIER_PID" "$WATCHDOG_PID"; do
            name=$(basename "$pf" .pid)
            if [ -f "$pf" ] && kill -0 "$(cat $pf)" 2>/dev/null; then
                echo "✅ $name: PID $(cat $pf)"
            else
                echo "❌ $name: 未运行"
            fi
        done

        # 队列状态
        if [ -f /tmp/alert_queue.jsonl ]; then
            total=$(wc -l < /tmp/alert_queue.jsonl)
            cursor=$(cat /tmp/alert_cursor.txt 2>/dev/null || echo 0)
            pending=$((total - cursor))
            echo "📊 队列: $total 条, 待消费 $pending 条"
        else
            echo "📊 队列: 空"
        fi

        # delivery-queue 待恢复
        dq_count=$(ls ~/.openclaw/delivery-queue/*.json 2>/dev/null | wc -l)
        echo "📬 delivery-queue: $dq_count 条待恢复"
        ;;

    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
