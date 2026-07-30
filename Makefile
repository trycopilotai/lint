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
	$(PYTHON) lint.py --all $(ARGS)

dlint:
	$(PYTHON) dlint.py --all $(ARGS)

lint_%:
	$(PYTHON) lint.py --all --language "$*" $(ARGS)

dlint_%:
	$(PYTHON) dlint.py --all --language "$*" $(ARGS)

lint_ts:
	$(PYTHON) lint.py --all \
		--language typescript --language tsx $(ARGS)

lint_js:
	$(PYTHON) lint.py --all --language javascript $(ARGS)

lint_md:
	$(PYTHON) lint.py --all --language markdown $(ARGS)

lint_py:
	$(PYTHON) lint.py --all --language python $(ARGS)

lint_sh:
	$(PYTHON) lint.py --all --language shell $(ARGS)

lint_c:
	$(PYTHON) lint.py --all --language c $(ARGS)

lint_cpp:
	$(PYTHON) lint.py --all --language cpp $(ARGS)

lint_cs:
	$(PYTHON) lint.py --all --language csharp $(ARGS)

test:
	@set -e; \
	for test_file in tests/*_test.py; do \
		$(PYTHON) "$$test_file"; \
	done

verify: test
	$(PYTHON) tools/verify_repo.py
