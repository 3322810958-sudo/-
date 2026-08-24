# 贡献指南

感谢参与改进燕翔车队经费管理系统。

1. 从 `main` 创建简短、明确的功能分支。
2. 不要提交数据库、发票、账号数据、同步密钥、OCR 模型或构建产物。
3. 功能修改需补充或更新自动化测试。
4. 提交前运行：

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests -q --import-mode=importlib -p no:cacheprovider
   ```

5. 界面修改需同时检查 1280×720 与窄屏布局。
6. Pull Request 请说明变更目标、测试结果及数据库兼容性。
