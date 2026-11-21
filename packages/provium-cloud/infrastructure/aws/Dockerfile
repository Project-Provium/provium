FROM python:3.12-slim
WORKDIR /opt/workspace
COPY packages/ packages/
COPY examples/ examples/
RUN python -m pip install --no-cache-dir \
    ./packages/provium ./packages/provium-pipeline \
    './packages/provium-cloud[aws,postgres]' ./examples/provium-text-pipeline \
    'pytest>=8.3,<10'
COPY runtime/ /opt/cloud-tests/
RUN useradd --create-home --uid 10001 cloudtest
USER cloudtest
WORKDIR /tmp
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/opt/cloud-tests/bootstrap.py"]
CMD ["provium", "--help"]
