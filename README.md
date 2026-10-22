# 足球胜负彩分析系统 MVP

一个参考 [lai1tony/football_analysis](https://github.com/lai1tony/football_analysis) 实现逻辑的轻量版足球分析系统。核心能力：

1. 自动解析胜负彩最新开售期并采集对赛列表，也可用 `--issue` 指定历史期，例如 `26120`。
2. 用竞彩列表 + `odds.xml` 补全完整队名、亚盘让球、大小盘和欧赔/市场概率。
3. 用 liansai 球队页采集双方最近 10 场往绩，拆分主客场、盘路和大小球倾向。
4. 用球探/Titan 赛程页把胜负彩场次映射到比赛 ID，再取双方历史交锋 H2H（主源 `zq.titan007.com`，镜像兜底 `m.nowscore.com`）。
5. 用独立泊松预期进球模型估算主胜/平/客胜概率，再向市场去水概率做收缩融合。
6. 对欧赔、亚盘让球、大小盘做独立的盘口/水位分析，融合模型比分覆盖概率后给出主推/轻仓/观望。
7. 跟踪 Nowscore 赛前指数中的亚洲盘让球初盘与即时盘，识别升盘/降盘、水位反转和大幅水位摆动，并在页面给出“变盘异动提醒”。
8. 输出 JSON / Markdown / CSV / HTML 报告，浏览器页面首屏提供变盘异动提醒、按信心排序的整期推荐汇总和全场比赛明细。
9. 把每期亚洲盘让球异动写入本地 SQLite，并提供命令行/浏览器查询。

## 快速运行

```powershell
python -m pip install -r requirements.txt
python run.py --cache-dir cache
```

不指定 `--issue` 时自动抓取 `trade.500.com` 当前最新一期；需要复测指定期号时才显式传：

```powershell
python run.py --issue 26120 --cache-dir cache
```

自动模式下，当期列表、竞彩列表和 `odds.xml` 不使用缓存，确保不会因上次运行缓存而停留在旧一期；球队往绩、H2H 和变盘页面仍会按缓存键复用。

常用参数：

```text
--issue 26120      指定胜负彩期号；缺省自动抓取最新一期
--output-dir reports
--cache-dir cache  保存原始页面，重跑不重复抓取
--db-path data/football_analysis.sqlite
--workers 4        球队页并发数
--no-cache         不使用缓存
--no-db            本次不写入本地异动库
--narrate          为变盘异动场次调用大模型生成解读（需 LLM_API_KEY）
--scout            对观望场次联网检索伤停/战意等软信息（需 LLM_API_KEY）
```

加 `--scout` 会对「观望」场次联网检索伤停、停赛、战意、赛程等软信息，单独渲染成「软信息补充」区块并标注是否与市场方向一致；检索依赖服务商的 `enable_search` 能力（DeepSeek 等支持），接口不认时设 `LLM_SEARCH=0` 关掉。软信息未经人工核实，不参与概率计算，也不改变任何推荐。

启用大模型解读（可选，需 OpenAI 兼容接口的 API Key）：

```powershell
$env:LLM_API_KEY = "sk-..."
python run.py --cache-dir cache --narrate
```

默认使用 `gpt-4o-mini`；用 DeepSeek 等兼容服务商时设置 `LLM_BASE_URL` 与 `LLM_MODEL`。无 Key 或请求失败会静默跳过，报告其余部分不受影响。解读只引用已采集的近况、当前对手的过去对战、盘口与模型数据，不产生新的投注建议。

查询已入库异动：

```powershell
python run.py query --attention-only
python run.py query --issue 26121 --team 巴萨
python run.py query --serve
python run.py cache --clean --keep 3
python run.py backup
python run.py backup --restore --file data/backups/backup_YYYYMMDD_HHMMSS.zip
```

`cache --clean` 会删除旧的 `.raw` 页面缓存和过期断点文件，并按期号保留最新 N 期

`backup` 默认将 SQLite 与 AI 接入点设置写入 `data/backups/`。恢复前会自动生成
`backup_before_restore_*.zip` 安全副本，并先校验 SQLite 完整性；缓存和报告属于可再生文件，
不进入备份包。查询页“设置”窗口也提供下载与恢复备份。
`odds_pages_*.json` 盘口产物；可加 `--dry-run` 先预览，不会删除数据库。

运行后会在 `reports/` 生成：

- `issue_<期号>_*.md`：人读报告
- `issue_<期号>_*.csv`：推荐表
- `issue_<期号>_*.json`：完整结构化数据，含往绩和模型中间结果
- `issue_<期号>_*.html`：可直接用浏览器打开的看板，含“变盘异动提醒”“整期推荐”“全场比赛明细”

## 独立版 exe

已打包的 Windows 单文件程序位于 `dist/football_analysis.exe`，不依赖本机 Python。放到任意可写目录后双击运行，程序会抓取最新一期、入库，并在同目录的 `cache/` 保存可复用抓取缓存、在 `reports/` 生成报告，随后起动异动查询服务并打开浏览器（关闭窗口或 Ctrl+C 停止）。

命令行缺省自动抓取最新一期；也可指定缓存目录或历史期号：

```powershell
.\dist\football_analysis.exe              # 默认：抓最新一期 → 入库 → 起异动查询服务 → 打开查询页

.\dist\football_analysis.exe analyze --cache-dir .\cache --no-open     # 只分析，不起服务、不开浏览器
```

重新打包：

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm "足球胜负彩分析工具.spec"
python -m PyInstaller --noconfirm "足球更新器.spec"
```

完整包（exe + 更新器 + data/cache/reports）用 `python make_package.py` 生成到 `dist/足彩分析工具_完整包_<日期>.zip`。

## 自动升级（GitHub Releases）

发布源是 [jackybee6933-code/zucaikan](https://github.com/jackybee6933-code/zucaikan) 的 Releases：打 `v*` tag 推送后，
[.github/workflows/release.yml](.github/workflows/release.yml) 在 Windows runner 上构建主程序与
`updater.exe`，把「主程序 + 更新器」合成 `dist/football_analysis_full.zip`，连同
`dist/latest.json`（版本 / 下载地址 / SHA-256）一起发布为 Release 资产。
Release 资产统一用 ASCII 文件名（`football_analysis.exe` / `updater.exe` /
`football_analysis_full.zip` / `latest.json`），压缩包内仍保留中文 exe 名；
Windows runner 上中文资产名会被 GitHub Actions 处理成 `default.exe` 之类，必须避免。

查询页「设置」窗口里有「软件升级」区块：

- **检查更新**：`GET /update/check` 读 `latest.json` 比较版本号；
- **一键升级**：`POST /update/install` 下载完整包，失败自动重试 3 次并校验下载长度与 SHA-256，
  任一步失败都不替换，
  然后启动同目录的 `updater.exe`：等主程序退出（exe 解锁）→ 从升级包取出新主程序替换
  `football_analysis.exe` → 自动重启。`data/`、`cache/` 等本地数据不动。

手动发版步骤：

```powershell
git tag v1.4
git push origin v1.4
```

发版前把版本号同步到目标版本，用 [tools/release.py](tools/release.py) 一键完成
（改版本号 + 残留检查 + 全量测试，可选 `--tag` 直接打 tag；提交与推送仍由你手动做）：

```powershell
python tools/release.py 1.4            # 同步 1.4 + 残留检查 + 跑 387 项测试
python tools/release.py 1.4 --tag      # 顺带打 v1.4 tag
# 然后：git add -A; git commit -m "..." ; git push origin v1.4
```

同步的文件：`football_analysis/__init__.py`（`__version__`）、`updater.py`（非包内兜底版本）、
`query_app.py`/`query_report.py`（查询页标题、H1、启动文案）、`tests/test_store.py`/`test_query_server.py`（断言）。

版本号集中在 [football_analysis/__init__.py](football_analysis/__init__.py) 的 `__version__`。
清单地址可用环境变量 `FA_UPDATE_MANIFEST_URL` 覆盖（默认指向该仓库 `releases/latest/download/latest.json`）。
注意：完整包必须包含 `updater.exe`（make_package.py / make_release.py 都已带），
否则 exe 内一键升级会提示手动解压新完整包。

## 归档

原型收尾状态、验证基线和交接边界见 `docs/归档说明.md`。正式归档包位于 `archives/football-analysis-mvp-20260910-r6.zip`，包含源码、测试、文档、独立 exe 和一份示例报告，不包含缓存与构建临时文件。归档包解压后，exe 在顶层 `football-analysis-mvp/football_analysis.exe`；`dist/` 只存在于当前源码树，不在归档包内。
## 目录结构

```text
football_analysis/
  http.py      抓取、GB18030/UTF-8 解码、页面缓存
  sfc.py       胜负彩列表解析
  jczq.py      竞彩列表解析，fixture_id -> 球队 ID / info_match_id
  odds_xml.py  odds.xml 解析：亚盘、大小球、欧赔、去水概率
  team.py      liansai 球队最近 10 场解析
  h2h.py       Titan 赛程映射 + 历史交锋解析/抓取
  model.py     泊松模型、市场融合、亚盘/大小球覆盖概率
  odds_analysis.py  欧赔/亚盘/大小盘水位分析、推荐与 EV
  odds_movement.py  亚洲盘让球初盘 -> 即时盘解析、变化分级与异动提示
  store.py     SQLite 异动库：期次快照与比赛异动
  query_report.py  异动查询页 HTML 渲染
  query_app.py 本地查询服务
  browser_report.py 浏览器报告页 HTML 渲染
  pipeline.py  数据流编排
  report.py    HTML 报告 / 控制台摘要
  main.py      CLI
docs/
  数据源与限制.md
  算法说明.md
tests/
  test_h2h.py
  test_odds_movement.py
  test_odds_analysis.py
  test_odds_and_model.py
```

## 数据链路

```text
trade.500.com/sfc 胜负彩列表
    ├─ jczq 竞彩列表 / 500 odds.xml ──→ 完整队名、欧赔、亚盘、大小盘
    ├─ liansai 球队页 ──→ 双方近 10 场、主客场、盘路、大小
    ├─ BF Next 日期页 ──→ match_no 映射 Titan match_id
    │                    └─ zq.titan007 / nowscore ──→ H2H
    └─ Nowscore 赛前指数 ──→ 亚洲盘让球初盘、即时盘，变盘异动提醒

Poisson 进球分布 + 市场锚定 + H2H 微调 ──→ 胜平负概率
odds.xml 亚盘/大小盘水位 + 模型比分覆盖 ──→ 让球/大小盘融合推荐
亚盘让球初盘/即时盘口与水位变化 ──→ 变盘异动提醒
```

## 与参考项目的关系

参考项目的核心不是黑箱机器学习，而是：

- 先抓胜负彩对赛、数据页、欧指、亚盘、投注页；
- 把近期战绩、主客场、交锋、赔率等转成数值特征；
- 泊松进球模型给出独立概率，再与市场概率收缩；
- 推荐方向跟随市场，独立模型只用来判断 EV、偏差和风险；
- 用 SQLite、赛后反馈和学习画像维护较长闭环。

本 MVP 保留了“采集 -> 特征 -> 泊松 -> 市场锚定 -> 动作门禁”的主骨架，但刻意砍掉了 Flask、SQLite、LLM 复核、离线回测等重型设施，方便从零阅读和继续扩展。

更完整的参考项目逐层分析见项目外的 [football_analysis_implementation.md](../football_analysis_implementation.md)。

## 已知边界

- `odds.500.com/fenxi/shuju-*.shtml` 目前被反爬/JS 校验拦截，因此本 MVP 的伤停、积分代理仍不可用；双方历史交锋由 Titan/Nowscore 页面补上，属于可选 adapter，单场失败会在报告中标记，不阻断整期。
- `odds.xml` 只覆盖竞彩当前开放的 23 场左右，SFC 某些场次只有亚盘和球队往绩，没有大小盘/欧赔；报告会把这些场次标记为“无市场”并只做谨慎参考。
- 历史期号可以指定 `--issue`，但 jczq 和 `odds.xml` 是当前快照，历史期的盘口/大小球覆盖率会显著下降。
- Titan 赛程页按日期归档，解析器会从 SFC 最早开赛时间推算候选日期并匹配 `zuCaiSchedule` 中的期号/场次；极端跨日赛程可能需要多抓一页，失败时同样只降级为无 H2H。
- 模型输出是研究参考，不是投注承诺。
