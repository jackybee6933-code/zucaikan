import sys
from datetime import datetime


def task_log(task: str, message: str) -> None:
    """后台任务日志即时输出；关闭控制台不应中断任务。"""
    text = f"[{datetime.now():%H:%M:%S}] [{task}] {message}"
    try:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        if not isinstance(encoding, str):
            encoding = "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding), flush=True)
    except (OSError, ValueError, LookupError, TypeError):
        pass
