PY ?= python3
AGENT ?= naive

export PYTHONPATH := src

.PHONY: help test eval eval-holdout compare validate-data clean results

help:
	@echo "make test          规则引擎单测"
	@echo "make eval          跑开发集评测（AGENT=naive|rule_based）"
	@echo "make eval-holdout  跑留出集 v1"
	@echo "make eval-holdout2 跑留出集 v2（验收修复用）"
	@echo "make matrix        全矩阵（AGENTS=naive,rule_based 可限定）"
	@echo "make compare       开发集 + 留出集一起跑，看泛化差距"
	@echo "make validate-data 数据体检：间隔期缺口 / 未登记组合 / 解析成功率"
	@echo "make clean         清理评测产物"

test:
	$(PY) -m unittest discover -s tests -v

eval:
	$(PY) tools/run_eval.py --agent $(AGENT) --run-id dev-$(AGENT)

# 留出集：不参与调参。开发集高分必须用这里复核，否则是过拟合。
eval-holdout:
	$(PY) tools/run_eval.py --agent $(AGENT) --cases eval/holdout.json --run-id holdout-$(AGENT)

# 留出集 v2：验收 W3 修复用。v1 已被看过，不再充当验收标准。
eval-holdout2:
	$(PY) tools/run_eval.py --agent $(AGENT) --cases eval/holdout2.json --run-id holdout2-$(AGENT)

# 全矩阵：3 个实现 × 3 个数据集
matrix:
	$(PY) tools/run_matrix.py $(if $(AGENTS),--agents $(AGENTS),)

# 一次跑完开发集与留出集，直接看泛化差距
compare:
	@for a in naive rule_based; do \
	  echo "=== $$a · 开发集 ==="; \
	  $(PY) tools/run_eval.py --agent $$a --run-id dev-$$a >/dev/null; \
	  $(PY) tools/run_eval.py --agent $$a --run-id dev-$$a | tail -7; \
	  echo "=== $$a · 留出集 ==="; \
	  $(PY) tools/run_eval.py --agent $$a --cases eval/holdout.json --run-id holdout-$$a | tail -7; \
	  echo; \
	done

validate-data:
	$(PY) tools/validate_data.py

results:
	@ls -1 eval/results/

clean:
	rm -f eval/results/*.json eval/results/*.md
