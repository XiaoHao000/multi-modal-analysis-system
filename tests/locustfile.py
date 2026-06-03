"""
Locust 性能压测脚本。

用法:
  pip install locust
  locust -f tests/locustfile.py --host http://localhost:8000

  打开浏览器 http://localhost:8089 设置并发数和启动间隔，
  或 headless 模式:
  locust -f tests/locustfile.py --host http://localhost:8000 \
    --users 50 --spawn-rate 10 --run-time 60s --headless --html report.html
"""

import random
from locust import HttpUser, task, between


ANALYSIS_QUERIES = [
    "2025年Q3哪个品类的毛利率最高？",
    "2025年前三季度销售额趋势如何？",
    "哪个区域在2025年Q2销售额最高？",
    "2025年7月华南区域是否有异常数据？",
    "食品品类的毛利率表现如何？和电子品类比呢？",
    "2025年各月份的总销售额变化趋势",
    "华东区域各品类的销量排名",
    "2025年上半年电子品类的成本变化",
    "哪些品类在Q2的环比增长最快？",
    "华南和西南区域的销售额对比",
]


class AnalyzeUser(HttpUser):
    """模拟用户调用 /api/analyze 端点。"""

    wait_time = between(2, 5)  # 模拟用户思考间隔

    def on_start(self):
        """登录获取 token（如果 auth 开启则先登录）。"""
        resp = self.client.post(
            "/api/login",
            json={"username": "admin", "password": "admin123"},
        )
        if resp.status_code == 200:
            token = resp.json().get("access_token", "")
            self.client.headers.update({"Authorization": f"Bearer {token}"})
        # 如果 auth 未开启，login 返回 404，直接发请求即可

    @task(3)
    def analyze(self):
        """主分析接口 — 权重 3。"""
        query = random.choice(ANALYSIS_QUERIES)
        self.client.post(
            "/api/analyze",
            json={"query": query},
            timeout=60,
        )

    @task(1)
    def health(self):
        """健康检查 — 权重 1。"""
        self.client.get("/health", timeout=10)

    @task(1)
    def metrics(self):
        """Prometheus 指标 — 权重 1。"""
        self.client.get("/metrics", timeout=10)
