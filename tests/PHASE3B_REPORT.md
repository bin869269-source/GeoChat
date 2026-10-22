# GeoChat — Phase 3B: Map Renderer Extraction

## Part I — Map Renderer Extraction Summary

开始时分支为 `refactor/modular-architecture`，HEAD 为
`f89f3c01d25d3605c5f1302f454573876a578c45`，工作区干净。
修改前完整复跑：108 Passed、13 strict xfail。

地图构建和渲染迁入 maps/renderer.py。默认中心、zoom、样式、色带、图层顺序、
最后有效图层 bounds 策略、显式视图覆盖和空地图行为均保留。
不重构 Agent、Session、Dataset/Layer 或 GIS Core。

两项局部调整：

1. 在两个 GeoJSON 渲染入口将数据复制后交给 Folium，避免 Folium 在缺少唯一属性时
   注入 feature.id 并修改调用方数据；渲染副本仍按原规则生成 id。
2. HTML 下载改用 UTF-8 内存字节，不再读写固定 map_export.html。按钮、下载标签、文件名
   geogpt_map.html、MIME text/html 和先创建地图/添加标题的流程不变。

## Part II — New Files and Migrated Functions

新增 maps/__init__.py 和 maps/renderer.py。迁移的公开名称及参数保持原样：

| 入口 | 职责 |
|---|---|
| DEFAULT_MAP_STYLE | 原默认样式 |
| create_layered_map | 底图、图层分派、bounds、LayerControl |
| add_points_layer | Marker 点图 |
| add_heatmap_layer | 加权 HeatMap |
| add_choropleth_layer | 连续色带、缺失值颜色、图例 |
| add_geojson_layer | GeoJSON 点/线/面/MultiPolygon 等 |
| Titel_hinzufügen | 原 HTML 地图标题 |
| render_map_html（新增） | 将显式 Folium 地图首次渲染为 UTF-8 HTML bytes |

backend 从新模块重导出上述名称，保留旧调用入口；frontend 仅新增 HTML helper 导入和
局部替换导出按钮的数据生成路径。地图模块不查找图层总线，不隐式提交业务状态。

## Part III — Dependency Independence

新模块直接依赖仅为标准库 copy/typing、Folium、Branca；未增加依赖。
独立进程测试使用 Python -I 模式与最小系统环境，不传入真实或测试假 Key。
禁止导入 backend、frontend、agent、Streamlit、LangGraph、LangChain、OpenAI、dotenv、gis，
同时禁止 socket 连接；真实导入 renderer、创建点图、添加标题、生成 HTML，并验证没有禁用导入或网络尝试。

地图和 HTML 测试不访问真实模型、Nominatim、瓦片或 CDN。HTML 仍引用浏览器侧资源，
生成过程不需要网络，不表示导出 HTML 已具备完整离线显示能力。

## Part IV — Compatibility Review

- 所有旧地图函数通过 backend 重导出，兼容测试检查对象一致性和默认样式。
- AST 对比：六个迁移函数在去除两个输入 deepcopy 包装后与原实现一致；非地图 backend
  顶层代码、七工具、模型配置、状态总线及 LangGraph 连线均未改变。
- GIS 包、GIS 测试及依赖声明无差异。
- 原导出 AppTest 从检查临时磁盘文件改为捕获真实 download_button 的入参，
  检查完整 bytes、标签、下载文件名和 MIME，并确认没有 map_export.html。
  实际 Streamlit download_button 仍执行，不以替身代替控件实现。
- 六种地图分别对相同初始地图状态的副本执行内存 helper、Folium BytesIO save、磁盘 save，
  验证完整 UTF-8 字节相等（包括非 ASCII 标题）。
- Folium 连续渲染同一对象会追加生成脚本。因此字节比较使用同一未渲染地图的独立副本，
  保留相同元素 ID，各比较首次渲染；没有通过忽略内容、正则删除脚本或放宽断言来通过。
- Helpers 按原约定修改传入地图对象；GeoJSON 和图层数据不被意外修改。

## Part V — Map Tests and Full Regression Results

实际执行：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_map_renderer.py -q --tb=short
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short
```

| 范围 | 总数 | Passed | strict xfail | 意外 Failed / XPASS |
|---|---:|---:|---:|---:|
| 既有回归集（仅导出测试适配内存路径） | 121 | 108 | 13 | 0 / 0 |
| 新增地图模块测试 | 25 | 25 | 0 | 0 / 0 |
| 最终全集 | 146 | 133 | 13 | 0 / 0 |

新增测试单独运行耗时 5.09s；最终全集 9.60s，退出码 0。
最终全集控制台日志本地保存在 tests/logs/phase3b-final-run.log（忽略，不提交）。

Finalization 使用相同完整测试命令复核：**133 Passed、13 strict xfail、0 意外 Failed、0 XPASS**，
146 个用例，耗时 10.08s，退出码 0。冻结审核日志本地保存在 tests/logs/phase3b-freeze-run.log，
不纳入提交。

新增测试覆盖：无 Key/无应用依赖/无网络独立运行；Point/Line/Polygon/MultiPolygon；
HeatMap/Choropleth；默认及自定义样式；图层顺序/LayerControl；默认视图/最后图层 bounds/
显式视图；空地图/空图层；缺失专题值；标题/HTML；错误输入；数据不变；旧入口；导出字节一致性。

实现中曾出现并修正：PowerShell 编码导致迁移脚本中断的三个导入失败；新增测试夹具的
重复对象别名、BytesIO 被 save 默认关闭，以及同对象多次渲染造成的比较问题。
未删除测试、放宽已知缺陷断言或改变生产投影来消除失败。初轮新增测试失败日志本地保留为
tests/logs/phase3b-run.log。前期失败不计为最终通过结果。

## Part VI — Known Defects and Unverified Behavior

原 13 个 strict xfail 的代码/标记/断言未修改，全部仍为 Known Defect，不能计入 Passed。
包括单 Feature 点渲染、线宽 0、GIS CRS/Buffer/面积/密度/异常几何及会话等历史问题。
固定 HTML 文件覆盖风险由本批独立的内存导出变更移除；它并不等于 Session 隔离已经完成。

未验证：浏览器 JavaScript 实际执行、真实地图视觉、CDN/瓦片可用性、浏览器下载点击及完整离线显示。
标题 HTML 插入规则、异常输入处理和旧地图格式兼容缺口未扩展修复。
深复制增加复杂 GeoJSON 的内存占用；本阶段没有大数据性能基准。

## Part VII — Git Diff and Modified Files

| 文件 | 变化 |
|---|---|
| backend.py | 删除内联渲染实现和对应依赖导入，改为重导出 |
| frontend.py | 导入 render_map_html，局部替换共享文件导出 |
| maps/__init__.py | 新增包初始化 |
| maps/renderer.py | 提取渲染实现及新增内存 helper |
| tests/test_agent_session.py | 仅调整导出测试；其他函数保持一致 |
| tests/test_map_renderer.py | 新增 25 个地图测试 |
| tests/PHASE3B_REPORT.md | 本报告 |

没有修改 GIS Core、其他历史报告、依赖配置或历史版本目录；没有新增 Agent/Session 模型。
git diff --check 通过。原 backend/frontend 的 Windows 只读属性在授权编辑期间临时解除，结束时恢复。
Finalization 仅暂存上表七个文件，检查 staged diff 后创建独立本地提交
`refactor: extract independent map renderer`；不 Push、Merge、Rebase、Reset 或修改 main。

Finalization 审核通过：146 个独立用例正常发现，13 个 strict xfail 的标记与合理断言未放宽。
六个渲染函数在去除已说明的 deepcopy 包装后与原实现 AST 一致；其余 backend 业务代码
及导出测试之外的 Agent/Session 测试未改变。提交候选文件未发现密钥、本机绝对路径、
缓存或无关生成文件；没有来源不明或其他成员的修改。

## Part VIII — Readiness for Phase 3C

具备提交 Phase 3B 审核的条件：地图包独立，兼容和回归通过，没有新增测试阻塞。
下一阶段范围需用户确认。本次到此停止，不开始 Phase 3C。
