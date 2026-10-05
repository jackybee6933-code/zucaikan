# AGENTS.md

## 项目定位

这是一个轻量级足球胜负彩分析 MVP：采集比赛、近期往绩、历史交锋和盘口数据，使用泊松模型与市场先验生成分析报告，并重点提示亚洲盘让球的初盘到即时盘异动。

## 运行

在本目录执行：

```powershell
python run.py --cache-dir cache
```

运行后会先自动抓取胜负彩最新一期，再在 `reports/` 生成一份 HTML 报告，并把亚洲盘让球异动写入 `data/football_analysis.sqlite`。查询：`python run.py query --serve`。独立版 Windows 程序位于源码树 `dist/足球胜负彩分析工具.exe`；解压正式归档包后位于压缩包顶层 `足球胜负彩分析工具.exe`，双击后会自动抓取最新一期、入库并打开 HTML 报告。
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
- 亚盘/大小球兜底顺序一致：`odds.xml`（`asian`/`dxq`）→ `cache/odds_pages_<期号>.json` 单场页产物 → 胜负彩交易页每行 `data-asian` → Nowscore 赛前指数即时盘，报告标注来源（`澳门`/`威廉`/`赛前指数`），异动提醒仍只消费亚盘。
- 异动提醒消费亚盘让球初盘/即时盘 + 大小盘初盘/即时盘；欧赔不触发异动提醒。
- 赛后复盘：`review` 子命令回写最终比分并统计亚盘/大小琄推荐命中率（主推/轻仓口径，走水不计）。
- 大模型解读（可选）：`analyze --narrate` 对达到关注线的变盘异动调用 OpenAI 兼容接口生成 3-4 句综合解读（先要点近况与当前对手的过去对战，再解读盘口异动），挂在 HTML 异动表行下方；需环境变量 `LLM_API_KEY`（可选 `LLM_BASE_URL`/`LLM_MODEL` 指向 DeepSeek 等），无 Key 或请求失败时静默回退，不影响报告生成。
- 软信息检索（可选）：`analyze --scout` 对本期「观望」场次（`prediction.action == 观望` 且双方队名齐全）调用 OpenAI 兼容接口的联网检索（请求体 `enable_search`，可用 `LLM_SEARCH=0` 关闭）采集伤停/战意/赛程，强约束返回 JSON（检索不到必须 `found:false`，禁止凭记忆编造），解析失败回退为原文总结 + 倾向「无明确指向」。结果挂在 analysis["scout"]，只在 HTML 报告渲染独立的「软信息补充」区块（带溯源标签与置信度），不进入概率计算、不改动作门禁。无 Key 或请求失败静默跳过。
- 跨期趋势：`review --trend` 按期号升序输出亚盘/大小琄命中率与环比变化，作为后续反馈学习的净基线。
- 裸跑 `python run.py`（即 exe 双击，spec 入口是 run.py）的默认流程：**analyze → 写入 SQLite → 起异动查询服务 → 浏览器打开查询页**，不再直接弹单期 HTML 报告（该报告仍会生成到 reports/）。流程显式传 `--cache-dir` 和 `--db-path`（都挂在 `main._app_root()`，即 exe 所在目录）：前者不传 `Fetcher.cache.root` 为 None 则 `odds_pages_<期号>.json` 加载不到（让球/大小球整体变「无市场」），后者不传会走 `default_db_path()` 的 `Path.cwd()`，换个方式启动 exe 就写到别处。
- 查询页工具条在「分析报告」旁新增「复盘」按钮（可勾「刷新比分」）：POST `/review` 起后台线程跑 `main.review_issue()`，前端轮询 `/review-status`。复盘核心 `review_issue` / `_refresh_scores` 不打印、只返回结构化结果，CLI 与查询页共用。
- 查询服务路由契约：`do_GET` **没有 404 兜底**，未知路径会回落到查询页（单页应用式）；`do_POST` 则对未知路径返回 404。`/report/` 空文件名返回 400，路径逃逸被拒（400/404）。上述行为均有集成测试覆盖（tests/test_query_server.py，真起服务打真请求，跑在 daemon 线程里随进程退出回收，因此不需要给 serve_query 加 shutdown 钩子）。
- 复盘的文案格式化在 `query_app.format_review()`，`run_review()` 调 `main.review_issue()` 并返回可直接展示的文本；闭包内的 `_do_review` 只负责状态与异常。两者均有单元测试（tests/test_review.py）。
- 当前验证基线（2026-10-05）：182 项单测通过；裸跑流程、查询页复盘按钮、exe（13:29 重新打包）均已实测。历史期可用 `--issue 26129 --cache-dir cache --no-db` 离线重跑；查询页同步更新为 `reports/movement_query.html`。
- 数据源故障记录（2026-10-04，26135 期）：500 竞彩 `odds.xml` 返回合法但空的 `<matches/>`（上游未发布本期盘口），胜负彩交易页 14 场中仅 4 场有 `data-asian`、欧赔 `data-bjpl` 全空，Nowscore 赛前指数仅 2/14 —— 该期实测欧赔 0/14、让球 4/14、大小球 2/14；接入 `odds_pages` 单场页后升至让球 12/14、大小球 12/14。缺的两场（`1494417` 中国-塔吉克斯坦 2026 友谊赛、`1398668` 卢森堡-保加利亚）在 500 单场页上确认零公司行，等上游开盘即可补齐。空 `odds.xml` 现已显式告警，不再静默出无盘口报告。
- titan007 盘口可达性：公开域只有欧赔（`op1/1x2.titan007.com/oddslist/{id}.htm` + `1x2d.titan007.com/{id}.js`）；亚盘与大小盘仅在会员区 `vip.titan007.com/AsianOdds_n.aspx`、`OverDown_n.aspx`，带 session 仍断连。`bf.titan007.com/football/Next_*.htm` 是纯赛程页，无盘口。
- `odds.500.com/fenxi/*` 单场赔率页（`yazhi-<fid>` 亚指 / `daxiao-<fid>` 大小指数）：`requests` 直连会被腾讯 EdgeOne 拦（先是 JS cookie 挑战，再跳 `TEOCaptchaWidget` 交互验证），但真实浏览器会话可正常打开。`football_analysis/odds_pages.py` 负责把抓取产物（`{"<fixture_id>|<kind>": {"rows": [[cid, 即时主水, 即时盘, 即时客水, 初主水, 初盘, 初客水]]}}`）转成与 `parse_odds_xml` 同构的结构，按 fixture_id 索引（jczq 失效时 `info_match_id` 为空，`odds.xml` 永远匹配不上）。公司优先澳门(cid=5，仅部分页面有)，兜底威廉(cid=293，实测 24/24 覆盖)。
- 产物需浏览器会话抓取后落盘，程序本身不负责自动刷新；缺失时静默跳过，交给下层兜底。
- 单场页产物过期同样会静默降级：`pages_age_hours()` 按文件 mtime 判定，超过 `PAGES_STALE_HOURS`(6h) 就在报告告警并给出重抓命令，但**不阻断使用**。阈值取 6h 的依据：26135 实测 11:52 → 14:29（隔 2.6h）异动从 8/14 变到 10/14，2.6 小时就能让三分之一场次的信号翻新。
- 盘口刷新常态化：`python run.py refresh --issue 26135` 按产物年龄决定是否重抓——尚未过期（≤ `PAGES_STALE_HOURS`）则直接跳过并退出 0，**不需要启动 Chrome**；过期了才启动抓取，且先探测 CDP 端口，不通则打印启动步骤并退 1。`--force` 可强制重抓。抓取本体仍由 `tools/scrape_odds_pages.py` 完成，中途失败不覆盖原产物。
- 单场页抓取产物的每个页面带 status 字段：ok（拿到公司行）/ empty（页面正常但零公司行 = 上游确实未开盘）/ timeout 与 error（抓取失败）。parse_odds_pages 只信 ok/empty，page_failures() 返回失败页面并在报告中告警——抓取失败不得被当成「上游没开盘」。旧产物无 status 时按有无公司行推断，向后兼容。
- 刷新工具 `tools/scrape_odds_pages.py`，**唯一模式：CDP**（2026-10-04 验证通过，28 页全量约 4 分钟）：Chrome 136+ 禁止在默认 profile 上开 `--remote-debugging-port`，所以用 `setup_cdp_profile.py` 把默认 profile 的 `Local State` / `Default/Preferences` / `Default/Network/Cookies` 复制到 `%LOCALAPPDATA%/fb-chrome-profile`，再以该目录 + 调试端口启动。复制过去的 EdgeOne 验证 cookie 可用（页面会短暂显示 Security Verification 后**自动通过，无需点击**）。脚本只开一个标签页复用，且**不会关闭用户的浏览器**。
- 脚本只在完整跑完后写文件，中途限流/断连/超时一律不写；`--delay 8` 起步（默认），带 ±40% 抖动，触发「请求过于频繁」立即停。
- 零盘口场次（如 1494417、1398668）是**上游没开盘的正常结果**：页面标题已是真实赔率页但公司行为空，此时等 `SETTLE_POLLS` 拍让 JS 渲染后即返回空，不要当成失败空转到超时。
- 异动检测同样吃单场页：`build_pages_movements` 把初盘/即时盘转成与 Nowscore 同构的结构，在 Nowscore 赛前指数缺失的场次兜底（26135 实测异动从 0/14 升到 8/14）。
- **主推档当前不可达，且不要为“修主推”而调低阈值**：`_two_way_decision` 的 `blended = 0.85*市场概率 + 0.15*模型概率`，而亚盘/大小球水位天然接近平衡，市场概率实测仅 0.51~0.60，85% 权重压在这个几乎常数的量上 —— 24 期 / 328 场实测 confidence 上限 0.589、margin 上限 0.178，而主推门槛是 0.65 / 0.28，**两个市场都从未触发过**。超过轻仓的分档只有 7 场和 5 场，命中率分别 71% 和 40%，全是噪声；Kendall tau = +0.03，confidence 与命中率几乎无相关。真正携带信号的是 margin（≥0.10 比低于其好约 7.7 个百分点），而轻仓档已经用了这个门槛。tests/test_main_tier.py 把这个事实钉住了。
- 盘口分歧提示：单场页各公司即时盘口差距 >= `SPLIT_WATCH`(0.5) 时，`build_pages_movements` 在 movement 里写 `split` 数据块列表：`[{"market": "大小球", "picked": "293", "rows": [{"cid", "name", "initial", "current"}]}]`，保留页面原有公司顺序。查询页「类型」列显示「盘口分歧」四字，鼠标悬停弹出 `#split-pop` 卡片，按市场分块列出「公司 / 初盘 → 即时」，报告实际采用的那家（`picked`）即时盘口加粗。弹层用 `position: fixed` + JS 定位，避开 `.table-scroll` 的 overflow 裁剪；内容放 `<template>` 由 JS 注入，天然规避属性转义问题。
- 盘口分歧只在「盘口变化列的数据来源就是单场页」时显示：Nowscore 赛前指数覆盖的场次用 Nowscore 盘口，与单场页各家公司口径不同，故不挂分歧标记。因此该数量会随 Nowscore 覆盖浮动（实测 8~10）。
- `COMPANY_NAMES` 只登记已确认的 cid（5 澳门 / 293 威廉 / 4 皇冠 / 6 伟德）；单场页对未登录用户打码公司名，未登记的直接显示裸 cid。
- 注意单家口径：单场页按公司优先取一家（澳门优先，兜底威廉）作为盘口数值，个别公司会显著偏离共识（26135 第 9 场大小球威廉 3.5→2.5，其余公司多为 3.5→3 或持平）——这类场次会同时被 `split` 标出，解读重点关注项时先看分歧再下结论。
- 这是研究型原型，尚未完成长期回测、赛后反馈学习、生产部署和多公司完整赔率时间线。


