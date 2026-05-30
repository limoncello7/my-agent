import json
import os
import time
import urllib.request
import subprocess
from datetime import datetime, timedelta
from collections import deque

STOCK_CODE = "sz300424"
STOCK_NAME = "航新科技"
OUTPUT_DIR = os.path.expanduser("~/.claude/stock_data")
STATE_FILE = os.path.join(OUTPUT_DIR, "monitor_state.json")
HISTORY_FILE = os.path.join(OUTPUT_DIR, "hangxin_history.jsonl")
ALERT_LOG = os.path.join(OUTPUT_DIR, "escape_alerts.log")

FETCH_INTERVAL = 60
SAMPLES_MAXLEN = 30

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

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "alerted_escape_types": [],
        "snapshots": {},
        "last_amount": 0,
    }

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_history(days=5):
    if not os.path.exists(HISTORY_FILE):
        return []
    records = []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                r = json.loads(line.strip())
                if r.get("date", "") >= cutoff:
                    records.append(r)
            except:
                continue
    return records

def fetch_eastmoney_fund_flow(stock_code="300424"):
    """获取东方财富实时主力资金流向"""
    secid = f"0.{stock_code}" if stock_code.startswith("3") or stock_code.startswith("0") else f"1.{stock_code}"
    url = (
        f"https://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?fltt=2&invt=2&fields=f2,f3,f12,f14,f62,f66,f69,f72,f75,f78,f81,f84,f87,f124"
        f"&secids={secid}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        diff = data.get("data", {}).get("diff", [])
        if diff:
            d = diff[0]
            return {
                "current": d.get("f2"),
                "change_pct": d.get("f3"),
                "main_net": d.get("f62", 0),
                "super_net": d.get("f66", 0),
                "super_ratio": d.get("f69", 0),
                "large_net": d.get("f72", 0),
                "large_ratio": d.get("f75", 0),
                "mid_net": d.get("f78", 0),
                "mid_ratio": d.get("f81", 0),
                "small_net": d.get("f84", 0),
                "small_ratio": d.get("f87", 0),
                "timestamp": d.get("f124"),
            }
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Fund flow fetch error: {e}")
    return None


def fetch_eastmoney_fund_history(stock_code="300424"):
    """获取近5日主力净流入历史（从stock/get接口f178字段）"""
    secid = f"0.{stock_code}" if stock_code.startswith("3") or stock_code.startswith("0") else f"1.{stock_code}"
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?ut=fa5fd1943c7b386f172d6893dbfba10b&fltt=2&invt=2&volt=2"
        f"&fields=f178&secid={secid}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = data.get("data", {}).get("f178", "[]")
        return json.loads(raw)
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Fund history fetch error: {e}")
    return []


def calc_avg_amount(history_records):
    daily_amount = {}
    for r in history_records:
        d = r.get("date")
        if d:
            daily_amount[d] = max(daily_amount.get(d, 0), r.get("amount", 0))
    amounts = list(daily_amount.values())
    if len(amounts) >= 2:
        return sum(amounts) / len(amounts)
    return 0

def popup(title, message, critical=False):
    """非阻塞弹窗：严重信号用 MessageBox，普通通知用 Windows Toast"""
    if critical:
        # 严重信号：强制弹窗（置顶）
        ps_cmd = (
            'Add-Type -AssemblyName System.Windows.Forms; '
            '$form = New-Object System.Windows.Forms.Form; '
            '$form.TopMost = $true; '
            '$form.ShowInTaskbar = $true; '
            '[System.Windows.Forms.MessageBox]::Show($form, "' + message.replace('"', '`"') + '", "' + title.replace('"', '`"') + '", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)'
        )
    else:
        # 普通信号：Windows 通知中心 Toast（不阻塞）
        ps_cmd = (
            '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; '
            '$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); '
            '$template.GetElementsByTagName("text")[0].AppendChild($template.CreateTextNode("' + title.replace('"', '`"') + '")) | Out-Null; '
            '$template.GetElementsByTagName("text")[1].AppendChild($template.CreateTextNode("' + message.replace('"', '`"') + '")) | Out-Null; '
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("航新监控").Show($template)'
        )
    try:
        subprocess.Popen(
            ['powershell', '-Command', ps_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE if critical else 0
        )
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Popup failed: {e}")

def detect_escape(data, state, samples, avg_amount_5d, fund_data=None, fund_history=None):
    alerts = []
    score = 0
    now = datetime.now()
    current = data["current"]
    open_p = data["open"]
    high = data["high"]
    low = data["low"]
    prev = data["previous_close"]
    amount = data["amount"]
    change_pct = (current - prev) / prev * 100 if prev > 0 else 0

    # 维度1: 放量下跌
    if change_pct < -1.5 and avg_amount_5d > 0:
        ratio = amount / avg_amount_5d
        hm = now.hour * 100 + now.minute
        progress = 0.3
        if 930 <= hm <= 1130:
            progress = (hm - 930) / 200 * 0.4
        elif 1300 <= hm <= 1500:
            progress = 0.4 + (hm - 1300) / 200 * 0.6
        expected_ratio = progress
        if ratio > expected_ratio * 2.0:
            alerts.append(f"出逃-巨量下跌: 成交额已达前5日均值{ratio:.1f}倍")
            score += 35
        elif ratio > expected_ratio * 1.5:
            alerts.append(f"出逃-明显放量: 成交额为前5日均值{ratio:.1f}倍")
            score += 20

    # 维度2: 冲高回落
    if high > open_p * 1.005:
        drop_from_high = (high - current) / high * 100
        if drop_from_high > 3 and change_pct < -1.5:
            alerts.append(f"出逃-冲高回落: 从高点{high}回撤{drop_from_high:.1f}%")
            score += 25
        elif drop_from_high > 5:
            alerts.append(f"出逃-深度回落: 从高点{high}暴跌{drop_from_high:.1f}%")
            score += 30

    # 维度3: 高开低走
    if open_p > prev * 1.005:
        drop_from_open = (open_p - current) / open_p * 100
        if drop_from_open > 4:
            alerts.append(f"出逃-高开闷杀: 高开{((open_p/prev-1)*100):.1f}%后跌{drop_from_open:.1f}%")
            score += 30
        elif drop_from_open > 2.5 and change_pct < -1:
            alerts.append(f"出逃-高开低走: 开盘即巅峰，无反弹")
            score += 18

    # 维度4: 尾盘急杀
    if (now.hour == 14 and now.minute >= 30) or now.hour == 15:
        snap_key = f"{data['date']}_1430"
        if snap_key in state["snapshots"]:
            price_1430 = state["snapshots"][snap_key]["current"]
            amount_1430 = state["snapshots"][snap_key]["amount"]
            drop_since_1430 = (price_1430 - current) / price_1430 * 100
            amount_after_1430 = amount - amount_1430
            if drop_since_1430 > 1.5:
                alerts.append(f"出逃-尾盘跳水: 14:30后急杀{drop_since_1430:.1f}%")
                score += 22
            if amount_after_1430 > avg_amount_5d * 0.25:
                alerts.append(f"出逃-尾盘放量: 14:30后成交{amount_after_1430/1e8:.1f}亿")
                score += 15

    # 维度5: 持续创新低
    if len(samples) >= 5:
        recent_lows = [s["current"] for s in list(samples)[-5:]]
        if all(recent_lows[i] <= recent_lows[i-1] for i in range(1, len(recent_lows))):
            if change_pct < -2:
                alerts.append("出逃-持续杀跌: 连续5分钟无反弹，空头主导")
                score += 20

    # 维度6: 跌破关键心理位
    if current < open_p and open_p > prev:
        alerts.append(f"出逃-翻绿: 跌破开盘价{open_p}")
        score += 10
    if current < prev and open_p > prev * 1.01:
        alerts.append("出逃-高开翻绿: 开盘诱多后翻绿")
        score += 12

    # 维度7: 概念退潮联动
    if change_pct < -4:
        alerts.append(f"注意-概念退潮: 大跌{change_pct:.1f}%，低空/军工板块可能整体调整")
        score += 10

    # 维度8: 主力资金流向（新增）
    if fund_data:
        main_net = fund_data.get("main_net", 0)
        super_net = fund_data.get("super_net", 0)
        large_net = fund_data.get("large_net", 0)
        small_net = fund_data.get("small_net", 0)

        # 8A: 股价大跌但主力逆势流入 -> 洗盘/换庄信号，降低出逃评分
        if change_pct < -5 and main_net > 10000000:
            alerts.append(f"主力异动: 股价大跌{change_pct:.1f}%但主力净流入{main_net/1e4:.0f}万，疑似洗盘/换庄")
            score -= 15

        # 8B: 主力大额净流出
        if main_net < -50000000:
            alerts.append(f"出逃-主力抛售: 主力净流出{abs(main_net)/1e4:.0f}万")
            score += 25
        elif main_net < -10000000:
            alerts.append(f"注意-主力流出: 主力净流出{abs(main_net)/1e4:.0f}万")
            score += 15

        # 8C: 超大单和大单同步流出（机构一致看空）
        if super_net < 0 and large_net < 0:
            alerts.append("出逃-机构一致卖出: 超大单和大单同步净流出")
            score += 20

        # 8D: 主力出货给散户
        if main_net < -10000000 and small_net > 5000000:
            alerts.append(f"出逃-散户接盘: 主力卖出{abs(main_net)/1e4:.0f}万，散户买入{small_net/1e4:.0f}万")
            score += 15

        # 8E: 散户恐慌割肉（小单大幅流出）
        if small_net < -10000000 and main_net > 0:
            alerts.append(f"主力吸筹: 散户割肉卖出{abs(small_net)/1e4:.0f}万，主力顺势吸筹")
            score -= 10

    # 维度9: 连续主力资金流向（结合历史）
    if fund_history:
        recent_outflow = sum(1 for h in fund_history if h.get("mainNetAmt", 0) < 0)
        if len(fund_history) >= 3 and recent_outflow >= 3:
            if fund_data and fund_data.get("main_net", 0) < 0:
                alerts.append(f"出逃-主力连续流出: 近{len(fund_history)}日有{recent_outflow}日主力净流出，今日继续流出")
                score += 15
            elif fund_data and fund_data.get("main_net", 0) > 10000000:
                alerts.append(f"主力反转: 近{len(fund_history)}日有{recent_outflow}日流出，但今日主力逆势流入{fund_data['main_net']/1e4:.0f}万")
                score -= 10

    if score >= 60:
        alerts.insert(0, f"【严重出逃信号】综合评分{score}/100")
    elif score >= 40:
        alerts.insert(0, f"【明显出逃信号】综合评分{score}/100")
    elif score >= 20:
        alerts.insert(0, f"【轻度出逃信号】综合评分{score}/100")

    return alerts, score

def is_trading_time():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hour, minute = now.hour, now.minute
    hm = hour * 100 + minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    state = load_state()
    samples = deque(maxlen=SAMPLES_MAXLEN)

    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_date") != today:
        state["alerted_escape_types"] = []
        state["snapshots"] = {}
        state["last_date"] = today
        print(f"[{datetime.now().isoformat()}] 新交易日，状态已重置")

    history = load_history(days=7)
    avg_amount_5d = calc_avg_amount(history)

    # 预加载主力资金历史
    fund_history = fetch_eastmoney_fund_history("300424")

    print(f"[{datetime.now().isoformat()}] 航新科技出逃监控启动（含主力资金维度）")
    print(f"前5日成交额均值: {avg_amount_5d/1e8:.2f}亿" if avg_amount_5d > 0 else "历史数据不足，放量判断暂不可用")
    fund_history_str = [f"{h['date']}: {h['mainNetAmt']/1e4:.0f}万" for h in fund_history]
    print(f"近5日主力流向: {fund_history_str}")
    print(f"抓取间隔: {FETCH_INTERVAL}秒 | 按 Ctrl+C 停止\n")

    last_time = None

    while True:
        if not is_trading_time():
            time.sleep(300)
            continue

        data = fetch_sina_realtime(STOCK_CODE)
        if not data:
            time.sleep(FETCH_INTERVAL)
            continue

        if data["time"] == last_time:
            time.sleep(FETCH_INTERVAL)
            continue
        last_time = data["time"]

        # 同时获取主力资金数据
        fund_data = fetch_eastmoney_fund_flow("300424")

        now = datetime.now()
        if now.hour == 14 and now.minute == 30:
            snap_key = f"{data['date']}_1430"
            if snap_key not in state["snapshots"]:
                state["snapshots"][snap_key] = {
                    "current": data["current"],
                    "amount": data["amount"],
                    "time": data["time"]
                }
                save_state(state)

        samples.append({
            "time": data["time"],
            "current": data["current"],
            "amount": data["amount"]
        })

        alerts, score = detect_escape(data, state, samples, avg_amount_5d, fund_data, fund_history)

        new_alerts = []
        for a in alerts:
            sig_type = a.split(":")[0] if ":" in a else a
            if sig_type not in state["alerted_escape_types"]:
                state["alerted_escape_types"].append(sig_type)
                new_alerts.append(a)

        if new_alerts:
            alert_text = "\n".join(new_alerts)
            with open(ALERT_LOG, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now().isoformat()}] {data['time']} {data['current']} fund={fund_data.get('main_net') if fund_data else 'N/A'} {new_alerts}\n")
            print(f"[{datetime.now().isoformat()}] ALERT {data['time']} {data['current']} {new_alerts}")

            # 弹窗通知：严重信号用 MessageBox，其他用 Toast
            is_critical = score >= 40
            change_pct = (data["current"]/data["previous_close"]-1)*100
            popup(
                title=f"航新科技 {data['current']} ({change_pct:+.1f}%)",
                message=alert_text,
                critical=is_critical
            )
        else:
            change_pct = (data["current"]/data["previous_close"]-1)*100
            fund_str = f"主力{fund_data['main_net']/1e4:.0f}万" if fund_data else "无资金数据"
            print(f"[{datetime.now().isoformat()}] {data['time']} {STOCK_NAME} {data['current']} ({change_pct:+.2f}%) 出逃评分:{score} {fund_str}")

        save_state(state)
        time.sleep(FETCH_INTERVAL)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n监控已停止")
