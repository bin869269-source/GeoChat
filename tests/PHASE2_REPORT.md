# GeoChat — Phase 2 测试基线报告

日期：2026-10-22。范围：唯一正式仓库 GeoChat-Sync；未修改业务源码。

最终运行：**104 个用例：91 Passed、13 Known Defect（严格 xfail）、0 意外 Failed、0 普通 skip、0 Blocked**。
Known Defect 不是通过。逐个测试的实际状态保存在 [CASE_RESULTS.md](CASE_RESULTS.md)，
JUnit 原始结果本地保存在 `tests/results.xml`，控制台日志本地保存在
`tests/logs/final-run.log`；这些可重复生成且含本机信息的证据不纳入版本控制。

## Part I — Git & Environment Baseline

| 项目 | 实际结果 |
|---|---|
| Git 根目录 | 正式仓库 `GeoChat-Sync/`；本文路径均相对于仓库根目录 |
| 初始分支与状态 | `main`，工作区干净；无暂存、未暂存或未跟踪文件 |
| 业务基线（冻结提交的父提交） | `dcad948d029842a555c609149b5cc226a1fdfe90` |
| 最新提交 | `Replace old GeoChat with latest version` |
| 新分支 | 初始不存在；授权后已创建并切换 `refactor/modular-architecture` |
| 生产代码差异 | 与审核提交比较，backend.py、frontend.py 无差异 |
| 项目约束 | 当前及祖先目录未发现 AGENTS.md；未发现原有测试或依赖配置 |
| 原默认 Python | WindowsApps 应用别名，启动失败，不能作为可用解释器 |
| 找到的解释器 | 本机 uv 管理的 CPython 3.14.5；机器绝对路径仅保存在忽略的环境快照中 |
| Python 版本 | CPython 3.14.5，Windows x64，MSC v.1944 |
| 初始依赖 | 检查的 pytest 与所有项目直接依赖均未安装 |
| 实际测试解释器 | 仓库内 `.venv\Scripts\python.exe` |
| 测试环境 | uv 创建，仅在项目 .venv 安装必要运行/测试依赖；没有升级全局环境 |

分支创建与包下载最初被沙箱阻止；请求提升权限并获准后完成。
初始 Phase 2 未提交；本次 Finalization 经授权建立本地冻结提交。
未执行 Push、Merge、Reset、Rebase 或 Git 配置修改。

直接依赖实际版本：pytest 9.1.1；Streamlit 1.66.1；streamlit-folium 0.27.4；
python-dotenv 1.2.4；requests 2.34.2；pandas 3.0.6；NumPy 2.5.4；
GeoPandas 1.2.0；Shapely 2.2.0；scikit-learn 1.9.1；Folium 0.20.0；
Branca 0.8.2；geopy 2.5.0；langchain-openai 1.7.2；langchain-core 1.6.13；
LangGraph 1.2.16；pyproj 3.8.0；HTTPX 0.28.1。

完整环境版本和解释器路径本地保存在 `tests/logs/environment.json`（不提交）；
共享版本约束见 `tests/requirements.txt` 和 `tests/constraints.txt`。
这是本次建立的可运行测试环境，不证明与团队此前环境一致；未声明跨 Python 版本兼容。

导入结果：

- **Passed**：替换模型构造、使用假 Key、禁用 dotenv/secrets 读取后，可以导入完整 backend 并执行真实计算及图工作流。
- **Passed**：无 Key 的隔离导入测试确认原模块会抛出 ValueError；这是导入障碍的证据，不是生产导入成功。
- **Not Run**：使用真实凭据的原始导入、真实 DeepSeek、真实 Nominatim、真实浏览器端运行。
- 预期启动入口：`.venv\Scripts\python.exe -m streamlit run frontend.py`；本阶段未实际联网启动。

## Part II — Test Files & Fixtures

| 测试文件 | Passed | Known Defect |
|---|---:|---:|
| test_tools.py | 37 | 0 |
| test_data_maps.py | 33 | 0 |
| test_agent_session.py | 10 | 1 |
| test_gis_correctness.py | 10 | 7 |
| test_known_defects.py | 1 | 5 |
| 总计 | 91 | 13 |

合成 fixtures：点集、闭合矩形面、线、GeoJSON Feature/FeatureCollection、内存 CSV/JSON、
固定投影下已知尺寸的矩形、小面、零面积面、自交面、空几何、null 几何及重叠面。
不依赖历史版本数据或真实用户数据。

conftest 为每个测试重新加载完整 backend；不提取或复制生产函数来替代被测对象。
网络防护拦截外部 socket、requests、HTTPX；即使业务捕获异常，收尾审计仍检查是否发生过网络尝试。
仅保留 Windows asyncio 所需的字面量 loopback socket；HTTP 客户端在本机地址上也仍被阻止。
使用 pytest 临时目录执行 HTML 导出。

## Part III — Agent Tool Regression Results

七工具均 **Passed**：注册清单顺序、名称、参数字段/默认值/必填约束及缺失参数验证均已检查。

| 工具 | 正常调用 | 错误反馈及状态 |
|---|---|---|
| fetch_city_boundary_osm | Passed：Mock HTTP 返回面，新增图层与名称去重 | Passed：503、无结果、超时、非法 JSON，不添加结果 |
| visualize_layer_on_map | Passed：四种渲染类型、读取原始数据、名称去重 | Passed：缺图层反馈，无意外状态变更 |
| calculate_buffer_analysis | Passed：默认 500、真实几何输出、原数据不变 | Passed：缺图层、空数据；查找及精度缺陷另列 |
| calculate_area_and_density | Passed：面积、计数、密度、专题图输出 | Passed：缺面、空数据；缺点退化与精度缺陷另列 |
| run_machine_learning_spatial_prediction | Passed：真实随机森林拟合、显式/自动字段、可重复输出及残差 | Passed：缺图层、缺字段、无数值、面几何、空数据 |
| update_map_style | Passed：颜色、线宽、零透明度、数据不变 | Passed：缺图层；线宽 0 缺陷另列 |
| calculate_feature_distances | Passed：距离表、输出数量、数据不变 | Passed：缺图层、少于两对象、空数据 |

真实 LangGraph + ToolNode 配合脚本化 AIMessage：**Passed**。
覆盖直接回答、显示后 Buffer、多次模型调用、ToolMessage 反馈、工具失败返回模型、
后续模型失败时当前部分业务结果仍保留。
这些结果不代表真实 LLM 能正确理解自然语言。

## Part IV — Data Import & Map Results

- **Passed**：CSV 必需字段、默认点名、属性、空行集、缺字段、非数值、空文件。
- **Passed**：JSON/GeoJSON、大小写扩展名、Feature 与 FeatureCollection 的点/线/面解析。
- **Passed**：错误 JSON、未知扩展名、空/不支持的几何输入错误反馈。
- **Passed**：Point、Line、Polygon、HeatMap、Choropleth 的真实 Folium HTML 输出。
- **Passed**：CSV → 工具 → Point/HeatMap HTML 的跨组件链路。
- **Passed**：底图开关、指定视图、标题、样式传递、HTML 保存；AppTest 走实际导出按钮路径。
- **Passed**：当前 Folium 0.20 接受 HeatMap 的额外样式参数并生成 HTML；初期怀疑其必然抛错，经实测不成立。
- **Known Defect**：单个 Point Feature 被点渲染器当成空集合；CSV 接受纬度 100。
- **Not Run**：浏览器 JavaScript、真实瓦片、下载点击、离线 HTML 自包含性及视觉正确性。

## Part V — GIS Correctness Results

**Passed：10 项独立参考测试。**

- WGS84 点 (9, 51) 转换至 UTM32 时横坐标为 500000m，往返误差在 1e-9 度内。
- Geod 直接解构造的赤道及 51°N 100m 点对；业务输出在 0.1m 容差内。
- 解析矩形间最短几何距离 30m；交叉线距离 0m。
- 赤道 500m Buffer 的椭球测地半径在 1% 容差内。
- 1000m × 1000m 解析矩形面积为 1km²，允许显示舍入 0.00005km²。
- 普通小区域面积与 WGS84 椭球面积参考比较，容差 0.2%。
- 面内计数、精确顶点排除、面外排除、重叠面分别计数；密度与椭球面积参考比较，容差 1%。
- null 几何距离返回错误，无距离结果或图层写入。

**Known Defect：7 项正确性测试。**

- 声明 EPSG:25832 的输入被直接标成 EPSG:4326，未按原 CRS 转换。
- 51°N 的 500m Buffer 不满足地面 1% 半径精度要求。
- 1m² 小面被舍入成 0km²，面积与密度丢失。
- 123.4m² 面先舍入面积再计算密度，不符合独立解析参考。
- 空面、零面积面、自交面均未被面积工具明确拒绝（三项）。

这些数值和输入验收期望保留为严格 xfail，没有修改算法、放宽正确断言或隐藏失败。
Geod 参考与业务的平面度量路径不同，但底层均使用 PROJ，不能视为独立测绘认证。
当前边界/重叠计数行为已测试，尚未获得产品语义确认；测试容差也尚非用户批准的精度指标。

## Part VI — Session & State Results

- **Passed**：上下文查询返回当前列表引用；工具新增/修改状态；清空上下文。
- **Passed**：AppTest 执行原始数据删除、processed_files 更新、显示层删除、会话重置。
- **Passed**：AppTest 聊天输入 → 图调用 → 显示层 → 最终消息与刷新。
- **Passed（现行为记录）**：前端只保留用户消息及最终 AI 回答，没有完整工具消息链。
- **Passed（现行为记录）**：模型在工具成功后失败，已追加图层仍保留，无回滚。
- **Known Defect**：A 上下文设置后被 B 设置覆盖，A 的待执行工具实际写入 B；确定性交错测试复现。

因此不能将测试 fixture 的逐例隔离解释为应用具备生产会话隔离。
未执行真实多浏览器并发压测。

## Part VII — Known Defects / Blocked / Not Run

| 编号 | 状态 | 问题 | 用例数 |
|---|---|---|---:|
| KD-SESSION | Known Defect | 进程全局上下文跨会话覆盖 | 1 |
| KD-CRS | Known Defect | 输入 CRS 被覆盖 | 1 |
| KD-BUFFER | Known Defect | 51°N 缓冲地面距离误差 | 1 |
| KD-TINY | Known Defect | 极小面积被舍入为零 | 1 |
| KD-DENSITY | Known Defect | 密度使用已舍入面积 | 1 |
| KD-GEOMETRY | Known Defect | 空、零面积、自交面未拒绝 | 3 |
| KD-LOOKUP | Known Defect | 存在显示层时 Buffer 无法读取其他原始层 | 1 |
| KD-MISSING-POINTS | Known Defect | 缺少请求的点层时静默返回面积成功 | 1 |
| KD-WEIGHT | Known Defect | 无法将线宽设置为 0 | 1 |
| KD-FEATURE | Known Defect | 单 Feature 点渲染丢失 | 1 |
| KD-COORDINATES | Known Defect | 非法纬度未拒绝 | 1 |

最终 **Failed 0、Blocked 0、普通跳过 0**；已知缺陷共 13 项，不能归为通过。
真实模型、在线 Nominatim、浏览器和多用户部署均 **Not Run**。
固定共享 HTML 文件的并发覆盖风险仍存在；测试只验证临时目录内的功能，不宣称风险已修复。

## Part VIII — Actual Test Commands and Logs

环境检查使用 `git rev-parse`、`git status --porcelain=v2 --branch`、`git branch --list`、
`Get-Command`、`uv python list --only-installed` 和 `importlib.metadata`；没有读取凭据内容。

初始使用本机 uv 管理的 CPython 3.14.5 创建环境，安装命令如下。
为避免提交本机路径，创建环境的可移植等价命令为
`uv venv --python 3.14.5 --cache-dir .uv-cache .venv`；它不是本次重新执行记录。

```powershell
uv pip install --python .venv\Scripts\python.exe --cache-dir .uv-cache pytest streamlit streamlit-folium python-dotenv requests pandas numpy geopandas shapely scikit-learn folium branca geopy langchain-openai langchain-core langgraph pyproj
```

最终实际测试命令：

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short --junitxml=tests/results.xml 2>&1 | Tee-Object -FilePath tests/logs/final-run.log
.venv\Scripts\python.exe tests/summarize_results.py
```

实际运行记录：

| 运行 | 结果 | 解释 |
|---|---|---|
| 首轮 | 75 Passed、13 Failed、12 xfailed | 原始结果保留于 logs/initial.xml |
| 修正测试隔离后 | 87 Passed、13 xfailed | logs/second-run.log |
| 增加缺凭据导入与网络尝试审计 | 88 Passed、13 xfailed | 增量验证，未保存单独日志 |
| 最终，补充 CSV 跨组件与前端聊天 | 91 Passed、13 xfailed，6.82s，exit 0 | logs/final-run.log、results.xml |

首轮 13 个失败分别为：9 个合成 fixture 的 tuple/list JSON 比较问题；2 个网络防护误挡
Windows socketpair；1 个坐标拒绝测试的 xfail 异常类型不匹配；1 个 HeatMap 导出实际通过的 XPASS。
修正的是测试夹具、网络隔离及缺陷分类，没有改生产逻辑或删除失败测试。

## Part IX — Git Diff & Changed Files

- 修改 `.gitignore`：新增 .venv、uv、pytest、coverage 缓存规则，以及本地测试日志和 XML 排除规则。
- 新增 `tests/__init__.py`、`conftest.py`、五个测试文件。
- 新增 `tests/requirements.txt`、`constraints.txt`：固定必要直接依赖及本次解析版本。
- 新增 `tests/README.md`、本报告、`CASE_RESULTS.md`、结果汇总脚本。
- `tests/results.xml` 及 `tests/logs/` 的运行证据和环境快照仅保留在本机，不提交。
- `.venv/`、缓存也属于忽略的本地测试产物。
- `git diff --exit-code dcad948d029842a555c609149b5cc226a1fdfe90 -- backend.py frontend.py` 无差异。
- 本次 Finalization 显式暂存测试与文档白名单，创建仅包含 Phase 2 的本地提交；不 Push/Merge。

### Finalization 审核

- 104 个用例正常发现，13 个用例设置 `xfail(strict=True, raises=AssertionError)`，没有无条件跳过或动态 xfail。
- 用例名称无重复，均有断言或预期异常检查。多层重复场景分别验证工具、图、前端边界，有明确不同目的。
- 模型/HTTP 替身仅位于外部边界；真实工具、GIS、随机森林、Folium 和 LangGraph 均执行。
- GIS 正确性参考与业务算法分开；回归中的旧投影复算不当作独立精度依据。
- 已知缺陷测试的准备步骤失败使用非 xfail 异常报告，避免把未到达数值断言的失败误归为已知缺陷。
- 依赖声明与当前环境逐项核对；不安装或升级依赖。
- 移除共享报告中的本机绝对路径；日志/XML/环境快照排除提交；保留 CASE_RESULTS.md 作为冻结审核记录。
- 最终运行命令：`.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short`。
- 最终结果：104 用例，91 Passed、13 strict xfail，0 意外 Failed、0 XPASS、0 Blocked。
- 原始本地证据及冻结运行输出位置：`tests/logs/`；冻结控制台输出为 `tests/logs/freeze-run.log`。

## Part X — Readiness for Phase 3

**可以提交 Phase 2 审核；获得授权后具备开展 Phase 3 的测试条件。当前未开始 Phase 3。**

测试已覆盖七工具、数据输入、地图与导出、真实图循环的 Mock 模型路径、GIS 独立参考和会话缺陷。
没有必须先修改生产代码才能运行测试的阻塞。纯核心测试无需真实 Key 或互联网。

进入下一阶段前应审核：新环境依赖版本是否适合团队；GIS 范围/精度；边界和重叠计数规则；
整轮失败是否回滚；全局状态及固定导出路径的处理顺序。

最终架构保持用户指定的 frontend.py、agent/{graph,tools,llm}.py、gis/{data_io,geometry,statistics}.py、
maps/renderer.py、core/{contracts,session}.py、tests/ 及必要 __init__.py；仓库不改名。
Phase 2 获批后再重新制定分批迁移顺序，不在本阶段实施或提前创建目标业务包。
现有随机森林保留；GNN 训练仍是独立项目。
