FROM python:3.12-slim
WORKDIR /app
# Corporate TLS proxy: put the proxy root CA (*.crt, PEM) in ./certs before building.
COPY certs/ /usr/local/share/ca-certificates/nixie/
RUN update-ca-certificates >/dev/null 2>&1 || true
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
COPY pyproject.toml ./
COPY nixie ./nixie
RUN pip install --no-cache-dir .
VOLUME /app/data
EXPOSE 8700
ENTRYPOINT ["nixie", "serve", "--config"]
CMD ["/app/config.yaml"]
