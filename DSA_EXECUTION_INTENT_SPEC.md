# DSA 执行意图结构化规格（B 方案，中期，未排期实施）

> 状态：**规格草稿**。前置依赖 C 方案（conditional_entry→限价计划，2026-07-16 已落地）先积累观测数据。
> 出处：2026-07-16 "DSA action 与正文不匹配" 深挖 + C/A/B 三层方案设计讨论。

## 1. 要解决的问题

**根因**（2026-07-16 核查确认）：DSA 的 `action` 字段由 `sentiment_score` 经 canonical scale
机械映射产生（60-79 → buy，见 vendor `src/schemas/decision_scale.py`）。score 表达的是
**立场**（有多看多），不是**执行指令**（现在买/回调再买）。当分析正文说"切忌追高、回调至
入场区再分批低吸"时，`action=buy` 把条件压扁丢失，造成系统性"标签与正文不一致"。

**代价**（2026-07-06 ~ 07-15 实测）：
- 一周 68 条 DSA buy（A股 32 / 美股 36），S1 一致性通过率 **0%**，全部拦截，零成交。
- "正文到底什么意思"目前靠两层脆弱组件重建：G5 补全 LLM（概率性，会误读）+
  S1 关键词表（硬编码，新表述会漏）。信息在源头生成时就存在，却在下游花钱重建。
- 单 action 槽表达不了 position_context_split（一个信号、两个受众：持有者 hold /
  空仓者 watch），这是美股最大冲突类（27/55）。

## 2. 方案：立场与执行意图拆分（源头自报）

**不动 `action` 枚举**（canonical scale 与报表统计共用口径；扩枚举要求全部消费方同步
升级，漏一个就静默劣化——executor 的 `normalize_action` 会把未知值归为 unknown→hard_conflict）。
改为**新增结构化字段组**，由 DSA 生成时与正文同源自报：

```
execution_intent: {
  flat_account_action:  buy | watch | hold | avoid        # 空仓者该做什么
  holding_action:       hold | add | reduce | sell        # 持有者该做什么
  entry_condition: {
    type:   immediate | pullback_zone | breakout_level | stabilize | none
    level:  <价位或区间，pullback_zone 复用 entry_low/high；breakout 填触发价>
    note:   <不可机器执行的条件原文，如"企稳后">
  }
}
```

- `pullback_zone` → 下游直接映射为限价计划（C 方案已备的执行通路）。
- `breakout_level` → 暂映射为 watch（日线系统无法盘中确认突破；Phase 2 若接分钟线再启用）。
- `stabilize`/`none` + 正文模糊 → watch。
- 与既有 `disciplined_signals.schema_version` 版本化机制配套，新字段只向前生效，
  **绝不回填历史**（point-in-time 红线）。

## 3. G5 与 S1 的角色变化

- **G5 从"解释者"降级为"校验者"**：不再从正文猜意图，而是比对 DSA 自报的
  execution_intent 与自己的独立解读，不一致才报警（`intent_mismatch` 事件）。
- **S1 保留，一层不撤**（纵深防御）：标签 vs 自报意图 vs G5 校验三方一致才放行。
- vendor 侧已落的确定性标注 `metadata.price_above_entry_zone`（A 方案，2026-07-16）
  作为过渡期桥梁：S1 可将其作为"条件性买入"的权威旁证，减少对关键词表的依赖。
  （S1 消费该标注的接线属于本规格 Phase 1。）

## 4. 迁移与验收门

1. **Shadow 双写 ≥ 10 个交易日**：DSA 自报意图与 G5 解读并行落库，互不影响执行。
2. **一致率门**：自报 vs G5 解读一致率 ≥ 90% 才允许 G5 降级为校验者；
   分歧样本逐条人工归因（谁错），错方修正。
3. **回归门**：executor CN+US 全绿 + 对照组（旧信号无新字段仍走现行路径）。
4. **观察指标**（C 方案已开始积累）：
   - 限价成交率、限价 vs 开盘价的价差改善
   - **踏空率**（信号有效期内未回落到区间、随后涨到 target 的比例）——红队"赢家踏空"关切
   - **接飞刀率**（限价成交后 2 日内触止损的比例）——红队"输家接飞刀"关切
   这两个指标决定 pullback_zone 语义是纪律还是逆向选择陷阱。

## 5. 非目标

- 不改 canonical scale 的 score→action 映射（报表兼容）。
- 不做盘中/分钟级执行（另行立项，见 2026-07-16 讨论：日线+限价单已覆盖
  "盘中触区间"的捕捉；盘中撤单/突破确认才需要分钟数据）。
- 不重跑/回填任何历史信号。

## 6. 实施位置

- vendor `daily_stock_analysis`：prompt schema 增槽 + extractor 透传 + 单测
  （注意该仓 AGENTS.md 纪律：最小改动、CHANGELOG、不擅自 commit）。
- 本仓 executor：`discipline_completion.py`（G5 校验者化）、`signal_reader*.py`
  （读自报意图）、`intent_resolution.py`（三方比对）。
