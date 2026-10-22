# GeoChat — Phase 3A: GIS Core Extraction

## Part I — GIS Extraction Summary

在 `refactor/modular-architecture` 上实施；开始时工作区干净，HEAD 为 Phase 2 冻结提交
`29234d28fde906f997cb0f4de9133bde4fc00043`。本批经 Finalization 审核后以独立本地提交固定。

提取确定性解析和空间计算，不修改生产计算语义。GIS 包可以直接导入并接收文件流或带 CRS 的
GeoDataFrame，不需要图层总线、模型、页面或地图对象。原来的七个工具留在 backend 中，
继续负责按名称找图层、提交列表修改、生成地图配置及成功/错误文本。

## Part II — New Files and Responsibilities

| 文件 | 职责 |
|---|---|
| `gis/__init__.py` | 独立 Python 包初始化，无应用初始化副作用 |
| `gis/data_io.py` | CSV/JSON/GeoJSON 文件流解析、要素转 GeoDataFrame、属性标量准备 |
| `gis/geometry.py` | Buffer 与全部要素对的几何最短距离 |
| `gis/statistics.py` | 面积、within 点计数、密度 |
| `tests/test_gis_core.py` | 17 个新增用例：独立导入/执行、无默认输入修改、兼容入口、CRS 要求 |

不创建 agent、maps、core 等后续业务模块，不增加第三方依赖。

## Part III — Migrated Functions and Compatibility Mapping

| 原实现 | 新入口 | 兼容安排 |
|---|---|---|
| backend.extract_features_to_gdf | gis.data_io.extract_features_to_gdf | backend 直接重导出，同签名及错误行为 |
| backend.parse_uploaded_file | gis.data_io.parse_uploaded_file | backend 直接重导出，前端无需修改 |
| backend.clean_gdf_for_geojson | gis.data_io.clean_gdf_for_geojson | 新入口默认复制；旧入口显式 inplace=True 保持原地修改 |
| calculate_buffer_analysis 内计算 | gis.geometry.buffer_geometries | 原工具保留查找、命名、样式、序列化和状态提交 |
| calculate_feature_distances 内计算 | gis.geometry.pairwise_distances | Core 返回行位置及米制距离；名称、表格、max_pairs 输出限制留在原工具 |
| calculate_area_and_density 内面积 | gis.statistics.calculate_area | 返回带 area_km2 的副本 |
| 同工具内空间连接计数 | gis.statistics.count_points_in_polygons | 返回与面索引一致的整数 Series |
| 同工具内密度 | gis.statistics.calculate_density | 接受带 area_km2 的面和点，返回含计数/密度的副本 |

计算接口约定：

- GeoDataFrame 必须声明 CRS；Core 按原实现重投影，不从 Session 推断 CRS。
- Buffer 在 EPSG:3857 中施加参数米数，结果 EPSG:4326；不能把投影米直接解释为精确地面距离。
- 距离使用 EPSG:25832、保留 0.1m 舍入与舍入后稳定排序；结果索引为零起始行位置，而非图层 ID。
- 面积使用 EPSG:25832，单位 km²，保留四位小数。
- calculate_density 要求已有 area_km2；输出 dichte_pro_km2 单位为点/km²。
- 解析消费文件流当前位置；不更改流内容。属性清理只有明确 inplace=True 才修改传入帧。
- 上传模型元数据建议、随机森林、HTTP 城市搜索仍留在 backend，本批不迁移。

## Part IV — Dependency Independence Verification

**Passed**：新进程使用 `.venv` Python 的 `-I` 模式，不继承测试假 Key 或模型替身；
只传入系统运行必需的环境变量及 PROJ_NETWORK=OFF，不读取 .env。

测试主动禁止导入 backend、frontend、agent、Streamlit、LangGraph、LangChain、OpenAI、
Folium、Branca、dotenv，同时禁止 socket 连接；随后真实导入三个 GIS 模块并执行文件解析、
Buffer、Distance、Area、Count、Density，检查禁用模块未加载。计算库没有被 Mock。

输入不变测试涵盖原始字典、GeoJSON、普通文件流内容、GeoDataFrame 属性/几何/CRS/索引、
面积中间结果和点输入。显式 inplace 清理及旧入口的原地行为单独验证。

## Part V — Regression Test Results

实际运行命令：

```powershell
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
```

| 时点 | 总用例 | Passed | strict xfail | 意外失败 / XPASS |
|---|---:|---:|---:|---:|
| 修改前复核 Phase 2 | 104 | 91 | 13 | 0 / 0 |
| 仅完成提取后 | 104 | 91 | 13 | 0 / 0 |
| 提取及新增独立测试完成 | 121 | 108 | 13 | 0 / 0 |

提取完成时全集耗时 8.06 秒，退出码 0。通过 Tee-Object 保留控制台输出到
`tests/logs/phase3a-run.log`，该路径沿用现有忽略规则，不纳入版本控制。

Finalization 按相同命令复跑：**108 Passed、13 strict xfail、0 意外 Failed、0 XPASS**，
共 121 个用例，耗时 7.94 秒，退出码 0。本地日志为 `tests/logs/phase3a-freeze-run.log`，不提交。

104 个冻结测试未修改；包括七工具 schema/注册、真实图循环配合 Mock 模型、
数据解析、地图 HTML、Streamlit 服务端控件与 GIS 正确性测试。新增 17 个测试全部 Passed。
真实模型、在线服务和浏览器端 JavaScript 均未验证。

## Part VI — GIS Correctness and Known Defects

原有 13 个 strict xfail 保持不变，没有移除、放宽或取消断言，未增加或解决任何 Known Defect。

保留：CRS 声明覆盖；Web Mercator Buffer 地面距离误差；小面归零及密度精度问题；
空/零面积/自交面未拒绝；全局 Session 串扰；Buffer 查找遗漏原始层；缺点图层静默退化；
线宽 0；单 Feature 点渲染；非法纬度接受。边界点排除和重叠面分别计数也保持原行为。

原正确性测试仍使用解析几何及 Geod 独立计算路径；提取后从旧工具经新 GIS 实现执行。
这些参考与业务共享部分 PROJ 底层，不等同于外部测绘认证。测试通过不代表历史缺陷解决。

## Part VII — Actual Diff and Modified Files

- 修改：`backend.py`，仅直接相关导入、三个 GIS 工具内计算调用及数据辅助兼容入口。
- 新增：`gis/__init__.py`、`gis/data_io.py`、`gis/geometry.py`、`gis/statistics.py`。
- 新增：`tests/test_gis_core.py` 和本报告。
- frontend、原 104 个测试及依赖声明无差异；Phase 2 报告/逐项结果表保留为历史冻结记录。
- AST 与基线比较确认非 GIS 函数、模块状态、模型配置、TOOLS 及 LangGraph 构图节点未改变；
  修改的三个工具签名和装饰器也一致。
- `git diff --check` 通过。Finalization 仅暂存上述七个文件并建立独立本地提交；不 Push、Merge 或修改 main。

Finalization 复核：没有来源不明或不属于本批的修改；121 个用例唯一且可发现，13 个 strict xfail
未修改；新增测试均有有效断言或预期异常检查。GIS 模块导入依赖仅为标准库及已有计算依赖。
提交候选文件未发现密钥、本机绝对路径、缓存、虚拟环境或无关生成文件。

backend 原带 Windows 只读属性；为执行授权编辑临时解除，结束时恢复该属性。

## Part VIII — Remaining Risks

- 固定投影、早期舍入、无效输入、边界及重叠语义保持原样，后续应独立批准和修复。
- 全量两两距离计算仍为 O(n²)；max_pairs 只限制工具输出。
- 后端仍在导入时初始化模型，工具仍通过进程全局总线提交业务状态；本批只使 GIS 包独立。
- 固定 HTML 导出路径和地图格式兼容风险仍存在。
- 新统计函数使用 area_km2 等旧属性名，尚未引入最终 Shared Contracts 或 Dataset/Layer 身份设计。

## Part IX — Readiness for Phase 3B

**具备提交 Phase 3A 审核及下一批迁移的测试条件。** 无新增测试阻塞，七工具和地图回归保持。
后续范围与顺序待用户批准；本次未实施 Agent、Map Renderer、Session 的完整重构，未开发 GNN。
