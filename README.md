# 燕翔车队经费管理系统 V2.1.1

面向大学生方程式车队的离线优先经费、发票、AA 分摊与报销管理软件。Windows 桌面端可独立运行，也可部署到 Linux 服务器进行多端同步。

## V2.1 主要功能

- 清晰深色、护眼浅色、赛车蓝高对比三种模式，并支持本机自定义快捷键。
- 删除、停用、回溯与恢复等重要操作使用软件内居中确认框。
- 发票图片、PDF、TXT 与 ZIP 批量导入；PP-OCRv5 完全离线识别。
- 自动填写发票号码、日期、销售方、金额、税额、产品类型与费用分类。
- 管理员可编辑分类关键词规则，并结合商家历史人工修正进行本地学习。
- 全队、指定成员、个人承担、等额与权重 AA 分摊，金额精确到分。
- 成员独立账号、管理员账号与公共只读账号。
- Wallpaper Engine 创意工坊扫描；图片、GIF、MP4、WebM 背景与登录轮播。
- 管理员可编辑登录轮播的顺序、标题、停留时间、切换效果与启停状态。
- SQLite WAL 长期存储、完整备份、审计日志、版本保护点及管理员回溯。
- 离线队列、附件 SHA-256 校验与可选云端双向同步。
- 简体中文赛车数据驾驶舱，电脑和手机浏览器自适应。

## 本地开发

需要 Windows 10/11 64 位与 Python 3.12。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\prefetch_ocr_models.py
.\.venv\Scripts\python.exe -m app.desktop
```

不安装 OCR 组件时，仍可开发账务、权限、界面与同步功能：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-core.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --import-mode=importlib -p no:cacheprovider
```

## 默认开发账号

首次运行会生成可删除的演示数据，并创建开发用账号。所有管理员和成员账号首次登录后必须修改账号与密码。正式部署前请同时配置新的同步密钥。

## 数据安全

数据库、附件、OCR 模型、构建目录、备份包和环境变量均已加入 `.gitignore`，不会提交到源码仓库。发布前仍应执行一次敏感信息检查。

## Wallpaper Engine 兼容范围

视频、图片和 GIF 壁纸可直接在软件内播放。Wallpaper Engine 的场景、网页与应用型壁纸采用本地预览图，避免执行第三方脚本或专有场景包。

## 开源许可

程序源代码采用 [MIT License](LICENSE)。`app/static/assets/` 中的车队名称、队标与品牌素材保留其原始权利，不属于 MIT 授权范围；二次分发时请替换为自己的品牌素材。
