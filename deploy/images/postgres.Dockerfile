# Preserve the official PostgreSQL17 server and entrypoint, refreshing signed OS fixes.
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS openssl-security
RUN apk upgrade --no-cache && apk add --no-cache alpine-sdk linux-headers perl
COPY openssl-backport /trip-openssl
RUN --mount=type=cache,id=trip-alpine-openssl-source,target=/sources,sharing=locked \
    sh /trip-openssl/build.sh

FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS busybox-security
RUN apk upgrade --no-cache \
    && apk add --no-cache alpine-sdk linux-headers openssl-dev utmps-dev utmps-static perl zip
COPY busybox-backport /trip-busybox
RUN sh /trip-busybox/build.sh

FROM golang:1.27.4-alpine AS gosu-security
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
WORKDIR /src
ADD --checksum=sha256:2b70da8a5480fafedc16bb1fc91b572f064d03df736cbd91a616af2f1b66ab51 https://codeload.github.com/tianon/gosu/tar.gz/29b939aa5bd4dc588af765b8248f6e39150d2565 /tmp/gosu-source.tar.gz
RUN tar -xzf /tmp/gosu-source.tar.gz --strip-components=1 -C /src
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go mod download && go mod verify \
    && go build -mod=readonly -trimpath -ldflags='-s -w' -o /out/gosu . \
    && go version -m /out/gosu > /out/gosu.buildinfo \
    && grep -q 'github.com/moby/sys/user[[:space:]]v0.4.1' /out/gosu.buildinfo

FROM postgres:17.12-alpine@sha256:526f86d93c6ea08fd3f1fe5605650cacc781b2c6013169a0ad8f42c7d3d84f32
RUN apk upgrade --no-cache && apk add --no-cache 'zlib>=1.4-r0'
COPY --from=busybox-security /out/ /tmp/trip-busybox/
RUN sh /tmp/trip-busybox/install.sh && rm -rf /tmp/trip-busybox
RUN --mount=type=bind,from=openssl-security,source=/out,target=/tmp/trip-openssl \
    sh /tmp/trip-openssl/install.sh
COPY --from=gosu-security /out/gosu /usr/local/bin/gosu
COPY --from=gosu-security /out/gosu.buildinfo /usr/local/share/tripplanner/gosu/gosu.buildinfo
COPY --from=gosu-security /src/LICENSE /usr/local/share/tripplanner/gosu/LICENSE
ENV GOSU_VERSION=1.20
RUN gosu --version && gosu nobody id
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner"
