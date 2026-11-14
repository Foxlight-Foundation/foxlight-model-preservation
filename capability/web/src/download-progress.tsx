import {useEffect,useRef,useState} from 'react';
import {bytes} from './client';

const minutes=(seconds:number)=>seconds<90?'about a minute left':seconds<3600?`about ${Math.round(seconds/60)} min left`:`about ${(seconds/3600).toFixed(1)} h left`;

/** "5.2 GB of 30 GB · 17% · about 20 min left" for a running save. */
export function DownloadProgress({received,reused=0,total}:{received:number;reused?:number;total?:number|null}) {
  const done=received+reused;
  const samples=useRef<{at:number;done:number}[]>([]);
  const [rate,setRate]=useState<number|null>(null);
  useEffect(()=>{
    const now=Date.now();const list=samples.current;
    if(list.length&&done<list[list.length-1].done)list.length=0; // a restart
    list.push({at:now,done});
    while(list.length>2&&now-list[0].at>60_000)list.shift(); // the last minute
    const first=list[0];const seconds=(now-first.at)/1000;
    setRate(seconds>=5&&done>first.done?(done-first.done)/seconds:null);
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
