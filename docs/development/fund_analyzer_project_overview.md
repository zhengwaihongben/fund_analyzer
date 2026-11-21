# Fund Analyzer：項目說明、開發計劃與目前進度

> **文件用途**：項目總覽與開發交接文件  
> **進度基準日**：2026-11-21  
> **目前開發主線**：V2.4 — GUI 與應用層整合  
> **目前進度**：Stage 0、A、B、C、D 已完成並接受；Stage E 的 E-01～E-13 全部通過（E-02 經延遲啟動修正後中位數 1.547 s）。D-10 的「開啟匯出檔」在本機無法以雙擊完成（VM 沒有 `.md` 檔案關聯），僅剩此人工走查項；V2.4 工程範圍已完成。

---

## 1. 項目概述

Fund Analyzer 是一個以 Python 開發的基金分析及投資組合研究工具。項目核心是建立可追溯、可重現的基金估值與歷史分析流程，尤其支援 QDII（合格境內機構投資者）基金。

項目主要方向：

- **基金資料分析**：取得基金基本資料、歷史淨值及持倉資料，並產生分析結果。
- **QDII NAV 估算**：根據基金披露持倉、各資產市場價格、外匯匯率及交易日曆，估算指定日期的基金 NAV。
- **多資產估值**：統一處理股票、ETF、債券 ETF、現金等資產類型；未知資產保留並標記，不自動假設為股票。
- **歷史資料及可追溯性**：保存持倉快照、價格、匯率、官方 NAV、估值輸入與估值結果，讓歷史估值可追溯及重現。
- **回測與驗證**：支援歷史策略回測、績效及風險分析，並將回測結果持久化。
- **桌面 GUI**：透過 Tkinter 提供基金分析、QDII 估值、回測及報告檢視等操作入口。

本項目定位為教育、研究及投資分析工具，不構成投資建議。外部資料來源可能延遲、缺漏或有誤。

## 2. 核心功能與分析範圍

### 2.1 基金分析

既有基金分析流程支援基金資料取得、歷史 NAV 分析、指標計算、圖表及 HTML 報告輸出。專案原有 CLI 和 Tkinter GUI 入口仍是 V2.4 整合時需要保留的既有使用方式。

### 2.2 QDII 基金 NAV 估算

QDII 估算流程的核心輸入包括：

- 基金官方 NAV 及其日期；
- 可用的基金持倉快照，包括持倉季度、披露日期及持倉權重；
- 各持倉資產的基準日與目標日價格；
- 各相關貨幣兌 CNY 的匯率；
- 各市場交易日曆、價格日期對齊及資料新鮮度資訊。

估值結果包含資產層級回報、組合估算結果、資料品質資訊，以及本地市場回報與匯率影響的歸因。設計上不會把未知或未覆蓋的持倉權重當作零，也不會任意重新正規化披露權重。

項目採用歷史收市價估算；即時盤中估值不屬於目前階段範圍。

### 2.3 回測

回測模組用於歷史策略驗證。V2.4 的 GUI 整合範圍包括策略輸入、執行狀態、結果呈現、報告及歷史紀錄；策略引擎、績效計算、成本模型及既有回測語義應保持穩定，不應因 GUI 整合而被改寫。

### 2.4 報告與歷史紀錄

項目會保存估值及回測執行紀錄，並支援從已持久化資料載入歷史結果。歷史檢視應以讀取已保存紀錄為主，不應為了顯示歷史資料而重新執行估值或回測。

---

## 3. 技術架構

### 3.1 主要技術

| 領域 | 技術／方案 |
|---|---|
| 程式語言 | Python |
| 桌面 GUI | Tkinter |
| 資料庫 | SQLite |
| 資料模型 | Python dataclasses／typed models |
| 基金及市場資料 | AKShare、yfinance 及項目內資料供應模組 |
| 圖表與報告 | Plotly、HTML／Markdown 報告 |
| 測試 | Python `unittest` |
| 執行環境 | 以 Python 3.10 為主要驗證環境 |

實際可用資料來源及套件版本以專案 `requirements.txt` 和執行環境為準。

### 3.2 分層設計

專案採用分層及模組化架構：

```text
fund_analyzer/
├── fa/
│   ├── models/          # 共用資料模型
│   ├── data/            # 資料供應、資料處理、資料庫
│   │   └── database/    # SQLite schema、connection、repository
│   ├── analysis/        # 基金分析、回報、估值、回測
│   ├── application/     # 應用服務層：協調分析流程與資料存取
│   ├── gui/             # Tkinter GUI
│   ├── utils/           # 日曆、資料品質及共用工具
│   └── reporting/       # 報告產生功能（依整合進度落實）
├── data/
│   ├── database/        # SQLite 資料庫
│   ├── cache/           # 資料快取
│   └── exports/         # 估值、回測及其他輸出
├── docs/
│   ├── architecture/
│   ├── technical_specification/
│   └── development/
├── scripts/
├── tests/
├── fund_analyzer.py
└── fund_analyzer_gui.py
```

以上是邏輯結構示意；實際目錄及檔案應以最新開發分支／交付 ZIP 為準。

### 3.3 重要架構原則

1. **Models 不依賴其他業務層**：共用資料模型應保持低耦合。
2. **Analysis 不負責 GUI**：分析及估值邏輯不應直接操作 Tkinter 元件。
3. **資料存取集中管理**：透過 Repository 管理 SQLite 資料讀寫。
4. **Application Service 負責協調**：GUI 將使用者輸入交給應用服務；應用服務協調 estimator、Repository 及輸出資料。
5. **GUI 主執行緒安全**：所有 Tkinter 操作只在主執行緒執行；耗時工作放到 worker thread，透過 queue 傳遞進度及結果。
6. **每次執行可追溯**：估值及回測結果應保留 run identifier 和可用的輸入紀錄。
7. **歷史檢視不重算**：Stored／History 模式只讀取已保存結果，不呼叫估值引擎。
8. **不以缺失值冒充零值**：`None`、數值 `0`、無效值及非有限數值必須區分。

---

## 4. 重要資料與估值設計

### 4.1 SQLite 持久化

SQLite 是項目的主要持久化層，保存基金與資產資料、持倉快照、價格、匯率、官方 NAV、估值執行紀錄及回測結果。

估值歷史的關鍵資料概念包括：

- `estimation_runs`：估值執行摘要及識別資料；
- `estimation_inputs`：估值輸入快照；
- `estimation_results`：估值結果。

歷史資料應能按 `run_id` 對應到正確執行，不可把不同執行的資產資料混在一起。

### 4.2 QDII 資產明細來源優先級

目前 Application Service Layer 的資產明細解析順序為：

1. SQLite `estimation_inputs`；
2. per-run JSON 的 `snapshots[run_id]`；
3. latest JSON，但只可在其中 `run_id` 與目前查詢完全一致時使用。

高優先級來源已提供的欄位，不應被低優先級來源覆蓋。Contribution 與 status 可分別補充，不應因其中一個欄位已存在而阻止另一欄位補充。

若沒有有效資產明細，應保留估值摘要，並使用既定提示：

> 此執行未保存可用資產明細（僅存摘要）

### 4.3 Live、Stored、History 三種服務路徑

| 模式 | 用途 | 主要行為 |
|---|---|---|
| Live | 執行新估值 | 呼叫 estimator、傳入 Repository、讀回驗證持久化結果 |
| Stored | 載入指定歷史估值 | 讀取 SQLite 及相應 JSON 資料，不呼叫 estimator |
| History | 列出歷史估值 | 按基金及數量限制列出已保存執行紀錄 |

Live Mode 不能只根據 estimator 回傳值推斷儲存成功；應以 Repository read-back 驗證。Stored Mode 不應執行估值或資料寫入。

---

## 5. 開發歷程與版本方向

項目由既有基金分析工具逐步演進至模組化 V2 架構。V2 的技術規格將工作分成技術基礎、資料基礎設施、多資產估值、回測驗證及 GUI 整合等階段。

### 5.1 V2.0 — Technical Foundation

主要工作：

- 定義共用資產、持倉、價格、回報及估值資料模型；
- 建立模組分層與依賴規則；
- 定義資料品質、錯誤分類及測試策略；
- 建立 V2 技術規格文件。

**狀態：已完成。**

### 5.2 V2.1 — Data Infrastructure

主要工作：

- SQLite schema、connection 及 Repository；
- 持倉快照與估值輸入持久化；
- 中國、美國、香港、日本市場日曆；
- 價格與匯率日期對齊；
- 價格及匯率資料新鮮度規則。

**狀態：已完成。**

歷史持倉資料的自動取得受外部資料來源可用性限制。設計不應假設一定能取得完整季度歷史；估值使用符合目標日期條件的可用持倉，並保留披露日期及 point-in-time 約束。

### 5.3 V2.2 — Multi-Asset Valuation

主要工作：

- 多資產回報計算；
- 組合估值；
- FX 轉換；
- 本地回報與匯率影響歸因；
- NAV estimator orchestration。

**狀態：已完成。**

### 5.4 V2.3 — Backtesting & Validation

主要方向：

- 歷史回測流程；
- 策略與執行設定；
- 成本模型；
- 回測結果持久化；
- 報告及合成資料驗收；
- 數值、資料時點及可重現性驗證。

**目前狀態：V2.3 已進入交付及後續整合基線；回測引擎及其既有語義屬於 V2.4 的受保護依賴。** 本文件不把 V2.3 每一項驗收狀態一概標記為通過；個別 Stage 的 PASS／未通過狀態應以對應證據文件及驗收矩陣為準。

### 5.5 V2.4 — GUI & Integration

V2.4 的定位是增量式 GUI 整合，不是重寫估值或回測引擎。保留 Tkinter、既有 GUI worker／queue／log lifecycle 及 QDII 視窗，逐步加入應用服務、回測介面、報告及歷史檢視。

主要階段：

| Stage | 名稱 | 主要內容 | 目前狀態 |
|---|---|---|---|
| Stage 0 | Baseline & Specification Freeze | 凍結基線、規格及成本來源語義 | 已完成 |
| Stage A | GUI Foundation & Navigation | GUI 基礎、導航、worker lifecycle、視窗整合及人工操作驗證 | 已完成並接受 |
| Stage B | QDII Estimation Integration | Application Service、Live／Stored／History、資產明細、資料品質及歷史紀錄整合 | 已完成並接受（B-1…B-3；B-16 併入 Stage E） |
| Stage C | Backtesting GUI | 回測輸入、執行、進度及結果呈現 | 已完成並接受 |
| Stage D | Reports & History | 報告及歷史紀錄介面整合 | 已完成並接受 |
| Stage E | Integration & Acceptance | 全系統整合、回歸及最終驗收 | E-01～E-13 通過；D-10 受本機 `.md` 無關聯限制，降為人工走查註記 |

> Stage E 的名稱與較早 V2.3 開發工作中的階段命名容易混淆。此表的 Stage A–E 專指 V2.4 GUI & Integration 計劃。

---

## 6. V2.4 目前開發狀況（截至 2026-11-21）

### 6.1 Stage 0 — 已完成

已完成基線及規格凍結。重要決定包括：

- 成本模型的有效數值與成本來源模式分開處理；
- 若歷史紀錄沒有持久化成本來源，不能單憑數值等於預設值推斷當初使用 default 或 custom；
- WorkerBridge 的事件語義為零個或多個 progress events，之後恰好一個 terminal result。

### 6.2 Stage A — 已完成並接受

已完成 GUI 基礎及導航整合，包括：

- 主視窗與多個功能頁籤；
- WorkerBridge、queue、progress 及 terminal result；
- Tk 主執行緒與背景 worker 分離；
- 視窗註冊、關閉及 worker shutdown 管理；
- 避免 worker 長期持有 Tk widget／controller 參照的修正；
- Windows 實機啟動、操作、切換頁籤、執行期間關閉及重新啟動測試；
- 人工檢查拖曳、滾輪及嵌入檢視器。

A-10 的人工檢查結果已記錄為 `PASS (manual)`，Stage A 已接受。

### 6.3 Stage B — QDII Estimation Integration：已完成並接受

Stage B 的應用服務層及 QDII GUI 已按批次推進並完成驗收。

| 批次 | 內容 | 狀態 |
|---|---|---|
| B-1 | `fa/application/estimation.py`：Live、Stored、History 服務基礎 | 已完成並接受 |
| B-2.1 | SQLite／JSON 資料來源優先級及資產欄位補充 | 已完成並接受 |
| B-2.2 | JSON 缺失、格式錯誤及無效內容容錯 | 已完成並接受 |
| B-2.3 | Contribution、status 獨立補充及來源標籤正確性 | 已完成並接受 |
| B-2.4 | Stored Mode 唯讀行為、Repository 關閉及例外路徑驗證 | 已完成並接受 |
| B-2.5 | 數值邊界、非有限值、超大整數及歷史資料相容性 | 已完成並接受 |
| B-2.6 | 完整回歸、文件一致性及交付 | 已完成並接受（1,009 tests OK） |
| B-3 | QDII 估值 GUI：Live／Stored／Handover、歷史列表、資料品質及來源 | 已完成並接受（1,048 tests OK） |

B-2.5 的兩項證據文件修正及 B-2.6 的完整回歸與交付包驗證均已完成。B-2.6 完成不等於 Stage B 整體驗收完成；B-16 的真實 Windows 桌面人工項目已併入 Stage E 的 E-01／E-10。

### 6.4 Stage B 驗收狀態

Stage B 的完整驗收矩陣為 B-01 至 B-17。Application Service Layer 與 QDII GUI 已交付並通過自動驗收：GUI 呈現、資料品質、History、handover、worker lifecycle 及 B-17 的 read-back 均有測試證據；B-16 的 Windows 真實基金人工操作併入 Stage E（E-01／E-10）執行。

Stage B 只有在 B-01 至 B-17 的驗收條件均有證據支持後，才可宣告整體通過。

---

## 7. V2.4 計劃回顧與後續方向

> **本節是歷史記錄。** 第一至第四階段（Stage B、C、D、E）均已完成並接受
> （2026-11-21），各階段驗收見 §10 與 `docs/development/v24_acceptance_matrix.md`。
> 下列「預計工作」清單保留開發當時的規劃，**不是待辦事項**；
> 未排入 V2.4 的改善項目記錄在 `docs/development/backlog.md`。

建議按依賴順序推進，避免 GUI、服務層及報告層同時大幅修改（歷史說明）。

> 更新（2026-11-21）：Stage B、C、D、E 均已完成並接受；E-02 經延遲啟動修正後
> 中位數 1.547 s；D-10 因本機無 `.md` 關聯，以人工豁免（waiver）結案。
> V2.4 計劃結束，沒有 V2.5。

### 第一階段：完成 Stage B（已完成）

1. B-2.6 完整回歸及交付（1,009 tests OK）。
2. QDII 摘要、資產明細及資料品質區塊整合（B-3）。
3. History 列表、篩選及詳細紀錄（B-3）。
4. GUI 與 Application Service 的 worker／queue 串接（B-3）。
5. Analysis → QDII handover（B-3）。
6. B-17 SQLite read-back／History 即時可見性已完成；B-16 Windows 人工測試併入 Stage E。
7. B-01 至 B-17 驗收矩陣已更新；B-16 於 Stage E 完成後收斂。

### 第二階段：Stage C — Backtesting GUI（已完成 2026-11-21）

預計工作：

- 回測策略輸入表單；
- StrategySpec 與 RunConfig 的欄位分工；
- 成本模型 default／custom 模式正確傳遞；
- 回測 worker 執行、進度與錯誤呈現；
- 回測結果摘要及圖表；
- 執行設定與結果的可追溯呈現；
- 使用既有回測服務及 Repository，不重寫回測引擎。

### 第三階段：Stage D — Reports & History（已完成 2026-11-21）

預計工作：

- 估值及回測報告檢視；
- 歷史執行列表及詳細資料；
- 報告由已持久化結果產生，不在檢視時重新執行分析；
- 共用報告 renderer，避免 CLI 與 GUI 產生兩套不同的報告邏輯；
- 確認報告中的成本、資料來源、日期及執行識別資訊不超出已保存資料所能支持的範圍。

### 第四階段：Stage E — Integration & Acceptance（已完成 2026-11-21）

預計工作：

- 全系統整合回歸；
- 估值、回測、報告及歷史流程端到端驗證；
- Windows GUI 操作與關閉測試；
- SQLite 資料一致性及持久化驗證；
- 文件、使用說明及交付包整理；
- 最終驗收矩陣及已知限制清單。

---

## 8. 開發與驗收規則

為保持項目可追溯性，後續開發沿用以下規則：

1. **一次只推進一個明確批次**：先定義範圍、檔案及驗收條件，再開始修改。
2. **先測試再宣告完成**：交付報告中的測試數字必須來自實際執行。
3. **獨立檢查交付 ZIP**：檢查 ZIP 完整性、檔案內容、SHA256 及必要測試。
4. **不以文件 PASS 取代實際驗收**：人工測試、Windows 測試及資料庫 read-back 必須有對應證據。
5. **不擴大修改範圍**：GUI 整合不應順便修改估值公式、回測引擎、資料庫 Schema 或既有成本模型。
6. **保留未知狀態**：沒有保存的欄位或無法確認的來源應標示未知，不可推斷或填入看似合理的預設值。
7. **明確記錄限制**：例如外部資料源不可用、歷史資料不足、不可取消的背景 worker 等，應如實記錄。

常用驗證命令：

```bash
# 執行完整測試
python -m unittest discover -s tests -t .

# 編譯檢查
python -m compileall -q fa scripts tests

# Git 空白及 patch 檢查
git diff --check
```

實際執行命令應以專案當前目錄及測試配置為準。

---

## 9. 目前已知限制與注意事項

- 外部基金及市場資料服務可能有 TLS、網絡、API 或資料品質問題；資料取得失敗不應被誤判為估值公式錯誤。
- 歷史持倉資料不保證完整；使用持倉時必須遵守披露日期及 point-in-time 限制。
- Stored Mode 已驗證不執行應用層資料寫入，但目前 Repository 初始化仍使用一般可寫連線並執行既有初始化流程；它不是 SQLite `mode=ro` 純唯讀連線。若將來需要資料庫層強制唯讀，應另立設計及工作批次。
- Python 無法安全強制終止任意執行中的 thread。GUI 關閉流程應採用有界等待、停止輪詢及丟棄 late messages，不應宣稱一定能殺死 worker。
- 歷史執行的成本來源模式若未被持久化，不能由有效成本數值反推原始 default／custom 選擇。
- 本文件的進度是截至 2026-11-21 的項目狀態摘要；若後續批次已交付，應以最新 ZIP、測試結果、證據文件及驗收矩陣更新本文件。

---

## 10. 項目狀態摘要

| 項目 | 狀態 |
|---|---|
| V2.0 Technical Foundation | 已完成 |
| V2.1 Data Infrastructure | 已完成 |
| V2.2 Multi-Asset Valuation | 已完成 |
| V2.3 Backtesting & Validation | 已形成回測及驗證基線；個別驗收狀態依證據文件 |
| V2.4 Stage 0 | 已完成 |
| V2.4 Stage A | 已完成並接受 |
| V2.4 Stage B：B-1 至 B-2.4 | 已完成並接受 |
| V2.4 Stage B：B-2.5／B-2.6 | 已完成並接受 |
| V2.4 Stage B：B-3（QDII GUI） | 已完成並接受 |
| V2.4 Stage B 整體驗收 | 已完成（B-16 併入 Stage E 人工項） |
| V2.4 Stage C（回測 GUI） | 已完成並接受 |
| V2.4 Stage D（報告及歷史） | 已完成並接受 |
| V2.4 Stage E（整合驗收） | E-01～E-13 通過；D-10 因 VM 無 `.md` 關聯，降為人工走查註記 |

**目前最直接的下一步：V2.4 不再開新開發批次。** D-10 已於 2026-11-21 以人工豁免（waiver）記錄在驗收矩陣與 `docs/development/release_manifest_v2.4.0.md`；剩下的非阻擋項目見 `docs/development/backlog.md`。
### 後續方向

V2.4 是現行路線圖的最後一個版本，沒有 V2.5。未排入本計畫的改善項目
（GUI 細節、報告格式、覆蓋率以外的測試、D-10 環境限制的長期解法等）集中記錄在
`docs/development/backlog.md`，不屬於任何已接受的驗收項目，也不阻擋 V2.4 結案。

