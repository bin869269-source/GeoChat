# GeoChat — Phase 4B：CSV 元数据 JSON 序列化缺陷修复

日期：2026-10-24（Europe/Berlin）。只处理 KD-CSV-METADATA。下列 Part I–IX 保留首次实施记录；Finalization 的最终审核、复测和本地提交说明见文末。未 Push、Merge、Rebase 或 Reset。

结论：**Test-verified，KD-CSV-METADATA 已修复**。完整回归 **241 Passed、12 strict xfail，共 253 项**，0 意外失败、0 XPASS。真实 Chrome 验收 **24 Passed、2 Not Run**；原失败的 numeric.csv 已完成元数据采样、Mock 元数据分析和真实 Dataset 注册。

**Finalization 最新结果：244 Passed、12 strict xfail，共 256 项；浏览器仍为 24 Passed、2 Not Run。** 增加 3 项直接相关测试，未改变生产修复实现。

## Part I — Root Cause Analysis

修改前重新确认：

- Branch：`refactor/modular-architecture`。
- HEAD：`5f8d53d75b5e950d6b0b751463cb8a286d1e2e48`。
- main：`dcad948d029842a555c609149b5cc226a1fdfe90`。
- 工作区干净，backend.py 不存在。修改前完整测试为 208 Passed、13 strict xfail，28.03s。

**Code-confirmed / Test-verified** 调用链：

1. `gis/data_io.py:57` 解析上传文件；CSV 使用 pandas.read_csv 和 `iterrows()`，在第 75 行直接收集属性标量。
2. 纯整数 CSV `lat,lon,value\n51,9,3\n` 的 value 是 numpy.int64。
3. 修改前 `frontend.py:151` 对前 3 条记录执行普通 json.dumps，在进入模型调用和 Dataset 注册前抛出 `Object of type int64 is not JSON serializable`。
4. 前端捕获错误并显示解析错误。`agent.llm.smart_analyze_geodata` 只接收已编码字符串，因此其内部 fallback 无法处理在调用前发生的序列化异常。

原缺陷测试实际位于 `tests/test_session_contracts.py`，不在请求所列的 `tests/test_known_defects.py`。Phase 4A 的原始浏览器证据仍保留在 `tests/logs/phase4a-44f3c84dab3c4f00a33fdaab03467fc1/`；本阶段没有改写历史报告或证据。

修改前还实际核对了特殊值规则：含缺失值的 CSV 属性产生 NaN；普通 json.dumps 默认输出非标准 NaN/Infinity/-Infinity。数据解析器没有将这些属性转换成 null。该行为与整数 TypeError 不同，修复策略只作用于 AI 预览副本。

## Part II — Modified Files and Implementation

| 文件 | 修改与必要性 |
|---|---|
| `agent/llm.py:14` | 新增轻量 serialize_metadata_sample；该模块负责模型元数据边界，不初始化模型或读取 Key |
| `frontend.py:149` | 将原来三处分支 JSON 编码改为调用辅助函数；否则在进入 agent.llm 之前仍会失败。保留后续模型获取与 Dataset 注册顺序 |
| `tests/test_metadata_serialization.py` | 新增 32 项直接相关的类型、精度、特殊值、所有权、上传状态和无 Key 测试 |
| `tests/test_session_contracts.py:402` | 只删除 KD-CSV-METADATA 的两行 xfail 装饰器；原测试体及正确性断言原样保留 |
| `tests/phase4a_acceptance.py` | 原失败复现改为成功验证；记录实际模型收到的 JSON 样本，检查 value 为整数、Dataset 元数据与状态隔离 |
| `tests/PHASE4B_REPORT.md` | 本报告 |

没有修改 Data I/O 解析规则、Agent Graph/Tools、Session/Contracts、GIS 算法或 Renderer，也没有增加依赖。NumPy 已是现有直接依赖。

本机 frontend.py 原先有 ReadOnly 文件属性。仅为必要的采样调用替换临时解除，修改后立即恢复；最终属性仍为 ReadOnly，未修改 Git 配置或其他文件权限。

## Part III — Serialization Semantics

**Code-confirmed / Test-verified**：

| 输入 | 元数据预览 | 原始输入/注册数据 |
|---|---|---|
| Python int / NumPy 有符号和无符号整数 | JSON 整数；不经过 float，不转字符串 | 不变 |
| Python float / NumPy float32、float64 | 有限值为 JSON 数值；保留标量已有精度，不作额外舍入 | 不变 |
| Python/NumPy bool | JSON true/false，保持布尔类型 | 不变 |
| 字符串、字段名称、Unicode | 保留名称和内容，ensure_ascii=False | 不变 |
| None | JSON null | 不变 |
| CSV 缺失值 NaN、正/负无穷 | 仅在 AI 预览中转换成 null，allow_nan=False 禁止非标准数值 token | NaN/正负无穷仍按原解析结果保存；没有变更分析数据 |
| 不支持的对象、Decimal | TypeError；不以 str() 掩盖类型问题或静默损失精度 | 不发布半成品，不修改已有 Dataset |

NumPy 标量采用 item()；若扩展精度标量转换后仍是 NumPy 对象，则明确拒绝，避免无依据降精度。该情况不属于当前默认 CSV 解析路径；本机没有将任意数值对象泛化成字符串或 float。

精度测试包括 int64 的 9007199254740993、uint64 最大值、float32(0.1) 的实际值 0.10000000149011612、float64 的 17 位有效数字，以及 1e-200。float32 的较长十进制表示反映原二进制标量值，不是新增舍入。

采样规则逐字等价验证：列表仍取前 3 条；具有 features 的字典仍构造前 2 个 Feature 的 FeatureCollection；其他输入仍将编码结果截取前 1000 个字符。有限普通输入的 JSON 文本与旧规则一致。1000 字符分支仍可能截断 JSON，这是原规则，本阶段没有重设计采样政策；正常列表 CSV 使用前 3 条分支，不受该截断影响。

**既有解析行为保留**：全数值混合 CSV 的 iterrows 可能在进入序列化器之前把整数列提升成 float，例如 count=3 变为 numpy.float64(3.0)。本修复保留边界实际收到的类型，不擅自将 3.0 改成 3，也不修改解析器。边界收到 int/NumPy 整数时有独立断言确保 JSON 仍为整数。

## Part IV — Unit and Regression Tests

新增 **32 项 Test-verified**：

| 范围 | 数量 | 断言内容 |
|---|---:|---|
| Python/NumPy 数值、精度和 bool | 12 | 编码后值与类型、输入标量身份不变 |
| 缺失/非有限值 | 6 | 严格 JSON 解析为 null；源对象及值不变 |
| 嵌套字段与 Unicode | 1 | 名称、内容、原 NumPy 类型不变 |
| 原有采样规则 | 1 | 3 条 / 2 Feature / 1000 字符及有限 JSON 文本保持 |
| 不支持对象 | 2 | 明确 TypeError，不统一转字符串 |
| DataFrame / GeoDataFrame 所有权 | 2 | 与预览共享嵌套属性时，原 frame、CRS、对象引用和 NumPy 标量不变 |
| 真实 CSV → frontend → 元数据 → Dataset | 6 | 纯整数、混合数值、字符串数值、缺失值、正无穷、负无穷；检查实际模型输入、注册数据和独立 Session |
| 失败序列化 | 1 | 不调用模型，不污染已有 Dataset/Layer/results/processed_files |
| 纯数值 CSV 无 Key | 1 | 已越过序列化阶段，但仍在原模型初始化处反馈缺 Key，不注册 Dataset |

既有 CSV、JSON、GeoJSON、GIS 参考、七工具 Schema/执行、多步/异步、Session 隔离和地图/导出回归全部继续执行。没有跳过或删除失败测试。

## Part V — Browser Reproduction and Verification

**Test-verified，真实 Chrome 156，受控 Mock 元数据模型**：

1. 通过正式页面 Reset 当前 Session。
2. 使用真实文件控件上传原复现文件 numeric.csv：`lat,lon,value\n51,9,3\n`。
3. 页面实际显示 `Erfolgreich geladen: numeric (points)`，不再出现 int64 JSON 错误。
4. 从模型实际收到的消息解析严格 JSON，样本如下，断言 value 的 Python 解码类型是 int：

```json
[{"name":"Point 1","lat":51.0,"lon":9.0,"properties":{"value":3}}]
```

5. 真实 Session 注册 1 个名为 numeric 的 Dataset，metadata.data_fields 为 ["value"]，没有额外 Layer 或 ToolResult；独立会话 B 的状态逐项不变。
6. 完整浏览器流程包括之前的上传、地图、七工具、随机森林、实际下载和 Reset；总计 24 Passed、2 Not Run，退出码 0。

本次复用 Phase 4A 脚本，证据目录名称沿用 phase4a 前缀；对应 Phase 4B 的实际目录是：

`tests/logs/phase4a-24ed4df7b5df4359836765f3dbb9c969/`

| 证据 | SHA-256 |
|---|---|
| results.json | `d97a098e23ab3d51b6647f98769291a4df5a69b009a98c0fd74830b3071ffadc` |
| 06-numeric-csv-fixed.png | `a02088a5e6d26fbd800fc2ffc424a888cf42697042888875bcc0e471a9514fa4` |
| cleanup-check.json | `9dea39dbce3a7c2603cfcdf46579499ec643bbe0146e99f8978f13048b80d234` |

截图已实际查看；snapshots 中包含实际 Dataset 和模型收到的 metadata_samples，cdp-events 包含真实浏览器事件。0 未捕获 JS 异常、0 console error、0 网络失败，37 条既有 warning 仍保留。Chrome 正常退出，两个自有 Streamlit 进程已回收，三个自有服务端口全部关闭。

**Not Run**：真实 DeepSeek/在线 Nominatim，未使用真实 Key；浏览器仍依赖既有 CDN 静态资源和底图，不声明完全离线。页面标题编辑功能原本不存在，独立标题 Renderer 样本的通过不能算标题编辑通过。下载后完整多图层 HTML 的独立逐层视觉检查仍未补做。

## Part VI — Known Defect Changes

| 项目 | 修改前 | 修改后 |
|---|---|---|
| KD-CSV-METADATA | strict xfail / 浏览器 Known Defect | 原断言 Passed / 浏览器 Passed |
| 其他缺陷 | 12 strict xfail | 12 strict xfail，断言和标记均不变 |
| pytest 总计 | 221 | 253 = 原有 221 + 新增 32 |
| Passed | 208 | 241 = 208 + 修复的 1 + 新增 32 |
| 意外 Failed / XPASS | 0 / 0 | 0 / 0 |

先执行新增用例和原缺陷用例的 --runxfail 验证，通过后才删除该装饰器。没有取消其余正确性断言。

保留的 **Known Defect**：CRS 覆盖、51N Buffer 地面半径、极小面积、密度精度、3 个无效几何参数用例、旧名称图层查找、缺失点层反馈、weight=0、单 Feature 点渲染、无效坐标，共 12 个测试项。本阶段没有修复这些问题。

## Part VII — Actual Test Commands and Results

实际执行：

```powershell
# 修改前完整基线
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
# 首批边界测试
.venv\Scripts\python.exe -m pytest tests/test_metadata_serialization.py -q -ra --tb=short
# 在删除 xfail 前验证原正确性断言
.venv\Scripts\python.exe -m pytest tests/test_session_contracts.py::test_numeric_only_csv_can_complete_frontend_upload --runxfail -q -ra --tb=short
# 修复后的完整测试和浏览器验收
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
.venv\Scripts\python.exe tests/phase4a_acceptance.py
# 补充精度/非有限 CSV/无 Key 断言后的最终完整回归
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short | Tee-Object -FilePath tests/logs/phase4b-regression.txt
git diff --check
```

| 执行 | 结果 |
|---|---|
| 修改前完整基线 | 208 Passed、13 xfailed，28.03s |
| 首批新增边界测试 | 27 Passed，4.86s |
| 原缺陷断言 --runxfail | 1 Passed，4.08s |
| 首次修复后完整回归 | 236 Passed、12 xfailed，29.57s |
| 真实浏览器 | 24 Passed、2 Not Run，退出码 0 |
| 最终完整回归 | **241 Passed、12 xfailed，28.85s，退出码 0** |
| git diff --check | 无空白错误 |

日志 `tests/logs/phase4b-regression.txt` 与浏览器原始证据按现有 .gitignore 保留本地，不进入暂存区。Git 的 LF/CRLF 提示属于换行处理，不是测试失败；没有修改换行配置。

## Part VIII — Git Diff and Modified Files

仅以下六个文件属于 Phase 4B：

- 修改 agent/llm.py：元数据预览序列化辅助函数。
- 修改 frontend.py：直接采样调用边界，无其他 UI 改动。
- 修改 tests/test_session_contracts.py：只移除已修复缺陷的装饰器。
- 修改 tests/phase4a_acceptance.py：复用浏览器路径验证修复，并记录实际元数据样本。
- 新增 tests/test_metadata_serialization.py：32 项边界和上传测试。
- 新增 tests/PHASE4B_REPORT.md：本报告。

逐文件比较确认 agent/graph.py、agent/tools.py、core/、gis/、maps/、tests/test_gis_correctness.py、tests/test_known_defects.py 均与 HEAD 一致。其余历史报告、README、依赖和配置保持不变。

HEAD 保持 `5f8d53d75b5e950d6b0b751463cb8a286d1e2e48`，main 保持 `dcad948d029842a555c609149b5cc226a1fdfe90`；backend.py 不存在。改动未暂存、未提交，无 Push/Merge 或危险 Git 操作。

## Part IX — Remaining Risks and Acceptance Assessment

**本阶段验收通过**：原复现用例、独立数值断言、真实前端路径和真实浏览器全部证明修复有效；Dataset 注册和会话隔离保持。只修复 KD-CSV-METADATA，未改变无 Key 上传策略或 GIS 语义。

明确保留的边界：

1. 元数据预览中的 null 不区分 NaN 和正负无穷；只表示无可用有限样本值，不替换或修复 Dataset 中的原始属性。后续 GIS 对非有限属性的适用性没有在本阶段改写。
2. CSV iterrows 的既有整数→浮点提升行为保留；本阶段修复 JSON 边界，而非重做 CSV 类型推断。
3. 其他输入前 1000 字符可能截断 JSON，保留原规则。任意对象、Decimal、pandas 扩展缺失标量等并非默认 CSV 标量路径，不承诺新增支持。
4. 真实外部模型/OSM、浏览器离线运行、生产负载仍为 Not Run。无 Key 时上传仍会在模型初始化处失败并给出反馈。
5. README 与旧阶段报告记载的是此前基线；本报告记录当前实际 241 Passed / 12 strict xfail，没有改写历史验收证据或扩大到无关文档整理。

完成后停止，等待用户审核；不自动提交，不进行下一缺陷修复或后续阶段。

## Phase 4B Finalization — 最终审核与本地提交

### 1. 最终审核结论

审核起点实际确认为 `refactor/modular-architecture`，HEAD 为 `5f8d53d75b5e950d6b0b751463cb8a286d1e2e48`，main 为 `dcad948d029842a555c609149b5cc226a1fdfe90`。四个已跟踪修改和两个新增文件均属于本阶段，未发现来源不明的改动；暂存区起初为空。

**Code-confirmed**：生产修复只修改 agent/llm.py 与 frontend.py 的元数据边界，Finalization 没有追加生产修改。**Test-verified**：完整 pytest 及真实浏览器复测通过；可创建独立本地提交。不存在必须先修复的新序列化回归。

### 2. 根因、实现及依赖复核

原失败发生在前端 json.dumps 元数据样本时，尚未到模型调用或 Dataset 注册。新函数只递归构造用于预览的容器，NumPy 数值标量以 item() 转成本地标量；没有修改 CSV、注册数据或源对象。

AST 对比证明 `create_llm` 和 `smart_analyze_geodata` 的原函数体（包括 System Prompt、配置、返回和 fallback）与上一个提交完全一致。前端仍按解析 → 采样 → 获取模型并分析元数据 → add_upload → processed_files 的顺序执行。无 Key 策略保持原样。

逐文件比较证明 Graph、Tools、GIS、Maps、Core 和依赖声明均与上一个提交一致；对 gis/、core/ 的导入 AST 检查未发现反向 agent 依赖。新增 NumPy 导入使用项目已有直接依赖，未安装或声明新依赖。

### 3. JSON 类型、精度及数据所有权

**Test-verified**：新增显式 np.int32(-7) 用例，连同 int64、uint64、float32、float64、Python int/float/bool、字符串/Unicode、None 和非有限值全部覆盖。有限值保留数值类型；非有限值只在样本中为 null；Decimal/任意对象仍明确 TypeError。

DataFrame/GeoDataFrame 测试改用命名索引 `feature_id=17`，仍验证原 frame、索引、CRS、共享属性引用和 NumPy 属性类型不变。没有削减原有断言。

大整数的实际边界分层验证如下：

| 环节 | 结论与证据 |
|---|---|
| NumPy → Python JSON | **Test-verified**：9007199254740993 和 uint64 最大值 18446744073709551615 编码、解码仍是精确 Python int，没有先转 float 或字符串 |
| 真实 CSV → 前端 → 元数据/注册 | **Test-verified（AppTest）**：有符号 int64 范围内的 9007199254740993 经真实 CSV 解析及前端上传仍保持整数；新增用例验证模型收到的样本和 Dataset |
| 元数据 → 当前模型客户端请求编码 | **Test-verified（MockTransport）**：使用已安装的真实 ChatOpenAI/OpenAI 客户端编码请求；HTTP 传输由本机 MockTransport 完全替代。请求中的 messages.content 是字符串，两个大整数的完整十进制文本均保留 |
| 页面/模型调用边界 | **Code-confirmed**：当前元数据从上传文件字节在服务端生成，再作为提示文本交给模型，没有经过浏览器 Number 的数值解析；不代表其他 JSON 消费方也有相同保证 |
| 任意 JavaScript 数值消费方 | **兼容边界**：超出 Number 安全整数范围 ±(2^53−1) 时不能承诺精确表示。这里保留 JSON 数字与原输入类型，没有为未来调用方统一转字符串 |
| 真实模型数值理解能力 | **Not Run**：SDK 编码保真不能证明真实模型能正确理解或计算任意大整数 |

**Code-confirmed / 实际解析复核的既有边界**：CSV 为 `lat,lon,value\n51,9,18446744073709551615\n` 时，当前未修改的 iterrows 路径已将混合 int64/uint64 行提升到 float64，值为 1.8446744073709552e19；转回整数比源值大 1。这一损失发生在元数据边界之前，Data I/O 与旧 HEAD 相同。序列化器不能恢复已经丢失的位；本阶段不修改解析策略，也不宣称所有大整数 CSV 从读取到分析全程保真。该限制与原报告已记录的 iterrows 类型提升属于同一既有路径。

### 4. 原缺陷断言与浏览器断言审核

通过完整文件比较确认，`test_numeric_only_csv_can_complete_frontend_upload` 的测试体和原正确性断言逐字保留，唯一删除是 KD-CSV-METADATA 的两行 xfail 装饰器。其他 12 项 strict xfail 的源码及独立参考断言未变。

Phase 4A 原浏览器步骤未削减，仅把已修复的 numeric.csv 从 Known Defect 变为实际成功验证，保留整数样本、Dataset 数量、元数据字段、零额外 Layer/result 及另一 Session 不变的断言。

Finalization 的首次浏览器复测在等待成功提示时超时，失败证据位于 `tests/logs/phase4a-bc67918044e5408c85588a216cd6a8d1/`。该次请求 HTTP 204、Dataset 和整数样本均已成功，失败 DOM 显示已上传的 numeric；Streamlit 自动重运行使一次性成功提示在轮询前消失。

因此只修正验收观察方式：上传前安装被动 DOM MutationObserver，捕获正式页面实际出现的同一句 `Erfolgreich geladen: numeric (points)`；仍要求该提示确实出现，并且不存在 numeric.csv 上传错误。没有注入成功 UI、制造状态或删除成功断言。观察结果保存为 numeric-upload-ui.json，完成后断开观察器。生产页面的 rerun 策略没有变化。

### 5. 完整 pytest 结果

实际执行：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_metadata_serialization.py -q -ra --tb=short
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short | Tee-Object -FilePath tests/logs/phase4b-finalization-regression.txt
```

最终边界测试为 **35 Passed，6.34s**；完整回归为 **244 Passed、12 strict xfail，共 256 项，28.64s，退出码 0**，0 意外失败、0 XPASS。相比报告基线只增加三个测试：int32、真实前端大整数 CSV、真实 SDK 请求编码的 MockTransport 验证。35 项新增测试 = 实施阶段 32 项 + 审核阶段 3 项。

SDK 扩展测试初版使用的 HTTP mock 未匹配当前 SDK 实际选用的客户端，离线保护成功阻止其连接尝试，产生 1 Failed / 1 teardown Error；未发生真实付费调用。依据本机 SDK 代码改为其兼容 HTTP 客户端的 MockTransport 后，全部测试通过。未绕过 socket 离线保护或修改生产 SDK 配置。

### 6. 浏览器验收结果与证据

实际执行 `.venv\Scripts\python.exe tests/phase4a_acceptance.py`。最终 **24 Passed、2 Not Run，退出码 0**，使用真实 Chrome 156 和受控 Mock LLM/OSM。

最终证据目录：`tests/logs/phase4a-e1bed124d23948878154228f2763fe10/`。

- numeric-upload-ui.json：success=true，实际提示文本完整，errors=[]。
- 实际模型样本中 value=3，解码类型为 int；注册 1 个 numeric Dataset，字段为 value，0 Layer / 0 ToolResult，会话 B 不变。
- 0 未捕获 JS 异常、0 console error、0 网络失败；37 条既有 warning 继续保留，未处理无关警告。
- Chrome 正常退出；两个自有 Streamlit 进程已回收；端口 57952、64031、55863 全部关闭，cleanup.errors=[]。
- 最终截图已实际查看。原始日志、截图、下载与 profile 按现有规则只保留本地。

| 最新证据相对路径 | SHA-256 |
|---|---|
| results.json | `d97a098e23ab3d51b6647f98769291a4df5a69b009a98c0fd74830b3071ffadc` |
| numeric-upload-ui.json | `e3b29c99109327ceae538c5a1d7c3ee5cea6d0210eafb59cb9f8411ff906cd39` |
| cleanup-check.json | `2b27c7ff0f39daa3b44dcc794c11dd6378fc29726793152d07fde62157307d9a` |
| 06-numeric-csv-fixed.png | `bf8408172d5df0cc620de007a2dfeab8e2a4d37c3c74193a3237fa4632953b4e` |
| cdp-events.json | `12b3957fb21c82a4f3cf9fa53679aa45ff9a7d93ea26316530e6cf17098443f2` |

### 7. 修改文件与本地提交

仅暂存并提交六个已审核的 Phase 4B 文件：agent/llm.py、frontend.py、tests/test_session_contracts.py、tests/phase4a_acceptance.py、tests/test_metadata_serialization.py、tests/PHASE4B_REPORT.md。

提交说明为 `fix: serialize numpy values in CSV metadata samples`。实际提交哈希以包含本报告的 Git Commit 和最终交付消息为准，报告不自引用哈希。staged diff、文件清单和敏感内容检查在提交前执行；不提交真实凭据、个人路径、日志、浏览器缓存或下载文件。

### 8. Git 状态要求

提交应仅改变 refactor/modular-architecture 的 HEAD；父提交为 `5f8d53d75b5e950d6b0b751463cb8a286d1e2e48`。main 保持 `dcad948d029842a555c609149b5cc226a1fdfe90`，backend.py 不存在。提交后检查工作区与暂存区干净，不 Push、Merge、Rebase、Reset 或修改其他仓库。

### 9. 未解决边界与最终判断

**本阶段最终审核通过**。KD-CSV-METADATA 的实际失败路径已修复，原断言与真实浏览器均通过；输入所有权、数值边界和模型请求编码有明确证据，正式分层结构未改变。

**Known Defect**：其他 12 项保留。既有超大 CSV 的上游类型提升、1000 字符截断、非有限原始属性的后续分析含义均未修复；无 Key 上传策略保持不变。**Not Run**：真实 DeepSeek 语义、在线 Nominatim、生产负载及下载后完整地图独立逐层视觉复核。浏览器仍依赖 CDN 静态资源和底图，不声明离线可用。

只完成 Phase 4B 最终审核和独立本地提交，停止等待用户审核。
