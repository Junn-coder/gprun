import os
import sys
import time
import datetime
import argparse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
import yfinance as yf
import baostock as bs

debug_logs = []

def log_debug(msg):
    print(msg)
    debug_logs.append(msg)

WHITELIST = [
    {"code": "603629", "name": "Litong Electronics", "sector": "AI Compute Rental"},
    {"code": "688610", "name": "Eko Photonics",      "sector": "Machine Vision"},
    {"code": "002266", "name": "Zhefu Holding",      "sector": "Resource Recycling / Clean Energy"},
    {"code": "301162", "name": "Guoneng Rixin",      "sector": "New Energy Digitalization"},
    {"code": "003010", "name": "Ruoyuchen",          "sector": "E-commerce Operations Transformation"},
]

def build_whitelist(codes_csv=None):
    """If --codes 'code,name,sector;...' is given, override WHITELIST with scanned candidates."""
    if not codes_csv:
        return WHITELIST
    out = []
    for item in codes_csv.split(";"):
        parts = item.strip().split(",")
        if len(parts) >= 2:
            out.append({"code": parts[0].strip(), "name": parts[1].strip(),
                         "sector": parts[2].strip() if len(parts) > 2 else "unknown"})
    return out if out else WHITELIST

def get_q1_growth(stock_code):
    """
    Use baostock query_growth_data to fetch the latest Q1 YoY growth ratios.
    growth_data field order: [code, pubDate, statDate, yoRevenue, yoNetProfit, ...]
    yoRevenue / yoNetProfit are YoY growth ratios (e.g. 0.50 means +50%).
    Returns (revenue_growth, profit_growth, debug_info) as percentages.
    """
    debug_info = []
    try:
        lg = bs.login()
        if lg.error_code != '0':
            debug_info.append(f"login failed: {lg.error_msg}")
            log_debug(f"  [X] {stock_code}: login failed")
            return None, None, "\n".join(debug_info)

        if stock_code.startswith('6'):
            bs_code = f"sh.{stock_code}"
        else:
            bs_code = f"sz.{stock_code}"

        current_year = datetime.datetime.now().year
        rs = bs.query_growth_data(code=bs_code, year=current_year, quarter=1)
        rows = []
        if rs.error_code == '0':
            while rs.next():
                rows.append(rs.get_row_data())
        else:
            debug_info.append(f"query_growth_data error: {rs.error_msg}")
        bs.logout()

        if not rows:
            debug_info.append(f"No growth data for {bs_code} ({current_year}Q1)")
            return None, None, "\n".join(debug_info)

        row = rows[0]
        if len(row) < 5:
            debug_info.append(f"Insufficient fields: got {len(row)}, need >=5")
            return None, None, "\n".join(debug_info)

        yo_revenue_str = row[3]
        yo_net_profit_str = row[4]
        revenue_growth = (float(yo_revenue_str) if yo_revenue_str else 0.0) * 100
        profit_growth = (float(yo_net_profit_str) if yo_net_profit_str else 0.0) * 100

        debug_info.append(
            f"{bs_code} {row[2]} YoY revenue={revenue_growth:.2f}%, profit={profit_growth:.2f}%"
        )
        return revenue_growth, profit_growth, "\n".join(debug_info)
    except Exception as e:
        err_msg = f"baostock exception: {str(e)}"
        debug_info.append(err_msg)
        log_debug(f"  [X] {stock_code}: {err_msg}")
        return None, None, "\n".join(debug_info)

def screen_growth_stocks(whitelist=None):
    if whitelist is None:
        whitelist = WHITELIST
    candidates = []
    for item in whitelist:
        code = item["code"]
        name = item["name"]
        log_debug(f"Analyzing {name} ({code}) ...")
        revenue_growth, profit_growth, debug_info = get_q1_growth(code)
        if revenue_growth is None or profit_growth is None:
            log_debug(f"  -> data missing, skipped (debug: {debug_info})")
            continue
        log_debug(f"  -> revenue growth {revenue_growth:.2f}%, profit growth {profit_growth:.2f}%")
        if profit_growth > 30 and revenue_growth > 20:
            candidates.append({
                "code": code,
                "name": name,
                "sector": item["sector"],
                "revenue_growth": round(revenue_growth, 2),
                "profit_growth": round(profit_growth, 2),
                "debug": debug_info
            })
            log_debug(f"  [OK] passes thresholds, added to candidates")
        else:
            log_debug(f"  [X] fails thresholds (profit {profit_growth:.1f}% needs >30, revenue {revenue_growth:.1f}% needs >20)")
    return candidates

def analyze_stock_with_myai(stock_info, api_key):
    prompt = f"""
请分析以下A股股票的投资价值，重点判断未来3-6个月是否具有30%-50%的上涨潜力：
- 股票名称：{stock_info['name']}（{stock_info['code']}）
- 所属板块：{stock_info['sector']}
- 一季度净利润同比：{stock_info['profit_growth']}%
- 一季度营业收入同比：{stock_info['revenue_growth']}%

输出格式：
1. 核心成长逻辑（2-3点）
2. 主要风险（2点）
3. 综合评级：A（强烈推荐）/ B（中性）/ C（回避）
4. 预期3-6个月涨幅区间：xx%
"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log_debug(f"myai analysis failed for {stock_info['name']}: {e}")
        return "Analysis failed, please retry later."

def analyze_yyg(api_key):
    try:
        ticker = yf.Ticker("601166.SS")
        hist = ticker.history(period="2d")
        if not hist.empty:
            price = hist['Close'].iloc[-1]
            if len(hist) > 1:
                prev_close = hist['Close'].iloc[-2]
                change = (price - prev_close) / prev_close * 100
            else:
                change = 0.0
        else:
            price, change = "N/A", "N/A"
    except Exception as e:
        log_debug(f"Failed to fetch Industrial Bank quote: {e}")
        price, change = "N/A", "N/A"
    prompt = f"""
兴业银行（601166）最新数据：
- 收盘价：{price}
- 当日涨跌幅：{change}%
请结合当前低利率环境、银行板块整体估值以及公司不良贷款率，给出简要分析和操作建议。
"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log_debug(f"Industrial Bank analysis failed: {e}")
        return "Analysis failed."

def send_email(candidates, yyg_analysis, smtp_user, smtp_password, to_email):
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    candidates_html = "<h2>High-Growth Stock Screening Results (Whitelist)</h2>"
    if candidates:
        candidates_html += """
        <table border="1" cellpadding="6" style="border-collapse: collapse; width: 100%; font-family: Arial;">
            <tr style="background-color: #f2f2f2;">
                <th>Code</th><th>Name</th><th>Sector</th>
                <th>Revenue YoY (%)</th><th>Profit YoY (%)</th><th>AI Rating</th><th>Expected Upside</th>
            </tr>
        """
        for c in candidates:
            analysis_text = analyze_stock_with_myai(c, os.getenv("_API_KEY"))
            rating = "pending"
            target_range = "pending"
            candidates_html += f"""
                <tr>
                    <td>{c['code']}</td><td>{c['name']}</td><td>{c['sector']}</td>
                    <td>{c['revenue_growth']}</td><td>{c['profit_growth']}</td>
                    <td>{rating}</td><td>{target_range}</td>
                </tr>
                <tr style="background-color: #fafafa;"><td colspan="7"><details><summary>Detailed analysis</summary><pre>{analysis_text}</pre></details></td></tr>
            """
            time.sleep(1)
        candidates_html += "</table>"
    else:
        candidates_html += "<p>No whitelisted stocks met the thresholds today (profit YoY > 30% AND revenue YoY > 20%).</p>"

    yyg_html = f"""
    <h2>Watchlist: Industrial Bank (601166)</h2>
    <pre>{yyg_analysis}</pre>
    """

    debug_html = "<h2>Debug Log</h2><details><summary>Click to expand</summary><pre>" + "\n".join(debug_logs) + "</pre></details>"

    full_html = f"""
    <html>
    <head><meta charset="UTF-8"></head>
    <body>
        <h1>Daily Stock Market Brief - {date_str}</h1>
        {candidates_html}
        <hr>
        {yyg_html}
        <hr>
        {debug_html}
        <hr>
        <p style="color: gray;">This report is auto-generated by GitHub Actions. Data from baostock/yfinance, analysis by AI. Not investment advice.</p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"High-Growth Screening + Industrial Bank Analysis - {date_str}"
    msg["From"] = smtp_user
    msg["To"] = to_email
    part = MIMEText(full_html, "html")
    msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to_email, msg.as_string())
    print("Email sent successfully")

def main():
    ap = argparse.ArgumentParser(description="Growth stock screener")
    ap.add_argument("--codes", default=None,
                    help="Override whitelist: 'code,name,sector;...' (from scan_cn.py)")
    args = ap.parse_args()

    whitelist = build_whitelist(args.codes)

    my_key = os.getenv("_API_KEY")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pwd = os.getenv("SMTP_PASSWORD")
    to_email = os.getenv("TO_EMAIL")

    if not all([my_key, smtp_user, smtp_pwd, to_email]):
        raise ValueError("Please configure _API_KEY, SMTP_USER, SMTP_PASSWORD, TO_EMAIL in GitHub Secrets")

    log_debug(f"========== Screening started ({len(whitelist)} stocks) ==========")
    candidates = screen_growth_stocks(whitelist)
    log_debug(f"Screening complete, {len(candidates)} stock(s) matched")
    log_debug("========== Analyzing Industrial Bank ==========")
    yyg_analysis = analyze_yyg(my_key)
    log_debug("========== Sending email ==========")
    send_email(candidates, yyg_analysis, smtp_user, smtp_pwd, to_email)
    log_debug("========== Done ==========")

if __name__ == "__main__":
    main()
