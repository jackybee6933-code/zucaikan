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
```

查询已入库异动：

```powershell
python run.py query --attention-only
python run.py query --issue 26121 --team 巴萨
python run.py query --serve
```

运行后会在 `reports/` 生成：

- `issue_<期号>_*.md`：人读报告
- `issue_<期号>_*.csv`：推荐表
- `issue_<期号>_*.json`：完整结构化数据，含往绩和模型中间结果
- `issue_<期号>_*.html`：可直接用浏览器打开的看板，含“变盘异动提醒”“整期推荐”“全场比赛明细”

## 独立版 exe

已打包的 Windows 单文件程序位于 `dist/足球胜负彩分析工具.exe`，不依赖本机 Python。把它放到任意可写目录后双击运行，程序会自动抓取最新一期，在同目录的 `cache/` 保存可复用抓取缓存、在 `reports/` 生成报告，并在结束后自动打开浏览器页面。

命令行缺省自动抓取最新一期；也可指定缓存目录或历史期号：

```powershell
.\dist\足球胜负彩分析工具.exe --cache-dir .\cache --no-open
```

重新打包：

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --console --name "足球胜负彩分析工具" --distpath dist --workpath build --specpath build standalone.py
```

## 归档

原型收尾状态、验证基线和交接边界见 `docs/归档说明.md`。正式归档包位于 `archives/football-analysis-mvp-20260910-r6.zip`，包含源码、测试、文档、独立 exe 和一份示例报告，不包含缓存与构建临时文件。归档包解压后，exe 在顶层 `football-analysis-mvp/足球胜负彩分析工具.exe`；`dist/` 只存在于当前源码树，不在归档包内。
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
  report.py    MD/CSV/JSON/控制台报告
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
