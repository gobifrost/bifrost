// Credential-free local Pyodide package tests; never deploy this entrypoint.
import serializationSource from './rpc-serialization-code.mjs';
import testsSource from './rpc-serialization-tests-code.mjs';
import allocationSource from './rpc-allocation-code.mjs';
const python = `
import json, sys, sysconfig, unittest
from workers import WorkerEntrypoint
import test_rpc_serialization
from rpc_allocation import measure_rpc_allocations
class Default(WorkerEntrypoint):
    async def qualify(self):
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromModule(test_rpc_serialization).run(result)
        return json.dumps({"tests_run": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors), "python": sys.version,
            "abi": sysconfig.get_config_var("SOABI"), "wire_limit": 4194304,
            "allocation": measure_rpc_allocations()})
`;
export default {
  async fetch(request, env) {
    if (env.ISOLATED_TEST_STACK !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status: 403});
    const child = env.LOADER.load({compatibilityDate: '2026-10-30', compatibilityFlags: ['python_workers'],
      mainModule: 'main.py', modules: {'main.py': {py: python},
        'rpc_serialization.py': {py: serializationSource}, 'test_rpc_serialization.py': {py: testsSource},
        'rpc_allocation.py': {py: allocationSource}},
      env: {}, globalOutbound: null, limits: {cpuMs: 1000, subRequests: 4}});
    return Response.json(JSON.parse(await child.getEntrypoint().qualify()));
  },
};
