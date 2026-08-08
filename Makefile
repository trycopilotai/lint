PYTHON ?= python3
ARGS ?=

.PHONY: \
  all \
  lint \
  doctor \
  ensure \
  dlint \
  lint_markdown \
  lint_html \
  lint_yaml \
  lint_json \
  lint_javascript \
  lint_typescript \
  lint_tsx \
  lint_css \
  lint_scss \
  lint_less \
  lint_bazel \
  lint_python \
  lint_requirements \
  lint_shell \
  lint_c \
  lint_cpp \
  lint_objective-c \
  lint_objective-cpp \
  lint_java \
  lint_go \
  lint_rust \
  lint_kotlin \
  lint_toml \
  lint_xml \
  lint_plist \
  lint_swift \
  lint_csharp \
  lint_julia \
  dlint_markdown \
  dlint_html \
  dlint_yaml \
  dlint_json \
  dlint_javascript \
  dlint_typescript \
  dlint_tsx \
  dlint_css \
  dlint_scss \
  dlint_less \
  dlint_bazel \
  dlint_python \
  dlint_requirements \
  dlint_shell \
  dlint_c \
  dlint_cpp \
  dlint_objective-c \
  dlint_objective-cpp \
  dlint_java \
  dlint_go \
  dlint_rust \
  dlint_kotlin \
  dlint_toml \
  dlint_xml \
  dlint_plist \
  dlint_swift \
  dlint_csharp \
  dlint_julia \
  lint_ts \
  lint_js \
  lint_md \
  lint_py \
  lint_sh \
  lint_cs \
  dlint_ts \
  dlint_js \
  dlint_md \
  dlint_py \
  dlint_sh \
  dlint_cs \
  codec \
  pyformat \
  test \
  verify

# Every document surface the codec covers, in one place.
# Listing it separately in each workflow let the sets drift:
# the issue forms and label definitions a contributor sees
# were checked locally and by no automation.
CODEC_PATHS = \
  README.md \
  CONTRIBUTING.md \
  SECURITY.md \
  action.yml \
  .github/workflows \
  .github/ISSUE_TEMPLATE \
  .github/labels.yml \
  .claude-plugin \
  .codex-plugin \
  docs \
  skills \
  languages.json \
  images/*.json \
  images/THIRD_PARTY_NOTICES.md \
  fixtures/http/*.json \
  evidence/comparison-sources.json

# Every Python surface Black formats, in one place. This set
# was spelled out separately in both workflows for the same
# reason the codec set was, and was one edit away from the
# same drift.
BLACK_PATHS = \
  lint.py \
  dlint.py \
  service.py \
  action_entrypoint.py \
  tests \
  images \
  scripts \
  skills \
  tools

all: lint

codec:
	npx -y prettier@3.7.4 --check --print-width 60 \
		--prose-wrap always --trailing-comma none \
		$(CODEC_PATHS)

pyformat:
	pipx run --spec black==24.10.0 black --check --diff \
		$(BLACK_PATHS)

lint:
	$(PYTHON) lint.py $(ARGS)

doctor:
	$(PYTHON) lint.py doctor $(ARGS)

ensure:
	$(PYTHON) lint.py ensure $(ARGS)

dlint:
	$(PYTHON) dlint.py $(ARGS)

lint_markdown \
lint_html \
lint_yaml \
lint_json \
lint_javascript \
lint_typescript \
lint_tsx \
lint_css \
lint_scss \
lint_less \
lint_bazel \
lint_python \
lint_requirements \
lint_shell \
lint_c \
lint_cpp \
lint_objective-c \
lint_objective-cpp \
lint_java \
lint_go \
lint_rust \
lint_kotlin \
lint_toml \
lint_xml \
lint_plist \
lint_swift \
lint_csharp \
lint_julia:
	$(PYTHON) lint.py --language "$(@:lint_%=%)" $(ARGS)

dlint_markdown \
dlint_html \
dlint_yaml \
dlint_json \
dlint_javascript \
dlint_typescript \
dlint_tsx \
dlint_css \
dlint_scss \
dlint_less \
dlint_bazel \
dlint_python \
dlint_requirements \
dlint_shell \
dlint_c \
dlint_cpp \
dlint_objective-c \
dlint_objective-cpp \
dlint_java \
dlint_go \
dlint_rust \
dlint_kotlin \
dlint_toml \
dlint_xml \
dlint_plist \
dlint_swift \
dlint_csharp \
dlint_julia:
	$(PYTHON) dlint.py --language "$(@:dlint_%=%)" $(ARGS)

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

lint_cs:
	$(PYTHON) lint.py --language csharp $(ARGS)

dlint_ts:
	$(PYTHON) dlint.py \
		--language typescript --language tsx $(ARGS)

dlint_js:
	$(PYTHON) dlint.py --language javascript $(ARGS)

dlint_md:
	$(PYTHON) dlint.py --language markdown $(ARGS)

dlint_py:
	$(PYTHON) dlint.py --language python $(ARGS)

dlint_sh:
	$(PYTHON) dlint.py --language shell $(ARGS)

dlint_cs:
	$(PYTHON) dlint.py --language csharp $(ARGS)

test:
	@set -e; \
	for test_file in tests/*_test.py; do \
		$(PYTHON) "$$test_file"; \
	done

verify: test
	$(PYTHON) tools/verify_repo.py
	$(PYTHON) tools/disclosure_scan.py --history
