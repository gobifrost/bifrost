-- Schema left behind on databases that ran the accidentally shipped Solution
-- Builder migrations (PR #557, since tombstoned by PR #560). Transcribed from
-- the original revisions at 3b29c703c, in their original order, ending in the
-- state the last of them (20260730_role_auth_scopes) left. Applied on top of
-- 20260928_audit_op_surface by the residue rehearsal test.

-- 20260725_private_visibility
ALTER TABLE solutions ADD COLUMN owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE solutions ADD COLUMN visibility VARCHAR(16) NOT NULL DEFAULT 'shared';
DROP INDEX ix_solutions_slug_org_unique;
DROP INDEX ix_solutions_slug_global_unique;
CREATE UNIQUE INDEX ix_solutions_slug_org_unique ON solutions (slug, organization_id)
    WHERE organization_id IS NOT NULL AND visibility = 'shared';
CREATE UNIQUE INDEX ix_solutions_slug_global_unique ON solutions (slug)
    WHERE organization_id IS NULL AND visibility = 'shared';
CREATE UNIQUE INDEX ix_solutions_owner_slug_private_unique ON solutions (owner_user_id, slug)
    WHERE visibility = 'private';
CREATE INDEX ix_solutions_owner_user_id ON solutions (owner_user_id);

-- 20260725_builder_tables
CREATE TABLE solution_source_revisions (
    id UUID NOT NULL PRIMARY KEY,
    solution_id UUID NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    parent_revision_id UUID REFERENCES solution_source_revisions(id) ON DELETE SET NULL,
    restored_from_revision_id UUID REFERENCES solution_source_revisions(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    source_sha256 VARCHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL,
    summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_solution_source_revisions_solution_id ON solution_source_revisions (solution_id);
CREATE INDEX ix_solution_source_revisions_solution_created
    ON solution_source_revisions (solution_id, created_at);

CREATE TABLE solution_builder_projects (
    solution_id UUID NOT NULL PRIMARY KEY REFERENCES solutions(id) ON DELETE CASCADE,
    current_revision_id UUID,
    deployed_revision_id UUID,
    promotion_status VARCHAR(16) NOT NULL DEFAULT 'none',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_solution_builder_projects_current_revision_id
        FOREIGN KEY (current_revision_id) REFERENCES solution_source_revisions(id) ON DELETE SET NULL,
    CONSTRAINT fk_solution_builder_projects_deployed_revision_id
        FOREIGN KEY (deployed_revision_id) REFERENCES solution_source_revisions(id) ON DELETE SET NULL
);

CREATE TABLE solution_builder_sessions (
    id UUID NOT NULL PRIMARY KEY,
    solution_id UUID NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_solution_builder_sessions_solution_id ON solution_builder_sessions (solution_id);
CREATE INDEX ix_solution_builder_sessions_conversation_id ON solution_builder_sessions (conversation_id);
CREATE INDEX ix_solution_builder_sessions_user_id ON solution_builder_sessions (user_id);

CREATE TABLE solution_builder_turns (
    id UUID NOT NULL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES solution_builder_sessions(id) ON DELETE CASCADE,
    requested_by UUID REFERENCES users(id) ON DELETE SET NULL,
    base_revision_id UUID REFERENCES solution_source_revisions(id) ON DELETE SET NULL,
    output_revision_id UUID REFERENCES solution_source_revisions(id) ON DELETE SET NULL,
    build_job_id UUID,
    deploy_job_id UUID,
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
CREATE INDEX ix_solution_builder_turns_session_id ON solution_builder_turns (session_id);
CREATE INDEX ix_solution_builder_turns_status ON solution_builder_turns (status);

-- 20260725_config_solution_id
ALTER TABLE configs ADD COLUMN solution_id UUID REFERENCES solutions(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX ix_configs_solution_key_unique ON configs (solution_id, key)
    WHERE solution_id IS NOT NULL;

-- 20260725_build_jobs, then 20260727_build_plane_jobs (drops the app_id FK,
-- adds claimed_at / last_progress_at)
CREATE TABLE solution_build_jobs (
    id UUID NOT NULL PRIMARY KEY,
    solution_id UUID NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    app_id UUID,
    source_revision_id UUID REFERENCES solution_source_revisions(id) ON DELETE SET NULL,
    requested_by UUID REFERENCES users(id) ON DELETE SET NULL,
    source_sha256 VARCHAR(64) NOT NULL,
    toolchain_version VARCHAR(64) NOT NULL,
    dependency_digest VARCHAR(64),
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    error TEXT,
    log_excerpt TEXT,
    output_manifest JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    claimed_at TIMESTAMPTZ,
    last_progress_at TIMESTAMPTZ
);
CREATE INDEX ix_solution_build_jobs_solution_id ON solution_build_jobs (solution_id);
CREATE INDEX ix_solution_build_jobs_solution_created ON solution_build_jobs (solution_id, created_at);
CREATE INDEX ix_solution_build_jobs_reuse_key
    ON solution_build_jobs (source_sha256, app_id, toolchain_version);

-- 20260727_agent_bundle_path
ALTER TABLE agents ADD COLUMN bundle_path VARCHAR(1024);

-- 20260727_durable_deploy_jobs
ALTER TABLE solution_deploy_jobs ADD COLUMN kind VARCHAR(32) NOT NULL DEFAULT 'deploy';
ALTER TABLE solution_deploy_jobs ADD COLUMN encrypted_options TEXT;
ALTER TABLE solution_deploy_jobs ADD COLUMN input_key TEXT;
ALTER TABLE solution_deploy_jobs ADD COLUMN input_sha256 VARCHAR(64);
ALTER TABLE solution_deploy_jobs ADD CONSTRAINT ck_solution_deploy_jobs_kind
    CHECK (kind IN ('deploy', 'install', 'install_from_repo'));

-- 20260727_promotion_pinning
ALTER TABLE solution_builder_projects ADD COLUMN promotion_revision_id UUID;
ALTER TABLE solution_builder_projects ADD COLUMN promotion_requested_by UUID;
ALTER TABLE solution_builder_projects ADD COLUMN promotion_requested_at TIMESTAMPTZ;
ALTER TABLE solution_builder_projects
    ADD CONSTRAINT fk_solution_builder_projects_promotion_revision_id
    FOREIGN KEY (promotion_revision_id) REFERENCES solution_source_revisions(id) ON DELETE SET NULL;
ALTER TABLE solution_builder_projects
    ADD CONSTRAINT fk_solution_builder_projects_promotion_requested_by
    FOREIGN KEY (promotion_requested_by) REFERENCES users(id) ON DELETE SET NULL;

-- 20260730_role_auth_scopes (the deterministic seed rows were already removed
-- by 20260807_withdraw_builder, so only the schema remains)
ALTER TABLE roles ADD COLUMN key VARCHAR(100);
ALTER TABLE roles ADD COLUMN scopes JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE roles ADD COLUMN is_builtin BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE roles ADD COLUMN assignable_to_resources BOOLEAN NOT NULL DEFAULT true;
CREATE UNIQUE INDEX uq_roles_key ON roles (key);
