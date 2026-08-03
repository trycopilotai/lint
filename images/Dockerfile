# syntax=docker/dockerfile:1.18

ARG ALPINE_3_23="alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40"
ARG DISTROLESS_CC="gcr.io/distroless/cc-debian13:nonroot@sha256:d97bc0a941b8d4be647dc0ee75b264ddbb772f1ac5ba690a4309c00723b23775"
ARG DOTNET_RUNTIME="mcr.microsoft.com/dotnet/runtime:10.0.10-alpine3.23@sha256:9297ae3050f7e80428142f784a6d016f80bc8b5cd54e7fc1b596935911b1e58e"
ARG GO_IMAGE="golang:1.26.5-alpine3.23@sha256:622e56dbc11a8cfe87cafa2331e9a201877271cbff918af53d3be315f3da88cc"
ARG JAVA_IMAGE="eclipse-temurin:21-jre-alpine-3.23@sha256:3f08b13888f595cc49edabea7250ba69499ba25602b267da591720769400e08c"
ARG JAVA_JDK_IMAGE="eclipse-temurin:21-jdk-alpine-3.23@sha256:1ff763083f2993d57d0bf374ab10bb3e2cb873af6c13a04458ebbd3e0337dc76"
ARG JULIA_IMAGE="julia:1.12.6-bookworm@sha256:4979dc5c6fbcd7f5f0dc2ba2b336d136bda63a40c25e52a848222aa24326dfe1"
ARG NODE_IMAGE="node:24.18.0-alpine3.23@sha256:595398b0081eacda8e1c4c5b97b76cd1020e4d58a8ebcb4843b9bca1e79e7436"
ARG PYTHON_IMAGE="python:3.13.14-alpine3.23@sha256:9fdbf2e3e82628351513560b121e2ee6ce31cac212be9e070c5a5e2769fb5e76"
ARG RUST_IMAGE="rust:1.97.1-alpine3.23@sha256:c4a364ddbf684fe038e6fa6a4f25b30c8dc85247423e0e660676ece0d17be4a2"
ARG SWIFT_IMAGE="swift:6.3-noble@sha256:66520bcba471018a34fd54ba09be97ba4abebd950a96ff5cb8c2bf50a2d33259"

FROM ${ALPINE_3_23} AS downloader
RUN apk add --no-cache ca-certificates curl

FROM downloader AS prettier-download
RUN mkdir /downloads \
    && curl --fail --location \
      --output /downloads/prettier-3.7.4.tgz \
      https://registry.npmjs.org/prettier/-/prettier-3.7.4.tgz \
    && echo "7e63a83230572391c5b1af4fcbac7273850c898652e680b6824b41e8f64aa4e6  /downloads/prettier-3.7.4.tgz" \
      | sha256sum -c

FROM downloader AS black-download
RUN mkdir /black-wheels \
    && curl --fail --location \
      --output /black-wheels/black-24.10.0-py3-none-any.whl \
      https://files.pythonhosted.org/packages/8d/a7/4b27c50537ebca8bec139b872861f9d2bf501c5ec51fcf897cb924d9e264/black-24.10.0-py3-none-any.whl \
    && echo "3bb2b7a1f7b685f85b11fed1ef10f8a9148bceb49853e47a294a3dd963c1dd7d  /black-wheels/black-24.10.0-py3-none-any.whl" \
      | sha256sum -c \
    && curl --fail --location \
      --output /black-wheels/click-8.1.7-py3-none-any.whl \
      https://files.pythonhosted.org/packages/00/2e/d53fa4befbf2cfa713304affc7ca780ce4fc1fd8710527771b58311a3229/click-8.1.7-py3-none-any.whl \
    && echo "ae74fb96c20a0277a1d615f1e4d73c8414f5a98db8b799a7931d1582f3390c28  /black-wheels/click-8.1.7-py3-none-any.whl" \
      | sha256sum -c \
    && curl --fail --location \
      --output /black-wheels/mypy_extensions-1.0.0-py3-none-any.whl \
      https://files.pythonhosted.org/packages/2a/e2/5d3f6ada4297caebe1a2add3b126fe800c96f56dbe5d1988a2cbe0b267aa/mypy_extensions-1.0.0-py3-none-any.whl \
    && echo "4392f6c0eb8a5668a69e23d168ffa70f0be9ccfd32b5cc2d26a34ae5b844552d  /black-wheels/mypy_extensions-1.0.0-py3-none-any.whl" \
      | sha256sum -c \
    && curl --fail --location \
      --output /black-wheels/packaging-24.1-py3-none-any.whl \
      https://files.pythonhosted.org/packages/08/aa/cc0199a5f0ad350994d660967a8efb233fe0416e4639146c089643407ce6/packaging-24.1-py3-none-any.whl \
    && echo "5b8f2217dbdbd2f7f384c41c628544e6d52f2d0f53c6d0c3ea61aa5d1d7ff124  /black-wheels/packaging-24.1-py3-none-any.whl" \
      | sha256sum -c \
    && curl --fail --location \
      --output /black-wheels/pathspec-0.12.1-py3-none-any.whl \
      https://files.pythonhosted.org/packages/cc/20/ff623b09d963f88bfde16306a54e12ee5ea43e9b597108672ff3a408aad6/pathspec-0.12.1-py3-none-any.whl \
    && echo "a0d503e138a4c123b27490a4f7beda6a01c6f288df0e4a8b79c7eb0dc7b4cc08  /black-wheels/pathspec-0.12.1-py3-none-any.whl" \
      | sha256sum -c \
    && curl --fail --location \
      --output /black-wheels/platformdirs-4.3.6-py3-none-any.whl \
      https://files.pythonhosted.org/packages/3c/a6/bc1012356d8ece4d66dd75c4b9fc6c1f6650ddd5991e421177d9f8f671be/platformdirs-4.3.6-py3-none-any.whl \
    && echo "73e575e1408ab8103900836b97580d5307456908a03e92031bab39e4554cc3fb  /black-wheels/platformdirs-4.3.6-py3-none-any.whl" \
      | sha256sum -c

FROM downloader AS julia-formatter-download
RUN mkdir /downloads \
    && curl --fail --location \
      --output /downloads/JuliaFormatter-e421b4f670555ff650f1b77df47cd42c806d8372.tar.gz \
      https://pkg.julialang.org/package/98e50ef6-434e-11e9-1051-2b60c6c9e899/e421b4f670555ff650f1b77df47cd42c806d8372 \
    && echo "6db065256d064086edc3519aa55937505180c7183a9ffbe68814fff49f0022cc  /downloads/JuliaFormatter-e421b4f670555ff650f1b77df47cd42c806d8372.tar.gz" \
      | sha256sum -c

FROM downloader AS swift-format-source
RUN curl --fail --location \
      --output /swift-format.tar.gz \
      https://github.com/swiftlang/swift-format/archive/refs/tags/603.0.0.tar.gz \
    && echo "39732a76ba0d86dca799b8c92f3516398aaa712e1ec489c9e439219e72feac1f  /swift-format.tar.gz" \
      | sha256sum -c \
    && mkdir /swift-format-source \
    && tar -xzf /swift-format.tar.gz \
      --strip-components=1 \
      -C /swift-format-source

FROM ${ALPINE_3_23} AS launcher-build
RUN apk add --no-cache build-base
COPY images/kotlin_entrypoint.c /kotlin_entrypoint.c
RUN cc -static -Os /kotlin_entrypoint.c -o /kotlin-format

FROM scratch AS license-notices
COPY LICENSE /licenses/lint/LICENSE
COPY images/THIRD_PARTY_NOTICES.md /licenses/lint/THIRD_PARTY_NOTICES.md

FROM ${NODE_IMAGE} AS prettier-build
COPY --from=prettier-download \
    /downloads/prettier-3.7.4.tgz \
    /downloads/prettier-3.7.4.tgz
RUN npm install \
      --global \
      --ignore-scripts \
      --no-audit \
      --no-fund \
      --offline \
      --omit=dev \
      /downloads/prettier-3.7.4.tgz \
    && npm cache clean --force \
    && test "$(node \
      /usr/local/lib/node_modules/prettier/bin/prettier.cjs \
      --version)" = "3.7.4" \
    && printf 'prettier=3.7.4\n' >/lint-tool-version

FROM scratch AS prettier
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/prettier /licenses/prettier
COPY --from=prettier-build /lib /lib
COPY --from=prettier-build /usr/lib /usr/lib
COPY --from=prettier-build /usr/local/bin/node /usr/local/bin/node
COPY --from=prettier-build \
    /usr/local/lib/node_modules/prettier \
    /usr/local/lib/node_modules/prettier
COPY --from=prettier-build /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/node", "/usr/local/lib/node_modules/prettier/bin/prettier.cjs", "--write", "--ignore-path", "/work/.lint-empty-ignore", "--print-width", "60", "--prose-wrap", "always", "--trailing-comma", "none"]

FROM downloader AS go-tools-source
RUN curl --fail --location \
      --output /buildtools.tar.gz \
      https://github.com/bazelbuild/buildtools/archive/refs/tags/v8.2.1.tar.gz \
    && echo "53119397bbce1cd7e4c590e117dcda343c2086199de62932106c80733526c261  /buildtools.tar.gz" \
      | sha256sum -c \
    && mkdir /buildtools \
    && tar -xzf /buildtools.tar.gz \
      --strip-components=1 \
      -C /buildtools

FROM ${GO_IMAGE} AS go-tools-build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
COPY --from=go-tools-source /buildtools /buildtools
RUN cd /buildtools \
    && go build \
      -mod=readonly \
      -trimpath \
      -ldflags="-s -w -X main.buildVersion=8.2.1 -X main.buildScmRevision=v8.2.1" \
      -o /buildifier \
      ./buildifier \
    && expected_buildifier="$(printf \
      'buildifier version: 8.2.1 \nbuildifier scm revision: v8.2.1 ')" \
    && test "$(/buildifier -version)" = "$expected_buildifier" \
    && printf 'buildifier=8.2.1\n' >/buildifier-tool-version
RUN module_metadata="$(go mod download -json \
      mvdan.cc/sh/v3@v3.13.1)" \
    && module_sums="$(printf '%s\n' "$module_metadata" \
      | grep -E '^[[:space:]]+"(Sum|GoModSum)": ')" \
    && expected_module_sums="$(printf \
      '\t"Sum": "h1:DP3TfgZhDkT7lerUdnp6PTGKyxxzz6T+cOlY/xEvfWk=",\n\t"GoModSum": "h1:lXJ8SexMvEVcHCoDvAGLZgFJ9Wsm2sulmoNEXGhYZD0=",')" \
    && test "$module_sums" = "$expected_module_sums"
RUN mkdir /out \
    && GOBIN=/out go install \
      mvdan.cc/sh/v3/cmd/shfmt@v3.13.1 \
    && cp /out/shfmt /shfmt \
    && test "$(/shfmt --version)" = "v3.13.1" \
    && printf 'shfmt=3.13.1\n' >/shfmt-tool-version

FROM scratch AS buildifier
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/buildifier /licenses/buildifier
COPY --from=go-tools-build /buildifier /buildifier
COPY --from=go-tools-build /buildifier-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/buildifier", "-mode=fix"]

FROM ${PYTHON_IMAGE} AS black-build
COPY --from=black-download /black-wheels /black-wheels
RUN pip install \
      --no-cache-dir \
      --no-compile \
      --no-index \
      /black-wheels/*.whl \
    && rm -rf \
      /black-wheels \
      /usr/local/bin/pip* \
      /usr/local/lib/python3.13/ensurepip \
      /usr/local/lib/python3.13/site-packages/pip* \
    && test "$(PYTHONDONTWRITEBYTECODE=1 python3 -c \
      'import black; print(black.__version__)')" = "24.10.0" \
    && printf 'black=24.10.0\n' >/lint-tool-version

FROM scratch AS black
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/black /licenses/black
COPY --from=black-build /lib /lib
COPY --from=black-build /usr/lib /usr/lib
COPY --from=black-build /usr/local /usr/local
COPY --from=black-build /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/python3", "-m", "black", "--quiet", "--line-length", "88"]

FROM ${PYTHON_IMAGE} AS requirements-build
COPY --chmod=0555 images/requirements_entrypoint.py /requirements-format
RUN echo "723bfe9070dd70e804fff734a7156b963e694446e161739d03394ca30d9f29b5  /requirements-format" \
      | sha256sum -c \
    && printf 'requirements=sha256:723bfe9070dd70e804fff734a7156b963e694446e161739d03394ca30d9f29b5\n' \
      >/lint-tool-version \
    && rm -rf \
      /usr/local/bin/pip* \
      /usr/local/lib/python3.13/ensurepip \
      /usr/local/lib/python3.13/site-packages/pip*

FROM scratch AS requirements
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/requirements /licenses/requirements
COPY --from=requirements-build /lib /lib
COPY --from=requirements-build /usr/lib /usr/lib
COPY --from=requirements-build /usr/local /usr/local
COPY --from=requirements-build /requirements-format /requirements-format
COPY --from=requirements-build /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/python3", "/requirements-format"]

FROM scratch AS shfmt
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/shfmt /licenses/shfmt
COPY --from=go-tools-build /shfmt /shfmt
COPY --from=go-tools-build /shfmt-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/shfmt", "-w", "-i", "2", "-ci"]

FROM downloader AS clang-download
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) \
        checksum="4762e95ea887d522bf664c1411d93d4d41fc9eb059835dd88b9ad54bc3cb08e0"; \
        url="https://files.pythonhosted.org/packages/c8/b1/7bdec805dfa84eb4d2f3cbe65a419eaeb4cb0d67634c68d0915f54a32b88/clang_format-18.1.8-py2.py3-none-musllinux_1_2_x86_64.whl" \
        ;; \
      arm64) \
        checksum="7d71869103d0f27be3c4930bea59dc6325177a259ba321a04663a231432ec343"; \
        url="https://files.pythonhosted.org/packages/a7/93/48c0fd19ac5d6221059049facf0e1a91572e7f811e57f94d4882e37ee8a7/clang_format-18.1.8-py2.py3-none-musllinux_1_2_aarch64.whl" \
        ;; \
      *) exit 2 ;; \
    esac; \
    curl --fail --location --output /clang-format.whl "${url}"; \
    echo "${checksum}  /clang-format.whl" | sha256sum -c; \
    mkdir /clang-wheel; \
    busybox unzip -q /clang-format.whl -d /clang-wheel; \
    chmod 0555 /clang-wheel/clang_format/data/bin/clang-format; \
    test "$(/clang-wheel/clang_format/data/bin/clang-format --version)" = \
      "clang-format version 18.1.8"; \
    printf 'clang-format=18.1.8\n' >/lint-tool-version

FROM scratch AS clang
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/clang /licenses/clang
COPY --from=clang-download /lib/ld-musl-*.so.1 /lib/
COPY --from=clang-download \
    /clang-wheel/clang_format.libs \
    /clang_format.libs
COPY --from=clang-download \
    /clang-wheel/clang_format/data/bin/clang-format \
    /clang_format/data/bin/clang-format
COPY --from=clang-download \
    /clang-wheel/clang_format-18.1.8.dist-info/licenses \
    /licenses/clang-format
COPY --from=clang-download /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/clang_format/data/bin/clang-format", "-i"]

FROM downloader AS java-download
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) asset_arch="x86-64"; checksum="e0f95ad6d26b8fbd5b1e3fd3014f8306c4eb3b29d2a6b15fab62d0a364629437" ;; \
      arm64) asset_arch="arm64"; checksum="a69b2e85deb681f3eae4c946b948908e162e76ef27d752fa31830866dd7aa764" ;; \
      *) exit 2 ;; \
    esac; \
    url="https://github.com/google/google-java-format/releases/download/v1.35.0/google-java-format_linux-${asset_arch}"; \
    curl --fail --location --output /google-java-format "${url}"; \
    echo "${checksum}  /google-java-format" | sha256sum -c; \
    chmod 0555 /google-java-format

FROM ${SWIFT_IMAGE} AS java-build
COPY --from=java-download /google-java-format /google-java-format
RUN test "$(/google-java-format --version 2>&1)" = \
      "google-java-format: Version 1.35.0" \
    && printf 'google-java-format=1.35.0\n' >/lint-tool-version

FROM ${DISTROLESS_CC} AS java
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/java /licenses/java
COPY --from=java-build /google-java-format /usr/local/bin/google-java-format
COPY --from=java-build /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["google-java-format", "--replace"]

FROM ${GO_IMAGE} AS go-source
RUN cp /usr/local/go/bin/gofmt /gofmt \
    && test "$(go version /gofmt)" = "/gofmt: go1.26.5" \
    && printf 'go=1.26.5\n' >/lint-tool-version

FROM scratch AS go
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/go /licenses/go
COPY --from=go-source /gofmt /gofmt
COPY --from=go-source /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/gofmt", "-w"]

FROM ${RUST_IMAGE} AS rust-build
RUN rustup component add rustfmt \
    && cp "$(rustup which rustfmt)" /rustfmt \
    && mkdir /rustfmt-libs \
    && cp /usr/local/rustup/toolchains/*/lib/librustc_driver*.so \
      /rustfmt-libs/ \
    && strip --strip-unneeded /rustfmt /rustfmt-libs/*.so \
    && LD_LIBRARY_PATH=/rustfmt-libs /rustfmt --version \
      | grep -qx \
        'rustfmt 1\.9\.0-stable ([0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f] [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])' \
    && printf 'rustfmt=1.9.0\n' >/lint-tool-version

FROM scratch AS rust
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/rust /licenses/rust
COPY --from=rust-build /lib /lib
COPY --from=rust-build /usr/lib/libgcc_s.so.1 /usr/lib/libgcc_s.so.1
COPY --from=rust-build /rustfmt /bin/rustfmt
COPY --from=rust-build /rustfmt-libs /lib
COPY --from=rust-build /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/bin/rustfmt"]

FROM ${JAVA_JDK_IMAGE} AS kotlin-build
RUN apk add --no-cache \
      libexpat=2.8.2-r0 \
      p11-kit=0.26.2-r0 \
      p11-kit-trust=0.26.2-r0
ADD --checksum=sha256:01b2e0ef893383a50dbeb13970fe7fa3be36ca3e83259e01649945b09d736985 \
    https://github.com/ktlint/ktlint/releases/download/1.3.0/ktlint \
    /ktlint
RUN cp /ktlint /ktlint.jar \
    && chmod 0444 /ktlint.jar \
    && modules="$(jdeps \
      --ignore-missing-deps \
      --multi-release 21 \
      --print-module-deps \
      /ktlint.jar)" \
    && jlink \
      --add-modules "$modules" \
      --compress=2 \
      --no-header-files \
      --no-man-pages \
      --strip-debug \
      --output /opt/ktlint-java \
    && find /opt/ktlint-java/bin \
      -mindepth 1 \
      ! -name java \
      -delete \
    && test "$(java -jar /ktlint.jar --version)" = \
      "ktlint version 1.3.0" \
    && printf 'ktlint=1.3.0\n' >/lint-tool-version

FROM scratch AS kotlin
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/kotlin /licenses/kotlin
COPY --from=kotlin-build /lib /lib
COPY --from=kotlin-build /usr/lib /usr/lib
COPY --from=kotlin-build /opt/ktlint-java /opt/ktlint-java
COPY --from=kotlin-build /ktlint.jar /ktlint.jar
COPY --from=launcher-build /kotlin-format /kotlin-format
COPY --from=kotlin-build /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/kotlin-format"]

FROM downloader AS taplo-download
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) asset_arch="x86_64"; checksum="8fe196b894ccf9072f98d4e1013a180306e17d244830b03986ee5e8eabeb6156" ;; \
      arm64) asset_arch="aarch64"; checksum="033681d01eec8376c3fd38fa3703c79316f5e14bb013d859943b60a07bccdcc3" ;; \
      *) exit 2 ;; \
    esac; \
    url="https://github.com/tamasfe/taplo/releases/download/0.10.0/taplo-linux-${asset_arch}.gz"; \
    curl --fail --location --output /taplo.gz "${url}"; \
    echo "${checksum}  /taplo.gz" | sha256sum -c; \
    gunzip /taplo.gz; \
    chmod 0555 /taplo; \
    test "$(/taplo --version)" = "taplo 0.10.0"; \
    printf 'taplo=0.10.0\n' >/lint-tool-version

FROM scratch AS taplo
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/taplo /licenses/taplo
COPY --from=taplo-download /taplo /taplo
COPY --from=taplo-download /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/taplo", "format"]

FROM ${ALPINE_3_23} AS xml-build
RUN apk add --no-cache build-base ca-certificates curl xz
COPY images/xml_entrypoint.c /xml_entrypoint.c
RUN curl --fail --location \
      --output /libxml2.tar.xz \
      https://download.gnome.org/sources/libxml2/2.15/libxml2-2.15.3.tar.xz \
    && echo "78262a6e7ac170d6528ebfe2efccdf220191a5af6a6cd61ea4a9a9a5042c7a07  /libxml2.tar.xz" \
      | sha256sum -c \
    && mkdir /source \
    && tar --extract --xz --file /libxml2.tar.xz \
      --directory /source --strip-components 1 \
    && cd /source \
    && ./configure \
      --prefix=/opt/libxml2 \
      --without-debug \
      --without-history \
      --without-http \
      --without-iconv \
      --without-lzma \
      --without-python \
      --without-zlib \
    && make -j2 \
    && make install-strip \
    && cc -static -Os \
      /xml_entrypoint.c \
      -o /xml-format \
    && test "$(/opt/libxml2/bin/xmllint --version 2>&1 \
      | head -n 1)" = \
      "/opt/libxml2/bin/xmllint: using libxml version 21503" \
    && printf 'libxml2=2.15.3\n' >/lint-tool-version

FROM scratch AS xml
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/xml /licenses/xml
COPY --from=xml-build /opt/libxml2 /opt/libxml2
COPY --from=xml-build /lib /lib
COPY --from=xml-build /xml-format /xml-format
COPY --from=xml-build /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["/xml-format"]

FROM ${SWIFT_IMAGE} AS swift-build
ENV SWIFTC_MAXIMUM_DETERMINISM=1
COPY --from=swift-format-source \
    /swift-format-source \
    /swift-format-source
COPY images/swift/Package.resolved \
    /swift-format-source/Package.resolved
RUN echo "599a6beab32b6e9164edaf89903fd30d5c4b9dcc46083b15ef7c4f886b386636  /swift-format-source/Package.resolved" \
      | sha256sum -c \
    && swift package \
      --package-path /swift-format-source \
      resolve \
    && echo "599a6beab32b6e9164edaf89903fd30d5c4b9dcc46083b15ef7c4f886b386636  /swift-format-source/Package.resolved" \
      | sha256sum -c \
    && swift build \
      --package-path /swift-format-source \
      --configuration release \
      --disable-sandbox \
      --product swift-format \
    && binary_path="$(swift build \
      --package-path /swift-format-source \
      --configuration release \
      --show-bin-path)" \
    && cp "$binary_path/swift-format" /swift-format \
    && strip \
      --strip-unneeded \
      --remove-section=.note.gnu.build-id \
      --remove-section=.swift_modhash \
      /swift-format \
    && test "$(/swift-format --version)" = "603.0.0" \
    && printf 'swift-format=603.0.0\n' >/lint-tool-version

FROM ${DISTROLESS_CC} AS swift
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/swift /licenses/swift
COPY --from=swift-build /swift-format /usr/bin/swift-format
COPY --from=swift-build /usr/lib/swift/linux /usr/lib/swift/linux
COPY --from=swift-build /lint-tool-version /lint-tool-version
USER 65532:65532
ENTRYPOINT ["swift-format", "format", "--in-place"]

FROM downloader AS csharp-download
RUN curl --fail --location \
      --output /csharpier.nupkg \
      https://api.nuget.org/v3-flatcontainer/csharpier/1.3.0/csharpier.1.3.0.nupkg \
    && echo "87089e1284137004f2299219ec241258034f06ffa9112860e9c0d75391087b15  /csharpier.nupkg" \
      | sha256sum -c \
    && mkdir /csharpier /tools \
    && busybox unzip -q /csharpier.nupkg -d /csharpier \
    && cp -R /csharpier/tools/net10.0/any/. /tools/ \
    && find /tools -name '*.pdb' -delete

FROM ${DOTNET_RUNTIME} AS csharp-runtime

FROM csharp-runtime AS csharp-build
COPY --from=csharp-download /tools /tools
RUN mkdir -p /tmp \
    && chmod 1777 /tmp \
    && test "$(DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
      dotnet /tools/CSharpier.dll --version)" = "1.3.0" \
    && printf 'csharpier=1.3.0\n' >/lint-tool-version

FROM scratch AS csharp
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/csharp /licenses/csharp
COPY --from=csharp-build /tools /tools
COPY --from=csharp-runtime /lib /lib
COPY --from=csharp-runtime /usr/lib /usr/lib
COPY --from=csharp-runtime /usr/share/dotnet /usr/share/dotnet
COPY --from=csharp-build /lint-tool-version /lint-tool-version
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
USER 65532:65532
ENTRYPOINT ["/usr/share/dotnet/dotnet", "/tools/CSharpier.dll", "format", "--no-cache"]

FROM julia-formatter-download AS julia-formatter-source
RUN mkdir /julia-formatter-source \
    && tar -xzf \
      /downloads/JuliaFormatter-e421b4f670555ff650f1b77df47cd42c806d8372.tar.gz \
      -C /julia-formatter-source

FROM ${JULIA_IMAGE} AS julia-build
RUN mkdir -p \
      /opt/julia-depot/dev/JuliaFormatter \
      /opt/julia-depot/environments/v1.12
COPY --from=julia-formatter-source \
    /julia-formatter-source \
    /opt/julia-depot/dev/JuliaFormatter
COPY images/julia/Project.toml \
    /opt/julia-depot/environments/v1.12/Project.toml
COPY images/julia/Manifest.toml \
    /opt/julia-depot/environments/v1.12/Manifest.toml
RUN echo "37746ac54a1c1565e2629782a858586e64581469e5b23b6e1a212c839824198f  /opt/julia-depot/environments/v1.12/Project.toml" \
      | sha256sum -c \
    && echo "68ac859b5d87a9f835ed8a30ed669b21bc7c7402e833f8b5ffc1a9260669d505  /opt/julia-depot/environments/v1.12/Manifest.toml" \
      | sha256sum -c \
    && JULIA_DEPOT_PATH=/opt/julia-depot \
      JULIA_PKG_PRECOMPILE_AUTO=0 \
      julia --startup-file=no --project=@v1.12 -e \
      'using Pkg; Pkg.instantiate()' \
    && echo "37746ac54a1c1565e2629782a858586e64581469e5b23b6e1a212c839824198f  /opt/julia-depot/environments/v1.12/Project.toml" \
      | sha256sum -c \
    && echo "68ac859b5d87a9f835ed8a30ed669b21bc7c7402e833f8b5ffc1a9260669d505  /opt/julia-depot/environments/v1.12/Manifest.toml" \
      | sha256sum -c \
    && test "$(JULIA_DEPOT_PATH=/opt/julia-depot \
      JULIA_PKG_PRECOMPILE_AUTO=0 \
      julia --compiled-modules=no --startup-file=no --project=@v1.12 -e \
      'using JuliaFormatter; print(pkgversion(JuliaFormatter))')" = \
      "2.12.3" \
    && printf 'juliaformatter=2.12.3\n' >/lint-tool-version \
    && rm -rf /root/.julia /tmp/*
RUN rm -f /usr/local/julia/libexec/julia/lld \
    && rm -rf /usr/local/julia/share/doc \
    && find \
      /usr/local/julia/share/julia \
      /opt/julia-depot/dev \
      /opt/julia-depot/packages \
      -type d \( -name docs -o -name test \) \
      -prune -exec rm -rf {} + \
    && rm -rf \
      /opt/julia-depot/compiled \
      /opt/julia-depot/logs \
      /opt/julia-depot/registries \
      /opt/julia-depot/scratchspaces \
    && chmod -R a+rX /opt/julia-depot
RUN mkdir -p /opt/runtime/lib64 \
    && if [ -e /lib64/ld-linux-x86-64.so.2 ]; then \
      cp -L /lib64/ld-linux-x86-64.so.2 /opt/runtime/lib64/; \
    fi

FROM scratch AS julia
LABEL org.opencontainers.image.source="https://github.com/trycopilotai/lint"
COPY --from=license-notices /licenses /licenses
COPY images/licenses/julia /licenses/julia
COPY --from=julia-build /lib /lib
COPY --from=julia-build /opt/runtime/lib64 /lib64
COPY --from=julia-build \
    /usr/lib/locale/C.utf8 \
    /usr/lib/locale/C.utf8
COPY --from=julia-build /usr/local/julia /usr/local/julia
COPY --from=julia-build /opt/julia-depot /opt/julia-depot
COPY --from=julia-build /lint-tool-version /lint-tool-version
COPY --chmod=0555 images/julia_entrypoint.jl /julia-format.jl
ENV LANG=C.UTF-8
USER 65532:65532
ENTRYPOINT ["/usr/local/julia/bin/julia", "--compiled-modules=no", "--compile=min", "--optimize=0", "--startup-file=no", "/julia-format.jl"]
