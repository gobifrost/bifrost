import assert from 'node:assert/strict';
import test from 'node:test';
import {testApiRouteAllowed} from './test-api-route.mjs';
test('actual SDK and router refresh path is admitted',()=>{
  assert.equal(testApiRouteAllowed(new URL('https://test-api.invalid/auth/refresh')),true);
  assert.equal(testApiRouteAllowed(new URL('https://test-api.invalid/api/auth/refresh')),false);
});
test('refresh admission cannot select another origin or auth route',()=>{
  for(const value of ['https://other.invalid/auth/refresh','http://test-api.invalid/auth/refresh',
    'https://test-api.invalid/auth/login','https://test-api.invalid/auth/refresh/extra',
    'https://test-api.invalid/api/tables-elsewhere','https://test-api.invalid/api/sdk-elsewhere']) {
    assert.equal(testApiRouteAllowed(new URL(value)),false,value);
  }
});
test('existing SDK and table paths retain the fixed destination',()=>{
  for(const path of ['/api/sdk/config/get','/api/sdk/modules/a.py','/api/tables','/api/tables/t/documents']) {
    assert.equal(testApiRouteAllowed(new URL('https://test-api.invalid'+path)),true);
  }
});
