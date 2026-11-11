# Build from the repository root; the receiver uses only Python's standard library.
ARG PYTHON_BASE=python:3.13.18-alpine3.24@sha256:1e1f6334529d1fb26a2ef619d7cffb30a7c7d7cc1cc7338ebee283a58d1aeaca
FROM ${PYTHON_BASE} AS python-upstream
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS busybox-security
RUN apk upgrade --no-cache \
    && apk add --no-cache alpine-sdk linux-headers openssl-dev utmps-dev utmps-static perl zip
COPY deploy/images/busybox-backport /trip-busybox
RUN sh /trip-busybox/build.sh

FROM busybox-security AS cpython-security
# Use the same Alpine SDK already needed for BusyBox. Copy the exact official
# interpreter, headers and configuration, then compile only its patched extension.
COPY --from=python-upstream /usr/local/ /usr/local/
RUN apk add --no-cache bluez-dev gdbm libbz2 libffi libncursesw libpanelw libuuid readline sqlite-libs xz
ADD --checksum=sha256:abb0102e6d4ae027509bcb82327d9dd85515b44a3ba8ad538cfc5e9847029f68 https://www.python.org/ftp/python/3.13.18/Python-3.13.18.tar.xz /tmp/Python-3.13.18.tar.xz
COPY backend/security/cpython-backports /patches
RUN sh /patches/build.sh

FROM ${PYTHON_BASE}
RUN apk upgrade --no-cache && apk add --no-cache 'zlib>=1.4-r0' \
    && python -m pip uninstall --yes pip setuptools \
    && apk add --no-cache gdbm libbz2 libcrypto3 libffi libncursesw libpanelw libssl3 libuuid readline xz-libs \
    && apk del .python-rundeps sqlite-libs \
    && rm -rf /usr/local/lib/python3.13/sqlite3 \
    && rm /usr/local/lib/python3.13/lib-dynload/_sqlite3*.so \
    && python -c "import http.server, json, hashlib, signal, importlib.util; assert importlib.util.find_spec('sqlite3') is None; assert importlib.util.find_spec('_sqlite3') is None" \
    && mkdir -p /inbox && chown 1000:1000 /inbox
COPY --from=busybox-security /out/ /tmp/trip-busybox/
RUN sh /tmp/trip-busybox/install.sh && rm -rf /tmp/trip-busybox
COPY --from=cpython-security /out/lib-dynload/ /usr/local/lib/python3.13/lib-dynload/
COPY --from=cpython-security /out/http/ /usr/local/lib/python3.13/http/
COPY --from=cpython-security /out/stdlib/ /usr/local/lib/python3.13/
COPY --from=cpython-security /out/provenance/ /usr/local/share/tripplanner/cpython-backports/
WORKDIR /app
COPY backend/scripts/operational_alert_receiver.py /app/receiver.py
ENV ALERT_INBOX=/inbox/alerts.jsonl PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
EXPOSE 9088
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner"
ENTRYPOINT ["python", "/app/receiver.py"]
