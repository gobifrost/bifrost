// Local fixed-fixture compatibility check, never authorization or a Python sandbox.
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const digest=/^[a-f0-9]{64}$/;
const record=value=>value!==null && typeof value==='object' && !Array.isArray(value);
export function fixtureRejection(body,active) {
  if(!Number.isInteger(active) || active<0 || active>=4) return 'capacity';
  if(!record(body) || !record(body.context) || !record(body.manifest)) return 'shape';
  if(body.fixture_mode!==undefined && body.fixture_mode!=='refresh_interruption') return 'fixture_mode';
  const c=body.context;
  const match=/^workflows\/pyodide_qualification_([a-f0-9]{32})\.py$/.exec(c.file_path ?? '');
  if(!match || c.function_name!=='qualified') return 'workspace';
  if(!uuid.test(body.execution_id ?? '') || c.execution_id!==body.execution_id) return 'execution_identity';
  if(body.mode!==undefined && body.mode!=='canonical') return 'mode';
  if(body.mode==='canonical' && !uuid.test(body.worker_id ?? '')) return 'worker_identity';
  if(typeof c.engine_token!=='string' || !c.engine_token || c.engine_token.length>16384) return 'credential_shape';
  if(c.engine_refresh_token!=null && (typeof c.engine_refresh_token!=='string' || !c.engine_refresh_token ||
    c.engine_refresh_token.length>16384)) return 'credential_shape';
  if(!Number.isInteger(c.timeout_seconds) || c.timeout_seconds<1 || c.timeout_seconds>30) return 'duration';
  if(c.code!=null || c.service!=null || c.is_platform_admin || c.solution_id!=null ||
    c.solution_global_repo_access || c.artifact_workspace_id!=null) return 'unsupported_context';
  const paths=Object.keys(body.manifest);
  const expected=[c.file_path,`pyodide_helper_${match[1]}.py`];
  if(paths.length!==expected.length || expected.some(path=>!Object.hasOwn(body.manifest,path) ||
    !digest.test(body.manifest[path]))) return 'manifest';
  if(c.content_hash!==body.manifest[c.file_path]) return 'provenance';
  return null;
}
export async function admitFixture(body,active,execute) {
  const reason=fixtureRejection(body,active);
  if(reason) return Response.json({error:'Fixture not admitted',reason},{status:422});
  return execute();
}
