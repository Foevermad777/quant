# daily_stock_analysis 调研总结

版本：0.1
日期：2026-07-05
调研方式：Claude 多 agent 并行深读（6 个子系统 + 1 轮交叉纠错），仓库位置 `/Users/yongyuanbuanzhede/.openclaw/workspace/daily_stock_analysis`
定位：本文件是《项目协作章程》与《金融策略 PRD》的调研支撑材料，回答"这个开源项目有什么、我们能复用什么、PRD 排雷预检的答案、fork 还是外挂"。

## 一、项目概况

- 作者 ZhuLinsen，MIT 许可证（fork/魔改/商用无限制，仅需保留版权声明）。
- 定位：**自选股每日 AI 分析 + 推送**系统，覆盖 A/港/美/日/韩/台股。设计中心是"几只到几十只自选股"，不是全市场扫描。
- 规模：约 494 个 Python 文件、13.5 万行核心代码；`.env.example` 910 行约 360 个配置项；228 个测试文件；45 篇文档；React Web 工作台 + Electron 桌面端 + 飞书/钉钉/Discord 机器人。
- 上游迭代极快（issue/PR 编号已到 #1900+，高频 fix 集中在 analyzer.py/pipeline.py/config.py 这几个 3000-4800 行的巨型文件）。

## 二、架构与核心链路

```text
数据层 data_provider/（多源降级链+熔断+SQLite缓存）
  -> 分析管线 src/core/pipeline.py（3671行总编排）
       路径A：src/analyzer.py 单次 LLM 调用（litellm 多渠道多Key）
       路径B：src/agent/ 多智能体 technical -> intel -> risk -> [策略skill] -> decision
  -> 三层机械护栏（结构稳定性 / 市场阶段 / 大盘退潮，关键词匹配式拦截改写）
  -> 决策信号抽取 decision_signal_extractor -> decision_signals 表（结构化信号资产）
  -> 后验评估（T+1/3/5/10 胜率统计）+ 报告级回测 + portfolio 事件溯源记账
  -> 决策仪表盘 Markdown 推送（13+ 渠道）/ Web 工作台 / bot
```

关键机制：

1. **策略即 Prompt**：`strategies/*.yaml` 15 个自然语言策略（含 `shrink_pullback` 缩量回踩、`bottom_volume` 底部放量），零代码即可新增，`AGENT_SKILL_DIR` 支持外挂自定义目录、同名覆盖内置。策略无量化计算成分，技术指标全部由 `src/stock_analyzer.py`（pandas 确定性计算 MA/MACD/RSI/量比/支撑压力）经工具喂给 LLM。
2. **结构化信号资产层**：`decision_signals` 三表（信号/后验结果/人工反馈），带同源去重、生命周期 TTL、反向信号自动失效、8 维度胜率统计。`POST /api/v1/decision-signals` 是完整的显式写入 API。
3. **模拟盘账本**：`portfolio_service` 事件溯源记账（交易/现金/公司行动流水，FIFO/均价重放，快照估值，集中度/回撤/止损距离风险报告）。但**交易需人工录入，没有"信号→自动模拟成交"的执行器**。
4. **护栏模式**：LLM 输出后由规则代码改写（数据降级→置信度封顶；盘前拦截"立即买入"；大盘退潮 buy→hold 并记录调整原因）——这正是 PRD"风控层拦截"的现成范例，但实现是 zh/en/ko 三套关键词表，换 prompt 措辞可能静默失效。
5. **运行模式 6 种**：CLI 单次 / 本地定时（18:00 每日）/ FastAPI 服务 / GitHub Actions / Docker Compose / 桌面端。GA 模式无状态（SQLite 不跨次持久化），**做连续复盘必须 Docker 或本地部署**。
6. **LLM 成本统计已内置**：`llm_usage` 表逐次记录 token（分 analysis/agent/market_review），`/api/v1/usage` 端点可查；支持 per-agent 分模型路由（便宜模型跑 technical/intel、强模型跑 decision），是控制批量成本的关键杠杆。

## 三、与我们 MVP 的映射

### 信号字段对照（章程第 6 节 JSON vs decision_signals）

| 章程字段 | DSA 对应 | 现状备注 |
|---|---|---|
| symbol / market | stock_code + market | ✅ 直接对应 |
| direction | action（8 态） | ✅ 映射即可 |
| confidence | confidence (0-1) | ⚠️ 目前只是"高/中/低"→0.8/0.6/0.4 的机械映射，非 LLM 数值直出 |
| entry_price | entry_low / entry_high | ✅ |
| stop_loss / take_profit | stop_loss / target_price | ✅ |
| valid_until | expires_at | ⚠️ 是服务端默认 TTL 补齐，不是 LLM 判断产出 |
| invalid_conditions | invalidation / watch_conditions | ❌ DB 有列，但自动提取链路**完全不生成** invalidation，需在 prompt 强制输出并接入 |
| reasoning_summary | reason / risk_summary / evidence | ✅ 还带 guardrail_reason 溯源 |
| max_position_pct | 无 | ❌ 需扩展 |
| Base/Bull/Bear 多情景 | 无 | ❌ 完全没有，需自建 schema |

### 三大机械纪律现状

1. **数据溯源**：❌ 不满足。搜索源新闻 `published_date` 归一化后只有**天级**精度（部分源本身就是"2 days ago"式模糊值，无日期的默认丢弃）；财务数据无 CY/FY 口径标注。**仓库内唯一秒级时间戳路径是 RSS 情报源**（`intelligence_service`，pubDate 精确到秒入库）——MVP 新闻溯源应以自建 RSS/公告情报源为主、搜索引擎为辅。
2. **市场判断 + 失效条件**：⚠️ 半满足。护栏机制是现成的机械拦截范例；但失效条件不闭环——invalidation 只是文本，系统不会自动据此把信号置为 invalidated（可用 alerts 告警中心的 price_cross 规则把止损/失效价位注册为规则，补一个映射器）。
3. **多情景 + 置信度逻辑支撑**：❌ 不满足，需自建。可参考 `analysis_context_pack`（数据质量状态标注）+ `phase_decision_guardrail`（数据降级→置信度封顶）的模式做"置信度逻辑支撑"的机械实现。

### PRD 排雷预检答案（第 3 节 Pre-flight Check）

- **行情数据源**：A 股日线降级链 Efinance(P0)→Tencent(P0)→AkShare(P1)→Pytdx(P2)→Baostock(P3)→YFinance(P4)；配置 `TUSHARE_TOKEN` 后 Tushare 自动升为绝对首选。限流实况：Tushare 免费档 80 次/分 + 500 次/天（筹码 cyq_perf 每天 15 次）；AkShare/Efinance 为东财爬虫口径，内置每请求随机休眠 2-5s/1.5-3s 防封。**沪深300 全池日更必撞限流 + LLM 成本爆炸，必须先量化粗筛缩池**——这反过来印证了章程"量化层粗筛→LLM 精审"的路线。
- **北向资金**：❌ 已废弃（2024 年 8 月后实时披露停止，代码里整段注释）。只能改用 T+1 持股口径（Tushare moneyflow_hsgt，需积分自行接入）。主力资金流（个股/板块）✅ 已支持但是 fail-open 尽力而为。
- **沪深300 成分股**：无现成 fetcher，需自加（建议 akshare `index_stock_cons_csindex("000300")` 或 Tushare index_weight，落成本地 CSV）。成分池可经 **watchlist REST API 动态注入**，不必改代码。
- **新闻舆情 API**：支持 Bocha/Tavily/SerpAPI(百度)/Brave/Anspire/MiniMax/SearXNG 多源多 Key 轮询，3 天时效硬过滤——但时间戳精度问题见上。
- **Prompt 位置**（PRD 说"集中在 src/analyzer.py"——方向对，但实际更散）：
  - `src/analyzer.py`：SYSTEM_PROMPT(L2068，含完整仪表盘 JSON 模板)、LEGACY 版(L1881)、`_format_prompt`(L3670，约 500 行 f-string 用户 prompt)
  - `src/agent/executor.py`：AGENT_SYSTEM_PROMPT(L221) + LEGACY + CHAT 版
  - `src/agent/agents/*.py`：technical/intel/risk/decision 四个 agent 各自的 system_prompt
  - `src/schemas/decision_scale.py`：评分口径常量；`src/agent/skills/defaults.py`：默认交易纪律（**三大纪律注入的最佳落点**）
  - `src/market_phase_prompt.py`（时间纪律模板范例）、`src/services/daily_market_context.py`、`src/analysis_context_pack_prompt.py`、`src/market_analyzer.py`（大盘复盘）
  - ⚠️ 最大的坑：仪表盘 JSON 契约在 **4 份 prompt 副本 + report_schema.py + 解析器 + extractor + 护栏词表**之间隐式同步，改任何字段名都是全链路手术。

## 四、核心建议：外挂封装为主，薄 fork 为辅

交叉验证 agent 基于代码结构给出的判断，我认可其结论：

**不建议深度 fork 魔改 prompt**，因为：
1. 我们要改的东西（prompt、输出 schema、三大纪律）恰好落在耦合最深处（4 份 prompt 副本 + 护栏关键词表），改完即与上游实质脱钩；
2. 上游高频迭代，冲突会集中在我们必动的巨型文件上，跟进合并不可行（fork 只能 fork-and-freeze）；
3. 项目**官方支持外挂**：`SKILL.md` + `docs/openclaw-skill-integration.md` 文档化了 REST 调用路径；无 pip 打包，受支持的复用形态就是 REST API。

**推荐架构**：
- DSA 以 Docker 服务形态部署，当作**数据层 + 信号资产层 + 后验复盘 + 模拟盘记账 + 告警推送**后端（这些模块与 prompt 零耦合、开箱即用）；
- **LLM 诊股层完全自建**（独立小模块）：自己的 prompt 模板（注入三大纪律）、Base/Bull/Bear 三情景 JSON schema、confidence 数值直出，产出信号后 `POST /api/v1/decision-signals` 写入——绕开 DSA 的 4 份 prompt 副本、护栏词表和 extractor 文本二次解析这三个最大的坑；
- 缩量池筛选器自建（基于 stock_daily 表的 volume_ratio 已内置于每根日线），经 watchlist API 注入；
- 新写一个**信号→模拟盘执行器**（读 active 信号 → 按次日开盘价+滑点假设写 portfolio_trades → 平仓时对账 outcome），两端接口都是现成的，这是全系统唯一缺失的闭环环节；
- 复盘用现成的 outcomes stats（8 维胜率）+ backtest + portfolio 风险报告，缺的盈亏比/夏普/期望值自行补聚合。

## 五、风险与坑清单（复盘时逐条核对）

1. 失效条件不闭环（invalidation 无自动评估器）。
2. 护栏是关键词匹配，prompt 措辞变更后可能静默失效或误杀，需回归测试。
3. 新闻时间戳天级精度；财报无 CY/FY 口径——溯源纪律要靠自建情报源。
4. 日线缓存无复权校验，跨源混存同一股票可能价格口径漂移，回测需固定单一源。
5. 回测是"事后验证"不是"事前模拟撮合"，无手续费/滑点/组合级资金曲线。
6. 后验评估无定时任务（需手动 POST /outcomes/run），MVP 要加 cron。
7. SQLite 单机并发写弱；任务队列/去重冷却为进程内存态，多 worker 不共享。
8. GitHub Actions 模式无状态，连续复盘必须 Docker/本地部署。
9. 社媒舆情源仅美股（Reddit/X）；A 股舆情只有新闻搜索 + RSS。
10. AkShare/Efinance 爬虫口径数据对外服务有合规灰区（自用无碍）。
