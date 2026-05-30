import json
import os
import urllib.request
from datetime import datetime

STOCK_CODE = "sh600176"  # 中国巨石 上海
OUTPUT_DIR = os.path.expanduser("~/.claude/stock_data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "china_jushi_realtime.json")
HISTORY_FILE = os.path.join(OUTPUT_DIR, "china_jushi_history.jsonl")

def fetch_sina_realtime(code):
    """从新浪行情接口获取实时数据"""
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
                    "bid": float(fields[6]),
                    "ask": float(fields[7]),
                    "volume": int(fields[8]),
                    "amount": float(fields[9]),
                    "date": fields[30],
                    "time": fields[31],
                }
    except Exception as e:
        print(f"Fetch error: {e}")
    return None

def calculate_metrics(data):
    """计算涨跌幅和简单预警指标"""
    prev = data["previous_close"]
    curr = data["current"]
    change = curr - prev
    change_pct = (change / prev) * 100 if prev > 0 else 0

    alerts = []
    if change_pct <= -4:
        alerts.append(f"跌幅超4%: {change_pct:.2f}%")
    elif change_pct <= -3:
        alerts.append(f"跌幅超3%: {change_pct:.2f}%")

    if change_pct >= 4:
        alerts.append(f"涨幅超4%: {change_pct:.2f}%")
    elif change_pct >= 3:
        alerts.append(f"涨幅超3%: {change_pct:.2f}%")

    # 量比估算（对比前5日平均成交量）
    # 这里简化：若成交额超过10亿视为放量
    if data["amount"] > 1000000000:
        alerts.append(f"放量成交: {data['amount']/1e8:.1f}亿")

    return {
        "change": round(change, 3),
        "change_pct": round(change_pct, 3),
        "alerts": alerts,
        "timestamp": datetime.now().isoformat(),
    }

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    data = fetch_sina_realtime(STOCK_CODE)
    if not data:
        print("Failed to fetch stock data")
        return 1

    metrics = calculate_metrics(data)
    record = {**data, **metrics}

    # 保存最新数据
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    # 追加历史记录
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

    print(f"[{record['timestamp']}] {record['name']} 当前价: {record['current']}  涨跌: {record['change_pct']}%")
    if record['alerts']:
        print("ALERTS:", record['alerts'])

    return 0

if __name__ == '__main__':
    exit(main())
