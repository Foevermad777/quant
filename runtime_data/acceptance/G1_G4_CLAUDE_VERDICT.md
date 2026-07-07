# G1–G4 验收结论（Claude，2026-07-07）

按 secure-build-and-verify 纪律核验（"配置≠生效，要用真实数据验效果"）。Codex 本轮把 G1–G4 全实现。

## 判定：通过（G2 效果由 Claude 补做真验证；两处如实标注为"待/软"）

| 项 | 判定 | 关键证据 |
|---|---|---|
| G1 成交模型 | ✅ 实现正确，⚠️真实偏差幅度待测 | next_open 默认、limit_entry_high 留开关做 A/B；开仓双倍滑点 engine.py:201-204(multiplier=2.0)、卖出单倍:453；fixture 10.5×(1+0.001×2)=10.521 ✓；42 测试过 |
| G2 纪律注入 | ✅ 效果已真验证（升级 Codex 弱证据）| **见下** |
| G3 防未来函数 | ✅ 实现 | executor.time_guard + weekly_review 时点校验列；单测:2026-07-06 15:30 盘后新闻被标 excluded_after_bar_available |
| G4 包裹层门控 | ✅ 实现（软注入的硬后盾）| executor.guardrails.gate_dsa_output：缺 来源/失效条件/三情景 则拒绝或降 confidence，原因记 guardrail 字段；含设计文档 |

## G2 的真验证（本轮重点）

**Codex 的证据是"弱"的**：只做了 skill 激活探针（active=discipline / has_data_trace=True 等——那只是检查 YAML 文本里有没有那几个词），并明说"为省 API 预算没跑真实 LLM"。按 secure-build 纪律，这属于"配置探针 ≠ 效果验证"。

**Claude 补做的金丝雀验证（零 API 花费）**：直接调 DSA 自己的 `resolve_skill_prompt_state(config)` 拼装 prompt，搜两个金丝雀——
- 金丝雀1｜bull-only 基线（`CORE_TRADING_SKILL_POLICY_ZH` 的"严进策略/默认技能基线"）→ **残留=False（已清空）** ✓
- 金丝雀2｜三大纪律（"三大纪律/数据溯源"）→ **注入=True** ✓
- 眼见为实：拼出的 `skill_instructions` 首段即"三大纪律框架…数据溯源…必须标注精确发布日期"。

结论：**纪律注入真生效**——发给 LLM 的 system prompt 里 bull-only 单情景基线确已被清空、三大纪律确已就位。

**仍待/软（如实标注，不夸大）**：
1. **LLM 是否真"遵守"纪律**（输出里真出现带日期引用/三情景/失效条件）——属软注入合规性，需一次真实 LLM 跑观察。Codex 为省预算未跑；**但 G4 硬门控是它的后盾**（不合规则拒收/降权），所以软注入可接受。建议：把"第一次真实基线跑"同时当作合规观察，一箭双雕。
2. **G1 幸存者偏差的真实幅度未测**：当前窗口唯一 buy 信号 600900 被 S1 拦，无 open candidate，A/B 两模型都 0 成交（Codex 如实说明）。等出现真实可成交 buy 信号，A/B 差值才有数——不是缺陷，是暂无素材。

## 遗留（Codex 收尾）
1. **G1–G4 全部未提交**（git status 全是 M）+ `PROJECT_OVERVIEW_FOR_REVIEW.md` 未跟踪——补一个 commit。
2. G2 Agent 路径成本/耗时：配置具体 skill 会切多智能体路径（4-6 次 LLM 调用/股），首次真实跑时记录 5 股耗时与成本。

## 里程碑
G1+G2 效果已验收 → **具备"信号质量基线 Day 1"起跑条件**。建议第一次真实基线跑（disciplines 生效 + next_open 成交）即作为 Day 1，同时观察 G2 合规性与 G1 成本。
