// Fixed local companion profile only; not authentication of a public caller.
export function boundHttpsFixturePolicy(body) {
  const policy = body?.https_policy;
  if (!policy || typeof policy !== 'object' || Array.isArray(policy) ||
      Object.keys(policy).sort().join(',') !== 'execution_id,max_requests,org_id,origins,policy_id' ||
      !['synthetic-fails-first', 'synthetic-succeeds-second'].includes(policy.policy_id) ||
      policy.execution_id !== body.execution_id || policy.org_id !== body.context?.organization?.id ||
      typeof policy.org_id !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(policy.org_id) ||
      policy.max_requests !== 6 || !Array.isArray(policy.origins) ||
      policy.origins.length !== 1 || policy.origins[0] !== 'https://vendor.example') return null;
  return Object.freeze({...policy, origins: Object.freeze([...policy.origins])});
}
