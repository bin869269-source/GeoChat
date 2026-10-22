# GeoChat — Phase 3D: Session & Shared Contracts

> 前十一部分保留实施阶段记录；Finalization 的复核、最小修复和最新结果见文末。
> Finalization 最终全集为 208 项：195 Passed、13 strict xfail，无意外失败或 XPASS。

## Part I — Contracts & Session Refactoring Summary

开始时实际核实：`refactor/modular-architecture`，HEAD
`9066354f8ee965462800bf4097e37ce9c04698ba`，工作区干净；修改前完整回归为
164 Passed、13 strict xfail（177 个用例）。没有其他成员未提交的修改。

本阶段建立显式 Session、Dataset/Layer 分离和单工具原子提交，完成直接相关的
Frontend、Agent 和 backend 兼容适配。不增加依赖，不修改 GIS 算法或地图渲染器。

最终实际运行：**203 个用例，190 Passed、13 strict xfail、0 意外 Failed、0 XPASS**，17.13s。
其中原有用例为 165 Passed、12 strict xfail；新增 26 个用例为 25 Passed、1 strict xfail。
Known Defect 的数量虽然仍为 13，组成已经改变：修复 KD-SESSION，新增记录历史 KD-CSV-METADATA。

本文使用 Code-confirmed 表示静态源码核查，Test-verified 表示实际执行测试，
Known Defect 表示保留正确业务期望的 strict xfail，Not Run 表示未执行的外部/浏览器验证。

## Part II — New Modules and Data Contracts

| 文件/位置 | 职责与字段 | 状态 |
|---|---|---|
| core/__init__.py | 无初始化副作用的 Package 入口 | Code-confirmed / Test-verified |
| core/contracts.py:12 Dataset | UUID dataset_id、名称、data、CRS、source、parent_ids、兼容 metadata | Code-confirmed / Test-verified |
| core/contracts.py:23 Layer | UUID layer_id、dataset_id、名称、renderer、data_field、style；不存 data | Code-confirmed / Test-verified |
| core/contracts.py:33 ToolResult | success、message、创建的 Dataset/Layer ID、更新 Layer ID、metrics、warnings、error_category | Code-confirmed / Test-verified |
| core/session.py:44 SessionData | 数据/显示注册表、查询、删除、重置、防御性读取、单工具提交 | Code-confirmed / Test-verified |

三个契约采用 frozen dataclass。嵌套数据的隔离由 Session 的防御性复制保证；
GeoDataFrame 的 object 单元格额外递归复制，避免 pandas deep copy 仍共享可变属性。

只有 Dataset 注册表持有权威载荷。Buffer、Area/Density、随机森林派生结果保存为 GeoDataFrame；
Layer 仅保存引用和显示配置。为保留原 CSV/list、单 Feature、FeatureCollection 和已知缺陷行为，
上传及旧上下文导入暂时保留原载荷形状。没有在其他模块保存另一套可变 GIS 数据。

legacy_layers/raw_view/map_view 是临时、分离的兼容投影，不是第二份状态仓库。
同一快照中同一 Dataset 的多个显示层复用一个序列化载荷；Renderer 不感知 Session。
原始 JSON/list 的 CRS 仍沿用旧 EPSG:4326 假定，不能解释为 CRS 已验证；
直接存入的 GeoDataFrame 则保留其原始 CRS，不做投影转换。

## Part III — Session Isolation Design

Frontend 为每个 Streamlit Session 创建 business_session 和 session_agent；后者显式持有 Session，
并绑定本会话的七工具、延迟模型和 Graph。模块中没有当前用户引用、全局图层总线或 ContextVar。
core/ 仅依赖标准库和本包契约，不依赖 Streamlit、Agent、LangGraph、模型、GIS 或 Folium。

每个 Session 独立 RLock。一次工具执行在本 Session 锁内取得分离快照、计算、验证和发布；
不同 Session 不共用业务锁。Frontend 读取的是防御性快照，不会通过旧列表引用修改其他会话。

Test-verified：

- 原 KD-SESSION 的 A-start / B-start / A-tool 交错业务断言现在通过。
- 两个真实 Graph 使用 Mock 模型，通过 Barrier 在同一进程并发执行，各自只生成自己的数据/图层。
- 会话 A 无法读取或修改 B 的 Dataset/Layer ID；B 重置不影响 A。
- 同一 Session 的 12 个并发显示调用不丢失更新，名称和 Layer ID 各自唯一。
- 人为阻塞 A 的 Buffer，B 的工具仍可完成，证明没有用全进程串行掩盖隔离问题。
- 两个未经注入的 AppTest 实例自行建立不同 Session 和延迟 Agent；重跑、上传、删除和重置均保持会话归属。
- 独立 Python -I 子进程不继承 Key，拦截框架/GIS/模型导入与 socket，仍可导入 core、创建和重置 Session。

## Part IV — Agent and Tool Integration

agent/tools.py:create_tools(session) 返回绑定会话的七个 StructuredTool，顺序保持：

1. fetch_city_boundary_osm
2. visualize_layer_on_map
3. calculate_buffer_analysis
4. calculate_area_and_density
5. run_machine_learning_spatial_prediction
6. update_map_style
7. calculate_feature_distances

Code-confirmed：与 HEAD 的 AST 比较，七工具参数、默认值、返回注解及描述一致；
GIS 仍委托 gis/，100 棵树、random_state=42 的现有随机森林保留。
正常返回仍是原成功文本或距离 Markdown；未改变 LLM 接收的数据格式。
新增加的重名歧义和提交失败提供明确错误文本，内部同时登记 ToolResult。
metrics 记录 Buffer 距离/要素数、面积或密度目标字段、ML 目标字段/样本数，以及距离对数/最小距离。

Graph 保持 START → agent → tools → agent / END 拓扑，模型与 Prompt 主要指引不变；
图层摘要补充 Dataset/Layer ID，帮助重名时明确引用。
ToolNode 仍执行和验证真实工具，但同一模型批次通过 RunnableLambda 显式按顺序调用。
同步和异步路径均串行等待每个工具完成，不依赖 Prompt 的顺序承诺。
Test-verified：max_concurrency=8 下，同批显示 → Buffer → 样式更新的同步/异步调用均可读取前一步结果。

backend.py 保留静态 GIS/Map 重导出和 SessionAgent 工厂门面，不再提供隐式默认用户 Agent。
原模块级 set_agent_context(raw, shown) 现在返回独立 handle，调用者必须保留它：

```python
from backend import create_agent_session
from core.session import SessionData

session = SessionData()
agent = create_agent_session(session)
agent.visualize_layer_on_map.invoke({"layer_name": "a Dataset ID or unambiguous name"})
# 实际对话入口：agent.agent_app.invoke({"messages": [...]})
```

handle.set_agent_context 导入旧列表时替换该 handle 的状态，不保留原列表引用。
旧代码若继续 import 全局 agent_app/TOOLS 或依赖原输入列表被原地追加，需要改为显式 handle；
保留这些隐式入口会重新引入共享用户状态。当前 Frontend 及测试均已适配。

## Part V — Dataset / Layer Identity & Lifecycle

Code-confirmed / Test-verified：

- Dataset 与 Layer 分别有稳定 UUID；样式更新不改变二者身份。
- 同名不同 Dataset 不会静默选第一个；返回歧义反馈，可用 Dataset/Layer ID 指定。
- 同一 Dataset 可创建多个 Layer，数据仍只有一个权威所有者；显示操作显式传递 dataset_id。
- 显式 Dataset ID 查询不受显示可见性限制；旧名称查找仍保留 KD-LOOKUP 所描述的限制。
- 样式操作若遇到同一 Dataset 的多个 Layer，要求明确 Layer ID；不会任意修改其中一个。
- 删除 Layer 只删除显示；其他显示及 Dataset 数据保留。
- Sidebar 删除原始上传项是 remove_upload，即取消上传列表归属，保留仍显示或被派生数据引用的 Dataset。
- 真正 delete_dataset 默认拒绝仍被 Layer/派生数据引用的对象；显式 cascade=True 才递归删除依赖。
- 派生 Dataset 保留 tool:name 来源和实际输入 Dataset 的 parent_ids。
- reset 清空当前会话 Dataset、Layer、上传列表和 ToolResult 历史，不影响其他会话。
- Frontend 重置同时更新上传控件 key，避免已选文件在 rerun 时立即重新导入；控件边界 Mock 测试通过。

reset 保留容器与 session_id、已初始化的模型/Graph，清空业务数据及 UI 对话历史；
新数据重新分配 ID，旧 ID 不会复用。旧上下文的显式整体替换也重新分配导入对象 ID。

## Part VI — Atomic Commit and Error Handling

core/session.py:244 commit_views 在候选注册表上验证名称/渲染类型、序列化、Dataset/Layer 外键和派生来源；
所有可能失败的防御性复制均在发布前完成，再同时替换注册表并登记成功 ToolResult。
既有 Layer 不允许被提交为其他 Dataset 的显示，也拒绝未知/其他 Session 的 Layer ID。

失败时不发布该工具的 Dataset/Layer；仅保存结构化失败记录及兼容反馈。
验证针对结构、传输和引用完整性，不新增几何有效性、投影或数值策略，避免顺带改变 GIS 语义。
工具级验证仍保留原 Schema 行为，Pydantic 参数错误由原工具/ToolNode 路径处理。

Test-verified：实际 GIS 运算后注入不可序列化属性，以及候选 Dataset 已构造后令 Layer 构造失败，
均不留下部分 Dataset 或显示层。成功结果引用可查询到实际对象。
前一步工具成功后再让模型失败，已提交结果继续保留；不实现整轮 Agent 回滚。

兼容层当前通过既有成功文本前缀识别成功，再形成 ToolResult；这是已测试的过渡边界，
未来修改工具文案时需要同步审核，不能假定任意新增文本都可自动识别。

## Part VII — Compatibility and Regression Results

| 验证范围 | 结果 | 证据性质 |
|---|---|---|
| 七工具名称、Schema、默认值、注册/绑定和实际 ToolNode 分派 | Passed | Test-verified + AST 对比 |
| Buffer、距离、面积、点计数、密度与独立参考 | Passed / 已有 Known Defect | Test-verified；GIS 源码/断言未变 |
| 随机森林预测、残差及错误路径 | Passed | 真实 scikit-learn、小型合成数据 |
| CSV/JSON/GeoJSON、Point/Line/Polygon/MultiPolygon、专题图 | Passed / 已有 Known Defect | 确定性解析及 HTML 结构测试 |
| 上传、图层管理、对话、独立 Session、重置 | Passed | Streamlit AppTest；上传控件及模型边界 Mock |
| HTML 内存下载、原文件名/MIME、无共享固定文件 | Passed | 既有 AppTest 与地图测试 |
| GIS / Map 独立导入及不修改输入 | Passed | 既有独立进程与回归测试 |
| core 独立导入、并发/交错隔离、原子提交 | Passed | 新增直接/子进程/真实 Graph 测试 |
| 纯数值 CSV 的 Frontend 元数据采样 | Known Defect | 新增 strict xfail，并已核实旧提交同样失败 |
| 真实 DeepSeek、线上 Nominatim、浏览器地图视觉/交互 | Not Run | 本次不使用真实凭据或外部网络 |

原测试的适配限于：fixture 返回显式 handle；状态断言读取 Session 视图，
不再要求原输入列表被原地修改；注册从会话工具工厂取得；AppTest 注入会话或验证其自行创建。
tool schema、正常/错误反馈、地图 HTML、数值及 ML 断言保留。
KD-WEIGHT 仅改为读取真实 Session 结果，其正确期望仍为 weight=0。
没有删除测试来制造通过；新增的历史缺陷保留合理的“上传应成功”断言。

## Part VIII — Known Defect Changes

修复 KD-SESSION：迁移后保留标记的首轮全集出现 XPASS(strict)：
1 Failed、164 Passed、12 xfailed；原业务断言 `len(a_shown) == 1 and b_shown == []` 未放宽。
补充跨线程、交错执行、外来 ID 拒绝、独立 AppTest、局部重置与异步顺序测试后，仅移除此项 xfail。

其余原有 12 个 strict xfail 均保留 strict=True/raises=AssertionError：

- CRS 覆盖、51N Web Mercator Buffer 地面距离误差；
- 极小面积提前归零、密度精度损失；
- 空/零面积/自交三种无效面输入未拒绝；
- Buffer 名称查询被显示层遮蔽、缺失密度点图层静默降级；
- weight=0 被忽略、单 Feature 点渲染、CSV 越界坐标未拒绝。

新增 KD-CSV-METADATA：有效纯数值 CSV `lat,lon,value` 在 Frontend 的
`json.dumps(raw_data[:3], ensure_ascii=False)` 遇到 numpy.int64，上传失败。
新增重置测试首次触发后，独立抽取 HEAD 和当前相同采样表达式，分别用同一未修改 parser 输入验证，
两者均为 `Object of type int64 is not JSON serializable`，证明是历史路径缺陷而非 Session 回归。
独立上传成功期望保留为 strict xfail；重置测试改用原本可成功导入的带名称 CSV，继续验证重置本身。
未修改 parser、采样序列化或降低成功期望。

最后缺陷总数：13 - 1 已修复 + 1 新发现的历史缺陷 = **13 strict xfail**。
这不表示全部 GIS 问题或全部 CSV 上传路径正确。

## Part IX — Actual Test Commands and Logs

实际使用 CPython 3.14.5 / .venv，pytest 9.1.1、Streamlit 1.66.1、LangGraph 1.2.16、
langchain-core 1.6.13、GeoPandas 1.2.0、Shapely 2.2.0、Folium 0.20.0、scikit-learn 1.9.1。
依赖声明及约束未修改、未安装或升级包。

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe -m pytest tests/test_session_contracts.py -q -ra --tb=short
.venv\Scripts\python.exe -m pytest tests/test_session_contracts.py::test_frontend_reset_rekeys_uploader_to_prevent_immediate_reimport -q --tb=short
git diff --check
```

上述聚焦命令用于中间诊断，不能替代最终全集结果。首批 19 项新增测试先发现 Dataset ID 多显示层
歧义（1 Failed、18 Passed），修正解析后为 19 Passed。后续新增边界覆盖，全集曾为
187 Passed/12 xfailed，再为 189 Passed/12 xfailed。新增重置路径触发历史 CSV 问题时为
1 Failed/189 Passed/12 xfailed；定位后保留为独立 Known Defect，并继续完成重置验证。

最终全集按第一条命令实际执行，通过 PowerShell Tee-Object 保存日志并传回 pytest 退出码：
**190 Passed、13 xfailed、0 意外 Failed、0 XPASS，17.13s，退出码 0**。

本地证据路径（被现有 .gitignore 排除，不提交缓存或日志）：

- tests/logs/phase3d-initial-run.log：保留 Session xfail 时的 XPASS 证据。
- tests/logs/phase3d-session-run.log：首批 19 项新增测试通过。
- tests/logs/phase3d-csv-preexisting.log：HEAD 与当前采样表达式同样失败的独立复现。
- tests/logs/phase3d-run.log：最终全集结果。

修改前 164 Passed/13 xfailed 基线已在本次工具输出核实，没有单独保存 baseline 日志文件。

额外实际检查：七工具签名/描述 AST 对比，候选源码语法检查，全部测试的断言检查，
本机绝对路径/常见私钥格式扫描，GIS/地图/依赖未变验证，以及 git diff --check。
未读取真实密钥；测试隔离 dotenv、使用合成凭据并拦截 requests/httpx/socket，不能把 Mock 通过理解为真实服务验收。

## Part X — Git Diff and Modified Files

共 15 个项目文件，全部属于本阶段；没有暂存、Commit、Push、Merge 或其他分支改写。

修改 10 个文件：

- agent/graph.py、agent/tools.py：会话工具绑定、顺序执行和结果提交。
- backend.py、frontend.py：显式会话门面及 UI 业务数据访问。
- tests/conftest.py、tests/test_agent_modules.py、tests/test_agent_session.py：会话 fixture/Agent/UI 适配。
- tests/test_data_maps.py、tests/test_known_defects.py、tests/test_tools.py：读取权威 Session 状态的直接适配。

新增 5 个文件：

- core/__init__.py、core/contracts.py、core/session.py。
- tests/test_session_contracts.py、tests/PHASE3D_REPORT.md。

gis/、maps/、agent/llm.py、所有依赖及项目配置无修改。
backend.py/frontend.py 的原 Windows 只读属性已恢复。
候选文件未发现真实凭据、硬编码本机绝对路径或生成缓存；.venv、缓存及日志未纳入变更清单。

分支仍为 refactor/modular-architecture，HEAD 仍为 9066354f8ee965462800bf4097e37ce9c04698ba；
main 仍为 dcad948d029842a555c609149b5cc226a1fdfe90。工作区有本阶段未提交修改，等待用户审核。

## Part XI — Remaining Risks and Readiness

本阶段具备审核和后续固定 Git 基线的条件，没有新增意外测试失败；不是最终系统验收或 GIS 正确性修复。
后续工作须重新获得用户授权，本次不自动进入集成收尾、清理 backend 或提交代码。

剩余风险：

- 上述 13 项 Known Defect 需要独立审批与修复；纯数值 CSV 问题影响特定真实上传路径。
- 兼容层、全文本成功识别及旧载荷格式仍在；统一所有输入为规范 GeoDataFrame 尚未完成。
- 每工具防御性复制及序列化增加内存/CPU；大数据量和长会话压力测试 Not Run。
- 同会话工具串行执行，慢网络/GIS 会占用本会话锁；不同会话已验证可并发。
- 不提供整轮事务、跨进程会话、持久化、撤销或正在执行整轮 Agent 的取消语义。
- 删除上传项/显示层不会自动清理仍保留的 Dataset；引用保护优先，完整 reset 可释放业务数据。
- 前端继续只保留用户及最终 AI 历史，不新增完整工具轨迹 UI。
- 数据保存在内存，部署重启需重建会话；未实现旧运行进程中 Streamlit 旧键的热迁移。
- 浏览器视觉、真实外部服务、生产并发负载及热升级均 Not Run。

## Finalization — 审核与 Git 基线固定

### 1. 审核结论

本次重新核实分支 refactor/modular-architecture、父基线
9066354f8ee965462800bf4097e37ce9c04698ba，暂存区为空，全部 15 个文件可归属 Phase 3D，
没有发现来源不明或其他成员新增的修改。审核直接检查源码、已跟踪 diff 和未跟踪文件，未只引用旧报告。

入口完整测试实际复跑：203 项，190 Passed、13 strict xfail，18.42s。
新增五项直接相关的审核测试后，发现并修复两个边界缺口；不修改 CSV/GIS 历史缺陷。

### 2. Contracts 与所有权

Code-confirmed：Dataset 是唯一持久业务载荷所有者；Layer 只存 dataset_id 和显示配置。
core 只导入标准库及包内契约；没有反向依赖、ORM、数据库或新的第三方依赖。

Test-verified：同一个含嵌套可变属性的 GeoDataFrame、Dataset metadata 和 Layer style 对象
被传入两个 Session 后，修改原输入、查询结果和 A 的已提交样式，均不改变 B 的数据或样式。
GeoDataFrame object 单元格的递归复制继续有效。

发现 ToolResult 返回对象与内部历史共享 metrics 字典，成功/失败两个新增测试均先复现失败。
最小修复为 record_failure/commit_views 返回分离副本；commit_views 的额外复制也在发布前完成，
避免复制失败发生在发布之后。补充测试已通过，不改变对 LLM 的字符串反馈。

### 3. Session 与并发

Code-confirmed：Frontend 每会话持有 SessionAgent 和 SessionData，工具工厂闭包绑定指定 Session。
没有全局当前用户、全局默认 Agent/Session 或 ContextVar 回退；无参数工厂每次新建容器。
本 Session RLock 覆盖工具快照、计算及提交，不同 Session 不共用该锁。

Test-verified：原交错测试、线程并发、同 Session 并发写、局部 Reset、外来 ID 拒绝、
慢工具不阻塞其他 Session 以及新增公开异步门面并发调用均通过。

### 4. Dataset / Layer 生命周期

Code-confirmed / Test-verified：ID 独立于名称，多显示共用 Dataset，样式修改保留 ID；
删除显示层不删除数据，remove_upload 仅取消上传归属，delete_dataset 默认保护引用，
显式 cascade 才删除依赖。派生 Dataset 保留来源和 parent_ids，Reset 清空业务引用和结果历史。
本阶段没有 Dataset 原地更新 API；工具产生派生 Dataset，旧上下文替换是显式整体替换并重新分配 ID。
旧 Renderer 接收从权威 Dataset 与 Layer 生成的分离投影，没有另一套地图业务载荷。
ToolResult 中的 ID 是事件发生时的历史引用，不承诺后续显式删除对象后仍可查询。

### 5. 原子提交

Code-confirmed：候选注册表的结构、序列化、引用和复制检查完成后才发布。
Test-verified：序列化失败、候选 Dataset 构造后 Layer 失败均无部分提交；新增测试故意先原地修改
计算函数收到的 GeoDataFrame 再抛错，两个 Session 及原输入都保持不变。
前一成功工具仍在后续模型失败后保留，不宣称整轮 Agent 事务、取消或回滚能力。
对内存耗尽、进程崩溃和不受支持的私有属性篡改不提供事务恢复保证。

### 6. Agent 公共接口与兼容性

Code-confirmed：全仓库实际 Python 调用位置已检索，生产调用只在 frontend.py，
已经使用 session_agent.agent_app.invoke；没有遗留全局 agent_app 调用方。
backend 仍用于显式 Agent 工厂、旧上下文导入及 GIS/地图静态重导出。

保持：七工具名称、参数、默认值、完整 Schema、注册顺序及正常反馈；GIS/地图静态入口；
显式 handle 的 invoke、模型延迟初始化与多步骤结果读取。
有意废弃：模块级全局 agent_app/TOOLS/模型运行实例，以及 set_agent_context 的隐式当前用户副作用。
外部旧脚本必须保存 set_agent_context 返回的 handle，或改用 create_agent_session(session)。
未发现仓库内待迁移的实际调用方；未连接的外部脚本无法证明已迁移。

复核发现 SessionAgent.agent_app 实际为 AgentRuntime，而旧异步测试只调用底层 Graph，
该门面缺少 ainvoke。新增公开异步测试先实际出现 AttributeError，再补充三行异步转发。
Test-verified：两个公开会话入口在 asyncio.gather 中并发运行真实 Graph/ToolNode，
每批显示 → Buffer 正确顺序执行、引用属于本会话、模型仅绑定一次。

### 7. Known Defect 审核

KD-SESSION 的原最终业务断言完整保留，补充隔离证据有效。
KD-CSV-METADATA 再次从固定旧提交 9066354 的采样表达式独立复现；当前同样失败，
且 parser 与采样表达式均未修改。新缺陷测试 strict=True、raises=AssertionError。
其他 12 个历史缺陷保留 strict xfail；GIS 独立参考测试文件无 diff。
最终仍为 13 项：原 13 - 已修复 SESSION + 新记录历史 CSV。没有跳过测试、放宽正确性期望或修复无关缺陷。

### 8. 实际命令与结果

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe -m pytest tests/test_session_contracts.py -k 'same_frame_and_style or tool_result_return or public_async_agent_entry or failing_calculation_cannot' -q -ra --tb=short
git diff --check
```

| 实际运行 | Passed | strict xfail | 意外 Failed | XPASS |
|---|---:|---:|---:|---:|
| 审核入口完整基线（203 项） | 190 | 13 | 0 | 0 |
| 五项补充测试，修复前 | 2 | 0 | 3 | 0 |
| 五项补充测试，最小修复后 | 5 | 0 | 0 | 0 |
| 最终完整全集（208 项，18.61s） | 195 | 13 | 0 | 0 |

测试使用现有 .venv，无真实 Key 或外部网络；未安装/升级依赖。
最终完整命令通过 PowerShell Tee-Object 记录，并传回 pytest 退出码 0。
本地日志均被忽略，不纳入提交：

- tests/logs/phase3d-review-baseline.log
- tests/logs/phase3d-review-gaps.log
- tests/logs/phase3d-review-gaps-fixed.log
- tests/logs/phase3d-review-csv-baseline.log
- tests/logs/phase3d-freeze-run.log

### 9. 审核通过的提交清单

| 文件 | Phase 3D 归属原因 |
|---|---|
| core/__init__.py | 公共状态/契约 Package |
| core/contracts.py | Dataset、Layer、ToolResult 契约 |
| core/session.py | 所有权、生命周期、隔离、提交和返回副本 |
| agent/tools.py | 七工具按 Session 绑定和结构化结果登记 |
| agent/graph.py | ID 上下文、顺序 ToolNode 与公开异步转发 |
| backend.py | 显式 SessionAgent 工厂与兼容门面 |
| frontend.py | 当前会话状态、上传、图层、重置和执行入口适配 |
| tests/conftest.py | 独立 Session fixture 与旧格式绑定适配 |
| tests/test_agent_modules.py | 模块边界、工厂、模型和 GIS 委托回归适配 |
| tests/test_agent_session.py | 新入口、UI 状态及 SESSION 缺陷转 Passed |
| tests/test_data_maps.py | 读取权威 Session 渲染状态 |
| tests/test_known_defects.py | KD-WEIGHT 改为读取 Session，正确期望保留 |
| tests/test_tools.py | Schema、注册及分离状态快照断言适配 |
| tests/test_session_contracts.py | 原 26 项及审核新增五项契约/隔离/提交/接口验证 |
| tests/PHASE3D_REPORT.md | 实施、缺陷变化及本次审核记录 |

候选中没有 .env、.venv、日志、缓存或无关生成文件；没有发现真实密钥或硬编码本机绝对路径。
建议独立本地提交说明：refactor: introduce session-scoped state and shared contracts。
实际提交 Hash 和提交后状态在本轮最终答复及 git log 中提供，不将提交自身 Hash 写入自身内容。

### 10. Git 范围

仅上述 15 文件纳入本阶段提交；不改变 main，不 Push、Merge、Rebase、Reset 或操作其他仓库。
提交前固定比较 main=dcad948d029842a555c609149b5cc226a1fdfe90；提交后再次核对分支、父提交、
文件列表、暂存区和工作区，最终状态以提交后的实际检查为准。

### 11. 后续条件与未验证行为

审核和自动化验证通过，具备固定 Phase 3D Git 基线的条件；后续阶段仍等待用户授权。
Not Run：真实 DeepSeek/Nominatim、浏览器视觉与完整浏览器上传、生产负载/大数据、进程崩溃恢复及热升级。
13 个 Known Defect、内存复制成本、旧兼容门面和历史对话轨迹限制仍在；未开展下一阶段开发。
