# 分點日報 → Telegram

追蹤指定券商分點的當日買超/賣超，每交易日晚上推播到 Telegram。
預設分點：**凱基-三多**。

## 資料源與重要限制

資料來自 FinLab 的 `broker_transactions`，內容是 **TWSE 每檔股票的「前 15 大買超 + 前 15 大賣超」分點**（每檔每日固定 30 列），**不是全量分點資料**。

實際意義：

- 分點只有在**擠進某檔股票的前 15 名買超或賣超**時才會出現在那一檔裡。
- 所以「凱基-三多 今日買超 TOP 10」= 它今天在哪些股票裡站上前 15 大買超，而不是它所有的買賣紀錄。
- 對主力/籌碼追蹤而言這通常正好夠用（買零星幾張本來就不重要），但別把總買賣張數當成該分點的完整成交量。

其他事實：

- `buy` / `sell` 單位是**張**。
- 資料每交易日約 **19:00–19:02（台灣時間）** 更新，因此排程設在 20:00。
- 全表約 1.09 億列、751 MB，每次執行會完整下載一次（FinLab VIP 每日額度 5000 MB，約占 15%）。

## 推播內容

- 當日上榜檔數、買/賣/淨張數
- 買超 TOP N、賣超 TOP N：股號、股名、淨張數、收盤價、漲跌幅、**佔當日成交量比重**
  （佔量 ≥ 10% 標 ⚡ — 對冷門股來說這是影響力最強的訊號）
- 近 N 個交易日累計買超前 5 名

## 設定

| 環境變數 | 預設 | 說明 |
| --- | --- | --- |
| `BROKERS` | `凱基-三多` | 逗號分隔可多個，格式為「券商-分點」（如 `凱基-三多,凱基-台北`）；總公司為 `凱基-總公司`，外資多為單一分點如 `美商高盛` |
| `TOP_N` | `10` | 買超列前幾名 |
| `SELL_N` | `5` | 賣超列前幾名 |
| `LOOKBACK` | `5` | 累計買超回看交易日數 |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | — | Telegram 推播目標 |
| `FORCE` | — | `1` = 略過交易日/新鮮度檢查 |
| `DRY_RUN` | — | `1` = 只印訊息不推播 |

FinLab 憑證用 `FINLAB_REFRESH_TOKEN` / `FINLAB_SESSION_ID` / `FINLAB_API_KEY`
（舊的 `FINLAB_API_TOKEN` 已於 2026/08/01 淘汰）。本機執行下列指令取得：

```bash
python -m finlab token --env
```

## 本機測試

```bash
DRY_RUN=1 FORCE=1 python broker_daily.py
```

## GitHub Actions

`.github/workflows/daily.yml`：週一~週五 12:00 UTC（20:00 台灣時間）自動執行，
也可用 workflow_dispatch 手動觸發（可指定分點、force、dry-run）。

需要的 repo secrets：
`FINLAB_REFRESH_TOKEN`、`FINLAB_SESSION_ID`、`FINLAB_API_KEY`、`TG_BOT_TOKEN`、`TG_CHAT_ID`

可選 repo variables：`BROKERS`、`TOP_N`、`SELL_N`、`LOOKBACK`（改追蹤對象不用動程式碼）。

是否為交易日由腳本自行判斷（比對 FinLab 收盤資料的最新日期），所以國定假日不會誤推。
若分點資料當天還沒更新，會改推一則簡短告警而不是假裝有資料。

## 換/加分點

分點名稱要跟資料裡的字串完全一致。查可用名稱：

```python
import pyarrow.feather as ft
tb = ft.read_table("~/finlab_db/broker_transactions.feather", memory_map=True)
names = tb.column("broker").chunk(0).dictionary.to_pylist()
print(sorted(n for n in names if "凱基" in n))
```

全市場共約 1060 個分點。
