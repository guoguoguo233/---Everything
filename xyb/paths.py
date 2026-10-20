# -*- coding: utf-8 -*-
"""
统一的路径解析：同时兼容两种运行方式

- 源码运行：仓库根目录存放 config.json（在 xyb/ 下）、chrome-profile、周志记录
- PyInstaller 打包的 EXE：所有可写数据放在 EXE 同目录（便携/绿色版）
"""
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的 EXE 里。"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """只读资源目录（配置模板、README、docs）。
    源码模式 = xyb/ 包目录；EXE 模式 = PyInstaller 解压出的临时目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def app_dir() -> Path:
    """可写数据目录。EXE 模式 = EXE 所在目录；源码模式 = 仓库根目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ---------- 具体位置 ----------
def config_path() -> Path:
    return (app_dir() / "config.json") if is_frozen() else (app_dir() / "xyb" / "config.json")


def example_config_path() -> Path:
    if is_frozen():
        p = bundle_dir() / "config.example.json"
        if p.exists():
            return p
    return app_dir() / "xyb" / "config.example.json"


def profile_dir() -> Path:
    return app_dir() / "chrome-profile"


def session_path() -> Path:
    return app_dir() / ".session.json" if is_frozen() else app_dir() / "xyb" / ".session.json"


def qr_png_path() -> Path:
    return app_dir() / "qr.png"


def pending_path() -> Path:
    name = ".pending.json"
    return app_dir() / name if is_frozen() else app_dir() / "xyb" / name


def batch_path() -> Path:
    name = ".pending_batch.json"
    return app_dir() / name if is_frozen() else app_dir() / "xyb" / name


def readme_path() -> Path:
    if is_frozen():
        p = bundle_dir() / "README.md"
        if p.exists():
            return p
    return app_dir() / "README.md"


def docs_dir() -> Path:
    if is_frozen():
        p = bundle_dir() / "docs"
        if p.exists():
            return p
    return app_dir() / "docs"
