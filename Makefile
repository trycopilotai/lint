PYTHON ?= python3
ARGS ?=

.PHONY: \
  all \
  lint \
  dlint \
  lint_ts \
  lint_js \
  lint_md \
  lint_py \
  lint_sh \
  lint_c \
  lint_cpp \
  lint_cs \
  test \
  verify

all: lint

lint:
	$(PYTHON) lint.py $(ARGS)

dlint:
	$(PYTHON) dlint.py $(ARGS)

lint_%:
	$(PYTHON) lint.py --language "$*" $(ARGS)

dlint_%:
	$(PYTHON) dlint.py --language "$*" $(ARGS)

lint_ts:
	$(PYTHON) lint.py \
		--language typescript --language tsx $(ARGS)

lint_js:
	$(PYTHON) lint.py --language javascript $(ARGS)

lint_md:
	$(PYTHON) lint.py --language markdown $(ARGS)

lint_py:
	$(PYTHON) lint.py --language python $(ARGS)

lint_sh:
	$(PYTHON) lint.py --language shell $(ARGS)

lint_c:
	$(PYTHON) lint.py --language c $(ARGS)

lint_cpp:
	$(PYTHON) lint.py --language cpp $(ARGS)

lint_cs:
	$(PYTHON) lint.py --language csharp $(ARGS)

test:
	@set -e; \
	for test_file in tests/*_test.py; do \
		$(PYTHON) "$$test_file"; \
	done

verify: test
	$(PYTHON) tools/verify_repo.py
