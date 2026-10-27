FROM python@sha256:5fb4bc53dd0180955e31e5245b648bbb24a4ac4ab03a1de81513642325220bcd
RUN apk upgrade --no-cache
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2 setuptools==80.10.2
RUN mkdir -p /inbox && chown 1000:1000 /inbox
WORKDIR /app
COPY receiver.py /app/receiver.py
ENV ALERT_INBOX=/inbox/alerts.jsonl PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
ENTRYPOINT ["python","/app/receiver.py"]
