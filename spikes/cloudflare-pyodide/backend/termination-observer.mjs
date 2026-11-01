// Correlate synthetic observations across cases without dropping late effects.
export function createTerminationObserver(now=()=>performance.now()) {
  const runs=new Map();
  return {
    register(id,mode) {
      if(runs.has(id) || runs.size>=3) throw new TypeError('Duplicate or excess fixed case');
      const run={id,mode,events:[],started:now()};
      runs.set(id,run);
      return run;
    },
    record(id,event) {
      const run=runs.get(id);
      if(!run || !['before','caught','after','abort_requested'].includes(event) || run.events.length>=16) return false;
      run.events.push({event,elapsed_ms:now()-run.started});
      return true;
    },
    get(id) {return runs.get(id);},
  };
}
