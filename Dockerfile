#
# Attempts are made to follow the guidelines at
# https://docs.docker.com/engine/userguide/eng-image/dockerfile_best-practices/
#

FROM library/ubuntu:16.04

# If there are security updates for any of the packages we install,
# bump the date in this environment variable to invalidate the Docker
# build cache and force installation of the new packages.  Otherwise,
# Docker's image/layer cache may prevent the security update from
# being retrieved.
ENV SECURITY_UPDATES="2017-15-01"

# Tell apt/dpkg/debconf that we're non-interactive so it won't write
# annoying warnings as it installs the software we ask for.  Making
# this an `ARG` sets it in the environment for the duration of the
# _build_ only - preventing this from having any effect on a container
# running this image (which shouldn't really be installing more
# software but who knows...).
ARG DEBIAN_FRONTEND=noninteractive

# We'll do an upgrade because the base Ubuntu image isn't guaranteed
# to include the latest security updates.  This is counter to best
# practice recommendations but security updates are important.
RUN apt-get --quiet update && \
    apt-get --quiet install -y unattended-upgrades && \
    unattended-upgrade --minimal_upgrade_steps && \
rm -rf /var/lib/apt/lists/*

RUN apt-get --quiet update && apt-get --quiet install -y \
    python-dev \
    libffi-dev \
    openssl \
    libssl-dev \
    \
    python-virtualenv \
&& rm -rf /var/lib/apt/lists/*

# Create a virtualenv into which to install magicwormhole in to.
RUN virtualenv /app/env

# Get a newer version of pip.
RUN /app/env/bin/pip install --upgrade pip

# Create the website account, the user as which the infrastructure
# server will run.
ENV WORMHOLE_USER_NAME="wormhole"

# Force the allocated user to uid 1000 because we hard-code 1000
# below.
RUN adduser --uid 1000 --disabled-password --gecos "" "${WORMHOLE_USER_NAME}"

# Run the application with this working directory.
WORKDIR /app/run

# And give it to the user the application will run as.
RUN chown ${WORMHOLE_USER_NAME} /app/run

# Facilitate network connections to the application.
EXPOSE 4000

# Put the source somewhere pip will be able to see it.
ADD . /src

# Get the app we want to run!
RUN /app/env/bin/pip install /src

# Switch to a non-root user.
USER 1000

CMD /app/env/bin/wormhole-server start \
    --rendezvous tcp:4000 \
    --no-daemon
