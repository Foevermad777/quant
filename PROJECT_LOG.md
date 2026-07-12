# 项目进度日志（新会话从这里开始读）

> **本文档用途**：任何新开的 AI 会话（Claude/Codex/OpenClaw）或未来的协作者，读完本文件即可掌握项目全貌与当前状态。
> **维护约定**：每完成一个里程碑、每做一个重大决策，由验收方（Claude）当天追加时间线条目并更新"当前状态"节。其他文档是专题深度材料，本文件是索引与状态权威源。

## 一、当前状态快照（更新于 2026-07-12）

- **项目根已迁移**：`~/quant`（原 `~/Documents/量化系统` 因 macOS TCC 阻塞 launchd 而废弃，待 CEO 确认后删）。所有路径、排程、git、验收均以 `~/quant` 为准。
- **阶段**：Phase 1 MVP。M0-M6 已验收；M7（执行器）/M8（周复盘）已实现并"有条件通过"（R1 已解决，R2/R3/R4 待整改）；迁移已验收通过。
- **自动化链已通**：迁移后 kickstart 完成首次完整端到端跑（DSA 抓数+Bocha 新闻+诊股+信号入库+执行器处理），launchd 两任务 exit 0。14 天基线积累底座就绪。
- **返工已验收通过**（`runtime_data/acceptance/REWORK_CLAUDE_VERDICT.md`）：P0/R2/R3/R4/D1/D2 全部完成（28 测试过），Tushare 已注册+Claude 接入 DSA .env（token 实测有效且能取到免费源缺的茅台/宁德 07-06 日线）。**剩收尾三步（交 Codex）**：①kickstart DSA 验证 Tushare 生效、07-06 补 5/5；②重跑 D1 确认无 remaining；③执行器 `--backfill-from 2026-07-06` 对齐台账。
- **OpenClaw 红队已响应**（`runtime_data/acceptance/RED_TEAM_RESPONSE_20260706.md`，技术结论经代码核查）：接受 Q1 成交偏差(改次日开盘价+双倍滑点，代码已证实 entry_high 幸存者偏差)、Q2 复盘改定性打脸、警告2 防未来函数护栏、警告3 OOS 红线；纪律注入找到不踩红线的两步解法(Path A 配置级软注入清空 bull-only 基线本周可做 / Path B 我方 executor 包裹层做 guardrail 强制)。**待 CEO 拍板 2 项**：①Q3 Phase2 标的池是否改中证500/1000(验证期留沪深300)；②是否本周优先做 Path A 纪律注入+Q1 成交改造。当前 14 天运行**重定义为工程压测**，信号质量基线待 Path A+Q1 落地后起算。
- **G1-G4 已实现并验收通过**（`runtime_data/acceptance/G1_G4_CLAUDE_VERDICT.md`）：G1 成交改 next_open+开仓双滑点(旧模型留A/B)、G2 三大纪律注入(Claude 金丝雀验证:bull-only 基线真被清空、纪律真进 prompt，零API花费)、G3 防未来函数护栏、G4 包裹层硬门控。**待收尾**：Codex 提交 G1-G4(全未 commit)；G2 的 LLM 合规性与 G1 真实偏差幅度待第一次真实基线跑观察(建议即作 Day 1)。
- **美股平行赛道已开工**（`US_TRACK_CHECKLIST.md` U0-U9，CEO 拍板完整 fork 强物理隔离 07-07）：`executor/us/` 复制全 7 件、零共享代码；股池 AAPL/NVDA/MSFT/JPM/SPCX、$1M、Tavily、北京清晨调度、SPY基线。与 A 股日常积累并行。**U0-U6 已验收通过**（`US_U6_CLAUDE_VERDICT.md`）：fork 隔离硬证据全成立(A股六件零改/executor us 零耦合/paper.db md5 不变/回归48+11绿)；U6 实盘由 Claude 补跑(Codex 沙箱阻塞外发)——5只美股 DSA 分析+yfinance免key抓日线+G5补全全过G4入 paper_us.db,**含2只真buy(AAPL/JPM)**。偏差:US新闻走了Bocha非Tavily(待修路由)。**Codex 剩余 U7(US执行器+清晨launchd)/U8(SPY周复盘)/Tavily路由修**;CEO:pmset 唤醒扩到清晨。G5 模型已定 gemini-3.5-flash(b5bbd22)。
- **【关键转折 2026-07-07】软注入被证明不足以产出纪律化输出**（`DAY1_PROBE_CLAUDE_VERDICT.md`）：单股 600519 探雷+Claude 独立复验——G2 注入进了 prompt，但 LLM 真实输出**无三情景、无 invalid_conditions**（根因：DSA 硬编码 JSON schema 无这些槽位，改不了）。Codex 正确判定"不开 Day 1"。**计划修订**：拿纪律化 Day 1 必须建**新工程项 G5 纪律补全包裹层**（我方 executor 一次补全 LLM 调用，生成三情景/失效条件/溯源，再经 G4 验证；自研诊股层雏形，红线内）。CEO 已选**最小补全**，规格 `G5_DISCIPLINE_COMPLETION_SPEC.md`：executor 层直连 Gemini(REST+responseSchema 结构化强制) 把 DSA 输出补出三情景/结构化失效条件/带日期溯源 → 写我方 disciplined_signals 表 → G4 校验(对照组:DSA原始必被拒/G5补全应放行) → 执行器改读此表。**G5 已实现并经 Claude 真实数据验收通过**（`G5_CLAUDE_VERDICT.md`）：真跑 600519 对照组成立(原始 DSA→G4拒/G5补全→G4放行)，产出真三情景+结构化失效条件+带真实日期溯源，并**当场抓出 DSA 看多偏见下调置信度**(single_side_flag)。注意：Codex 只交了代码+mock单测、未做真实跑(Claude 补验)、且未提交——流程缺口已记。G5 已提交(6abac71)+全池5只补全并 Claude 验收通过(全过G4/对照组成立/溯源真实/纪律层有区分度只降茅台看多偏见)。执行器已接线读 disciplined_signals。**信号质量 Day 1 数据=2026-07-07**(今日5只手动补全)。**Day 2+ 自动化前 Codex 必须补两根线**(见 G5_CLAUDE_VERDICT.md)：①把 `discipline_completion --all-active` 插进每日流程(DSA后、执行器前)；②超时鲁棒性(单只重试,一只超时不拖垮整批)。之后 D+14 定性复盘、再开美股赛道。当前全为 watch/hold 无 buy,短期无纸面交易属正常。
- **DSA 日跑结构已统一到共享大盘上下文 + 单股隔离**（2026-07-12）：A 股、美股每日各自先生成/复用一次市场级上下文，再让 5 只股票分别在独立进程内复用同一份上下文。单股超时/失败不拖垮整批；脚本 exit 0 但业务 0/5 成功会升格为可告警 exit 70。已实测 CN/US 共享上下文各一份、600519/AAPL 单股复用成功；launchd 仍调用原 wrapper 路径，下一次自动调度直接走新结构。
- **【失败率专项复盘已完成 + P0 前三条已落地 2026-07-12】**（正式复盘文档：`runtime_data/acceptance/DSA_FAILURE_POSTMORTEM_20260712.md`，多智能体双镜头对抗验证）：窗口内真实丢失的 17 个股票-日中 **0% 是上游 API 独立故障**——~70% 为本地代理出口事故（07-09 05:15 SSL EOF → Gemini 地区封锁 400，至 07-10 10:10 代理重启才恢复），~30% 为机器睡眠（07-11 US 晨跑）；东财闪断/筹码分布/tushare 限频等日志噪音全被 failover 吸收、零损失（Codex"API 总出错"只解释噪音不解释丢失）。三大结构缺陷已修复：①LLM 假降级（fallback=主模型自身、CN 无 DeepSeek、G5 DeepSeek 走代理陪葬）→ CN wrapper 接入 DeepSeek 降级 + `.env` fallback 改 gemini-3-flash-preview + G5 DeepSeek 强制直连；②`nc -z` 探活探不出出口地理 → CN 接入 provider preflight（`--region cn`，真实 Gemini 探测可识别 region_unsupported），代理端口挂不再 exit 75 丢全天，Gemini 不可用自动切 deepseek-chat 当日主模型；③沉默失败（0/5 报 ok、backfill 只看 stock_daily、无告警、恢复全靠人肉）→ 新增 `ops/verify_dsa_analysis.py` 以 analysis_history 为最终成功判定（CN/US wrapper 跑批后核对，缺口自动延迟 600s 重试一轮，仍缺则 exit 70 + macOS 通知 + `dsa_alerts.log`；校验层自身异常 exit 72），backfill 巡检增加 analysis 维度（往日缺口 exit 71 + 通知；当日让位给 wrapper 重试避免竞态）。验收：ops 19 + executor 65 + executor/us 37 + dashboard 2 全绿；真实 preflight（Gemini 2.6s/DeepSeek 0.6s）与真实 DB 校验（US 5/5→0、CN 缺 4→exit 3）实测通过；真实巡检准确复现 07-09 全池分析缺口。改动经 23-agent 对抗审查（16 项确认问题全部修复，含契约测试回归）。
- **P0-4 晨跑定时唤醒已设（2026-07-12，CEO 执行）**：`sudo pmset repeat wakeorpoweron TWRFS 05:05:00` 已生效（`pmset -g sched` 确认 `wakepoweron at 5:05AM Some days`），覆盖美股周二~六 05:10 晨跑——07-11 丢失的正是这个无人值守窗口。**约束**：macOS `pmset repeat` 全局只能存一条 wake 规则（man page 明确），故无法用第二条 repeat 覆盖 A股窗口。**A股傍晚 17:58 窗口仍走"插电+开盖"人工纪律**（审计窗口内 A股从未因睡眠丢过，傍晚机器通常在用，风险低一档）。如需 A股窗口也上硬保险=方案 B（自调度 daemon 每日用 `pmset schedule wake` 排次日两窗口一次性唤醒 + 给 `/usr/bin/pmset` 加一行 NOPASSWD sudoers），待 CEO 决定是否做。
- **CEO 待办**：确认新根稳定一天后删旧根；决定是否上 P0-4 方案 B（A股窗口硬唤醒）。
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
| 美股赛道架构 | **完整 fork 强物理隔离**（CEO 拍板 07-07）：`executor/us/` 复制全部 7 件、运行期与 A 股零共享代码；唯二共享=DSA只读库 + G5(靠 --store-db paper_us.db 输出隔离)。代价(双引擎独立演化、bug修复需手工移植)明确接受。股池 AAPL/NVDA/MSFT/JPM/SPCX、$1M、Tavily、北京清晨调度、SPY基线 | US_TRACK_CHECKLIST.md |
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

**2026-07-12（日）**
- DSA 失败率专项排查后，统一 A 股/美股日跑结构：每天每个市场只生成一次大盘上下文，5 只股票走单股进程隔离并共享该上下文。核心变更在 `ops/prepare_dsa_market_context.py`、`ops/run_dsa_daily.sh`、`ops/run_us_dsa_daily.sh`，以及 vendor DSA CLI/pipeline/context service。
- 大盘上下文新增精确 `query_id` 复用、lock 竞争校验、闭市跳过、状态 JSON、hash 审计与失败 exit 68；个股进程新增 `--reuse-market-context --market-context-query-id`，若找不到精确上下文会在个股分析前失败，避免悄悄重新生成或串错市场。
- 美股 wrapper 保留原有 provider preflight/DeepSeek 降级路径，同时新增单股超时/失败隔离；A 股 wrapper 同步改成同一结构。两边都将“脚本技术 exit 0 但业务 0/5 成功”标成告警态 exit 70。
- 真实验证结果：CN 生成/复用 `shared_market_cn_cn_live_20260712`，history_id 72，有效交易日 2026-07-10；US 生成/复用 `shared_market_us_us_live_20260712`，history_id 73，有效交易日 2026-07-10。600519 复用 CN 上下文成功入库 id74；AAPL 复用 US 上下文成功入库 id75。
- 自动化/测试验证：父仓 37 个 unittest 通过；vendor 受影响 unittest 153 个通过；context 相关直接测试 47 个通过；`bash -n`、`py_compile`、`git diff --check`、secret scan 均通过。用户提供的 DeepSeek key 未入库。
- 提交：父仓 `fe24639 Unify daily DSA market context flow`；vendor 子仓 `8e5ea0c7 Share one market context across isolated stocks`。vendor 中 `docs/CHANGELOG.md`、`src/search_service.py`、`tests/test_search_tavily_provider.py` 为既有未提交改动，本次未触碰。
- **失败率专项复盘（Claude 多智能体取证，晚间）**：六路取证 + 8 假设 × 双镜头对抗验证（7 确认 1 推翻），结论与全量验证记录见 `runtime_data/acceptance/DSA_FAILURE_POSTMORTEM_20260712.md`。核心裁定：丢失的股票-日 0% 归因上游 API 独立故障；头号根因为代理出口事故（H1），放大器为 LLM 假降级（H2）、沉默失败编排（H3）、无定时唤醒（H4）；被推翻假设：进程隔离放大 tushare 限频（实际超限发生在未隔离的旧 CN 批处理内）。
- **P0 前三条当日落地**（Claude 实现并验收）：①CN 主管道 DeepSeek 跨 provider 降级 + `.env` 假 fallback 修正 + NO_PROXY 补 api.deepseek.com + G5 DeepSeek 直连（07-09 它曾随代理陪葬）；②`ops/us_dsa_preflight.py` 增 `--region cn`，CN wrapper 代理挂/Gemini 地区封锁时自动降级 DeepSeek 续跑而非丢全天；③新增 `ops/verify_dsa_analysis.py`，CN/US 跑批后以 analysis_history 为最终判定，缺口延迟 600s 自动重试一轮 + macOS 通知 + `dsa_alerts.log`（缺口 exit 70 / 校验层异常 exit 72），backfill 巡检补 analysis 维度（往日缺口 exit 71 + 通知，当日让位 wrapper）。全部改动经 23-agent 对抗审查修复 16 项确认问题（含 verify 退出码吞没、状态文件与退出码不一致、契约测试回归、osascript 注入、CN 假期误报、18:40 巡检与重试窗竞态、report_type NULL 语义）。测试：ops 19 / executor 65 / executor/us 37 / dashboard 2 全绿；真实 preflight、真实 DB 校验、真实巡检（准确复现 07-09 缺口）实测通过。**P0-4（pmset 定时唤醒）待 CEO 执行**，命令见"当前状态快照"。

## 五、已知问题与观察项（复盘会逐条核对）

1. 公告维度实质无效（轮转到 Tavily 的英文噪音，带 0 分标记可滤；正解 Phase 2 接 RSS/公告源）。
2. 筹码分布持续全源失败；主力资金流 fail-open 不稳定——买入信号被护栏系统性降级的幅度需在复盘中量化。
3. Bocha 免费 1000 次若为一次性总额，约 6-8 周耗尽——周检观察，耗尽前决定充值。
4. DSA 成本表因重试漏记系统性低估约 20%（免费额度下无实际影响）。
5. LLM 残留行为基线（Phase 2 三大纪律的靶子）：股吧帖被包装为"机构评级"、无媒体名/URL 溯源标注、旧闻当新利好。
