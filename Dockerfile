# ---- Stage 1: Blender installer ----
FROM debian:bullseye as blender-downloader

ARG BLENDER_VERSIONS="2.83.14 2.93.4 3.0.1 3.1.2 3.2.2 3.3.1 3.4.1 3.5.0 3.6.1 3.6.7 4.0.2 4.1.1 4.2.0 4.4.0"

RUN apt-get update && apt-get install -y wget

WORKDIR /downloads

# Download and extract each Blender version
RUN for version in $BLENDER_VERSIONS; do \
    short=$(echo $version | cut -d. -f1,2); \
    wget -O blender-$version.tar.xz https://download.blender.org/release/Blender$short/blender-$version-linux-x64.tar.xz; \
    done

# ---- Stage 2: Runtime ----
FROM python:3.10-slim as final

ENV IS_CLOUD_HOST=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Required system libraries for headless Blender CPU rendering and rentaflop hosts
RUN apt-get update && apt-get install -y \
    unzip curl \
    libgl1 libx11-6 libxi6 libxrender1 libxxf86vm1 \
    libxrandr2 libxcursor1 libxinerama1 libglu1-mesa \
    libegl1 libdbus-1-3 \
    firejail \
    && apt-get clean

# Copy Blender versions from build stage
COPY --from=blender-downloader /downloads/*.tar.xz /app/

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . /app

# ensure we didn't accidentally copy test tasks folder
RUN rm -rf tasks

# Default to running as ECS task
CMD ["python3", "cloud_native_host.py"]
