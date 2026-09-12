# RAG 检索评测报告

- 模式：`live`
- 案例数：40
- 标注集：`travel-rag-static-v1-2`
- 标注集 SHA-256：`dd994e273c591f2a3bb397cbce72f73b47fe96cde185c5221e7cc161128b2df7`
- 检索配置：`dense_chroma`，embedding=`text-embedding-3-large`，top-k=5

## 检索质量

| 指标 | 数值 |
| --- | ---: |
| recall_at_3 | 1.0000 |
| precision_at_3 | 0.3333 |
| mrr_at_3 | 0.9333 |
| ndcg_at_3 | 0.9506 |
| recall_at_5 | 1.0000 |
| precision_at_5 | 0.2000 |
| mrr_at_5 | 0.9333 |
| ndcg_at_5 | 0.9506 |
| fact_coverage | 1.0000 |
| source_coverage | 1.0000 |

## 时延

> 范围：仅查询 embedding 与公共知识 Chroma 检索；不包含动态建库、历史检索、上下文拼接或 LLM 生成

- `query_embedding`：n=40，平均 1.0787s，p50 1.0246s，p95 1.4762s，最大 1.8671s
- `knowledge_vector_search`：n=40，平均 0.0054s，p50 0.0054s，p95 0.0064s，最大 0.0090s
- `retrieval_end_to_end`：n=40，平均 1.0841s，p50 1.0298s，p95 1.4843s，最大 1.8726s

## 分类表现

| 分类 | 案例数 | Recall@5 | MRR@5 | nDCG@5 | 事实覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| opening-hours | 7 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| overview | 2 | 1.0000 | 0.7500 | 0.8155 | 1.0000 |
| planning | 5 | 1.0000 | 0.7667 | 0.8262 | 1.0000 |
| ticket | 11 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| transport | 15 | 1.0000 | 0.9333 | 0.9508 | 1.0000 |

## 解释边界

- `fact_coverage` 是召回片段包含标注事实的比例，不是最终 LLM 答案正确率。
- p95 需要足够多且分布稳定的样本；请结合 `n` 解读，不能把小样本结果当作线上 SLA。
- 只有标注集、知识快照、embedding 模型与检索配置一致时，才可比较两份报告。
