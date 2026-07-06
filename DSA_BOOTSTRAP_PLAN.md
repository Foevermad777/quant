# DSA 闭环启动执行计划（Phase 1 · 第一批里程碑）

版本：0.1
日期：2026-07-05
制定：Claude（计划与验收）
执行：Codex
监督：OpenClaw（环境与风控红线）
拍板：CEO

目标：在完全隔离的环境里把 `daily_stock_analysis`（下称 DSA）原样跑通"沪深300 小池 → LLM 诊股 → 结构化信号入库 → 报告生成"的最小闭环，并完成 PRD 第 3 节的 API 排雷预检。**本阶段一行 DSA 源码都不改，纯配置运行。**

---

## 0. 硬性红线（违反任何一条即停工上报）

1. **禁止污染全局环境**：所有 pip 安装必须发生在项目内 venv 里。禁止 `sudo pip`、禁止向系统 Python site-packages 写入任何包。执行前后各存一份全局 `pip3 list` 快照比对，必须完全一致。
2. **禁止修改 DSA 源码**：本阶段只允许改 `.env` 配置。发现"必须改代码才能跑"的情况，记录下来上报，不要动手改。
3. **默认不推送**：所有通知渠道保持关闭（不配置任何 webhook/token），直到报告质量经人工验收。
4. **不碰任何真实资金相关配置**：Longbridge 等券商相关配置一律留空。
5. **API key 只进 `.env`**，`.env` 与运行数据一律不进 git（见 M0 的 .gitignore 要求）。

---

## M0：手术台清洗（环境隔离与固定版本）

预计：0.5 小时。无外部依赖，可立即开工。

步骤：

1. 全局环境快照：`pip3 list > runtime_data/acceptance/global_pip_before.txt`（目录不存在先创建）。
2. 确认 Python 版本 ≥ 3.10：`python3 --version` 并记录。若低于 3.10，停下上报。
   **→ 2026-07-05 已触发**：实测系统仅有 Python 3.9.6，无 brew、无任何 3.10+ 解释器（Codex 上报，Claude 独立复核确认）。解除方案见下方"M0-fix"，已由 Claude 批准（比原 Homebrew 备选更轻量，CEO 保留否决权）。
3. 从 OpenClaw 已有的本地副本克隆（保证与调研快照同版本，不受上游高频更新影响）：
   ```bash
   git clone /Users/yongyuanbuanzhede/.openclaw/workspace/daily_stock_analysis vendor/daily_stock_analysis
   cd vendor/daily_stock_analysis && git rev-parse HEAD
   ```
   把 commit hash 记入 `runtime_data/acceptance/PINNED_COMMIT.txt`。此后升级 DSA 必须走"重新评估→重新验收"，不许静默 pull。
4. 在克隆目录内建 venv（**按 M0-fix 改用 uv**）：`~/.local/bin/uv venv .venv --python 3.11`，然后 `source .venv/bin/activate` 并确认 `python --version` 为 3.11.x。
5. ~~在本项目根目录创建 `.gitignore`~~ 已由 Claude 于 2026-07-05 提前完成（因密钥交接需要先有隔离区），Codex 核验其覆盖 `vendor/`、`runtime_data/`、`.env` 即可。

### M0-fix：Python 阻塞解除方案（Claude 已批准，2026-07-05）

原则：一切落在用户目录 `~/.local/` 内，不碰系统 Python、不需要 sudo、不修改 shell 配置文件。选 3.11 是因为 DSA 官方 Docker 镜像就是 python3.11。

```bash
# 1) 快照 shell 配置（证明安装器没动它）
cp ~/.zshrc runtime_data/acceptance/zshrc_before.txt 2>/dev/null || touch runtime_data/acceptance/zshrc_before.txt

# 2) 安装 uv 到 ~/.local/bin（--no-modify-path 禁止改 PATH/rc 文件）
curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --no-modify-path
~/.local/bin/uv --version

# 3) 复核 shell 配置零改动（有差异则记录并停工上报）
diff ~/.zshrc runtime_data/acceptance/zshrc_before.txt 2>/dev/null; echo "diff exit: $?"

# 4) 安装独立 Python 3.11（落在 ~/.local/share/uv/，与系统完全隔离）
~/.local/bin/uv python install 3.11
~/.local/bin/uv python list > runtime_data/acceptance/uv_python_list.txt
```

后续所有步骤中 uv 一律用绝对路径 `~/.local/bin/uv` 调用（我们故意不进 PATH）。

完整卸载路径（记入验收档案，供 CEO 随时行使"扔废纸篓"权利）：`rm -rf ~/.local/share/uv ~/.local/bin/uv ~/.local/bin/uvx`——执行后机器回到今天快照的原样。

M0-fix 追加验收项：
- [ ] `uv --version` 输出存档；
- [ ] `uv_python_list.txt` 存在且含 cpython-3.11；
- [ ] `~/.zshrc` before/after 零差异（或差异已记录上报）；
- [ ] venv 内 `python --version` 为 3.11.x。

验收标准（Claude 验）：
- [ ] `global_pip_before.txt` 存在；
- [ ] PINNED_COMMIT.txt 存在且与 OpenClaw 工作区 HEAD 一致；
- [ ] venv 内 `which python` 指向 `vendor/daily_stock_analysis/.venv/`；
- [ ] `.gitignore` 生效（`git status` 看不到 vendor 与 runtime_data）。

## M1：依赖安装与导入冒烟

预计：0.5-1 小时（下载时间为主）。

步骤：

1. 激活 venv 后执行 `~/.local/bin/uv pip install -r requirements.txt`（uv 装依赖显著更快；个别包失败可回退普通 `pip install` 单装）。已知注意点（来自调研）：
   - `alphasift` 是 git pin 安装且默认关闭——若安装失败，允许从 requirements 临时排除并记录（这不算改源码）；
   - `wkhtmltopdf` 是系统级依赖（MD 转图用），**本阶段不装**，涉及功能不验收；
   - `longbridge` 若安装失败同样可排除（我们不用券商网关）。
2. 冒烟：`python main.py --help` 正常输出；`python -c "from src.config import Config"` 不报错。
3. 全局环境复查：`pip3 list > runtime_data/acceptance/global_pip_after.txt`，与 before 比对必须零差异。

验收标准：
- [ ] `main.py --help` 输出存档；
- [ ] 安装期间被排除的包及原因有记录；
- [ ] before/after 全局快照 diff 为空（红线 1 的证据）。

## M2：最小配置

预计：0.5 小时。**CEO 已提供 Gemini 密钥：LLM 与新闻搜索均指定使用同一 Gemini key。实际密钥不得写入本文档，只能在执行 M2 时写入 `.env`。**

步骤：

1. `cp .env.example .env`，只配置以下最小集，其余全部保持默认或留空：
   - LLM：使用 Gemini。key 从 `runtime_data/secrets/gemini_api_key.txt` 读取（已由 Claude 存放，实测有效），写入 **`.env` 的 `GEMINI_API_KEY`**（`.env.example` 第 150 行的对应项）。**严禁写入 `.env.example`**——该文件在 vendor 克隆自己的 git 里，写进去等于把密钥提交进版本库；
   - `STOCK_LIST`：沪深300 试验小池 5 只（跨行业、高辨识度）：`600519`（贵州茅台）、`300750`（宁德时代）、`601318`（中国平安）、`600036`（招商银行）、`600900`（长江电力）；
   - 新闻搜索：使用 **Tavily**。key 从 `runtime_data/secrets/tavily_api_key.txt` 读取（已由 Claude 存放，实测有效），写入 `.env` 的 `TAVILY_API_KEYS`（注意是复数、支持逗号分隔，本次只填一个）。免费额度 1000 次/月，5 只股 × 最多 5 维搜索 ≈ 750 次/月，够用但没有余量——M3/M4 反复试跑时注意别浪费配额；（背景：Gemini 不能当搜索源，Claude 已核实 `search_service.py` 无 Gemini 接入点，CEO 已改为提供 Tavily key）
   - `TUSHARE_TOKEN`：有则配（会自动升为最高优先级数据源），没有就走免费链；
   - 通知渠道：**全部留空**（红线 3）；
   - 数据持久化：查 `.env.example` 里数据库/报告/日志路径类配置项（调研记录默认是 `./data/stock_analysis.db`），若支持自定义路径，指向本项目 `runtime_data/`，让历史数据独立于 vendor 目录存活；若不支持则接受默认并在验收记录里注明。
2. 跑 DSA 自带的配置校验（`python main.py --check-notify` 或 dry-run 方式，以 `--help` 实际提供的为准）。

验收标准：
- [ ] `.env` 存在且不在 git 内；
- [ ] 配置校验通过的输出存档；
- [ ] 通知渠道确认为空的证据（`.env` 相关段落截取，脱敏 key）。

## M3：PRD 排雷预检（对应 PRD 第 3 节 checklist）

预计：1-2 小时。产出物是一份 `runtime_data/acceptance/PREFLIGHT_REPORT.md`，逐项记录：

1. **行情数据源**：对 5 只试验股各拉一次日线 + 实时行情，记录：实际响应的数据源（结果里有 data_source 溯源字段）、耗时、失败与降级情况。跑两轮验证 SQLite 缓存生效（第二轮应命中 db_cache）。
2. **新闻舆情**（Tavily 已配置，本项必测）：对茅台做一次新闻搜索，记录三件事：① 返回条数与**中文财经内容的相关性**（Claude 2026-07-05 直连实测发现中文查询可能返回不相关英文结果，这是 Tavily 的已知短板，必须量化：5 只股各搜一次，统计相关条数占比）；② `published_date` 精度对比——Tavily 原始返回是秒级 GMT（已实测），但调研显示 DSA 入库时会归一化为天级，请核实 `news_intel` 表里实际存的是什么精度，这个"源头有秒级、入库剩天级"的差值直接决定我们数据溯源纪律的实现方案；③ 无日期新闻是否被 3 天时效过滤丢弃。
3. **A股特色数据**：主力资金流、筹码分布、龙虎榜各抽查一次，记录可用性（调研预期：fail-open 尽力而为）。北向资金已知废弃，不必测，在报告里注明即可。
4. **限流实测**：记录单股全流程数据获取耗时，据此推算 5 只/30 只/300 只的时间成本，写进报告。

验收标准：
- [x] PREFLIGHT_REPORT.md 覆盖上述 4 项，每项有实测数据（不是转述文档）；
- [x] PRD 第 3 节三个 checkbox 可据此打勾或明确标记阻塞原因。

### M3 验收备注（Claude，2026-07-05：通过，附两个跟进项）

1. **新闻相关性 0/5，严重不合格**：茅台查询经 DSA 过滤后的 5 条全是不相关英文新闻（油价、美股集体诉讼、加密货币）。时间戳字段本身合格（天级、无缺失），但内容废了。**不阻塞 M4 闭环验证，但必须在启动 14 天日跑积累之前解决**——否则两周的 LLM 分析建立在垃圾新闻上，"新闻解读+排雷"的验证目标作废。方案待 CEO 决策（见 CEO 待办 4）。
2. **主力资金流全链路 ConnectionError、筹码分布 unavailable**：影响不只是少个数据块——调研确认 analyzer 内有"无资金流数据则下调买入建议"的护栏，资金流长期缺失会系统性压低 buy 信号。M4-fix 落地后复测一次（注意区分是否代理所致）；若持续失败，列为已知数据缺口写进复盘口径。

## M4：首次端到端闭环（本计划的核心验收）

预计：1 小时 + LLM 调用等待。

步骤：

1. 单股试跑：`python main.py --stocks 600519 --no-notify`（若支持 dry-run 先 dry-run 一次）。
2. 检查四个落点：
   - 报告文件生成，人工可读；
   - SQLite 里 `analysis_history` 有本次记录;
   - `decision_signals` 表有自动抽取的信号行（action/confidence/entry/stop_loss/target_price/expires_at 非空情况如实记录——调研预期：invalidation 为空、confidence 是三档映射值，这是已知现状不算失败）；
   - `llm_usage` 表有 token 记录。
3. 全池试跑：5 只全跑一遍，记录总耗时与总 token。
4. 成本推算：按实测 token 写出"5 只/日"与假想"30 只/日"的月度 LLM 成本估算，进验收报告。

验收标准：
- [x] 5 只股票各有一份报告 + 一条 decision_signals 记录，证据为 SQLite 查询输出存档；
- [x] token/成本估算表存在；
- [x] 全程零通知外发、零全局环境污染、零源码修改（三条红线复查）。

**→ M4 验收结论（Claude，2026-07-05）：通过（有保留）。** 完整判定与六项保留事项（S1 信号口径矛盾 / S2 垃圾新闻硬编关联 / S3 SearXNG 全灭 / S4 成本低估 20% / S5 同股多 active 信号 / S6 跨表时区混用）见 `runtime_data/acceptance/M4_CLAUDE_ACCEPTANCE_VERDICT.md`。两项口径已裁定：复盘统计以护栏后 advice 层为准；时间以 UTC 为基准。**D+14 日跑启动的唯一阻塞项是新闻源（CEO 待办 4）。**

### M4-fix：Gemini 调用卡死解除方案（Claude 已诊断并批准，2026-07-05）

**根因**（Claude 实测定位）：本机访问 Google 只能走本地代理 `127.0.0.1:7890`（macOS 系统级代理开启中）。Claude 的 shell 继承了 `HTTPS_PROXY` 环境变量所以 curl 全通（generateContent 实测 3.7 秒返回）；Codex 的 Python 进程环境没有这组变量，LiteLLM 直连 Google 被黑洞——与"流式 5 分钟无首包、非流式永不返回、所有模型一致卡死"的现象完全吻合。key、配额、代理本身均正常。

**修复**（零代码，纯 .env）：在 DSA 的 `.env` 追加三行——config.py:1209 起会读取并传播到进程环境，litellm/httpx 自动走代理：

```
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
NO_PROXY=localhost,127.0.0.1
```

**执行顺序**：
1. 前置检查：`nc -z 127.0.0.1 7890` 确认代理在听（以后每次跑之前都查——**代理软件关掉 Gemini 必挂**，这条列入日常运行注意事项）；
2. 闭环验证统一改用 flash：`GEMINI_MODEL=gemini-3-flash-preview`（免费额度对 pro-preview 太紧，flash 快且足够验证闭环；模型质量对比是后话），`LLM_TIMEOUT_SEC=120` 保留；
3. 复跑 LiteLLM 冒烟（期望秒级返回 OK）→ 单股 600519 完整跑 → 查 M4 四落点 → 5 只全池 → 成本表；
4. 顺带复测资金流/筹码（M3 跟进项 2）：若加代理后国内数据源（腾讯/东财）反而变慢或失败，把相关域名加入 NO_PROXY 再测——先观察，不预先复杂化。

## M4.5：新闻源切换 Bocha（Claude 已验证 key，2026-07-06，Codex 执行）

背景：Tavily 中文财经相关性 0/5，对照实验证明是语料库缺口（英文查茅台同样垃圾、英文查苹果精准，见会话记录 2026-07-06）。Bocha 实测：茅台中文查询 **5/5 相关**、`datePublished` **秒级精度**、来源全为国内媒体（腾讯网/网易/证券之星）。CEO 已注册充值 ¥10 并领取 1000 次免费资源包，key 已验证并存于 `runtime_data/secrets/bocha_api_key.txt`。

代码事实：`search_service.py:2306` 起的 provider 装配顺序中 Bocha 位列第一（先于 Tavily），配置 `BOCHA_API_KEYS` 后自动成为主源、Tavily 自动降为备源，无需其他改动。

步骤：
1. `.env` 追加一行：`BOCHA_API_KEYS=<从 runtime_data/secrets/bocha_api_key.txt 读取>`（注意是复数键名）；
2. 单股验证跑（600519 完整分析一次）：核对 news_intel 新增行来源为 Bocha、标题中文且与茅台相关；日志中"最新消息/风险排查"维度 direct 命中数 > 0（M4 基线为 0）；
3. 留证：news_intel 查询输出与日志摘录存 `runtime_data/acceptance/m45_bocha_*`；
4. 配额纪律：免费包 1000 次 + ¥10 余额备用，验证跑控制在 1-2 次。

验收标准：
- [ ] news_intel 出现 Bocha 来源的中文相关新闻（≥3 条/股）；
- [ ] "机构分析/公司公告"两维度状态如实记录（SearXNG 仍会失败属已知；观察 Bocha 是否顶上）；
- [ ] 三条红线照旧零违反。

**→ 2026-07-06 首验未通过**（Codex 判断正确，不开绿灯是对的）。三个独立根因已由 Claude 调查工作流查实（细节见会话记录，全部有 file:line 实锤）：

1. **窗口错配，不是 Bocha 质量问题、也不是日期解析问题**：DSA 把 3 天档粗映射成 Bocha 的 `freshness='oneWeek'` 且每维度只取相关度前 6 条（search_service.py:915-931），本地却按 [今天-2, 今天+1] 硬过滤（:3413-3415）——本次 12 条返回全部落在 06-29~07-03（周一叠加周末新闻淡），全灭于 drop_old。日志 drop_unknown=0 + 07-04 样本被正确保留，证明解析与过滤逻辑无 bug。
2. **risk_check 落 SearXNG 是确定性轮转**：多维度搜索按维度 round-robin（:4085，非失败降级），SearXNG 因 `.env` 里 `SEARXNG_PUBLIC_INSTANCES_ENABLED=true` 无 key 也入池，而其公共实例发现依赖 searx.space（无代理直连超时）→ 排雷维度必败。
3. **代理离线属瞬时故障**：7890 当时未监听（现已恢复），且 NO_PROXY 不含搜索源域名，代理挂时殃及 Bocha/Tavily。

### M4.5-fix（Codex 执行，纯 .env 五行，零代码改动）

```
NEWS_STRATEGY_PROFILE=medium        # 与下一行取 min，缺一不可（config.py:317）
NEWS_MAX_AGE_DAYS=7                 # 过滤窗放宽到 7 天，与 Bocha oneWeek 请求窗对齐
SEARXNG_PUBLIC_INSTANCES_ENABLED=false   # 踢掉必败的 SearXNG，池变 [Bocha,Tavily]，risk_check 落回 Bocha
SCHEDULE_RUN_IMMEDIATELY=false      # 卫生项：防止哪天忘带 --no-run-immediately 造成计划外跑批
# NO_PROXY 追加：bocha.cn,bochaai.com,api.tavily.com（搜索层与代理解耦，代理挂了只影响 Gemini）
```

改后轮转分配：最新消息→Bocha、机构分析→Tavily、**风险排查→Bocha**、公司公告→Tavily、业绩→Bocha（公告维度走 Tavily 偏弱属已知残留，公告的正解是 Phase 2 接 RSS/公告源）。7 天窗的时效性代价接受：比"零新闻"好得多，且每条自带秒级时间戳，Phase 2 自建层再用精确日期区间收紧。

复验（先 `nc -z 127.0.0.1 7890` 确认代理在听）：单股 600519 完整跑，验收看四点：日志 `effective_window=7`；`[新闻过滤] ...Bocha:latest_news` kept>0；news_intel 新增 Bocha 行 ≥3；Gemini 闭环完成（analysis_history/decision_signals/llm_usage 各 +1）。留证 `m45fix_*`。

**→ M4.5-fix 复验通过（Claude 验收 2026-07-06）**：四验收点全过，risk_check 落回 Bocha 且抓到实质风险线索（禁酒令传闻），报告新闻引用全部逐字溯源、"硬编关联"显著减轻。带入积累期的残留（公告维度无效/筹码缺失/Bocha 配额周检）与完整证据见 `runtime_data/acceptance/M45FIX_M6_CLAUDE_VERDICT.md`。

## M5：Web 工作台可视化验证（可选，不阻塞验收）

`python main.py --serve` 起服务，浏览器确认：历史报告页、决策信号池页可见 M4 产生的数据。截图存档即可。

## M6：定时调度方案（Claude 裁定 2026-07-06：不用长驻 --schedule，改 launchd 单次）

Codex 提案（`main.py --schedule` 长驻）经调查判定**在这台 Mac 上可用但不合格**，关键事实：`pmset` 实测本机空闲约 1 分钟即睡眠，18:00 时进程大概率被冻结；schedule 库唤醒后补跑会撞上"按触发当天判交易日"的闸（周五错过→周六补跑→判休市→当天数据永久丢失），还可能当天双跑污染样本；长驻进程崩溃后无人拉起；代理检查只在启动一刻做一次。

采纳方案（不改 DSA 源码；launchd plist 属用户目录级配置，Claude 批准，CEO 有否决权）：

1. **Codex 建三个文件**：
   - `ops/run_dsa_daily.sh`（wrapper）：①`nc -z 127.0.0.1 7890` 失败则记日志退出；②`exec caffeinate -i .venv/bin/python main.py`（单次全量模式，跑完即退，caffeinate 防中途睡眠）；
   - `~/Library/LaunchAgents/com.quant.dsa.daily.plist`：StartCalendarInterval 周一至五 17:58，RunAtLoad=false，日志重定向 runtime_data/logs/；launchd 对睡眠中错过的触发会在唤醒时合并补跑一次，天然优于长驻轮询；
   - `ops/healthcheck.sh`（纯只读晨检）：昨日 report 文件存在 / 三表昨日增量 ≈5 只量级 / 日志 ERROR 计数=0 / 当天代理预检。
2. **CEO 两件事**：①工作日傍晚让 Mac 插电（合盖+电池下定时唤醒不保证撑完全量分析）；②亲自执行一次 `sudo pmset repeat wakeorpoweron MTWRF 17:57:00`（需管理员密码，系统级变更必须你本人做；撤销命令 `sudo pmset repeat cancel`）。
3. **D+14 计时**：以第一次调度成功运行日为 Day 1；若 2026-07-06 18:00 首跑成功，阈值为 2026-07-20，第一轮复盘会 2026-07-21（Codex 的推算正确）。14 个自然日 ≈ 10 个交易日样本，第一轮复盘按此口径预期。

**→ M6 首验未通过（Claude 验收 2026-07-06）**：三件套内容质量合格（plist 语法/要素齐全、wrapper 在 launchd 极简 PATH 下逐命令可解析、单次跑语义确认），但 **plist 从未 bootstrap 进 launchd——不返工今晚 17:58 不会触发**。返工三项（见 M45FIX_M6_CLAUDE_VERDICT.md）：① `launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.quant.dsa.daily.plist` 并 print 复核留证；② ops/ 两脚本从 vendor 内部迁到项目根 `ops/`（vendor 重克隆会连带丢失自研脚本），plist 路径同步更新；③ 晨检脚本周一需传上周五日期（默认查"昨天"会撞周日）。

**→ M6-fix 返工验收通过（Claude 独立复核，2026-07-06 12:00）**：① launchctl 服务已加载（print 显示 state=not running 待 17:58 触发，program 指向项目根新路径）；② ops/ 已迁项目根、vendor 内已清，脚本内目录全部为硬编码绝对路径，搬迁零断链；③ 晨检周一自动取上周五（date -v-3d）已实证。**今晚 17:58 为 Day 1 首跑窗口，Claude 将在 18:15 后自动巡检首跑结果。** 守则重申：插电、开盖、锁屏随意、代理软件保持运行。CEO 两项：pmset 定时唤醒尚未设置（定位=合盖/电池场景的兜底保险，非当晚必需）；睡眠行为已实测修正（2026-07-06）——powerd 在屏幕亮着时持有防睡眠断言（"Prevent sleep while display is on"），**插电+开盖即不会系统睡眠，锁屏无妨**；合盖或电池供电会深睡（当日晨 09:14-09:45 电池状态反复深睡有 pmset 日志为证）。今晚守则：插电、开盖、锁屏随意。

**→ M6-fix 返工通过（Codex 执行 2026-07-06）**：已执行 `launchctl bootstrap gui/501 /Users/yongyuanbuanzhede/Library/LaunchAgents/com.quant.dsa.daily.plist` 并用 `launchctl print`/`list` 留证；`ops/run_dsa_daily.sh` 与 `ops/healthcheck.sh` 已迁到项目根 `ops/`，vendor 内 `ops/` 已删除，plist 路径同步为项目根脚本；晨检无参数时周一默认查上周五（2026-07-06 周一实证默认日期 2026-07-03）。证据见 `runtime_data/acceptance/M6_FIX_ACCEPTANCE_VERDICT.md` 与 `m6fix_*`。

## 暂不验收、挂起到 D+14 的事项

- 信号后验评估（outcomes）与报告级回测：DSA 要求信号/报告存量满 14 天才有意义（min_age_days=14）。**从首次调度成功日起连续日跑积累两周**后，再由我出下一份验收单。
- 后验评估无自动定时任务（需手动 POST /outcomes/run），D+14 验收时一并处理。

---

## CEO 待办（M2 的前置条件，M0/M1 不等这个）

1. ~~提供一个 LLM API key，并说明用哪家（影响成本估算口径）；~~ ✅ 已解决：Gemini key 已验证有效并存放于 `runtime_data/secrets/gemini_api_key.txt`（600 权限，git 之外），Codex 从该文件读取。
2. ~~提供新闻搜索 key~~ ✅ 已解决（2026-07-05）：CEO 已提供 Tavily key，验证有效并存放于 `runtime_data/secrets/tavily_api_key.txt`。若 M3 实测中文相关性太差，再评估升级 Bocha（付费、中文财经更友好）。
3. 决定是否注册 Tushare（免费档即可起步，不注册也能跑）。
4. ~~新闻源升级决策~~ ✅ 已解决（2026-07-06）：CEO 已注册 Bocha 并提供 key，Claude 实测通过（茅台 5/5 相关、秒级时间戳），切换步骤见 M4.5。
5. **日常运行须知**：跑 DSA 时本机代理软件（7890 端口那个）必须开着，否则 Gemini 调用会卡死。

## 分工确认

- **Codex**：按本文档顺序执行 M0→M4，所有证据存 `runtime_data/acceptance/`，遇红线冲突或计划外情况停工记录，不自行变通。
- **Claude（我）**：按各里程碑验收标准逐条核验证据，出验收结论；验收不过给出返工点。
- **OpenClaw**：对 M0/M1 的环境隔离做红队抽查；对 M3 预检报告的金融口径把关。
- **CEO**：提供密钥；对 M4 报告做"人话质检"——系统对茅台的判断你觉得靠不靠谱，这是任何自动验收都替代不了的一环。
