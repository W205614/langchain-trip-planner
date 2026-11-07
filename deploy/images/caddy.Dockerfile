# Official Caddy source and standard modules, with upstream security dependencies.
FROM golang:1.27.3-alpine AS build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
WORKDIR /src
ADD --checksum=sha256:7b8de20699ec38a43c97d70dd5347ed8e19d3db96d0c9d76ded020f018cb6e5e https://codeload.github.com/caddyserver/caddy/tar.gz/0d98caea58f794f88e709b1bef2880996a471bca /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src
# crypto v0.92.0 and certificates' upstream cddde30af36e migration remove
# go-jose/v3 from the linked PKI dependency chain;
# x/net v0.62.0 fixes GO-2026-6860. Keep generated module checksums reviewed.
COPY caddy.go.mod ./go.mod
COPY caddy.go.sum ./go.sum
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    for attempt in 1 2 3; do \
      go mod download && break; \
      if [ "$attempt" -eq 3 ]; then exit 1; fi; sleep 2; \
    done \
    && go mod verify \
    && go build -p 2 -mod=readonly -trimpath -ldflags="-s -w -X github.com/caddyserver/caddy/v2.CustomVersion=v2.11.9-security1" -o /out/caddy ./cmd/caddy \
    && go version -m /out/caddy > /out/buildinfo.txt \
    && ! grep -q 'github.com/go-jose/go-jose/v3' /out/buildinfo.txt \
    && grep -q 'golang.org/x/net[[:space:]]v0.62.0' /out/buildinfo.txt \
    && grep -q 'go.step.sm/crypto[[:space:]]v0.92.0' /out/buildinfo.txt
# Preserve upstream entrypoint, ACME support, trust roots and persistent directories.
FROM caddy:2.11.9-alpine
RUN apk upgrade --no-cache
COPY --from=build /out/caddy /usr/bin/caddy
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner" \
      io.tripplanner.upstream="caddy-v2.11.9-security1"
