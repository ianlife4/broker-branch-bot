# -*- coding: utf-8 -*-
"""
分點日報:追蹤指定券商分點的當日買超/賣超,推播到 Telegram。

資料源:FinLab `broker_transactions`
  = TWSE 每檔股票「前15大買超 + 前15大賣超」分點(每檔每日 30 列),
    非全量分點資料 —— 分點沒擠進某檔前15名就不會出現在該檔中。
更新時間:每交易日約 19:00~19:02 (台灣時間)

環境變數:
  BROKERS        逗號分隔的分點名稱,預設 "凱基-三多"(格式為「券商-分點」)
  TOP_N          買超列前幾名,預設 10
  SELL_N         賣超列前幾名,預設 5
  LOOKBACK       累計買超回看交易日數,預設 5
  TG_BOT_TOKEN   Telegram bot token
  TG_CHAT_ID     Telegram chat id
  FORCE          "1" = 不管資料新不新都跑(手動測試用)
  DRY_RUN        "1" = 只印出訊息不推播
"""
import datetime as dt
import os
import sys

import pandas as pd
import requests
from finlab import data

# Windows 主控台預設 cp950,印不出 emoji;GHA 也統一走 UTF-8
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

TW = dt.timezone(dt.timedelta(hours=8))
WD = ["一", "二", "三", "四", "五", "六", "日"]

BROKERS = [b.strip() for b in os.environ.get("BROKERS", "凱基-三多").split(",") if b.strip()]
TOP_N = int(os.environ.get("TOP_N", "10"))
SELL_N = int(os.environ.get("SELL_N", "5"))
LOOKBACK = int(os.environ.get("LOOKBACK", "5"))
TG_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT = os.environ.get("TG_CHAT_ID")
FORCE = os.environ.get("FORCE") == "1"
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def log(msg):
    print(f"[{dt.datetime.now(TW):%H:%M:%S}] {msg}", flush=True)


def esc(s):
    """Telegram HTML 需跳脫的字元"""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_tg(text):
    if DRY_RUN:
        print("\n===== DRY RUN =====\n" + text + "\n===================\n")
        return True
    if not (TG_TOKEN and TG_CHAT):
        log("未設定 TG_BOT_TOKEN/TG_CHAT_ID,略過推播")
        print(text)
        return False
    # Telegram 單則上限 4096 字元,超過就切段
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)

    ok = True
    for i, c in enumerate(chunks):
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={
                "chat_id": TG_CHAT,
                "text": c,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if r.status_code != 200:
            ok = False
            log(f"TG 推播失敗 ({i + 1}/{len(chunks)}): {r.status_code} {r.text[:300]}")
        else:
            log(f"TG 推播成功 ({i + 1}/{len(chunks)})")
    return ok


def fmt_pct(x):
    if pd.isna(x):
        return "—"
    return f"{'▲' if x >= 0 else '▼'}{abs(x) * 100:.1f}%"


def build_broker_section(broker, today_rows, hist_rows, meta, last_date):
    """單一分點的訊息段落"""
    lines = []
    lines.append(f"📍 <b>{esc(broker)}</b>")

    if today_rows.empty:
        lines.append("　今日未進入任何個股的前15大買/賣超名單")
        return lines

    tot_buy = int(today_rows["buy"].sum())
    tot_sell = int(today_rows["sell"].sum())
    lines.append(
        f"　上榜 {len(today_rows)} 檔｜買 {tot_buy:,} / 賣 {tot_sell:,} 張"
        f"｜淨 {tot_buy - tot_sell:+,} 張"
    )

    buys = today_rows[today_rows["net"] > 0].nlargest(TOP_N, "net")
    sells = today_rows[today_rows["net"] < 0].nsmallest(SELL_N, "net")

    def row_line(i, r):
        sid = r["stock_id"]
        m = meta.get(sid, {})
        name = m.get("name", "")
        px = m.get("close")
        chg = m.get("chg")
        share = r.get("vol_share")
        parts = [f"{i}. <b>{esc(sid)}</b> {esc(name)}", f"{int(r['net']):+,} 張"]
        if px is not None and not pd.isna(px):
            parts.append(f"{px:,.2f} {fmt_pct(chg)}")
        if share is not None and not pd.isna(share):
            # 佔當日成交量 10% 以上 = 對該檔影響力大,標記出來
            parts.append(f"佔量 {share:.1f}%" + ("⚡" if share >= 10 else ""))
        return "　" + "｜".join(parts)

    if not buys.empty:
        lines.append("")
        lines.append("🟢 <b>買超</b>")
        for i, (_, r) in enumerate(buys.iterrows(), 1):
            lines.append(row_line(i, r))

    if not sells.empty:
        lines.append("")
        lines.append("🔴 <b>賣超</b>")
        for i, (_, r) in enumerate(sells.iterrows(), 1):
            lines.append(row_line(i, r))

    # 近 N 日累計買超(僅列今日也有買超的,避免噪音)
    if not hist_rows.empty:
        cum = hist_rows.groupby("stock_id", observed=True).agg(
            net=("net", "sum"), days=("date", "nunique")
        )
        cum = cum[cum["net"] > 0].nlargest(5, "net")
        if not cum.empty:
            lines.append("")
            lines.append(f"🔥 <b>近{LOOKBACK}日累計買超</b>")
            for sid, r in cum.iterrows():
                name = meta.get(sid, {}).get("name", "")
                lines.append(
                    f"　<b>{esc(sid)}</b> {esc(name)}｜{int(r['net']):+,} 張"
                    f"｜{int(r['days'])} 天"
                )
    return lines


def main():
    now = dt.datetime.now(TW)
    log(f"啟動 brokers={BROKERS} top_n={TOP_N} force={FORCE}")

    # --- 1. 價量與股名(小表,先抓來判斷今天是不是交易日) ---
    close = data.get("price:收盤價")
    vol = data.get("price:成交股數")
    sec = data.get("security_categories")
    name_map = dict(zip(sec["stock_id"], sec["name"]))

    last_close_date = close.index.max()
    log(f"收盤資料最新日期: {last_close_date:%Y-%m-%d}")

    if not FORCE and last_close_date.date() != now.date():
        log(f"今天({now:%Y-%m-%d})沒有收盤資料 → 非交易日,結束不推播")
        return 0

    # --- 2. 分點資料(751MB,GHA 上每天重抓) ---
    log("下載 broker_transactions ...")
    bt = data.get("broker_transactions")
    last_date = bt["date"].max()
    log(f"分點資料最新日期: {last_date:%Y-%m-%d} (共 {len(bt):,} 列)")

    if not FORCE and last_date.date() != last_close_date.date():
        msg = (
            f"⚠️ <b>分點日報</b>\n"
            f"分點資料尚未更新(最新 {last_date:%m/%d},收盤已到 {last_close_date:%m/%d})。\n"
            f"FinLab 通常 19:00 更新,稍後重跑或用 workflow_dispatch 手動觸發。"
        )
        send_tg(msg)
        log("分點資料落後,已送告警")
        return 0

    # --- 3. 一次過濾出目標分點的近 N 日資料,然後釋放大表 ---
    trading_days = [d for d in close.index if d <= last_date][-LOOKBACK:]
    start = trading_days[0]
    sub = bt[bt["broker"].isin(BROKERS) & (bt["date"] >= start)].copy()
    del bt
    sub["net"] = sub["buy"].astype("int64") - sub["sell"].astype("int64")
    sub["stock_id"] = sub["stock_id"].astype(str)
    log(f"目標分點近{LOOKBACK}日資料 {len(sub):,} 列")

    today_all = sub[sub["date"] == last_date]

    # --- 4. 個股 meta:收盤、漲跌幅、成交量占比 ---
    px_today = close.loc[last_date]
    prev_idx = close.index.get_loc(last_date) - 1
    px_prev = close.iloc[prev_idx] if prev_idx >= 0 else None
    vol_today = vol.loc[last_date]

    meta = {}
    for sid in sub["stock_id"].unique():  # 含回看區間,累計段才有股名
        c = px_today.get(sid)
        p = px_prev.get(sid) if px_prev is not None else None
        chg = (c / p - 1) if (c is not None and p not in (None, 0) and not pd.isna(p)) else None
        meta[sid] = {"name": name_map.get(sid, ""), "close": c, "chg": chg}

    def vol_share(r):
        v = vol_today.get(r["stock_id"])
        if v is None or pd.isna(v) or v == 0:
            return None
        return abs(r["net"]) * 1000 / v * 100

    today_all = today_all.assign(vol_share=today_all.apply(vol_share, axis=1))

    # --- 5. 組訊息 ---
    header = [
        f"🏦 <b>分點日報</b>　{last_date:%Y/%m/%d} ({WD[last_date.weekday()]})",
        "",
    ]
    body = []
    for b in BROKERS:
        body += build_broker_section(
            b,
            today_all[today_all["broker"] == b],
            sub[(sub["broker"] == b) & (sub["date"] <= last_date)],
            meta,
            last_date,
        )
        body.append("")
    footer = ["<i>資料:FinLab 分點進出(各檔前15大買/賣超)</i>"]

    send_tg("\n".join(header + body + footer).strip())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"錯誤: {type(e).__name__}: {e}")
        if TG_TOKEN and TG_CHAT and not DRY_RUN:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    data={
                        "chat_id": TG_CHAT,
                        "text": f"❌ 分點日報執行失敗\n{type(e).__name__}: {str(e)[:500]}",
                    },
                    timeout=20,
                )
            except Exception:
                pass
        raise
