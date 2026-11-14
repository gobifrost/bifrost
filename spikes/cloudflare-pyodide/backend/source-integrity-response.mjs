// An expected source denial is an HTTP result, not an uncaught RPC exception.
// Keep the existing integrity check and every unexpected failure unchanged.
export async function sourceIntegrityResponse(verify, observation) {
  try {
    return await verify();
  } catch (error) {
    if (!(error instanceof Error) || error.message !== 'Workspace module digest mismatch') throw error;
    observation.upstream_status = observation.status;
    observation.status = 409;
    observation.integrity_error = 'Workspace module digest mismatch';
    return Response.json({detail: 'Workspace module digest mismatch'}, {status: 409});
  }
}
