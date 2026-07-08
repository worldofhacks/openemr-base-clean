# syntax=docker/dockerfile:1
# ============================================================================
# OpenEMR Dockerfile for Railway deployment of THIS FORK
# ============================================================================
# Derived from docker/release/Dockerfile (the repo's production build) with
# exactly two intentional deviations — keep this file in sync with it when
# upstream changes, and diff against it during upgrades:
#
#   1. SOURCE STAGE: the upstream file git-clones openemr/openemr from GitHub
#      (docker/release/Dockerfile:162), so the image would contain upstream's
#      code, never this fork's. Here the openemr-source stage COPYs the build
#      context instead, so the image provably contains the exact checkout
#      Railway builds from. All context-relative COPY paths are therefore
#      prefixed with docker/railway/ or docker/release/ (build context = repo
#      root, set via railway.json dockerfilePath).
#   2. APACHE LISTEN: upstream forces "Listen 0.0.0.0:80" (IPv4-only). Railway
#      routes edge and private-network traffic over an IPv6 fabric, so this
#      image keeps Alpine's default "Listen 80" (dual-stack v4+v6).
#   3. RAILWAY BUILDER COMPAT (each rejected by Railway's builder, verified by
#      failed builds): upstream's RUN --mount=type=cache mounts are removed —
#      Railway requires ids prefixed with a service-specific cacheKey
#      (id=s/<service-id>-...), which would hardcode one Railway service into
#      the file; the mounts only speed up rebuilds, so plain RUN is used
#      instead. The upstream VOLUME ["/etc/letsencrypt/", "/etc/ssl"]
#      instruction is also dropped — Railway volumes are configured on the
#      service, not in the image, and the entrypoint regenerates /etc/ssl
#      content when missing (TLS terminates at the Railway edge regardless).
#
# The kcov coverage target from the upstream file is omitted (not needed for
# deployment). Everything else — packages, permissions hardening, entrypoint
# scripts, upgrade system — is copied verbatim from docker/release/.
#
# Build locally (from repo root):
#   docker build -f docker/railway/Dockerfile -t openemr-railway .
# ============================================================================

# ============================================================================
# BASE IMAGE CONFIGURATION
# ============================================================================
ARG ALPINE_VERSION=3.23
FROM alpine:${ALPINE_VERSION} AS base

# PHP version configuration
# Important: When updating PHP version, also update php.ini file to match
ARG PHP_VERSION=8.5
ENV PHP_VERSION=${PHP_VERSION}
# Create abbreviated version (e.g., "8.5" -> "85") for package naming
ARG PHP_VERSION_ABBR=${PHP_VERSION//./}
ENV PHP_VERSION_ABBR=${PHP_VERSION_ABBR}

# ============================================================================
# SYSTEM PACKAGE INSTALLATION
# ============================================================================
# Update Alpine packages to latest versions for security patches
RUN apk --no-cache upgrade

# Install system packages required for OpenEMR and Apache
# (identical list to docker/release/Dockerfile)
RUN apk add --no-cache \
    apache2 \
    apache2-proxy \
    apache2-ssl \
    apache2-utils \
    bash \
    certbot \
    curl \
    dcron \
    git \
    imagemagick \
    mariadb-client \
    mariadb-connector-c \
    ncurses \
    nodejs \
    npm \
    openssl \
    openssl-dev \
    perl \
    rsync \
    shadow \
    su-exec \
    tar

# Install PHP and all required extensions for OpenEMR
# (identical list to docker/release/Dockerfile)
RUN apk add --no-cache \
    php${PHP_VERSION_ABBR} \
    php${PHP_VERSION_ABBR}-apache2 \
    php${PHP_VERSION_ABBR}-bcmath \
    php${PHP_VERSION_ABBR}-calendar \
    php${PHP_VERSION_ABBR}-ctype \
    php${PHP_VERSION_ABBR}-curl \
    php${PHP_VERSION_ABBR}-dom \
    php${PHP_VERSION_ABBR}-fileinfo \
    php${PHP_VERSION_ABBR}-fpm \
    php${PHP_VERSION_ABBR}-gd \
    php${PHP_VERSION_ABBR}-iconv \
    php${PHP_VERSION_ABBR}-intl \
    php${PHP_VERSION_ABBR}-ldap \
    php${PHP_VERSION_ABBR}-mbstring \
    php${PHP_VERSION_ABBR}-mysqli \
    php${PHP_VERSION_ABBR}-openssl \
    php${PHP_VERSION_ABBR}-pdo \
    php${PHP_VERSION_ABBR}-pdo_mysql \
    php${PHP_VERSION_ABBR}-pecl-apcu \
    php${PHP_VERSION_ABBR}-pecl-imagick \
    php${PHP_VERSION_ABBR}-phar \
    php${PHP_VERSION_ABBR}-pecl-redis \
    php${PHP_VERSION_ABBR}-session \
    php${PHP_VERSION_ABBR}-simplexml \
    php${PHP_VERSION_ABBR}-soap \
    php${PHP_VERSION_ABBR}-sockets \
    php${PHP_VERSION_ABBR}-sodium \
    php${PHP_VERSION_ABBR}-tokenizer \
    php${PHP_VERSION_ABBR}-xml \
    php${PHP_VERSION_ABBR}-xmlreader \
    php${PHP_VERSION_ABBR}-xmlwriter \
    php${PHP_VERSION_ABBR}-xsl \
    php${PHP_VERSION_ABBR}-zip \
    php${PHP_VERSION_ABBR}-zlib

# ============================================================================
# APACHE CONFIGURATION
# ============================================================================
# DEVIATION from docker/release/Dockerfile: keep Alpine's default "Listen 80"
# (dual-stack IPv4+IPv6) instead of forcing "Listen 0.0.0.0:80". Railway's
# edge proxy and private networking run over IPv6; an IPv4-only listener can
# be unreachable from the Railway fabric.

# ============================================================================
# USER AND PERMISSIONS CONFIGURATION
# ============================================================================
# Set Apache user UID to 1000 for consistent permissions across shared volumes
RUN usermod -u 1000 apache

# ============================================================================
# PHP CONFIGURATION
# ============================================================================
# Create symlink for PHP binary to support PHP 8+ on Alpine 3.13+
RUN ln -sf /usr/bin/php${PHP_VERSION_ABBR} /usr/bin/php

# Install Composer (PHP dependency manager) for OpenEMR package installation
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/bin --filename=composer

# ============================================================================
# MULTI-STAGE BUILD: OpenEMR Source and Dependencies
# ============================================================================
# Stage 1: THIS FORK's source code, from the build context (repo root).
# DEVIATION from docker/release/Dockerfile, which git-clones upstream
# openemr/openemr here. COPYing the context means the image contains the
# exact commit Railway checked out — provable provenance for the fork.
FROM base AS openemr-source
COPY . /openemr
# Match upstream's post-clone cleanup (it runs `rm -rf openemr/.git`), and
# drop local-only scratch dirs that a clone would never contain.
RUN rm -rf /openemr/.git /openemr/tmp

# Stage 2: Install PHP dependencies (Composer)
FROM base AS openemr-composer
COPY --from=openemr-source /openemr /openemr
WORKDIR /openemr
RUN composer install --no-dev --optimize-autoloader \
    && composer dump-autoload --optimize --apcu

# Stage 3: Build frontend assets (npm)
FROM base AS openemr-assets
COPY --from=openemr-source /openemr /openemr
WORKDIR /openemr
RUN apk add --no-cache build-base nodejs npm
RUN npm install --unsafe-perm \
    && npm run build \
    && cd ccdaservice \
    && npm install --unsafe-perm \
    && cd .. \
    && rm -fr node_modules \
    && apk del --no-cache build-base

# Stage 4: Final assembly - combine everything in base image
FROM base AS production
# Copy OpenEMR source
COPY --from=openemr-source /openemr /tmp/openemr
# Copy Composer dependencies
COPY --from=openemr-composer /openemr/vendor /tmp/openemr/vendor
COPY --from=openemr-composer /openemr/composer.json /tmp/openemr/composer.json
COPY --from=openemr-composer /openemr/composer.lock /tmp/openemr/composer.lock
# Copy built assets
COPY --from=openemr-assets /openemr/public /tmp/openemr/public
COPY --from=openemr-assets /openemr/ccdaservice /tmp/openemr/ccdaservice

RUN cd /tmp \
    # Pre-set file permissions during build (major startup optimization):
    # directories 500, files 400, with setup-time exceptions below
    && find openemr -type d -exec chmod 500 {} + \
    && find openemr -type f -exec chmod 400 {} + \
    # sqlconf.php: written during auto-configuration
    && chmod 666 openemr/sites/default/sqlconf.php \
    # sites/default directory: needs write access for setup
    && chmod 700 openemr/sites/default \
    # documents directory: needs write access for uploads
    && find openemr/sites/default/documents -type d -exec chmod 700 {} + \
    && find openemr/sites/default/documents -type f -exec chmod 600 {} + \
    # Set ownership to apache user for proper file access
    && chown -R apache:apache openemr/ \
    # Move OpenEMR to web root directory
    && mv openemr /var/www/localhost/htdocs/ \
    # Create SSL certificate directories
    && mkdir -p /etc/ssl/certs /etc/ssl/private \
    # Disable Apache logging to reduce disk usage (logs handled by Docker)
    && sed -i 's/^ *CustomLog/#CustomLog/' /etc/apache2/httpd.conf \
    && sed -i 's/^ *ErrorLog/#ErrorLog/' /etc/apache2/httpd.conf \
    && sed -i 's/^ *CustomLog/#CustomLog/' /etc/apache2/conf.d/ssl.conf \
    && sed -i 's/^ *TransferLog/#TransferLog/' /etc/apache2/conf.d/ssl.conf

# ============================================================================
# WORKING DIRECTORY AND VOLUMES
# ============================================================================
WORKDIR /var/www/localhost/htdocs/openemr

# NOTE: upstream declares VOLUME ["/etc/letsencrypt/", "/etc/ssl"] here.
# Railway's builder rejects the VOLUME instruction (volumes are service-level
# config), so it is intentionally omitted — see header deviation 3.

# ============================================================================
# APACHE AND PHP CONFIGURATION FILES
# ============================================================================
ENV APACHE_LOG_DIR=/var/log/apache2

# PHP configuration with OpenEMR-optimized settings (from docker/release/)
COPY docker/release/php.ini /etc/php${PHP_VERSION_ABBR}/php.ini

# Apache virtual host configuration for OpenEMR (from docker/release/)
COPY docker/release/openemr.conf /etc/apache2/conf.d/

# ============================================================================
# OPENEMR SCRIPTS AND UTILITIES (all from docker/release/, unmodified)
# ============================================================================
# - openemr.sh: Main container startup script (handles setup, upgrades, Apache)
# - ssl.sh: SSL/TLS certificate management script
# - xdebug.sh: XDebug configuration script (for development)
# - auto_configure.php: Automated OpenEMR installation script
COPY docker/release/openemr.sh docker/release/ssl.sh docker/release/xdebug.sh docker/release/auto_configure.php /var/www/localhost/htdocs/openemr/

# Admin unlock utilities (for password recovery)
COPY docker/release/utilities/unlock_admin.php docker/release/utilities/unlock_admin.sh /root/

RUN chmod 500 openemr.sh ssl.sh xdebug.sh /root/unlock_admin.sh \
    && chmod 000 auto_configure.php /root/unlock_admin.php

# ============================================================================
# UPGRADE SYSTEM
# ============================================================================
COPY docker/release/upgrade/docker-version \
     docker/release/upgrade/fsupgrade-1.sh \
     docker/release/upgrade/fsupgrade-2.sh \
     docker/release/upgrade/fsupgrade-3.sh \
     docker/release/upgrade/fsupgrade-4.sh \
     docker/release/upgrade/fsupgrade-5.sh \
     docker/release/upgrade/fsupgrade-6.sh \
     docker/release/upgrade/fsupgrade-7.sh \
     docker/release/upgrade/fsupgrade-8.sh \
     docker/release/upgrade/fsupgrade-9.sh \
     docker/release/upgrade/fsupgrade-10.sh \
     docker/release/upgrade/fsupgrade-11.sh \
     /root/

RUN chmod 500 \
    /root/fsupgrade-1.sh \
    /root/fsupgrade-2.sh \
    /root/fsupgrade-3.sh \
    /root/fsupgrade-4.sh \
    /root/fsupgrade-5.sh \
    /root/fsupgrade-6.sh \
    /root/fsupgrade-7.sh \
    /root/fsupgrade-8.sh \
    /root/fsupgrade-9.sh \
    /root/fsupgrade-10.sh \
    /root/fsupgrade-11.sh

# ============================================================================
# APACHE RUNTIME DIRECTORY
# ============================================================================
RUN mkdir -p /run/apache2

# ============================================================================
# DEVELOPMENT TOOLS LIBRARY
# ============================================================================
# Shared library of utility functions used by openemr.sh (database operations,
# configuration helpers, OPENEMR_SETTING_* processing, etc.)
COPY docker/release/utilities/devtoolsLibrary.source /root/

# ============================================================================
# SWARM MODE SUPPORT
# ============================================================================
# Templates restored by openemr.sh when containers start with EMPTY volumes
# (SWARM_MODE=yes). Railway volumes mount empty — this is what makes the
# sites/ volume work on first boot.
RUN mkdir /swarm-pieces \
    && rsync --owner --group --perms --delete --recursive --links /etc/ssl /swarm-pieces/ \
    && rsync --owner --group --perms --delete --recursive --links /var/www/localhost/htdocs/openemr/sites /swarm-pieces/

# ============================================================================
# CONTAINER STARTUP
# ============================================================================
CMD [ "./openemr.sh" ]

EXPOSE 80 443

# ============================================================================
# FINAL BUILD TARGET (ALIAS)
# ============================================================================
FROM production AS final

ARG IMAGE_VERSION=8.2.0-dev
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="OpenEMR (openemr-base-clean fork)"
LABEL org.opencontainers.image.source="https://github.com/worldofhacks/openemr-base-clean"
LABEL org.opencontainers.image.url="https://www.open-emr.org"
LABEL org.opencontainers.image.licenses="GPL-3.0-or-later"
LABEL org.opencontainers.image.version="${IMAGE_VERSION}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
