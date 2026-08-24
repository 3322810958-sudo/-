# Linux 云端部署与同步

当前交付不包含在线服务器。以下文件用于以后在自有 Linux 服务器部署。

## 服务器建议

- Ubuntu 24.04 LTS 或同等 Linux；
- 2 核 CPU、4 GB 内存起步，批量 OCR 建议 4 核、8 GB；
- 预留足够磁盘保存发票、备份和 Docker 镜像；
- 已安装 Docker Engine 与 Docker Compose；
- 准备域名并开放 80、443 端口。

## 部署

1. 将完整项目上传到服务器。
2. 进入 `deploy` 目录，将 `.env.example` 复制为 `.env`。
3. 把 `YXRT_SYNC_SHARED_SECRET` 改为至少 32 位的随机字符串。
4. 执行：`docker compose --env-file .env up -d --build`
5. 内网测试：`http://服务器IP:8765`
6. 生产环境使用 Caddy 或现有反向代理启用 HTTPS。`Caddyfile.example` 可作为起点。

首次构建会下载依赖和约 22 MB 的轻量 OCR 模型，完成后的容器可离线识别。业务数据库保存在 `deploy/runtime/data`，附件保存在 `deploy/runtime/uploads`。

## Windows 端连接

管理员打开“系统设置 → 云端同步”，填写：

- 服务器地址：例如 `https://finance.example.com`；
- 同步密钥：与服务器 `.env` 完全一致；
- 启用自动同步。

保存后点击“立即同步”。状态区会显示待上传数量、最近同步时间或错误信息。

## 运维

- 查看状态：`docker compose ps`
- 查看日志：`docker compose logs --tail 200`
- 更新版本：先备份 `runtime`，再执行 `docker compose up -d --build`
- 停止服务：`docker compose down`
- 服务器备份：定期备份整个 `deploy/runtime` 目录。

生产环境必须启用 HTTPS，不要在公网直接暴露 8765 端口；同步密钥不得发到公开群聊或代码仓库。
