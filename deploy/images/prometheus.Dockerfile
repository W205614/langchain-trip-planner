# Official Prometheus source and UI, with only the x/net security dependency raised.
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS busybox-security
RUN apk upgrade --no-cache \
    && apk add --no-cache alpine-sdk linux-headers openssl-dev utmps-dev utmps-static perl zip
COPY busybox-backport /trip-busybox
RUN sh /trip-busybox/build.sh

FROM prom/prometheus:v3.16.0@sha256:e04987641b57704f408e31f6373ba4138ecf27cb4a165d24aabb6c38fddd0f06 AS upstream
FROM golang:1.27.4-alpine AS build
# The local build proxy truncates Go HTTP/2 downloads; this affects build fetches only.
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOWORK=off GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org GODEBUG=http2client=0
WORKDIR /src
ADD --checksum=sha256:5691de55569c79ac40b5ddef0336cfd13f8b50ae85e541f401af16be45b9a270 https://codeload.github.com/prometheus/prometheus/tar.gz/e2e28adeee2c5883d000fc82aca6008cfbf7e96d /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src
ADD --checksum=sha256:000a45231f6dd22a5703218aad3ee11da125c8a44d1824a27670416c07531001 https://github.com/prometheus/prometheus/releases/download/v3.16.0/prometheus-web-ui-3.16.0.tar.gz /tmp/web-ui.tar.gz
RUN apk add --no-cache bash gzip \
    && tar -xzf /tmp/web-ui.tar.gz -C /src/web/ui \
    && bash scripts/compress_assets.sh
COPY prometheus.go.mod ./go.mod
COPY prometheus.go.sum ./go.sum
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    for attempt in 1 2 3; do \
      go mod download && break; \
      if [ "$attempt" -eq 3 ]; then exit 1; fi; sleep 2; \
    done \
    && go mod verify \
    && go build -p 2 -mod=readonly -trimpath -tags=netgo,builtinassets -ldflags="-s -w -X github.com/prometheus/common/version.Version=3.16.0 -X github.com/prometheus/common/version.Revision=e2e28adeee2c5883d000fc82aca6008cfbf7e96d-security1 -X github.com/prometheus/common/version.Branch=HEAD -X github.com/prometheus/common/version.BuildUser=trip-planner-security" -o /out/prometheus ./cmd/prometheus \
    && go build -p 2 -mod=readonly -trimpath -tags=netgo,builtinassets -ldflags="-s -w -X github.com/prometheus/common/version.Version=3.16.0 -X github.com/prometheus/common/version.Revision=e2e28adeee2c5883d000fc82aca6008cfbf7e96d-security1 -X github.com/prometheus/common/version.Branch=HEAD -X github.com/prometheus/common/version.BuildUser=trip-planner-security" -o /out/promtool ./cmd/promtool \
    && for binary in prometheus promtool; do \
      go version -m /out/$binary > /out/$binary.buildinfo; \
      grep -q 'golang.org/x/net[[:space:]]v0.62.0' /out/$binary.buildinfo \
      && grep -q 'github.com/klauspost/compress[[:space:]]v1.20.3' /out/$binary.buildinfo || exit 1; \
    done
FROM alpine:3.24
RUN apk upgrade --no-cache && apk add --no-cache ca-certificates tzdata \
    && mkdir -p /prometheus /etc/prometheus \
    && chown -R 65534:65534 /prometheus /etc/prometheus && chmod g+w /prometheus
COPY --from=busybox-security /out/ /tmp/trip-busybox/
RUN sh /tmp/trip-busybox/install.sh && rm -rf /tmp/trip-busybox
COPY --from=build /out/prometheus /bin/prometheus
COPY --from=build /out/promtool /bin/promtool
COPY --from=upstream /etc/prometheus/prometheus.yml /etc/prometheus/prometheus.yml
COPY --from=upstream /LICENSE /LICENSE
COPY --from=upstream /NOTICE /NOTICE
USER 65534:65534
WORKDIR /prometheus
EXPOSE 9090
VOLUME ["/prometheus"]
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner" \
      io.tripplanner.upstream="prometheus-v3.16.0-security1"
ENTRYPOINT ["/bin/prometheus"]
CMD ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus"]
