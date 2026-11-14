// Local test synchronization only; stores no response body or credentials.
export function createRefreshGate() {
  let ready,release;
  const received=new Promise(resolve=>{ready=resolve;});
  const released=new Promise(resolve=>{release=resolve;});
  let releaseRequested=false;
  const events=[];
  return {events,
    async holdResponse(){events.push('server_response_held');ready(true);await released;events.push('response_forwarded');},
    async waitReady(){const value=await received;if(value)events.push('ready_observed');return value;},
    release(){if(!releaseRequested){releaseRequested=true;events.push('release_requested');release();}},
    close(){ready(false);this.release();},
  };
}
