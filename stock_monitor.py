#!/usr/bin/env python3
"""
股票监控脚本 — QQQ / 512890 / 159558 / 513500
每天12:30（北京时间）由 GitHub Actions 自动执行
生成 HTML 面板并部署到 GitHub Pages
"""

import json
import urllib.request
import datetime
import math
import os
import sys
from collections import defaultdict

# ============================================================
# 配置
# ============================================================

# 159558 持仓
HOLDINGS = {
    "shares": 80100,
    "cost": 1.284,
    "cash": 50000,
    "tiers": [
        (1.05, 1.06, 20000, "第一档"),
        (1.02, 1.03, 15000, "第二档"),
        (0.95, 1.00, 15000, "第三档"),
    ],
}

# 159558 拆股：2026-07-09 1拆3，此前数据 ÷3
SPLIT_DATE = "2026-07-09"
STOP_LOSS_MA200 = 0.85  # 200日线止损线（近似）

# 文件输出路径
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "index.html")

# ============================================================
# 工具函数
# ============================================================


def http_get(url):
    """HTTP GET 请求"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def calc_ma(closes, period):
    """简单移动平均"""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_rsi(closes, period=14):
    """RSI 计算"""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_weekly_ma(closes_with_dates, period=60):
    """从日K数据构建周K，计算周线MA。日期格式 YYYY-MM-DD"""
    weekly = []
    current_week = None
    current_close = None
    for item in closes_with_dates:
        d = item["date"]
        c = item["close"]
        # ISO week
        dt = datetime.date.fromisoformat(d)
        iso_year, iso_week, _ = dt.isocalendar()
        week_key = (iso_year, iso_week)
        current_close = c
        if week_key != current_week:
            if current_week is not None and current_close is not None:
                weekly.append(current_close)
            current_week = week_key
        current_close = c
    # 最后一根
    if current_close is not None:
        weekly.append(current_close)
    if len(weekly) < period:
        return None
    return sum(weekly[-period:]) / period


# ============================================================
# 数据获取
# ============================================================


def fetch_qqq():
    """获取QQQ日K数据"""
    url = (
        "https://stock.finance.sina.com.cn/usstock/api/json_v2.php/"
        "US_MinKService.getDailyK?symbol=qqq&type=daily&num=400"
    )
    data = http_get(url)
    closes = []
    closes_with_dates = []
    for item in data:
        try:
            c = float(item["c"])
            d = item["d"]
            closes.append(c)
            closes_with_dates.append({"date": d, "close": c})
        except (KeyError, ValueError):
            continue
    price = closes[-1] if closes else None
    ma80 = calc_ma(closes, 80)
    ma60_weekly = calc_weekly_ma(closes_with_dates, 60)
    return {
        "name": "QQQ",
        "code": "usQQQ",
        "price": price,
        "ma80": ma80,
        "ma60_weekly": ma60_weekly,
        "ma80_095": ma80 * 0.95 if ma80 else None,
        "raw_closes": closes,
    }


def fetch_a_share(symbol, full_code, datalen=300):
    """获取A股（ETF）日K数据"""
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={full_code}&scale=240&ma=no&datalen={datalen}"
    )
    data = http_get(url)
    closes = []
    for item in data:
        try:
            d = item["day"]
            c = float(item["close"])
            # 159558 拆股前复权
            if full_code == "sz159558" and d <= SPLIT_DATE:
                c = c / 3.0
            closes.append(c)
        except (KeyError, ValueError):
            continue
    price = closes[-1] if closes else None
    ma80 = calc_ma(closes, 80)
    ma200 = calc_ma(closes, 200) if len(closes) >= 200 else None
    rsi14 = calc_rsi(closes, 14) if symbol == "512890" else None
    return {
        "name": symbol,
        "code": full_code,
        "price": price,
        "ma80": ma80,
        "ma80_095": ma80 * 0.95 if ma80 else None,
        "ma200": ma200,
        "rsi14": rsi14,
        "raw_closes": closes,
    }


# ============================================================
# 触发判断
# ============================================================


def check_qqq(data):
    """QQQ: 条件1 跌破80MA×0.95；条件2 周线60MA下方"""
    if data["price"] is None or data["ma80"] is None:
        return None
    below_ma80_095 = data["price"] < data["ma80_095"]
    below_weekly = (
        data["ma60_weekly"] is not None and data["price"] < data["ma60_weekly"]
    )
    triggered = below_ma80_095 and below_weekly
    return {
        "triggered": triggered,
        "conditions": [
            {
                "label": "价格 < 80MA×0.95",
                "met": below_ma80_095,
                "detail": f"{data['price']:.2f} < {data['ma80_095']:.2f}",
            },
            {
                "label": "价格 < 周线60MA",
                "met": below_weekly,
                "detail": (
                    f"{data['price']:.2f} < {data['ma60_weekly']:.2f}"
                    if data["ma60_weekly"]
                    else "周线数据不足"
                ),
            },
        ],
    }


def check_512890(data):
    """512890: 价格<80MA×0.95 且 RSI<30"""
    if data["price"] is None or data["ma80"] is None or data["rsi14"] is None:
        return None
    below_ma = data["price"] < data["ma80_095"]
    rsi_low = data["rsi14"] < 30
    return {
        "triggered": below_ma and rsi_low,
        "conditions": [
            {
                "label": "价格 < 80MA×0.95",
                "met": below_ma,
                "detail": f"{data['price']:.3f} < {data['ma80_095']:.3f}",
            },
            {
                "label": "RSI(14) < 30",
                "met": rsi_low,
                "detail": f"RSI={data['rsi14']:.1f}",
            },
        ],
    }


def check_159558(data):
    """159558: 价格>MA200 且 价格<MA80×0.95"""
    if data["price"] is None or data["ma80"] is None or data["ma200"] is None:
        return None
    above_ma200 = data["price"] > data["ma200"]
    below_ma80_095 = data["price"] < data["ma80_095"]
    return {
        "triggered": above_ma200 and below_ma80_095,
        "conditions": [
            {
                "label": "价格 > 200日均线",
                "met": above_ma200,
                "detail": f"{data['price']:.3f} > {data['ma200']:.3f}",
            },
            {
                "label": "价格 < 80MA×0.95",
                "met": below_ma80_095,
                "detail": f"{data['price']:.3f} < {data['ma80_095']:.3f}",
            },
        ],
    }


def check_513500(data):
    """513500: 跌破80MA×0.95 + 250日SMA下方"""
    if data["price"] is None or data["ma80"] is None:
        return None
    below_ma80_095 = data["price"] < data["ma80_095"]
    ma250 = calc_ma(data["raw_closes"], 250)
    below_ma250 = ma250 is not None and data["price"] < ma250
    return {
        "triggered": below_ma80_095 and below_ma250,
        "conditions": [
            {
                "label": "价格 < 80MA×0.95",
                "met": below_ma80_095,
                "detail": f"{data['price']:.3f} < {data['ma80_095']:.3f}",
            },
            {
                "label": "价格 < 250日SMA",
                "met": below_ma250,
                "detail": (
                    f"{data['price']:.3f} < {ma250:.3f}" if ma250 else "数据不足"
                ),
            },
        ],
    }


# ============================================================
# HTML 生成
# ============================================================


def bar_svg(
    bars,
    width=380,
    height=220,
):
    """生成 SVG 柱状图"""
    n = len(bars)
    if n == 0:
        return '<svg width="380" height="60"><text y="30" fill="#999">暂无数据</text></svg>'
    bar_w = min(70, (width - 60) // n)
    gap = (width - bar_w * n) / (n + 1)
    max_val = max(b["value"] for b in bars)
    min_val = min(b["value"] for b in bars)
    range_val = max(max_val - min_val, max_val * 0.1, 1)
    plot_bottom = height - 35
    plot_top = 20

    svg_parts = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
    ]
    # 背景网格线
    for i in range(5):
        y = plot_top + (plot_bottom - plot_top) * i / 4
        svg_parts.append(
            f'<line x1="20" x2="{width-20}" y1="{y:.0f}" y2="{y:.0f}" stroke="#E5EAF1" stroke-width="0.5"/>'
        )

    for i, bar in enumerate(bars):
        val = bar["value"]
        bar_h = max(4, (val - min_val) / range_val * (plot_bottom - plot_top))
        x = gap + i * (bar_w + gap)
        y = plot_bottom - bar_h
        color = bar.get("color", "#071B3A")

        # 柱体
        svg_parts.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{bar_w:.0f}" '
            f'height="{bar_h:.0f}" fill="{color}" rx="3"/>'
        )
        # 顶部数值
        svg_parts.append(
            f'<text x="{x + bar_w/2:.0f}" y="{y - 6:.0f}" text-anchor="middle" '
            f'font-size="11" font-weight="700" fill="{color}">{bar["label_val"]}</text>'
        )
        # 底部标签
        svg_parts.append(
            f'<text x="{x + bar_w/2:.0f}" y="{plot_bottom + 14:.0f}" '
            f'text-anchor="middle" font-size="10" fill="#65758A">{bar["label"]}</text>'
        )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def build_bar(stock_data, bar_specs):
    """根据 bar_specs 构建柱子数据"""
    bars = []
    for spec in bar_specs:
        key = spec["key"]
        val = stock_data.get(key)
        if val is None:
            continue
        bars.append(
            {
                "label": spec["label"],
                "value": val,
                "color": spec.get("color", "#071B3A"),
                "label_val": spec.get("fmt", "{}").format(val),
            }
        )
    return bars


def format_price(v, is_us=False):
    """格式化价格"""
    if is_us:
        return f"${v:.2f}"
    return f"¥{v:.3f}"


def build_card(stock, check_result):
    """生成单只股票卡片 HTML"""
    name = stock["name"]
    code = stock["code"]
    is_triggered = check_result["triggered"] if check_result else False
    badge_class = "triggered" if is_triggered else "safe"
    badge_text = "🔴 已触发" if is_triggered else "✅ 未触发"

    # 构建柱子
    if name == "QQQ":
        bar_specs = [
            {"key": "price", "label": "实时价", "color": "#071B3A", "fmt": lambda v: f"${v:.2f}"},
            {"key": "ma80", "label": "80日均线", "color": "#2BC4B6", "fmt": lambda v: f"${v:.2f}"},
            {"key": "ma80_095", "label": "80MA×0.95", "color": "#e74c3c", "fmt": lambda v: f"${v:.2f}"},
            {"key": "ma60_weekly", "label": "周线60MA", "color": "#65758A", "fmt": lambda v: f"${v:.2f}"},
        ]
    elif name == "512890":
        bar_specs = [
            {"key": "price", "label": "实时价", "color": "#071B3A", "fmt": lambda v: f"¥{v:.3f}"},
            {"key": "ma80", "label": "80日均线", "color": "#2BC4B6", "fmt": lambda v: f"¥{v:.3f}"},
            {"key": "ma80_095", "label": "80MA×0.95", "color": "#e74c3c", "fmt": lambda v: f"¥{v:.3f}"},
        ]
    elif name == "159558":
        bar_specs = [
            {"key": "price", "label": "实时价", "color": "#071B3A", "fmt": lambda v: f"¥{v:.3f}"},
            {"key": "ma80", "label": "80日均线", "color": "#2BC4B6", "fmt": lambda v: f"¥{v:.3f}"},
            {"key": "ma80_095", "label": "80MA×0.95", "color": "#e74c3c", "fmt": lambda v: f"¥{v:.3f}"},
            {"key": "ma200", "label": "200日均线", "color": "#65758A", "fmt": lambda v: f"¥{v:.3f}"},
        ]
    else:  # 513500
        bar_specs = [
            {"key": "price", "label": "实时价", "color": "#071B3A", "fmt": lambda v: f"¥{v:.3f}"},
            {"key": "ma80", "label": "80日均线", "color": "#2BC4B6", "fmt": lambda v: f"¥{v:.3f}"},
            {"key": "ma80_095", "label": "80MA×0.95", "color": "#e74c3c", "fmt": lambda v: f"¥{v:.3f}"},
        ]
        # 加250日SMA
        ma250 = calc_ma(stock.get("raw_closes", []), 250)
        if ma250 is not None:
            stock["ma250"] = ma250
            bar_specs.append(
                {"key": "ma250", "label": "250日SMA", "color": "#65758A", "fmt": lambda v: f"¥{v:.3f}"}
            )

    bars = build_bar(stock, bar_specs)
    chart = bar_svg(bars)

    # 条件检查
    conditions_html = ""
    if check_result:
        for c in check_result["conditions"]:
            dot_color = "green" if c["met"] else "red"
            sign = "✓" if c["met"] else "✗"
            conditions_html += (
                f'<div class="cond">'
                f'<span class="dot {dot_color}"></span>'
                f'<span class="label">{c["label"]} {sign}</span>'
                f'<span class="value">{c["detail"]}</span>'
                f"</div>"
            )

    display_name_map = {
        "QQQ": "QQQ · 纳指100ETF",
        "512890": "512890 · 红利低波ETF",
        "159558": "159558 · 半导体设备ETF",
        "513500": "513500 · 标普500ETF",
    }

    return f"""
    <div class="card">
      <div class="card-header">
        <h2>{display_name_map.get(name, name)}</h2>
        <span class="code">{code}</span>
      </div>
      <div class="subtitle">
        现价 {format_price(stock["price"], name=="QQQ")}
        <span class="status-badge {badge_class}" style="margin-left:12px;vertical-align:middle">{badge_text}</span>
      </div>
      <div class="chart-wrap">{chart}</div>
      <div class="conditions">{conditions_html}</div>
    </div>"""


def build_strategy_card(price):
    """生成159558策略备忘"""
    holding_value = HOLDINGS["shares"] * price
    pnl_pct = (price - HOLDINGS["cost"]) / HOLDINGS["cost"] * 100

    tiers_html = ""
    for lo, hi, amt, label in HOLDINGS["tiers"]:
        tiers_html += f"""
        <div class="tier">
          <div class="t-price">¥{lo}-{hi}</div>
          <div class="t-amount">¥{amt:,}</div>
          <div class="t-note">{label}</div>
        </div>"""

    return f"""
    <div class="strategy-card">
      <h3><span class="icon">📋</span>159558 半导体设备ETF · 策略备忘</h3>
      <div class="strategy-grid">
        <div class="strategy-item">
          <div class="s-label">📦 持仓</div>
          <div class="s-value">{HOLDINGS["shares"]:,} 股</div>
          <div class="s-detail">成本 ¥{HOLDINGS["cost"]:.3f} · 市值 ¥{holding_value:,.0f} · 浮亏 {pnl_pct:+.1f}%</div>
        </div>
        <div class="strategy-item">
          <div class="s-label">💰 剩余资金</div>
          <div class="s-value">¥{HOLDINGS["cash"]:,}</div>
          <div class="s-detail">待分批入场</div>
        </div>
        <div class="strategy-item">
          <div class="s-label">🎯 补仓三档</div>
          <div class="strategy-tiers">{tiers_html}</div>
        </div>
        <div class="stop-loss">
          🛑 <strong>止损纪律</strong>：跌破200日线（≈¥{STOP_LOSS_MA200:.2f}）且3-5个交易日收不回 → 暂停补仓，考虑减仓。
        </div>
      </div>
    </div>"""


def build_html(stocks_data):
    """生成完整HTML"""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    date_str = now.strftime("%Y年%m月%d日")
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]

    any_triggered = any(
        s["check"]["triggered"] for s in stocks_data if s["check"] is not None
    )
    overall_badge = "triggered" if any_triggered else "safe"
    overall_text = "⚠️ 有触发信号" if any_triggered else "四只均未触发"

    # 159558 当前价
    stock_159558 = next((s for s in stocks_data if s["name"] == "159558"), None)
    price_159558 = stock_159558["stock"]["price"] if stock_159558 else 1.0

    cards = ""
    for sd in stocks_data:
        cards += build_card(sd["stock"], sd["check"])

    strategy = build_strategy_card(price_159558)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>股票监控 · {date_str}</title>
<style>
:root{{
  --bg:#F3F7FB;--card:#fff;--text:#102033;--sub:#65758A;
  --blue:#071B3A;--gold:#D5A84D;--teal:#2BC4B6;--red:#e74c3c;
  --green:#27ae60;--border:#E5EAF1;--radius:12px;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"PingFang SC","Microsoft YaHei","Helvetica Neue",sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:20px;min-height:100vh}}
.header{{background:var(--blue);color:#fff;padding:24px 32px;border-radius:var(--radius);margin-bottom:24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}}
.header h1{{font-size:1.4rem;font-weight:700}}
.header .date{{font-size:.9rem;opacity:.8}}
.status-badge{{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:20px;font-size:.82rem;font-weight:600}}
.status-badge.safe{{background:rgba(39,174,96,.15);color:var(--green)}}
.status-badge.triggered{{background:rgba(231,76,60,.12);color:var(--red)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:20px;margin-bottom:20px}}
.card{{background:var(--card);border-radius:var(--radius);padding:24px;box-shadow:0 1px 4px rgba(0,0,0,.04);border:1px solid var(--border)}}
.card-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}}
.card-header h2{{font-size:1.05rem;font-weight:700;color:var(--blue)}}
.card-header .code{{font-size:.78rem;color:var(--sub);background:var(--bg);padding:2px 8px;border-radius:4px;font-family:monospace}}
.card .subtitle{{font-size:.82rem;color:var(--sub);margin-bottom:20px}}
.chart-wrap{{overflow-x:auto;margin-bottom:16px}}
.chart-wrap svg{{display:block;min-width:340px}}
.conditions{{border-top:1px solid var(--border);padding-top:14px;display:flex;flex-wrap:wrap;gap:10px;font-size:.82rem}}
.cond{{display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:6px;background:var(--bg)}}
.cond .dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.cond .dot.red{{background:var(--red)}}
.cond .dot.green{{background:var(--green)}}
.cond .label{{color:var(--sub)}}
.cond .value{{font-weight:700;font-variant-numeric:tabular-nums}}
.strategy-card{{background:linear-gradient(135deg,#FFF8E1,#FFF3CD);border:1.5px solid var(--gold);border-radius:var(--radius);padding:20px 24px;margin-bottom:20px}}
.strategy-card h3{{font-size:.95rem;font-weight:700;color:var(--blue);margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.strategy-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}}
.strategy-item{{background:#fff;border-radius:8px;padding:12px 14px;border:1px solid var(--border)}}
.strategy-item .s-label{{font-size:.75rem;color:var(--sub);margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}}
.strategy-item .s-value{{font-size:.88rem;font-weight:700;color:var(--blue)}}
.strategy-item .s-detail{{font-size:.78rem;color:var(--sub);margin-top:2px}}
.strategy-tiers{{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:8px;margin-top:4px}}
.tier{{flex:1;min-width:120px;background:#fff;border-radius:8px;padding:10px 12px;border:1px solid var(--border);text-align:center}}
.tier .t-price{{font-size:.9rem;font-weight:700;color:var(--blue)}}
.tier .t-amount{{font-size:.78rem;color:var(--sub)}}
.tier .t-note{{font-size:.72rem;color:var(--teal);margin-top:2px}}
.stop-loss{{grid-column:1/-1;background:#FFF0F0;border:1px solid #f5c6cb;border-radius:8px;padding:10px 14px;font-size:.82rem;color:#721c24;margin-top:4px}}
.stop-loss strong{{color:var(--red)}}
.footer{{text-align:center;color:var(--sub);font-size:.78rem;padding:16px}}
@media(max-width:480px){{body{{padding:10px}}.header{{padding:16px 20px}}.card{{padding:16px}}.grid{{grid-template-columns:1fr}}.strategy-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header class="header">
  <div>
    <h1>📊 股票监控面板</h1>
    <div class="date">{date_str} 星期{weekday} · 12:30</div>
  </div>
  <span class="status-badge {overall_badge}">{overall_text}</span>
</header>
{strategy}
<main class="grid">{cards}</main>
<footer class="footer">
  数据来源：新浪财经 · GitHub Actions 自动执行 · 仅供参考，不构成投资建议
</footer>
</body>
</html>"""


# ============================================================
# 主流程
# ============================================================


def main():
    print("📊 股票监控脚本启动...")

    # 获取数据
    print("→ 获取QQQ数据...")
    qqq_stock = fetch_qqq()
    print("→ 获取512890数据...")
    s512890_stock = fetch_a_share("512890", "sh512890", 300)
    print("→ 获取159558数据...")
    s159558_stock = fetch_a_share("159558", "sz159558", 400)
    print("→ 获取513500数据...")
    s513500_stock = fetch_a_share("513500", "sh513500", 300)

    # 判断触发
    stocks_data = [
        {"name": "QQQ", "stock": qqq_stock, "check": check_qqq(qqq_stock)},
        {"name": "512890", "stock": s512890_stock, "check": check_512890(s512890_stock)},
        {"name": "159558", "stock": s159558_stock, "check": check_159558(s159558_stock)},
        {"name": "513500", "stock": s513500_stock, "check": check_513500(s513500_stock)},
    ]

    # 打印摘要
    print("\n" + "=" * 50)
    for sd in stocks_data:
        s = sd["stock"]
        c = sd["check"]
        trig = "🔴 触发!" if (c and c["triggered"]) else "✅ 正常"
        print(f"  {sd['name']:>8s}  {format_price(s['price'], sd['name']=='QQQ'):>10s}  {trig}")
    print("=" * 50)

    # 生成 HTML
    html = build_html(stocks_data)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ HTML 已生成: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()