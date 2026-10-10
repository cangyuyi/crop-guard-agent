PY ?= python3
AGENT ?= naive

export PYTHONPATH := src

.PHONY: help test eval eval-holdout compare validate-data clean results

help:
	@echo "make test          规则引擎单测"
	@echo "make eval          跑开发集评测（AGENT=naive|rule_based）"
	@echo "make eval-holdout  跑留出集（检验是否过拟合）"
	@echo "make compare       开发集 + 留出集一起跑，看泛化差距"
	@echo "make validate-data 数据体检：间隔期缺口 / 未登记组合 / 解析成功率"
	@echo "make clean         清理评测产物"

test:
	$(PY) -m unittest discover -s tests -v

eval:
	$(PY) tools/run_eval.py --agent $(AGENT)

# 留出集：不参与调参。开发集高分必须用这里复核，否则是过拟合。
eval-holdout:
	$(PY) tools/run_eval.py --agent $(AGENT) --cases eval/holdout.json --run-id holdout-$(AGENT)

# 一次跑完开发集与留出集，直接看泛化差距
compare:
	@for a in naive rule_based; do \
	  echo "=== $$a · 开发集 ==="; \
	  $(PY) tools/run_eval.py --agent $$a >/dev/null; \
	  $(PY) tools/run_eval.py --agent $$a | tail -7; \
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
