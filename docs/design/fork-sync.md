# 集中同步

每 15 分钟用只读 App 令牌枚举 installation repositories，筛选 couai 自有、公开、未归档的 fork。试运行先限制 GestaltX 与 agent-purge；验证后清空 include，新 fork 下一轮自动接管。

每个目标独立获取只限该仓库的 1 小时安装令牌，具备 Contents、Workflows、Administration 写权限。关闭目标 Actions，合并上游默认分支，复制原始 tag 对象，再同步已发布 Releases 与附件。只运行中央仓库代码，不运行上游脚本。标签冲突和合并冲突报错，不强推、不删除上游消失的版本。单库失败不阻塞其他仓库。

Releases 复用已验证的复制逻辑：上传前后核对 SHA-256，新版本先草稿后发布，已有附件按哈希跳过。所有请求均作用于目标 fork，不写上游。

中央仓库公开运行标准 Linux runner；私钥仅放中央 Actions Secret。月度状态提交用于保持调度仓库活跃。没有数据库、服务器或每个 fork 的密钥配置。月度状态不是同步成功证明，具体结果看 Sync forks 运行记录。

迁移先验证两个仓库，成功后关闭旧同步入口、移除本工具创建的旧 Deploy Key 与 Secret；再开放全部公开 fork。旧同步文件暂留 Git 历史，避免改写用户分支。
