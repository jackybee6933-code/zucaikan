# AGENTS.md

## 项目定位

这是一个轻量级足球胜负彩分析 MVP：采集比赛、近期往绩、历史交锋和盘口数据，使用泊松模型与市场先验生成分析报告，并重点提示亚洲盘让球的初盘到即时盘异动。

## 运行

在本目录执行：

```powershell
python run.py --cache-dir cache
```

运行后会先自动抓取胜负彩最新一期，再在 `reports/` 生成 `md`、`csv`、`json`、`html` 四类报告，并把亚洲盘让球异动写入 `data/football_analysis.sqlite`。查询：`python run.py query --serve`。独立版 Windows 程序位于源码树 `dist/足球胜负彩分析工具.exe`；解压正式归档包后位于压缩包顶层 `足球胜负彩分析工具.exe`，双击后会自动抓取最新一期、入库并打开 HTML 报告。
常用命令：

```powershell
python run.py --cache-dir cache            # 抓最新一期并生成报告
python run.py --issue 26129 --cache-dir cache --no-db   # 用缓存重跑历史期
python run.py query --serve                # 异动查询页（自选 8765 附近空闲端口）
python run.py review --issue 26120 [--refresh]   # 赛后复盘：刷新比分并统计推荐命中率
python run.py review --trend [--refresh]    # 跨期命中率趋势
python -m unittest discover -s tests       # 测试
```

## 技术栈

- Python 3.14 验证通过
- `requests`、`beautifulsoup4`
- PyInstaller 用于生成 Windows 单文件 exe

## 目录约定

- `football_analysis/`：采集、解析、模型和报告代码
- `tests/`：解析器、盘口分析和异动提醒测试
- `docs/`：算法、数据源限制和归档说明
- `data/`：本地 SQLite 异动库，不纳入正式归档包
- `reports/`：运行生成的报告，不作为源码依赖
- `cache/`：原始页面缓存，不纳入正式归档包
- `build/`：PyInstaller 临时构建目录，不纳入正式归档包
- `dist/`：可交付 exe；测试报告目录只用于验证

## 当前状态

- 现役功能：自动抓取最新一期胜负彩、往绩/H2H、泊松模型、欧赔/亚盘/大小盘分析、亚洲盘让球异动提醒、本地 SQLite 异动库、浏览器 HTML 报告、独立 exe。
- 大小球双来源兜底：`odds.xml` 的 `dxq` 优先；缺失时回落到 Nowscore 赛前指数即时盘，报告标注来源（`澳门`/`赛前指数`），异动提醒仍只消费亚盘。
- 异动提醒消费亚盘让球初盘/即时盘 + 大小盘初盘/即时盘；欧赔不触发异动提醒。
- 赛后复盘：`review` 子命令回写最终比分并统计亚盘/大小琄推荐命中率（主推/轻仓口径，走水不计）。
- 大模型解读（可选）：`analyze --narrate` 对达到关注线的变盘异动调用 OpenAI 兼容接口生成 3-4 句综合解读（先要点近况与当前对手的过去对战，再解读盘口异动），挂在 HTML 异动表行下方；需环境变量 `LLM_API_KEY`（可选 `LLM_BASE_URL`/`LLM_MODEL` 指向 DeepSeek 等），无 Key 或请求失败时静默回退，不影响报告生成。
- 跨期趋势：`review --trend` 按期号升序输出亚盘/大小琄命中率与环比变化，作为后续反馈学习的净基线。
- 当前验证基线（2026-09-29）：71 项单测通过；最新一期 26134 可自动抓取并生成 14 场报告，大小球 14/14 覆盖（odds.xml 与赛前指数混合来源）；历史期可用 `--issue 26129 --cache-dir cache --no-db` 离线重跑；查询页同步更新为 `reports/movement_query.html`。
- 这是研究型原型，尚未完成长期回测、赛后反馈学习、生产部署和多公司完整赔率时间线。


