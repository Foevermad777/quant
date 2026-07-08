# 美股赛道执行清单（给 Codex）

版本：0.1　｜　日期：2026-07-07　｜　制定：Claude｜执行：Codex｜验收：Claude
项目根：`/Users/yongyuanbuanzhede/quant`
来源：`US_TRACK_PLAN.md`（v0.1）展开 + 对现有 `executor/` / `ops/` / DSA vendor 的代码勘察。

> 这份清单是把计划书的四块（**美股规则模块 T+0/NYSE 日历 + `paper_us.db` + 清晨调度 + Tavily 接线**）落成有依赖顺序、有落点文件、有验收口径的可执行项，让美股赛道与 A 股日常积累**并行**推进，两条赛道用同一引擎、独立台账、独立时钟。

---

## 0. 开工门槛与红线（先读）

**门槛（계획书 §0，不满足不开工）**：
- G1（无偏成交）+ G2（三大纪律注入）**已提交并验收**，且 A 股第一次真实纪律基线跑（G5 disciplined 信号入库）链路确认正常——否则美股赛道会继承一个即将被替换的执行器。当前 git 已含 `6583dc1 G1-G4`、`6abac71/24aab59 G5`，**开工前 Codex 先确认 A 股 disciplined 链路当日跑通**（`runtime_data/quant/paper.db` 有当日 `disciplined_signals` + 台账快照），确认后再动本清单。
- 加密货币不在本清单内。

**红线继承（照旧，逐项都适用）**：
1. **DSA 库只读**——美股数据的抓取/落库全部归 DSA 域（`vendor/daily_stock_analysis`，其自带 `.venv`）；执行器只读 `runtime_data/dsa/stock_analysis.db`，每次 run 前后 md5 比对不变（引擎已有 `dsa_readonly_md5` 日志）。
2. **台账独立**——`paper_us.db` 与 A 股 `paper.db` 物理隔离，互不写。
3. **依赖进 venv 不污染全局**——美股新依赖（如 `exchange_calendars`/US 基线抓取库）装进对应 venv；勘察确认我方 `.venv` 目前**没有** `exchange_calendars`/`yfinance`/`pandas_market_calendars`，按需增补时记进 `requirements`。
4. **密钥不入 git**——`TAVILY_API_KEYS`、行情源 key 走 `runtime_data/secrets/` + `.env`，`.gitignore` 已覆盖。
5. **遇计划外情况停工上报**，每项完成 commit + 存证 `runtime_data/acceptance/`。
6. **只增不改（见下方护栏 G-US）**——美股赛道是新增的独立模块，**不重构正在跑的 A 股代码**，不碰 A 股稳定闭环。

---

## 工程护栏 G-US：只增不改（硬约束，优先级高于本单其他一切实现取向）

**背景**：A 股闭环正在每天 launchd 真金白银地跑（DSA 傍晚批 + G5 纪律 + 执行器 + 台账），是已经稳定的资产。美股赛道**绝不能以"重构共享代码"的方式引入**——否则一次为美股服务的改动就可能碰坏 A 股闭环，得不偿失。

**规则**：

1. **美股执行器 = 彻底物理隔离的独立副本**（CEO 拍板，2026-07-07）。`executor/us/` 自带**全套所需件的副本**（engine/ledger/rules/models/signal_reader/config/time_guard），**运行期与 A 股执行器零共享代码路径**——不 import `executor/` 下任何 A 股件、不子类、不共享内存/DB。目的：让两个执行器在代码层就无耦合，任何一侧改动**物理上不可能**波及另一侧，杜绝相互误导。
2. **A 股热路径文件冻结（禁止改、禁止被 US 运行期依赖）**：`executor/engine.py`、`executor/ledger.py`、`executor/rules.py`、`executor/models.py`、`executor/config.py`、`executor/time_guard.py`、`executor/signal_reader.py`。本清单默认**不修改**这些文件，且 `executor/us/` **不在运行期 import 它们**。
3. **落地方式 = 复制后裁剪**。以这些 A 股件为**模板复制**进 `executor/us/`，删掉 A 股专有分支（涨跌停门控、T+1 锁仓/顺延、印花税、100 股手数），改成美股口径（无涨跌停、T+0、SEC 费、lot=1、`market='us'` 过滤）。复制即完全独立，可接受的代价见「维护纪律」。
4. **唯二共享（有意为之，非疏漏）**：(a) **DSA 只读库**——两执行器都只读同一 `stock_analysis.db`，代码全局唯一、且只读，不构成误导；(b) **G5 纪律层 `discipline_completion.py`**——按计划书 §2.6「三大纪律市场无关、直接复用」保持共享代码，但以 CLI `--store-db paper_us.db --stock-code <US>` **把输出物理隔离**到美股台账（进程独立、产物独立）。除此之外无任何共享。
5. **若非改 A 股共享文件不可** → **停工上报**，不擅改（含 §4 的 `discipline_completion.py`：只允许纯新增可选 flag、cn 行为逐字节不变、附回归全绿，经 Claude 确认）。
6. **回归红线**：每项交付证明 **A 股行为零变化**——硬证据是 A 股热路径七文件 `git diff --stat` 零改动 + 既有单测/台账/快照逐字节不变。

> **维护纪律（物理隔离的代价，明确接受）**：完整 fork 意味着两套引擎会**独立演化**——A 股引擎日后若修了成交/结算类正确性 bug，**不会自动同步到美股副本**，需人肉判断是否移植。Codex 在 `executor/us/` 顶部标注「fork 自 executor/ @ <当前 commit>」，并在 `PROJECT_LOG` 记录 fork 基线 commit，便于日后 diff 对照移植。这是 CEO 为「零误导」选择的取舍，非疏忽。

---

## 1. 架构：完整 fork 的独立美股执行器（遵护栏 G-US）

**取向**：美股赛道 = `executor/us/` **完整独立副本**，运行期与 A 股执行器**零共享代码路径**。以 A 股件为模板复制进 US 包、裁剪成美股口径。这是 CEO「彻底物理隔离、避免误导」的拍板；代价（双份引擎独立演化、正确性修复需手工移植）已在护栏 G-US「维护纪律」中明确接受。

**新增结构**（Codex 可微调命名，均为**独立副本**、不 import A 股件）：
```
executor/us/
  __init__.py
  config_us.py          # UsExecutorConfig：独立 config（US 池 / SPY / paper_us.db / lot=1 / 无印花税 / T+0）
  rules_us.py           # 美股规则副本：无涨跌停（删 limit 逻辑）、T+0 持仓模型、lot=1
  models_us.py          # 成交/滑点/费用副本：UsFeeModel（无印花税 + SEC 卖出费）、NextOpen/Slippage 拷贝
  time_guard_us.py      # 16:00 ET bar-available（副本）
  signal_reader_us.py   # SignalReader 副本，选取层过滤 market='us' + 只读 paper_us.db 的 disciplined store
  ledger_us.py          # PaperLedger 副本，写 paper_us.db，T+0 结算（买入即 old=quantity）
  engine_us.py          # PaperEngine 副本：删涨跌停门控 + T+0（当日止损当日成交），不 import executor.engine
  __main__.py           # python -m executor.us  入口
  tests/                # 美股独立单测
```

**唯二共享（护栏 G-US §4）**：DSA 只读库（`stock_analysis.db`，两侧只读、代码全局唯一）；G5 `discipline_completion.py`（计划书 §2.6 三大纪律市场无关、共享代码，但 `--store-db paper_us.db` 输出物理隔离）。台账彻底分家：A 股 `paper.db` 冻结不动，美股 `paper_us.db` 独立。

各关注点在 US 副本里怎么落（对照 A 股模板）：

| 关注点 | A 股模板（文件:符号，**冻结**） | 美股副本 `executor/us/` 怎么改 |
|---|---|---|
| 涨跌停门控 | `engine.py` 三处 `is_limit_up_open/down` | `engine_us.py`：**删除**涨跌停门控分支（US 无此约束） |
| T+1 锁仓 | `ledger.py:_apply_sell` T+1 gate、`settle_positions`；`engine.py:same_day_stop_pending` 顺延 | `ledger_us.py`：买入即 `old_quantity=quantity`（当日可卖）；`engine_us.py`：同日止损当日成交，**删顺延** |
| 手数 | `rules.py:round_lot_shares(100)`、`config.lot_size=100` | `rules_us.py`/`config_us.py`：`lot_size=1` |
| 费用 | `models.py:FeeModel(...stamp_tax_rate)` | `models_us.py:UsFeeModel`：印花税恒 0 + SEC 卖出费 |
| 行情可得时刻 | `time_guard.py:A_SHARE_BAR_AVAILABLE_TIME=15:00` | `time_guard_us.py`：16:00 ET |
| 股池/基线/台账 | `config.py:stock_pool/benchmark_codes/ledger_db_path` | `config_us.py`：US 池 / SPY / `paper_us.db` |
| 信号市场过滤 | `signal_reader.py` 取全量（无过滤） | `signal_reader_us.py`：选取层过滤 `market='us'` + 只读 `paper_us.db` disciplined store |

---

## 执行顺序（有依赖，按序做）

### U0｜市场隔离守卫（先做·美股侧新增，A 股侧靠分库天然隔离）

- **问题**：`signal_reader.py:active_signals_before()` 取**所有** active 信号、不按 market/pool 过滤。若两侧都读 DSA 全量，会**互相消费对方市场的信号**（物理隔离只挡"写"、没挡"读"）。
- **物理隔离下的做法（不改、不 import `signal_reader.py`）**：
  - **美股侧（副本）**：`executor/us/signal_reader_us.py` = `SignalReader` 的**独立副本**，在选取层（`open_candidates`/`exit_candidates`/`s1_conflicts` 的入口 `active_signals_before`）只保留 `signal.market == "us"`（`disciplined_signals`/`decision_signals` 都有 `market` 列）+ 叠加 `stock_code in US_STOCK_POOL` 双保险；且只读 `paper_us.db` 的 disciplined store。US 引擎用这个副本，运行期与 A 股 reader 无任何交集。
  - **A 股侧（零改，靠分库天然隔离）**：A 股执行器走 disciplined 路径读 `paper.db` 的 `disciplined_signals`；美股 G5 用 `--store-db paper_us.db --stock-code <US>`（见 U6）把美股 disciplined 信号**只写进 `paper_us.db`**——`paper.db` 里根本不会出现美股信号，A 股闭环天然干净，**无需改 `signal_reader.py`**。
  - **残留项（A 股非 disciplined 回退路径仍读 DSA 全量）**：A 股侧既有小隐患，**明确移出美股赛道范围**；A 股默认 `use_disciplined_signals=True` 不触发该路径，闭环安全。作为独立 A 股加固另行处理。
- **落点**：`executor/us/signal_reader_us.py`（副本）。**不改、不 import** `executor/signal_reader.py`。
- **验收**：US 独立单测——同一 DSA 库混入 cn + us 两条 active 信号，US reader 只回 us；A 股既有单测/台账**零变化**（未触碰其 reader）。

### U1｜美股 config（新增独立 `UsExecutorConfig`，不继承 A 股 config）

- **动作**：`executor/us/config_us.py` 定义**独立的** `@dataclass(frozen=True) class UsExecutorConfig`——**不继承 `ExecutorConfig`**（物理隔离，避免运行期耦合），自带美股所需的全部字段：
  - `ledger_db_path=PAPER_US_DB`、`disciplined_db_path=PAPER_US_DB`、`stock_pool=US_STOCK_POOL`、`benchmark_codes=("SPY",)`、`market="us"`、`t_plus=0`、`lot_size=1`、`initial_cash=1_000_000`、`per_signal_cash`/`symbol_cap_rate`（同 A 股便于对比）。
  - 费用：`commission_rate`/`min_commission`（可配，模拟可 0/小额）、`sec_fee_rate`（卖出侧）、**无印花税字段**（US 副本不设该概念）。
  - 成交：`fill_model`（next_open）、`slippage_rate`、`open_slippage_multiplier`（同 A 股口径，市场无关）、`bar_available_time`（16:00 ET）、`honor_luld=False`。
  - 常量：`US_STOCK_POOL=("AAPL","NVDA","MSFT","JPM","SPCX")`（计划书 §2.1）、`PAPER_US_DB`（指向 `runtime_data/quant/paper_us.db`；路径常量在 US 包内自定义，不 import `config.py`）。
- **落点**：`executor/us/config_us.py`（副本）。**不改、不继承** `executor/config.py`。
- **验收**：`UsExecutorConfig()` 实例化、被 `ledger_us`/`engine_us` 消费；`grep -r "from executor.config" executor/us/` 无命中（证明零耦合）。

### U2｜美股规则副本：无涨跌停 + 手数（`rules_us.py`）

- **动作**：`executor/us/rules_us.py` = `rules.py` 的**独立副本**，裁剪成美股口径：
  - **删涨跌停**：不带 `limit_rate`/`limit_price`/`is_limit_up_open`/`is_limit_down_open`（US 无涨跌停）；`engine_us.py` 里对应门控分支一并删除。
  - **手数**：`round_lot_shares`/`cap_order_shares` 副本以 `lot_size=1` 用；`_exit_signal_max_shares` 侧 `% lot_size` 在 1 下余数恒 0（正确）。
  - **T+0 持仓辅助**：副本里的持仓模型（对应 A 股 `T1Position`）改成买入即可卖，或直接由 `ledger_us` 结算保证（U4/U5）。
  - **LULD/熔断**：首版按计划书 §2.2 **暂缓**，`UsExecutorConfig.honor_luld=False` + TODO，不实现。
- **落点**：`executor/us/rules_us.py`（副本）。**不改、不 import** `rules.py`。
- **验收**：US 独立单测——开盘大涨/大跌均可正常成交（无 limit 门控）；lot=1 可买 1 股整数股；A 股单测（涨停禁买/跌停不卖）保持全绿（未触碰其代码）。

### U3｜美股成交/费用/时钟副本（`models_us.py` + `time_guard_us.py`）

- **动作**：
  - **成交件副本（`models_us.py`）**：拷贝 `NextOpenFillModel`、`SlippageModel`（市场无关，原样）；`FeeModel` 拷成 `UsFeeModel`——**无印花税**（该方法删除或恒 0）+ 新增 `sec_fee`（卖出侧按 `sec_fee_rate`，极小额；佣金可配，模拟可 0/小额）。`total_costs` 返回 (commission, other_fees)；台账 `taxes` 列在 US 语义下装 SEC 费 + reason 注明。
  - **时钟副本（`time_guard_us.py`）**：bar-available = 16:00 ET；US 版 `bar_available_at`/`classify_news_for_attribution`（ET 时区，注意夏令时），供 US 周复盘/归因。首版固定 ET 偏移 + TODO 标注 DST 精度。
- **落点**：`executor/us/models_us.py`、`executor/us/time_guard_us.py`（副本）。**不改、不 import** `models.py`/`time_guard.py`。
- **验收**：US 独立单测——卖出费用 = 佣金 + SEC 费、买入无 SEC 费、全程无印花税；A 股 `FeeModel`（印花税卖出侧 0.0005）口径不变（未触碰其代码）。

### U4｜美股引擎副本 T+0（`engine_us.py`，独立引擎）

- **动作**：`executor/us/engine_us.py` = `PaperEngine` 的**独立副本**（**不 import `executor.engine`**），裁剪成美股 T+0：
  - **删涨跌停门控**：`_process_open_candidates`/`_process_pending_exits`/`_process_position_triggers`/`_process_exit_signals` 副本里去掉 `is_limit_up_open/down` 分支。
  - **T+0 当日可卖**：配合 `ledger_us`（U5）——买入即 `old_quantity=quantity`，当日可卖。
  - **当日止损当日成交**：删掉 A 股的 `same_day_stop_pending` 顺延逻辑，当日 `first_exit_trigger` 命中即当日平仓。
  - **注入 US 件**：`UsExecutorConfig`（U1）、`rules_us`（U2）、`UsFeeModel`（U3）、`signal_reader_us`（U0）、`ledger_us`（U5）——引擎副本内全部用 US 件，不触任何 A 股件。
- **落点**：`executor/us/engine_us.py`（副本）。**不改、不 import** `engine.py`/`ledger.py`/`rules.py`。
- **验收**：US 独立单测——"当日买入 → 同日止损触发 → 当日成交"，台账/`signal_events` 正确；A 股"当日买入次日才可卖"单测保持全绿（base 未动）。

### U5｜美股台账副本 `ledger_us.py` + `paper_us.db`（T+0 结算）

- **动作**：`executor/us/ledger_us.py` = `PaperLedger` 的**独立副本**（**不 import `executor.ledger`**），写 `paper_us.db`：
  - schema 与 A 股相同（account/positions/trades/order_attempts/signal_events/pending_exits/portfolio_snapshots），`initialize()` 自建表 + 注入 `initial_cash=$1M`。
  - **T+0 结算**：`_apply_buy` 副本令新仓 `old_quantity=quantity`（当日可卖）；`_apply_sell` 的 T+1 gate 相应放开（美股无 T+1）。
- **落点**：`executor/us/ledger_us.py`（副本）。**不改、不 import** `executor/ledger.py`。
- **验收**：跑一次 US `run_day` 后 `paper_us.db` 出现全部表；对 `paper.db` 做 md5 前后比对**不变**（零交叉写）；两库 `account.initial_cash` 各自 $1M。

### U6｜美股 DSA 跑批 + G5 接线（依赖 无代码依赖，可与 U1–U5 并行；红线：DSA 只读）

- **动作**：
  - **US DSA 跑批脚本** `ops/run_us_dsa_daily.sh`（仿 `run_dsa_daily.sh`）：`cd vendor/daily_stock_analysis` → `caffeinate -i .venv/bin/python main.py --stocks AAPL,NVDA,MSFT,JPM,SPCX`；保留代理 `nc -z 127.0.0.1 7890` 检查（Tavily/US 源可能需代理，Codex 摸底后决定去留）；DSA 已有多市场交易日门控（`src/core/trading_calendar.py:get_open_markets_today()`/`get_market_for_stock()`），传美股代码即自动按 NYSE 日历跑、非美股交易日空转。
  - **Tavily 接线**：DSA 读 `TAVILY_API_KEYS`（逗号分隔，`vendor/.../src/config.py:1516`）。Codex 把 CEO 提供的 key 写 `runtime_data/secrets/` + DSA 运行环境 `.env`（不入 git）；跑一次确认 US 新闻走 Tavily（计划书 §2.5：AAPL 曾返高相关结果）。社媒情绪（`SOCIAL_SENTIMENT_API_KEY`）**暂不配**（计划书 §3）。
  - **US 行情源摸底**：CEO 的 yfinance key 未到位前，Codex 摸底 yfinance 免注册档 / finnhub / alphavantage（DSA 已有对应 fetcher）在 US 池的可用性与限流，记进存证；SPCX 短历史注意见附录 A。
  - **US G5 纪律完成（零改 `discipline_completion.py`）**：其 CLI 已支持 `--store-db`（默认 `paper.db`）与 `--stock-code`（可重复）——**现成的 US 分流开关**。US 批次调用：`python -m executor.discipline_completion --all-active --store-db runtime_data/quant/paper_us.db --stock-code AAPL --stock-code NVDA --stock-code MSFT --stock-code JPM --stock-code SPCX --retries 1`。**US disciplined 信号只入 `paper_us.db`、只含 US 代码**（也正是 U0 里 A 股靠分库天然隔离的成因）。`--stock-code` 已足够，**不改** `discipline_completion.py`；`--market us` 只是可选增强，若做须走护栏 G-US §4 例外流程（纯新增 flag + cn 行为不变 + 回归证明）。
- **落点**：`ops/run_us_dsa_daily.sh`（新增）、DSA 运行环境 `.env`。**默认不改** `executor/discipline_completion.py`。
- **验收**：手动跑一次 US DSA 批 → `stock_analysis.db` 出现 US 代码当日 bar + US `decision_signals`（market='us'）；US 新闻条目来源含 Tavily；跑 US G5 → `paper_us.db` 出现 US `disciplined_signals`、`paper.db` 无变化。

### U7｜美股执行器清晨调度（依赖 U1–U6）

- **动作**：
  - **US 执行器脚本** `ops/run_us_executor_daily.sh`（仿 `run_executor_daily.sh`）：顺序 = US DSA 缺口补抓（US 版 `backfill_dsa_gaps` 首版可先跳过）→ **US G5 完成**（U6 命令）→ `caffeinate -i .venv/bin/python -m executor.us`（新 US 入口，非 `executor.engine`）。日志分文件（`executor_us_daily_*.log` 等）。
  - **launchd** `ops/com.quant.us.executor.daily.plist` + `ops/com.quant.us.dsa.daily.plist`（仿现有两个 plist）：美股 16:00 ET 收盘 ≈ 北京**清晨 4–5 点**，建议 US DSA ~**05:10**、US 执行器 ~**05:30** 触发（北京时区；与 A 股傍晚 17:58/18:40 批次完全错峰）。Label 用 `com.quant.us.*`，独立 std out/err 日志。
  - **按美股交易日门控**：DSA 侧 `main.py --stocks <us>` 已按 NYSE 日历空转；执行器侧 `run_day` 以 DSA 落库的美股 bar 存在与否天然门控（无 bar 则 `new_openings_degraded`）。**夏令时**导致北京对应时刻浮动 1 小时（见附录 B）——plist 用略早的固定北京时刻 + 交易日内容门控兜底，不追分钟级精度。
- **落点**：`ops/run_us_executor_daily.sh`、`ops/com.quant.us.executor.daily.plist`、`ops/com.quant.us.dsa.daily.plist`（均新增）；`launchctl load` 步骤写进存证（不自动 load，交 CEO/你确认）。
- **验收**：`launchd` 干跑（手动执行脚本）在美股交易日产出：US 信号入 `paper_us.db` disciplined + US 模拟成交 + 快照；美股节假日跑则优雅空转、无脏写；两个 US plist 时刻与 A 股 plist 不重叠。

### U8｜美股周复盘（新增 `weekly_review_us.py`，双基线换 SPY，不改 `weekly_review.py`）

- **动作**：新增**自包含的** `ops/weekly_review_us.py`——为与两执行器一致的物理隔离，**不 import `weekly_review.py`**，需要的纯数学 helper（`max_drawdown`/`bootstrap_mean_ci`/`profit_loss_ratio`/等权收益/持有天数等）**复制进本文件**（这些是无副作用的统计函数，复制成本低）：
  - **US 基线抓取**：SPY，走 stooq CSV 或 yfinance/finnhub（我方复盘件、可自行联网，非 DSA 只读边界）；不带 A 股的 `_hs300`/`_eastmoney`/`_tencent`。
  - 读 `paper_us.db` + `UsExecutorConfig`（`stock_pool=US`），等权基线算 US 池。
  - 两条基线（SPY + 等权 US 池）**口径统一**（推荐都裸收益作市场基准，策略收益已含费用即超额），与 A 股口径一致并注明；报告标题用 "SPY"。
- **落点**：`ops/weekly_review_us.py`（自包含新增）+ US 基线抓取库进 venv。**不改、不 import** `ops/weekly_review.py`。
- **验收**：US 周复盘产出 SPY 与等权 US 两条基线均有数、区间对齐、口径一致且注明；读 `paper_us.db`；A 股周复盘产出逐字节不受影响。

### U9｜单测与验收存证（贯穿）

- **动作**：US 单测放**新增** `executor/us/tests/`，覆盖 U0–U4 关键分支（市场过滤、无涨跌停、T+0 当日卖、US 费用口径、NYSE 门控空转）；A 股既有 `executor/tests/` **不改一行**，作为回归护栏原样跑。另加一个 **`test_cn_us_isolation`**：同一 DSA 库混入 cn+us 信号，分别跑 A 股执行器与美股执行器，断言——(1) A 股只碰 `paper.db`、美股只碰 `paper_us.db`（跑后互测对方库 md5 不变）；(2) 各自只消费自己市场的信号；(3) 两库账户/持仓无交叉行。每完成一项 commit + 存证 `runtime_data/acceptance/`（命名仿 `M7_M8_*`）。
- **验收**：
  - `python -m pytest executor/tests executor/us/tests` 全绿（含 `test_cn_us_isolation`）；
  - **零耦合证明**：`grep -rE "from executor\.(engine|ledger|rules|models|config|time_guard|signal_reader) import|import executor\.(engine|ledger|rules|models|config|time_guard|signal_reader)" executor/us/` **无命中**（美股副本运行期不依赖任何 A 股件）；
  - **零改证明**：`git diff --stat` 显示 A 股热路径七文件（护栏 G-US §2）**零改动**。

---

## 附录 A：SPCX 短历史降级（运行注意，非阻塞）

SPCX 2026-06-12 纳斯达克 IPO，上市不足 1 个月、日线极短：依赖长周期指标（MA20/60）短期不完整甚至取不到。Codex 需确认 **DSA 对短历史标的降级不报错**（取不到长周期指标时降级而非崩），并在 US DSA 跑批存证里记录 SPCX 当前可得历史长度。次新股波动剧烈（解禁/炒作-退潮），早期信号额外审慎——但正因它是最热的情绪驱动次新，是"LLM 读情绪"假设最好的试验与对照，值得单独观察。

## 附录 B：夏令时（DST）说明

美股 16:00 ET 对应北京时间随美国夏令时切换浮动：夏令时(EDT, 3 月中–11 月初)≈北京次日 04:00 收盘、冬令时(EST)≈05:00 收盘。US 批次取略晚的**北京 05:10/05:30** 起跑可覆盖两种情形；真正的门控靠"当日美股 bar 是否已落库"而非墙钟精度。首版不做分钟级 DST 自适应，plist 注释标明此权衡。

## 附录 C：Tavily 接线要点

- 环境变量：`TAVILY_API_KEYS`（**复数、逗号分隔**，可多 key 轮换；DSA `src/config.py` 解析）。
- 密钥落地：`runtime_data/secrets/` + DSA 运行 `.env`，不入 git（`.gitignore` 已覆盖 `runtime_data/secrets/`，Codex 复核）。
- 定位：US 新闻走 Tavily（英文），与 A 股 Bocha 中文新闻是**两套料**，让平行实验更有对照价值。社媒情绪（Reddit/X，`SOCIAL_SENTIMENT_API_KEY`）本期不配、留后续可选。

---

## 验收标准映射（计划书 §4 → 本清单）

| 计划书 §4 验收项 | 对应清单项 |
|---|---|
| 美股规则单测：T+0 当日可卖、无涨跌停、美股费用口径、NYSE 日历门控 | U2 / U3 / U4 / U7 |
| `paper_us.db` 与 `paper.db` 完全隔离（互不写） | U0 / U5 / U9（`test_cn_us_isolation` + 零耦合 grep 证明） |
| US launchd 于北京清晨按美股交易日触发 | U7 |
| 一次 US 跑批产出：US 信号入库 + US 模拟成交 + US 周复盘（含 SPY 基线） | U6 / U7 / U8 |
| 三大纪律在美股 prompt 同样生效（金丝雀复验） | U6（G5 US 完成 + disciplined 入 `paper_us.db`） |
| 红线照旧（DSA 只读、依赖进 venv、密钥不入 git） | §0 红线 + 各项验收 |

## 交付方式

Codex 按 U0→U9 顺序执行，每项：**commit + 存证 `runtime_data/acceptance/`**，Claude 逐项核验。全部完成后出一份合并验收结论并更新 `PROJECT_LOG.md`。**A 股赛道的日常积累不受影响**——落地在护栏 G-US 的双重硬证据上：(1) A 股热路径七文件 `git diff --stat` 零改动；(2) A 股既有单测/台账/快照逐字节不变。任何一项若被迫要动共享文件 → 停工上报，不擅改。
