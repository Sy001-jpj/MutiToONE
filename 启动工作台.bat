@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title MutiToONE 工作台

rem 让 Python 输出用 UTF-8，配合上面的 chcp 65001，中文不乱码
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( where py >nul 2>nul && set "PY=py -3" )

if not defined PY (
  echo.
  echo   [x] 没有找到 Python
  echo.
  echo   这个版本需要先安装 Python 3.10 或更高版本：
  echo     https://www.python.org/downloads/
  echo   安装时请勾选 "Add python.exe to PATH"
  echo.
  pause
  exit /b 1
)

echo.
echo   正在启动「真题合订本」工作台...
echo   浏览器会自动打开；没反应就用下面显示的地址手动访问。
echo.
%PY% -m hebing serve %*
set "rc=%ERRORLEVEL%"
echo.
if not "%rc%"=="0" (
  echo   [x] 启动失败（退出码 %rc%）
  echo.
  echo   常见原因：
  echo     1^) 依赖没装:   pip install -r requirements.txt
  echo     2^) 端口占用:   启动工作台.bat --port 8888
  echo.
)
echo   服务已停止，按任意键关闭窗口。
pause >nul
