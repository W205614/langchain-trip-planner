FROM maven:3.9.9-eclipse-temurin-21 AS build
ARG MAVEN_MIRROR_URL=https://repo.maven.apache.org/maven2
WORKDIR /build
COPY pom.xml .
COPY .mvn/docker-settings.xml /tmp/maven-settings.xml
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -s /tmp/maven-settings.xml -B -ntp -DskipTests package

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN groupadd --system trip && useradd --system --gid trip trip && mkdir -p /data/uploads && chown -R trip:trip /data
COPY --from=build /build/target/business-backend-1.0.0-SNAPSHOT.jar /app/app.jar
USER trip
EXPOSE 9000
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/W205614/langchain-trip-planner"
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
