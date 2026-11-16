# 禾序 · 报销异常分类复核

财务共享组把历史人工复核标签训练成可解释的逻辑回归版本，再按异常概率排定待核票据；逐票评分会漏掉拆单报销，因此另做按报销人/商户/消费时间的关联筛查；最终判断和签核由人员完成。原生 HTML/CSS/JS 五个业务页面：样本池、训练配置、分类评估、风险复核、关联筛查。

## 真实模拟场景

模拟客户禾序咨询有 180 名顾问，财务共享组 6 人每周核对差旅报销。原流程是导出差旅明细、在工作簿标记疑点、随机抽取票据、在群里确认处理。三个痛点是复核顺序依赖个人经验，模型指标和业务阈值混为一谈，最终判断难以追溯到当时的样本和模型。上游输入报销金额、出行里程、节假日和人工历史标签；本项目替代样本整理、风险排序、版本评估与复核记录；下游由财务负责人结合原票据作最终处理。本项目不连接真实财务系统，也不自动拒付。

| 角色 | 主要页面 | 权限 | 典型动作 |
|---|---|---|---|
| admin | 全部、签核 | 读写、训练、复核、签核和审计 | 签核已填人工判断的任务 |
| analyst | 样本、训练、评估、复核 | 读写业务和提交训练，不能签核 | 补标签并生成复核候选 |
| viewer | 五页只读 | 可看与下载，不能写入或训练 | 查看模型和冻结评分 |

## 一日剧本

1. 08:50 viewer 查看 `HX-1115-001` 等历史样本，初始 300 笔（260 笔有人工标签、40 笔待标注），另附 8 笔 `HX-CR-*` 关联筛查演示样本。
2. 09:00 analyst 录入 `HX-QA-NEW`：金额 2600 元、里程 180 km、节假日“是”、标签“待标注”。
3. 09:10 对已标注记录填记录 ID 与完整字段，可修正人工标签；已有模型不变。
4. 09:20 创建“11月差旅风险基线”，复核阈值 0.5，Redis worker 执行固定 seed 的分层留出训练。
5. 09:30 查看 Precision/Recall/F1、混淆矩阵及 0.3/0.5/0.7 阈值敏感度。
6. 09:40 生成当前版本高风险待标注样本的复核任务，重复生成不会重复添加。
6b. 09:45 关联筛查：补录报销人、商户、消费时间，限额 5000 元；同人同商户 24 小时内连边，传递聚合后超限组进入组级复核。
6c. 09:50 补录桥接票导致旧组拆分：旧结论保留但标记依据过期，新组重新待核，未变组沿用原结论。
7. 10:00 analyst 复核原票据并填“核验正常”或“需进一步核实”及理由。
8. 10:10 admin 签核人工结论；10:20 viewer 下载 `claim-triage-<版本ID>.json` 留档。

## 算法与口径

输入金额（0–100000元）、里程（0–10000km）、节假日布尔与人工标签 0/1/空。派生特征“每公里金额”为 amount/(distance+1)，加一避免零里程除零。标签不进入特征矩阵。

已标注样本至少40笔且每类至少10笔，按标签分层分成75%训练/25%留出（seed=3106）。StandardScaler **只在训练集拟合**；Pipeline 后接 L2 LogisticRegression（C=1，max_iter=1000）。概率为 sigmoid(w·标准化特征+b)，阈值以上预测异常。Precision=TP/(TP+FP)，Recall=TP/(TP+FN)，F1=2PR/(P+R)，分母为零时指标为0。混淆矩阵合计等于留出集样本量。每个版本保留完整输入快照、训练/测试编号、训练集标准化均值、系数、留出概率与待标注概率、sha256 checksum。

## 关联筛查口径

逐票评分只看单张票据，拆单报销会把一笔大额消费拆成多张小票，分数都不高。关联筛查补这一层：

- 只有“报销人 + 商户 + 消费时间”三项齐全的样本参与连边；历史样本缺字段时单列展示，不臆测、不参与连边。
- 同人、同商户、消费时间相差不超过 24 小时的两笔样本连边；按传递关系聚合，跨日链式样本（A-B、B-C）会合入同一组，即使 A-C 间隔超过 24 小时。
- 组内金额合计**严格超过**限额才进入组级复核；未超限额的组保留供追溯，但不产生复核/签核动作。
- 每次筛查生成新版本。成员与每笔金额都没变的组沿用原结论（含已签核）；发生拆分、合并或替换的旧组保留原结论并标记 `stale` 与原因（`split`/`merge`/`replaced`），新组重新待核。限额变化时不再沿用旧结论。
- 样本补录/更正若改变金额或关联字段，会在同一事务内自动生成新筛查版本；任一步失败整体回滚，不会留下“样本已改、分组未算”的半套状态。
- 筛查状态 `pending → running → done/failed`；失败只落一条 failed 记录，旧版本分组原样保留，可重试。重试会先清空本版本的残留分组，重复投递不会重复建组。
- 组级复核与签核携带 `expected_version` 做条件更新：并发复核/签核只有一个成功，结论不会写到已拆分或过期的旧组。

阈值敏感度在同一留出集计算。反复选择阈值后，该集合不能继续当成最终独立验收集。本项目用于小规模业务演示，不包含额外生产验收集。标准化系数可解释模型关联，不作为因果结论。类别不足会以 failed 状态保存明确错误；重复编号返回409，无效金额/标签返回422。输入修订后须新建训练版本，旧快照不变。任务只对其快照里的待标注样本评分，不提供模型在线部署端点。评分仅排定人工复核顺序。

## 快速启动

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 自动 seed，也可显式执行两次验证
 docker compose exec -T backend python -m scripts.seed_demo
```

前端 http://localhost:18205 ，后端 http://localhost:19205/docs ，MySQL 映射17705。停止并删除演示数据：`docker compose down -v --remove-orphans`。Compose 顶层 name=claim-triage，五个服务各有健康检查；backend/worker 等待db+Redis健康，frontend等待backend健康。数据库、Redis队列、worker产物均有独立命名卷。worker单进程，启动时恢复 queued/running 的训练版本与筛查；筛查重试按筛查行加锁，重复投递不会重复建组，但整体仍不承诺多worker exactly-once。

账号为 `admin@demo.local` / `analyst@demo.local` / `viewer@demo.local`，密码均 `Demo123!`，JWT 8小时。密码PBKDF2使用每账号随机盐；JWT_SECRET为演示环境值。按角色后端强制检查，前端只读账号按钮禁用。

## 技术栈和目录

| 层 | 实现 |
|---|---|
| UI | 原生 ES modules、HTML、CSS；Noto Serif SC/Noto Sans SC 双字体 |
| API | Python3.11、FastAPI、Pydantic、SQLAlchemy、PyJWT |
| 算法 | scikit-learn1.6.0、NumPy2.2.0 |
| 异步 | Redis列表队列、独立Python worker与心跳 |
| 数据 | MySQL8.0、Redis7；Nginx反代 `/api/` 去前缀 |

`frontend/src` 下 api/stores/types/components/common/hooks/pages/router/utils/constants 分离网络、状态、契约、组件、表单、页面和枚举。`backend/src` 下 routes/controllers/services/models/schemas/middlewares/utils/constants/config 分离HTTP、查询、算法、持久化、校验、授权和配置。`backend/scripts/seed_demo.py` 用自然键只插入缺失账号/报销，不覆盖人工修订。`backend/migrations/001_initial.sql` 记录初始架构，实际初始DDL由SQLAlchemy metadata建立。关联筛查相关表（`screenings` / `correlation_groups` / `group_members` / `group_events` / `correlation_states`）同样由 metadata 建立，见 `backend/migrations/002_correlation.sql`。

共享枚举 ROLES/STATES/DECISIONS/LABELS 位于后端 `backend/src/constants/enums.py` 和前端 `frontend/src/constants/enums.js`；Pydantic枚举校验位于 `backend/src/schemas/business.py`，权限检查位于 `backend/src/middlewares/auth.py`。状态徽章/表格/表单字段为共享组件，两种中间件提供request ID和安全响应头；全局异常处理与审计记录横切业务。

本地开发：启动Compose的db/redis，将DATABASE_URL和REDIS_URL改指宿主映射；Python3.11虚拟环境安装 backend/requirements.txt，在backend目录 `uvicorn src.main:app --reload`。前端建议继续用Compose Nginx以保留同源API代理，也可使用可代理 `/api` 的本地静态服务器。单独开放静态文件不能替代API代理。

关联筛查接口：`POST /screenings`（限额）、`GET /screenings[/{id}]`、`POST /screenings/{id}/retry`、`GET /groups/{id}`、`PUT /groups/{id}/review`、`POST /groups/{id}/approve`、`GET /claims/{id}/correlation`。worker 同时消费 `claim:trainings` 与 `claim:screenings` 两个队列，启动时恢复 running 状态的筛查。

测试：后端 `PYTHONPATH=. python3 -m pytest tests/test_correlation.py`（覆盖跨日链式合并、桥接票更正后拆分、并发签核、失败回滚与重试不重复建组）。测试使用 SQLite 与 fakeredis，不需要 Docker。

环境变量：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO、NETWORK_SUBNET，均见.env.example。NETWORK_SUBNET默认10.253.106.0/24，可改为本机未用子网。

## 已知范围

逐条表单/API录入和完整字段修订；无CSV/Excel导入、删除、分页或外部审批通知。关联筛查只按“同人同商户 24 小时”这一口径连边，不做模糊地址/名称归一；跨天边界按 UTC 绝对时间计算，不含人工可调窗口。原始seed为确定性模拟数据（300 笔历史样本中每 37 笔保留一笔缺关联字段，用于演示信息不全单列），不能推断真实风险。风险复核任务不自动回写训练标签；须人工在样本页确认标签后重新训练。Google Fonts联网加载，离线字体回退。测试、截图和报告在源码外 `test-artifacts/gb-3106/`。

License：MIT，见 LICENSE。
