# Same official Alertmanager source; only reviewed dependency/toolchain security patches.
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS busybox-security
RUN apk upgrade --no-cache \
    && apk add --no-cache alpine-sdk linux-headers openssl-dev utmps-dev utmps-static perl zip
COPY busybox-backport /trip-busybox
RUN sh /trip-busybox/build.sh

FROM golang:1.27.3-alpine AS build
# The local build proxy truncates Go HTTP/2 downloads; this affects build fetches only.
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org GODEBUG=http2client=0
WORKDIR /src
ADD --checksum=sha256:5c5c72ec6b4c4746d523f2ed1994dfec4b94f7457e530ee6fb16cfec158e545e https://codeload.github.com/prometheus/alertmanager/tar.gz/73c6bfe7393929211294c1954f30d8ed78e4d0ad /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src
# Preserve the exact official embedded UI; rebuilding Go needs these generated
# assets, which are intentionally absent from the source archive.
ADD --checksum=sha256:881fe0f7beb9573f35be47dd233af6220e72c8db72c03bcd1780402eb8dc1307 https://github.com/prometheus/alertmanager/releases/download/v0.34.1/alertmanager-web-ui-0.34.1.tar.gz /tmp/web-ui.tar.gz
RUN tar -xzf /tmp/web-ui.tar.gz -C /src/ui/app
COPY alertmanager.go.mod ./go.mod
COPY alertmanager.go.sum ./go.sum
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    for attempt in 1 2 3; do \
      go mod download && break; \
      if [ "$attempt" -eq 3 ]; then exit 1; fi; sleep 2; \
    done \
    && go mod verify \
    && go build -p 2 -mod=readonly -trimpath -ldflags="-s -w -X github.com/prometheus/common/version.Version=0.34.1 -X github.com/prometheus/common/version.Revision=73c6bfe7393929211294c1954f30d8ed78e4d0ad-security1" -o /out/alertmanager ./cmd/alertmanager \
    && go build -p 2 -mod=readonly -trimpath -ldflags="-s -w -X github.com/prometheus/common/version.Version=0.34.1 -X github.com/prometheus/common/version.Revision=73c6bfe7393929211294c1954f30d8ed78e4d0ad-security1" -o /out/amtool ./cmd/amtool
RUN for binary in alertmanager amtool; do \
      go version -m /out/$binary > /out/$binary.buildinfo \
      && grep -q 'go.opentelemetry.io/otel/sdk[[:space:]]v1.45.0' /out/$binary.buildinfo \
      && grep -q 'go.opentelemetry.io/otel/exporters/otlp/otlptrace[[:space:]]v1.45.0' /out/$binary.buildinfo || exit 1; \
    done
FROM alpine:3.24
RUN apk upgrade --no-cache && apk add --no-cache ca-certificates tzdata \
    && mkdir -p /alertmanager /etc/alertmanager \
    && chown -R 65534:65534 /alertmanager /etc/alertmanager && chmod -R g+w /alertmanager
COPY --from=busybox-security /out/ /tmp/trip-busybox/
RUN sh /tmp/trip-busybox/install.sh && rm -rf /tmp/trip-busybox
COPY --from=build /out/alertmanager /bin/alertmanager
COPY --from=build /out/amtool /bin/amtool
COPY --from=build /src/examples/ha/alertmanager.yml /etc/alertmanager/alertmanager.yml
COPY --from=build /src/LICENSE /LICENSE
COPY --from=build /src/NOTICE /NOTICE
USER 65534:65534
WORKDIR /alertmanager
EXPOSE 9093
VOLUME ["/alertmanager"]
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner" \
      io.tripplanner.upstream="alertmanager-v0.34.1-security1"
ENTRYPOINT ["/bin/alertmanager"]
CMD ["--config.file=/etc/alertmanager/alertmanager.yml", "--storage.path=/alertmanager"]
