# syntax=docker/dockerfile:1.7

# One image, two entrypoints. The predict and ingest functions differ only
# in their CMD. Keeping them together means one build, one digest, and no
# chance of the two drifting apart on the feature engineering code, which
# is the bug that would be hardest to find.

FROM public.ecr.aws/lambda/python:3.11 AS deps

# Build tools needed for packages that compile from source (e.g. pyarrow via meson).
RUN dnf install -y gcc gcc-c++ make cmake meson && dnf clean all

# CPU-only wheels. The default PyPI torch package pulls the full CUDA
# runtime and lands around 2.5 GB, which does not fit comfortably in a
# Lambda image and would do nothing for you here. This index gives you
# roughly 200 MB instead.
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torch==2.4.1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Strip test suites and bytecode caches. Worth about 150 MB, which is
# 150 MB less to pull on every cold start.
RUN find /var/lang/lib/python3.11/site-packages \
      \( -name "tests" -o -name "test" -o -name "__pycache__" \) \
      -type d -prune -exec rm -rf {} + 2>/dev/null || true


FROM deps AS runtime

# The checkpoint is baked in, not fetched at runtime. Two reasons.
# First, no cold start download. Second, the image digest then pins the
# exact model that is serving, so a Lambda rollback restores the model
# as well as the code. The workflow pulls this from the artifact bucket
# before building.
COPY artifacts/full_model.pth /opt/model/full_model.pth
COPY artifacts/model.sha256   /opt/model/model.sha256

COPY handler.py ${LAMBDA_TASK_ROOT}/
COPY core/      ${LAMBDA_TASK_ROOT}/core/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2

CMD ["handler.predict"]
