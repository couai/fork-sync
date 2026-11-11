# fork-sync

集中同步 couai 的公开 forks：默认分支、原始 Tags、已发布 Releases、附件。每 15 分钟检查一次，也可在 [Actions](https://github.com/couai/fork-sync/actions/workflows/sync.yml) 手动运行或 dry run。

## 自动接管

`config.json` 的 `include` 为空时，下一轮自动发现新 fork。试运行阶段仅包含 `GestaltX`、`agent-purge`。`exclude` 支持仓库名或 `owner/name`。非 fork、非本人仓库、私有、归档或禁用的仓库不处理。

同步使用 merge，不强推；冲突时该仓库失败，其他仓库继续。标签同名但对象不同会报错。已消失的上游分支、标签、Release 不自动删除。Release 上传前后核对 SHA-256；附件不变时不下载、不上传；新版本先完成草稿附件再发布。GitHub 上的发布者、发布时间和下载计数属于镜像记录。

目标 fork 按镜像管理，自动关闭其 GitHub Actions，避免上游构建覆盖复制的成品。同步代码只运行在本仓库。需要自行开发、运行 CI 的 fork 应加入 `exclude`。

## 一次性授权

创建自有 GitHub App，安装到 couai 并选择 **All repositories**。这会覆盖账号当前和未来仓库，程序仍仅选择符合配置的 fork。权限为 Metadata read、Contents write、Workflows write、Administration write；后者用于关闭目标仓库 Actions。App 不需要 webhook。

中央仓库配置：

- Variable `APP_CLIENT_ID`：App Client ID。
- Secret `APP_PRIVATE_KEY`：App 的 PEM 私钥。

发现任务只获取 Metadata read 令牌；每个同步任务使用仅授权该目标仓库的短期令牌，结束后撤销。私钥只存中央仓库 Secret，fork 中不存密钥，不执行上游脚本。

## 调度与验证

`Sync forks` 提供每库结果摘要；失败由 GitHub Actions 通知设置处理，无变化时不额外发消息。`Monthly status` 每月更新 `status/scheduler.json`，保持公开调度仓库活跃，降低 60 天无活动停用定时任务的风险。GitHub 调度可能延迟。

Python 3.12+，仅标准库：

```sh
python3 -m unittest discover -s tests -v
```

实际验证应包括：首次补齐、无变化复跑、默认分支和标签一致性、Release 元数据与全部附件 SHA-256 一致。设计见 [docs/design/fork-sync.md](docs/design/fork-sync.md)。
