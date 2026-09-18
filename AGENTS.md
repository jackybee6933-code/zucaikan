# AGENTS.md

## 项目定位

这是一个轻量级足球胜负彩分析 MVP：采集比赛、近期往绩、历史交锋和盘口数据，使用泊松模型与市场先验生成分析报告，并重点提示亚洲盘让球的初盘到即时盘异动。

## 运行

在本目录执行：

```powershell
python run.py --cache-dir cache
```

运行后会先自动抓取胜负彩最新一期，再在 `reports/` 生成 `md`、`csv`、`json`、`html` 四类报告，并把亚洲盘让球异动写入 `data/football_analysis.sqlite`。查询：`python run.py query --serve`。独立版 Windows 程序位于源码树 `dist/足球胜负彩分析工具.exe`；解压正式归档包后位于压缩包顶层 `足球胜负彩分析工具.exe`，双击后会自动抓取最新一期、入库并打开 HTML 报告。

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
- 异动提醒只消费亚洲盘让球初盘和即时盘；大小盘、欧赔不触发异动提醒。
- 当前验证基线（2026-09-17）：42 项单测通过；最新一期 26128 可自动抓取并生成 14 场报告（含 11 场异动提醒），查询页同步更新为 `reports/movement_query.html`。
- 这是研究型原型，尚未完成长期回测、赛后反馈学习、生产部署和多公司完整赔率时间线。
