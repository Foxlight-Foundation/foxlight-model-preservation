import {useEffect,useId,useRef,useState,type KeyboardEvent} from 'react';
import {Icon} from './ui';

export interface SelectOption {value:string;label:string;disabled?:boolean}

/** The app's own dropdown, in place of the system menu: a button styled like
 *  our inputs and a list on the app's surface (the select-only combobox
 *  pattern: arrows, Home/End, typing to jump, Enter/Space, Escape). */
export function Select({value,options,onChange,label,id,disabled=false}:{value:string;options:readonly SelectOption[];onChange:(value:string)=>void;label:string;id?:string;disabled?:boolean}) {
  const auto=useId().replaceAll(':','');
  const buttonId=id??`select-${auto}`,listId=`${buttonId}-list`;
  const selectedIndex=Math.max(0,options.findIndex(option=>option.value===value));
  const [open,setOpen]=useState(false),[active,setActive]=useState(selectedIndex);
  const wrap=useRef<HTMLDivElement>(null),button=useRef<HTMLButtonElement>(null),list=useRef<HTMLUListElement>(null);
  const typed=useRef({text:'',at:0});
  const current=options[selectedIndex];
  // Clicking anywhere else closes the list without changing the choice.
  useEffect(()=>{if(!open)return;const away=(event:PointerEvent)=>{if(!wrap.current?.contains(event.target as Node))setOpen(false);};document.addEventListener('pointerdown',away);return()=>document.removeEventListener('pointerdown',away);},[open]);
  useEffect(()=>{if(open)list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({block:'nearest'});},[open,active]);
  const step=(from:number,by:number)=>{let index=from;for(let tries=0;tries<options.length;tries++){index=(index+by+options.length)%options.length;if(!options[index]?.disabled)return index;}return from;};
  function show(){setActive(selectedIndex);setOpen(true);}
  function choose(index:number){const option=options[index];if(!option||option.disabled)return;setOpen(false);button.current?.focus();if(option.value!==value)onChange(option.value);}
  function jump(key:string){
    const now=Date.now(),memory=typed.current;
    memory.text=now-memory.at<700?memory.text+key.toLowerCase():key.toLowerCase();memory.at=now;
    const start=open?active:selectedIndex;
    for(let offset=1;offset<=options.length;offset++){
      const index=(start+(memory.text.length>1?0:offset))%options.length,option=options[index];
      if(option&&!option.disabled&&option.label.toLowerCase().startsWith(memory.text)){if(open)setActive(index);else if(option.value!==value)onChange(option.value);return;}
      if(memory.text.length>1)break;
    }
  }
  function keyDown(event:KeyboardEvent<HTMLButtonElement>){
    switch(event.key){
      case 'ArrowDown':event.preventDefault();if(open)setActive(index=>step(index,1));else show();return;
      case 'ArrowUp':event.preventDefault();if(open)setActive(index=>step(index,-1));else show();return;
      case 'Home':if(open){event.preventDefault();setActive(step(-1,1));}return;
      case 'End':if(open){event.preventDefault();setActive(step(options.length,-1));}return;
      case 'Enter':case ' ':event.preventDefault();if(open)choose(active);else show();return;
      case 'Escape':if(open){event.preventDefault();setOpen(false);}return;
      case 'Tab':setOpen(false);return;
      default:if(event.key.length===1&&!event.ctrlKey&&!event.metaKey&&!event.altKey){event.preventDefault();jump(event.key);}
    }
  }
  return <div className="sk-select" ref={wrap}>
    <button ref={button} id={buttonId} type="button" className="sk-input sk-select-button" role="combobox" aria-label={label} aria-haspopup="listbox" aria-expanded={open} aria-controls={listId} aria-activedescendant={open?`${listId}-${active}`:undefined} disabled={disabled} onClick={()=>open?setOpen(false):show()} onKeyDown={keyDown}>
      <span className="sk-select-value">{current?.label??''}</span><Icon name="chevron"/>
    </button>
    {open&&<ul ref={list} id={listId} role="listbox" aria-label={label} className="sk-select-list">
      {options.map((option,index)=><li key={option.value} id={`${listId}-${index}`} data-index={index} role="option" aria-selected={option.value===value} aria-disabled={option.disabled||undefined} data-active={index===active||undefined} className="sk-select-option" onPointerMove={()=>{if(!option.disabled)setActive(index);}} onPointerDown={event=>event.preventDefault()} onClick={()=>choose(index)}>
        <span className="sk-select-tick" aria-hidden="true">{option.value===value&&<Icon name="check"/>}</span><span>{option.label}</span>
      </li>)}
    </ul>}
  </div>;
}
