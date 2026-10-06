# Evals

Evals are automated quality checks that run your agent pipelines against a fixed dataset and score the outputs.

## Creating an eval

1. Go to **Evals** in the dashboard
2. Click **New eval**
3. Select a pipeline definition and a dataset
4. Configure scoring criteria (exact match, LLM-as-judge, custom function)

## Scheduling

Evals can run:
- **On demand** — triggered manually or via API
- **On schedule** — cron-based (e.g. daily at 02:00 UTC)
- **On deploy** — automatically after each successful INT deploy

## Compare runs

The **Compare** view lets you run the same dataset through two different configurations (different models, prompts, or pipeline versions) side by side to measure regressions or improvements.
