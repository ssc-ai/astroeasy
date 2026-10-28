ARG BASE_IMAGE=ubuntu:20.04
FROM ${BASE_IMAGE}
USER root

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get upgrade -y

RUN apt-get install -y --no-install-recommends \
       build-essential curl git file pkg-config swig \
       libcairo2-dev libnetpbm10-dev netpbm libpng-dev libjpeg-dev \
       zlib1g-dev libbz2-dev libcfitsio-dev wcslib-dev

RUN apt-get install -y --no-install-recommends python3 python3-pip \
       python3-dev python3-numpy python3-scipy python3-pil

RUN apt-get install -y --no-install-recommends ffmpeg libsm6 libxext6

# install Astrometry.net, pinned to a release tag so two builds give the same solver.
# Upgrade deliberately: bump the default (or `docker build --build-arg ASTROMETRY_NET_REF=...`),
# rebuild, run `make test-install-docker`, and tag the image with the version.
ARG ASTROMETRY_NET_REF=0.99
RUN git clone --branch ${ASTROMETRY_NET_REF} --depth 1 \
        https://github.com/dstndstn/astrometry.net.git /install-astrometry

WORKDIR /install-astrometry

RUN make
RUN make py
RUN make extra
RUN make install

# add astrometry to path
ENV PATH="${PATH}:/usr/local/astrometry/bin"

# setup flask interface to astrometry.net
ENV USER_NAME="starman"
ENV GROUP_NAME="astrometry"
ENV HOME=/home/${USER_NAME}
ENV SHELL=/bin/bash

RUN groupadd -g 5001 ${GROUP_NAME} && useradd -r -u 5001 -g 5001 ${USER_NAME}

# Create home directory
RUN mkdir -p ${HOME}

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        gosu \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*


# Change ownership recursively
RUN chown --recursive ${USER_NAME}:${GROUP_NAME} /home/${USER_NAME}/

# Give read, write, and execute (traverse) permissions to USER_NAME; read and
# execute (traverse) to everyone else
RUN chmod u+rwx,o+rx /home/${USER_NAME}

# Do the same recursively for all subdirectories
RUN find /home/${USER_NAME}/ -type d -exec chmod u+rwx,o+rx {} +

# Give USER_NAME read and write permissions for all files; read permissions for
# everyone else
RUN find /home/${USER_NAME}/ -type f -exec chmod u+rw,o+r {} +

RUN pip3 install astropy

USER ${USER_NAME}

WORKDIR ${HOME}/

USER root

CMD ["solve-field", "--help"]
