# syntax=docker/dockerfile:1.18

ARG ALPINE_3_23="alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40"
ARG DISTROLESS_CC="gcr.io/distroless/cc-debian13:nonroot@sha256:d97bc0a941b8d4be647dc0ee75b264ddbb772f1ac5ba690a4309c00723b23775"
ARG DOTNET_RUNTIME="mcr.microsoft.com/dotnet/runtime:10.0.7-alpine3.23@sha256:405a4e86f50ab5fd78c65bab461499913088d1702da4faf6236426653cc052c4"
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

FROM ${ALPINE_3_23} AS launcher-build
RUN apk add --no-cache build-base
COPY images/kotlin_entrypoint.c /kotlin_entrypoint.c
RUN cc -static -Os /kotlin_entrypoint.c -o /kotlin-format

FROM ${NODE_IMAGE} AS prettier-build
RUN npm install --global --omit=dev prettier@3.7.4 \
    && npm cache clean --force

FROM scratch AS prettier
COPY --from=prettier-build /lib /lib
COPY --from=prettier-build /usr/lib /usr/lib
COPY --from=prettier-build /usr/local/bin/node /usr/local/bin/node
COPY --from=prettier-build \
    /usr/local/lib/node_modules/prettier \
    /usr/local/lib/node_modules/prettier
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/node", "/usr/local/lib/node_modules/prettier/bin/prettier.cjs", "--write", "--no-config", "--no-editorconfig", "--ignore-unknown", "--print-width", "60", "--prose-wrap", "always", "--trailing-comma", "none"]

FROM downloader AS buildifier-download
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) checksum="6ceb7b0ab7cf66fceccc56a027d21d9cc557a7f34af37d2101edb56b92fcfa1a" ;; \
      arm64) checksum="3baa1cf7eb41d51f462fdd1fff3a6a4d81d757275d05b2dd5f48671284e9a1a5" ;; \
      *) exit 2 ;; \
    esac; \
    url="https://github.com/bazelbuild/buildtools/releases/download/v8.2.1/buildifier-linux-${TARGETARCH}"; \
    curl --fail --location --output /buildifier "${url}"; \
    echo "${checksum}  /buildifier" | sha256sum -c; \
    chmod 0555 /buildifier

FROM scratch AS buildifier
COPY --from=buildifier-download /buildifier /buildifier
USER 65532:65532
ENTRYPOINT ["/buildifier", "-mode=fix"]

FROM ${PYTHON_IMAGE} AS black-build
RUN pip install --no-cache-dir black==24.10.0 \
    && rm -rf \
      /usr/local/bin/pip* \
      /usr/local/lib/python3.13/ensurepip \
      /usr/local/lib/python3.13/site-packages/pip*

FROM scratch AS black
COPY --from=black-build /lib /lib
COPY --from=black-build /usr/lib /usr/lib
COPY --from=black-build /usr/local /usr/local
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/python3", "-m", "black", "--quiet", "--line-length", "88"]

FROM ${PYTHON_IMAGE} AS requirements-build
RUN rm -rf \
      /usr/local/bin/pip* \
      /usr/local/lib/python3.13/ensurepip \
      /usr/local/lib/python3.13/site-packages/pip*

FROM scratch AS requirements
COPY --from=requirements-build /lib /lib
COPY --from=requirements-build /usr/lib /usr/lib
COPY --from=requirements-build /usr/local /usr/local
COPY --chmod=0555 images/requirements_entrypoint.py /requirements-format
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/python3", "/requirements-format"]

FROM downloader AS shfmt-download
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) checksum="fb096c5d1ac6beabbdbaa2874d025badb03ee07929f0c9ff67563ce8c75398b1" ;; \
      arm64) checksum="32d92acaa5cd8abb29fc49dac123dc412442d5713967819d8af2c29f1b3857c7" ;; \
      *) exit 2 ;; \
    esac; \
    url="https://github.com/mvdan/sh/releases/download/v3.13.1/shfmt_v3.13.1_linux_${TARGETARCH}"; \
    curl --fail --location --output /shfmt "${url}"; \
    echo "${checksum}  /shfmt" | sha256sum -c; \
    chmod 0555 /shfmt

FROM scratch AS shfmt
COPY --from=shfmt-download /shfmt /shfmt
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
    chmod 0555 /clang-wheel/clang_format/data/bin/clang-format

FROM scratch AS clang
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

FROM ${DISTROLESS_CC} AS java
COPY --from=java-download /google-java-format /usr/local/bin/google-java-format
USER 65532:65532
ENTRYPOINT ["google-java-format", "--replace"]

FROM ${GO_IMAGE} AS go-source

FROM scratch AS go
COPY --from=go-source /usr/local/go/bin/gofmt /gofmt
USER 65532:65532
ENTRYPOINT ["/gofmt", "-w"]

FROM ${RUST_IMAGE} AS rust-build
RUN rustup component add rustfmt \
    && cp "$(rustup which rustfmt)" /rustfmt \
    && mkdir /rustfmt-libs \
    && cp /usr/local/rustup/toolchains/*/lib/librustc_driver*.so \
      /rustfmt-libs/ \
    && strip --strip-unneeded /rustfmt /rustfmt-libs/*.so

FROM scratch AS rust
COPY --from=rust-build /lib /lib
COPY --from=rust-build /usr/lib/libgcc_s.so.1 /usr/lib/libgcc_s.so.1
COPY --from=rust-build /rustfmt /bin/rustfmt
COPY --from=rust-build /rustfmt-libs /lib
USER 65532:65532
ENTRYPOINT ["/bin/rustfmt"]

FROM ${JAVA_JDK_IMAGE} AS kotlin-build
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
      -delete

FROM scratch AS kotlin
COPY --from=kotlin-build /lib /lib
COPY --from=kotlin-build /usr/lib /usr/lib
COPY --from=kotlin-build /opt/ktlint-java /opt/ktlint-java
COPY --from=kotlin-build /ktlint.jar /ktlint.jar
COPY --from=launcher-build /kotlin-format /kotlin-format
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
    chmod 0555 /taplo

FROM scratch AS taplo
COPY --from=taplo-download /taplo /taplo
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
      -o /xml-format

FROM scratch AS xml
COPY --from=xml-build /opt/libxml2 /opt/libxml2
COPY --from=xml-build /lib /lib
COPY --from=xml-build /xml-format /xml-format
USER 65532:65532
ENTRYPOINT ["/xml-format"]

FROM ${SWIFT_IMAGE} AS swift-source

FROM ${DISTROLESS_CC} AS swift
COPY --from=swift-source /usr/bin/swift-format /usr/bin/swift-format
COPY --from=swift-source /usr/lib/swift/linux /usr/lib/swift/linux
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

FROM scratch AS csharp
COPY --from=csharp-download /tools /tools
COPY --from=csharp-runtime /lib /lib
COPY --from=csharp-runtime /usr/lib /usr/lib
COPY --from=csharp-runtime /usr/share/dotnet /usr/share/dotnet
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
USER 65532:65532
ENTRYPOINT ["/usr/share/dotnet/dotnet", "/tools/CSharpier.dll", "format", "--no-cache"]

FROM ${JULIA_IMAGE} AS julia-build
RUN mkdir -p /opt/julia-depot \
    && JULIA_DEPOT_PATH=/opt/julia-depot julia --startup-file=no -e \
    'using Pkg; Pkg.add(PackageSpec(name="JuliaFormatter", version="2.12.3")); Pkg.precompile()' \
    && rm -rf /root/.julia /tmp/*

FROM scratch AS julia
COPY --from=julia-build /lib /lib
COPY --from=julia-build \
    /usr/lib/locale/C.utf8 \
    /usr/lib/locale/C.utf8
COPY --from=julia-build /usr/local/julia /usr/local/julia
COPY --from=julia-build /opt/julia-depot /opt/julia-depot
COPY --chmod=0555 images/julia_entrypoint.jl /julia-format.jl
ENV LANG=C.UTF-8
USER 65532:65532
ENTRYPOINT ["/usr/local/julia/bin/julia", "--startup-file=no", "/julia-format.jl"]
