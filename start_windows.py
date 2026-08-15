from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
import json
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
VENV = ROOT / ".venv"
REQ = BACKEND / "requirements-local.txt"
PORT = 8000
URL = f"http://127.0.0.1:{PORT}"
EXPECTED_BUILD = "v97-postgres-migration-fix"


def venv_python() -> Path:
    return VENV / "Scripts" / "python.exe"


def ensure_venv() -> Path:
    py = venv_python()
    if py.exists():
        return py
    print("[1/3] 首次运行：正在创建本地 Python 环境...")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    return py


def ensure_dependencies(py: Path) -> None:
    marker = VENV / ".cps_deps_v1"
    if marker.exists():
        return
    print("[2/3] 首次运行：正在安装 CPS 依赖，请保持网络连接...")
    subprocess.check_call([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQ)])
    marker.write_text("ok", encoding="utf-8")


def port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0



def running_build() -> str | None:
    try:
        with urllib.request.urlopen(f"{URL}/api/public/build-info", timeout=1.0) as r:
            data = json.loads(r.read().decode("utf-8"))
            return str(data.get("version") or "") or None
    except Exception:
        return None


def wait_and_open_browser() -> None:
    for _ in range(80):
        try:
            with urllib.request.urlopen(URL, timeout=0.5) as r:
                if r.status < 500:
                    webbrowser.open(URL)
                    return
        except Exception:
            time.sleep(0.25)


def main() -> int:
    if sys.version_info < (3, 11):
        print("需要 Python 3.11 或更高版本。")
        print("请安装 Python 3.12，并勾选 Add python.exe to PATH。")
        return 2

    if port_open():
        build = running_build()
        if build == EXPECTED_BUILD:
            print(f"检测到当前版本 {EXPECTED_BUILD} 已在运行。")
            webbrowser.open(URL)
            return 0
        if build:
            print(f"检测到旧 CPS 进程仍在运行：{build}")
            print(f"当前源码版本应为：{EXPECTED_BUILD}")
            print("请先关闭之前的 CPS 启动窗口/进程，再重新双击启动系统.bat。")
            print("仅刷新浏览器无法让已运行的 Python 进程加载新源码。")
            return 4
        print(f"端口 {PORT} 已被其它程序占用，无法启动当前 CPS。")
        return 4

    try:
        py = ensure_venv()
        ensure_dependencies(py)
    except subprocess.CalledProcessError:
        print("\n依赖安装失败。请检查网络连接后重新双击启动系统.bat。")
        return 3

    env = os.environ.copy()
    env.setdefault("DATABASE_URL", "sqlite:///./cps.db")
    env.setdefault("JWT_SECRET", "cps-local-windows-change-before-production")
    env.setdefault("ADMIN_USERNAME", "admin")
    env.setdefault("ADMIN_PASSWORD", "ChangeMe123!")
    env.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "720")

    print("[3/3] 正在启动 CPS 智能代理系统...")
    print(f"后台地址：{URL}")
    print("管理员账号：admin")
    print("管理员密码：ChangeMe123!")
    print("\n注意：这个窗口不要关闭；关闭窗口就会停止系统。")
    print("如需停止，按 Ctrl+C。\n")

    import threading
    threading.Thread(target=wait_and_open_browser, daemon=True).start()

    try:
        return subprocess.call(
            [str(py), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=str(BACKEND),
            env=env,
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
