# 项目迁移计划：~/Documents/量化系统 → ~/quant（解除 macOS TCC 阻塞）

版本：1.0
日期：2026-07-06
制定：Claude｜执行：Codex｜拍板：CEO（已确认迁往 `~/quant`）
背景：launchd 定时任务因 macOS TCC 保护无法访问 `~/Documents`（今晚 DSA 排程 exit 126，err 日志 `getcwd: Operation not permitted`）。迁到不受 TCC 保护的 `~/quant` 根治。

## 0. 目标路径

- 旧根：`/Users/yongyuanbuanzhede/Documents/量化系统`
- 新根：`/Users/yongyuanbuanzhede/quant`（CEO 已手动创建空目录）
- 迁移后全部工作、验收、git、排程均以新根为准。

## 1. 红线（迁移专属）

1. **runtime_data/ 必须完整迁移、零丢失**——内含真实数据：`runtime_data/dsa/stock_analysis.db`（400 日线/7 信号/37 新闻）、`runtime_data/quant/paper.db`（模拟盘台账）、`runtime_data/secrets/*.txt`（**三把 API 密钥**）、`runtime_data/acceptance/`（全部验收证据）、`runtime_data/logs/`。迁移前后对这些文件做 md5 或行数比对，证明无损。
2. **vendor/daily_stock_analysis/.env 必须完整迁移**——内含 DSA 的密钥与代理配置。
3. **两个 .venv 不迁移、就地重建**（venv 内绝对路径焊死，移动即坏）。
4. git 历史（.git，两个 commit 9e412d1/e65f4f1）必须完整保留。
5. 迁移期间不改任何业务逻辑，只改路径与重建环境。

## 2. 执行步骤

### 2.1 迁移文件（保留 dotfiles，跳过 venv）

将旧根全部内容迁入 `~/quant/`，**包含**隐藏文件（`.git`/`.gitignore`），**排除**两个 `.venv` 目录（下一步重建）。建议用能保留权限和隐藏文件的方式（如 `rsync -a --exclude='.venv' --exclude='vendor/daily_stock_analysis/.venv' 旧根/ ~/quant/`）。完成后：
- `cd ~/quant && git log --oneline`：应见 9e412d1/e65f4f1 两 commit；
- runtime_data 三个关键库/密钥 md5 与迁移前一致（留证 `M_RELOC_*`）；
- 确认旧根可删除（**先不删**，验收通过后再由 CEO 删）。

### 2.2 重建两个 venv（uv，Python 3.11）

1. 项目根执行器 venv：`cd ~/quant && ~/.local/bin/uv venv .venv --python 3.11`；执行器仅需标准库（config.py 已确认无第三方依赖）——若 `python -m executor.engine --help` 或单测能跑即可，缺包再装。
2. DSA venv：`cd ~/quant/vendor/daily_stock_analysis && ~/.local/bin/uv venv .venv --python 3.11 && ~/.local/bin/uv pip install -r requirements.txt`（照 M1：alphasift/longbridge 装失败可排除并记录）。
3. 全局 pip 快照前后比对照旧为空（红线继承）。

### 2.3 改写硬编码路径（共 7 处，逐一 grep 验证改净）

将下列文件中的 `/Users/yongyuanbuanzhede/Documents/量化系统` 全部替换为 `/Users/yongyuanbuanzhede/quant`：
1. `~/quant/ops/run_dsa_daily.sh`（PROJECT_DIR/DSA_DIR/LOG_DIR/PYTHON_BIN）
2. `~/quant/ops/run_executor_daily.sh`（PROJECT_DIR/PYTHON_BIN）
3. `~/quant/ops/healthcheck.sh`（PROJECT_DIR/DSA_DIR/DB_PATH/LOG_DIR/REPORT_DIR）
4. `~/quant/ops/com.quant.executor.daily.plist`（repo 副本；若无 dsa 的 repo 副本一并补齐，保持与系统副本一致）
5. `~/quant/vendor/daily_stock_analysis/.env`（`DATABASE_PATH`、`LOG_DIR` 及任何绝对路径——grep 旧根字符串确保改净，密钥/代理行不动）
6. `~/Library/LaunchAgents/com.quant.dsa.daily.plist`（ProgramArguments/StandardOut/ErrPath/WorkingDirectory）
7. `~/Library/LaunchAgents/com.quant.executor.daily.plist`（同上）

改完 `grep -rn "Documents/量化系统" ~/quant ~/Library/LaunchAgents/com.quant.*.plist`（排除 acceptance 历史文档里的叙述性引用）应为空。
注：`executor/config.py`、`ops/weekly_review.py` 用 `__file__` 派生路径，**无需改**，勿画蛇添足。

### 2.4 重载 launchd

```
launchctl bootout gui/501/com.quant.dsa.daily 2>/dev/null
launchctl bootout gui/501/com.quant.executor.daily 2>/dev/null
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.quant.dsa.daily.plist
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.quant.executor.daily.plist
launchctl print gui/501/com.quant.dsa.daily     # 确认 program 指向 ~/quant
launchctl print gui/501/com.quant.executor.daily
```

### 2.5 冒烟验证（TCC 是否解除的直接证据）

**手动触发一次 DSA 排程任务**（不等明天）验证 TCC 已解除、且顺带回答悬而未决的 R1：
```
launchctl kickstart -k gui/501/com.quant.dsa.daily
```
观察：① `launchctl print` 的 `last exit code` 应为 0（不再是 126）；② `runtime_data/logs/dsa_daily_launchd.err.log` 无 `Operation not permitted`；③ **查 stock_daily 是否新增 2026-07-06 当日日线**——这正是 R1 悬而未决的"DSA 盘后是否落当日 bar"的实证（现已过 15:00 收盘，理应能取到）。

## 3. 验收标准（Claude 验）

- [ ] runtime_data 三库/密钥迁移前后 md5 一致（零丢失铁证）；
- [ ] git log 两 commit 完好，git status 仅 ignored；
- [ ] 两 venv 重建，executor 单测 19 项全过、DSA `main.py --help` 正常；
- [ ] 7 处路径改净，全项目 grep 旧根为空；
- [ ] launchd 两任务重载、program 指向 ~/quant；
- [ ] **kickstart DSA 任务 exit code=0**（TCC 解除铁证），并记录 stock_daily 是否落 07-06 bar（R1 实证结论）；
- [ ] 红线复查：全局 pip 零污染、DSA 库只读未变、无密钥入 git。

## 4. 迁移后待办（承接，不在本次）

1. R1 结论落定后，据实决定执行器排程成交日语义是否需调整；
2. R2（sell/reduce/avoid 平仓）、R3（沪深300 基线 + 计费对齐）、R4（归因顺序/apply_trade 测试/ST/NULL）整改；
3. 执行器 backfill 从 07-06 起重跑，模拟盘对齐；
4. 本文档与全部 .md 已随迁移进入 ~/quant；PROJECT_LOG 更新新根路径。
5. CEO 确认新根一切正常后，删除旧根 `~/Documents/量化系统`。
