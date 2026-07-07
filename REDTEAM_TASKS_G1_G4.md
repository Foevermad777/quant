# 红队驱动工程清单 G1–G4（CEO 已批准，2026-07-06）

制定：Claude｜执行：Codex｜验收：Claude｜项目根：`~/quant`
来源：OpenClaw 红队 + 代码核查（`runtime_data/acceptance/RED_TEAM_RESPONSE_20260706.md`）。CEO 已批准两战略决策 + 本周优先级。
红线继承：不改 DSA 源码（只配置/我方代码）、DSA 库只读、依赖进 venv、密钥不入 git、遇计划外停工上报。

## 优先级
- **本周最高优**：G1（成交改造）+ G2（Path A 纪律注入）——两者落地验收后，才起算「信号质量基线」Day 1。
- **紧随**：G3（防未来函数护栏）。
- **Phase 2 起步（设计先行）**：G4（Path B 自研诊股包裹层）。
- 当前 launchd 运行继续，重定义为「工程压力测试」（验证 launchd/Tushare/执行器/数据完整性可靠性），非信号基线。

---

## G1｜成交模型改造：消除幸存者偏差（改的是 M7 执行器成交侧）

**背景**：`executor/models.py:52-74` 现为「限价=entry_high」三分支，代码核实存在结构性幸存者偏差（赢家踏空/输家接飞刀）。

**改造**：
1. **开仓成交价 = 次日开盘价**：改 `models.py` buy_fill——bar 存在则 `FillResult(filled, price=float(bar.open), reason='next_day_open')`；bar 缺失(停牌)/open 缺失仍走 unfilled 顺延。**entry_high 不再参与择价**（保留在信号记录里仅作参考）。⚠️ 为保证「无偏度量」，成交不设任何 entry_high 门槛（「开盘价过高不追」是 Phase 4 实盘细化，不进度量口径，否则重新引入选择偏差）。
2. **开仓侧双倍滑点**：买入用 `2×slippage_rate`（0.002）；卖出维持单倍（0.001）。实现建议：给 SlippageModel.execution_price 加 multiplier 参数或 engine.py:193 传 `config.slippage_rate*2`；新增 config 项 `open_slippage_multiplier=2.0` 便于调。
3. **摩擦成本不改**：双边佣金 max(5,万2.5) + 卖出印花税万5 已是 A 股真实全额。
4. **旧限价模型留在开关后做 A/B**：新增 config `FILL_MODEL = 'next_open'（默认）| 'limit_entry_high'`，两模型并存。目的——**量化幸存者偏差的实际幅度，把 bug 变成可测反面教材**。

**验收**：单测更新（原 open_within_limit/intraday_limit_touch/limit_not_touched 用例改为 next_open 行为）+ 新增 A/B 切换测试；Claude 独立复算一笔「次日开盘价×1.002 买入 + 全额摩擦」对账；A/B 两模型各跑一遍回填、偏差幅度出数。

## G2｜Path A：三大纪律软注入（纯配置，零改 DSA 源码）

**目标**：清空 DSA 自带 bull-only 单情景基线，换上我方三大纪律，让信号基线跑在我方 prompt 上。

**做法**：
1. 我方目录建 skill：`~/quant/dsa_skills/discipline.yaml`，category=framework，instructions 写全三大纪律：
   - **数据溯源**：引用任何新闻/公告/财报必须标注精确发布日期；区分 CY/FY 口径；禁止摘要式无源结论。
   - **术语规范化 + 失效条件**：禁模糊词（放水/美债跌→规范为流动性扩张/美债收益率下降）；每个看多看空必须在 `invalid_conditions` 写明反证情景。
   - **Base/Bull/Bear 多情景 + 置信度逻辑支撑**：三情景条件概率推演；confidence 必须附逻辑支撑，极端单边情绪自动降权。
2. `.env`：`AGENT_SKILL_DIR=/Users/yongyuanbuanzhede/quant/dsa_skills`、`AGENT_SKILLS=discipline`（锁定后 `explicit_skill_selection=True` 会清空 defaults.py:73-89 的 7 条 bull-only 基线）。
3. **⚠️ 已知副作用需 Codex 核实**：配置具体 skill 会触发 `pipeline.py:463-473` 自动切 Agent 多智能体路径（4-6 次 LLM 调用，比传统单次慢/贵）。Codex 需实测 5 股跑一轮的耗时/成本是否可接受；若不可接受，查是否能保留传统路径注入（analyzer.py:2319-2366 也走 skill 注入）。

**验收**：跑一轮 600519，证据要看——① 实际 system prompt（日志或抓取）里 DSA 的 7 条 bull-only 基线消失、我方三大纪律在；② LLM 输出里出现带日期的新闻引用、三情景、失效条件（软约束，不保证 100% 合规——硬强制是 G4 的活）；③ 耗时/成本记录。**明确标注上限**：这是软指令，硬编码的多头排列/乖离率评分锚点与固定 JSON schema 仍并存、无法覆盖。

## G3｜防未来函数护栏（当前非 active bug，防复盘归因 + Phase 2 回测）

**背景**：日频成交侧无 look-ahead（次日开盘成交，新闻都更早）；风险在复盘归因与将来回测。我们有 Bocha 秒级时间戳。

**做法**：
1. 每次分析记录「决策时点」（decision_timestamp）；
2. 复盘/归因侧强制校验：某条新闻若 `published_time > 被预测 bar 的可用时点`（如声称预测 D 日走势但新闻发于 D 日 15:00 后），标记为「事后信息」不计入正向归因；
3. weekly_review 加时点校验列。

**验收**：构造一条盘后发布的新闻，验证它被正确标记/排除出同日正向归因。

## G4｜Path B：自研诊股包裹层（Phase 2 起步，本轮只做设计 + 首个校验器）

**目标**：不改 DSA 源码实现「硬强制」——在我方 executor/ 建包裹层，对 DSA 输出做结构化校验 + guardrail 门控。这是 Phase 2 自研诊股层的雏形。

**做法（本轮范围）**：
1. 设计文档：包裹层接口（输入=DSA 分析结果，输出=经校验/门控的信号）；
2. 首个校验器：检查 DSA 输出是否含①数据来源标注 ②失效条件 ③三情景字段——**缺失则拒绝入库或降 confidence**，并记录门控原因。
3. 与 G2 的软注入配合：G2 让 LLM「被要求」产出，G4 让我方「强制校验」是否真产出。

**验收**：设计文档 + 首个校验器单测（缺来源/失效条件/三情景各触发一次门控）。

---

## 与 M7/M8 的关系
- G1 **取代** M7 的限价成交侧（M7 其余部分——T+1/涨跌停/停牌/S1/台账——不变）。
- G3 增强 M8 复盘的时点严谨性。
- G2/G4 是 Phase 2 自研诊股层的前置与雏形。

## Codex 执行方式
每项 commit + 存证 `runtime_data/acceptance/`，Claude 逐项验收。G1+G2 完成验收后，宣布「信号质量基线 Day 1」，14 天重新计时（工程压测数据并入，不浪费）。
