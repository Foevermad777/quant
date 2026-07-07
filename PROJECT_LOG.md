# 项目进度日志（新会话从这里开始读）

> **本文档用途**：任何新开的 AI 会话（Claude/Codex/OpenClaw）或未来的协作者，读完本文件即可掌握项目全貌与当前状态。
> **维护约定**：每完成一个里程碑、每做一个重大决策，由验收方（Claude）当天追加时间线条目并更新"当前状态"节。其他文档是专题深度材料，本文件是索引与状态权威源。

## 一、当前状态快照（更新于 2026-07-06 深夜）

- **项目根已迁移**：`~/quant`（原 `~/Documents/量化系统` 因 macOS TCC 阻塞 launchd 而废弃，待 CEO 确认后删）。所有路径、排程、git、验收均以 `~/quant` 为准。
- **阶段**：Phase 1 MVP。M0-M6 已验收；M7（执行器）/M8（周复盘）已实现并"有条件通过"（R1 已解决，R2/R3/R4 待整改）；迁移已验收通过。
- **自动化链已通**：迁移后 kickstart 完成首次完整端到端跑（DSA 抓数+Bocha 新闻+诊股+信号入库+执行器处理），launchd 两任务 exit 0。14 天基线积累底座就绪。
- **返工已验收通过**（`runtime_data/acceptance/REWORK_CLAUDE_VERDICT.md`）：P0/R2/R3/R4/D1/D2 全部完成（28 测试过），Tushare 已注册+Claude 接入 DSA .env（token 实测有效且能取到免费源缺的茅台/宁德 07-06 日线）。**剩收尾三步（交 Codex）**：①kickstart DSA 验证 Tushare 生效、07-06 补 5/5；②重跑 D1 确认无 remaining；③执行器 `--backfill-from 2026-07-06` 对齐台账。
- **OpenClaw 红队已响应**（`runtime_data/acceptance/RED_TEAM_RESPONSE_20260706.md`，技术结论经代码核查）：接受 Q1 成交偏差(改次日开盘价+双倍滑点，代码已证实 entry_high 幸存者偏差)、Q2 复盘改定性打脸、警告2 防未来函数护栏、警告3 OOS 红线；纪律注入找到不踩红线的两步解法(Path A 配置级软注入清空 bull-only 基线本周可做 / Path B 我方 executor 包裹层做 guardrail 强制)。**待 CEO 拍板 2 项**：①Q3 Phase2 标的池是否改中证500/1000(验证期留沪深300)；②是否本周优先做 Path A 纪律注入+Q1 成交改造。当前 14 天运行**重定义为工程压测**，信号质量基线待 Path A+Q1 落地后起算。
- **G1-G4 已实现并验收通过**（`runtime_data/acceptance/G1_G4_CLAUDE_VERDICT.md`）：G1 成交改 next_open+开仓双滑点(旧模型留A/B)、G2 三大纪律注入(Claude 金丝雀验证:bull-only 基线真被清空、纪律真进 prompt，零API花费)、G3 防未来函数护栏、G4 包裹层硬门控。**待收尾**：Codex 提交 G1-G4(全未 commit)；G2 的 LLM 合规性与 G1 真实偏差幅度待第一次真实基线跑观察(建议即作 Day 1)。
- **美股平行赛道计划已出**（`US_TRACK_PLAN.md`，CEO 已同意方向）：独立规则模块(T+0/无涨跌停/NYSE日历)+独立台账 paper_us.db+北京清晨调度+Tavily英文新闻+社媒情绪，复用三大纪律。**排在 G1/G2 提交与 A 股首基线跑之后启动**。待 CEO 提供美股股池(~5只)、账户金额、数据key。
- **CEO 待办**：`sudo pmset repeat wakeorpoweron MTWRF 17:57:00`（合盖保险，非阻塞）；确认新根稳定一天后删旧根。
- **日常运行须知**：跑批时段机器插电+开盖（锁屏无妨）；本地代理 127.0.0.1:7890 必须在线。

## 二、文档地图（阅读顺序）

1. `PROJECT_CHARTER.md` — 协作章程：四方角色（CEO/OpenClaw 产品风控/Claude 计划验收/Codex 工程）、工程纪律、红线、阶段规划。**一切争议以此为准。**
2. `FINANCIAL_STRATEGY_PRD.md` — 金融业务约束：三大机械纪律（数据溯源/术语规范+失效条件/多情景置信度）、Phase 1 标的池（沪深300 缩量池，选项 C 已拍板）。
3. `DSA_RESEARCH_SUMMARY.md` — 对参考项目 daily_stock_analysis 的深度调研：架构、可复用清单、**核心架构决策：外挂封装为主不深度 fork**。
4. `DSA_BOOTSTRAP_PLAN.md` — M0-M6 执行计划书，含全部里程碑的验收结论批注（通过/返工记录内联）。
5. `QUANT_OSS_SURVEY.md` — 开源量化项目调研与借鉴地图：执行器抄谁（RQAlpha/Hikyuu/LEAN/Freqtrade 到文件级）、Phase 2 回测短名单、Phase 4 实盘通道现实边界（散户走 miniQMT）。
6. `M7_M8_EXECUTION_PLAN.md` — 当前执行中的计划：执行器与周复盘聚合的完整规格与验收标准。
7. `runtime_data/acceptance/` — 全部验收证据与专项结论（不进 git；关键结论文件：`M4_CLAUDE_ACCEPTANCE_VERDICT.md`、`M45FIX_M6_CLAUDE_VERDICT.md`）。

## 三、关键决策登记簿（新会话必读，避免重新辩论已决事项）

| 决策 | 结论 | 出处 |
|---|---|---|
| 总架构 | DSA 当后端服务（数据/信号库/推送），自研薄层（筛选器/诊股纪律层/执行器/复盘），外挂不深 fork | DSA_RESEARCH_SUMMARY |
| 标的池 | 验证期锁死 5 只沪深300（600519/300750/601318/600036/600900）不换池；**Alpha 期(Phase 2)改中证500/1000**（不碰微盘：冲击成本/操纵/无人肉直觉），CEO 已拍板 07-06 | PRD + 红队响应 |
| 成交模型 | **次日开盘价成交 + 开仓双倍滑点 + 全额摩擦（地狱模式）**；旧 entry_high 限价模型留开关做 A/B 量化幸存者偏差。（原限价=entry_high 被红队证伪:赢家踏空/输家接飞刀） | RED_TEAM_RESPONSE / G1 |
| 纪律注入 | Path A 配置级软注入(discipline.yaml 清空 DSA bull-only 基线,本周) + Path B 我方 executor 包裹层做 guardrail 硬强制(不改 DSA 源码) | RED_TEAM_RESPONSE / G2+G4 |
| 复盘口径 | 首次复盘以**定性"验尸打脸"为主**(旧闻当新利好/看图说话/逻辑自洽但市场不认),PnL 是 10 日噪音明确降级;**样本外 OOS 刻入红线** | 红队 Q2/警告3 |
| 信号消费口径 | 以护栏后 advice 层为准；decision_signals.action 与其冲突则跳过（S1）；同股取最新（S5）；时间 UTC 基准（S6） | M4 验收 |
| LLM | Gemini flash（免费额度内），走本地代理；新闻搜索 Bocha 主源 + Tavily 备源 | M4-fix / M4.5 |
| 环境纪律 | uv 独立 Python，一切依赖进项目 venv，全局零污染；DSA 库对自研代码只读 | M0-fix / M7 计划 |
| 仓位规则 v1 | 每信号固定 10% 等权，不按 confidence 加权（未校准前加权=引噪音），单股上限 20% | M7 计划（CEO 可否决） |
| 频率 | 日频刻意为之；升频需复盘数据证明；分钟级只上告警层；秒级=换赛道不做 | 会话 07-06 |
| Phase 2 选型 | 回测短名单 RQAlpha/PyBroker/vectorbt/Qlib+RD-Agent，07-21 后横评；backtrader 停更 3 年不用 | QUANT_OSS_SURVEY |
| Phase 4 预埋 | 散户实盘通道=券商 miniQMT/Ptrade（10-50 万门槛）；vn.py 非散户首选 | QUANT_OSS_SURVEY |

## 四、时间线日志

**2026-07-04** 立项。章程与 PRD 定稿；四方协作模式确立；Phase 1 标的池选项 C（沪深300 核心资产）拍板。

**2026-07-05（日）**
- Claude 完成 DSA 深度调研（多 agent 并行），产出 DSA_RESEARCH_SUMMARY，定"外挂为主"架构。
- 出 DSA_BOOTSTRAP_PLAN（M0-M4），环境隔离为硬红线。
- M0 触发 Python 版本停工（系统仅 3.9.6）→ M0-fix：uv 用户级安装 Python 3.11，零系统污染。
- M0-M3 完成并验收：依赖隔离安装、Gemini+Tavily 配置、PRD 排雷预检（发现 Tavily 中文相关性 0/5、北向已废弃、资金流不可用）。
- M4 阻塞：Gemini/LiteLLM 全部挂死 → Claude 定位根因为本机须走 127.0.0.1:7890 代理而 Python 进程无代理变量 → .env 三行修复并在 venv 实测 3 秒通。
- M4 完成并验收（通过有保留）：5 股闭环、信号入库、成本 $0.07/天（免费额度内实为 0）。六项保留（S1-S6）记录在案，两项口径当场裁定。

**2026-07-06（一）**
- 新闻源升级：实验证明 Tavily 病根是语料库缺口（英文查茅台同样垃圾、查苹果精准）→ CEO 注册 Bocha，实测茅台 5/5 相关、秒级时间戳。
- M4.5 首验未通过 → 诊断出三根因（请求窗 oneWeek vs 过滤窗 3 天错配、SearXNG 必败仍入池、代理瞬时离线）→ M4.5-fix 五行 .env 修复 → 复验通过。**定性里程碑：真实中文新闻输入后，LLM"硬编关联"现象显著减轻（引用全部逐字可溯源）。**
- M6 调度：首验未通过（plist 未 bootstrap、ops/ 误放 vendor 内）→ 返工 → 验收通过。launchd 每工作日 17:58 单次拉起，wrapper 带代理预检+caffeinate。实测澄清：插电+开盖+锁屏不会系统睡眠（powerd 断言），合盖/电池才会深睡。
- 开源选型调研（OpenClaw 清单 + Claude 三路检索）→ QUANT_OSS_SURVEY 定稿："抄设计不抄依赖"，执行器参考实现到文件级；vn.py 散户论断修正。
- M7/M8 计划书定稿，CEO 授权 Codex 开工。项目代码自此进 git。
- 成交语义升级（CEO 提问触发）：执行器由"次日开盘价无脑成交"改为**限价单语义**（限价=entry_high 不追高线，开盘虚高但日内回落触及即当日成交；过期未成交单独统计为"纪律挡掉的追涨"）。只用日线 OHLC 实现，零新增数据依赖。
- **今晚 17:58 = 14 天基线 Day 1 首跑窗口。**
- M7/M8 Codex 交付并 commit（9e412d1/e65f4f1）→ Claude 三路独立核验：**有条件通过**。红线全清（DSA md5 不变、只读、git 净）、核心交易规则正确、复盘数学正确、独立复算成交吻合。但四项须整改（详见 `runtime_data/acceptance/M7_M8_CLAUDE_VERDICT.md`）：**R1 关键**——执行器排程无 --date 默认对 latest_trading_date(现=07-03)成交、而信号是 07-05/06 的，整条链隐含"DSA 盘后写当日 bar"前提从未实证（计划§2.3 要求的验证被跳过），今晚 17:58/18:40 排程是首次真实端到端测试；R2 sell/reduce/avoid 平仓未实现；R3 沪深300 基线因 DSA 不抓指数永久为空+基线计费不对称；R4 归因顺序掩盖 S1 真因+apply_trade 无测试+ST/NULL 潜伏。无数据丢失风险（台账可 backfill 重算）。

## 五、已知问题与观察项（复盘会逐条核对）

1. 公告维度实质无效（轮转到 Tavily 的英文噪音，带 0 分标记可滤；正解 Phase 2 接 RSS/公告源）。
2. 筹码分布持续全源失败；主力资金流 fail-open 不稳定——买入信号被护栏系统性降级的幅度需在复盘中量化。
3. Bocha 免费 1000 次若为一次性总额，约 6-8 周耗尽——周检观察，耗尽前决定充值。
4. DSA 成本表因重试漏记系统性低估约 20%（免费额度下无实际影响）。
5. LLM 残留行为基线（Phase 2 三大纪律的靶子）：股吧帖被包装为"机构评级"、无媒体名/URL 溯源标注、旧闻当新利好。
