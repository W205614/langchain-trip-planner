# RAG 检索评测报告

- 模式：`live`
- 案例数：40
- 标注集：`travel-rag-static-v1`
- 标注集 SHA-256：`1423b9fb1a3adbefb7763c7298bacd13bd470b6948885364830a5263760f4d72`
- 检索配置：`dense_chroma`，embedding=`text-embedding-3-large`，top-k=5

## 检索质量

| 指标 | 数值 |
| --- | ---: |
| recall_at_3 | 1.0000 |
| precision_at_3 | 0.3333 |
| mrr_at_3 | 0.9458 |
| ndcg_at_3 | 0.9598 |
| recall_at_5 | 1.0000 |
| precision_at_5 | 0.2000 |
| mrr_at_5 | 0.9458 |
| ndcg_at_5 | 0.9598 |
| fact_coverage | 1.0000 |
| source_coverage | 1.0000 |

## 时延

> 范围：仅查询 embedding 与公共知识 Chroma 检索；不包含动态建库、历史检索、上下文拼接或 LLM 生成

- `query_embedding`：n=40，平均 1.7892s，p50 1.3284s，p95 4.0354s，最大 4.9353s
- `knowledge_vector_search`：n=40，平均 0.0060s，p50 0.0060s，p95 0.0068s，最大 0.0072s
- `retrieval_end_to_end`：n=40，平均 1.7952s，p50 1.3344s，p95 4.0418s，最大 4.9409s

## 分类表现

| 分类 | 案例数 | Recall@5 | MRR@5 | nDCG@5 | 事实覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| opening-hours | 7 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| overview | 2 | 1.0000 | 0.7500 | 0.8155 | 1.0000 |
| planning | 5 | 1.0000 | 0.8667 | 0.9000 | 1.0000 |
| ticket | 11 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| transport | 15 | 1.0000 | 0.9333 | 0.9508 | 1.0000 |

## 解释边界

- `fact_coverage` 是召回片段包含标注事实的比例，不是最终 LLM 答案正确率。
- p95 需要足够多且分布稳定的样本；请结合 `n` 解读，不能把小样本结果当作线上 SLA。
- 只有标注集、知识快照、embedding 模型与检索配置一致时，才可比较两份报告。
