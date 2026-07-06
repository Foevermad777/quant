# Codex 返工清单（迁移后 · 2026-07-06）

制定：Claude｜执行：Codex｜验收：Claude｜项目根：`/Users/yongyuanbuanzhede/quant`
来源：M7/M8 验收（`runtime_data/acceptance/M7_M8_CLAUDE_VERDICT.md`）+ 迁移验收（`M_RELOCATION_CLAUDE_VERDICT.md`）+ 2026-07-06 数据完整性讨论。
红线继承：DSA 库只读、台账独立、依赖进 venv 不污染全局、密钥不入 git、遇计划外情况停工上报。

## 执行顺序（有依赖，按序做）

### P0｜补 relocation 提交（先做，清理未提交态）
- 现状：迁移后 5 个文件 modified 未 commit（PROJECT_LOG.md + ops 4 个路径edit）。
- 动作：一个提交，message 如 `chore: relocate project to ~/quant (paths + launchd)`。
- 验收：`git status` 仅 ignored；`git log` 新增该提交。

### P1｜R4 归因顺序（小改，先改再谈其他 R）
- 问题：engine 先记 data_gap 再评估 open_candidates，掩盖了"0 开仓真因是 S1 过滤"。
- 动作：调整 run_day 判断顺序——先评估候选/S1，再记数据状态；让 signal_events 能同时反映"S1 挡单"与"数据缺口"两个独立事实，不互相覆盖。
- 验收：构造 07-06 场景（有 buy 信号被 S1 挡 + 部分股缺 bar），signal_events 两类事件都在、归因清晰。

### R2｜sell/reduce/avoid 平仓触发（HIGH·功能缺口）
- 问题：计划明列为平仓触发，但代码无消费路径，持仓期 LLM 喊卖会被无视。
- 动作：signal_reader 增加 exit 类信号读取；engine 在盯市阶段消费——持有该股且收到 sell/reduce/avoid（同样走 advice 层口径 + 同股取最新）则按次日开盘价平仓/减仓，写台账与 signal_events。
- 验收：单测覆盖"持仓中收到 sell → T+1 次日开盘平仓";reduce 按比例减仓;avoid 等价 sell 处理（或按你实现口径，注释说明）。

### R3｜沪深300 基线 + 计费对齐（HIGH·复盘残缺）
- 问题：weekly_review 从 stock_daily 取指数 bar，但 DSA 不抓指数→沪深300 基线永久为空；且指数基线裸 close 无费用，等权基线却计费，两者口径不一致。
- 动作：weekly_review 自取沪深300 行情（akshare `index_zh_a_hist("000300")` 或等价接口，独立于 DSA 只读边界——这是我们自己的复盘件，可自行联网取数）；两条基线统一计费口径（要么都裸收益、要么都按同样滑点/费用，二选一并注释说明，推荐都裸收益作为"市场基准"，我们的策略收益已含费用即为超额）。
- 验收：WEEKLY_REVIEW 里沪深300 与等权两条基线均有数、区间对齐、口径一致且注明。

### R4 余项｜apply_trade 测试 + ST 接线 + NULL 幂等
- apply_trade（引擎真实写路径）补单测 + 一个 run_day 引擎级集成测试（涨停禁买/pending 卖出/settle 全链）。
- ST 限价接线：engine 三处涨跌停判定传入 is_st（从股票代码或 DSA 数据判定），当前池无 ST 但补上。
- 幂等 NULL 键：apply_trade/record_trade 对 signal_id=None 用 -1 哨兵（与 record_event 一致），防 NULL 卖出重跑重复入账。
- 验收：新增测试覆盖三点。

### D1｜行情缺口补抓工具（数据完整性·新增）
- 目标：保证股池最近 N 天日线完整，不静默带窟窿（今天 07-06 只落 3/5）。
- 动作：ops 层加一个"缺口补抓"步骤，在执行器跑之前运行——扫描股池最近 N（如 10）交易日，对缺失的 (股,日) **定向触发 DSA 补抓**（调 DSA 的数据获取路径，因写库归 DSA 域；不要在执行器里写 DSA 库）。注意：**间隔重试、不密集猛抓**（失败是反爬/限流性质，猛抓招封）；单股多源已由 DSA 降级链覆盖，本工具只负责"发现缺口→触发一次补抓→记录仍缺的"。
- 验收：对当前 07-06 缺的茅台/宁德跑一次，补上则 stock_daily 出现两条 07-06 bar；仍失败则如实记录待下轮。

### D2｜晨检增加日线完整性告警（数据完整性·新增）
- 动作：healthcheck.sh 增加一项——当日（交易日）stock_daily 该股池 bar 数 < 5 即标 WARN 并列出缺哪只，方便早晨一眼发现。
- 验收：手动对 07-06（3/5）跑晨检，应告警并列出茅台/宁德。

### D3｜Tushare token（可选·待 CEO 定）
- 性质：更稳的**行情**源（非新闻，与 Bocha 不重复），注册免费、按积分限流，免费档够 5 只股日线。配 `TUSHARE_TOKEN` 后自动升为最高优先级行情源，从根上缓解免费爬虫链不稳。
- 状态：**待 CEO 决定是否注册**。注册则 key 走 `runtime_data/secrets/` 老流程，Codex 配 .env；不注册则依赖 D1 补缺兜底。

### P-last｜执行器 backfill（须在 D1 + R2/R3/R4 之后）
- 依赖：先靠 D1 补齐 07-06 起的日线缺口、R2/R3/R4 代码落地，再 backfill，保证重放用的是完整数据 + 修正后逻辑。
- 动作：`python -m executor.engine --backfill-from 2026-07-06`，模拟盘台账从 Day 1 对齐。
- 验收：台账覆盖 07-06 至今每个交易日；Claude 抽一日独立复算对账。

## 验收方式
Codex 每完成一项 commit + 存证 `runtime_data/acceptance/`，Claude 逐项核验。全部完成后出一份合并验收结论，并更新 PROJECT_LOG。
