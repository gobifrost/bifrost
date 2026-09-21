"""SQL visibility predicates for agent findings.

Review-derived findings are only visible while every frozen contributor run
remains readable to the caller. This module returns SQLAlchemy conditions so
routes can filter before pagination and before returning or mutating rows.
"""
from __future__ import annotations

from typing import cast

from sqlalchemy import Text, and_, bindparam, case, exists, func, literal, not_, select, text, true
from sqlalchemy import cast as sa_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import ColumnElement

from src.core.principal import UserPrincipal
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_reviews import AgentReviewRun
from src.models.orm.agent_runs import AgentRun
from src.services.execution.agent_run_access import agent_run_visibility_conditions

_UUID_RE = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


_REF_SHAPE_SQL = r"""
jsonb_typeof({alias}.value) = 'object'
AND {alias}.value ? 'run_id'
AND jsonb_typeof({alias}.value->'run_id') = 'string'
AND ({alias}.value->>'run_id') ~ :uuid_re
AND {alias}.value ? 'agent_id'
AND jsonb_typeof({alias}.value->'agent_id') IN ('string', 'null')
AND (
  jsonb_typeof({alias}.value->'agent_id') = 'null'
  OR ({alias}.value->>'agent_id') ~ :uuid_re
)
AND {alias}.value ? 'org_id'
AND jsonb_typeof({alias}.value->'org_id') IN ('string', 'null')
AND (
  jsonb_typeof({alias}.value->'org_id') = 'null'
  OR ({alias}.value->>'org_id') ~ :uuid_re
)
AND {alias}.value ? 'root_run_id'
AND jsonb_typeof({alias}.value->'root_run_id') IN ('string', 'null')
AND (
  jsonb_typeof({alias}.value->'root_run_id') = 'null'
  OR ({alias}.value->>'root_run_id') ~ :uuid_re
)
AND {alias}.value ? 'parent_run_id'
AND jsonb_typeof({alias}.value->'parent_run_id') IN ('string', 'null')
AND (
  jsonb_typeof({alias}.value->'parent_run_id') = 'null'
  OR ({alias}.value->>'parent_run_id') ~ :uuid_re
)
AND {alias}.value ? 'trigger_type'
AND jsonb_typeof({alias}.value->'trigger_type') = 'string'
"""

_REVIEW_FINDING_VISIBILITY_SQL = rf"""
(
  (:is_superuser OR (:has_user_org AND agent_findings.org_id = :user_org_id))
  AND EXISTS (
    SELECT 1
    FROM agents visible_agent
    WHERE visible_agent.id = agent_findings.agent_id
      AND (
        :is_superuser
        OR (
          :has_user_org
          AND (visible_agent.organization_id = :user_org_id OR visible_agent.organization_id IS NULL)
          AND (
            visible_agent.access_level = 'everyone'
            OR (visible_agent.access_level = 'authenticated' AND :is_external IS NOT TRUE)
            OR (
              visible_agent.access_level = 'private'
              AND visible_agent.owner_user_id = :user_id_uuid
            )
            OR (
              visible_agent.access_level = 'role_based'
              AND EXISTS (
                SELECT 1
                FROM agent_roles arl
                JOIN user_roles url ON url.role_id = arl.role_id
                WHERE arl.agent_id = visible_agent.id
                  AND url.user_id = :user_id_uuid
              )
            )
          )
        )
      )
  )
  AND (
    (
      agent_findings.source_review_id IS NULL
      AND agent_findings.source_review_version_id IS NULL
      AND agent_findings.source_review_run_id IS NULL
      AND agent_findings.source_review_version IS NULL
      AND agent_findings.source_ordinal IS NULL
    )
    OR (
      agent_findings.source_review_id IS NOT NULL
      AND agent_findings.source_review_version_id IS NOT NULL
      AND agent_findings.source_review_run_id IS NOT NULL
      AND agent_findings.source_review_version IS NOT NULL
      AND agent_findings.source_ordinal IS NOT NULL
      AND jsonb_typeof(agent_findings.source_run_refs) = 'array'
      AND jsonb_array_length(
        CASE
          WHEN jsonb_typeof(agent_findings.source_run_refs) = 'array'
          THEN agent_findings.source_run_refs
          ELSE '[]'::jsonb
        END
      ) > 0
      AND EXISTS (
        SELECT 1
        FROM agent_review_runs arr
        JOIN agent_review_definitions ard ON ard.id = arr.review_id
        JOIN agent_review_versions arv ON arv.id = arr.review_version_id
        WHERE arr.id = agent_findings.source_review_run_id
          AND ard.id = agent_findings.source_review_id
          AND arv.id = agent_findings.source_review_version_id
          AND arv.review_id = ard.id
          AND arv.version = agent_findings.source_review_version
          AND arr.review_id = ard.id
          AND arr.review_version_id = arv.id
          AND arr.review_version = agent_findings.source_review_version
          AND arr.agent_id = agent_findings.agent_id
          AND ard.agent_id = agent_findings.agent_id
          AND arr.org_id IS NOT DISTINCT FROM agent_findings.org_id
          AND ard.org_id IS NOT DISTINCT FROM agent_findings.org_id
          AND jsonb_typeof(arr.source_refs) = 'array'
          AND jsonb_array_length(
            CASE
              WHEN jsonb_typeof(arr.source_refs) = 'array'
              THEN arr.source_refs
              ELSE '[]'::jsonb
            END
          ) > 0
          AND NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
              CASE
                WHEN jsonb_typeof(arr.source_refs) = 'array'
                THEN arr.source_refs
                ELSE '[]'::jsonb
              END
            ) AS cref(value)
            WHERE (
              {_REF_SHAPE_SQL.format(alias='cref')}
              AND (
                (jsonb_typeof(cref.value->'org_id') = 'null' AND arr.org_id IS NULL)
                OR (jsonb_typeof(cref.value->'org_id') = 'string' AND cref.value->>'org_id' = arr.org_id::text)
              )
              AND EXISTS (
                SELECT 1
                FROM agent_runs ar
                WHERE ar.id::text = cref.value->>'run_id'
                  AND (
                    (jsonb_typeof(cref.value->'agent_id') = 'null' AND ar.agent_id IS NULL)
                    OR (jsonb_typeof(cref.value->'agent_id') = 'string' AND cref.value->>'agent_id' = ar.agent_id::text)
                  )
                  AND (
                    (jsonb_typeof(cref.value->'org_id') = 'null' AND ar.org_id IS NULL)
                    OR (jsonb_typeof(cref.value->'org_id') = 'string' AND cref.value->>'org_id' = ar.org_id::text)
                  )
                  AND (
                    (jsonb_typeof(cref.value->'root_run_id') = 'null' AND ar.root_run_id IS NULL)
                    OR (jsonb_typeof(cref.value->'root_run_id') = 'string' AND cref.value->>'root_run_id' = ar.root_run_id::text)
                  )
                  AND (
                    (jsonb_typeof(cref.value->'parent_run_id') = 'null' AND ar.parent_run_id IS NULL)
                    OR (jsonb_typeof(cref.value->'parent_run_id') = 'string' AND cref.value->>'parent_run_id' = ar.parent_run_id::text)
                  )
                  AND ar.trigger_type = cref.value->>'trigger_type'
              )
            ) IS NOT TRUE
          )
          AND NOT EXISTS (
            SELECT 1
            FROM unnest(arr.selected_run_ids) AS selected_run_id(value)
            WHERE NOT EXISTS (
              SELECT 1
              FROM jsonb_array_elements(
                CASE
                  WHEN jsonb_typeof(arr.source_refs) = 'array'
                  THEN arr.source_refs
                  ELSE '[]'::jsonb
                END
              ) AS sref(value)
              WHERE (
                {_REF_SHAPE_SQL.format(alias='sref')}
                AND sref.value->>'run_id' = selected_run_id.value::text
                AND jsonb_typeof(sref.value->'agent_id') = 'string'
                AND sref.value->>'agent_id' = arr.agent_id::text
                AND sref.value->>'trigger_type' <> 'evaluation_synthetic'
              ) IS TRUE
            )
          )
          AND NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
              CASE
                WHEN jsonb_typeof(agent_findings.source_run_refs) = 'array'
                THEN agent_findings.source_run_refs
                ELSE '[]'::jsonb
              END
            ) AS fref(value)
            WHERE (
              {_REF_SHAPE_SQL.format(alias='fref')}
              AND EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                  CASE
                    WHEN jsonb_typeof(arr.source_refs) = 'array'
                    THEN arr.source_refs
                    ELSE '[]'::jsonb
                  END
                ) AS mref(value)
                WHERE (
                  {_REF_SHAPE_SQL.format(alias='mref')}
                  AND mref.value->>'run_id' IS NOT DISTINCT FROM fref.value->>'run_id'
                  AND mref.value->>'agent_id' IS NOT DISTINCT FROM fref.value->>'agent_id'
                  AND mref.value->>'org_id' IS NOT DISTINCT FROM fref.value->>'org_id'
                  AND mref.value->>'root_run_id' IS NOT DISTINCT FROM fref.value->>'root_run_id'
                  AND mref.value->>'parent_run_id' IS NOT DISTINCT FROM fref.value->>'parent_run_id'
                  AND mref.value->>'trigger_type' IS NOT DISTINCT FROM fref.value->>'trigger_type'
                ) IS TRUE
              )
            ) IS NOT TRUE
          )
      )
    )
  )
)
"""


def visible_agent_finding_condition(user: UserPrincipal) -> ColumnElement[bool]:
    """Return the caller-visible predicate for ``AgentFinding`` rows."""
    identity_condition = cast(
        ColumnElement[bool],
        text(_REVIEW_FINDING_VISIBILITY_SQL).bindparams(
            bindparam("is_superuser", bool(user.is_superuser)),
            bindparam("has_user_org", user.organization_id is not None),
            bindparam("user_org_id", user.organization_id),
            bindparam("user_id_uuid", user.user_id),
            bindparam("is_external", bool(user.is_external)),
            bindparam("uuid_re", _UUID_RE),
        ),
    )
    return and_(identity_condition, _review_contributors_readable_condition(user))


def _jsonb_array(value: ColumnElement) -> ColumnElement:
    return case(
        (func.jsonb_typeof(value) == "array", value),
        else_=sa_cast(literal("[]"), JSONB),
    )


def _review_contributors_readable_condition(user: UserPrincipal) -> ColumnElement[bool]:
    ref = (
        func.jsonb_array_elements(_jsonb_array(AgentReviewRun.source_refs))
        .table_valued("value")
        .alias("visible_source_ref")
    )
    visible_run_ids = select(sa_cast(AgentRun.id, Text)).where(
        *agent_run_visibility_conditions(user)
    )
    unreadable_ref = (
        select(1)
        .select_from(AgentReviewRun)
        .join(ref, true())
        .where(
            AgentReviewRun.id == AgentFinding.source_review_run_id,
            ref.c.value.op("->>")("run_id").not_in(visible_run_ids),
        )
    )
    return not_(exists(unreadable_ref))
