import assert from 'node:assert/strict';
import test from 'node:test';
import {definition,pair} from './cold-concurrency-entry.mjs';
test('each participant receives a separate fresh restricted definition',async()=>{
  const definitions=[];
  let entered=0;
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  const loader={load(value){definitions.push(value);return {getEntrypoint(){return {
    async fetch(){entered++;if(entered===2)release();await gate;return Response.json({before:null,value:42});},
  };}};}};
  const result=await pair(loader);
  assert.equal(result.length,2);
  assert.notEqual(definitions[0],definitions[1]);
  assert.deepEqual(definitions[0],definition());
  assert.equal(definitions[0].globalOutbound,null);
  assert.deepEqual(definitions[0].env,{});
  assert.equal(definitions[0].limits.subRequests,0);
  assert.deepEqual(result.map(r=>r.body),[{before:null,value:42},{before:null,value:42}]);
});
test('a provider failure is evidence and does not erase the other participant',async()=>{
  let index=0;
  const result=await pair({load(){if(index++===0)throw new Error('native failure');return {
    getEntrypoint(){return {fetch:async()=>Response.json({before:null,value:42})};},
  };}});
  assert.equal(result[0].error,'native failure');
  assert.equal(result[1].status,200);
});
