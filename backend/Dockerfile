# 后端服务镜像: Python 3.13 精简版
ARG PYTHON_BASE=python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ARG NATIVE_BUILD_BASE=python:3.11.18-slim-trixie@sha256:ffe4b0c76963e64f05484d74dbb3f67fad1effcf3c5c22c2235f0fafc8a9c98f
FROM ${NATIVE_BUILD_BASE} AS native-build-inputs
USER root
COPY security/native-backports/build-inputs.sh security/native-backports/sources.sha256 /patches/
RUN bash /patches/build-inputs.sh --dependencies-only
RUN --mount=type=cache,id=trip-native-sources,target=/native-source-cache \
    bash /patches/build-inputs.sh --sources-only /native-source-cache
FROM native-build-inputs AS glibc-compiled
COPY security/native-backports/build-glibc.sh security/native-backports/glibc-patches.txt security/native-backports/glibc-build.sha256 /patches/
COPY security/native-backports/patches/glibc-*.patch /patches/patches/
RUN bash /patches/build-glibc.sh

FROM native-build-inputs AS glibc-hardened-compiled
COPY security/native-backports/build-glibc.sh security/native-backports/glibc-patches.txt security/native-backports/glibc-build.sha256 /patches/
COPY security/native-backports/patches/glibc-*.patch /patches/patches/
COPY security/glibc-hardening/tls-guard.patch security/glibc-hardening/stack-randomization.patch security/glibc-hardening/arena-randomization.patch security/glibc-hardening/hardening-patches.sha256 /hardening/
RUN bash /patches/build-glibc.sh --hardening

FROM glibc-hardened-compiled AS glibc-backport
COPY security/native-backports/check-glibc.sh security/native-backports/glibc-tests.sha256 security/native-backports/glibc-boundary-regression.c /patches/
COPY security/native-backports/test-patches/ /patches/test-patches/
RUN bash /patches/check-glibc.sh
COPY security/glibc-hardening/ /hardening/
RUN bash /hardening/check-hardening.sh

FROM native-build-inputs AS libstdcxx-backport
COPY security/native-backports/build-libstdcxx.sh security/native-backports/libstdcxx-patches.sha256 /patches/
COPY security/native-backports/patches/gcc-*.patch /patches/patches/
RUN bash /patches/build-libstdcxx.sh

FROM native-build-inputs AS util-linux-backport
COPY security/native-backports/build-util-linux.sh security/native-backports/util-linux-patches.txt security/native-backports/util-linux-patches.sha256 security/native-backports/openat2-regression.c security/native-backports/login-pam-rhost-regression.c security/native-backports/check-login-pam-rhost.py /patches/
COPY security/native-backports/patches/util-*.patch /patches/patches/
RUN bash /patches/build-util-linux.sh

FROM ${NATIVE_BUILD_BASE} AS pam-backport
COPY security/pam-backport/build-inputs.sh security/pam-backport/sources.sha256 /pam-patches/
RUN bash /pam-patches/build-inputs.sh
COPY security/pam-backport /pam-patches
RUN bash /pam-patches/build.sh

FROM ${NATIVE_BUILD_BASE} AS dash-backport
COPY security/dash-backport/build-inputs.sh security/dash-backport/sources.sha256 /dash-patches/
RUN bash /dash-patches/build-inputs.sh
COPY security/dash-backport /dash-patches
RUN bash /dash-patches/build.sh

FROM ${PYTHON_BASE} AS cpython-backport
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev patch xz-utils curl \
    && rm -rf /var/lib/apt/lists/*
# Resume the observed truncated source transfer; curl validates range offsets,
# and the release checksum is mandatory before any source is used.
RUN for attempt in 1 2 3; do \
      curl --fail --location --connect-timeout 20 --max-time 180 --continue-at - \
        --output /tmp/Python-3.13.18.tar.xz https://www.python.org/ftp/python/3.13.18/Python-3.13.18.tar.xz && break; \
      test "$attempt" -lt 3 || exit 1; \
    done \
    && echo 'abb0102e6d4ae027509bcb82327d9dd85515b44a3ba8ad538cfc5e9847029f68  /tmp/Python-3.13.18.tar.xz' | sha256sum -c -
RUN apt-get update && apt-get install -y --no-install-recommends libbluetooth-dev \
    && rm -rf /var/lib/apt/lists/*
COPY security/cpython-backports /patches
RUN sh /patches/build.sh

FROM ${PYTHON_BASE} AS python-dependencies
COPY --from=cpython-backport /out/lib-dynload/ /usr/local/lib/python3.13/lib-dynload/
COPY --from=cpython-backport /out/http/ /usr/local/lib/python3.13/http/
COPY --from=cpython-backport /out/stdlib/ /usr/local/lib/python3.13/
COPY --from=cpython-backport /out/provenance/ /usr/local/share/tripplanner/cpython-backports/
WORKDIR /app
COPY requirements.txt requirements.lock ./
# Keep locked wheel installation independent of native source rebuilds.
RUN --mount=type=cache,target=/root/.cache/pip \
    for attempt in 1 2 3; do \
      pip install --retries 5 --timeout 120 --upgrade pip==26.2.2 setuptools==84.2.1 \
        && pip install --retries 5 --timeout 120 -r requirements.txt && pip check && exit 0; \
      if [ "$attempt" -lt 3 ]; then sleep 2; fi; \
    done; exit 1

FROM python-dependencies AS runtime-security-base
USER root
# Refresh signed distribution security packages; --pull alone can retain packages
# from the base image's publication date.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY runtime-security-packages.json /tmp/runtime-security-packages.json
COPY scripts/install_runtime_security_patches.py /tmp/install_runtime_security_patches.py
RUN TRIP_SECURITY_IMAGE_BUILD=1 python /tmp/install_runtime_security_patches.py --lock /tmp/runtime-security-packages.json \
    && rm -f /usr/bin/infocmp && test ! -e /usr/bin/infocmp \
    && rm -rf /var/lib/apt/lists/*

FROM runtime-security-base
COPY --from=glibc-backport /out/ /tmp/native-backports/
COPY --from=libstdcxx-backport /out/ /tmp/native-backports/
COPY --from=util-linux-backport /out/ /tmp/native-backports/
COPY --from=pam-backport /out/packages/ /tmp/native-backports/
COPY --from=dash-backport /out/packages/ /tmp/native-backports/
RUN DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-remove /tmp/native-backports/*.deb \
    && rm -rf /tmp/native-backports
COPY --from=pam-backport /out/provenance/ /usr/local/share/tripplanner/pam-backport/
COPY --from=dash-backport /out/provenance/ /usr/local/share/tripplanner/dash-backport/
COPY --from=glibc-backport /hardening-proof/ /usr/local/share/tripplanner/glibc-hardening/
# 2. 复制应用代码
COPY app ./app
COPY evals ./evals
COPY run.py .
COPY scripts ./scripts
# 手工维护的旅游知识随镜像发布；运行数据通过 Compose 绑定到宿主机持久化。
COPY data/knowledge ./data/knowledge

# 3. 非 root 用户运行；显式挂载的数据目录仍可按授权权限读写。
RUN (id -u appuser >/dev/null 2>&1 || useradd -m -K SUB_UID_COUNT=0 -K SUB_GID_COUNT=0 appuser) \
    && mkdir -p logs data/chroma runtime \
    && chown -R appuser:appuser /app
# Apply after the final ownership setup; retain runtime libraries and inventory
# tooling, and record actual removed binaries/aliases without erasing metadata.
RUN TRIP_SECURITY_IMAGE_BUILD=1 python scripts/restrict_runtime_tools.py --apply \
    --manifest /usr/local/share/tripplanner/restricted-runtime-tools.json
USER appuser

EXPOSE 9000

# Release metadata must not invalidate the locked dependency installation layer.
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner"

# Internal capabilities only. Java/Flyway owns business tables and task scheduling.
CMD ["uvicorn", "app.agent_api.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1"]
