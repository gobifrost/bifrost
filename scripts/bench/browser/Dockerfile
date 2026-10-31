# The caller supplies an immutable Playwright base digest.  Build this image on
# the guarded host, with its npm cache already populated, before measurement.
ARG PLAYWRIGHT_BASE_IMAGE
FROM ${PLAYWRIGHT_BASE_IMAGE}
WORKDIR /bench
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --fund=false
COPY driver.mjs ./driver.mjs
ENTRYPOINT ["node", "/bench/driver.mjs"]
