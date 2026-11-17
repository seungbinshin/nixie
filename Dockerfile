# Agent sandbox image. No browser inside: browser work goes to the shared browser pool.
FROM python:3.12-slim
WORKDIR /app
# Corporate TLS proxy: put the proxy root CA (*.crt, PEM) in ./certs before building.
COPY certs/ /usr/local/share/ca-certificates/nixie/
RUN update-ca-certificates >/dev/null 2>&1 || true
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml ./
COPY nixie ./nixie
RUN pip install --no-cache-dir . && useradd -u 10001 -m nixie
USER 10001
EXPOSE 8700
ENTRYPOINT ["nixie", "serve", "--config"]
CMD ["/home/nixie/agent.yaml", "--host", "0.0.0.0", "--port", "8700"]
