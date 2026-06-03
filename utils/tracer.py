import os
from config import Config
from utils.logger import logger


def setup_langsmith() -> None:
    if Config.langsmith_api_key:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = Config.langsmith_api_key
        os.environ["LANGCHAIN_PROJECT"] = Config.langsmith_project
        logger.info(f"LangSmith 追踪已启用，项目: {Config.langsmith_project}")
    else:
        logger.info("LangSmith API Key 未配置，跳过追踪")
