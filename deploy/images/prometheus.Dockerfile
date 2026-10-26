# Prometheus binaries are unchanged from the verified official release image.
FROM prom/prometheus:v3.15.1@sha256:d445d1806b38f85b5c394a1b87f94113fde0473b45245922c2e8b1bd41fa68ae AS upstream
FROM alpine:3.24
RUN apk upgrade --no-cache && apk add --no-cache ca-certificates tzdata \
    && mkdir -p /prometheus /etc/prometheus \
    && chown -R 65534:65534 /prometheus /etc/prometheus && chmod g+w /prometheus
COPY --from=upstream /bin/prometheus /bin/prometheus
COPY --from=upstream /bin/promtool /bin/promtool
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
      io.tripplanner.upstream="prometheus-v3.15.1-unmodified-binaries"
ENTRYPOINT ["/bin/prometheus"]
CMD ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus"]
