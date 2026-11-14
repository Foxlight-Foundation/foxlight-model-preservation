import {useEffect,useRef,useState} from 'react';
import {bytes} from './client';

const minutes=(seconds:number)=>seconds<90?'about a minute left':seconds<3600?`about ${Math.round(seconds/60)} min left`:`about ${(seconds/3600).toFixed(1)} h left`;
const WINDOW=60_000;

/** "5.2 GB of 30 GB · 17% · about 20 min left" for a running save.
 *  `kept` is what is actually on disk (finished files plus the current file's
 *  buffer), so a restarted download never overshoots; transfer counters are
 *  only a fallback for older hosts. */
export function DownloadProgress({received,reused=0,kept,total}:{received:number;reused?:number;kept?:number|null;total?:number|null}) {
  const done=kept??received+reused;
  const latest=useRef(done);latest.current=done;
  const samples=useRef<{at:number;done:number}[]>([]);
  const [rate,setRate]=useState<number|null>(null);
  useEffect(()=>{
    // Sample on a timer too, so a stall slows the estimate and then clears it.
    function sample(){
      const now=Date.now();const value=latest.current;const list=samples.current;
      if(list.length&&value<list[list.length-1].done)list.length=0; // a restart
      list.push({at:now,done:value});
      while(list.length>2&&now-list[0].at>WINDOW)list.shift();
      const first=list[0];const seconds=(now-first.at)/1000;
      setRate(seconds>=5&&value>first.done?(value-first.done)/seconds:null);
    }
    sample();
    const timer=setInterval(sample,2000);
    return()=>clearInterval(timer);
  },[]);
  useEffect(()=>{
    const list=samples.current;
    if(list.length&&done<list[list.length-1].done){list.length=0;setRate(null);}
  },[done]);
  if(!done&&!total)return null;
  if(!total)return <p className="sk-meta">{bytes(done)} received</p>;
  const shown=Math.min(done,total);
  const percent=Math.floor(shown/total*100);
  const left=rate?minutes((total-shown)/rate):null;
  return <div className="sk-download-progress">
    <progress max={total} value={shown} aria-label="Download progress"/>
    <p className="sk-meta">{bytes(shown)} of {bytes(total)} · {percent}%{left&&` · ${left}`}{rate&&` · ${bytes(rate)}/s`}</p>
  </div>;
}
