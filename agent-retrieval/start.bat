@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "data\meta.json" (
  echo [ERROR] 缺少 data\ 目录。
  echo   - 用 full 包可以直接跑；
  echo   - 只拿到 code 包时，先按 README 运行 tools.build_knowledge 构建。
  pause
  exit /b 1
)
echo 安装依赖...
python -m pip install -r requirements.txt
echo.
echo 启动服务：http://127.0.0.1:8000/  （接口文档 /docs）
python -m src.cli serve --port 8000
pause
