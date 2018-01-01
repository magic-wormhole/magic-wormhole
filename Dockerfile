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

# libffi-dev should probably be a build-dep for python-nacl and python-openssl
# but isn't for some reason.  Also, versioneer depends on the git cli to
# compute the source version.
RUN apt-get --quiet update && apt-get --quiet install -y \
    libffi-dev \
    python-virtualenv \
    git \
&& rm -rf /var/lib/apt/lists/*

# Source repositories seem to be disabled on the Xenial image now.  Enable
# them so we can actually get some build deps.
RUN sed -i -e 's/^# deb-src/deb-src/' /etc/apt/sources.list

# magic-wormhole depends on these and pip wants to build them both from
# source.
RUN apt-get --quiet update && apt-get --quiet build-dep -y \
    python-openssl \
    python-nacl \
&& rm -rf /var/lib/apt/lists/*

# Create a virtualenv into which to install magicwormhole in to.
RUN virtualenv /app/env

# Get a newer version of pip.  The version in the virtualenv installed from
# Ubuntu might not be very recent, depending on when the build happens.
RUN /app/env/bin/pip install --upgrade pip

# Create a less privileged account to actually use to run the server.
ENV WORMHOLE_USER_NAME="wormhole"

# Force the allocated user to uid 1000 because we hard-code 1000 below.
RUN adduser --uid 1000 --disabled-password --gecos "" "${WORMHOLE_USER_NAME}"

# Facilitate network connections to the application.  The rendezvous server
# listens on 4000 by default.
EXPOSE 4000

# Put the source somewhere pip will be able to see it.
ADD . /magic-wormhole

# Get the app we want to run!
WORKDIR /magic-wormhole
RUN /app/env/bin/pip install .

# Run the application with this working directory.
WORKDIR /app/run

# And give it to the user the application will run as.
RUN chown ${WORMHOLE_USER_NAME} /app/run

# Switch to a non-root user.
USER 1000

# This makes starting a server succinct.
ENTRYPOINT ["/app/env/bin/wormhole-server", "start", "--no-daemon"]

# By default, start up a pretty reasonable server.  This can easily be
# overridden by another command which will get added to the entrypoint.
CMD ["--rendezvous", "tcp:4000"]
