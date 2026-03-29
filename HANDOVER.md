# 批量易优 V2 (AutoCMS) - 项目交接文档

## 1. 项目概述与核心需求
**项目名称**：批量易优 V2 (AutoCMS API Service)
**项目定位**：企业级 SEO 站群系统，主要用于批量部署、管理基于 EyouCMS 的站点，并提供配套的 SEO、运维管理功能。
**核心功能需求**：
1. **服务器管理**：批量管理宝塔服务器节点（录入主控 IP、宝塔 API 密钥、可用 IP 池等）。
2. **资源库管理**：
   - **TDK 库**：批量管理站点的 Title、Description、Keywords 配置方案。
   - **模板/核心包库**：管理 EyouCMS 核心包和网站模板，支持大文件直传至华为云 OBS。
3. **批量一键上站**：
   - 选择目标服务器、模板、TDK 方案。
   - 输入 `域名|IP` 列表，系统通过 Celery 异步任务，调用宝塔 API 和 SSH 自动完成环境创建、数据库创建、源码下载（从 OBS）、解压、安装及 TDK 注入。
4. **站点管理 (首页)**：展示已部署站点的列表、状态（部署中/成功/失败）、绑定 IP、TDK 摘要等，支持快捷进入后台和删除记录。
5. **待开发模块**：采集发布、运维大屏、SEO 数据报表、角色/用户/日志等常规后台功能。

## 2. 技术栈与架构
- **后端**：Python + FastAPI + SQLAlchemy (ORM) + Pydantic (数据校验)
- **数据库**：PostgreSQL (生产环境) / SQLite (本地开发测试)
- **异步任务**：Celery + Redis (用于处理耗时的 SSH 和宝塔 API 部署流程)
- **对象存储**：华为云 OBS (兼容 AWS S3 协议，使用 `boto3` SDK)
- **前端**：HTML + Vue 3 (CDN 引入) + TailwindCSS (CDN 引入)
- **部署方式**：Docker Compose (`docker-compose.yml` 包含 db, redis, backend_api, celery_worker)

## 3. 当前项目结构
```text
.
├── backend/                  # 后端代码目录
│   ├── main.py               # FastAPI 入口文件
│   ├── api/routers/          # API 路由 (deploy, server, site, tdk, template)
│   ├── core/                 # 核心组件 (database, config, obs_client, ssh_client, bt_api_client)
│   ├── models/               # SQLAlchemy 数据库模型 (server, site, asset)
│   ├── schemas/              # Pydantic 数据模式 (server, site, deploy, asset)
│   ├── services/             # 业务逻辑服务 (deploy_service: 封装宝塔和SSH操作)
│   ├── worker/               # Celery 异步任务 (celery_app, deploy_tasks)
│   ├── requirements.txt      # Python 依赖
│   └── Dockerfile            # 后端 Docker 构建文件
├── design/                   # 设计原型目录
│   └── index.html            # 前端产品原型 (包含所有规划的模块 UI)
├── fronttype/                # 当前实际开发的前端目录
│   └── index.html            # 已实现的前端页面 (Vue 3 单文件组件风格)
├── docker-compose.yml        # 生产/测试环境编排文件
└── dev.sh                    # 开发辅助脚本
```

## 4. 当前进展 (已完成的功能)
1. **服务器管理**：增删查改、宝塔 API 连通性测试。
2. **TDK 管理**：文本框大批量解析导入、列表展示、删除。
3. **模板/核心包管理**：支持将 ZIP 包通过 FastAPI 接收并使用多线程 (`run_in_threadpool`) 直传至华为云 OBS，信息入库。
4. **批量上站核心逻辑**：
   - 前端表单组装数据提交到 `/api/deploy/batch`。
   - 后端在 `sites` 表中创建初始状态为 `deploying` 的记录。
   - 触发 Celery 任务 `process_single_site`，通过宝塔 API 建站、建库，通过 SSH 下载 OBS 资源并执行 SQL 注入。
   - 任务完成后更新 `sites` 表的状态为 `success` 或 `failed`（并记录错误信息）。
5. **站点管理 UI**：前端已实现站点列表展示、按服务器筛选、状态展示。

## 5. 关键注意事项与坑点 (踩过的坑)
1. **OBS 客户端配置**：华为云 OBS 兼容 S3，但必须指定 `region_name`（如 `ap-southeast-1`），且 `endpoint_url` 必须带 `https://` 前缀。上传文件时需使用 `put_object` 传递字节流。
2. **大文件上传阻塞**：FastAPI 中如果直接同步上传大文件到 OBS 会阻塞事件循环，必须使用 `run_in_threadpool` 包装 `obs_client.upload_file_bytes`。
3. **Celery 任务状态同步**：Celery 任务在单独的进程中运行。为了更新部署状态，必须在 `deploy_tasks.py` 中独立创建数据库会话 (`SessionLocal()`)，并在 `try...except...finally` 中正确提交状态并关闭连接。
4. **前端架构**：目前前端没有使用 Vite/Webpack 等构建工具，而是直接在 `fronttype/index.html` 中通过 CDN 引入 Vue 3 和 TailwindCSS。所有的组件逻辑都在这一个文件里。后续如果代码量过大，可能需要考虑拆分或重构为标准的 Vue CLI/Vite 项目。
5. **Redis 架构兼容**：在 `docker-compose.yml` 中，Redis 启动命令加了 `--ignore-warnings ARM64-COW-BUG`，这是为了防止在 ARM64 架构（如 Mac M 系列芯片）下 Redis 崩溃。

## 6. 接下来的开发方案 (To-Do List)
接手该项目的 AI 需要按照以下顺序继续推进（参考 `design/index.html` 的原型）：

### 阶段一：完善与健壮性优化
- [ ] **站点管理增强**：实现站点列表的分页功能；实现“批量删除”站点的功能（目前前端有按钮但未对接接口）。
- [ ] **部署日志回传**：目前 Celery 任务只记录了最终的成功/失败状态。需要实现实时日志回传（如通过 WebSocket 或 Redis Pub/Sub），让前端能看到类似原型中的“实时动态日志”。

### 阶段二：新模块开发 (按原型设计)
- [ ] **采集与 AI 管理 (`scrape_mgt`)**：设计文章采集规则模型，对接大模型 API 进行内容伪原创/生成，并自动发布到指定的 EyouCMS 站点。
- [ ] **SEO 数据报表 (`seo_monitor`)**：对接百度等搜索引擎的 API，或者通过爬虫，定期获取各个站点的收录量、蜘蛛抓取频次，并在前端展示图表。
- [ ] **全盘运维大屏 (`ops_screen`)**：通过 SSH 或探针脚本，实时获取各服务器的 CPU、内存、硬盘使用率以及 Ping 延迟，在前端大屏展示。
- [ ] **系统操作日志 (`action_logs`)**：记录用户的关键操作（如删除服务器、批量上站等）供审计。

### 阶段三：前端工程化 (可选)
- [ ] 当前 `fronttype/index.html` 已经超过 600 行，随着新模块加入会越来越难以维护。建议将其重构为标准的 Vue 3 + Vite 项目，按路由和组件拆分代码。

---
**交接寄语**：项目的基础骨架（数据库、API、Celery 异步队列、OBS 存储）已经搭建完毕并跑通了核心的“批量上站”闭环。接下来的重点是横向扩展业务模块和提升系统的可观测性。祝编码愉快！