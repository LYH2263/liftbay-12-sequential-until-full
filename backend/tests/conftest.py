import os
import tempfile

# 必须在任何 app.* 模块导入前生效：app.database 在导入时即用 settings 建引擎。
# 用临时文件库（而非 sqlite:// 内存库），保证 TestClient 工作线程与测试线程
# 看到的是同一份数据。
_TMPDIR = tempfile.mkdtemp(prefix="liftbay-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMPDIR}/test.db")
os.environ.setdefault("SEED_ON_EMPTY", "false")
