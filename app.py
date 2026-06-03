"""Multi-Modal Data Insight Agent — 启动入口"""

from utils.bootstrap import bootstrap_sync
from utils.logger import logger

# Streamlit 入口：首次启动自动初始化数据库 + 知识库
bootstrap_sync()

from frontend.streamlit_ui import main  # noqa: E402

if __name__ == "__main__":
    main()
