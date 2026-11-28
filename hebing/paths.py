# -*- coding: utf-8 -*-
"""统一的路径解析：源码运行、桌面窗口、打包成 exe 三种情况都能用。

优先级：
1. 环境变量 MUTITOO_HOME（桌面窗口用 --home 指定，通常指向 应用程序 文件夹）
2. 打包成 exe 时：exe 所在目录
3. 源码运行：包目录的上一级（项目根）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))


def app_root():
    env = os.environ.get("MUTITOO_HOME")
    if env:
        return os.path.abspath(env)
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(HERE)


APP_ROOT = app_root()
OUT_DIR = os.path.join(APP_ROOT, "输出")


def set_home(path):
    """在进程内改主目录（必须在其它模块读取 APP_ROOT 之前调用才有效）。"""
    global APP_ROOT, OUT_DIR
    os.environ["MUTITOO_HOME"] = os.path.abspath(path)
    APP_ROOT = app_root()
    OUT_DIR = os.path.join(APP_ROOT, "输出")
    return APP_ROOT
