# G5｜纪律补全包裹层（最小版）规格

版本：0.1　｜　日期：2026-07-07　｜　制定：Claude｜执行：Codex｜验收：Claude
CEO 已选"最小补全"。目标：不改 DSA 源码，在我方 executor 层，把 DSA 的非纪律化输出**补全**成结构化纪律信号，让信号质量 Day 1 能真正起跑。
背景根因（已实证）：DSA 硬编码 JSON schema 无 scenarios/invalid_conditions 槽位 → 软注入产不出结构化纪律（见 `DAY1_PROBE_CLAUDE_VERDICT.md`）。G5 用**我方独立 LLM 调用 + 我方 schema 结构化强制**解决。

## 1. 位置与数据流

```
DSA 产出(只读)  ──►  G5 补全(我方一次 Gemini 结构化调用)  ──►  我方纪律信号store  ──► G4 校验 ──► 执行器消费
  decision_signals                                          disciplined_signals(新表)
  analysis_history
  news_intel(带日期)
```

## 2. 输入（全部只读 DSA 库，真实字段已核）
每只股票、每条 DSA `decision_signals`（active）为一个补全单元，取：
- `decision_signals`：action / confidence / score / entry_low/high / stop_loss / target_price / reason / risk_summary / catalyst_summary / invalidation(现空) / source_report_id；
- `analysis_history`（按 source_report_id 关联）：operation_advice / sentiment_score / analysis_summary / news_summary / trend_prediction；
- `news_intel`（该 code）：title / snippet / url / source / published_date —— **只把有 published_date 的作为可溯源材料；无日期的仅作线索，禁止当核心依据**（纪律1）。

## 3. 处理：一次直连 Gemini 结构化调用（关键机制）
- **不经 DSA**、我方 executor 代码直发 Gemini `generateContent`（REST，`requests`/`urllib`，走本地代理 127.0.0.1:7890，key 从 `runtime_data/secrets/gemini_api_key.txt`）。executor venv 无 litellm，故直连，不引重依赖。
- **用 `responseSchema` 结构化输出强制字段存在**（这是 DSA 固定 schema 做不到、我方能做到的核心）。我方 schema 必含：
  - `scenarios`: { base, bull, bear }，每个含 `assumptions` / `triggers` / `key_risks` / `probability`（三情景，纪律3）；
  - `invalid_conditions`: 结构化数组，每项含 `condition` / `trigger_price_or_data` / `type`(price|data|event)（失效条件，纪律2）；
  - `source_attribution`: 数组，每项 `claim` / `source` / `published_date`（YYYY-MM-DD，仅引有日期的 news_intel，纪律1）；
  - `confidence`(0-1) + `confidence_rationale`(须覆盖证据质量/价位/基本面/技术/反证)；
  - `single_side_flag`(bool，只列利好不列风险/极端单边时=true，并据此自动降 confidence，纪律3)；
  - `normalized_terms`(可选，术语规范化示例，纪律2)。
- 我方 prompt = 三大纪律全文（可复用 discipline.yaml 文本）+ 明确"基于以下 DSA 分析与带日期新闻补全，不足处降置信度或标注缺口"。

## 4. 输出：我方纪律信号 store（不写 DSA 库）
- 新表 `disciplined_signals`（建议落 `runtime_data/quant/paper.db` 或同目录 companion 库，我方可控）：
  - 键：`source_signal_id`（关联 DSA decision_signals.id，**幂等**：同源只补一次，可标记版本重跑）；
  - 存：上述结构化字段 + 继承的 action/entry/stop/target + 补全时间戳 + 用的模型 + 补全调用 token/耗时。
- **执行器改读此表**：`signal_reader` 增加"读 disciplined_signals"路径，执行器消费**纪律信号**而非 DSA 原始信号（S1 口径/同股取最新/UTC 继续适用）。

## 5. G4 闭环（补全→校验）
- 每条 G5 产出的纪律信号，过一遍 `gate_dsa_output`（G4）；
- **预期：G5 补全后应 PASS**（这是与"DSA 原始信号必被 G4 拒"的对照组）；仍不合规的按 reject/degrade 处理并记录。
- 校准 G4 使其放行标准与 G5 输出结构对齐（source_attribution 有日期项才算溯源；scenarios 三键齐；invalid_conditions 结构化非空）。

## 6. 验收标准（secure-build 纪律：真实数据 + 对照组）
- [ ] G5 对**真实** 600519（source signal id=18 / analysis id=21）跑通，产出纪律信号，结构化含 base/bull/bear 三情景 + 结构化 invalid_conditions + 带日期 source_attribution；
- [ ] **对照组金丝雀**：DSA 原始 id=18 过 G4 = **reject**（缺纪律）；G5 补全后过 G4 = **accept**（合规）——证明确是 G5 这步在起作用；
- [ ] source_attribution 的 date 真实来自 news_intel（非泛化 "agent:gemini"）；无日期新闻未被当核心依据；
- [ ] 幂等：同 source_signal_id 重跑不重复入库；
- [ ] 执行器改读 disciplined_signals，链路通（有可成交 buy 时能落一笔纸面交易）；
- [ ] 记录补全调用 token/耗时/成本（预期 +1 调用/股 ~$0.05）；
- [ ] 红线：DSA 库全程只读、纪律信号入我方 store、key 不入 git、跑前 nc 代理预检。

## 7. 明确不在最小版内（防范围膨胀）
- 不重写/替换 DSA 的诊断（那是"完整自研诊股层"，Phase 2）；
- 不做置信度对后验结果的**校准回路**（Phase 2）；
- 不做多模型/多 prompt 赛马（Phase 2）。

## 8. 完成后
G5 在 600519 验收通过 → 跑全池 5 只补全 → **这份纪律化信号链才是信号质量 Day 1**，launchd 继续、D+14 出首份定性复盘。之后再开美股赛道。
