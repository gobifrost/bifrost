FROM node:20-bookworm-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

# Prime npm's content-addressed cache with the exact curated catalog and every
# transitive package selected by npm. The second install is an image-build
# smoke test proving that the resulting cache is sufficient with networking
# disabled.
COPY api/shared/builder_package_catalog.json /tmp/builder_package_catalog.json
RUN mkdir -p /opt/npm-cache /tmp/catalog-seed \
    && node -e 'const fs=require("fs"); const dependencies=JSON.parse(fs.readFileSync("/tmp/builder_package_catalog.json")); fs.writeFileSync("/tmp/catalog-seed/package.json", JSON.stringify({name:"bifrost-builder-cache",private:true,dependencies}));' \
    && cd /tmp/catalog-seed \
    && npm install --ignore-scripts --no-audit --no-fund --cache=/opt/npm-cache \
    && rm -rf node_modules package-lock.json \
    && npm install --offline --ignore-scripts --no-audit --no-fund --cache=/opt/npm-cache \
    && rm -rf /tmp/catalog-seed /tmp/builder_package_catalog.json \
    && chown -R 1000:1000 /opt/npm-cache

ENV HOME=/tmp \
    npm_config_audit=false \
    npm_config_cache=/opt/npm-cache \
    npm_config_fund=false \
    npm_config_ignore_scripts=true \
    npm_config_offline=true \
    npm_config_update_notifier=false

WORKDIR /srv
COPY --chown=1000:1000 builder_runner/server.py /srv/server.py

USER 1000:1000
EXPOSE 8300
CMD ["python3", "/srv/server.py"]
