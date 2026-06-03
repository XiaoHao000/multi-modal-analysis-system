"""
RAGAS 质量评估脚本（手动运行，需 LLM API Key 和 ragas 依赖）。

安装依赖: pip install ragas>=0.2 datasets>=2.0
运行: pytest tests/test_quality_eval.py -v -s

CI 自动跳过（ragas 未安装）。
"""

import json
import os

import pytest

pytest.importorskip("ragas", reason="ragas 未安装，跳过质量评估（手动运行需先 pip install ragas datasets）")

from ragas import evaluate, EvaluationDataset
from ragas.metrics import faithfulness, answer_relevancy, context_precision
from ragas.llms import LangchainLLMWrapper

from utils.llm_factory import get_text_llm, get_embeddings


def _load_eval_dataset() -> list[dict]:
    """从 JSON 文件加载评测数据集。"""
    dataset_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "eval_dataset.json",
    )
    with open(dataset_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _populate_responses(dataset: list[dict]) -> list[dict]:
    """通过实际 LLM 调用填充 response 字段。

    注意：这会消耗 API tokens，建议评估时才运行。
    """
    llm = get_text_llm()
    for sample in dataset:
        if sample["response"]:
            continue
        prompt = (
            f"你是一个资深数据分析师。请根据以下信息回答问题。\n\n"
            f"用户问题：{sample['user_input']}\n\n"
            f"参考知识：\n" + "\n".join(sample["retrieved_contexts"]) +
            f"\n\n请用中文回答，控制在100字以内。"
        )
        response = llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)
        sample["response"] = text
    return dataset


@pytest.mark.skip(reason="需要 LLM API 调用，手动运行: pytest tests/test_quality_eval.py -v -s -k 'not skip'")
def test_ragas_quality():
    """RAGAS 质量评估主测试。

    评估指标：
    - faithfulness: 回答是否忠实于检索到的上下文（幻觉检测）
    - answer_relevancy: 回答与问题的相关程度
    - context_precision: 检索到的上下文是否精准
    """
    # 填充 response（真实 LLM 调用）
    samples = _populate_responses(_load_eval_dataset())

    # 构造 RAGAS EvaluationDataset
    eval_dataset = EvaluationDataset.from_list([
        {
            "user_input": s["user_input"],
            "response": s["response"],
            "reference": s["reference"],
            "retrieved_contexts": s["retrieved_contexts"],
        }
        for s in samples
    ])

    # 使用项目自身的 LLM 作为 RAGAS judge
    judge_llm = LangchainLLMWrapper(get_text_llm())

    result = evaluate(
        dataset=eval_dataset,
        metrics=[faithfulness, answer_relevancy, context_precision],
        llm=judge_llm,
        embeddings=get_embeddings(),
    )

    # 打印评估报告
    print("\n" + "=" * 60)
    print("RAGAS 质量评估报告")
    print("=" * 60)
    report = result.to_pandas() if hasattr(result, "to_pandas") else result
    print(report)
    print("-" * 60)
    if hasattr(result, "to_pandas"):
        print(f"Faithfulness 均值:     {result['faithfulness'].mean():.3f}")
        print(f"Answer Relevancy 均值: {result['answer_relevancy'].mean():.3f}")
        print(f"Context Precision 均值:{result['context_precision'].mean():.3f}")
    print("=" * 60)

    # 最低质量阈值断言
    assert result["faithfulness"].mean() >= 0.5, (
        f"Faithfulness {result['faithfulness'].mean():.3f} 低于阈值 0.5"
    )
    assert result["answer_relevancy"].mean() >= 0.5, (
        f"Answer Relevancy {result['answer_relevancy'].mean():.3f} 低于阈值 0.5"
    )
