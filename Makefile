PY ?= python3
AGENT ?= naive

export PYTHONPATH := src

.PHONY: help test eval validate-data clean results

help:
	@echo "make test          规则引擎单测"
	@echo "make eval          跑评测并出报告（AGENT=naive）"
	@echo "make validate-data 检查数据夹具是否已替换"
	@echo "make clean         清理评测产物"

test:
	$(PY) -m unittest discover -s tests -v

eval:
	$(PY) tools/run_eval.py --agent $(AGENT)

validate-data:
	$(PY) tools/validate_data.py

results:
	@ls -1 eval/results/

clean:
	rm -f eval/results/*.json eval/results/*.md
