# Preserve the official PostgreSQL17 server and entrypoint, refreshing signed OS fixes.
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

FROM postgres:17.11-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b
RUN apk upgrade --no-cache && apk add --no-cache 'zlib>=1.4-r0'
COPY --from=busybox-security /out/ /tmp/trip-busybox/
RUN sh /tmp/trip-busybox/install.sh && rm -rf /tmp/trip-busybox
COPY --from=gosu-security /out/gosu /usr/local/bin/gosu
COPY --from=gosu-security /out/gosu.buildinfo /usr/local/share/tripplanner/gosu/gosu.buildinfo
COPY --from=gosu-security /src/LICENSE /usr/local/share/tripplanner/gosu/LICENSE
ENV GOSU_VERSION=1.20
RUN gosu --version && gosu nobody id
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner"
