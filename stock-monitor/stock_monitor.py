import json
import os
import time
import urllib.request
from datetime import datetime

STOCK_CODE = "sh600176"
OUTPUT_DIR = os.path.expanduser("~/.claude/stock_data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "china_jushi_realtime.json")
HISTORY_FILE = os.path.join(OUTPUT_DIR, "china_jushi_history.jsonl")
ALERT_LOG = os.path.join(OUTPUT_DIR, "china_jushi_alerts.log")

FETCH_INTERVAL = 60  # 秒

def fetch_sina_realtime(code):
    url = f"https://hq.sinajs.cn/list={code}"
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0'
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read().decode('gbk')
        prefix = f'var hq_str_{code}="'
        if prefix in data:
            content = data.split(prefix)[1].split('";')[0]
            fields = content.split(',')
            if len(fields) >= 33:
                return {
                    "name": fields[0],
                    "open": float(fields[1]),
                    "previous_close": float(fields[2]),
                    "current": float(fields[3]),
                    "high": float(fields[4]),
                    "low": float(fields[5]),
                    "volume": int(fields[8]),
                    "amount": float(fields[9]),
                    "date": fields[30],
                    "time": fields[31],
                }
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Fetch error: {e}")
    return None

def calculate_metrics(data):
    prev = data["previous_close"]
    curr = data["current"]
    change = curr - prev
    change_pct = (change / prev) * 100 if prev > 0 else 0

    alerts = []
    if change_pct <= -5:
        alerts.append(f"紧急: 跌幅超5% ({change_pct:.2f}%)")
    elif change_pct <= -4:
        alerts.append(f"重要: 跌幅超4% ({change_pct:.2f}%)")
    elif change_pct <= -3:
        alerts.append(f"注意: 跌幅超3% ({change_pct:.2f}%)")

    if change_pct >= 5:
        alerts.append(f"注意: 涨幅超5% ({change_pct:.2f}%)")
    elif change_pct >= 4:
        alerts.append(f"注意: 涨幅超4% ({change_pct:.2f}%)")

    if data["amount"] > 1000000000:
        alerts.append(f"放量: 成交额 {data['amount']/1e8:.1f} 亿")

    return {
        "change": round(change, 3),
        "change_pct": round(change_pct, 3),
        "alerts": alerts,
        "timestamp": datetime.now().isoformat(),
    }

def is_trading_time():
    """判断当前是否为A股交易时间（周一至周五 9:30-11:30, 13:00-15:00）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hour, minute = now.hour, now.minute
    hm = hour * 100 + minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"[{datetime.now().isoformat()}] 股票监控启动: 中国巨石(600176)")
    print(f"数据保存至: {OUTPUT_DIR}")
    print(f"抓取间隔: {FETCH_INTERVAL}秒")
    print("按 Ctrl+C 停止\n")

    last_time = None

    while True:
        if not is_trading_time():
            # 非交易时间：降低频率，每5分钟检查一次是否开盘
            time.sleep(300)
            continue

        data = fetch_sina_realtime(STOCK_CODE)
        if not data:
            time.sleep(FETCH_INTERVAL)
            continue

        # 避免重复写入同一分钟的收盘数据
        if data["time"] == last_time:
            time.sleep(FETCH_INTERVAL)
            continue
        last_time = data["time"]

        metrics = calculate_metrics(data)
        record = {**data, **metrics}

        # 保存最新数据
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        # 追加历史
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

        # 记录警报
        if metrics["alerts"]:
            with open(ALERT_LOG, 'a', encoding='utf-8') as f:
                f.write(f"[{record['timestamp']}] {record['current']} {metrics['alerts']}\n")
            print(f"[{record['timestamp']}] ALERT {record['current']} {metrics['change_pct']}% -> {metrics['alerts']}")
        else:
            print(f"[{record['timestamp']}] {record['name']} {record['current']} ({metrics['change_pct']}%)")

        time.sleep(FETCH_INTERVAL)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n监控已停止")
