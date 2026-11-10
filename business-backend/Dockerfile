FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS busybox-security
RUN apk upgrade --no-cache \
    && apk add --no-cache alpine-sdk linux-headers openssl-dev utmps-dev utmps-static perl zip
COPY security/busybox-backport /trip-busybox
RUN sh /trip-busybox/build.sh

FROM maven:3.9.9-eclipse-temurin-21 AS build
ARG MAVEN_MIRROR_URL=https://repo.maven.apache.org/maven2
WORKDIR /build
COPY pom.xml .
COPY .mvn/docker-settings.xml /tmp/maven-settings.xml
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -s /tmp/maven-settings.xml -B -ntp -DskipTests package

FROM eclipse-temurin:21-jre-alpine@sha256:ac05c3c42b7c2f1a52c9bd52f01041fa1958231b15ebeae7420429cbc5baf474
RUN apk upgrade --no-cache && apk add --no-cache curl
COPY --from=busybox-security /out/ /tmp/trip-busybox/
RUN sh /tmp/trip-busybox/install.sh && rm -rf /tmp/trip-busybox
# The application uses PostgreSQL and Java's TLS stack. Temurin's download-time
# GPG tooling (including its SQLite keybox) and GNU CLI bundle are not runtime
# dependencies; retain BusyBox utilities and the curl health check.
RUN apk del --no-network gnupg coreutils \
    && test -z "$(apk --no-network info -e sqlite-libs gnupg coreutils)"
WORKDIR /app
# Alpine's empty ping group occupies 999; retain the deployed numeric identity.
RUN sed -i 's/^ping:x:999:$/trip:x:999:/' /etc/group \
    && adduser -S -D -H -u 999 -G trip trip \
    && mkdir -p /data/uploads && chown -R trip:trip /data
COPY --from=build /build/target/business-backend-1.0.0-SNAPSHOT.jar /app/app.jar
USER trip
EXPOSE 9000
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner"
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
