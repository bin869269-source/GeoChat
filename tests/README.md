# GeoChat Phase 2 测试运行说明

仅建立测试基线。业务源码、工具签名和工作流保持不变，最终分层架构尚未实施。

## 环境与运行

本次验证环境为 Windows x64 / CPython 3.14.5。它是本次新建的测试环境，
不是此前团队运行环境的恢复结果。其他 Python 版本尚未验证。

```powershell
# 已有 .venv 时直接运行，不需要联网或任何真实 API Key。
.venv\Scripts\python.exe -m pytest tests -q -ra --tb=short --junitxml=tests/results.xml
.venv\Scripts\python.exe tests/summarize_results.py

# 新环境：用已安装的 Python 创建隔离环境，再安装测试所需依赖。
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r tests/requirements.txt
```

本次 .venv 使用 uv 创建，可能没有 pip；也可使用：

```powershell
uv pip install --python .venv\Scripts\python.exe -r tests/requirements.txt
```

安装依赖需要包源访问权限；安装完成后核心测试不访问互联网。
`requirements.txt` 固定直接依赖，`constraints.txt` 记录本次完整解析版本。
`.venv/`、uv 缓存、pytest 缓存不提交。
`tests/logs/`、`tests/results.xml` 为本地生成证据，也不提交。
`CASE_RESULTS.md` 是审核后保留的逐项基线记录；仅在有意更新基线时重新生成。

Streamlit 预期启动命令为：

```powershell
.venv\Scripts\python.exe -m streamlit run frontend.py
```

实际联网启动需要合法模型配置；本次未执行此命令、未进行真实模型验证。

## 测试边界

- `conftest.py` 执行完整原始 backend 模块，用假 Key 替换进程环境值，禁用 dotenv 读取，并替换 Streamlit secrets。
- 仅模型构造、模型回答及 Nominatim HTTP 响应被替换；LangGraph、ToolNode、装饰工具、GIS 库、随机森林和 Folium 都是真实执行。
- 阻止 HTTP 客户端与外部 socket 连接。仅允许 Windows asyncio 所需的字面量 loopback 连接；即使业务捕获了网络阻断异常，fixture 收尾仍报错。
- 每个测试重新加载 backend 并清空全局上下文；会话串扰测试在单个测试内保留真实缺陷，不以 fixture 隔离冒充生产隔离。
- `AppTest` 执行 Streamlit Python 服务端及控件事件；不执行浏览器 JavaScript，不验证地图瓦片、离线资源或真实下载交互。
- HTML 保存到 pytest 临时目录，现有固定导出路径的测试也切换到临时目录；不产生仓库根目录地图文件。

## Regression 与 Correctness

| 文件 | 内容 |
|---|---|
| `test_tools.py` | 七工具注册、参数名/默认值/必填参数、调用、异常、状态及数据 |
| `test_data_maps.py` | CSV/JSON/GeoJSON、点线面、地图、专题图、HTML、元数据模型替身 |
| `test_agent_session.py` | 实际 LangGraph、脚本化模型、多步骤、失败、状态、Streamlit 控件 |
| `test_gis_correctness.py` | 解析几何基准与 WGS84 椭球独立参考；正确性期望 |
| `test_known_defects.py` | 有确定复现的接口、渲染及输入校验历史缺陷 |

GIS 参考包括：已知米制坐标的矩形面积和间距；WGS84 Geod 直接解生成的 100m 点对；
Geod 反解检验 Buffer 半径；椭球多边形面积；明确面内/顶点/面外点及重叠区计数。
Geod 与业务采用的平面距离/面积计算路径不同，但底层仍共享 PROJ；不是外部测绘认证。
回归测试中复算旧 EPSG:3857 Buffer 的断言明确标注为 characterization，不充当精度参考。

已知缺陷采用 `xfail(strict=True)`，并限定预期异常类型：

- `XFAIL` = Known Defect，绝不等于 Passed。
- `XPASS` 导致失败，修复算法后必须审核并移除对应标记。
- 其他异常和未标记失败会导致非零退出码。
- 若要观察历史缺陷的真实失败，可运行 `pytest --runxfail`；不将该模式的失败计入普通基线通过率。

边界点排除、重叠面重复计数、部分结果保留及历史缺工具轨迹被记录为当前行为，
不表示已经批准这些业务语义。GIS 容差仅是本次测试标准，不能替代产品精度验收。

## 后续重构如何使用

每批修改先运行全集，再检查 Known Defect 是否增加或出现 XPASS。
迁移到用户确定的 `agent/`、`gis/`、`maps/`、`core/` 后，优先调整测试导入适配，
保留输入数据、独立参考值和用户行为断言；新接口迁移完成前可保留 backend 兼容门面。
GIS 算法修复与文件搬迁分别审核，不降低断言或放宽容差以消除回归。

最终状态见 `PHASE2_REPORT.md`；逐个测试状态见 `CASE_RESULTS.md`，机器结果见 `results.xml`。
