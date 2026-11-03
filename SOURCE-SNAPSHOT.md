# 公开候选的源码与复现边界

本说明针对PORTFOLIO-PRESENTATION-01的**独立公开候选**。私有工程仓库另含完整历史、数据和验收原件；不要把它的原Git历史直接公开。

候选只含经审查的阅读源码、本文档、脱敏截图/证据卡和未激活工作流模板。它不是完整可启动发行版，不承诺clone后重现历史PG、GLPI、真人决定、模型响应或账单。源码阅读快照基于`d53a835615b1feebae9d55eaed95b7aa05c43734`；历史真实Agent执行另绑定`354c17941c5d44a84ad27ad650df1ee58e09c7fb`，不能混为同一运行。

| 阅读目标 | 主要源码 |
|---|---|
| Agent节点与工具约束 | `backend/agent_graph.py`、`tools.py`、`knowledge.py`、`agent_trace.py` |
| 可信审批与模拟权限 | `backend/access_graph.py`、`access_service.py`、`permission_simulator.py` |
| 事件、归属与持久回执 | `backend/ticket_events.py`、`notification_receiver.py`、`adapter.py`、`service.py` |
| 身份、持久化与费用边界 | `backend/auth.py`、`checkpoints.py`、`model_gateway.py`、`cost.py`、`billable_bounds.py` |
| 产品界面 | `frontend/src/`；实际React/TypeScript页面，不是这次重新开发的网站 |

这些文件保留当前原始源码字节，未为公开展示重新设计业务。`SOURCE-MANIFEST.json`列出候选选入的原路径及SHA。某些内部导入依赖未被选入（例如真实历史风险接受/一次性执行器），不可把选段当独立可执行包。

**刻意不包含：** 原`.git`、`.env`、私有账号/凭据、运行日志、SQLite/PG/GLPI卷、完整执行输入、原审批/账单原件、全部评估样本和私有初始化/付费运行脚本。不要通过删除HOLD或补伪造审批来“修复运行”。

本地已有演示入口见[首页](README.md)，由所有者在已授权隔离环境按原交接维护。公开读者可以直接阅读文档、图片和源码，不能获得企业或本机服务权限。首次复现完整环境需另做合成账号、受控初始化及安全审查，不是本候选已完成的承诺。

公开图片与事实来自历史原件：相应原文件SHA在`docs/assets/portfolio/`清单，原路径/UUID映射由所有者私有保存。SHA证明关联及完整性，不把人工挑选的证据卡冒充独立认证。通知与审批截图保留真实状态；遮挡/裁切和结构示意均明示。

正式开源许可待所有者决定，本轮没有生成授权签名或自动授予MIT等许可。见[第三方归属](THIRD-PARTY-NOTICES.md)。
